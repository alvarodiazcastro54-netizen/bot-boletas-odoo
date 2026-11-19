import io
import zipfile

import pillow_heif
import pytest
import zxingcpp
from PIL import Image

from boletas import archivos
from boletas.lectura import LecturaError
from boletas.qr import leer_qr
from boletas.sunat import DatosQR, NoEsCompra

from test_sunat import factura_xml


def _imagen(formato="JPEG", tamano=(600, 900), color=(240, 240, 240)) -> bytes:
    salida = io.BytesIO()
    Image.new("RGB", tamano, color).save(salida, format=formato)
    return salida.getvalue()


def _pdf(paginas=1) -> bytes:
    salida = io.BytesIO()
    hojas = [Image.new("RGB", (595, 842), (255, 255, 255)) for _ in range(paginas)]
    hojas[0].save(salida, format="PDF", save_all=True, append_images=hojas[1:])
    return salida.getvalue()


def _zip(contenido: dict[str, bytes]) -> bytes:
    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w") as z:
        for nombre, datos in contenido.items():
            z.writestr(nombre, datos)
    return salida.getvalue()


@pytest.mark.parametrize(
    "datos,tipo",
    [
        (_imagen(), "imagen"),
        (_imagen("PNG"), "imagen"),
        (_pdf(), "pdf"),
        (factura_xml(), "xml"),
        (b"\xef\xbb\xbf<?xml version='1.0'?><Invoice/>", "xml"),
        (_zip({"a.txt": b"x"}), "zip"),
        (b"hola", "otro"),
    ],
)
def test_reconoce_el_tipo_de_archivo(datos, tipo):
    assert archivos.tipo_de_archivo(datos) == tipo


def test_foto_normal_va_tal_cual():
    datos = _imagen()
    doc = archivos.preparar(datos)
    assert doc.imagen == datos and doc.adjuntos == [("boleta.jpg", datos)] and doc.origen == "foto"


def test_foto_de_iphone_heic_se_pasa_a_jpg():
    salida = io.BytesIO()
    pillow_heif.from_pillow(Image.new("RGB", (64, 64), (10, 120, 200))).save(salida, format="HEIF")
    doc = archivos.preparar(salida.getvalue())
    assert Image.open(io.BytesIO(doc.imagen)).format == "JPEG" and doc.adjuntos[0][0] == "boleta.jpg"


def test_pdf_de_dos_paginas_va_lado_a_lado_y_se_adjunta_el_original():
    datos = _pdf(2)
    doc = archivos.preparar(datos)
    imagen = Image.open(io.BytesIO(doc.imagen))
    assert imagen.width > imagen.height and doc.adjuntos == [("boleta.pdf", datos)] and doc.origen == "pdf"


def test_pdf_danado():
    with pytest.raises(LecturaError, match="PDF"):
        archivos.preparar(b"%PDF-1.4 roto")


def test_xml_no_necesita_imagen():
    doc = archivos.preparar(factura_xml())
    assert doc.imagen is None and doc.boleta.serie_numero == "F001-123"
    assert doc.adjuntos[0][0] == "F001-123.xml" and doc.origen == "xml"


def test_zip_usa_el_xml_y_adjunta_tambien_el_pdf():
    pdf = _pdf()
    datos = _zip(
        {
            "R-20101567428-01-F001-123.xml": b"<respuesta/>",
            "20101567428-01-F001-123.xml": factura_xml(),
            "20101567428-01-F001-123.pdf": pdf,
        }
    )
    doc = archivos.preparar(datos)
    assert doc.boleta.total == 70.12
    assert [n for n, _ in doc.adjuntos] == ["F001-123.xml", "20101567428-01-F001-123.pdf"]


def test_zip_solo_con_pdf():
    assert archivos.preparar(_zip({"boleta.pdf": _pdf()})).origen == "pdf"


def test_zip_sin_comprobante():
    with pytest.raises(LecturaError, match="ZIP"):
        archivos.preparar(_zip({"leeme.txt": b"hola"}))


def test_zip_con_nota_de_credito():
    with pytest.raises(NoEsCompra):
        archivos.preparar(_zip({"nc.xml": factura_xml("07")}))


def test_archivo_que_no_se_puede_leer():
    with pytest.raises(LecturaError, match="No puedo leer ese tipo"):
        archivos.preparar(b"un documento de word")


def test_album_junta_las_fotos_una_debajo_de_otra():
    doc = archivos.desde_album([_imagen(tamano=(600, 900)), _imagen(tamano=(300, 300))])
    imagen = Image.open(io.BytesIO(doc.imagen))
    assert imagen.size == (600, 1500) and len(doc.adjuntos) == 2 and doc.origen == "album"


def test_lee_el_qr_de_sunat_de_la_foto():
    codigo = zxingcpp.create_barcode(
        "20101567428|03|B410|00170132|8.89|58.30|2026-11-05|1|45678912|x|", zxingcpp.BarcodeFormat.QRCode
    )
    qr = Image.fromarray(codigo.to_image(scale=6)).convert("RGB")
    hoja = Image.new("RGB", (900, 1400), "white")
    hoja.paste(qr, (500, 1000))
    salida = io.BytesIO()
    hoja.save(salida, format="JPEG", quality=95)
    assert leer_qr(salida.getvalue()) == DatosQR("20101567428", "03", "B410-00170132", 58.30, "2026-11-05")


def test_foto_sin_qr():
    assert leer_qr(_imagen()) is None and leer_qr(None) is None and leer_qr(b"no es foto") is None
