from __future__ import annotations

import math
import statistics
import time
from decimal import ROUND_HALF_UP, Decimal

MARGEN_POR_DEFECTO = 2.0
MINIMO_DE_MUESTRAS = 5
GANANCIA_MINIMA = 1.1
VIGENCIA = 3600


def _redondeo(valor: float, paso: float) -> float:
    unidades = Decimal(str(valor)) / Decimal(str(paso))
    return float(unidades.quantize(Decimal(1), rounding=ROUND_HALF_UP) * Decimal(str(paso)))


def _paso(precio: float) -> float:
    if precio < 5:
        return 0.1
    if precio < 10:
        return 0.5
    return 5.0


def redondear(precio: float) -> float:
    if precio < 5:
        return math.ceil(round(precio * 10, 6)) / 10
    return round(max(_redondeo(precio, _paso(precio)), 0.1), 2)


def precio_sugerido(costo: float, margen: float) -> float:
    if costo <= 0:
        return 0.0
    precio = redondear(costo * margen)
    minimo = costo * GANANCIA_MINIMA
    while precio < minimo:
        precio = round(precio + _paso(precio), 2)
    return precio


def calcular_margenes(filas: list[tuple[str, float, float]]) -> dict[str, float]:
    grupos: dict[str, list[float]] = {}
    for categoria, venta, costo in filas:
        if venta <= 0 or costo <= 0 or not categoria:
            continue
        partes = categoria.split(" / ")
        for n in range(1, len(partes) + 1):
            grupos.setdefault(" / ".join(partes[:n]), []).append(venta / costo)
        grupos.setdefault("", []).append(venta / costo)
    return {
        nombre: round(statistics.median(valores), 2)
        for nombre, valores in grupos.items()
        if len(valores) >= MINIMO_DE_MUESTRAS
    }


def margen_para(margenes: dict[str, float], categoria: str | None) -> float:
    partes = (categoria or "").split(" / ") if categoria else []
    for n in range(len(partes), 0, -1):
        nombre = " / ".join(partes[:n])
        if nombre in margenes:
            return margenes[nombre]
    return margenes.get("", MARGEN_POR_DEFECTO)


class Margenes:
    def __init__(self, leer_filas):
        self._leer_filas = leer_filas
        self._datos: tuple[float, dict[str, float]] | None = None

    def todos(self) -> dict[str, float]:
        if self._datos is None or time.monotonic() - self._datos[0] > VIGENCIA:
            self._datos = (time.monotonic(), calcular_margenes(self._leer_filas()))
        return self._datos[1]

    def para(self, categoria: str | None) -> float:
        return margen_para(self.todos(), categoria)
