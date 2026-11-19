import asyncio
import io
import time
from types import SimpleNamespace

import pytest
from PIL import Image

from boletas import mensajes
from boletas.bot import BotBoletas
from boletas.lectura import LecturaError, preparar_foto_producto
from boletas.odoo import OdooError
from boletas.registro import Registrador as RegistradorReal
from boletas.registro import Resultado
from boletas.tienda import EstadoProducto

from test_bot import OdooFalso


def _imagen(formato="JPEG", tamano=(3000, 2000), modo="RGB", color=(200, 30, 30)) -> bytes:
    salida = io.BytesIO()
    Image.new(modo, tamano, color).save(salida, format=formato)
    return salida.getvalue()


def test_foto_grande_se_achica_a_1920():
    foto = Image.open(io.BytesIO(preparar_foto_producto(_imagen())))
    assert foto.format == "JPEG" and max(foto.size) == 1920


def test_png_transparente_queda_con_fondo_blanco():
    datos = preparar_foto_producto(_imagen("PNG", (100, 100), "RGBA", (0, 0, 0, 0)))
    foto = Image.open(io.BytesIO(datos))
    assert foto.format == "JPEG" and foto.getpixel((50, 50)) == pytest.approx((255, 255, 255), abs=2)


def test_archivo_que_no_es_imagen():
    with pytest.raises(LecturaError, match="No pude abrir"):
        preparar_foto_producto(b"no es una imagen")


class Archivo:
    def __init__(self, datos):
        self.datos = datos

    async def download_as_bytearray(self):
        return bytearray(self.datos)


class Adjunto:
    file_size = 1000

    def __init__(self, datos):
        self.datos = datos

    async def get_file(self):
        return Archivo(self.datos)


class Mensaje:
    def __init__(self, texto_de_la_foto=None, datos=None, texto=None):
        self.caption = texto_de_la_foto
        self.text = texto
        self.photo = [Adjunto(datos or _imagen(tamano=(400, 300)))]
        self.document = None
        self.media_group_id = None
        self.chat_id = 1
        self.respuestas = []
        self.teclados = []

    async def reply_text(self, texto, **opciones):
        self.respuestas.append(texto)
        self.teclados.append(opciones.get("reply_markup"))
        return self

    async def edit_text(self, texto, **opciones):
        self.respuestas.append(texto)
        return self

    async def edit_reply_markup(self, **opciones):
        return self


class Tienda:
    def __init__(self, sin_precio=()):
        self.publicados = []
        self.precios = {}
        self.sin_precio = set(sin_precio)
        self.fotos = set()

    def plantilla_de(self, producto_id):
        return 90 + producto_id

    def _plantillas_de(self, producto_ids):
        return {p: 90 + p for p in producto_ids}

    def _estado(self, i):
        e = EstadoProducto(
            i,
            f"Producto {i}",
            self.precios.get(i, 0 if i in self.sin_precio else 30.0),
            False,
            i in self.fotos,
            f"https://tienda/shop/p-{i}",
        )
        if i not in self.fotos:
            e.faltan.append("foto")
        if e.precio <= 0:
            e.faltan.append("precio")
        return e

    def publicar(self, ids):
        estados = [self._estado(i) for i in ids]
        listos = [e for e in estados if e.listo]
        self.publicados += [e.plantilla_id for e in listos]
        return listos, [e for e in estados if not e.listo]

    def poner_precio(self, plantilla_id, precio):
        self.precios[plantilla_id] = precio
        self.sin_precio.discard(plantilla_id)

    def costo_y_categoria(self, plantilla_id):
        return 10.0, "Accesorios"

    def buscar(self, texto, limite=10):
        return [{"id": 12, "name": "CARTERA HYPNOTIC LAURA ARENA"}] if "cartera" in texto else []

    def del_bot(self, limite=300):
        return [14, 13, 12]

    def estados(self, ids):
        return [self._estado(i) for i in ids]


class Registrador:
    def __init__(self, falla=None, sin_foto=None, tienda=None):
        self.plantillas = []
        self.falla = falla
        self.sin_foto = sin_foto or ([], 0)
        self.tienda = tienda or Tienda()
        self.margenes = SimpleNamespace(para=lambda categoria: 3.0)

    def poner_foto_en_plantilla(self, plantilla_id, foto):
        if self.falla:
            raise OdooError(self.falla, 403)
        self.plantillas.append((plantilla_id, foto))
        self.tienda.fotos.add(plantilla_id)

    def productos_sin_foto(self):
        return self.sin_foto


