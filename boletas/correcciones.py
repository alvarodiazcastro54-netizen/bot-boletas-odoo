from __future__ import annotations

import datetime as dt
import re
import unicodedata

from .modelos import Boleta

AYUDA = (
    "Escribe una corrección por mensaje:\n"
    "• <code>3 nombre SORBETE ACERO INOX RECTO Y OBLICUO</code>\n"
    "• <code>3 cantidad 2</code> · <code>3 precio 11</code> · <code>3 descuento 50</code> · "
    "<code>3 importe 16.50</code> · <code>3 codigo 5509007</code> · <code>3 categoria cocina</code>\n"
    "• <code>borrar 3</code> (quita la línea 3)\n"
    "• <code>ruc 20101567428</code> · <code>proveedor SB TRADING SRL</code> · "
    "<code>numero B410-00170132</code> · <code>fecha 05/11/2026</code> · <code>total 58.30</code>"
)

_CAMPOS_LINEA = {
    "nombre": "descripcion",
    "descripcion": "descripcion",
    "cantidad": "cantidad",
    "cant": "cantidad",
    "precio": "precio_unitario",
    "descuento": "descuento_pct",
    "dscto": "descuento_pct",
    "importe": "importe",
    "codigo": "codigo",
    "código": "codigo",
}
_NUMERICOS = {"cantidad", "precio_unitario", "descuento_pct", "importe"}


class CorreccionInvalida(ValueError):
    pass


def _numero(texto: str) -> float:
    limpio = texto.strip().replace("S/", "").replace("%", "").replace(",", ".").strip()
    try:
        return float(limpio)
    except ValueError:
        raise CorreccionInvalida(f"«{texto}» no es un número.") from None


def _fecha(texto: str) -> str:
    texto = texto.strip()
    for formato in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y"):
        try:
            return dt.datetime.strptime(texto, formato).date().isoformat()
        except ValueError:
            pass
    raise CorreccionInvalida(f"«{texto}» no es una fecha (usa 05/11/2026).")


def _sin_tildes(texto: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")


def buscar_categoria(texto: str, categorias: list[str]) -> str:
    buscado = _sin_tildes(texto.strip())
    exactas = [c for c in categorias if _sin_tildes(c) == buscado or _sin_tildes(c.split(" / ")[-1]) == buscado]
    if len(exactas) == 1:
        return exactas[0]
    parciales = [c for c in categorias if buscado in _sin_tildes(c)]
    if len(parciales) == 1:
        return parciales[0]
    if not parciales:
        raise CorreccionInvalida(f"No hay ninguna categoría «{texto}». /categorias muestra la lista.")
    opciones = "\n".join(f"• {c}" for c in parciales[:10])
    raise CorreccionInvalida(f"«{texto}» coincide con varias categorías; sé más específico:\n{opciones}")


def aplicar(boleta: Boleta, texto: str, categorias: list[str] | None = None) -> tuple[Boleta, str]:
    texto = " ".join(texto.split())
    b = boleta.model_copy(deep=True)

    m = re.fullmatch(r"(?i)(\d+)\s+(categoria|categoría|cat)\s+(.+)", texto)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= len(b.lineas):
            raise CorreccionInvalida(f"No hay línea {n}.")
        if not categorias:
            raise CorreccionInvalida("No pude leer las categorías de Odoo.")
        b.lineas[n - 1].categoria = buscar_categoria(m.group(3), categorias)
        return b, f"Línea {n}: categoría = {b.lineas[n - 1].categoria}."

    m = re.fullmatch(r"(?i)borrar\s+(\d+)", texto)
    if m:
        n = int(m.group(1))
        if not 1 <= n <= len(b.lineas):
            raise CorreccionInvalida(f"No hay línea {n}.")
        quitada = b.lineas.pop(n - 1)
        return b, f"Quité la línea {n} ({quitada.descripcion})."

    m = re.fullmatch(r"(\d+)\s+(\S+)\s+(.+)", texto)
    if m:
        n, campo, valor = int(m.group(1)), m.group(2).lower(), m.group(3)
        if campo not in _CAMPOS_LINEA:
            raise CorreccionInvalida(f"No conozco el campo «{campo}».")
        if not 1 <= n <= len(b.lineas):
            raise CorreccionInvalida(f"No hay línea {n}.")
        linea = b.lineas[n - 1]
        atributo = _CAMPOS_LINEA[campo]
        if atributo in _NUMERICOS:
            setattr(linea, atributo, _numero(valor))
        else:
            setattr(linea, atributo, valor.strip())
        if atributo == "descripcion":
            linea.nombre_cortado = False
        return b, f"Línea {n}: {campo} = {valor.strip()}."

    m = re.fullmatch(r"(?i)(ruc|proveedor|razon|numero|número|serie|fecha|total)\s+(.+)", texto)
    if m:
        campo, valor = m.group(1).lower(), m.group(2).strip()
        if campo == "ruc":
            b.proveedor.ruc = re.sub(r"\D", "", valor)
        elif campo in ("proveedor", "razon"):
            b.proveedor.razon_social = valor
        elif campo in ("numero", "número", "serie"):
            b.serie_numero = valor.upper()
        elif campo == "fecha":
            b.fecha_emision = _fecha(valor)
        elif campo == "total":
            b.total = _numero(valor)
        return b, f"{campo.capitalize()} = {valor}."

    raise CorreccionInvalida("No entendí la corrección.")
