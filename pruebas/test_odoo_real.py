import os
import pathlib
import random

import httpx
import pytest

from boletas.odoo import Odoo
from boletas.registro import Registrador, RegistroError, precio_de_venta
from boletas.validacion import ruc_valido

URL = os.environ.get("ODOO_PRUEBA_URL", "http://127.0.0.1:8070")
BASE = os.environ.get("ODOO_PRUEBA_BASE", "prueba_bot")
ARCHIVO_CLAVE = pathlib.Path("D:/Entornos/odoo19/clave_api_prueba.txt")


def _clave() -> str | None:
    if os.environ.get("ODOO_PRUEBA_CLAVE"):
        return os.environ["ODOO_PRUEBA_CLAVE"]
    return ARCHIVO_CLAVE.read_text(encoding="utf-8").strip() if ARCHIVO_CLAVE.exists() else None


def _disponible() -> bool:
    try:
        return httpx.get(f"{URL}/web/health", timeout=3).status_code == 200 and _clave() is not None
    except httpx.HTTPError:
        return False


pytestmark = pytest.mark.skipif(not _disponible(), reason="Odoo de prueba no disponible")


def _ruc_nuevo() -> str:
    while True:
        ruc = "20" + "".join(random.choice("0123456789") for _ in range(9))
        if ruc_valido(ruc):
            return ruc


@pytest.fixture(scope="module")
def odoo():
    assert BASE == "prueba_bot", "Estas pruebas escriben datos: solo se corren en la base de prueba."
    cliente = Odoo(URL, BASE, _clave())
    yield cliente
    cliente.cerrar()


@pytest.fixture
def boleta(boleta_hongkong):
    boleta_hongkong.proveedor.ruc = _ruc_nuevo()
    return boleta_hongkong


def _orden(odoo, plan_o_nombre):
    return odoo.buscar_leer("purchase.order", [("name", "=", plan_o_nombre)],
                            ["id", "amount_total", "amount_tax", "state", "partner_ref", "order_line",
                             "picking_ids", "invoice_ids", "partner_id"])[0]


def _lineas(odoo, ids):
    return odoo.leer("purchase.order.line", ids,
                     ["product_id", "product_qty", "price_unit", "discount", "tax_ids", "price_subtotal"])


def test_registro_completo_de_la_boleta_real(odoo, boleta, foto_hongkong):
    reg = Registrador(odoo)
    plan = reg.planificar(boleta)
    assert plan.proveedor_id is None and plan.productos_nuevos == 7 and not plan.con_credito_fiscal

    r = reg.registrar(boleta, foto_hongkong, "B410-00170132.jpg")
    assert r.proveedor_nuevo and len(r.productos_nuevos) == 7
    assert "LLAVERO METAL" in r.productos_nuevos

    orden = _orden(odoo, r.orden)
    assert orden["state"] == "purchase" and orden["partner_ref"] == "B410-00170132"
    assert orden["amount_total"] == pytest.approx(58.30) and orden["amount_tax"] == pytest.approx(0)
    lineas = _lineas(odoo, orden["order_line"])
    for linea, esperada in zip(lineas, boleta.lineas):
        assert linea["tax_ids"] == []
        assert linea["price_subtotal"] == pytest.approx(esperada.importe)
        assert linea["discount"] == pytest.approx(esperada.descuento_pct)

    socio = odoo.leer("res.partner", [orden["partner_id"][0]],
                      ["vat", "name", "l10n_latam_identification_type_id", "country_id"])[0]
    assert socio["vat"] == boleta.proveedor.ruc and socio["name"] == "SB TRADING S.R.LTDA"
    assert socio["l10n_latam_identification_type_id"][1].startswith("RUC")

    costos = {"5406059": 11.00, "9070031": 4.95, "5509007": 5.50, "2102086": 2.75,
              "5501671": 1.75, "5512023": 6.75, "5010010": 1.80}
    for linea, esperada in zip(lineas, boleta.lineas):
        producto = odoo.leer("product.product", [linea["product_id"][0]],
                             ["qty_available", "standard_price", "is_storable", "seller_ids"])[0]
        assert producto["is_storable"]
        assert producto["qty_available"] == pytest.approx(esperada.cantidad)
        assert producto["standard_price"] == pytest.approx(costos[esperada.codigo])
        vendedor = odoo.leer("product.supplierinfo", producto["seller_ids"][:1], ["product_code"])[0]
        assert vendedor["product_code"] == esperada.codigo

    for picking in odoo.leer("stock.picking", orden["picking_ids"], ["state"]):
        assert picking["state"] == "done"

    assert len(orden["invoice_ids"]) == 1
    factura = odoo.leer("account.move", orden["invoice_ids"],
                        ["state", "move_type", "amount_total", "invoice_date", "l10n_latam_document_type_id",
                         "l10n_latam_document_number", "partner_id"])[0]
    assert factura["state"] == "draft" and factura["move_type"] == "in_invoice"
    assert factura["amount_total"] == pytest.approx(58.30)
    assert factura["invoice_date"] == "2026-11-05"
    tipo = odoo.leer("l10n_latam.document.type", [factura["l10n_latam_document_type_id"][0]], ["code"])[0]
    assert tipo["code"] == "03"
    assert factura["l10n_latam_document_number"] == "B410-00170132"
    adjuntos = odoo.buscar_leer("ir.attachment", [("res_model", "=", "account.move"),
                                                  ("res_id", "=", orden["invoice_ids"][0])],
                                ["name", "file_size", "mimetype"])
    assert [a["name"] for a in adjuntos] == ["B410-00170132.jpg"]
    assert adjuntos[0]["file_size"] == len(foto_hongkong) and adjuntos[0]["mimetype"] == "image/jpeg"

    assert reg.planificar(boleta).ya_registrada
    with pytest.raises(RegistroError, match="ya está registrada"):
        reg.registrar(boleta, foto_hongkong)


