import asyncio
import io
from types import SimpleNamespace

import pytest
from PIL import Image

from boletas import bot as modulo_bot
from boletas.bot import BotBoletas, nombres_de_adjuntos
from boletas.registro import Registrador
from boletas.sunat import xml_a_boleta

from test_archivos import _imagen, _pdf
from test_bot import OdooFalso
from test_sunat import factura_xml


class Archivo:
    def __init__(self, datos):
        self.datos = datos

    async def download_as_bytearray(self):
        return bytearray(self.datos)


class Adjunto:
    def __init__(self, datos):
        self.datos = datos
        self.file_size = len(datos)

    async def get_file(self):
        return Archivo(self.datos)


class Mensaje:
    def __init__(self, datos=None, foto=False, texto=None, grupo=None, id_=1):
        self.photo = [Adjunto(datos)] if foto else []
        self.document = None if foto else Adjunto(datos)
        self.caption = texto
        self.media_group_id = grupo
        self.message_id = id_
        self.chat_id = 1
        self.respuestas = []

    async def reply_text(self, texto, **opciones):
        self.respuestas.append(texto)
        return self

    async def edit_text(self, texto, **opciones):
        self.respuestas.append(texto)
        return self


class Lector:
    def __init__(self, boleta):
        self.boleta = boleta
        self.llamadas = []

    async def leer(self, foto, categorias=None, texto=None):
        self.llamadas.append((foto, texto))
        b = self.boleta.model_copy(deep=True)
        for linea in b.lineas:
            linea.categoria = "Accesorios / Dijes y Llaveros"
        return b


def _bot(boleta):
    bot = BotBoletas(Lector(boleta), Registrador(OdooFalso(), usa_credito_fiscal=False), frozenset({1}))
    bot._categorias = lambda: asyncio.sleep(0, result=["Accesorios / Dijes y Llaveros"])
    return bot


def _contexto(chat_data):
    return SimpleNamespace(
        chat_data=chat_data, args=[], bot=SimpleNamespace(send_chat_action=lambda *a, **k: asyncio.sleep(0))
    )


def _update(msg):
    return SimpleNamespace(effective_user=SimpleNamespace(id=1, username="prueba"), effective_message=msg)


def _mandar(bot, msg, chat_data=None):
    chat_data = {} if chat_data is None else chat_data
    asyncio.run(bot.recibir_archivo(_update(msg), _contexto(chat_data)))
    return chat_data


def test_pdf_se_lee_como_imagen_y_queda_adjunto(boleta_hongkong):
    bot = _bot(boleta_hongkong)
    msg = Mensaje(_pdf())
    chat_data = _mandar(bot, msg)
    foto, _ = bot.lector.llamadas[0]
    assert Image.open(io.BytesIO(foto)).format == "JPEG"
    assert msg.respuestas[0] == "📄 Leyendo el comprobante…" and "📄 Leído del PDF." in msg.respuestas[1]
    assert chat_data["pendiente"]["documento"].adjuntos[0][0] == "boleta.pdf"


def test_xml_usa_los_datos_exactos_y_la_ia_solo_pone_categorias(boleta_hongkong):
    leida_por_la_ia = xml_a_boleta(factura_xml())
    leida_por_la_ia.total = 1.0
    bot = _bot(leida_por_la_ia)
    msg = Mensaje(factura_xml())
    chat_data = _mandar(bot, msg)
    foto, texto = bot.lector.llamadas[0]
    assert foto is None and texto.startswith("Datos exactos del comprobante electrónico (XML)")
    boleta = chat_data["pendiente"]["boleta"]
    assert boleta.serie_numero == "F001-123" and boleta.total == 70.12
    assert {l.categoria for l in boleta.lineas} == {"Accesorios / Dijes y Llaveros"}
    assert "XML del comprobante electrónico" in msg.respuestas[1]


def test_nota_de_credito_explica_que_no_es_compra(boleta_hongkong):
    bot = _bot(boleta_hongkong)
    msg = Mensaje(factura_xml("07"))
    chat_data = _mandar(bot, msg)
    assert "nota de crédito" in msg.respuestas[1] and "pendiente" not in chat_data and not bot.lector.llamadas


def test_archivo_que_no_es_un_comprobante(boleta_hongkong):
    msg = Mensaje(b"PK\x03\x04no es un zip de verdad")
    chat_data = _mandar(_bot(boleta_hongkong), msg)
    assert msg.respuestas[-1].startswith("❌") and "pendiente" not in chat_data


def test_pdf_con_numero_no_es_foto_de_producto(boleta_hongkong):
    msg = Mensaje(_pdf(), texto="3")
    _mandar(_bot(boleta_hongkong), msg, {"ultima": {"orden": "P1", "productos": [(3, "x", 5)]}})
    assert msg.respuestas == ["La foto de un producto tiene que ser una imagen, no un archivo PDF o XML."]


def test_album_se_lee_como_un_solo_comprobante(boleta_hongkong, monkeypatch):
    monkeypatch.setattr(modulo_bot, "ESPERA_ALBUM", 0.05)
    bot = _bot(boleta_hongkong)
    chat_data = {}
    primero = Mensaje(_imagen(tamano=(600, 900)), foto=True, grupo="g1", id_=1)
    segundo = Mensaje(_imagen(tamano=(600, 400)), foto=True, grupo="g1", id_=2)

    async def ambos():
        await asyncio.gather(
            bot.recibir_archivo(_update(primero), _contexto(chat_data)),
            bot.recibir_archivo(_update(segundo), _contexto(chat_data)),
        )

    asyncio.run(ambos())
    assert len(bot.lector.llamadas) == 1
    assert Image.open(io.BytesIO(bot.lector.llamadas[0][0])).size == (600, 1300)
    assert chat_data["pendiente"]["documento"].origen == "album" and not segundo.respuestas


def test_album_de_fotos_de_productos_pide_mandarlas_una_por_una(boleta_hongkong, monkeypatch):
    monkeypatch.setattr(modulo_bot, "ESPERA_ALBUM", 0.01)
    msg = Mensaje(_imagen(), foto=True, grupo="g2", texto="1")
    _mandar(_bot(boleta_hongkong), msg)
    assert "una por una" in msg.respuestas[0]


def test_video_o_audio_no_se_leen(boleta_hongkong):
    msg = Mensaje(b"")
    asyncio.run(_bot(boleta_hongkong).no_soportado(_update(msg), _contexto({})))
    assert "Solo leo comprobantes" in msg.respuestas[0]


@pytest.mark.parametrize(
    "adjuntos,ref,esperado",
    [
        ([("boleta.jpg", b"1")], "B410-00170132", ["B410-00170132.jpg"]),
        (
            [("boleta-1.jpg", b"1"), ("boleta-2.jpg", b"2")],
            "NV-2026-11-05-58.30",
            ["NV-2026-11-05-58.30-1.jpg", "NV-2026-11-05-58.30-2.jpg"],
        ),
        (
            [("F001-123.xml", b"1"), ("20101567428-01-F001-123.pdf", b"2")],
            "F001-00000123",
            ["F001-123.xml", "20101567428-01-F001-123.pdf"],
        ),
    ],
)
def test_nombres_de_los_adjuntos(adjuntos, ref, esperado):
    assert [n for n, _ in nombres_de_adjuntos(adjuntos, ref)] == esperado
