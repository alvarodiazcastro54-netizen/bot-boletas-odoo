from __future__ import annotations

import datetime as dt
import re
from dataclasses import dataclass
from typing import Literal

from .modelos import Boleta, Linea

TOLERANCIA = 0.02
TASA_IGV = 0.18
SERIE_NUMERO = re.compile(r"^[A-Z0-9]{1,4}-\d{1,8}$")


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
    suma = sum(int(d) * p for d, p in zip(ruc[:10], pesos))
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

    if not boleta.es_comprobante:
        error("La foto no parece una boleta, factura o ticket de compra.")
        return p
    if not boleta.legible:
        error("La foto no se lee bien. Toma otra con más luz, de frente y sin tapar nada.")

    if boleta.tipo == "nota_de_venta":
        aviso("Es una nota de venta: no es un comprobante válido para SUNAT.")
    elif boleta.tipo == "otro":
        aviso("No se reconoce el tipo de comprobante; se registrará como boleta.")

    ruc = boleta.proveedor.ruc
    if not ruc:
        error("No se leyó el RUC del proveedor.")
    elif not ruc_valido(ruc):
        error(f"El RUC {ruc} no es válido (falla el dígito verificador). Revísalo.")
    if not (boleta.proveedor.razon_social or boleta.proveedor.nombre_comercial):
        error("No se leyó el nombre del proveedor.")

    if not boleta.serie_numero:
        error("No se leyó la serie y el número del comprobante.")
    elif not SERIE_NUMERO.match(boleta.serie_numero):
        aviso(f"La serie y número «{boleta.serie_numero}» tiene un formato raro. Revísalo.")

    fecha = _fecha(boleta.fecha_emision)
    if fecha is None:
        error("No se leyó la fecha de emisión.")
    elif fecha > hoy:
        error(f"La fecha {fecha:%d/%m/%Y} está en el futuro.")
    elif (hoy - fecha).days > 365:
        aviso(f"La boleta es del {fecha:%d/%m/%Y}, hace más de un año.")

    if boleta.moneda != "PEN":
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
            error(f"Las líneas suman {esperado:.2f}, pero el total de la boleta es {boleta.total:.2f}.")

    if boleta.op_gravadas is not None and boleta.igv is not None:
        if abs(boleta.op_gravadas * TASA_IGV - boleta.igv) > TOLERANCIA:
            aviso(
                f"El IGV ({boleta.igv:.2f}) no es el 18 % de la base ({boleta.op_gravadas:.2f}). "
                "Puede haber productos exonerados."
            )

    if boleta.observaciones:
        aviso(f"Nota de la lectura: {boleta.observaciones}")
    return p


def hay_errores(problemas: list[Problema]) -> bool:
    return any(x.nivel == "error" for x in problemas)
