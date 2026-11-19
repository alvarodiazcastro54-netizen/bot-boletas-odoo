from __future__ import annotations

import datetime as dt
import re
import unicodedata

from .modelos import Boleta

AYUDA = (
    "Escribe una corrección por mensaje:\n"
    "• <code>3 venta 65</code> (precio de venta en la tienda) · <code>3 tienda Gancho mariposa dorado</code> "
    "(nombre en la tienda)\n"
    "• <code>3 nombre SORBETE ACERO INOX RECTO Y OBLICUO</code> (como dice la boleta)\n"
    "• <code>3 cantidad 2</code> · <code>3 precio 11</code> (precio de compra) · <code>3 descuento 50</code> · "
    "<code>3 importe 16.50</code> · <code>3 codigo 5509007</code> · <code>3 categoria cocina</code>\n"
    "• <code>3 paquete 12</code> (la línea 3 viene por paquete de 12 y la vendes por unidad)\n"
    "• <code>borrar 3</code> (quita la línea 3) · <code>cargo 3</code> (flete, carreta o delivery: va a la "
    "factura y no se crea como producto)\n"
    "• <code>descuento total 15</code> (descuento sobre toda la compra) · <code>otros 33.60</code> "
    "(percepción, flete u otro monto que se suma al total)\n"
    "• <code>igv incluido</code> (los precios ya traen IGV) · <code>igv aparte</code> (el IGV se suma al final) · "
    "<code>3 exonerado</code> / <code>3 gravado</code> (la línea 3 no paga IGV, o sí)\n"
    "• <code>ruc 20101567428</code> · <code>proveedor SB TRADING SRL</code> · "
    "<code>numero B410-00170132</code> · <code>fecha 05/11/2026</code> · <code>total 58.30</code> · "
    "<code>tipo nota</code> (nota de venta sin RUC) · <code>tipo liquidacion</code> · <code>dni 12345678</code> "
    "(vendedor de una liquidación de compra)"
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
    "venta": "precio_venta",
    "pv": "precio_venta",
    "tienda": "nombre_tienda",
}
_NUMERICOS = {"cantidad", "precio_unitario", "descuento_pct", "importe", "precio_venta"}
_TIPOS = {
    "nota": "nota_de_venta",
    "nota de venta": "nota_de_venta",
    "cotizacion": "cotizacion",
    "presupuesto": "cotizacion",
    "proforma": "cotizacion",
    "liquidacion": "liquidacion_de_compra",
    "liquidacion de compra": "liquidacion_de_compra",
    "boleta": "boleta",
    "factura": "factura",
    "ticket": "ticket",
}


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


def _linea_valida(b: Boleta, n: int) -> int:
    if not 1 <= n <= len(b.lineas):
        raise CorreccionInvalida(f"No hay línea {n}.")
    return n


