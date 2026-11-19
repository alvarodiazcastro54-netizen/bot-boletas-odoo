from __future__ import annotations

import base64
import datetime as dt
import hashlib
import logging
import math
import re
import time
from dataclasses import dataclass, field

from .modelos import Boleta, Linea
from .odoo import Odoo, OdooError
from .precios import Margenes, precio_sugerido
from .sunat import serie_normalizada
from .tienda import MARCA_DEL_BOT, ORIGEN_DEL_BOT, Tienda
from .validacion import SERIE_NUMERO, TASA_IGV, importe_calculado, subtotal_lineas

log = logging.getLogger(__name__)

CODIGO_DOCUMENTO = {"boleta": "03", "factura": "01", "ticket": "12", "liquidacion_de_compra": "04", "otro": "03"}
_CAMPOS_FACTURA = ["id", "name", "state", "amount_total"]
_CAMPOS_ORDEN = ["id", "name", "state", "picking_ids", "invoice_ids", "order_line"]
_UNIDAD_AL_FINAL = re.compile(r"\s+(UND|UNID|UNIDAD|UN|UNI|PZA|PZ)\.?$", re.IGNORECASE)
_SERIE_EN_LA_MARCA = re.compile(r"comprobante (\S+) (?:del RUC|de )")
LINEA_OTROS = "Otros cargos de la boleta (ICBPER, percepción, flete, redondeo)"
DIAS_PARA_COMPRA_PARECIDA = 10


class RegistroError(Exception):
    pass


def tipo_mime(datos: bytes) -> str:
    if datos.startswith(b"%PDF"):
        return "application/pdf"
    if datos.lstrip(b"\xef\xbb\xbf \t\r\n").startswith(b"<"):
        return "application/xml"
    if datos.startswith(b"\x89PNG"):
        return "image/png"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def precio_de_venta(costo: float, margen: float) -> float:
    return math.ceil(round(costo * (1 + margen) * 10, 6)) / 10


def sin_codigo(linea: Linea) -> str:
    texto = linea.descripcion.strip()
    if linea.codigo:
        prefijo = f"[{linea.codigo.strip()}]"
        if texto.upper().startswith(prefijo.upper()):
            texto = texto[len(prefijo) :].strip() or texto
    return texto


def nombre_producto(linea: Linea) -> str:
    texto = sin_codigo(linea)
    return _UNIDAD_AL_FINAL.sub("", texto) or texto


def nombre_en_tienda(linea: Linea) -> str:
    return (linea.nombre_tienda or "").strip() or nombre_producto(linea)


NOTAS = {"nota_de_venta": "Nota de venta", "cotizacion": "Cotización"}


def es_nota(boleta: Boleta) -> bool:
    return boleta.tipo in NOTAS


def sin_ruc(boleta: Boleta) -> bool:
    return es_nota(boleta) and not boleta.proveedor.ruc


def huella_de_lineas(boleta: Boleta) -> str:
    filas = sorted(f"{l.cantidad:g}x{l.importe:.2f}" for l in boleta.lineas)
    return hashlib.sha1("|".join(filas).encode("utf-8")).hexdigest()[:4]


def factor_de_igv(boleta: Boleta) -> float:
    base = subtotal_lineas(boleta) - (boleta.descuento_global or 0)
    if boleta.igv is not None and base > 0:
        return (base + boleta.igv) / base
    return 1 + TASA_IGV


def exoneradas_cuadran(boleta: Boleta) -> bool:
    if not any(l.exonerado for l in boleta.lineas):
        return False
    if boleta.igv is None:
        return True
    subtotal = subtotal_lineas(boleta)
    reparto = (subtotal - (boleta.descuento_global or 0)) / subtotal if subtotal else 1.0
    gravadas = sum(l.importe for l in boleta.lineas if not l.exonerado) * reparto
    return abs(gravadas * TASA_IGV - boleta.igv) <= max(0.05, boleta.igv * 0.01)


def factor_de_igv_de_la_linea(boleta: Boleta, linea: Linea) -> float:
    if exoneradas_cuadran(boleta):
        return 1.0 if linea.exonerado else 1 + TASA_IGV
    return factor_de_igv(boleta)


def es_liquidacion(boleta: Boleta) -> bool:
    return boleta.tipo == "liquidacion_de_compra"


def sin_comprobante_de_sunat_confirmable(boleta: Boleta) -> bool:
    return es_nota(boleta) or es_liquidacion(boleta)


def nombre_proveedor(boleta: Boleta) -> str | None:
    p = boleta.proveedor
    return (p.razon_social or p.nombre_comercial or "").strip() or None


def referencia(boleta: Boleta) -> str | None:
    if boleta.serie_numero:
        return serie_normalizada(boleta.serie_numero)
    if sin_ruc(boleta) and boleta.fecha_emision and boleta.total is not None:
        return f"NV-{boleta.fecha_emision}-{boleta.total:.2f}-{huella_de_lineas(boleta)}"
    return None


def referencia_antigua(boleta: Boleta) -> str | None:
    if sin_ruc(boleta) and not boleta.serie_numero and boleta.fecha_emision and boleta.total is not None:
        return f"NV-{boleta.fecha_emision}-{boleta.total:.2f}"
    return None


def marca_de_boleta(boleta: Boleta) -> str:
    if boleta.proveedor.ruc:
        return f"comprobante {referencia(boleta)} del RUC {boleta.proveedor.ruc}"
    return f"comprobante {referencia(boleta)} de {nombre_proveedor(boleta)}"