class Lector:
    async def leer(self, foto, categorias=None, texto=None):
        raise LecturaError("lector de prueba")


def _bot(registrador):
    bot = BotBoletas(Lector(), registrador, frozenset({1}))
    bot._categorias = lambda: asyncio.sleep(0, result=[])
    return bot


def _contexto(chat_data, args=None):
    return SimpleNamespace(
        chat_data=chat_data, args=args or [], bot=SimpleNamespace(send_chat_action=lambda *a, **k: asyncio.sleep(0))
    )


def _update(msg, consulta=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=1, username="prueba"), effective_message=msg, callback_query=consulta
    )


def _enviar(texto_de_la_foto, ultima=None, registrador=None, chat_data=None):
    registrador = registrador or Registrador()
    msg = Mensaje(texto_de_la_foto)
    datos = chat_data if chat_data is not None else ({"ultima": ultima} if ultima else {})
    asyncio.run(_bot(registrador).recibir_archivo(_update(msg), _contexto(datos)))
    return msg, registrador


ULTIMA = {"orden": "P00036", "productos": [(1, "Set cortador", 51), (7, "Llavero de metal", 57)]}


@pytest.mark.parametrize("texto", ["7", " 7 ", "foto 7", "Foto7"])
def test_foto_con_numero_va_al_producto_y_ofrece_publicar(texto):
    msg, registrador = _enviar(texto, ULTIMA)
    assert [p for p, _ in registrador.plantillas] == [147]
    assert Image.open(io.BytesIO(registrador.plantillas[0][1])).format == "JPEG"
    assert msg.respuestas == ["📷 Foto puesta: Llavero de metal (línea 7). ¿Lo publico en la tienda?"]
    botones = [b.callback_data for b in msg.teclados[0].inline_keyboard[0]]
    assert botones == ["pubp:147", "nopub:"]


def test_si_ya_pidio_publicar_la_foto_lo_publica():
    chat_data = {"ultima": ULTIMA, "por_publicar": {147}}
    msg, registrador = _enviar("7", chat_data=chat_data)
    assert registrador.tienda.publicados == [147]
    assert "🌐 Publicado: Producto 147 a S/ 30.00" in msg.respuestas[1]
    assert chat_data["por_publicar"] == set()


def test_foto_de_producto_sin_compra_registrada():
    msg, registrador = _enviar("3")
    assert registrador.plantillas == [] and "/foto" in msg.respuestas[0] and "/sinfoto" in msg.respuestas[0]


def test_linea_que_no_existe():
    msg, registrador = _enviar("9", ULTIMA)
    assert registrador.plantillas == [] and "no tiene la línea 9" in msg.respuestas[0]
    assert "de la 1 a la 7" in msg.respuestas[0]


def test_odoo_no_deja_poner_la_foto():
    msg, _ = _enviar("1", ULTIMA, Registrador(falla="No puede modificar Product"))
    assert msg.respuestas == ["❌ No pude poner la foto en Odoo: No puede modificar Product"]


@pytest.mark.parametrize("texto", [None, "", "boleta de hoy", "1234"])
def test_foto_sin_numero_es_un_comprobante(texto):
    msg, registrador = _enviar(texto, ULTIMA)
    assert registrador.plantillas == [] and msg.respuestas[0] == "📄 Leyendo el comprobante…"


SIN_FOTO = (
    [
        {"id": 12, "nombre": "JARRO MUG ELECTRICO P/AUTO MS-156", "serie": "B410-00170132"},
        {"id": 15, "nombre": "SOLING CUCHILLO COCINA ACERO INOX #736/MW9011", "serie": None},
    ],
    14,
)


class Consulta:
    def __init__(self, data):
        self.data = data
        self.message = Mensaje()

    async def answer(self, *args, **opciones):
        return None


def _comando(registrador, chat_data, nombre, args=None):
    msg = Mensaje()
    asyncio.run(getattr(_bot(registrador), nombre)(_update(msg), _contexto(chat_data, args)))
    return msg


