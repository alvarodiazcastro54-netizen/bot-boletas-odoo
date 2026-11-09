import asyncio
import base64
import io
import json

import anthropic
import httpx2
import pytest
from PIL import Image

from boletas.lectura import Lector, LecturaError, preparar_imagen


def _cliente(respuesta: dict, capturado: list, estado: int = 200):
    def manejar(peticion: httpx2.Request) -> httpx2.Response:
        capturado.append({"url": str(peticion.url), "headers": dict(peticion.headers),
                          "cuerpo": json.loads(peticion.content)})
        return httpx2.Response(estado, json=respuesta)
    return anthropic.AsyncAnthropic(
        api_key="sk-ant-prueba", max_retries=0,
        http_client=anthropic.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(manejar)))


def _mensaje(texto: str, stop: str = "end_turn") -> dict:
    return {
        "id": "msg_prueba", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": texto}], "stop_reason": stop, "stop_sequence": None,
        "usage": {"input_tokens": 1500, "output_tokens": 900},
    }


def test_lee_y_valida(boleta_hongkong, foto_hongkong):
    capturado: list = []
    cliente = _cliente(_mensaje(boleta_hongkong.model_dump_json()), capturado)
    lector = Lector(cliente, "claude-opus-5-5", "high")
    boleta = asyncio.run(lector.leer(foto_hongkong))
    assert lector.ultimo_uso == (1500, 900)

    assert boleta == boleta_hongkong
    pedido = capturado[0]
    cuerpo = pedido["cuerpo"]
    assert pedido["url"].endswith("/v1/messages")
    assert "server-side-fallback-2026-07-01" in pedido["headers"]["anthropic-beta"]
    assert cuerpo["model"] == "claude-opus-5-5"
    assert cuerpo["fallbacks"] == "default"
    assert cuerpo["output_config"]["effort"] == "high"
    esquema = cuerpo["output_config"]["format"]["schema"]
    assert cuerpo["output_config"]["format"]["type"] == "json_schema"
    assert "lineas" in esquema["properties"] and "total" in esquema["properties"]
    imagen = cuerpo["messages"][0]["content"][0]
    assert imagen["type"] == "image" and imagen["source"]["media_type"] == "image/jpeg"
    assert base64.b64decode(imagen["source"]["data"]) == foto_hongkong


def test_categorias_limitadas_a_las_de_la_tienda(boleta_hongkong, foto_hongkong):
    capturado: list = []
    cliente = _cliente(_mensaje(boleta_hongkong.model_dump_json()), capturado)
    categorias = ["Hogar y Manualidades / Cocina", "Accesorios / Dijes y Llaveros"]
    asyncio.run(Lector(cliente, "claude-opus-5-5").leer(foto_hongkong, categorias))
    cuerpo = capturado[0]["cuerpo"]
    campo = cuerpo["output_config"]["format"]["schema"]["$defs"]["Linea"]["properties"]["categoria"]
    assert campo["anyOf"][0]["enum"] == categorias and campo["anyOf"][1] == {"type": "null"}
    assert "- Accesorios / Dijes y Llaveros" in cuerpo["messages"][0]["content"][1]["text"]
    from boletas.lectura import ESQUEMA
    assert "enum" not in json.dumps(ESQUEMA["$defs"]["Linea"]["properties"]["categoria"])


def test_rechazo_de_la_ia(foto_hongkong):
    cliente = _cliente(_mensaje("", stop="refusal") | {"stop_details": {"type": "refusal", "category": None,
                                                                         "explanation": None}}, [])
    with pytest.raises(LecturaError, match="no quiso"):
        asyncio.run(Lector(cliente, "claude-opus-5-5").leer(foto_hongkong))


def test_respuesta_cortada(foto_hongkong):
    cliente = _cliente(_mensaje('{"es_comprobante": true', stop="max_tokens"), [])
    with pytest.raises(LecturaError):
        asyncio.run(Lector(cliente, "claude-opus-5-5").leer(foto_hongkong))


@pytest.mark.parametrize("estado,texto", [(401, "clave"), (429, "ocupada"), (529, "falló"), (400, "procesar")])
def test_errores_de_la_api(foto_hongkong, estado, texto):
    error = {"type": "error", "error": {"type": "x", "message": "x"}}
    cliente = _cliente(error, [], estado=estado)
    with pytest.raises(LecturaError, match=texto):
        asyncio.run(Lector(cliente, "claude-opus-5-5").leer(foto_hongkong))


def test_modo_de_prueba(boleta_hongkong, foto_hongkong):
    from conftest import MUESTRAS
    from boletas.lectura import LectorDePrueba
    lector = LectorDePrueba(MUESTRAS / "boleta-01-hongkong.esperado.json")
    boleta = asyncio.run(lector.leer(foto_hongkong))
    assert boleta.lineas == boleta_hongkong.lineas and boleta.total == 58.30
    assert boleta.observaciones.startswith("MODO DE PRUEBA")
    with pytest.raises(LecturaError):
        asyncio.run(lector.leer(b"no es una imagen"))


def test_imagen_grande_se_achica():
    grande = Image.new("RGB", (5000, 3000), "white")
    buf = io.BytesIO()
    grande.save(buf, format="PNG")
    datos, tipo = preparar_imagen(buf.getvalue())
    assert tipo == "image/jpeg"
    assert max(Image.open(io.BytesIO(datos)).size) <= 2400


def test_archivo_que_no_es_imagen():
    with pytest.raises(LecturaError):
        preparar_imagen(b"esto no es una imagen")
