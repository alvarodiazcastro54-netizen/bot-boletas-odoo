import asyncio
import io
import pickle
import time
import zipfile
from types import SimpleNamespace

import pytest

from boletas import archivos
from boletas import bot as modulo_bot
from boletas.bot import BotBoletas
from boletas.lectura import LecturaError
from boletas.validacion import validar

from test_ajustes import compra, linea
from test_archivos import _imagen, _pdf
from test_bot_archivos import Lector, Mensaje, _bot, _contexto, _mandar, _update
from test_sunat import factura_xml


class LectorDeVarias(Lector):
    async def leer(self, foto, categorias=None, texto=None):
        b = await super().leer(foto, categorias, texto)
        b.serie_numero = f"B410-{len(self.llamadas):08d}"
        return b


def test_dos_comprobantes_seguidos_hacen_cola(boleta_hongkong):

    bot = _bot(boleta_hongkong)
    bot.lector = LectorDeVarias(boleta_hongkong)
    chat_data = _mandar(bot, Mensaje(_pdf()))
    primero = chat_data["pendiente"]["id"]
    segundo = Mensaje(_pdf())
    _mandar(bot, segundo, chat_data)
    assert chat_data["pendiente"]["id"] == primero and len(chat_data["cola"]) == 1
    assert "Lo dejé en cola (1 en espera)" in segundo.respuestas[-1]

    cancelar = Mensaje(b"")
    asyncio.run(bot.cmd_cancelar(_update(cancelar), _contexto(chat_data)))
    assert chat_data["pendiente"]["id"] != primero and chat_data["cola"] == []
    assert "Sigue el próximo comprobante" in "\n".join(cancelar.respuestas)


def test_varios_pdf_juntos_se_leen_todos(boleta_hongkong, monkeypatch):

    monkeypatch.setattr(modulo_bot, "ESPERA_ALBUM", 0.05)
    bot = _bot(boleta_hongkong)
    bot.lector = LectorDeVarias(boleta_hongkong)
    chat_data = {}
    mensajes_album = [Mensaje(_pdf(), grupo="g9", id_=n) for n in (1, 2, 3)]

    async def todos():
        await asyncio.gather(*(bot.recibir_archivo(_update(m), _contexto(chat_data)) for m in mensajes_album))

    asyncio.run(todos())
    assert len(bot.lector.llamadas) == 3 and len(chat_data["cola"]) == 2
    assert "3 comprobantes juntos" in mensajes_album[0].respuestas[0]


def test_pdf_de_muchas_paginas_manda_cada_pagina(boleta_hongkong):

    bot = _bot(boleta_hongkong)
    chat_data = _mandar(bot, Mensaje(_pdf(12)))
    foto, _ = bot.lector.llamadas[0]
    assert isinstance(foto, list) and len(foto) == archivos.MAX_PAGINAS
    texto = BotBoletas.avisos_extra(chat_data["pendiente"])
    assert any("tiene 12 páginas y leí las primeras 10" in a for a in texto)


def test_el_mismo_comprobante_reemplaza_al_pendiente_en_vez_de_hacer_cola(boleta_hongkong):

    bot = _bot(boleta_hongkong)
    chat_data = _mandar(bot, Mensaje(_pdf()))
    primero = chat_data["pendiente"]["id"]
    _mandar(bot, Mensaje(_pdf()), chat_data)
    assert chat_data["pendiente"]["id"] != primero and not chat_data.get("cola")


def test_zip_con_dos_comprobantes_se_leen_los_dos(boleta_hongkong):

    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w") as z:
        z.writestr("F001-123.xml", factura_xml())
        z.writestr("F001-123.pdf", b"%PDF-1.4 a")
        z.writestr("F001-124.xml", factura_xml().replace(b"F001-123", b"F001-124"))
        z.writestr("F001-124.pdf", b"%PDF-1.4 b")
    documentos = archivos.documentos_de_zip(salida.getvalue())
    assert [d.boleta.serie_numero for d in documentos] == ["F001-123", "F001-124"]
    assert [d.adjuntos[1] for d in documentos] == [("F001-123.pdf", b"%PDF-1.4 a"), ("F001-124.pdf", b"%PDF-1.4 b")]
    msg = Mensaje(salida.getvalue())
    chat_data = _mandar(_bot(boleta_hongkong), msg)
    assert "El ZIP trae 2 comprobantes" in msg.respuestas[0] and len(chat_data["cola"]) == 1