def _tocar(registrador, chat_data, data):
    consulta = Consulta(data)
    asyncio.run(_bot(registrador).boton(_update(consulta.message, consulta), _contexto(chat_data)))
    return consulta.message


def test_sinfoto_lista_los_productos_con_botones():
    msg = _comando(Registrador(sin_foto=SIN_FOTO), {}, "cmd_sinfoto")
    assert "Productos sin foto: 14" in msg.respuestas[0] and "los 2 más recientes" in msg.respuestas[0]
    botones = [fila[0] for fila in msg.teclados[0].inline_keyboard]
    assert [b.callback_data for b in botones] == ["fot:12", "fot:15"]
    assert botones[0].text == "📷 JARRO MUG ELECTRICO P/AUTO MS-156 · B410-00170132"
    assert botones[1].text.startswith("📷 SOLING CUCHILLO COCINA ACERO INOX #736/…")


def test_sinfoto_cuando_todos_tienen_foto():
    msg = _comando(Registrador(), {}, "cmd_sinfoto")
    assert msg.respuestas == ["✅ Todos los productos que registró el bot tienen foto."]


def test_tocar_un_producto_y_mandar_la_foto():
    registrador = Registrador(sin_foto=SIN_FOTO)
    chat_data = {}
    _comando(registrador, chat_data, "cmd_sinfoto")
    aviso = _tocar(registrador, chat_data, "fot:12")
    assert "Mándame la foto de «JARRO MUG ELECTRICO P/AUTO MS-156»" in aviso.respuestas[0]
    msg, _ = _enviar(None, registrador=registrador, chat_data=chat_data)
    assert [p for p, _ in registrador.plantillas] == [12]
    assert msg.respuestas == ["📷 Foto puesta: JARRO MUG ELECTRICO P/AUTO MS-156. ¿Lo publico en la tienda?"]
    assert "esperando_foto" not in chat_data


def test_foto_busca_cualquier_producto():
    registrador = Registrador()
    chat_data = {}
    msg = _comando(registrador, chat_data, "cmd_foto", ["cartera", "hypnotic"])
    assert [fila[0].callback_data for fila in msg.teclados[0].inline_keyboard] == ["fot:12"]
    assert chat_data["mostrados"] == {12: "CARTERA HYPNOTIC LAURA ARENA"}
    aviso = _tocar(registrador, chat_data, "fot:12")
    assert "Mándame la foto de «CARTERA HYPNOTIC LAURA ARENA»" in aviso.respuestas[0]


def test_foto_sin_texto_explica_como_usarlo():
    assert "/foto cartera" in _comando(Registrador(), {}, "cmd_foto").respuestas[0]


def test_foto_que_no_encuentra_nada():
    assert "No encontré" in _comando(Registrador(), {}, "cmd_foto", ["peluche"]).respuestas[0]


def test_si_pasaron_10_minutos_la_foto_es_un_comprobante():
    chat_data = {"esperando_foto": {"plantilla": 12, "nombre": "JARRO", "hasta": time.time() - 1}}
    msg, registrador = _enviar(None, chat_data=chat_data)
    assert registrador.plantillas == [] and msg.respuestas[0] == "📄 Leyendo el comprobante…"


def test_cancelar_la_espera_de_la_foto():
    chat_data = {"esperando_foto": {"plantilla": 12, "nombre": "JARRO", "hasta": time.time() + 600}}
    aviso = _tocar(Registrador(), chat_data, "fotno:")
    assert "esperando_foto" not in chat_data and aviso.respuestas == ["Listo, ya no espero la foto."]


def test_si_odoo_falla_sigue_esperando_la_foto():
    chat_data = {"esperando_foto": {"plantilla": 12, "nombre": "JARRO", "hasta": time.time() + 600}}
    msg, _ = _enviar(None, registrador=Registrador(falla="sin permiso"), chat_data=chat_data)
    assert msg.respuestas == ["❌ No pude poner la foto en Odoo: sin permiso"] and "esperando_foto" in chat_data


