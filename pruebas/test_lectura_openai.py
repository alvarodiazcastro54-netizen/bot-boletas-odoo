import asyncio
import base64
import json

import httpx2
import openai
import pytest

from boletas import config as modulo
from boletas.lectura import LectorOpenAI, LecturaError, esquema_con_categorias

MODELO = "modelo-de-prueba"


def _cliente(respuesta: dict, capturado: list, estado: int = 200):
    def manejar(peticion: httpx2.Request) -> httpx2.Response:
        capturado.append(
            {"url": str(peticion.url), "headers": dict(peticion.headers), "cuerpo": json.loads(peticion.content)}
        )
        return httpx2.Response(estado, json=respuesta)

    return openai.AsyncOpenAI(
        api_key="sk-prueba",
        max_retries=0,
        http_client=openai.DefaultAsyncHttpxClient(transport=httpx2.MockTransport(manejar)),
    )


def _respuesta(
    texto: str | None = None, estado: str = "completed", motivo: str | None = None, rechazo: str | None = None
) -> dict:
    contenido = []
    if texto is not None:
        contenido.append({"type": "output_text", "text": texto, "annotations": []})
    if rechazo is not None:
        contenido.append({"type": "refusal", "refusal": rechazo})
    return {
        "id": "resp_prueba",
        "object": "response",
        "created_at": 0,
        "model": MODELO,
        "status": estado,
        "incomplete_details": {"reason": motivo} if motivo else None,
        "error": None,
        "output": [
            {"type": "message", "id": "msg_prueba", "role": "assistant", "status": "completed", "content": contenido}
        ],
        "usage": {
            "input_tokens": 1500,
            "output_tokens": 900,
            "total_tokens": 2400,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens_details": {"reasoning_tokens": 300},
        },
    }


def test_lee_y_valida(boleta_hongkong, foto_hongkong):
    capturado: list = []
    cliente = _cliente(_respuesta(boleta_hongkong.model_dump_json()), capturado)
    lector = LectorOpenAI(cliente, MODELO, "high")
    boleta = asyncio.run(lector.leer(foto_hongkong))
    assert boleta == boleta_hongkong
    assert lector.ultimo_uso == (1500, 900)

    pedido = capturado[0]
    cuerpo = pedido["cuerpo"]
    assert pedido["url"].endswith("/v1/responses")
    assert pedido["headers"]["authorization"] == "Bearer sk-prueba"
    assert cuerpo["model"] == MODELO
    assert cuerpo["store"] is False
    assert cuerpo["reasoning"] == {"effort": "high"}
    assert "comprobantes de compra peruanos" in cuerpo["instructions"]
    formato = cuerpo["text"]["format"]
    assert formato["type"] == "json_schema" and formato["strict"] is True
    assert "lineas" in formato["schema"]["properties"] and "total" in formato["schema"]["properties"]
    imagen = cuerpo["input"][0]["content"][0]
    assert imagen["type"] == "input_image"
    cabecera, datos = imagen["image_url"].split(",", 1)
    assert cabecera == "data:image/jpeg;base64"
    assert base64.b64decode(datos) == foto_hongkong


def test_esquema_estricto_valido_para_openai():

    def revisar(nodo):
        if isinstance(nodo, dict):
            if nodo.get("type") == "object":
                assert nodo.get("additionalProperties") is False
                assert set(nodo.get("required", [])) == set(nodo.get("properties", {}))
            for valor in nodo.values():
                revisar(valor)
        elif isinstance(nodo, list):
            for valor in nodo:
                revisar(valor)

    revisar(esquema_con_categorias(None))
    revisar(esquema_con_categorias(["Accesorios / Dijes y Llaveros"]))


def test_categorias_y_sin_esfuerzo(boleta_hongkong, foto_hongkong):
    capturado: list = []
    cliente = _cliente(_respuesta(boleta_hongkong.model_dump_json()), capturado)
    categorias = ["Hogar y Manualidades / Cocina", "Accesorios / Dijes y Llaveros"]
    asyncio.run(LectorOpenAI(cliente, MODELO, None).leer(foto_hongkong, categorias))
    cuerpo = capturado[0]["cuerpo"]
    assert "reasoning" not in cuerpo
    campo = cuerpo["text"]["format"]["schema"]["$defs"]["Linea"]["properties"]["categoria"]
    assert campo["anyOf"][0]["enum"] == categorias
    assert "- Accesorios / Dijes y Llaveros" in cuerpo["input"][0]["content"][1]["text"]


def test_rechazo_de_la_ia(foto_hongkong):
    cliente = _cliente(_respuesta(rechazo="No puedo ayudar con eso."), [])
    with pytest.raises(LecturaError, match="no quiso"):
        asyncio.run(LectorOpenAI(cliente, MODELO).leer(foto_hongkong))


def test_respuesta_cortada(foto_hongkong):
    cliente = _cliente(_respuesta('{"es_comprobante": true', estado="incomplete", motivo="max_output_tokens"), [])
    with pytest.raises(LecturaError, match="incompleta"):
        asyncio.run(LectorOpenAI(cliente, MODELO).leer(foto_hongkong))


