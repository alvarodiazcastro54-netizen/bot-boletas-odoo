import json

import httpx
import pytest

from boletas.odoo import Odoo, OdooError


def _odoo(respuestas, capturado, **kwargs):
    def manejar(peticion: httpx.Request) -> httpx.Response:
        capturado.append({"ruta": peticion.url.path, "headers": peticion.headers,
                          "cuerpo": json.loads(peticion.content)})
        estado, cuerpo = respuestas.pop(0)
        return httpx.Response(estado, json=cuerpo)
    return Odoo("https://odoo.ejemplo/", "mi_base", "clave-secreta", transporte=httpx.MockTransport(manejar),
                **kwargs)


def test_llamada_json2():
    capturado = []
    odoo = _odoo([(200, [7])], capturado)
    assert odoo.crear("res.partner", {"name": "SB TRADING"}) == 7
    p = capturado[0]
    assert p["ruta"] == "/json/2/res.partner/create"
    assert p["headers"]["authorization"] == "bearer clave-secreta"
    assert p["headers"]["x-odoo-database"] == "mi_base"
    assert p["cuerpo"] == {"vals_list": [{"name": "SB TRADING"}]}


def test_empresa_en_el_contexto():
    capturado = []
    odoo = _odoo([(200, []), (200, [])], capturado, empresa_id=2)
    odoo.buscar_leer("account.tax", [], ["id"])
    odoo.buscar_leer("product.product", [], ["id"], contexto={"lang": "es_PE"})
    assert capturado[0]["cuerpo"]["context"] == {"allowed_company_ids": [2]}
    assert capturado[1]["cuerpo"]["context"] == {"allowed_company_ids": [2], "lang": "es_PE"}


def test_errores():
    odoo = _odoo([(422, {"name": "odoo.exceptions.UserError", "message": "Falta el proveedor"}),
                  (401, {"name": "werkzeug.exceptions.Unauthorized", "message": "Invalid apikey"})], [])
    with pytest.raises(OdooError, match="Falta el proveedor") as exc:
        odoo.llamar("purchase.order", "button_confirm", ids=[1])
    assert exc.value.estado == 422
    with pytest.raises(OdooError, match="rechazó la clave"):
        odoo.llamar("res.partner", "search_read", domain=[])