def test_segunda_boleta_reusa_productos(odoo, boleta):
    reg = Registrador(odoo)
    primera = reg.registrar(boleta)
    otra = boleta.model_copy(deep=True)
    otra.serie_numero = "B410-00170200"
    otra.lineas = otra.lineas[:2] + [otra.lineas[6].model_copy(update={"codigo": "9999999",
                                                                         "descripcion": "TAZA NUEVA UND"})]
    otra.total = round(sum(l.importe for l in otra.lineas), 2)
    plan = reg.planificar(otra)
    assert plan.proveedor_id is not None
    assert [lp.producto_id is not None for lp in plan.lineas] == [True, True, False]

    r = reg.registrar(otra)
    assert not r.proveedor_nuevo and r.productos_nuevos == ["TAZA NUEVA"]
    set_cortador = _lineas(odoo, _orden(odoo, r.orden)["order_line"])[0]["product_id"][0]
    assert set_cortador == _lineas(odoo, _orden(odoo, primera.orden)["order_line"])[0]["product_id"][0]
    stock = odoo.leer("product.product", [set_cortador], ["qty_available"])[0]["qty_available"]
    assert stock == pytest.approx(4)


def test_factura_de_la_empresa_con_credito_fiscal(odoo, boleta):
    ruc_empresa = _ruc_nuevo()
    reg = Registrador(odoo, ruc_empresa=ruc_empresa)
    boleta.tipo = "factura"
    boleta.serie_numero = "F001-00000456"
    boleta.cliente_documento = ruc_empresa
    plan = reg.planificar(boleta)
    assert plan.con_credito_fiscal

    r = reg.registrar(boleta)
    orden = _orden(odoo, r.orden)
    assert orden["amount_total"] == pytest.approx(58.30, abs=0.02)
    assert orden["amount_tax"] == pytest.approx(8.89, abs=0.02)
    factura = odoo.leer("account.move", orden["invoice_ids"],
                        ["amount_total", "amount_tax", "l10n_latam_document_type_id"])[0]
    assert factura["amount_tax"] == pytest.approx(8.89, abs=0.02)
    tipo = odoo.leer("l10n_latam.document.type", [factura["l10n_latam_document_type_id"][0]], ["code"])[0]
    assert tipo["code"] == "01"
    producto = _lineas(odoo, orden["order_line"])[0]["product_id"][0]
    costo = odoo.leer("product.product", [producto], ["standard_price"])[0]["standard_price"]
    assert costo == pytest.approx(11.00 / 1.18, abs=0.01)


