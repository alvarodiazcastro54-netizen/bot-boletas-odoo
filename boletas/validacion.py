from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Literal

from .modelos import Boleta, Linea
from .sunat import NO_SON_COMPRA

TOLERANCIA = 0.02
TASA_IGV = 0.18
SERIE_NUMERO = re.compile(r"^[A-Z0-9]{1,4}-\d{1,10}$")


@dataclass(frozen=True)
class Problema:
    nivel: Literal["error", "aviso"]
    mensaje: str


def ruc_valido(ruc: str | None) -> bool:
    if not ruc or not re.fullmatch(r"\d{11}", ruc):
        return False
    if ruc[:2] not in {"10", "15", "16", "17", "20"}:
        return False
    pesos = (5, 4, 3, 2, 7, 6, 5, 4, 3, 2)
    suma = sum(int(d) * p for d, p in zip(ruc[:10], pesos, strict=True))
    digito = 11 - suma % 11
    if digito == 10:
        digito = 0
    elif digito == 11:
        digito = 1
    return digito == int(ruc[10])


def importe_calculado(linea: Linea) -> float:
    bruto = linea.cantidad * linea.precio_unitario
    neto = bruto * (1 - (linea.descuento_pct or 0) / 100) - (linea.descuento_monto or 0)
    return round(neto, 2)


def subtotal_lineas(boleta: Boleta) -> float:
    return round(sum(l.importe for l in boleta.lineas), 2)


def total_calculado(boleta: Boleta) -> float:
    base = subtotal_lineas(boleta) - (boleta.descuento_global or 0)
    if not boleta.precios_incluyen_igv:
        igv = boleta.igv if boleta.igv is not None else round(base * TASA_IGV, 2)
        base += igv
    return round(base + (boleta.otros_cargos or 0), 2)


def pista_del_total(boleta: Boleta, esperado: float) -> str:
    total = boleta.total or 0
    base = subtotal_lineas(boleta) - (boleta.descuento_global or 0) + (boleta.otros_cargos or 0)
    if not boleta.precios_incluyen_igv and abs(base - total) <= TOLERANCIA:
        return " Si los precios ya incluyen el IGV, escribe «igv incluido»."
    if boleta.precios_incluyen_igv and abs(round(base * (1 + TASA_IGV), 2) - total) <= 0.05:
        return " Si a los precios se les suma el IGV al final, escribe «igv aparte»."
    diferencia = round(total - esperado, 2)
    if diferencia < 0:
        return (
            f" Si hay un descuento sobre toda la compra, escribe «descuento total {-diferencia:.2f}»; si falta "
            "o sobra una línea, corrígela."
        )
    return (
        f" Si la diferencia es percepción, flete u otro cargo, escribe «otros {diferencia:.2f}»; si falta una "
        "línea, revisa la lectura."
    )


def _fecha(texto: str | None) -> dt.date | None:
    if not texto:
        return None
    try:
        return dt.date.fromisoformat(texto)
    except ValueError:
        return None


