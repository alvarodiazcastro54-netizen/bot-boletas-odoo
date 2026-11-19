from __future__ import annotations

import logging

import zxingcpp

from .archivos import abrir_imagen
from .lectura import LecturaError
from .sunat import DatosQR, interpretar_qr

log = logging.getLogger(__name__)
LADO_MAXIMO = 3000


def leer_qr(imagen: bytes | None) -> DatosQR | None:
    if not imagen:
        return None
    try:
        foto = abrir_imagen(imagen)
    except LecturaError:
        return None
    foto.thumbnail((LADO_MAXIMO, LADO_MAXIMO))
    try:
        codigos = zxingcpp.read_barcodes(foto.convert("L"), formats=zxingcpp.BarcodeFormat.QRCode)
    except Exception as exc:
        log.warning("No pude buscar el QR: %s", exc)
        return None
    for codigo in codigos:
        datos = interpretar_qr(codigo.text or "")
        if datos:
            return datos
    return None