def aplicar(boleta: Boleta, texto: str, categorias: list[str] | None = None) -> tuple[Boleta, str]:
    texto = " ".join(texto.split())
    b = boleta.model_copy(deep=True)

    m = re.fullmatch(r"(?i)(\d+)\s+(categoria|categoría|cat)\s+(.+)", texto)
    if m:
        n = _linea_valida(b, int(m.group(1)))
        if not categorias:
            raise CorreccionInvalida("No pude leer las categorías de Odoo.")
        b.lineas[n - 1].categoria = buscar_categoria(m.group(3), categorias)
        return b, f"Línea {n}: categoría = {b.lineas[n - 1].categoria}."

    m = re.fullmatch(r"(?i)(\d+)\s+(?:paquete|paq|caja|x)\s+(?:de\s+|x\s*)?(\d+)", texto)
    if m:
        n, unidades = int(m.group(1)), int(m.group(2))
        _linea_valida(b, n)
        if unidades < 2:
            raise CorreccionInvalida("El paquete tiene que traer 2 unidades o más.")
        linea = b.lineas[n - 1]
        linea.cantidad = round(linea.cantidad * unidades, 6)
        linea.precio_unitario = round(linea.precio_unitario / unidades, 6)
        return b, (
            f"Línea {n}: {linea.cantidad:g} unidades a S/ {linea.precio_unitario:.2f} cada una "
            f"(paquetes de {unidades})."
        )

    m = re.fullmatch(r"(?i)(?:descuento|dscto)\s+(?:total|global|general)\s+(?:s/\s*)?([\d.,]+)", texto)
    if m:
        monto = _numero(m.group(1))
        b.descuento_global = monto or None
        return b, f"Descuento sobre el total = {monto:.2f}."

    m = re.fullmatch(r"(?i)(?:otros|otros cargos|percepcion|percepción)\s+(?:s/\s*)?(-?[\d.,]+)", texto)
    if m:
        monto = _numero(m.group(1))
        b.otros_cargos = monto or None
        return b, f"Otros cargos (percepción, flete, ICBPER) = {monto:.2f}: van en la factura, no en el costo."

    m = re.fullmatch(r"(?i)igv\s+(incluido|aparte|no incluido)", texto)
    if m:
        incluido = m.group(1).lower() == "incluido"
        b.precios_incluyen_igv = incluido
        if incluido:
            return b, "Los precios ya incluyen el IGV: el costo es lo que pagaste."
        return b, "Los precios no incluyen el IGV: se suma el 18 % al final."

    m = re.fullmatch(r"(?i)(\d+)\s+(exonerado|exonerada|inafecto|inafecta|sin igv|gravado|gravada|con igv)", texto)
    if m:
        n = _linea_valida(b, int(m.group(1)))
        b.lineas[n - 1].exonerado = m.group(2).lower() in ("exonerado", "exonerada", "inafecto", "inafecta", "sin igv")
        estado = "sin IGV (exonerada)" if b.lineas[n - 1].exonerado else "con IGV"
        return b, f"Línea {n}: {estado}."

    m = re.fullmatch(r"(?i)dni\s+(\d{8})", texto)
    if m:
        b.proveedor.dni = m.group(1)
        return b, f"DNI del vendedor = {m.group(1)}."

    m = re.fullmatch(r"(?i)(?:cargo|flete)\s+(\d+)", texto)
    if m:
        n = _linea_valida(b, int(m.group(1)))
        quitada = b.lineas.pop(n - 1)
        b.otros_cargos = round((b.otros_cargos or 0) + quitada.importe, 2)
        return b, f"La línea {n} ({quitada.descripcion}) pasa a otros cargos: va en la factura, no como producto."

    m = re.fullmatch(r"(?i)borrar\s+(\d+)", texto)
    if m:
        n = _linea_valida(b, int(m.group(1)))
        quitada = b.lineas.pop(n - 1)
        return b, f"Quité la línea {n} ({quitada.descripcion})."

    m = re.fullmatch(r"(\d+)\s+(\S+)\s+(.+)", texto)
    if m:
        n, campo, valor = int(m.group(1)), m.group(2).lower(), m.group(3)
        if campo not in _CAMPOS_LINEA:
            raise CorreccionInvalida(f"No conozco el campo «{campo}».")
        _linea_valida(b, n)
        linea = b.lineas[n - 1]
        atributo = _CAMPOS_LINEA[campo]
        if atributo in _NUMERICOS:
            numero = _numero(valor)
            if atributo == "precio_venta" and numero <= 0:
                raise CorreccionInvalida("El precio de venta tiene que ser mayor que cero.")
            setattr(linea, atributo, numero)
        else:
            setattr(linea, atributo, valor.strip())
        if atributo == "descripcion":
            linea.nombre_cortado = False
        return b, f"Línea {n}: {campo} = {valor.strip()}."

    m = re.fullmatch(r"(?i)tipo\s+(.+)", texto)
    if m:
        tipo = _TIPOS.get(_sin_tildes(m.group(1)).strip())
        if not tipo:
            raise CorreccionInvalida("Los tipos son: boleta, factura, ticket o nota (nota de venta).")
        b.tipo = tipo
        return b, f"Tipo = {tipo.replace('_', ' ')}."

    m = re.fullmatch(r"(?i)(ruc|proveedor|razon|numero|número|serie|fecha|total)\s+(.+)", texto)
    if m:
        campo, valor = m.group(1).lower(), m.group(2).strip()
        if campo == "ruc":
            b.proveedor.ruc = re.sub(r"\D", "", valor) or None
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
