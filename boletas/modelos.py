from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

TipoComprobante = Literal["boleta", "factura", "ticket", "nota_de_venta", "otro"]


class Proveedor(BaseModel):
    ruc: Optional[str] = Field(description="RUC del emisor (11 dígitos), solo números. null si no se lee.")
    razon_social: Optional[str] = Field(description="Razón social del emisor, tal como está impresa.")
    nombre_comercial: Optional[str] = Field(description="Nombre comercial o de la tienda, si aparece aparte.")
    direccion: Optional[str] = Field(description="Dirección del emisor.")


class Linea(BaseModel):
    codigo: Optional[str] = Field(description="Código del producto impreso en la boleta. null si no hay.")
    descripcion: str = Field(description="Descripción del producto copiada tal cual, sin corregir ni completar.")
    nombre_cortado: bool = Field(
        description="true si la descripción se ve cortada por el ancho del papel "
        "(termina a media palabra o en un pedazo como '+ CE' o '1:')."
    )
    cantidad: float = Field(description="Cantidad comprada.")
    unidad: Optional[str] = Field(description="Unidad impresa (UND, KG, etc.). null si no hay.")
    precio_unitario: float = Field(description="Precio unitario impreso, antes del descuento.")
    descuento_pct: float = Field(description="Descuento de la línea en porcentaje. 0 si no hay.")
    descuento_monto: Optional[float] = Field(
        description="Descuento de la línea en soles, solo si la boleta lo muestra como monto y no como porcentaje."
    )
    importe: float = Field(description="Importe final de la línea, ya con el descuento.")
    categoria: Optional[str] = Field(
        description="Categoría de la tienda que le corresponde al producto, elegida de la lista dada "
        "(la más específica). null si no se dio lista o ninguna encaja."
    )


class Boleta(BaseModel):
    es_comprobante: bool = Field(description="false si la foto no es una boleta, factura o ticket de compra.")
    legible: bool = Field(description="false si la foto está tan borrosa o cortada que no se puede leer bien.")
    tipo: TipoComprobante
    serie_numero: Optional[str] = Field(description="Serie y número, por ejemplo B410-00170132.")
    fecha_emision: Optional[str] = Field(description="Fecha de emisión en formato AAAA-MM-DD.")
    moneda: Literal["PEN", "USD"]
    proveedor: Proveedor
    cliente_documento: Optional[str] = Field(
        description="DNI o RUC del cliente impreso en el comprobante (99999999 = cliente final)."
    )
    precios_incluyen_igv: bool = Field(
        description="true si los precios de las líneas ya incluyen IGV (lo normal en boletas y tickets). "
        "false si son valores sin IGV, como en muchas facturas."
    )
    lineas: list[Linea]
    descuento_global: Optional[float] = Field(
        description="Descuento sobre el total, aparte de los descuentos de cada línea. null si no hay."
    )
    otros_cargos: Optional[float] = Field(
        description="ICBPER (bolsas), redondeo u otros montos que suman o restan al total y no son productos. "
        "Negativo si restan. null si no hay."
    )
    op_gravadas: Optional[float] = Field(description="Operaciones gravadas (base imponible), si aparece.")
    igv: Optional[float] = Field(description="Monto del IGV, si se ve. null si está tapado o no aparece.")
    total: Optional[float] = Field(description="Total a pagar.")
    observaciones: Optional[str] = Field(
        description="Partes ilegibles o tapadas, dudas de lectura u otra cosa que el usuario deba revisar."
    )
