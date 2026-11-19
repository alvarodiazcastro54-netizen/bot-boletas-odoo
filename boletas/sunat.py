from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse

from .modelos import Boleta, Linea, Proveedor

CAC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2}"
CBC = "{urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2}"
IGV = "1000"
ICBPER = "7152"
MAX_XML = 5 * 1024 * 1024

TIPOS = {
    "01": "factura",
    "03": "boleta",
    "12": "ticket",
    "04": "liquidacion_de_compra",
    "07": "nota_de_credito",
    "08": "nota_de_debito",
    "09": "guia_de_remision",
    "31": "guia_de_remision",
    "02": "recibo_por_honorarios",
}
RAICES = {"CreditNote": "nota_de_credito", "DebitNote": "nota_de_debito", "DespatchAdvice": "guia_de_remision"}
NO_SON_COMPRA = {
    "nota_de_credito": "una nota de crédito (una devolución o un descuento)",
    "nota_de_debito": "una nota de débito (un cobro extra sobre otro comprobante)",
    "guia_de_remision": "una guía de remisión (el documento del transporte, no el de la compra)",
    "recibo_por_honorarios": "un recibo por honorarios (un servicio, no mercadería)",
}


class NoEsCompra(Exception):
    def __init__(self, tipo: str):
        self.tipo = tipo
        super().__init__(
            f"Es {NO_SON_COMPRA.get(tipo, 'un documento que no es una compra')}. No lo registro; "
            "si hace falta, regístralo a mano en Odoo."
        )


class XmlInvalido(Exception):
    pass


def _texto(nodo: ET.Element | None, ruta: str) -> str | None:
    if nodo is None:
        return None
    hallado = nodo.find(ruta)
    if hallado is None or hallado.text is None:
        return None
    return " ".join(hallado.text.split()) or None


def _numero(nodo: ET.Element | None, ruta: str) -> float | None:
    texto = _texto(nodo, ruta)
    try:
        return float(texto) if texto is not None else None
    except ValueError:
        return None


def _impuestos(nodo: ET.Element) -> dict[str, tuple[float, float]]:
    totales: dict[str, tuple[float, float]] = {}
    for sub in nodo.findall(f"{CAC}TaxTotal/{CAC}TaxSubtotal"):
        esquema = _texto(sub, f"{CAC}TaxCategory/{CAC}TaxScheme/{CBC}ID") or ""
        base, monto = totales.get(esquema, (0.0, 0.0))
        totales[esquema] = (
            base + (_numero(sub, f"{CBC}TaxableAmount") or 0.0),
            monto + (_numero(sub, f"{CBC}TaxAmount") or 0.0),
        )
    return totales


EXONERADOS = {"9997", "9998"}


def _exonerado(nodo: ET.Element) -> bool:
    for sub in nodo.findall(f"{CAC}TaxTotal/{CAC}TaxSubtotal"):
        esquema = _texto(sub, f"{CAC}TaxCategory/{CAC}TaxScheme/{CBC}ID") or ""
        motivo = _texto(sub, f"{CAC}TaxCategory/{CBC}TaxExemptionReasonCode") or ""
        if esquema in EXONERADOS or motivo[:1] in ("2", "3"):
            return True
    return False


def _documento(nodo: ET.Element | None) -> tuple[str | None, str | None]:
    if nodo is None:
        return None, None
    hallado = nodo.find(f"{CAC}PartyIdentification/{CBC}ID")
    if hallado is None or not hallado.text:
        return None, None
    return hallado.get("schemeID"), hallado.text.strip()


def _referencial(nodo: ET.Element) -> float | None:
    for alternativo in nodo.findall(f"{CAC}PricingReference/{CAC}AlternativeConditionPrice"):
        if _texto(alternativo, f"{CBC}PriceTypeCode") == "02":
            return _numero(alternativo, f"{CBC}PriceAmount") or 0.0
    return None