def test_retoma_un_registro_a_medias(odoo, boleta):
    reg = Registrador(odoo)
    r = reg.registrar(boleta)
    orden = _orden(odoo, r.orden)
    odoo.llamar("account.move", "unlink", ids=orden["invoice_ids"])

    plan = reg.planificar(boleta)
    assert plan.orden and not plan.ya_registrada and plan.productos_nuevos == 0
    r2 = reg.registrar(boleta)
    assert r2.orden == r.orden and r2.productos_nuevos == []
    assert len(_orden(odoo, r.orden)["invoice_ids"]) == 1


def _categoria(odoo, padre: str, hija: str) -> int:
    def buscar_o_crear(nombre, padre_id):
        filas = odoo.buscar_leer("product.category", [("name", "=", nombre), ("parent_id", "=", padre_id)],
                                 ["id"], limite=1)
        return filas[0]["id"] if filas else odoo.crear("product.category",
                                                       {"name": nombre, "parent_id": padre_id})
    return buscar_o_crear(hija, buscar_o_crear(padre, False))


def test_categorias_y_precio_de_venta(odoo, boleta):
    cocina = _categoria(odoo, "Hogar y Manualidades", "Cocina")
    llaveros = _categoria(odoo, "Accesorios", "Dijes y Llaveros")
    reg = Registrador(odoo, margen_venta=1.0)
    nombres = reg.nombres_categorias()
    assert "Hogar y Manualidades / Cocina" in nombres and "Accesorios / Dijes y Llaveros" in nombres
    assert "Hogar y Manualidades" not in nombres
    assert not any(n.startswith("All") for n in nombres)

    r = reg.registrar(boleta)
    lineas = _lineas(odoo, _orden(odoo, r.orden)["order_line"])
    web = "is_published" in odoo.campos("product.product")
    productos = odoo.leer("product.product", [l["product_id"][0] for l in lineas],
                          ["categ_id", "list_price", "standard_price"] + (["is_published"] if web else []))
    assert [p["categ_id"][0] for p in productos] == [cocina] * 6 + [llaveros]
    for p in productos:
        assert p["list_price"] == pytest.approx(precio_de_venta(p["standard_price"], 1.0))
        if web:
            assert not p["is_published"]


def test_sin_margen_el_precio_queda_en_cero(odoo, boleta):
    r = Registrador(odoo).registrar(boleta)
    linea = _lineas(odoo, _orden(odoo, r.orden)["order_line"])[0]
    assert odoo.leer("product.product", [linea["product_id"][0]], ["list_price"])[0]["list_price"] == 0


def test_empresa_configurada(boleta):
    con_empresa = Odoo(URL, BASE, _clave(), empresa_id=1)
    try:
        reg = Registrador(con_empresa)
        assert reg.empresa()["id"] == 1
        r = reg.registrar(boleta)
        orden = con_empresa.buscar_leer("purchase.order", [("name", "=", r.orden)], ["company_id"])[0]
        assert orden["company_id"][0] == 1
    finally:
        con_empresa.cerrar()


def test_empresa_inexistente():
    con_empresa = Odoo(URL, BASE, _clave(), empresa_id=999999)
    try:
        with pytest.raises(RegistroError, match="no tiene acceso a la empresa 999999"):
            Registrador(con_empresa).empresa()
    finally:
        con_empresa.cerrar()


def test_otros_cargos_y_descuento_global(odoo, boleta):
    reg = Registrador(odoo)
    boleta.otros_cargos = 0.50
    boleta.descuento_global = 5.00
    boleta.total = 53.80
    r = reg.registrar(boleta)
    orden = _orden(odoo, r.orden)
    assert orden["amount_total"] == pytest.approx(53.30, abs=0.01)
    factura = odoo.leer("account.move", orden["invoice_ids"], ["amount_total"])[0]
    assert factura["amount_total"] == pytest.approx(53.80, abs=0.01)
