import os
import pathlib
import random

import httpx
import pytest

from boletas import correcciones, mensajes
from boletas.ajustes import ajustar
from boletas.lectura import preparar_foto_producto
from boletas.odoo import Odoo, OdooError
from boletas.registro import Registrador, RegistroError, marca_de_boleta, precio_de_venta, referencia
from boletas.sunat import xml_a_boleta
from boletas.validacion import ruc_valido

from conftest import UNIDADES_LUMA
from test_sunat import factura_xml

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
    return odoo.buscar_leer(
        "purchase.order",
        [("name", "=", plan_o_nombre)],
        [
            "id",
            "amount_total",
            "amount_tax",
            "state",
            "partner_ref",
            "order_line",
            "picking_ids",
            "invoice_ids",
            "partner_id",
        ],
    )[0]


def _lineas(odoo, ids):
    return odoo.leer(
        "purchase.order.line", ids, ["product_id", "product_qty", "price_unit", "discount", "tax_ids", "price_subtotal"]
    )


def test_registro_completo_de_la_boleta_real(odoo, boleta, foto_hongkong):
    reg = Registrador(odoo)
    plan = reg.planificar(boleta)
    assert plan.proveedor_id is None and plan.productos_nuevos == 7 and not plan.con_credito_fiscal

    boleta.lineas[6].nombre_tienda = "Llavero de metal"
    r = reg.registrar(boleta, [("B410-00170132.jpg", foto_hongkong)])
    assert r.proveedor_nuevo and len(r.productos_nuevos) == 7
    assert "Llavero de metal" in r.productos_nuevos
    assert [n for n, _, _ in r.productos] == list(range(1, 8))

    _, nombre, producto_id = r.productos[6]
    assert reg.poner_foto(producto_id, preparar_foto_producto(foto_hongkong)) == nombre
    plantilla = odoo.leer("product.product", [producto_id], ["product_tmpl_id"])[0]["product_tmpl_id"][0]
    assert odoo.leer("product.template", [plantilla], ["image_128"])[0]["image_128"]
    sin_foto = {p["id"]: p for p in reg.productos_sin_foto(limite=1000)[0]}
    otra = odoo.leer("product.product", [r.productos[0][2]], ["product_tmpl_id"])[0]["product_tmpl_id"][0]
    assert plantilla not in sin_foto and sin_foto[otra]["serie"] == "B410-00170132"

    orden = _orden(odoo, r.orden)
    assert orden["state"] == "purchase" and orden["partner_ref"] == "B410-00170132"
    assert orden["amount_total"] == pytest.approx(58.30) and orden["amount_tax"] == pytest.approx(0)
    lineas = _lineas(odoo, orden["order_line"])
    for linea, esperada in zip(lineas, boleta.lineas, strict=True):
        assert linea["tax_ids"] == []
        assert linea["price_subtotal"] == pytest.approx(esperada.importe)
        assert linea["discount"] == pytest.approx(esperada.descuento_pct)

    socio = odoo.leer(
        "res.partner", [orden["partner_id"][0]], ["vat", "name", "l10n_latam_identification_type_id", "country_id"]
    )[0]
    assert socio["vat"] == boleta.proveedor.ruc and socio["name"] == "SB TRADING S.R.LTDA"
    assert socio["l10n_latam_identification_type_id"][1].startswith("RUC")

    costos = {
        "5406059": 11.00,
        "9070031": 4.95,
        "5509007": 5.50,
        "2102086": 2.75,
        "5501671": 1.75,
        "5512023": 6.75,
        "5010010": 1.80,
    }
    for linea, esperada in zip(lineas, boleta.lineas, strict=True):
        producto = odoo.leer(
            "product.product",
            [linea["product_id"][0]],
            ["qty_available", "standard_price", "is_storable", "seller_ids", "list_price", "description"],
        )[0]
        assert producto["is_storable"] and producto["list_price"] >= producto["standard_price"] * 1.1
        assert marca_de_boleta(boleta) in producto["description"]
        assert producto["qty_available"] == pytest.approx(esperada.cantidad)
        assert producto["standard_price"] == pytest.approx(costos[esperada.codigo])
        vendedor = odoo.leer("product.supplierinfo", producto["seller_ids"][:1], ["product_code"])[0]
        assert vendedor["product_code"] == esperada.codigo

    for picking in odoo.leer("stock.picking", orden["picking_ids"], ["state"]):
        assert picking["state"] == "done"

    assert len(orden["invoice_ids"]) == 1
    factura = odoo.leer(
        "account.move",
        orden["invoice_ids"],
        [
            "state",
            "move_type",
            "amount_total",
            "invoice_date",
            "l10n_latam_document_type_id",
            "l10n_latam_document_number",
            "partner_id",
        ],
    )[0]
    assert factura["state"] == "draft" and factura["move_type"] == "in_invoice"
    assert factura["amount_total"] == pytest.approx(58.30)
    assert factura["invoice_date"] == "2026-11-05"
    tipo = odoo.leer("l10n_latam.document.type", [factura["l10n_latam_document_type_id"][0]], ["code"])[0]
    assert tipo["code"] == "03"
    assert factura["l10n_latam_document_number"] == "B410-00170132"
    adjuntos = odoo.buscar_leer(
        "ir.attachment",
        [("res_model", "=", "account.move"), ("res_id", "=", orden["invoice_ids"][0])],
        ["name", "file_size", "mimetype"],
    )
    assert [a["name"] for a in adjuntos] == ["B410-00170132.jpg"]
    assert adjuntos[0]["file_size"] == len(foto_hongkong) and adjuntos[0]["mimetype"] == "image/jpeg"

    assert reg.planificar(boleta).ya_registrada
    with pytest.raises(RegistroError, match="ya está registrada"):
        reg.registrar(boleta, [("B410-00170132.jpg", foto_hongkong)])