def test_mensaje_de_registro_lista_las_lineas_y_ofrece_publicar():
    r = Resultado(
        "P00036",
        [],
        "B BA34-1",
        ["Set cortador", "Llavero de metal"],
        False,
        productos=[(1, "Set cortador", 51), (7, "Llavero de metal", 57)],
    )
    texto = mensajes.registrado(r)
    assert "1. Set cortador" in texto and "7. Llavero de metal" in texto
    assert "¿Los publico en la tienda?" in texto and "con su número" in texto


def test_publicar_la_ultima_compra_publica_las_que_tienen_foto():
    tienda = Tienda()
    tienda.fotos.add(141)
    registrador = Registrador(tienda=tienda)
    chat_data = {"ultima": ULTIMA}
    msg = _tocar(registrador, chat_data, "pub:")
    assert tienda.publicados == [141]
    assert "🌐 Publicado: Producto 141" in msg.respuestas[0] and "https://tienda/shop/p-141" in msg.respuestas[0]
    assert "⏳ 7. Producto 147: falta la foto" in msg.respuestas[0]
    assert chat_data["por_publicar"] == {147}


def test_solo_en_odoo_no_publica_nada():
    registrador = Registrador()
    msg = _tocar(registrador, {"ultima": ULTIMA}, "nopub:")
    assert registrador.tienda.publicados == [] and "Quedan solo en Odoo" in msg.respuestas[0]


def test_producto_sin_precio_pide_el_precio_con_sugerencia():
    tienda = Tienda(sin_precio={147})
    tienda.fotos.add(147)
    registrador = Registrador(tienda=tienda)
    chat_data = {}
    msg = _tocar(registrador, chat_data, "pubp:147")
    assert "falta el precio" in msg.respuestas[0]
    assert "te sugiero S/ 30.00" in msg.respuestas[1]
    assert msg.teclados[1].inline_keyboard[0][0].callback_data == "pre:147:30.0"
    respuesta = _tocar(registrador, chat_data, "pre:147:30.0")
    assert tienda.precios == {147: 30.0} and tienda.publicados == [147]
    assert "🌐 Publicado: Producto 147 a S/ 30.00" in respuesta.respuestas[1]


def test_precio_escrito_para_el_producto_que_espera():
    tienda = Tienda(sin_precio={147})
    tienda.fotos.add(147)
    registrador = Registrador(tienda=tienda)
    chat_data = {
        "por_publicar": {147},
        "esperando_precio": {"plantilla": 147, "nombre": "X", "hasta": time.time() + 600},
    }
    msg = Mensaje(texto="45,50")
    asyncio.run(_bot(registrador).recibir_texto(_update(msg), _contexto(chat_data)))
    assert tienda.precios == {147: 45.5} and tienda.publicados == [147]


def test_precio_de_una_linea_despues_de_registrar():
    tienda = Tienda()
    registrador = Registrador(tienda=tienda)
    msg = Mensaje(texto="7 venta 39.90")
    asyncio.run(_bot(registrador).recibir_texto(_update(msg), _contexto({"ultima": ULTIMA})))
    assert tienda.precios == {147: 39.9} and msg.respuestas == ["💲 Precio de venta: S/ 39.90."]


def test_porpublicar_lista_lo_que_falta():
    tienda = Tienda(sin_precio={13})
    tienda.fotos.update({14, 13})
    msg = _comando(Registrador(tienda=tienda), {}, "cmd_porpublicar")
    assert "sin publicar: 3" in msg.respuestas[0]
    datos = [fila[0].callback_data for fila in msg.teclados[0].inline_keyboard]
    assert datos == ["pubp:14", "pubp:13", "fot:12"]
    textos = [fila[0].text for fila in msg.teclados[0].inline_keyboard]
    assert textos[0].startswith("🌐") and textos[1].startswith("💲") and textos[2].startswith("📷")


def test_foto_con_numero_mientras_otro_comprobante_espera(boleta_hongkong):
    pendiente = {"id": "b2", "boleta": boleta_hongkong, "ocupado": False, "desde": 9e18}
    chat_data = {"ultima": ULTIMA, "pendiente": pendiente, "cola": [{"id": "b3"}]}
    msg, registrador = _enviar("7", chat_data=chat_data)
    assert registrador.plantillas[0][0] == 147 and "Llavero de metal (línea 7)" in msg.respuestas[0]
    assert chat_data["pendiente"] is pendiente and chat_data["cola"] == [{"id": "b3"}]