def _linea(nodo: ET.Element) -> Linea:
    cantidad = _numero(nodo, f"{CBC}InvoicedQuantity") or 1.0
    unidad = nodo.find(f"{CBC}InvoicedQuantity")
    comunes = {
        "codigo": _texto(nodo, f"{CAC}Item/{CAC}SellersItemIdentification/{CBC}ID"),
        "descripcion": _texto(nodo, f"{CAC}Item/{CBC}Description") or "PRODUCTO SIN DESCRIPCIÓN",
        "nombre_cortado": False,
        "cantidad": cantidad,
        "unidad": unidad.get("unitCode") if unidad is not None else None,
        "categoria": None,
        "nombre_tienda": None,
        "precio_venta": None,
    }
    referencial = _referencial(nodo)
    if referencial is not None:
        return Linea(
            **comunes, precio_unitario=round(referencial, 6), descuento_pct=100.0, descuento_monto=None, importe=0.0
        )
    valor = _numero(nodo, f"{CBC}LineExtensionAmount") or 0.0
    igv = _impuestos(nodo).get(IGV, (0.0, 0.0))[1]
    importe = round(valor + igv, 2)
    precio = None
    for alternativo in nodo.findall(f"{CAC}PricingReference/{CAC}AlternativeConditionPrice"):
        if _texto(alternativo, f"{CBC}PriceTypeCode") in (None, "01"):
            precio = _numero(alternativo, f"{CBC}PriceAmount")
            break
    if precio is None:
        sin_igv = _numero(nodo, f"{CAC}Price/{CBC}PriceAmount")
        precio = sin_igv * (1 + igv / valor) if valor and sin_igv is not None else importe / cantidad
    precio = round(precio, 6)
    descuento = round(cantidad * precio - importe, 2)
    return Linea(
        **comunes,
        precio_unitario=precio,
        descuento_pct=0.0,
        descuento_monto=descuento if descuento > 0.005 else None,
        importe=importe,
        exonerado=_exonerado(nodo),
    )


def xml_a_boleta(datos: bytes) -> Boleta:
    if len(datos) > MAX_XML or b"<!DOCTYPE" in datos[:4096] or b"<!ENTITY" in datos[:4096]:
        raise XmlInvalido("El XML es demasiado grande o trae declaraciones no permitidas.")
    try:
        raiz = ET.fromstring(datos)
    except ET.ParseError as exc:
        raise XmlInvalido("No pude leer el XML: está dañado o no es un comprobante.") from exc
    etiqueta = raiz.tag.rsplit("}", 1)[-1]
    if etiqueta in RAICES:
        raise NoEsCompra(RAICES[etiqueta])
    if etiqueta != "Invoice":
        raise XmlInvalido("El XML no es un comprobante electrónico de SUNAT (factura o boleta).")
    tipo = TIPOS.get(_texto(raiz, f"{CBC}InvoiceTypeCode") or "", "boleta")
    if tipo in NO_SON_COMPRA:
        raise NoEsCompra(tipo)
    moneda = _texto(raiz, f"{CBC}DocumentCurrencyCode") or "PEN"
    if moneda not in ("PEN", "USD"):
        raise XmlInvalido(f"El comprobante está en {moneda}: solo registro soles o dólares.")
    emisor = raiz.find(f"{CAC}AccountingSupplierParty/{CAC}Party")
    cliente = raiz.find(f"{CAC}AccountingCustomerParty/{CAC}Party")
    dni = None
    if tipo == "liquidacion_de_compra" and _documento(emisor)[0] == "6" and _documento(cliente)[0] != "6":
        emisor, cliente = cliente, emisor
    if tipo == "liquidacion_de_compra":
        esquema, numero = _documento(emisor)
        dni = numero if esquema != "6" else None
    lineas = [_linea(n) for n in raiz.findall(f"{CAC}InvoiceLine")]
    impuestos = _impuestos(raiz)
    total = _numero(raiz, f"{CAC}LegalMonetaryTotal/{CBC}PayableAmount")
    resto = round((total or 0) - sum(l.importe for l in lineas), 2) if total is not None else 0.0
    return Boleta(
        es_comprobante=True,
        legible=True,
        tipo=tipo,
        serie_numero=_texto(raiz, f"{CBC}ID"),
        fecha_emision=_texto(raiz, f"{CBC}IssueDate"),
        moneda=moneda,
        proveedor=Proveedor(
            ruc=None if dni else _texto(emisor, f"{CAC}PartyIdentification/{CBC}ID"),
            dni=dni,
            razon_social=_texto(emisor, f"{CAC}PartyLegalEntity/{CBC}RegistrationName"),
            nombre_comercial=_texto(emisor, f"{CAC}PartyName/{CBC}Name"),
            direccion=_texto(emisor, f"{CAC}PartyLegalEntity/{CAC}RegistrationAddress/{CAC}AddressLine/{CBC}Line"),
        ),
        cliente_documento=_texto(cliente, f"{CAC}PartyIdentification/{CBC}ID"),
        precios_incluyen_igv=True,
        lineas=lineas,
        descuento_global=-resto if resto < -0.005 else None,
        otros_cargos=resto if resto > 0.005 else None,
        op_gravadas=impuestos[IGV][0] if IGV in impuestos else None,
        igv=impuestos[IGV][1] if IGV in impuestos else None,
        total=total,
        observaciones=None,
    )