def test_segunda_boleta_crea_sus_propios_productos(odoo, boleta):
    reg = Registrador(odoo)
    primera = reg.registrar(boleta)
    otra = boleta.model_copy(deep=True)
    otra.serie_numero = "B410-00170200"
    otra.lineas = otra.lineas[:2]
    otra.total = round(sum(l.importe for l in otra.lineas), 2)
    plan = reg.planificar(otra)
    assert plan.proveedor_id is not None and plan.productos_nuevos == 2

    r = reg.registrar(otra)
    assert not r.proveedor_nuevo and len(r.productos_nuevos) == 2
    nuevo = _lineas(odoo, _orden(odoo, r.orden)["order_line"])[0]["product_id"][0]
    anterior = _lineas(odoo, _orden(odoo, primera.orden)["order_line"])[0]["product_id"][0]
    assert nuevo != anterior
    stock = odoo.leer("product.product", [nuevo], ["qty_available"])[0]["qty_available"]
    assert stock == pytest.approx(otra.lineas[0].cantidad)


class _FallaAlCrearElTercerProducto(Registrador):
    creados = 0

    def _crear_producto(self, *args, **kwargs):
        if self.creados == 2:
            raise OdooError("falla simulada al crear un producto", 0)
        self.creados += 1
        return super()._crear_producto(*args, **kwargs)


def test_reintento_reusa_los_productos_que_creo_la_misma_boleta(odoo, boleta):
    with pytest.raises(OdooError, match="simulada"):
        _FallaAlCrearElTercerProducto(odoo).registrar(boleta)
    reg = Registrador(odoo)
    plan = reg.planificar(boleta)
    assert plan.orden is None and plan.productos_nuevos == 5
    r = reg.registrar(boleta)
    assert len(r.productos_nuevos) == 5
    marca = marca_de_boleta(boleta)
    plantillas = odoo.buscar_leer("product.template", [("description", "ilike", marca)], ["id"])
    assert len(plantillas) == 7