@dataclass
class LineaPlan:
    numero: int
    linea: Linea
    producto_id: int | None
    producto_nombre: str | None
    precio_unitario: float
    descuento: float
    costo_unitario: float
    impuestos: list[int]
    categoria_id: int | None = None
    categoria_nombre: str | None = None
    precio_venta: float | None = None
    nombre_tienda: str | None = None
    categoria_web_id: int | None = None
    margen: float | None = None
    tipo_de_cambio: float = 1.0

    @property
    def costo_soles(self) -> float:
        return round(self.costo_unitario * self.tipo_de_cambio, 6)


@dataclass
class Plan:
    boleta: Boleta
    proveedor_id: int | None
    proveedor_nombre: str | None
    con_credito_fiscal: bool
    lineas: list[LineaPlan]
    orden: dict | None = None
    ya_registrada: bool = False
    avisos: list[str] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    parecida: dict | None = None
    tipo_de_cambio: float = 1.0

    @property
    def productos_nuevos(self) -> int:
        return sum(1 for l in self.lineas if l.producto_id is None)


@dataclass
class Resultado:
    orden: str
    recepciones: list[str]
    factura: str | None
    productos_nuevos: list[str]
    proveedor_nuevo: bool
    avisos: list[str] = field(default_factory=list)
    factura_confirmada: bool = False
    productos: list[tuple[int, str, int]] = field(default_factory=list)