def test_json_invalido(foto_hongkong):
    cliente = _cliente(_respuesta('{"es_comprobante": true}'), [])
    with pytest.raises(LecturaError, match="datos válidos"):
        asyncio.run(LectorOpenAI(cliente, MODELO).leer(foto_hongkong))


@pytest.mark.parametrize(
    "estado,tipo,codigo,texto",
    [
        (401, "invalid_request_error", "invalid_api_key", "clave"),
        (429, "insufficient_quota", "insufficient_quota", "no tiene saldo"),
        (429, "insufficient_quota", "credit_balance_exhausted", "no tiene saldo"),
        (429, "requests", "rate_limit_exceeded", "ocupada"),
        (404, "invalid_request_error", "model_not_found", "no existe"),
        (400, "invalid_request_error", "invalid_image", "procesar"),
        (500, "server_error", None, "falló"),
    ],
)
def test_errores_de_la_api(foto_hongkong, estado, tipo, codigo, texto):
    error = {"error": {"message": "x", "type": tipo, "param": None, "code": codigo}}
    cliente = _cliente(error, [], estado=estado)
    with pytest.raises(LecturaError, match=texto):
        asyncio.run(LectorOpenAI(cliente, MODELO).leer(foto_hongkong))


@pytest.mark.parametrize(
    "param,codigo",
    [
        ("reasoning.effort", "unsupported_value"),
        ("reasoning", None),
        ("model", None),
        (None, "unsupported_parameter"),
    ],
)
def test_configuracion_rechazada_no_culpa_a_la_foto(foto_hongkong, param, codigo):
    error = {"error": {"message": "x", "type": "invalid_request_error", "param": param, "code": codigo}}
    cliente = _cliente(error, [], estado=400)
    with pytest.raises(LecturaError, match="configuración del bot"):
        asyncio.run(LectorOpenAI(cliente, MODELO, "none").leer(foto_hongkong))


def test_uso_se_reinicia_en_cada_lectura(boleta_hongkong, foto_hongkong):
    lector = LectorOpenAI(_cliente(_respuesta(boleta_hongkong.model_dump_json()), []), MODELO)
    asyncio.run(lector.leer(foto_hongkong))
    assert lector.ultimo_uso == (1500, 900)
    lector.cliente = _cliente(
        {"error": {"message": "x", "type": "server_error", "param": None, "code": None}}, [], estado=500
    )
    with pytest.raises(LecturaError):
        asyncio.run(lector.leer(foto_hongkong))
    assert lector.ultimo_uso == (0, 0)


def test_uso_cuenta_aunque_la_lectura_falle(foto_hongkong):
    lector = LectorOpenAI(_cliente(_respuesta('{"es_comprobante": true}'), []), MODELO)
    with pytest.raises(LecturaError):
        asyncio.run(lector.leer(foto_hongkong))
    assert lector.ultimo_uso == (1500, 900)


def test_esfuerzo_none_se_manda(boleta_hongkong, foto_hongkong):
    capturado: list = []
    cliente = _cliente(_respuesta(boleta_hongkong.model_dump_json()), capturado)
    asyncio.run(LectorOpenAI(cliente, MODELO, "none").leer(foto_hongkong))
    assert capturado[0]["cuerpo"]["reasoning"] == {"effort": "none"}


def test_config_elige_openai(monkeypatch):

    monkeypatch.setattr(modulo, "load_dotenv", lambda: None)
    for nombre, valor in {
        "TELEGRAM_TOKEN": "t",
        "ODOO_URL": "http://x",
        "ODOO_BASE": "b",
        "ODOO_CLAVE_API": "c",
        "MODO_PRUEBA": "no",
        "IA": "chatgpt",
        "OPENAI_MODELO": "",
        "OPENAI_ESFUERZO": "",
        "OPENAI_API_KEY": "",
    }.items():
        monkeypatch.setenv(nombre, valor)
    with pytest.raises(modulo.ConfigError, match="OPENAI_API_KEY"):
        modulo.Config.desde_entorno()
    monkeypatch.setenv("OPENAI_API_KEY", "sk-prueba")
    c = modulo.Config.desde_entorno()
    assert c.ia == "openai" and c.modelo == modulo.MODELO_OPENAI and c.esfuerzo is None
    monkeypatch.setenv("IA", "gemini")
    with pytest.raises(modulo.ConfigError, match="IA debe ser"):
        modulo.Config.desde_entorno()


def test_varias_paginas_van_como_varias_imagenes(boleta_hongkong, foto_hongkong):
    capturado: list = []
    lector = LectorOpenAI(_cliente(_respuesta(boleta_hongkong.model_dump_json()), capturado), MODELO, "high")
    asyncio.run(lector.leer([foto_hongkong, foto_hongkong, foto_hongkong], texto="Texto extraído del PDF:\nX"))
    contenido = capturado[0]["cuerpo"]["input"][0]["content"]
    assert [c["type"] for c in contenido] == ["input_image"] * 3 + ["input_text"]