def test_factura_confirmada_sola(odoo, boleta, foto_hongkong):
    r = Registrador(odoo, confirmar_factura=True).registrar(boleta, [("B410-00170132.jpg", foto_hongkong)])
    assert r.factura_confirmada and not r.avisos
    orden = _orden(odoo, r.orden)
    factura = odoo.leer("account.move", orden["invoice_ids"], ["state", "name", "amount_total"])[0]
    assert factura["state"] == "posted" and factura["name"] == r.factura
    assert factura["amount_total"] == pytest.approx(58.30)


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
    factura = odoo.leer(
        "account.move", orden["invoice_ids"], ["amount_total", "amount_tax", "l10n_latam_document_type_id"]
    )[0]
    assert factura["amount_tax"] == pytest.approx(8.89, abs=0.02)
    tipo = odoo.leer("l10n_latam.document.type", [factura["l10n_latam_document_type_id"][0]], ["code"])[0]
    assert tipo["code"] == "01"
    producto = _lineas(odoo, orden["order_line"])[0]["product_id"][0]
    costo = odoo.leer("product.product", [producto], ["standard_price"])[0]["standard_price"]
    assert costo == pytest.approx(11.00 / 1.18, abs=0.01)


class _FallaAlConfirmar(Registrador):
    def _confirmar_factura(self, *args, **kwargs):
        raise OdooError("falla simulada al confirmar", 0)


def test_reintento_confirma_la_factura_que_quedo_en_borrador(odoo, boleta):
    with pytest.raises(OdooError, match="simulada"):
        _FallaAlConfirmar(odoo, confirmar_factura=True).registrar(boleta)
    reg = Registrador(odoo, confirmar_factura=True)
    assert not reg.planificar(boleta).ya_registrada
    r = reg.registrar(boleta)
    assert r.factura_confirmada and r.productos_nuevos == []
    assert reg.planificar(boleta).ya_registrada


class _FallaAlFacturar(Registrador):
    def _facturar(self, *args, **kwargs):
        raise RegistroError("falla simulada al crear la factura")


def test_retoma_un_registro_a_medias(odoo, boleta):
    with pytest.raises(RegistroError, match="simulada"):
        _FallaAlFacturar(odoo).registrar(boleta)

    reg = Registrador(odoo)
    plan = reg.planificar(boleta)
    assert plan.orden and not plan.ya_registrada and plan.productos_nuevos == 0
    orden = _orden(odoo, plan.orden["name"])
    assert orden["state"] == "purchase" and not orden["invoice_ids"]

    r = reg.registrar(boleta)
    assert r.orden == plan.orden["name"] and r.productos_nuevos == [] and not r.proveedor_nuevo
    assert len(_orden(odoo, r.orden)["invoice_ids"]) == 1
    assert reg.planificar(boleta).ya_registrada


def _categoria(odoo, padre: str, hija: str) -> int:
    def buscar_o_crear(nombre, padre_id):
        filas = odoo.buscar_leer(
            "product.category", [("name", "=", nombre), ("parent_id", "=", padre_id)], ["id"], limite=1
        )
        return filas[0]["id"] if filas else odoo.crear("product.category", {"name": nombre, "parent_id": padre_id})

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
    productos = odoo.leer(
        "product.product",
        [l["product_id"][0] for l in lineas],
        ["categ_id", "list_price", "standard_price"] + (["is_published"] if web else []),
    )
    assert [p["categ_id"][0] for p in productos] == [cocina] * 6 + [llaveros]
    for p in productos:
        assert p["list_price"] == pytest.approx(precio_de_venta(p["standard_price"], 1.0))
        if web:
            assert not p["is_published"]


def test_sin_sugerencia_el_precio_queda_en_cero(odoo, boleta):
    r = Registrador(odoo, sugerir_precio=False).registrar(boleta)
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