def test_zip_demasiado_grande():

    salida = io.BytesIO()
    with zipfile.ZipFile(salida, "w", zipfile.ZIP_DEFLATED) as z:
        for n in range(archivos.MAX_EN_ZIP + 1):
            z.writestr(f"{n}.txt", b"x")
    with pytest.raises(LecturaError, match="demasiado"):
        archivos.documentos_de_zip(salida.getvalue())


def test_album_de_un_ticket_largo_manda_todas_las_fotos():

    doc = archivos.desde_album([_imagen() for _ in range(7)])
    assert len(doc.imagenes) == 7 and len(doc.adjuntos) == 7 and doc.paginas_sin_leer == 0
    doc = archivos.desde_album([_imagen() for _ in range(12)])
    assert len(doc.imagenes) == 10 and doc.paginas_sin_leer == 2
    avisos = BotBoletas.avisos_extra({"boleta": compra([linea("X", 1, 1, 1)], 1), "documento": doc})
    assert any("Me mandaste 12 fotos y leí las primeras 10" in a for a in avisos)


def test_el_precio_que_pide_el_bot_no_se_toma_como_correccion(boleta_hongkong, monkeypatch):

    bot = _bot(boleta_hongkong)
    puestos = []

    async def poner(mensaje, context, plantilla, precio):
        puestos.append((plantilla, precio))

    monkeypatch.setattr(bot, "_poner_precio_y_publicar", poner)
    chat_data = {
        "pendiente": {"id": "a", "boleta": boleta_hongkong, "ocupado": False},
        "esperando_precio": {"plantilla": 50, "nombre": "Cartera", "hasta": time.time() + 60},
    }
    msg = Mensaje(b"")
    msg.text = "45"
    asyncio.run(bot.recibir_texto(_update(msg), _contexto(chat_data)))
    assert puestos == [(50, 45.0)] and not msg.respuestas


def test_lo_que_no_es_comprobante_no_entra_a_la_cola(boleta_hongkong):

    no_es = boleta_hongkong.model_copy(deep=True)
    no_es.es_comprobante = False
    bot = _bot(no_es)
    activo = {"id": "a", "boleta": boleta_hongkong, "ocupado": False, "desde": 9e18}
    msg = Mensaje(_pdf())
    chat_data = _mandar(bot, msg, {"pendiente": activo})
    assert chat_data["pendiente"] is activo and not chat_data.get("cola")
    assert "Si es la foto de un producto" in msg.respuestas[-1]


def test_un_pendiente_abandonado_no_tapa_al_nuevo(boleta_hongkong):

    viejo = boleta_hongkong.model_copy(deep=True)
    viejo.serie_numero = "B410-00000001"
    activo = {"id": "viejo", "boleta": viejo, "ocupado": False}
    msg = Mensaje(_pdf())
    chat_data = _mandar(_bot(boleta_hongkong), msg, {"pendiente": activo})
    assert chat_data["pendiente"]["id"] != "viejo" and chat_data["cola"] == [activo]
    assert any("llevaba más de 6 horas" in r for r in msg.respuestas)


def test_un_pendiente_guardado_antes_del_arreglo_se_ajusta_al_mostrarlo(mayorista):

    pendiente = {"id": "a", "boleta": mayorista, "documento": None, "qr": None, "ocupado": False}
    msg = Mensaje(b"")
    asyncio.run(_bot(mayorista).mostrar(msg, pendiente, editar=False))
    assert pendiente["boleta"].lineas[4].cantidad == 24 and "Hay que corregir" not in "\n".join(msg.respuestas)


def test_comprobante_guardado_con_la_version_anterior(mayorista):

    for linea_ in mayorista.lineas:
        del linea_.__dict__["exonerado"]
    del mayorista.proveedor.__dict__["dni"]
    viejo = pickle.loads(
        pickle.dumps({"pendiente": {"id": "a", "boleta": mayorista, "documento": None, "qr": None, "ocupado": False}})
    )
    with pytest.raises(AttributeError):
        validar(viejo["pendiente"]["boleta"])

    bot = _bot(mayorista)
    app = SimpleNamespace(chat_data={1: viejo})
    asyncio.run(bot.al_iniciar(app))
    boleta = viejo["pendiente"]["boleta"]
    assert boleta.lineas[0].exonerado is False and boleta.proveedor.dni is None
    assert BotBoletas.poner_al_dia(viejo) == 0

    otro = pickle.loads(pickle.dumps({"id": "b", "boleta": mayorista, "documento": None, "qr": None, "ocupado": False}))
    msg = Mensaje(b"")
    asyncio.run(bot.mostrar(msg, otro, editar=False))
    assert otro["boleta"].lineas[4].cantidad == 24 and "Hay que corregir" not in "\n".join(msg.respuestas)
