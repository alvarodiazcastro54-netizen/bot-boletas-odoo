from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass, field

import pypdfium2
from PIL import Image, ImageOps

from .lectura import LecturaError
from .modelos import Boleta
from .sunat import xml_a_boleta

MAX_PAGINAS = 10
PAGINAS_LADO_A_LADO = 2
MAX_FOTOS_ALBUM = 10
FOTOS_JUNTAS = 2
MAX_ZIP = 60 * 1024 * 1024
MAX_EN_ZIP = 50
ANCHO_PAGINA = 1600
MAX_TEXTO_PDF = 40000


@dataclass
class Documento:
    imagen: bytes | None
    adjuntos: list[tuple[str, bytes]] = field(default_factory=list)
    texto: str | None = None
    boleta: Boleta | None = None
    origen: str = "foto"
    paginas: list[bytes] = field(default_factory=list)
    total_paginas: int = 0

    @property
    def imagenes(self) -> list[bytes]:
        return getattr(self, "paginas", None) or ([self.imagen] if self.imagen else [])

    @property
    def paginas_sin_leer(self) -> int:
        return max(0, getattr(self, "total_paginas", 0) - MAX_PAGINAS)


def tipo_de_archivo(datos: bytes) -> str:
    inicio = datos[:64].lstrip(b"\xef\xbb\xbf \t\r\n")
    if datos.startswith(b"%PDF"):
        return "pdf"
    if datos.startswith(b"PK\x03\x04"):
        return "zip"
    if inicio.startswith(b"<?xml") or inicio.startswith(b"<Invoice") or b"urn:oasis:names" in datos[:2000]:
        return "xml"
    if (
        datos.startswith(b"\xff\xd8")
        or datos.startswith(b"\x89PNG")
        or datos[8:12] in (b"WEBP", b"heic", b"heix", b"mif1")
    ):
        return "imagen"
    if datos[4:8] == b"ftyp":
        return "imagen"
    return "otro"


def a_jpeg(imagen: Image.Image, calidad: int = 90) -> bytes:
    salida = io.BytesIO()
    imagen.convert("RGB").save(salida, format="JPEG", quality=calidad)
    return salida.getvalue()


def abrir_imagen(datos: bytes) -> Image.Image:
    try:
        imagen = Image.open(io.BytesIO(datos))
        imagen.load()
        return ImageOps.exif_transpose(imagen)
    except Exception as exc:
        raise LecturaError("No pude abrir la imagen. Mándala como foto, JPG, PNG o PDF.") from exc


def _extension(datos: bytes) -> str:
    if datos.startswith(b"\x89PNG"):
        return "png"
    if datos[8:12] == b"WEBP":
        return "webp"
    if datos.startswith(b"\xff\xd8"):
        return "jpg"
    return "heic"


def desde_imagen(datos: bytes) -> Documento:
    imagen = abrir_imagen(datos)
    extension = _extension(datos)
    if extension == "heic":
        jpeg = a_jpeg(imagen)
        return Documento(jpeg, [("boleta.jpg", jpeg)])
    return Documento(datos, [(f"boleta.{extension}", datos)])


def juntar_imagenes(imagenes: list[Image.Image]) -> Image.Image:
    if len(imagenes) == 1:
        return imagenes[0]
    ancho = max(i.width for i in imagenes)
    escaladas = [i if i.width == ancho else i.resize((ancho, round(i.height * ancho / i.width))) for i in imagenes]
    alto = sum(i.height for i in escaladas)
    lienzo = Image.new("RGB", (ancho, alto), "white")
    y = 0
    for imagen in escaladas:
        lienzo.paste(imagen.convert("RGB"), (0, y))
        y += imagen.height
    return lienzo


def lado_a_lado(imagenes: list[Image.Image]) -> Image.Image:
    if len(imagenes) == 1:
        return imagenes[0]
    alto = max(i.height for i in imagenes)
    lienzo = Image.new("RGB", (sum(i.width for i in imagenes), alto), "white")
    x = 0
    for imagen in imagenes:
        lienzo.paste(imagen.convert("RGB"), (x, 0))
        x += imagen.width
    return lienzo


def desde_album(fotos: list[bytes]) -> Documento:
    imagenes = [abrir_imagen(f) for f in fotos[:MAX_FOTOS_ALBUM]]
    jpegs = [a_jpeg(i) for i in imagenes]
    adjuntos = [(f"boleta-{n}.jpg", j) for n, j in enumerate(jpegs, start=1)]
    if len(imagenes) <= FOTOS_JUNTAS:
        return Documento(a_jpeg(juntar_imagenes(imagenes)), adjuntos, origen="album", total_paginas=len(fotos))
    return Documento(jpegs[0], adjuntos, origen="album", paginas=jpegs, total_paginas=len(fotos))