def test_nota_de_venta_sin_ruc(odoo, boleta, foto_hongkong):
    boleta.tipo = "nota_de_venta"
    boleta.proveedor.ruc = None
    boleta.serie_numero = None
    boleta.proveedor.razon_social = f"BAZAR PRUEBA {random.randint(1, 10**9)}"
    reg = Registrador(odoo, confirmar_factura=True)
    plan = reg.planificar(boleta)
    assert plan.proveedor_id is None and plan.productos_nuevos == 7

    r = reg.registrar(boleta, [("NV.jpg", foto_hongkong)])
    assert r.proveedor_nuevo and not r.factura_confirmada and len(r.productos_nuevos) == 7
    orden = _orden(odoo, r.orden)
    assert orden["partner_ref"].startswith("NV-2026-11-05-58.30-") and orden["state"] == "purchase"
    socio = odoo.leer("res.partner", [orden["partner_id"][0]], ["vat", "name"])[0]
    assert not socio["vat"] and socio["name"] == boleta.proveedor.razon_social
    factura = odoo.leer("account.move", orden["invoice_ids"], ["state", "ref"])[0]
    assert factura["state"] == "draft" and factura["ref"].startswith("Nota de venta NV-2026-11-05-58.30")

    otra = Registrador(odoo, confirmar_factura=True)
    assert otra.planificar(boleta).proveedor_id == orden["partner_id"][0]
    assert otra.planificar(boleta).orden["name"] == r.orden
    assert otra.planificar(boleta).ya_registrada
    assert "Factura de proveedor en borrador: todavía sin número" in mensajes.registrado(r)


def test_xml_de_factura_electronica_se_registra(odoo):

    boleta = xml_a_boleta(factura_xml())
    boleta.proveedor.ruc = _ruc_nuevo()
    boleta.serie_numero = f"F001-{random.randint(1, 10**7)}"
    r = Registrador(odoo, usa_credito_fiscal=False, confirmar_factura=True).registrar(
        boleta, [("factura.xml", factura_xml())]
    )
    orden = _orden(odoo, r.orden)
    assert orden["amount_total"] == pytest.approx(69.62, abs=0.01)
    factura = odoo.leer("account.move", orden["invoice_ids"], ["amount_total", "state"])[0]
    assert factura["amount_total"] == pytest.approx(70.12, abs=0.01) and factura["state"] == "posted"
    adjunto = odoo.buscar_leer(
        "ir.attachment", [("res_model", "=", "account.move"), ("res_id", "=", orden["invoice_ids"][0])], ["mimetype"]
    )[0]
    assert adjunto["mimetype"] in ("application/xml", "text/xml")


def test_compra_por_paquetes_entra_por_unidad_y_la_factura_cuadra(odoo, mayorista):

    mayorista.proveedor.ruc = _ruc_nuevo()
    mayorista.serie_numero = f"F{random.randint(100, 999)}-{random.randint(1, 99999999)}"
    mayorista.tipo = "factura"
    mayorista.precios_incluyen_igv = True
    boleta, _ = ajustar(mayorista)
    r = Registrador(odoo, sugerir_precio=False, confirmar_factura=True).registrar(boleta)
    orden = _orden(odoo, r.orden)
    assert [l["product_qty"] for l in _lineas(odoo, orden["order_line"])] == UNIDADES_LUMA
    factura = odoo.leer("account.move", orden["invoice_ids"], ["amount_total", "state"])[0]
    assert factura["amount_total"] == pytest.approx(1519.45, abs=0.001)
    assert factura["state"] == "posted" and r.factura_confirmada


def _stock(odoo, producto_ids):
    return {f["id"]: f["qty_available"] for f in odoo.leer("product.product", producto_ids, ["qty_available"])}


