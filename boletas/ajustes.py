from __future__ import annotations

import re
from dataclasses import dataclass, field

from .modelos import Boleta, Linea
from .validacion import (
    TASA_IGV,
    TOLERANCIA,
    importe_calculado,
    subtotal_lineas,
    total_calculado,
)

UNIDADES_POR_PAQUETE = frozenset(
    {2, 3, 4, 5, 6, 8, 10, 12, 15, 16, 18, 20, 24, 25, 30, 36, 40, 48, 50, 60, 72, 100, 120, 144}
)
UNIDADES_CON_CANTIDAD = {
    "DOC": 12,
    "DOCENA": 12,
    "DZN": 12,
    "DCN": 12,
    "DOCE": 12,
    "CTO": 100,
    "CIENTO": 100,
    "CEN": 100,
    "MIL": 1000,
    "MILLAR": 1000,
    "MLL": 1000,
    "MLR": 1000,
}
UNIDADES_DE_ENVASE = {
    "CJA",
    "CAJA",
    "BX",
    "BOX",
    "PQT",
    "PAQ",
    "PAQUETE",
    "PK",
    "PCK",
    "PACK",
    "DISPLAY",
    "DSP",
    "BLS",
    "BOLSA",
    "BLT",
    "BULTO",
    "TIRA",
    "FARDO",
}
TASAS_DE_PERCEPCION = (0.02, 0.01, 0.005)
_MARCA = re.compile(
    r"(?i)(?<!\d)(?<!\d\s)(?:\bx|\bbox|\bpack|\bpaq(?:uete)?|\bcaja|\bdisplay|\bblister)\s*(?:de\s*)?(\d{1,4})(?!\d)"
    r"(?!\s*(?:hojas?|ml|gr?|cm|mm|kg|lts?|l|m|mts?|metros|oz|onz|cc|w|v|mah|gb)\b)"
)
_DOCENA = re.compile(r"(?i)\b(docena|doc)\b")


@dataclass(frozen=True)
class PorUnidad:
    descripcion: str
    cantidad_impresa: float
    precio_impreso: float
    cantidad: float
    importe: float
    por_paquete: int


@dataclass
class Ajustes:
    por_unidad: list[PorUnidad] = field(default_factory=list)
    precio_sin_igv: list[str] = field(default_factory=list)
    precio_con_igv: list[str] = field(default_factory=list)
    igv_incluido: bool = False
    bonificaciones: list[str] = field(default_factory=list)
    descuentos: list[tuple[str, float]] = field(default_factory=list)
    percepcion: tuple[float, float] | None = None
    retencion: float | None = None


def marcas_de_paquete(linea: Linea) -> set[int]:
    marcas = {int(m) for m in _MARCA.findall(linea.descripcion) if 1 < int(m) <= 1000}
    if _DOCENA.search(linea.descripcion):
        marcas.add(12)
    return marcas


def _unidad(linea: Linea) -> str:
    palabra = re.match(r"[A-Z]+", (linea.unidad or "").strip().upper())
    return palabra.group() if palabra else ""


def unidades_de_la_unidad(linea: Linea) -> int | None:
    unidad = _unidad(linea)
    if unidad in UNIDADES_CON_CANTIDAD:
        return UNIDADES_CON_CANTIDAD[unidad]
    if unidad in UNIDADES_DE_ENVASE:
        numero = re.search(r"\d{1,4}", linea.unidad or "")
        if numero and int(numero.group()) > 1:
            return int(numero.group())
        marcas = marcas_de_paquete(linea)
        return marcas.pop() if len(marcas) == 1 else None
    return None


def es_envase(linea: Linea) -> bool:
    unidad = _unidad(linea)
    return unidad in UNIDADES_CON_CANTIDAD or unidad in UNIDADES_DE_ENVASE


def _aceptable(por_paquete: int, linea: Linea) -> bool:
    return (
        por_paquete in UNIDADES_POR_PAQUETE
        or por_paquete in marcas_de_paquete(linea)
        or por_paquete == unidades_de_la_unidad(linea)
    )


def igv_cruzado(linea: Linea) -> str | None:
    if linea.descuento_pct or linea.descuento_monto or linea.cantidad <= 0 or linea.importe <= 0:
        return None
    if linea.exonerado or abs(importe_calculado(linea) - linea.importe) <= TOLERANCIA:
        return None
    margen = linea.cantidad * 0.005 * (1 + TASA_IGV) + TOLERANCIA
    bruto = linea.cantidad * linea.precio_unitario
    if abs(bruto - linea.importe * (1 + TASA_IGV)) <= margen:
        return "precio_con_igv"
    if abs(bruto * (1 + TASA_IGV) - linea.importe) <= margen:
        return "importe_con_igv"
    return None