class Registrador:
    def __init__(
        self,
        odoo: Odoo,
        margen_venta: float | None = None,
        categoria_id: int | None = None,
        ruc_empresa: str | None = None,
        usa_credito_fiscal: bool = True,
        confirmar_factura: bool = False,
        sugerir_precio: bool = True,
        tienda: Tienda | None = None,
        reusar_productos: bool = False,
    ):
        self.odoo = odoo
        self.reusar_productos = reusar_productos
        self.confirmar_factura = confirmar_factura
        self.margen_venta = margen_venta
        self.categoria_id = categoria_id
        self._ruc_empresa = ruc_empresa
        self.usa_credito_fiscal = usa_credito_fiscal
        self.sugerir_precio = sugerir_precio
        self.tienda = tienda or Tienda(odoo)
        self.margenes = Margenes(self.tienda.filas_de_margen)
        self._cache: dict = {}

    def _una_vez(self, clave, funcion):
        if clave not in self._cache:
            self._cache[clave] = funcion()
        return self._cache[clave]

    def categorias(self) -> dict[str, int]:
        guardado = self._cache.get("categorias")
        if guardado and time.monotonic() - guardado[0] < 600:
            return guardado[1]
        filas = self.odoo.buscar_leer(
            "product.category", [("child_id", "=", False)], ["id", "complete_name"], orden="complete_name"
        )
        externos = self._ids_externos("product.category", [f["id"] for f in filas])
        modulos_odoo = {"product", "delivery", "point_of_sale", "stock", "account", "sale", "purchase", "website_sale"}
        resultado = {
            f["complete_name"]: f["id"] for f in filas if externos.get(f["id"], "").split(".")[0] not in modulos_odoo
        }
        self._cache["categorias"] = (time.monotonic(), resultado)
        return resultado

    def nombres_categorias(self) -> list[str]:
        return list(self.categorias())

    def _ids_externos(self, modelo: str, ids: list[int]) -> dict[int, str]:
        if not ids:
            return {}
        respuesta = self.odoo.llamar(modelo, "get_external_id", ids=ids) or {}
        return {int(k): v or "" for k, v in respuesta.items()}

    def tiene_campo(self, modelo: str, campo: str) -> bool:
        return campo in self._una_vez(("campos", modelo), lambda: self.odoo.campos(modelo))

    @property
    def empresa_id(self) -> int | None:
        return getattr(self.odoo, "empresa_id", None)

    def empresa(self) -> dict:
        def leer():
            dominio = [("id", "=", self.empresa_id)] if self.empresa_id else []
            try:
                filas = self.odoo.buscar_leer(
                    "res.company", dominio, ["id", "name", "vat", "currency_id"], limite=1, orden="id"
                )
            except OdooError as exc:
                if exc.estado == 403:
                    raise RegistroError(
                        f"El usuario del bot no tiene acceso a la empresa {self.empresa_id} o no existe "
                        "(revisa ODOO_EMPRESA_ID y las empresas permitidas del usuario en Odoo)."
                    ) from exc
                raise
            if not filas:
                raise RegistroError(f"No existe la empresa {self.empresa_id} en Odoo (ODOO_EMPRESA_ID).")
            return filas[0]

        return self._una_vez("empresa", leer)

    def ruc_empresa(self) -> str | None:
        if self._ruc_empresa:
            return self._ruc_empresa
        return re.sub(r"\D", "", self.empresa().get("vat") or "") or None

    def impuesto_igv_compra(self) -> int | None:
        def buscar():
            empresa_id = self.empresa_id or self.empresa()["id"]
            candidatos = self.odoo.buscar_leer(
                "account.tax",
                [
                    ("type_tax_use", "=", "purchase"),
                    ("amount", "=", 18),
                    ("amount_type", "=", "percent"),
                    ("company_id", "=", empresa_id),
                ],
                ["id", "name"],
                orden="sequence, id",
            )
            externos = self._ids_externos("account.tax", [c["id"] for c in candidatos])
            buscado = f"account.{empresa_id}_purchase_tax_igv_18"
            for c in candidatos:
                if externos.get(c["id"]) == buscado:
                    return c["id"]
            log.warning("No encontré el IGV de compras por su id externo; lo busco por su nombre.")
            for c in candidatos:
                if c["name"] in ("IGV 18%", "VAT 18%"):
                    return c["id"]
            return None

        return self._una_vez("igv", buscar)

    def pais_peru(self) -> int | None:
        def buscar():
            filas = self.odoo.buscar_leer("res.country", [("code", "=", "PE")], ["id"], limite=1)
            return filas[0]["id"] if filas else None

        return self._una_vez("pais_pe", buscar)

    def moneda(self, codigo: str) -> int:
        def buscar():
            filas = self.odoo.buscar_leer("res.currency", [("name", "=", codigo)], ["id"], limite=1)
            return filas[0]["id"] if filas else None

        moneda_id = self._una_vez(("moneda", codigo), buscar)
        if not moneda_id:
            raise RegistroError(f"La moneda {codigo} no está activa en Odoo.")
        return moneda_id

    def moneda_de_la_empresa(self) -> str:
        moneda = self.empresa().get("currency_id")
        return moneda[1] if isinstance(moneda, list) and len(moneda) > 1 else "PEN"

    def tipo_de_cambio(self, codigo: str, fecha: str | None) -> float | None:
        if codigo == self.moneda_de_la_empresa():
            return 1.0

        def buscar():
            contexto = {"date": fecha} if fecha else {}
            filas = self.odoo.buscar_leer(
                "res.currency", [("name", "=", codigo)], ["inverse_rate", "rate"], limite=1, contexto=contexto
            )
            if not filas or not filas[0].get("rate") or abs(filas[0]["rate"] - 1) < 1e-9:
                return None
            return filas[0].get("inverse_rate") or 1 / filas[0]["rate"]

        return self._una_vez(("cambio", codigo, fecha), buscar)

    def tipo_identificacion_ruc(self) -> int | None:
        return self._tipo_identificacion("6")

    def tipo_identificacion_dni(self) -> int | None:
        return self._tipo_identificacion("1")

    def _tipo_identificacion(self, codigo: str) -> int | None:
        if not self.tiene_campo("res.partner", "l10n_latam_identification_type_id"):
            return None

        def buscar():
            filas = self.odoo.buscar_leer(
                "l10n_latam.identification.type", [("l10n_pe_vat_code", "=", codigo)], ["id"], limite=1
            )
            return filas[0]["id"] if filas else None

        return self._una_vez(("ident", codigo), buscar)

    def tipo_documento(self, tipo: str) -> int | None:
        if not self.tiene_campo("account.move", "l10n_latam_document_type_id"):
            return None
        codigo = CODIGO_DOCUMENTO.get(tipo, "03")

        def buscar():
            filas = self.odoo.buscar_leer(
                "l10n_latam.document.type",
                [("code", "=", codigo), ("country_id.code", "=", "PE"), ("internal_type", "=", "invoice")],
                ["id"],
                limite=1,
                orden="sequence, id",
            )
            return filas[0]["id"] if filas else None

        return self._una_vez(("doc", codigo), buscar)

    def _buscar_proveedor(self, ruc: str) -> dict | None:
        filas = self.odoo.buscar_leer(
            "res.partner",
            ["|", ("vat", "=", ruc), ("vat", "=", f"PE{ruc}")],
            ["id", "name"],
            limite=1,
            orden="is_company desc, id",
        )
        return filas[0] if filas else None

    def _buscar_proveedor_sin_ruc(self, nombre: str) -> dict | None:
        filas = self.odoo.buscar_leer(
            "res.partner",
            [("name", "=ilike", nombre), ("vat", "in", [False, ""])],
            ["id", "name"],
            limite=1,
            orden="supplier_rank desc, id",
        )
        return filas[0] if filas else None

    def proveedor_de(self, boleta: Boleta) -> dict | None:
        if boleta.proveedor.ruc:
            return self._buscar_proveedor(boleta.proveedor.ruc)
        nombre = nombre_proveedor(boleta)
        if es_liquidacion(boleta) and boleta.proveedor.dni:
            filas = self.odoo.buscar_leer(
                "res.partner",
                [("vat", "=", boleta.proveedor.dni)],
                ["id", "name"],
                limite=1,
                orden="supplier_rank desc, id",
            )
            if filas:
                return filas[0]
        return (
            self._buscar_proveedor_sin_ruc(nombre) if (sin_ruc(boleta) or es_liquidacion(boleta)) and nombre else None
        )

    def _credito_fiscal(self, boleta: Boleta) -> bool:
        if boleta.tipo != "factura" or not self.usa_credito_fiscal:
            return False
        cliente = re.sub(r"\D", "", boleta.cliente_documento or "")
        return bool(cliente) and cliente == self.ruc_empresa() and self.impuesto_igv_compra() is not None

    def _precios(self, boleta: Boleta, linea: Linea, credito: bool) -> tuple[float, float, float, list[int]]:
        subtotal = subtotal_lineas(boleta)
        factor = 1.0
        if boleta.descuento_global and subtotal:
            factor = (subtotal - boleta.descuento_global) / subtotal
        exacto = abs(importe_calculado(linea) - linea.importe) <= 0.005 and not linea.descuento_monto
        if factor == 1.0 and exacto:
            precio, descuento = linea.precio_unitario, linea.descuento_pct or 0.0
        else:
            precio, descuento = linea.importe * factor / linea.cantidad, 0.0
        con_igv = credito and not (linea.exonerado and exoneradas_cuadran(boleta))
        if con_igv and boleta.precios_incluyen_igv:
            precio = precio / (1 + TASA_IGV)
        elif not credito and not boleta.precios_incluyen_igv:
            precio = precio * factor_de_igv_de_la_linea(boleta, linea)
        precio = round(precio, 6)
        impuestos = [self.impuesto_igv_compra()] if con_igv else []
        return precio, descuento, round(precio * (1 - descuento / 100), 6), impuestos

    def _productos_de_la_boleta(self, proveedor_id: int, boleta: Boleta) -> dict[tuple[str, str], dict]:
        if not referencia(boleta):
            return {}
        codigos = sorted({l.codigo for l in boleta.lineas if l.codigo})
        nombres = sorted({l.descripcion for l in boleta.lineas if not l.codigo})
        condiciones = []
        if codigos:
            condiciones.append(("product_code", "in", codigos))
        if nombres:
            condiciones.append(("product_name", "in", nombres))
        if not condiciones:
            return {}
        dominio: list = [("partner_id", "=", proveedor_id)]
        original = f"comprobante {boleta.serie_numero} del RUC {boleta.proveedor.ruc}"
        marcas = sorted({marca_de_boleta(boleta), original})
        if boleta.serie_numero and boleta.proveedor.ruc and len(marcas) == 2:
            dominio += ["|"] + [("product_tmpl_id.description", "ilike", m) for m in marcas]
        else:
            dominio.append(("product_tmpl_id.description", "ilike", marca_de_boleta(boleta)))
        if len(condiciones) == 2:
            dominio.append("|")
        dominio += condiciones
        return self._productos_del_proveedor(dominio, orden="sequence, id")

    def _productos_por_codigo(self, proveedor_id: int, boleta: Boleta) -> dict[tuple[str, str], dict]:
        codigos = sorted({l.codigo for l in boleta.lineas if l.codigo})
        if not codigos:
            return {}
        dominio = [("partner_id", "=", proveedor_id), ("product_code", "in", codigos)]
        return self._productos_del_proveedor(dominio, orden="id desc")

    def _productos_del_proveedor(self, dominio: list, orden: str) -> dict[tuple[str, str], dict]:
        filas = self.odoo.buscar_leer(
            "product.supplierinfo",
            dominio,
            ["product_code", "product_name", "product_id", "product_tmpl_id"],
            orden=orden,
        )
        plantillas = [f["product_tmpl_id"][0] for f in filas if not f["product_id"] and f["product_tmpl_id"]]
        variantes = {}
        if plantillas:
            for t in self.odoo.leer("product.template", plantillas, ["product_variant_id"]):
                if t["product_variant_id"]:
                    variantes[t["id"]] = t["product_variant_id"]
        encontrados: dict[tuple[str, str], dict] = {}
        for f in filas:
            producto = f["product_id"] or variantes.get(f["product_tmpl_id"][0] if f["product_tmpl_id"] else 0)
            if producto:
                clave = ("codigo", f["product_code"]) if f["product_code"] else ("nombre", f.get("product_name"))
                encontrados.setdefault(clave, {"id": producto[0], "name": producto[1]})
        return encontrados

    def compra_parecida(self, proveedor_id: int, boleta: Boleta) -> dict | None:
        if boleta.total is None or not boleta.fecha_emision:
            return None
        try:
            fecha = dt.date.fromisoformat(boleta.fecha_emision)
        except ValueError:
            return None
        margen = dt.timedelta(days=DIAS_PARA_COMPRA_PARECIDA)
        dominio = [
            ("partner_id", "=", proveedor_id),
            ("origin", "=", ORIGEN_DEL_BOT),
            ("state", "!=", "cancel"),
            ("date_order", ">=", f"{fecha - margen} 00:00:00"),
            ("date_order", "<=", f"{fecha + margen} 23:59:59"),
        ]
        if self.empresa_id:
            dominio.append(("company_id", "=", self.empresa_id))
        campos = [*_CAMPOS_ORDEN, "partner_ref", "amount_total", "date_order"]
        filas = self.odoo.buscar_leer("purchase.order", dominio, campos, limite=20, orden="id desc")
        totales = {round(boleta.total, 2), round(boleta.total - (boleta.otros_cargos or 0), 2)}
        refs = {r for r in (boleta.serie_numero, referencia(boleta)) if r}
        candidatas = [f for f in filas if f.get("partner_ref") not in refs]
        for f in candidatas:
            if any(abs((f.get("amount_total") or 0) - t) <= 0.05 for t in totales):
                return {**f, "exacta": True}
        if not es_nota(boleta):
            for f in candidatas:
                if not SERIE_NUMERO.match(f.get("partner_ref") or ""):
                    return {**f, "exacta": False}
        return None

    def _orden_existente(self, proveedor_id: int, boleta: Boleta) -> dict | None:
        refs = sorted({r for r in (boleta.serie_numero, referencia(boleta), referencia_antigua(boleta)) if r})
        if not refs:
            return None
        dominio = [("partner_id", "=", proveedor_id), ("partner_ref", "in", refs), ("state", "!=", "cancel")]
        if self.empresa_id:
            dominio.append(("company_id", "=", self.empresa_id))
        filas = self.odoo.buscar_leer("purchase.order", dominio, _CAMPOS_ORDEN, limite=1, orden="id desc")
        return filas[0] if filas else None

    def _esta_completa(self, orden: dict, boleta: Boleta | None = None) -> bool:
        if orden["state"] not in ("purchase", "done") or not orden["invoice_ids"]:
            return False
        if orden["picking_ids"]:
            pendientes = self.odoo.buscar_leer(
                "stock.picking",
                [("id", "in", orden["picking_ids"]), ("state", "not in", ("done", "cancel"))],
                ["id"],
                limite=1,
            )
            if pendientes:
                return False
        if self.confirmar_factura and not (boleta and sin_comprobante_de_sunat_confirmable(boleta)):
            facturas = self.odoo.leer("account.move", orden["invoice_ids"], ["state"])
            if any(f["state"] == "draft" for f in facturas):
                return False
        return True

    def _precio_de_venta(self, lp: LineaPlan) -> None:
        if lp.linea.precio_venta is not None:
            lp.precio_venta = lp.linea.precio_venta
        elif self.margen_venta is not None:
            lp.precio_venta = precio_de_venta(lp.costo_soles, self.margen_venta)
        elif self.sugerir_precio:
            try:
                lp.margen = self.margenes.para(lp.categoria_nombre)
            except OdooError as exc:
                log.warning("No pude leer los márgenes de la tienda: %s", exc.mensaje)
                return
            lp.precio_venta = precio_sugerido(lp.costo_soles, lp.margen)

    def _categoria_web(self, lp: LineaPlan) -> None:
        try:
            lp.categoria_web_id = self.tienda.categoria_web(lp.categoria_nombre)
        except OdooError as exc:
            log.warning("No pude leer las categorías de la web: %s", exc.mensaje)

    def planificar(self, boleta: Boleta) -> Plan:
        proveedor = self.proveedor_de(boleta)
        credito = self._credito_fiscal(boleta)
        existentes = self._productos_de_la_boleta(proveedor["id"], boleta) if proveedor else {}
        if proveedor and self.reusar_productos:
            existentes = {**self._productos_por_codigo(proveedor["id"], boleta), **existentes}
        categorias = self.categorias() if any(l.categoria for l in boleta.lineas) else {}
        lineas = []
        avisos = []
        errores = []
        cambio = self.tipo_de_cambio(boleta.moneda, boleta.fecha_emision)
        if cambio is None:
            errores.append(
                f"Odoo no tiene el tipo de cambio de {boleta.moneda} para el "
                f"{boleta.fecha_emision or 'día de la compra'}: sin eso el costo en soles saldría mal. "
                "Que lo cargue contabilidad en Odoo (Contabilidad → Monedas) y vuelve a intentar."
            )
        for n, linea in enumerate(boleta.lineas, start=1):
            clave = ("codigo", linea.codigo) if linea.codigo else ("nombre", linea.descripcion)
            producto = existentes.get(clave)
            precio, descuento, costo, impuestos = self._precios(boleta, linea, credito)
            lp = LineaPlan(
                n,
                linea,
                producto["id"] if producto else None,
                producto["name"] if producto else None,
                precio,
                descuento,
                costo,
                impuestos,
                tipo_de_cambio=cambio or 1.0,
            )
            if producto is None:
                lp.nombre_tienda = nombre_en_tienda(linea)
                if linea.categoria and linea.categoria in categorias:
                    lp.categoria_id, lp.categoria_nombre = categorias[linea.categoria], linea.categoria
                    self._categoria_web(lp)
                elif linea.categoria:
                    avisos.append(f"Línea {n}: la categoría «{linea.categoria}» no existe en Odoo.")
                self._precio_de_venta(lp)
            lineas.append(lp)
        plan = Plan(
            boleta,
            proveedor["id"] if proveedor else None,
            proveedor["name"] if proveedor else None,
            credito,
            lineas,
            avisos=avisos,
            errores=errores,
            tipo_de_cambio=cambio or 1.0,
        )
        if proveedor:
            plan.orden = self._orden_existente(proveedor["id"], boleta)
            if plan.orden:
                plan.ya_registrada = self._esta_completa(plan.orden, boleta)
            else:
                plan.parecida = self.compra_parecida(proveedor["id"], boleta)
        if sin_ruc(boleta):
            plan.avisos.append(
                "Nota de venta sin RUC: el proveedor queda solo con su nombre y la factura de "
                "proveedor queda en borrador para que la revise contabilidad."
            )
        elif es_nota(boleta):
            plan.avisos.append(
                f"{NOTAS[boleta.tipo]}: no es un comprobante de SUNAT. La factura de proveedor queda en "
                "borrador y sin tipo de documento; cuando llegue la boleta o factura electrónica de "
                "esta compra, mándamela y la uno a esta compra."
            )
        if cambio and cambio != 1.0:
            plan.avisos.append(
                f"Compra en {boleta.moneda}: el costo en soles usa el tipo de cambio de Odoo ({cambio:.4f})."
            )
        if boleta.tipo == "factura" and not credito and self.usa_credito_fiscal:
            plan.avisos.append("La factura no está a nombre de la empresa: el IGV irá al costo.")
        if boleta.otros_cargos:
            plan.avisos.append(
                f"Otros cargos (flete, percepción, ICBPER) por {boleta.otros_cargos:.2f} irán en la factura, "
                "no en el costo de los productos."
            )
        return plan

    def _crear_proveedor(self, boleta: Boleta) -> int:
        p = boleta.proveedor
        valores = {
            "name": nombre_proveedor(boleta),
            "is_company": True,
            "supplier_rank": 1,
        }
        if p.ruc:
            valores["vat"] = p.ruc
            if tipo := self.tipo_identificacion_ruc():
                valores["l10n_latam_identification_type_id"] = tipo
        elif es_liquidacion(boleta) and p.dni:
            valores["vat"] = p.dni
            valores["is_company"] = False
            if tipo := self.tipo_identificacion_dni():
                valores["l10n_latam_identification_type_id"] = tipo
        if p.direccion:
            valores["street"] = p.direccion
        if pais := self.pais_peru():
            valores["country_id"] = pais
        notas = []
        if p.nombre_comercial and p.razon_social and p.nombre_comercial != p.razon_social:
            notas.append(f"Nombre comercial: {p.nombre_comercial}")
        if es_liquidacion(boleta):
            notas.append("Vendedor sin RUC (liquidación de compra), creado por el bot de boletas.")
        elif not p.ruc:
            notas.append("Proveedor sin RUC (nota de venta), creado por el bot de boletas.")
        if notas:
            valores["comment"] = " ".join(notas)
        return self.odoo.crear("res.partner", valores)

    def _crear_producto(self, lp: LineaPlan, proveedor_id: int, moneda_id: int, boleta: Boleta) -> int:
        linea = lp.linea
        valores = {
            "name": lp.nombre_tienda or nombre_en_tienda(linea),
            "type": "consu",
            "purchase_ok": True,
            "sale_ok": True,
            "standard_price": lp.costo_soles,
            "description": f"{MARCA_DEL_BOT}: {marca_de_boleta(boleta)}",
            "seller_ids": [
                [
                    0,
                    0,
                    {
                        "partner_id": proveedor_id,
                        "product_code": linea.codigo or False,
                        "product_name": sin_codigo(linea),
                        "price": lp.costo_unitario,
                        "currency_id": moneda_id,
                    },
                ]
            ],
        }
        if self.tiene_campo("product.product", "is_storable"):
            valores["is_storable"] = True
        valores["list_price"] = lp.precio_venta if lp.precio_venta is not None else 0.0
        if lp.categoria_id or self.categoria_id:
            valores["categ_id"] = lp.categoria_id or self.categoria_id
        if lp.categoria_web_id:
            valores["public_categ_ids"] = [[6, 0, [lp.categoria_web_id]]]
        return self.odoo.crear("product.product", valores)

    def unir(self, orden_id: int, boleta: Boleta, adjuntos: list[tuple[str, bytes]] | None = None) -> Resultado:
        orden = self.odoo.leer("purchase.order", [orden_id], [*_CAMPOS_ORDEN, "partner_ref"])[0]
        if orden["state"] == "cancel":
            raise RegistroError(f"La compra {orden['name']} está cancelada.")
        if not orden["invoice_ids"]:
            raise RegistroError(f"La compra {orden['name']} no tiene factura de proveedor; únelo a mano en Odoo.")
        factura_id = orden["invoice_ids"][0]
        campos = ["id", "name", "state", "ref", "amount_total"]
        latam = self.tiene_campo("account.move", "l10n_latam_document_type_id")
        if latam:
            campos.append("l10n_latam_use_documents")
        factura = self.odoo.leer("account.move", [factura_id], campos)[0]
        ref = referencia(boleta)
        oficial = not es_nota(boleta) and bool(boleta.proveedor.ruc or es_liquidacion(boleta)) and bool(ref)
        avisos: list[str] = []
        if oficial and factura["state"] == "draft":
            cambios: dict = {"invoice_date": boleta.fecha_emision}
            tipo = self.tipo_documento(boleta.tipo) if latam and factura.get("l10n_latam_use_documents") else None
            if tipo:
                cambios["l10n_latam_document_type_id"] = tipo
                cambios["l10n_latam_document_number"] = ref
                cambios["ref"] = False
            else:
                cambios["ref"] = ref
            self.odoo.escribir("account.move", [factura_id], cambios)
        elif oficial and factura["state"] == "posted":
            avisos.append("La factura de proveedor ya estaba confirmada: solo le adjunté este comprobante.")
        elif not oficial:
            avisos.append("Este documento no es un comprobante de SUNAT: solo lo adjunté a la compra.")
        self._adjuntar(factura_id, adjuntos or [])
        if oficial and orden.get("partner_ref") != ref:
            self.odoo.escribir("purchase.order", [orden["id"]], {"partner_ref": ref})
        factura = self.odoo.leer("account.move", [factura_id], _CAMPOS_FACTURA)[0]
        if oficial and self.confirmar_factura and factura["state"] == "draft" and not es_liquidacion(boleta):
            aviso, factura = self._confirmar_factura(factura, boleta)
            if aviso:
                avisos.append(aviso)
        return Resultado(
            orden["name"],
            [],
            factura["name"],
            [],
            False,
            avisos,
            factura["state"] == "posted",
            self._productos_de_las_lineas(orden["order_line"]),
        )

    def registrar(self, boleta: Boleta, adjuntos: list[tuple[str, bytes]] | None = None) -> Resultado:
        if not nombre_proveedor(boleta) or not boleta.fecha_emision or not referencia(boleta):
            raise RegistroError("Faltan el proveedor, la serie y número o la fecha.")
        if not boleta.proveedor.ruc and not sin_ruc(boleta) and not es_liquidacion(boleta):
            raise RegistroError("Falta el RUC del proveedor.")
        plan = self.planificar(boleta)
        if plan.ya_registrada:
            raise RegistroError(f"Esta boleta ya está registrada en la compra {plan.orden['name']}.")
        if plan.errores:
            raise RegistroError(" ".join(plan.errores))
        moneda_id = self.moneda(boleta.moneda)
        avisos = list(plan.avisos)

        proveedor_nuevo = plan.proveedor_id is None
        proveedor_id = plan.proveedor_id or self._crear_proveedor(boleta)

        nuevos: list[str] = []
        orden = plan.orden
        if orden is None:
            creados: dict[tuple[str, str], int] = {}
            lineas_orden = []
            for lp in plan.lineas:
                producto_id = lp.producto_id
                clave = ("codigo", lp.linea.codigo) if lp.linea.codigo else ("nombre", lp.linea.descripcion)
                if producto_id is None:
                    producto_id = creados.get(clave)
                if producto_id is None:
                    producto_id = self._crear_producto(lp, proveedor_id, moneda_id, boleta)
                    creados[clave] = producto_id
                    nuevos.append(lp.nombre_tienda or nombre_en_tienda(lp.linea))
                lineas_orden.append(
                    [
                        0,
                        0,
                        {
                            "product_id": producto_id,
                            "product_qty": lp.linea.cantidad,
                            "price_unit": lp.precio_unitario,
                            "discount": lp.descuento,
                            "tax_ids": [[6, 0, lp.impuestos]],
                            "date_planned": f"{boleta.fecha_emision} 17:00:00",
                        },
                    ]
                )
            valores_orden = {
                "partner_id": proveedor_id,
                "partner_ref": referencia(boleta),
                "date_order": f"{boleta.fecha_emision} 17:00:00",
                "currency_id": moneda_id,
                "origin": ORIGEN_DEL_BOT,
                "order_line": lineas_orden,
            }
            if self.empresa_id:
                valores_orden["company_id"] = self.empresa_id
            orden_id = self.odoo.crear("purchase.order", valores_orden)
            orden = self.odoo.leer("purchase.order", [orden_id], [*_CAMPOS_ORDEN, "amount_total"])[0]
            log.info("Compra %s creada (total %s)", orden["name"], orden.get("amount_total"))

        if orden["state"] in ("draft", "sent", "to approve"):
            self.odoo.llamar("purchase.order", "button_confirm", ids=[orden["id"]])
            orden = self.odoo.leer("purchase.order", [orden["id"]], _CAMPOS_ORDEN)[0]
            if orden["state"] not in ("purchase", "done"):
                raise RegistroError(
                    f"La compra {orden['name']} quedó en estado «{orden['state']}»: necesita aprobación en Odoo."
                )

        recepciones = self._recibir(orden["picking_ids"])
        factura = self._facturar(orden, boleta, adjuntos or [])
        if self.confirmar_factura and not sin_comprobante_de_sunat_confirmable(boleta):
            aviso, factura = self._confirmar_factura(factura, boleta)
            if aviso:
                avisos.append(aviso)
        confirmada = factura["state"] == "posted"
        productos = self._productos_de_la_orden(orden["order_line"], plan.lineas)
        return Resultado(
            orden["name"], recepciones, factura["name"], nuevos, proveedor_nuevo, avisos, confirmada, productos
        )

    def productos_de_compra(self, nombre: str) -> list[tuple[int, str, int]]:
        dominio = [("name", "=", nombre)]
        if self.empresa_id:
            dominio.append(("company_id", "=", self.empresa_id))
        ordenes = self.odoo.buscar_leer("purchase.order", dominio, ["order_line"], limite=1)
        return self._productos_de_las_lineas(ordenes[0]["order_line"]) if ordenes else []

    def _productos_de_las_lineas(self, linea_ids: list[int]) -> list[tuple[int, str, int]]:
        filas = self.odoo.leer("purchase.order.line", linea_ids, ["product_id"]) if linea_ids else []
        return [
            (n, f["product_id"][1], f["product_id"][0]) for n, f in enumerate(filas, start=1) if f.get("product_id")
        ]

    def _productos_de_la_orden(self, linea_ids: list[int], lineas: list[LineaPlan]) -> list[tuple[int, str, int]]:
        filas = self.odoo.leer("purchase.order.line", linea_ids, ["product_id"]) if linea_ids else []
        if len(filas) != len(lineas):
            return []
        return [
            (lp.numero, f["product_id"][1], f["product_id"][0])
            for lp, f in zip(lineas, filas, strict=True)
            if f["product_id"]
        ]

    def poner_foto(self, producto_id: int, foto: bytes) -> str:
        plantilla = self.odoo.leer("product.product", [producto_id], ["product_tmpl_id"])[0]["product_tmpl_id"]
        self.poner_foto_en_plantilla(plantilla[0], foto)
        return plantilla[1]

    def poner_foto_en_plantilla(self, plantilla_id: int, foto: bytes) -> None:
        self.odoo.escribir("product.template", [plantilla_id], {"image_1920": base64.b64encode(foto).decode("ascii")})

    def productos_sin_foto(self, limite: int = 10, revisar: int = 300) -> tuple[list[dict], int]:
        ids = self.tienda.del_bot(revisar)
        if not ids:
            return [], 0
        con_foto = self.tienda.con_foto(ids)
        sin_foto = [i for i in ids if i not in con_foto]
        if not sin_foto:
            return [], 0
        leidas = self.odoo.leer("product.template", sin_foto[:limite], ["id", "name", "description"])
        filas = {f["id"]: f for f in leidas}
        lista = []
        for i in sin_foto[:limite]:
            p = filas.get(i)
            if not p:
                continue
            serie = _SERIE_EN_LA_MARCA.search(p["description"] or "")
            lista.append({"id": p["id"], "nombre": p["name"], "serie": serie.group(1) if serie else None})
        return lista, len(sin_foto)

    def _confirmar_factura(self, factura: dict, boleta: Boleta) -> tuple[str | None, dict]:
        if factura["state"] == "posted":
            return None, factura
        if factura["state"] != "draft":
            return f"La factura de proveedor está en estado «{factura['state']}» y no se confirmó.", factura
        if boleta.total is None:
            return (
                "La factura quedó en borrador: no se leyó el total de la boleta para compararlo. "
                "Revísala y confírmala a mano en Odoo."
            ), factura
        if abs(factura["amount_total"] - boleta.total) >= 0.005:
            return (
                f"La factura quedó en borrador: en Odoo suma {factura['amount_total']:.2f} y la boleta dice "
                f"{boleta.total:.2f}. Revísala y confírmala a mano en Odoo."
            ), factura
        error = None
        try:
            self.odoo.llamar("account.move", "action_post", ids=[factura["id"]])
        except OdooError as exc:
            error = exc
        try:
            factura = self.odoo.leer("account.move", [factura["id"]], _CAMPOS_FACTURA)[0]
        except OdooError as exc:
            log.warning("No se pudo leer la factura %s después de confirmarla: %s", factura["id"], exc.mensaje)
            return "No pude comprobar si la factura se confirmó. Revísala en Odoo.", factura
        if factura["state"] == "posted":
            log.info("Factura %s confirmada", factura["id"])
            return None, factura
        if error:
            log.warning("No se pudo confirmar la factura %s: %s", factura["id"], error.mensaje)
            return (
                f"La factura quedó en borrador porque Odoo no dejó confirmarla: {error.mensaje} "
                "Confírmala a mano en Odoo."
            ), factura
        return (
            "La factura quedó en borrador: Odoo pidió algo más para confirmarla. Confírmala a mano en Odoo."
        ), factura

    def _recibir(self, picking_ids: list[int]) -> list[str]:
        if not picking_ids:
            return []
        nombres = []
        for picking in self.odoo.leer("stock.picking", picking_ids, ["id", "name", "state", "move_ids"]):
            nombres.append(picking["name"])
            if picking["state"] in ("done", "cancel"):
                continue
            for mov in self.odoo.leer("stock.move", picking["move_ids"], ["id", "product_uom_qty", "state"]):
                if mov["state"] in ("done", "cancel"):
                    continue
                self.odoo.escribir("stock.move", [mov["id"]], {"quantity": mov["product_uom_qty"], "picked": True})
            respuesta = self.odoo.llamar("stock.picking", "button_validate", ids=[picking["id"]])
            if isinstance(respuesta, dict):
                raise RegistroError(
                    f"Odoo pidió confirmar algo al validar la recepción {picking['name']} "
                    f"({respuesta.get('res_model') or respuesta.get('name')}). Valídala a mano en Odoo."
                )
            estado = self.odoo.leer("stock.picking", [picking["id"]], ["state"])[0]["state"]
            if estado != "done":
                raise RegistroError(f"La recepción {picking['name']} quedó en estado «{estado}».")
        return nombres

    def _adjuntar(self, factura_id: int, adjuntos: list[tuple[str, bytes]]) -> None:
        for nombre, datos in adjuntos:
            if not datos or self.odoo.buscar_leer(
                "ir.attachment",
                [("res_model", "=", "account.move"), ("res_id", "=", factura_id), ("name", "=", nombre)],
                ["id"],
                limite=1,
            ):
                continue
            self.odoo.crear(
                "ir.attachment",
                {
                    "name": nombre,
                    "type": "binary",
                    "datas": base64.b64encode(datos).decode("ascii"),
                    "res_model": "account.move",
                    "res_id": factura_id,
                    "mimetype": tipo_mime(datos),
                },
            )

    def _facturar(self, orden: dict, boleta: Boleta, adjuntos: list[tuple[str, bytes]]) -> dict:
        if not orden["invoice_ids"]:
            self.odoo.llamar("purchase.order", "action_create_invoice", ids=[orden["id"]])
            orden = self.odoo.leer("purchase.order", [orden["id"]], ["invoice_ids"])[0] | {"id": orden["id"]}
        if not orden["invoice_ids"]:
            raise RegistroError("Odoo no creó la factura de proveedor.")
        factura_id = orden["invoice_ids"][0]

        campos = ["id", "name", "state", "invoice_date", "ref", "invoice_line_ids"]
        latam = self.tiene_campo("account.move", "l10n_latam_document_type_id")
        if latam:
            campos += ["l10n_latam_document_type_id", "l10n_latam_document_number", "l10n_latam_use_documents"]
        factura = self.odoo.leer("account.move", [factura_id], campos)[0]

        if factura["state"] == "draft":
            cambios: dict = {}
            if not factura["invoice_date"]:
                cambios["invoice_date"] = boleta.fecha_emision
            if es_nota(boleta):
                if not factura["ref"]:
                    cambios["ref"] = f"{NOTAS[boleta.tipo]} {referencia(boleta)} (sin comprobante de SUNAT)"
            elif latam and factura.get("l10n_latam_use_documents"):
                tipo = self.tipo_documento(boleta.tipo)
                actual = factura["l10n_latam_document_type_id"]
                if tipo and (not actual or actual[0] != tipo):
                    cambios["l10n_latam_document_type_id"] = tipo
                if not factura["l10n_latam_document_number"]:
                    cambios["l10n_latam_document_number"] = referencia(boleta)
            elif not factura["ref"]:
                cambios["ref"] = referencia(boleta)
            if boleta.otros_cargos and not self._tiene_linea_otros(factura["invoice_line_ids"]):
                cambios["invoice_line_ids"] = [
                    [
                        0,
                        0,
                        {
                            "name": LINEA_OTROS,
                            "quantity": 1,
                            "price_unit": boleta.otros_cargos,
                            "tax_ids": [[6, 0, []]],
                        },
                    ]
                ]
            if cambios:
                self.odoo.escribir("account.move", [factura_id], cambios)

        self._adjuntar(factura_id, adjuntos)
        return self.odoo.leer("account.move", [factura_id], _CAMPOS_FACTURA)[0]

    def _tiene_linea_otros(self, linea_ids: list[int]) -> bool:
        if not linea_ids:
            return False
        filas = self.odoo.buscar_leer(
            "account.move.line",
            [("id", "in", linea_ids), ("name", "=like", "Otros cargos de la boleta%")],
            ["id"],
            limite=1,
        )
        return bool(filas)