def test_nota_con_ruc_queda_en_borrador_y_se_une_a_su_boleta(odoo, mayorista):

    ruc = _ruc_nuevo()
    mayorista.proveedor.ruc = ruc
    mayorista.serie_numero = f"S{random.randint(10000, 99999)}"
    nota, _ = ajustar(mayorista)
    reg = Registrador(odoo, sugerir_precio=False, confirmar_factura=True)
    r = reg.registrar(nota)
    orden = _orden(odoo, r.orden)
    factura = odoo.leer("account.move", orden["invoice_ids"], ["state", "ref", "amount_total"])[0]
    assert factura["state"] == "draft" and not r.factura_confirmada
    assert factura["ref"] == f"Nota de venta {nota.serie_numero} (sin comprobante de SUNAT)"
    productos = [l["product_id"][0] for l in _lineas(odoo, orden["order_line"])]
    stock = _stock(odoo, productos)

    boleta = nota.model_copy(deep=True)
    boleta.tipo = "boleta"
    boleta.serie_numero = f"B{random.randint(100, 999)}-{random.randint(1, 99999999)}"
    plan = reg.planificar(boleta)
    assert plan.orden is None and plan.parecida["name"] == r.orden
    unida = reg.unir(plan.parecida["id"], boleta, [("boleta.pdf", b"%PDF-1.4 prueba")])
    assert unida.orden == r.orden and unida.factura_confirmada and len(unida.productos) == len(nota.lineas)
    factura = odoo.leer(
        "account.move",
        orden["invoice_ids"],
        ["state", "amount_total", "l10n_latam_document_number", "l10n_latam_document_type_id"],
    )[0]
    assert factura["state"] == "posted" and factura["amount_total"] == pytest.approx(1519.45, abs=0.001)
    assert (
        factura["l10n_latam_document_type_id"][1].startswith("(03)")
        or "Boleta" in factura["l10n_latam_document_type_id"][1]
    )
    assert _orden(odoo, r.orden)["partner_ref"] == referencia(boleta)
    assert _stock(odoo, productos) == stock
    assert reg.planificar(boleta).ya_registrada


def test_compra_en_dolares_guarda_el_costo_en_soles(odoo, boleta):
    usd = odoo.llamar(
        "res.currency",
        "search_read",
        domain=[("name", "=", "USD"), ("active", "in", [True, False])],
        fields=["id", "active"],
        limit=1,
    )[0]
    if not usd["active"]:
        odoo.escribir("res.currency", [usd["id"]], {"active": True})
    empresa = Registrador(odoo).empresa()["id"]
    fecha = boleta.fecha_emision
    tasas = odoo.buscar_leer(
        "res.currency.rate",
        [("currency_id", "=", usd["id"]), ("name", "=", fecha), ("company_id", "=", empresa)],
        ["id"],
    )
    if tasas:
        odoo.escribir("res.currency.rate", [tasas[0]["id"]], {"rate": 0.25})
    else:
        odoo.crear("res.currency.rate", {"currency_id": usd["id"], "name": fecha, "rate": 0.25, "company_id": empresa})
    boleta.moneda = "USD"
    reg = Registrador(odoo, sugerir_precio=True)
    plan = reg.planificar(boleta)
    assert not plan.errores and plan.tipo_de_cambio == pytest.approx(4.0)
    r = reg.registrar(boleta)
    orden = odoo.buscar_leer("purchase.order", [("name", "=", r.orden)], ["currency_id", "order_line"])[0]
    assert orden["currency_id"][1] == "USD"
    linea = _lineas(odoo, orden["order_line"])[0]
    producto = odoo.leer("product.product", [linea["product_id"][0]], ["standard_price", "list_price"])[0]
    assert producto["standard_price"] == pytest.approx(plan.lineas[0].costo_unitario * 4, abs=0.01)
    assert producto["list_price"] >= producto["standard_price"]


def test_reusa_el_producto_por_codigo_si_se_pide(odoo, boleta):
    reg = Registrador(odoo, sugerir_precio=False, reusar_productos=True)
    primera = reg.registrar(boleta)
    producto = _lineas(odoo, _orden(odoo, primera.orden)["order_line"])[0]["product_id"][0]
    antes = _stock(odoo, [producto])[producto]
    otra = boleta.model_copy(deep=True)
    otra.serie_numero = f"B410-{random.randint(1, 99999999)}"
    plan = reg.planificar(otra)
    assert plan.lineas[0].producto_id == producto and plan.productos_nuevos == 0
    segunda = reg.registrar(otra)
    assert not segunda.productos_nuevos
    assert _stock(odoo, [producto])[producto] == antes + boleta.lineas[0].cantidad