def desde_pdf(datos: bytes) -> Documento:
    try:
        pdf = pypdfium2.PdfDocument(datos)
    except Exception as exc:
        raise LecturaError("No pude abrir el PDF. Puede estar dañado o con contraseña.") from exc
    try:
        if len(pdf) == 0:
            raise LecturaError("El PDF no tiene páginas.")
        paginas, textos = [], []
        total = len(pdf)
        for indice in range(min(total, MAX_PAGINAS)):
            pagina = pdf[indice]
            ancho_puntos = pagina.get_width() or 595
            escala = max(1.0, min(4.0, ANCHO_PAGINA / ancho_puntos))
            paginas.append(pagina.render(scale=escala).to_pil())
            textos.append(pagina.get_textpage().get_text_range())
    finally:
        pdf.close()
    texto = "\n".join(t.strip() for t in textos if t and t.strip())[:MAX_TEXTO_PDF] or None
    if len(paginas) <= PAGINAS_LADO_A_LADO:
        return Documento(
            a_jpeg(lado_a_lado(paginas)), [("boleta.pdf", datos)], texto, origen="pdf", total_paginas=total
        )
    jpegs = [a_jpeg(pagina) for pagina in paginas]
    return Documento(jpegs[0], [("boleta.pdf", datos)], texto, origen="pdf", paginas=jpegs, total_paginas=total)


def desde_xml(datos: bytes, adjuntos: list[tuple[str, bytes]] | None = None) -> Documento:
    boleta = xml_a_boleta(datos)
    nombre = f"{boleta.serie_numero or 'boleta'}.xml"
    return Documento(None, [(nombre, datos)] + (adjuntos or []), boleta=boleta, origen="xml")


def _es_respuesta_sunat(nombre: str) -> bool:
    base = nombre.rsplit("/", 1)[-1].upper()
    return base.startswith("R-") or base.startswith("CDR")


def _contenido_del_zip(datos: bytes) -> dict[str, bytes]:
    try:
        archivo = zipfile.ZipFile(io.BytesIO(datos))
    except zipfile.BadZipFile as exc:
        raise LecturaError("No pude abrir el ZIP.") from exc
    entradas = [i for i in archivo.infolist() if not i.is_dir() and not _es_respuesta_sunat(i.filename)]
    if len(entradas) > MAX_EN_ZIP or sum(i.file_size for i in entradas) > MAX_ZIP:
        raise LecturaError(
            "El ZIP es demasiado grande o trae demasiados archivos. Mándame los comprobantes de a pocos."
        )
    return {i.filename: archivo.read(i) for i in entradas if i.file_size <= 20 * 1024 * 1024}


def _raiz(nombre: str) -> str:
    return nombre.rsplit("/", 1)[-1].rsplit(".", 1)[0].upper()


def documentos_de_zip(datos: bytes) -> list[Documento]:
    contenido = _contenido_del_zip(datos)
    xmls = [n for n, d in contenido.items() if tipo_de_archivo(d) == "xml"]
    pdfs = [n for n, d in contenido.items() if tipo_de_archivo(d) == "pdf"]
    imagenes = [n for n, d in contenido.items() if tipo_de_archivo(d) == "imagen"]
    if xmls:
        documentos = []
        for xml in xmls:
            parejas = [p for p in pdfs if _raiz(p) == _raiz(xml)] or (pdfs[:1] if len(xmls) == 1 else [])
            documentos.append(desde_xml(contenido[xml], [(p.rsplit("/", 1)[-1], contenido[p]) for p in parejas]))
        return documentos
    if pdfs:
        return [desde_pdf(contenido[p]) for p in pdfs]
    if imagenes:
        return [desde_imagen(contenido[i]) for i in imagenes]
    raise LecturaError("El ZIP no tiene un XML, PDF ni imagen de comprobante.")


def desde_zip(datos: bytes) -> Documento:
    return documentos_de_zip(datos)[0]


def preparar_todos(datos: bytes) -> list[Documento]:
    if tipo_de_archivo(datos) == "zip":
        return documentos_de_zip(datos)
    return [preparar(datos)]


def preparar(datos: bytes) -> Documento:
    tipo = tipo_de_archivo(datos)
    if tipo == "pdf":
        return desde_pdf(datos)
    if tipo == "xml":
        return desde_xml(datos)
    if tipo == "zip":
        return desde_zip(datos)
    if tipo == "imagen":
        return desde_imagen(datos)
    raise LecturaError("No puedo leer ese tipo de archivo. Mándame una foto, una imagen, un PDF, un XML o un ZIP.")