def validar(boleta: Boleta, hoy: dt.date | None = None) -> list[Problema]:
    hoy = hoy or dt.date.today()
    p: list[Problema] = []

    def error(msg: str) -> None:
        p.append(Problema("error", msg))

    def aviso(msg: str) -> None:
        p.append(Problema("aviso", msg))

    if boleta.tipo in NO_SON_COMPRA:
        error(
            f"Es {NO_SON_COMPRA[boleta.tipo]}, no una compra. No se registra; si hace falta, regístralo a mano en Odoo."
        )
        return p
    if not boleta.es_comprobante:
        error("No parece una boleta, factura, ticket ni nota de venta de una compra.")
        return p
    if not boleta.legible:
        error(
            "No se lee bien. Toma otra foto con más luz, de frente y sin tapar nada, o mándala como archivo "
            "(📎) para que Telegram no le baje la calidad."
        )

    nota_de_venta = boleta.tipo in ("nota_de_venta", "cotizacion")
    if boleta.tipo == "cotizacion":
        aviso(
            "Es una cotización (presupuesto o proforma): no prueba que la compra se hizo. Regístrala solo si ya "
            "pagaste y recibiste la mercadería."
        )
    elif nota_de_venta:
        aviso("Es una nota de venta: no es un comprobante válido para SUNAT.")
    elif boleta.tipo == "otro":
        aviso("No se reconoce el tipo de comprobante; se registrará como boleta.")

    liquidacion = boleta.tipo == "liquidacion_de_compra"
    if liquidacion:
        aviso(
            "Es una liquidación de compra (el vendedor no tiene RUC). Lleva retenciones de IGV y de renta que se "
            "declaran aparte: la factura de proveedor queda en borrador para el contador."
        )
        if not boleta.proveedor.dni:
            aviso("No se leyó el DNI del vendedor. Escríbelo con «dni 12345678».")
        elif not re.fullmatch(r"\d{8}", boleta.proveedor.dni):
            aviso(f"El DNI «{boleta.proveedor.dni}» no tiene 8 dígitos. Revísalo.")

    ruc = boleta.proveedor.ruc
    if not ruc and not nota_de_venta and not liquidacion:
        error(
            "No se leyó el RUC del proveedor. Si no tiene RUC (nota de venta, compra por internet o puesto sin "
            "comprobante), escribe «tipo nota»."
        )
    elif ruc and not ruc_valido(ruc):
        error(f"El RUC {ruc} no es válido (falla el dígito verificador). Revísalo.")
    if not (boleta.proveedor.razon_social or boleta.proveedor.nombre_comercial):
        error("No se leyó a quién le compraste. Escribe el nombre del negocio, por ejemplo «proveedor BAZAR JUANITA».")

    if not boleta.serie_numero:
        if not nota_de_venta:
            error("No se leyó la serie y el número del comprobante.")
    elif not nota_de_venta and not SERIE_NUMERO.match(boleta.serie_numero):
        aviso(f"La serie y número «{boleta.serie_numero}» tiene un formato raro. Revísalo.")

    fecha = _fecha(boleta.fecha_emision)
    if fecha is None:
        error("No se leyó la fecha de emisión.")
    elif fecha > hoy:
        error(f"La fecha {fecha:%d/%m/%Y} está en el futuro.")
    elif (hoy - fecha).days > 365:
        aviso(f"La boleta es del {fecha:%d/%m/%Y}, hace más de un año.")

    if boleta.moneda == "OTRA":
        error(
            "La compra está en una moneda que no es soles ni dólares: no puedo registrar bien su costo. "
            "Regístrala a mano en Odoo."
        )
    elif boleta.moneda != "PEN":
        aviso(f"La boleta está en {boleta.moneda}.")

    if not boleta.lineas:
        error("No se leyó ningún producto.")
    for n, linea in enumerate(boleta.lineas, start=1):
        if linea.cantidad <= 0:
            error(f"Línea {n}: la cantidad debe ser mayor que cero.")
            continue
        if linea.importe < 0 or linea.precio_unitario < 0:
            error(f"Línea {n}: hay un precio negativo.")
            continue
        calculado = importe_calculado(linea)
        if abs(calculado - linea.importe) > TOLERANCIA:
            error(
                f"Línea {n}: {linea.cantidad:g} × {linea.precio_unitario:.2f}"
                f"{f' − {linea.descuento_pct:g} %' if linea.descuento_pct else ''}"
                f" da {calculado:.2f}, pero la boleta dice {linea.importe:.2f}."
            )
        if linea.nombre_cortado:
            aviso(f"Línea {n}: el nombre se ve cortado («{linea.descripcion}»).")

    if boleta.total is None:
        error("No se leyó el total.")
    elif boleta.lineas:
        esperado = total_calculado(boleta)
        if abs(esperado - boleta.total) > TOLERANCIA:
            error(
                f"Las líneas suman {esperado:.2f}, pero el total de la boleta es {boleta.total:.2f}."
                f"{pista_del_total(boleta, esperado)}"
            )

    exoneradas = [l for l in boleta.lineas if l.exonerado]
    if exoneradas and not boleta.precios_incluyen_igv and boleta.igv is not None and boleta.lineas:
        gravadas = sum(l.importe for l in boleta.lineas if not l.exonerado) - (boleta.descuento_global or 0)
        if abs(gravadas * TASA_IGV - boleta.igv) > max(0.05, boleta.igv * 0.01):
            aviso(
                "Las líneas marcadas sin IGV no cuadran con el IGV de la factura: reparto el IGV entre todas. "
                "Revisa cuáles son exoneradas («3 exonerado» o «3 gravado»)."
            )

    if (
        boleta.op_gravadas is not None
        and boleta.igv is not None
        and abs(boleta.op_gravadas * TASA_IGV - boleta.igv) > TOLERANCIA
    ):
        aviso(
            f"El IGV ({boleta.igv:.2f}) no es el 18 % de la base ({boleta.op_gravadas:.2f}). "
            "Puede haber productos exonerados."
        )

    if boleta.observaciones:
        aviso(f"Nota de la lectura: {boleta.observaciones}")
    return p


def hay_errores(problemas: list[Problema]) -> bool:
    return any(x.nivel == "error" for x in problemas)