def test_foto_esperada_mientras_otro_comprobante_espera(boleta_hongkong):

    pendiente = {"id": "b2", "boleta": boleta_hongkong, "ocupado": False, "desde": 9e18}
    chat_data = {
        "pendiente": pendiente,
        "esperando_foto": {"plantilla": 12, "nombre": "Cartera", "hasta": time.time() + 60},
    }
    _, registrador = _enviar(None, chat_data=chat_data)
    assert registrador.plantillas[0][0] == 12 and chat_data["pendiente"] is pendiente
    assert "esperando_foto" not in chat_data


def test_foto_sin_numero_que_no_es_comprobante_no_toca_lo_pendiente(boleta_hongkong):
    no_es = boleta_hongkong.model_copy(deep=True)
    no_es.es_comprobante = False

    class LectorDeProducto:
        async def leer(self, foto, categorias=None, texto=None):
            return no_es

    pendiente = {"id": "b2", "boleta": boleta_hongkong, "ocupado": False, "desde": 9e18}
    chat_data = {"ultima": ULTIMA, "pendiente": pendiente}
    msg = Mensaje(None)
    bot = _bot(Registrador())
    bot.lector = LectorDeProducto()
    asyncio.run(bot.recibir_archivo(_update(msg), _contexto(chat_data)))
    assert chat_data["pendiente"] is pendiente and not chat_data.get("cola")
    assert "mándala con el número de su línea" in msg.respuestas[-1]


def test_despues_de_unir_las_fotos_van_a_esa_compra():

    r = Resultado("P00236", [], "B B001-1", [], False, [], True, [(1, "Calendario", 501), (2, "Pack", 502)])
    assert "Las fotos con número van a los productos de la compra P00236 (líneas 1 a 2)" in mensajes.unido(r)


class RegistradorConCompras(Registrador):
    def __init__(self, **opciones):
        super().__init__(**opciones)
        self.consultadas = []

    def productos_de_compra(self, nombre):
        self.consultadas.append(nombre)
        return [(1, "Gorro de lana", 61), (2, "Chalina", 62)]


def test_publicar_desde_un_mensaje_viejo_publica_esa_compra():
    tienda = Tienda()
    tienda.fotos.update({151, 152})
    registrador = RegistradorConCompras(tienda=tienda)
    msg = _tocar(registrador, {"ultima": ULTIMA}, "pub:P00010")
    assert registrador.consultadas == ["P00010"] and tienda.publicados == [151, 152]
    assert "Producto 141" not in msg.respuestas[0]


def test_publicar_la_ultima_no_consulta_odoo():
    registrador = RegistradorConCompras()
    _tocar(registrador, {"ultima": ULTIMA}, "pub:P00036")
    assert registrador.consultadas == []


def test_boton_de_un_comprobante_que_esta_en_la_cola():
    respuestas = []

    class ConsultaQueAnota(Consulta):
        async def answer(self, *args, **opciones):
            respuestas.append(args[0] if args else None)

    consulta = ConsultaQueAnota("reg:b3")
    chat_data = {"pendiente": {"id": "b2", "ocupado": False}, "cola": [{"id": "b3"}]}
    asyncio.run(_bot(Registrador()).boton(_update(consulta.message, consulta), _contexto(chat_data)))
    assert "está en la cola" in respuestas[0] and chat_data["pendiente"]["id"] == "b2"


def test_al_corregir_el_resumen_anterior_pierde_sus_botones(boleta_hongkong):

    quitados = []

    class BotFalso:
        async def edit_message_reply_markup(self, chat_id, message_id, reply_markup):
            quitados.append(message_id)

    class MensajeConBot(Mensaje):
        message_id = 900

        def get_bot(self):
            return BotFalso()

    bot = BotBoletas(Lector(), RegistradorReal(OdooFalso()), frozenset({1}))
    pendiente = {"id": "b2", "boleta": boleta_hongkong, "ocupado": False, "mensaje_id": 812, "ajustes": None}
    asyncio.run(bot.mostrar(MensajeConBot(), pendiente, editar=False))
    assert quitados == [812] and pendiente["mensaje_id"] == 900
