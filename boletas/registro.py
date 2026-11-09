from __future__ import annotations

import base64
import logging
import math
import re
import time
from dataclasses import dataclass, field

from .modelos import Boleta, Linea
from .odoo import Odoo, OdooError
from .validacion import TASA_IGV, importe_calculado, subtotal_lineas

log = logging.getLogger(__name__)

CODIGO_DOCUMENTO = {"boleta": "03", "factura": "01", "ticket": "12", "nota_de_venta": "03", "otro": "03"}
_UNIDAD_AL_FINAL = re.compile(r"\s+(UND|UNID|UNIDAD|UN|UNI|PZA|PZ)\.?$", re.IGNORECASE)


class RegistroError(Exception):
    pass


def _tipo_imagen(datos: bytes) -> str:
    if datos.startswith(b"\x89PNG"):
        return "image/png"
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def precio_de_venta(costo: float, margen: float) -> float:
    return math.ceil(round(costo * (1 + margen) * 10, 6)) / 10


def nombre_producto(linea: Linea) -> str:
    return _UNIDAD_AL_FINAL.sub("", linea.descripcion.strip()) or linea.descripcion.strip()


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


class Registrador:
    def __init__(self, odoo: Odoo, margen_venta: float | None = None, categoria_id: int | None = None,
                 ruc_empresa: str | None = None, usa_credito_fiscal: bool = True):
        self.odoo = odoo
        self.margen_venta = margen_venta
        self.categoria_id = categoria_id
        self._ruc_empresa = ruc_empresa
        self.usa_credito_fiscal = usa_credito_fiscal
        self._cache: dict = {}

    def _una_vez(self, clave, funcion):
        if clave not in self._cache:
            self._cache[clave] = funcion()
        return self._cache[clave]

    def categorias(self) -> dict[str, int]:
        guardado = self._cache.get("categorias")
        if guardado and time.monotonic() - guardado[0] < 600:
            return guardado[1]
        filas = self.odoo.buscar_leer("product.category", [("child_id", "=", False)],
                                      ["id", "complete_name"], orden="complete_name")
        externos = self._ids_externos("product.category", [f["id"] for f in filas])
        modulos_odoo = {"product", "delivery", "point_of_sale", "stock", "account", "sale", "purchase",
                        "website_sale"}
        resultado = {f["complete_name"]: f["id"] for f in filas
                     if externos.get(f["id"], "").split(".")[0] not in modulos_odoo}
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
                filas = self.odoo.buscar_leer("res.company", dominio, ["id", "name", "vat"], limite=1, orden="id")
            except OdooError as exc:
                if exc.estado == 403:
                    raise RegistroError(
                        f"El usuario del bot no tiene acceso a la empresa {self.empresa_id} o no existe "
                        "(revisa ODOO_EMPRESA_ID y las empresas permitidas del usuario en Odoo).") from exc
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
                [("type_tax_use", "=", "purchase"), ("amount", "=", 18), ("amount_type", "=", "percent"),
                 ("company_id", "=", empresa_id)],
                ["id", "name"], orden="sequence, id")
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

    def tipo_identificacion_ruc(self) -> int | None:
        if not self.tiene_campo("res.partner", "l10n_latam_identification_type_id"):
            return None
        def buscar():
            filas = self.odoo.buscar_leer(
                "l10n_latam.identification.type", [("l10n_pe_vat_code", "=", "6")], ["id"], limite=1)
            return filas[0]["id"] if filas else None
        return self._una_vez("ident_ruc", buscar)

    def tipo_documento(self, tipo: str) -> int | None:
        if not self.tiene_campo("account.move", "l10n_latam_document_type_id"):
            return None
        codigo = CODIGO_DOCUMENTO.get(tipo, "03")
        def buscar():
            filas = self.odoo.buscar_leer(
                "l10n_latam.document.type",
                [("code", "=", codigo), ("country_id.code", "=", "PE"), ("internal_type", "=", "invoice")],
                ["id"], limite=1, orden="sequence, id",
            )
            return filas[0]["id"] if filas else None
        return self._una_vez(("doc", codigo), buscar)

    def _buscar_proveedor(self, ruc: str) -> dict | None:
        filas = self.odoo.buscar_leer(
            "res.partner", ["|", ("vat", "=", ruc), ("vat", "=", f"PE{ruc}")],
            ["id", "name"], limite=1, orden="is_company desc, id")
        return filas[0] if filas else None

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
        if credito and boleta.precios_incluyen_igv:
            precio = precio / (1 + TASA_IGV)
        elif not credito and not boleta.precios_incluyen_igv:
            precio = precio * (1 + TASA_IGV)
        precio = round(precio, 6)
        impuestos = [self.impuesto_igv_compra()] if credito else []
        return precio, descuento, round(precio * (1 - descuento / 100), 6), impuestos

    def _productos_existentes(self, proveedor_id: int, lineas: list[Linea]) -> dict[tuple[str, str], dict]:
        codigos = sorted({l.codigo for l in lineas if l.codigo})
        nombres = sorted({l.descripcion for l in lineas if not l.codigo})
        condiciones = []
        if codigos:
            condiciones.append(("product_code", "in", codigos))
        if nombres:
            condiciones.append(("product_name", "in", nombres))
        if not condiciones:
            return {}
        dominio = [("partner_id", "=", proveedor_id)]
        if len(condiciones) == 2:
            dominio.append("|")
        dominio += condiciones
        filas = self.odoo.buscar_leer(
            "product.supplierinfo", dominio,
            ["product_code", "product_name", "product_id", "product_tmpl_id"], orden="sequence, id")
        plantillas = [f["product_tmpl_id"][0] for f in filas if not f["product_id"] and f["product_tmpl_id"]]
        variantes = {}
        if plantillas:
            for t in self.odoo.leer("product.template", plantillas, ["product_variant_id"]):
                if t["product_variant_id"]:
                    variantes[t["id"]] = t["product_variant_id"]
        encontrados: dict[tuple[str, str], dict] = {}
        for f in filas:
            producto = f["product_id"] or variantes.get(f["product_tmpl_id"][0] if f["product_tmpl_id"] else 0)
            if not producto:
                continue
            clave = ("codigo", f["product_code"]) if f["product_code"] else ("nombre", f["product_name"])
            encontrados.setdefault(clave, {"id": producto[0], "name": producto[1]})
        return encontrados

    def _orden_existente(self, proveedor_id: int, serie: str) -> dict | None:
        dominio = [("partner_id", "=", proveedor_id), ("partner_ref", "=", serie), ("state", "!=", "cancel")]
        if self.empresa_id:
            dominio.append(("company_id", "=", self.empresa_id))
        filas = self.odoo.buscar_leer(
            "purchase.order", dominio,
            ["id", "name", "state", "picking_ids", "invoice_ids"], limite=1, orden="id desc")
        return filas[0] if filas else None

    def _esta_completa(self, orden: dict) -> bool:
        if orden["state"] not in ("purchase", "done") or not orden["invoice_ids"]:
            return False
        if orden["picking_ids"]:
            pendientes = self.odoo.buscar_leer(
                "stock.picking", [("id", "in", orden["picking_ids"]), ("state", "not in", ("done", "cancel"))],
                ["id"], limite=1)
            if pendientes:
                return False
        return True

    def planificar(self, boleta: Boleta) -> Plan:
        ruc = boleta.proveedor.ruc or ""
        proveedor = self._buscar_proveedor(ruc) if ruc else None
        credito = self._credito_fiscal(boleta)
        existentes = self._productos_existentes(proveedor["id"], boleta.lineas) if proveedor else {}
        categorias = self.categorias() if any(l.categoria for l in boleta.lineas) else {}
        lineas = []
        avisos = []
        for n, linea in enumerate(boleta.lineas, start=1):
            clave = ("codigo", linea.codigo) if linea.codigo else ("nombre", linea.descripcion)
            producto = existentes.get(clave)
            precio, descuento, costo, impuestos = self._precios(boleta, linea, credito)
            lp = LineaPlan(n, linea, producto["id"] if producto else None,
                           producto["name"] if producto else None, precio, descuento, costo, impuestos)
            if producto is None:
                if linea.categoria and linea.categoria in categorias:
                    lp.categoria_id, lp.categoria_nombre = categorias[linea.categoria], linea.categoria
                elif linea.categoria:
                    avisos.append(f"Línea {n}: la categoría «{linea.categoria}» no existe en Odoo.")
                if self.margen_venta is not None:
                    lp.precio_venta = precio_de_venta(costo, self.margen_venta)
            lineas.append(lp)
        plan = Plan(boleta, proveedor["id"] if proveedor else None, proveedor["name"] if proveedor else None,
                    credito, lineas, avisos=avisos)
        if proveedor and boleta.serie_numero:
            plan.orden = self._orden_existente(proveedor["id"], boleta.serie_numero)
            if plan.orden:
                plan.ya_registrada = self._esta_completa(plan.orden)
        if boleta.tipo == "factura" and not credito:
            cliente = re.sub(r"\D", "", boleta.cliente_documento or "")
            if not self.usa_credito_fiscal and cliente and cliente == self.ruc_empresa():
                plan.avisos.append(
                    "Factura a nombre de la empresa, pero el bot está configurado sin crédito fiscal "
                    "(USA_CREDITO_FISCAL=no): el IGV irá al costo. Si la empresa no está en el Nuevo RUS, "
                    "el contador puede agregar el IGV en la factura borrador.")
            else:
                plan.avisos.append("La factura no está a nombre de la empresa: el IGV irá al costo.")
        if boleta.otros_cargos:
            plan.avisos.append(
                f"Otros cargos (ICBPER, redondeo) por {boleta.otros_cargos:.2f} irán en la factura, "
                "no en el costo de los productos.")
        return plan

    def _crear_proveedor(self, boleta: Boleta) -> int:
        p = boleta.proveedor
        valores = {
            "name": p.razon_social or p.nombre_comercial,
            "vat": p.ruc,
            "is_company": True,
            "supplier_rank": 1,
        }
        if p.direccion:
            valores["street"] = p.direccion
        if pais := self.pais_peru():
            valores["country_id"] = pais
        if tipo := self.tipo_identificacion_ruc():
            valores["l10n_latam_identification_type_id"] = tipo
        if p.nombre_comercial and p.razon_social and p.nombre_comercial != p.razon_social:
            valores["comment"] = f"Nombre comercial: {p.nombre_comercial}"
        return self.odoo.crear("res.partner", valores)

    def _crear_producto(self, lp: LineaPlan, proveedor_id: int, moneda_id: int) -> int:
        linea = lp.linea
        valores = {
            "name": nombre_producto(linea),
            "type": "consu",
            "purchase_ok": True,
            "sale_ok": True,
            "standard_price": lp.costo_unitario,
            "seller_ids": [[0, 0, {
                "partner_id": proveedor_id,
                "product_code": linea.codigo or False,
                "product_name": linea.descripcion,
                "price": lp.costo_unitario,
                "currency_id": moneda_id,
            }]],
        }
        if self.tiene_campo("product.product", "is_storable"):
            valores["is_storable"] = True
        valores["list_price"] = lp.precio_venta if lp.precio_venta is not None else 0.0
        if lp.categoria_id or self.categoria_id:
            valores["categ_id"] = lp.categoria_id or self.categoria_id
        return self.odoo.crear("product.product", valores)

    def registrar(self, boleta: Boleta, foto: bytes | None = None, nombre_foto: str = "boleta.jpg") -> Resultado:
        if not boleta.proveedor.ruc or not boleta.serie_numero or not boleta.fecha_emision:
            raise RegistroError("Faltan el RUC, la serie o la fecha.")
        plan = self.planificar(boleta)
        if plan.ya_registrada:
            raise RegistroError(f"Esta boleta ya está registrada en la compra {plan.orden['name']}.")
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
                    producto_id = self._crear_producto(lp, proveedor_id, moneda_id)
                    creados[clave] = producto_id
                    nuevos.append(nombre_producto(lp.linea))
                lineas_orden.append([0, 0, {
                    "product_id": producto_id,
                    "product_qty": lp.linea.cantidad,
                    "price_unit": lp.precio_unitario,
                    "discount": lp.descuento,
                    "tax_ids": [[6, 0, lp.impuestos]],
                    "date_planned": f"{boleta.fecha_emision} 17:00:00",
                }])
            valores_orden = {
                "partner_id": proveedor_id,
                "partner_ref": boleta.serie_numero,
                "date_order": f"{boleta.fecha_emision} 17:00:00",
                "currency_id": moneda_id,
                "origin": "Bot de boletas",
                "order_line": lineas_orden,
            }
            if self.empresa_id:
                valores_orden["company_id"] = self.empresa_id
            orden_id = self.odoo.crear("purchase.order", valores_orden)
            orden = self.odoo.leer("purchase.order", [orden_id],
                                   ["id", "name", "state", "picking_ids", "invoice_ids", "amount_total"])[0]
            log.info("Compra %s creada (total %s)", orden["name"], orden.get("amount_total"))

        if orden["state"] in ("draft", "sent", "to approve"):
            self.odoo.llamar("purchase.order", "button_confirm", ids=[orden["id"]])
            orden = self.odoo.leer("purchase.order", [orden["id"]],
                                   ["id", "name", "state", "picking_ids", "invoice_ids"])[0]
            if orden["state"] not in ("purchase", "done"):
                raise RegistroError(
                    f"La compra {orden['name']} quedó en estado «{orden['state']}»: necesita aprobación en Odoo.")

        recepciones = self._recibir(orden["picking_ids"])
        factura = self._facturar(orden, boleta, foto, nombre_foto)
        return Resultado(orden["name"], recepciones, factura, nuevos, proveedor_nuevo, avisos)

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
                    f"({respuesta.get('res_model') or respuesta.get('name')}). Valídala a mano en Odoo.")
            estado = self.odoo.leer("stock.picking", [picking["id"]], ["state"])[0]["state"]
            if estado != "done":
                raise RegistroError(f"La recepción {picking['name']} quedó en estado «{estado}».")
        return nombres

    def _facturar(self, orden: dict, boleta: Boleta, foto: bytes | None, nombre_foto: str) -> str | None:
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
            if latam and factura.get("l10n_latam_use_documents"):
                tipo = self.tipo_documento(boleta.tipo)
                actual = factura["l10n_latam_document_type_id"]
                if tipo and (not actual or actual[0] != tipo):
                    cambios["l10n_latam_document_type_id"] = tipo
                if not factura["l10n_latam_document_number"]:
                    cambios["l10n_latam_document_number"] = boleta.serie_numero
            elif not factura["ref"]:
                cambios["ref"] = boleta.serie_numero
            if boleta.otros_cargos and not self._tiene_linea_otros(factura["invoice_line_ids"]):
                cambios["invoice_line_ids"] = [[0, 0, {
                    "name": "Otros cargos de la boleta (ICBPER, redondeo)",
                    "quantity": 1,
                    "price_unit": boleta.otros_cargos,
                    "tax_ids": [[6, 0, []]],
                }]]
            if cambios:
                self.odoo.escribir("account.move", [factura_id], cambios)

        if foto and not self.odoo.buscar_leer(
                "ir.attachment", [("res_model", "=", "account.move"), ("res_id", "=", factura_id),
                                  ("name", "=", nombre_foto)], ["id"], limite=1):
            self.odoo.crear("ir.attachment", {
                "name": nombre_foto,
                "type": "binary",
                "datas": base64.b64encode(foto).decode("ascii"),
                "res_model": "account.move",
                "res_id": factura_id,
                "mimetype": _tipo_imagen(foto),
            })
        return self.odoo.leer("account.move", [factura_id], ["name"])[0]["name"]

    def _tiene_linea_otros(self, linea_ids: list[int]) -> bool:
        if not linea_ids:
            return False
        filas = self.odoo.buscar_leer(
            "account.move.line", [("id", "in", linea_ids), ("name", "=like", "Otros cargos de la boleta%")],
            ["id"], limite=1)
        return bool(filas)
