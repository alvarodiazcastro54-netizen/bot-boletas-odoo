from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

TipoComprobante = Literal[
    "boleta",
    "factura",
    "ticket",
    "nota_de_venta",
    "cotizacion",
    "liquidacion_de_compra",
    "nota_de_credito",
    "nota_de_debito",
    "guia_de_remision",
    "recibo_por_honorarios",
    "otro",
]


class Proveedor(BaseModel):
    ruc: str | None = Field(description="RUC del emisor (11 dígitos), solo números. null si no se lee.")
    razon_social: str | None = Field(description="Razón social del emisor, tal como está impresa.")
    nombre_comercial: str | None = Field(description="Nombre comercial o de la tienda, si aparece aparte.")
    direccion: str | None = Field(description="Dirección del emisor.")
    dni: str | None = Field(
        default=None,
        description="DNI del vendedor (8 dígitos), solo en una liquidación de compra. null en lo demás.",
    )


class Linea(BaseModel):
    codigo: str | None = Field(description="Código del producto impreso en la boleta. null si no hay.")
    descripcion: str = Field(description="Descripción del producto copiada tal cual, sin corregir ni completar.")
    nombre_cortado: bool = Field(
        description="true si la descripción se ve cortada por el ancho del papel "
        "(termina a media palabra o en un pedazo como '+ CE' o '1:')."
    )
    cantidad: float = Field(description="Cantidad impresa, tal cual, aunque sea de paquetes, cajas o docenas.")
    unidad: str | None = Field(
        description="Unidad impresa de la cantidad (UND, DOC, CJA, PQT, KG, etc.). null si no hay."
    )
    exonerado: bool = Field(
        default=False,
        description="true solo si el comprobante indica que esta línea no paga IGV (exonerada o inafecta, como los "
        "libros): una columna o marca «EXO», «E», «INA», «Exonerado» o la línea va en «Op. exonerada». false si paga "
        "IGV o no se sabe.",
    )
    precio_unitario: float = Field(description="Precio unitario impreso, antes del descuento.")
    descuento_pct: float = Field(description="Descuento de la línea en porcentaje. 0 si no hay.")
    descuento_monto: float | None = Field(
        description="Descuento de la línea en soles, solo si la boleta lo muestra como monto y no como porcentaje."
    )
    importe: float = Field(description="Importe final de la línea, ya con el descuento.")
    categoria: str | None = Field(
        description="Categoría de la tienda que le corresponde al producto, elegida de la lista dada "
        "(la más específica). null si no se dio lista o ninguna encaja."
    )
    nombre_tienda: str | None = Field(
        default=None,
        description="Nombre claro para mostrarlo a los clientes en la tienda web: palabras completas en vez de "
        "abreviaturas, sin códigos ni medidas de empaque, con mayúscula inicial y el resto en minúsculas "
        "(salvo marcas). No inventes datos que no estén en la descripción. null si no se puede mejorar.",
    )
    precio_venta: float | None = Field(
        default=None, description="Siempre null: el precio de venta lo decide la tienda."
    )


def al_dia(boleta: Boleta) -> Boleta:
    lineas_viejas = any(set(Linea.model_fields) - set(vars(l)) for l in boleta.lineas)
    if (
        not lineas_viejas
        and not set(Proveedor.model_fields) - set(vars(boleta.proveedor))
        and not set(Boleta.model_fields) - set(vars(boleta))
    ):
        return boleta
    datos = dict(vars(boleta))
    datos["proveedor"] = dict(vars(boleta.proveedor))
    datos["lineas"] = [dict(vars(l)) for l in boleta.lineas]
    return Boleta.model_validate(datos)


class Boleta(BaseModel):
    es_comprobante: bool = Field(description="false si la foto no es una boleta, factura o ticket de compra.")
    legible: bool = Field(description="false si la foto está tan borrosa o cortada que no se puede leer bien.")
    tipo: TipoComprobante
    serie_numero: str | None = Field(description="Serie y número, por ejemplo B410-00170132.")
    fecha_emision: str | None = Field(description="Fecha de emisión en formato AAAA-MM-DD.")
    moneda: Literal["PEN", "USD", "OTRA"] = Field(
        description="PEN (soles), USD (dólares) u OTRA si está en cualquier otra moneda (yuanes, euros, etc.)."
    )
    proveedor: Proveedor
    cliente_documento: str | None = Field(
        description="DNI o RUC del cliente impreso en el comprobante (99999999 = cliente final)."
    )
    precios_incluyen_igv: bool = Field(
        description="true si los precios de las líneas ya incluyen IGV (lo normal en boletas y tickets). "
        "false si son valores sin IGV, como en muchas facturas."
    )
    lineas: list[Linea]
    descuento_global: float | None = Field(
        description="Descuento sobre el total, aparte de los descuentos de cada línea. null si no hay."
    )
    otros_cargos: float | None = Field(
        description="ICBPER (bolsas), percepción, flete, redondeo u otros montos que suman o restan al total y "
        "no son productos. "
        "Negativo si restan. null si no hay."
    )
    op_gravadas: float | None = Field(description="Operaciones gravadas (base imponible), si aparece.")
    igv: float | None = Field(description="Monto del IGV, si se ve. null si está tapado o no aparece.")
    total: float | None = Field(description="Total a pagar.")
    observaciones: str | None = Field(
        description="Partes ilegibles o tapadas, dudas de lectura u otra cosa que el usuario deba revisar."
    )
