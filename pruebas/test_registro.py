import pytest

from boletas.registro import Registrador, nombre_producto, precio_de_venta

RUC_EMPRESA = "20100047218"


class OdooSimulado:
    empresa_id = 2

    def __init__(self):
        self.consultas = []

    def buscar_leer(self, modelo, dominio, campos, limite=None, orden=None, contexto=None):
        self.consultas.append((modelo, dominio))
        if modelo == "res.company":
            return [{"id": 2, "name": "UYAY", "vat": RUC_EMPRESA}]
        if modelo == "ir.model.data" and ("model", "=", "account.tax") in dominio:
            assert ("name", "=", "2_purchase_tax_igv_18") in dominio
            return [{"res_id": 25}]
        if modelo == "ir.model.data":
            return [{"res_id": 1}]
        if modelo == "product.category":
            return [{"id": 1, "complete_name": "All"},
                    {"id": 10, "complete_name": "Hogar y Manualidades / Cocina"},
                    {"id": 11, "complete_name": "Accesorios / Dijes y Llaveros"}]
        return []

    def campos(self, modelo):
        return set()


def _factura(boleta):
    boleta.tipo = "factura"
    boleta.cliente_documento = RUC_EMPRESA
    return boleta


def test_boleta_sin_credito_fiscal(boleta_hongkong):
    plan = Registrador(OdooSimulado()).planificar(boleta_hongkong)
    assert not plan.con_credito_fiscal
    sorbete = plan.lineas[2]
    assert (sorbete.precio_unitario, sorbete.descuento, sorbete.costo_unitario, sorbete.impuestos) == \
        (11.0, 50, 5.5, [])


def test_factura_de_la_empresa_usa_el_igv_de_esa_empresa(boleta_hongkong):
    plan = Registrador(OdooSimulado()).planificar(_factura(boleta_hongkong))
    assert plan.con_credito_fiscal
    sorbete = plan.lineas[2]
    assert sorbete.impuestos == [25]
    assert sorbete.precio_unitario == pytest.approx(11.0 / 1.18)
    assert sorbete.costo_unitario == pytest.approx(5.5 / 1.18)


def test_factura_a_nombre_de_otro(boleta_hongkong):
    boleta = _factura(boleta_hongkong)
    boleta.cliente_documento = "20101567428"
    plan = Registrador(OdooSimulado()).planificar(boleta)
    assert not plan.con_credito_fiscal
    assert any("no está a nombre de la empresa" in a for a in plan.avisos)


def test_nuevo_rus_nunca_usa_credito_fiscal(boleta_hongkong):
    plan = Registrador(OdooSimulado(), usa_credito_fiscal=False).planificar(_factura(boleta_hongkong))
    assert not plan.con_credito_fiscal
    assert plan.lineas[2].impuestos == []
    assert any("USA_CREDITO_FISCAL=no" in a for a in plan.avisos)


@pytest.mark.parametrize("costo,margen,venta", [
    (11.00, 1.0, 22.00), (1.80, 1.0, 3.60), (1.815, 1.0, 3.70), (0.33, 1.0, 0.70), (5.50, 0.5, 8.30),
])
def test_precio_de_venta_redondeado_hacia_arriba(costo, margen, venta):
    assert precio_de_venta(costo, margen) == pytest.approx(venta)


def test_factura_con_precios_sin_igv_y_sin_credito(boleta_hongkong):
    boleta = _factura(boleta_hongkong)
    boleta.precios_incluyen_igv = False
    plan = Registrador(OdooSimulado(), usa_credito_fiscal=False).planificar(boleta)
    assert plan.lineas[2].costo_unitario == pytest.approx(5.5 * 1.18)


def test_descuento_global_se_reparte(boleta_hongkong):
    boleta_hongkong.descuento_global = 5.83
    plan = Registrador(OdooSimulado()).planificar(boleta_hongkong)
    assert plan.lineas[0].descuento == 0
    assert plan.lineas[0].costo_unitario == pytest.approx(11.0 * 0.9)
    assert sum(lp.costo_unitario * lp.linea.cantidad for lp in plan.lineas) == pytest.approx(58.30 - 5.83)


def test_categorias_de_la_tienda(boleta_hongkong):
    reg = Registrador(OdooSimulado())
    assert reg.nombres_categorias() == ["Hogar y Manualidades / Cocina", "Accesorios / Dijes y Llaveros"]
    plan = reg.planificar(boleta_hongkong)
    assert [lp.categoria_id for lp in plan.lineas] == [10] * 6 + [11]


@pytest.mark.parametrize("texto,esperado", [
    ("LLAVERO METAL UND", "LLAVERO METAL"),
    ("SOLING CUCHILLO COCINA ACERO INOX #736/MW9011 UN", "SOLING CUCHILLO COCINA ACERO INOX #736/MW9011"),
    ("CIERRE HERMETICO BOLSAS #651 (3x18.5CM) 4 UNI SET", "CIERRE HERMETICO BOLSAS #651 (3x18.5CM) 4 UNI SET"),
    ("UND", "UND"),
])
def test_nombre_producto(boleta_hongkong, texto, esperado):
    linea = boleta_hongkong.lineas[0].model_copy(update={"descripcion": texto})
    assert nombre_producto(linea) == esperado