@dataclass(frozen=True)
class DatosQR:
    ruc: str | None
    tipo: str | None
    serie_numero: str | None
    total: float | None
    fecha: str | None


def _fecha_iso(texto: str | None) -> str | None:
    if not texto:
        return None
    texto = texto.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", texto):
        return texto
    if m := re.fullmatch(r"(\d{2})[/-](\d{2})[/-](\d{4})", texto):
        return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return None


def _monto(texto: str | None) -> float | None:
    try:
        return float(texto.replace(",", "")) if texto else None
    except ValueError:
        return None


def _serie(serie: str | None, numero: str | None) -> str | None:
    if not serie or not numero or not numero.strip().isdigit():
        return None
    return f"{serie.strip().upper()}-{int(numero):08d}"


def interpretar_qr(texto: str) -> DatosQR | None:
    texto = texto.strip()
    if "|" in texto:
        partes = [p.strip() for p in texto.split("|")]
        if len(partes) < 7 or not re.fullmatch(r"\d{11}", partes[0]):
            return None
        return DatosQR(
            partes[0], partes[1] or None, _serie(partes[2], partes[3]), _monto(partes[5]), _fecha_iso(partes[6])
        )
    if texto.lower().startswith("http"):
        params = {k.lower(): v[0] for k, v in parse_qs(urlparse(texto).query).items()}
        ruc = params.get("ruc") or params.get("rucemisor")
        if not ruc or not re.fullmatch(r"\d{11}", ruc):
            return None
        return DatosQR(
            ruc,
            params.get("tipo") or params.get("tipodoc"),
            _serie(params.get("serie"), params.get("numero") or params.get("correlativo")),
            _monto(params.get("total") or params.get("monto")),
            _fecha_iso(params.get("fecha")),
        )
    return None


def serie_normalizada(valor: str | None) -> str | None:
    if not valor or "-" not in valor:
        return valor
    serie, _, numero = valor.partition("-")
    return _serie(serie, numero) or valor.upper()


def aplicar_qr(boleta: Boleta, datos: DatosQR) -> tuple[Boleta, list[str]]:
    b = boleta.model_copy(deep=True)
    cambios = []
    tipo = TIPOS.get(datos.tipo or "")
    if tipo in NO_SON_COMPRA:
        raise NoEsCompra(tipo)
    if tipo and tipo != b.tipo:
        b.tipo = tipo
    if datos.ruc and datos.ruc != b.proveedor.ruc:
        cambios.append(f"RUC {datos.ruc} (se leyó {b.proveedor.ruc or 'nada'})")
        b.proveedor.ruc = datos.ruc
    if datos.serie_numero and datos.serie_numero != serie_normalizada(b.serie_numero):
        cambios.append(f"número {datos.serie_numero} (se leyó {b.serie_numero or 'nada'})")
        b.serie_numero = datos.serie_numero
    if datos.total is not None and (b.total is None or abs(datos.total - b.total) > 0.009):
        leido = f"{b.total:.2f}" if b.total is not None else "nada"
        cambios.append(f"total {datos.total:.2f} (se leyó {leido})")
        b.total = datos.total
    if datos.fecha and datos.fecha != b.fecha_emision:
        cambios.append(f"fecha {datos.fecha} (se leyó {b.fecha_emision or 'nada'})")
        b.fecha_emision = datos.fecha
    return b, cambios