def por_unidad(linea: Linea) -> tuple[float, float, int] | None:
    if linea.precio_unitario <= 0 or linea.importe <= 0 or linea.cantidad <= 0:
        return None
    if (linea.descuento_pct or 0) >= 100:
        return None
    factor = 1 - (linea.descuento_pct or 0) / 100
    bruto = linea.importe + (linea.descuento_monto or 0)
    efectivo = linea.precio_unitario * factor
    if abs(importe_calculado(linea) - linea.importe) <= TOLERANCIA:
        por_paquete = unidades_de_la_unidad(linea)
        unidades = linea.cantidad * (por_paquete or 0)
        if por_paquete and por_paquete > 1 and abs(unidades - round(unidades)) < 1e-6:
            return float(round(unidades)), round(linea.precio_unitario / por_paquete, 6), por_paquete
        return None
    if linea.cantidad != int(linea.cantidad):
        return None
    cantidad = int(linea.cantidad)
    veces = round(bruto / efectivo)
    diferencia = abs(veces * efectivo - bruto)
    if (
        veces > cantidad
        and veces % cantidad == 0
        and _aceptable(veces // cantidad, linea)
        and diferencia <= veces * 0.005 + TOLERANCIA
    ):
        return float(veces), round(bruto / (veces * factor), 6), veces // cantidad
    if (
        1 <= veces < cantidad
        and cantidad % veces == 0
        and _aceptable(cantidad // veces, linea)
        and diferencia <= TOLERANCIA
    ):
        return float(cantidad), round(bruto / (cantidad * factor), 6), cantidad // veces
    return None


def lineas_suman_el_total(boleta: Boleta) -> bool:
    if boleta.total is None or not boleta.lineas:
        return False
    base = subtotal_lineas(boleta) - (boleta.descuento_global or 0) + (boleta.otros_cargos or 0)
    return abs(base - boleta.total) <= TOLERANCIA


def _retencion_de_la_liquidacion(boleta: Boleta) -> float | None:
    if boleta.tipo != "liquidacion_de_compra" or boleta.op_gravadas is None or boleta.igv is None:
        return None
    importe_total = round(boleta.op_gravadas + boleta.igv, 2)
    if boleta.total is None or abs(boleta.total - importe_total) <= TOLERANCIA:
        return None
    if abs(subtotal_lineas(boleta) - (boleta.descuento_global or 0) - boleta.op_gravadas) > TOLERANCIA:
        return None
    if boleta.otros_cargos is not None and boleta.otros_cargos < 0:
        return round(-boleta.otros_cargos, 2)
    if boleta.total < boleta.op_gravadas:
        return round(boleta.op_gravadas - boleta.total, 2)
    return round(importe_total - boleta.total, 2)


def _igv_esta_incluido(boleta: Boleta) -> bool:
    if boleta.precios_incluyen_igv or not lineas_suman_el_total(boleta):
        return False
    if boleta.tipo not in ("factura", "liquidacion_de_compra"):
        return True
    if boleta.igv is not None and boleta.igv <= TOLERANCIA:
        return True
    return (
        boleta.op_gravadas is not None
        and boleta.igv is not None
        and abs(boleta.op_gravadas + boleta.igv - boleta.total) <= TOLERANCIA
        and abs(boleta.op_gravadas * TASA_IGV - boleta.igv) <= 0.05
    )


def _percepcion(boleta: Boleta) -> tuple[float, float] | None:
    if boleta.otros_cargos or boleta.total is None or not boleta.lineas:
        return None
    esperado = total_calculado(boleta)
    diferencia = round(boleta.total - esperado, 2)
    if diferencia < 1:
        return None
    for tasa in TASAS_DE_PERCEPCION:
        if abs(diferencia - round(esperado * tasa, 2)) <= TOLERANCIA:
            return diferencia, tasa
    return None


def ajustar(boleta: Boleta) -> tuple[Boleta, Ajustes]:
    b = boleta.model_copy(deep=True)
    hechos = Ajustes()
    if not b.es_comprobante:
        return b, hechos

    negativas = [l for l in b.lineas if l.importe < 0]
    if negativas and len(negativas) < len(b.lineas):
        for linea in negativas:
            hechos.descuentos.append((linea.descripcion, -linea.importe))
        b.descuento_global = round((b.descuento_global or 0) + sum(-l.importe for l in negativas), 2)
        b.lineas = [l for l in b.lineas if l.importe >= 0]

    for linea in b.lineas:
        if linea.importe == 0 and linea.precio_unitario > 0 and not linea.descuento_pct and not linea.descuento_monto:
            linea.descuento_pct = 100.0
            hechos.bonificaciones.append(linea.descripcion)
            continue
        cruce = igv_cruzado(linea) if b.tipo == "factura" else None
        if cruce == "importe_con_igv" or (cruce == "precio_con_igv" and not b.precios_incluyen_igv):
            linea.precio_unitario = round(linea.importe / linea.cantidad, 6)
            (hechos.precio_sin_igv if cruce == "precio_con_igv" else hechos.precio_con_igv).append(linea.descripcion)
            continue
        cambio = por_unidad(linea)
        if cambio is None:
            continue
        cantidad, precio, por_paquete = cambio
        hechos.por_unidad.append(
            PorUnidad(linea.descripcion, linea.cantidad, linea.precio_unitario, cantidad, linea.importe, por_paquete)
        )
        linea.cantidad, linea.precio_unitario = cantidad, precio

    retencion = _retencion_de_la_liquidacion(b)
    if retencion is not None:
        hechos.retencion = retencion
        b.total = round(b.op_gravadas + b.igv, 2)
        b.precios_incluyen_igv = False
        if b.otros_cargos and abs(b.otros_cargos + retencion) <= TOLERANCIA:
            b.otros_cargos = None

    if _igv_esta_incluido(b):
        b.precios_incluyen_igv = True
        hechos.igv_incluido = True

    hechos.percepcion = _percepcion(b)
    if hechos.percepcion:
        b.otros_cargos = hechos.percepcion[0]
    return b, hechos


def _numeros(numeros: list[int]) -> str:
    tramos: list[list[int]] = []
    for n in numeros:
        if tramos and n == tramos[-1][-1] + 1:
            tramos[-1].append(n)
        else:
            tramos.append([n])
    partes = []
    for tramo in tramos:
        if len(tramo) >= 3:
            partes.append(f"{tramo[0]} a {tramo[-1]}")
        else:
            partes += [str(n) for n in tramo]
    return partes[0] if len(partes) == 1 else ", ".join(partes[:-1]) + " y " + partes[-1]


def _lineas(numeros: list[int]) -> str:
    return f"La línea {numeros[0]}" if len(numeros) == 1 else f"Las líneas {_numeros(numeros)}"


def _aviso_por_unidad(boleta: Boleta, ajustes: Ajustes) -> str | None:
    vigentes = []
    for n, linea in enumerate(boleta.lineas, start=1):
        for a in ajustes.por_unidad:
            if linea.descripcion == a.descripcion and linea.cantidad == a.cantidad and linea.importe == a.importe:
                vigentes.append((n, a))
                break
    if not vigentes:
        return None
    n, a = vigentes[0]
    precio = boleta.lineas[n - 1].precio_unitario
    if a.cantidad > a.cantidad_impresa:
        paquetes = "paquete" if a.cantidad_impresa == 1 else "paquetes"
        ejemplo = f"{a.cantidad_impresa:g} {paquetes} de {a.por_paquete} → {a.cantidad:g} unidades de S/ {precio:.2f}"
        del_paquete = round(a.cantidad * precio / a.cantidad_impresa, 2)
        volver = f"«{n} cantidad {a.cantidad_impresa:g}» y «{n} precio {del_paquete:g}»"
    else:
        ejemplo = (
            f"{a.cantidad:g} unidades a S/ {a.precio_impreso:.2f} el paquete de {a.por_paquete} → "
            f"S/ {precio:.2f} cada una"
        )
        volver = f"«{n} cantidad {a.cantidad / a.por_paquete:g}» y «{n} precio {a.precio_impreso:g}»"
    numeros = [v[0] for v in vigentes]
    if len(numeros) == 1:
        return (
            f"La línea {n} venía por paquete: la registro por unidad ({ejemplo}). Si se vende por paquete, "
            f"escribe {volver}."
        )
    return (
        f"Las líneas {_numeros(numeros)} venían por paquete: las registro por unidad (línea {n}: {ejemplo}). "
        f"Si alguna se vende por paquete, escribe {volver}."
    )


def _aviso_paquetes_sin_cambiar(boleta: Boleta, ajustes: Ajustes) -> str | None:
    tocadas = {a.descripcion for a in ajustes.por_unidad} | set(ajustes.bonificaciones)
    candidatas = []
    for n, linea in enumerate(boleta.lineas, start=1):
        if linea.descripcion in tocadas or linea.cantidad != int(linea.cantidad) or linea.importe <= 0:
            continue
        if abs(importe_calculado(linea) - linea.importe) > TOLERANCIA:
            continue
        marcas = sorted(k for k in marcas_de_paquete(linea) if linea.cantidad < k or linea.cantidad % k)
        if marcas or (es_envase(linea) and not unidades_de_la_unidad(linea)):
            candidatas.append((n, marcas[0] if len(marcas) == 1 else None))
    if not candidatas:
        return None
    n, k = candidatas[0]
    ejemplo = f"«{n} paquete {k}»" if k else f"«{n} paquete 12» con las unidades que trae"
    verbo = "parece venir" if len(candidatas) == 1 else "parecen venir"
    return (
        f"{_lineas([c[0] for c in candidatas])} {verbo} por paquete o caja, con el precio del paquete. "
        f"Si lo vendes por unidad, escribe {ejemplo}."
    )


def avisos(boleta: Boleta, ajustes: Ajustes | None) -> list[str]:
    if ajustes is None:
        return []
    filas = []
    if ajustes.descuentos and boleta.descuento_global:
        total = sum(m for _, m in ajustes.descuentos)
        nombres = ", ".join(f"«{d}» (−{m:.2f})" for d, m in ajustes.descuentos)
        filas.append(f"Pasé a descuento del total {nombres}: los S/ {total:.2f} bajan el costo de los productos.")
    bonificadas = [
        n
        for n, l in enumerate(boleta.lineas, start=1)
        if l.descripcion in ajustes.bonificaciones and l.descuento_pct == 100
    ]
    if bonificadas:
        if len(bonificadas) == 1:
            filas.append(f"La línea {bonificadas[0]} es una bonificación (importe 0): entra al stock con costo 0.")
        else:
            filas.append(f"{_lineas(bonificadas)} son bonificaciones (importe 0): entran al stock con costo 0.")
    sin_igv = [n for n, l in enumerate(boleta.lineas, start=1) if l.descripcion in ajustes.precio_sin_igv]
    if sin_igv:
        verbo = "traía" if len(sin_igv) == 1 else "traían"
        filas.append(
            f"{_lineas(sin_igv)} {verbo} el precio unitario con IGV y el importe sin IGV: uso el precio sin "
            "IGV (importe ÷ cantidad); el IGV se suma al final, como en la factura."
        )
    con_igv = [
        n for n, l in enumerate(boleta.lineas, start=1) if l.descripcion in getattr(ajustes, "precio_con_igv", [])
    ]
    if con_igv:
        verbo = "traía" if len(con_igv) == 1 else "traían"
        filas.append(
            f"{_lineas(con_igv)} {verbo} el precio unitario sin IGV y el importe con IGV: uso el precio con "
            "IGV (importe ÷ cantidad)."
        )
    texto = _aviso_por_unidad(boleta, ajustes)
    if texto:
        filas.append(texto)
    texto = _aviso_paquetes_sin_cambiar(boleta, ajustes)
    if texto:
        filas.append(texto)
    if ajustes.igv_incluido and boleta.precios_incluyen_igv and lineas_suman_el_total(boleta):
        filas.append(
            "Los importes de las líneas ya suman el total: el IGV está incluido y el costo de cada "
            "producto es lo que pagaste. Si no es así, escribe «igv aparte»."
        )
    if getattr(ajustes, "retencion", None):
        filas.append(
            f"En la liquidación tomo el importe total (valor + IGV): el IGV que UYAY paga a SUNAT va al costo. "
            f"La retención de renta (S/ {ajustes.retencion:.2f}) no es un costo: es del vendedor y se paga "
            "aparte a SUNAT."
        )
    if ajustes.percepcion and boleta.otros_cargos == ajustes.percepcion[0]:
        monto, tasa = ajustes.percepcion
        filas.append(
            f"Los S/ {monto:.2f} que el total tiene de más son la percepción del {tasa * 100:g} %: van en "
            "la factura como otro cargo, no en el costo de los productos."
        )
    return filas