def test_dos_notas_sin_ruc_del_mismo_dia_y_monto(odoo, boleta):

    boleta.tipo = "nota_de_venta"
    boleta.proveedor.ruc = None
    boleta.serie_numero = None
    boleta.proveedor.razon_social = f"PUESTO PRUEBA {random.randint(1, 10**9)}"
    reg = Registrador(odoo, sugerir_precio=False)
    primera = reg.registrar(boleta)

    corregida = boleta.model_copy(deep=True)
    corregida.lineas[0].descripcion += " ROJO"
    assert reg.planificar(corregida).ya_registrada

    otra = boleta.model_copy(deep=True)
    otra.lineas[0].cantidad, otra.lineas[0].precio_unitario = 1, otra.lineas[0].importe
    plan = reg.planificar(otra)
    assert not plan.ya_registrada and plan.parecida["name"] == primera.orden
    assert reg.registrar(otra).orden != primera.orden

    igual, _ = correcciones.aplicar(corregida, "numero 2")
    assert not reg.planificar(igual).ya_registrada
    assert reg.registrar(igual).orden != primera.orden


def test_el_proveedor_guarda_el_nombre_sin_el_codigo(odoo, boleta):
    boleta.lineas[0].descripcion = f"[{boleta.lineas[0].codigo}] {boleta.lineas[0].descripcion}"
    r = Registrador(odoo, sugerir_precio=False).registrar(boleta)
    linea = _lineas(odoo, _orden(odoo, r.orden)["order_line"])[0]
    info = odoo.buscar_leer(
        "product.supplierinfo",
        [("product_id", "=", linea["product_id"][0])],
        ["product_name", "product_code"],
        limite=1,
    )
    if not info:
        plantilla = odoo.leer("product.product", [linea["product_id"][0]], ["product_tmpl_id"])[0]
        info = odoo.buscar_leer(
            "product.supplierinfo",
            [("product_tmpl_id", "=", plantilla["product_tmpl_id"][0])],
            ["product_name", "product_code"],
            limite=1,
        )
    assert not info[0]["product_name"].startswith("[")
    nombre = odoo.leer("purchase.order.line", [linea["id"]] if "id" in linea else [], ["name"])
    assert not nombre or nombre[0]["name"].count(f"[{boleta.lineas[0].codigo}]") <= 1


def test_liquidacion_de_compra_con_dni_y_tipo_04(odoo, boleta):

    boleta.tipo = "liquidacion_de_compra"
    boleta.proveedor.ruc = None
    boleta.proveedor.dni = f"4{random.randint(1000000, 9999999)}"
    boleta.proveedor.razon_social = f"VENDEDOR PRUEBA {random.randint(1, 10**9)}"
    boleta.serie_numero = f"E001-{random.randint(1, 99999999)}"
    boleta, _ = ajustar(boleta)
    reg = Registrador(odoo, sugerir_precio=False, confirmar_factura=True)
    r = reg.registrar(boleta)
    assert not r.factura_confirmada and r.proveedor_nuevo
    orden = _orden(odoo, r.orden)
    socio = odoo.leer(
        "res.partner", [orden["partner_id"][0]], ["vat", "is_company", "l10n_latam_identification_type_id"]
    )[0]
    assert socio["vat"] == boleta.proveedor.dni and not socio["is_company"]
    factura = odoo.leer(
        "account.move", orden["invoice_ids"], ["state", "l10n_latam_document_type_id", "l10n_latam_document_number"]
    )[0]
    assert factura["state"] == "draft" and "04" in factura["l10n_latam_document_type_id"][1]
    assert reg.planificar(boleta).ya_registrada and reg.planificar(boleta).proveedor_id == orden["partner_id"][0]


def test_productos_de_una_compra_por_su_nombre(odoo, boleta):
    reg = Registrador(odoo, sugerir_precio=False)
    r = reg.registrar(boleta)
    productos = reg.productos_de_compra(r.orden)
    assert [n for n, _, _ in productos] == list(range(1, 8)) and [p for _, _, p in productos] == [
        p for _, _, p in r.productos
    ]
    assert reg.productos_de_compra("P99999999") == []
