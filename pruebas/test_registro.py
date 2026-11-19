import base64

import pytest

from boletas import mensajes
from boletas.odoo import OdooError
from boletas.registro import Registrador, Resultado, nombre_producto, precio_de_venta

RUC_EMPRESA = "20100047218"


class OdooSimulado:
    empresa_id = 2

    def __init__(self):
        self.consultas = []

    def buscar_leer(self, modelo, dominio, campos, limite=None, orden=None, contexto=None):
        self.consultas.append((modelo, dominio))
        assert modelo != "ir.model.data", "un usuario que no es administrador no puede leer ir.model.data"
        if modelo == "res.company":
            return [{"id": 2, "name": "UYAY", "vat": RUC_EMPRESA}]
        if modelo == "account.tax":
            assert ("company_id", "=", 2) in dominio
            return [{"id": 24, "name": "VAT 18% G NG"}, {"id": 25, "name": "VAT 18%"}, {"id": 26, "name": "VAT 18% NG"}]
        if modelo == "product.category":
            return [
                {"id": 1, "complete_name": "All"},
                {"id": 10, "complete_name": "Hogar y Manualidades / Cocina"},
                {"id": 11, "complete_name": "Accesorios / Dijes y Llaveros"},
            ]
        return []

    def llamar(self, modelo, metodo, **argumentos):
        assert metodo == "get_external_id"
        externos = {
            "account.tax": {
                24: "account.2_purchase_tax_igv_18g_ng",
                25: "account.2_purchase_tax_igv_18",
                26: "account.2_purchase_tax_igv_18_ng",
            },
            "product.category": {1: "product.product_category_all"},
        }[modelo]
        return {str(i): externos.get(i, False) for i in argumentos["ids"]}

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
    assert (sorbete.precio_unitario, sorbete.descuento, sorbete.costo_unitario, sorbete.impuestos) == (
        11.0,
        50,
        5.5,
        [],
    )


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
    assert not any("IGV" in a for a in plan.avisos)


def test_busca_solo_productos_creados_por_esta_boleta(boleta_hongkong):
    odoo = OdooSimulado()
    reg = Registrador(odoo)
    reg._productos_de_la_boleta(7, boleta_hongkong)
    dominio = next(d for m, d in odoo.consultas if m == "product.supplierinfo")
    assert ("partner_id", "=", 7) in dominio
    assert ("product_tmpl_id.description", "ilike", "comprobante B410-00170132 del RUC 20101567428") in dominio


class OdooQueCrea(OdooSimulado):
    def __init__(self):
        super().__init__()
        self.creados = []

    def crear(self, modelo, valores):
        self.creados.append((modelo, valores))
        return len(self.creados)


def test_producto_nuevo_con_precio_sugerido_y_su_boleta(boleta_hongkong):
    odoo = OdooQueCrea()
    reg = Registrador(odoo)
    lp = reg.planificar(boleta_hongkong).lineas[0]
    reg._crear_producto(lp, 7, 1, boleta_hongkong)
    modelo, valores = odoo.creados[0]
    assert modelo == "product.product" and valores["list_price"] == 20.0
    assert valores["standard_price"] == lp.costo_unitario
    assert valores["description"] == "Creado por el bot de boletas: comprobante B410-00170132 del RUC 20101567428"
    assert valores["seller_ids"][0][2]["product_name"] == boleta_hongkong.lineas[0].descripcion


def test_sin_sugerir_precio_el_producto_entra_en_cero(boleta_hongkong):
    odoo = OdooQueCrea()
    reg = Registrador(odoo, sugerir_precio=False)
    lp = reg.planificar(boleta_hongkong).lineas[0]
    reg._crear_producto(lp, 7, 1, boleta_hongkong)
    assert odoo.creados[0][1]["list_price"] == 0.0


def test_precio_y_nombre_que_pone_el_usuario(boleta_hongkong):
    boleta_hongkong.lineas[0].precio_venta = 39.9
    boleta_hongkong.lineas[0].nombre_tienda = "Set cortador de verduras"
    odoo = OdooQueCrea()
    reg = Registrador(odoo)
    lp = reg.planificar(boleta_hongkong).lineas[0]
    reg._crear_producto(lp, 7, 1, boleta_hongkong)
    valores = odoo.creados[0][1]
    assert valores["list_price"] == 39.9 and valores["name"] == "Set cortador de verduras"


@pytest.mark.parametrize(
    "costo,margen,venta",
    [
        (11.00, 1.0, 22.00),
        (1.80, 1.0, 3.60),
        (1.815, 1.0, 3.70),
        (0.33, 1.0, 0.70),
        (5.50, 0.5, 8.30),
    ],
)
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


@pytest.mark.parametrize(
    "texto,esperado",
    [
        ("LLAVERO METAL UND", "LLAVERO METAL"),
        ("SOLING CUCHILLO COCINA ACERO INOX #736/MW9011 UN", "SOLING CUCHILLO COCINA ACERO INOX #736/MW9011"),
        ("CIERRE HERMETICO BOLSAS #651 (3x18.5CM) 4 UNI SET", "CIERRE HERMETICO BOLSAS #651 (3x18.5CM) 4 UNI SET"),
        ("UND", "UND"),
    ],
)
def test_nombre_producto(boleta_hongkong, texto, esperado):
    linea = boleta_hongkong.lineas[0].model_copy(update={"descripcion": texto})
    assert nombre_producto(linea) == esperado


class OdooFacturas:
    def __init__(self, estado="draft", falla=None, queda="posted", total=58.30, falla_al_leer=False):
        self.estado = estado
        self.falla = falla
        self.queda = queda
        self.total = total
        self.falla_al_leer = falla_al_leer
        self.llamadas = []
        self.lecturas = 0

    def factura(self):
        return {"id": 140, "name": "B BA34-03650027", "state": self.estado, "amount_total": self.total}

    def leer(self, modelo, ids, campos):
        assert modelo == "account.move" and ids == [140]
        self.lecturas += 1
        if self.falla_al_leer:
            raise OdooError("sin conexión", 0)
        return [self.factura()]

    def llamar(self, modelo, metodo, **argumentos):
        self.llamadas.append((modelo, metodo, argumentos["ids"]))
        self.estado = self.queda
        if self.falla:
            raise OdooError(self.falla, 422)


def _confirmar(odoo, boleta):
    return Registrador(odoo, confirmar_factura=True)._confirmar_factura(odoo.factura(), boleta)


def test_confirma_la_factura_borrador_con_una_sola_lectura(boleta_hongkong):
    odoo = OdooFacturas()
    aviso, factura = _confirmar(odoo, boleta_hongkong)
    assert aviso is None and factura["state"] == "posted"
    assert odoo.llamadas == [("account.move", "action_post", [140])] and odoo.lecturas == 1


def test_factura_ya_confirmada_no_se_vuelve_a_confirmar(boleta_hongkong):
    odoo = OdooFacturas(estado="posted")
    assert _confirmar(odoo, boleta_hongkong)[0] is None
    assert odoo.llamadas == [] and odoo.lecturas == 0


@pytest.mark.parametrize(
    "opciones,texto",
    [
        ({"falla": "Falta la fecha contable.", "queda": "draft"}, "Falta la fecha contable."),
        ({"queda": "draft"}, "pidió algo más"),
        ({"estado": "cancel"}, "cancel"),
        ({"total": 58.31}, "suma 58.31 y la boleta dice 58.30"),
        ({"falla_al_leer": True}, "No pude comprobar"),
    ],
)
def test_si_no_se_puede_confirmar_avisa(boleta_hongkong, opciones, texto):
    aviso, factura = _confirmar(OdooFacturas(**opciones), boleta_hongkong)
    assert aviso and texto in aviso and factura["state"] != "posted"


def test_total_distinto_no_llama_a_odoo(boleta_hongkong):
    odoo = OdooFacturas(total=58.31)
    _confirmar(odoo, boleta_hongkong)
    assert odoo.llamadas == []


def test_sin_total_no_confirma(boleta_hongkong):
    odoo = OdooFacturas()
    aviso, _ = _confirmar(odoo, boleta_hongkong.model_copy(update={"total": None}))
    assert "no se leyó el total" in aviso and odoo.llamadas == []


def test_timeout_al_confirmar_pero_odoo_la_confirmo(boleta_hongkong):
    odoo = OdooFacturas(falla="ReadTimeout")
    aviso, factura = _confirmar(odoo, boleta_hongkong)
    assert aviso is None and factura["state"] == "posted"


class OdooOrden:
    def __init__(self, estado_factura):
        self.estado_factura = estado_factura

    def buscar_leer(self, modelo, dominio, campos, **opciones):
        return []

    def leer(self, modelo, ids, campos):
        assert modelo == "account.move"
        return [{"id": i, "state": self.estado_factura} for i in ids]


ORDEN = {"id": 7, "name": "P00036", "state": "purchase", "picking_ids": [3], "invoice_ids": [140]}


@pytest.mark.parametrize(
    "confirmar,estado,completa",
    [
        (True, "draft", False),
        (True, "posted", True),
        (False, "draft", True),
    ],
)
def test_factura_sin_confirmar_permite_reintentar(confirmar, estado, completa):
    assert Registrador(OdooOrden(estado), confirmar_factura=confirmar)._esta_completa(ORDEN) is completa


class OdooDeLaOrden:
    def __init__(self, productos):
        self.productos = productos
        self.escrito = None

    def campos(self, modelo):
        return set()

    def leer(self, modelo, ids, campos):
        if modelo == "purchase.order.line":
            return [{"id": i, "product_id": p} for i, p in zip(ids, self.productos, strict=False)]
        assert modelo == "product.product" and campos == ["product_tmpl_id"]
        return [{"id": ids[0], "product_tmpl_id": [90 + ids[0], "LLAVERO METAL"]}]

    def escribir(self, modelo, ids, valores):
        self.escrito = (modelo, ids, valores)


def test_cada_linea_sabe_su_producto(boleta_hongkong):
    plan = Registrador(OdooSimulado()).planificar(boleta_hongkong)
    odoo = OdooDeLaOrden([[50 + n, f"Producto {n}"] for n in range(7)])
    productos = Registrador(odoo)._productos_de_la_orden(list(range(1, 8)), plan.lineas)
    assert [(n, p) for n, _, p in productos] == [(n, 49 + n) for n in range(1, 8)]
    assert productos[6][1] == "Producto 6"


def test_si_la_orden_cambio_no_inventa_productos(boleta_hongkong):
    plan = Registrador(OdooSimulado()).planificar(boleta_hongkong)
    odoo = OdooDeLaOrden([[50, "x"]])
    assert Registrador(odoo)._productos_de_la_orden([1], plan.lineas) == []


def test_poner_foto_en_la_plantilla_del_producto():
    odoo = OdooDeLaOrden([])
    assert Registrador(odoo).poner_foto(7, b"\xff\xd8foto") == "LLAVERO METAL"
    modelo, ids, valores = odoo.escrito
    assert modelo == "product.template" and ids == [97]
    assert base64.b64decode(valores["image_1920"]) == b"\xff\xd8foto"


class OdooConFotos:
    def __init__(self):
        self.consultas = []

    def campos(self, modelo):
        return set()

    def buscar_leer(self, modelo, dominio, campos, limite=None, orden=None, contexto=None):
        self.consultas.append((modelo, dominio, limite, orden))
        if modelo == "product.template":
            assert dominio == [("description", "ilike", "Creado por el bot de boletas")] and orden == "id desc"
            return [{"id": 30}, {"id": 29}]
        if modelo == "purchase.order.line":
            assert dominio == [("order_id.origin", "=", "Bot de boletas")]
            return [{"product_id": [128, "SET CORTADOR"]}]
        assert modelo == "ir.attachment" and ("res_field", "=", "image_1920") in dominio
        return [{"id": 1, "res_id": 29}]

    def leer(self, modelo, ids, campos):
        if modelo == "product.product":
            return [{"id": 128, "product_tmpl_id": [28, "SET CORTADOR"]}]
        filas = {
            30: {
                "id": 30,
                "name": "LLAVERO METAL",
                "description": "<p>Creado por el bot de boletas: comprobante B410-00170200 del RUC 20101567428</p>",
            },
            28: {"id": 28, "name": "SET CORTADOR", "description": False},
        }
        return [filas[i] for i in ids]


def test_productos_sin_foto_incluyen_los_viejos_del_bot():
    lista, total = Registrador(OdooConFotos()).productos_sin_foto(limite=5)
    assert total == 2
    assert lista == [
        {"id": 30, "nombre": "LLAVERO METAL", "serie": "B410-00170200"},
        {"id": 28, "nombre": "SET CORTADOR", "serie": None},
    ]


def test_mensaje_dice_si_la_factura_quedo_confirmada():
    borrador = mensajes.registrado(Resultado("P00036", [], "B BA34-1", [], False))
    confirmada = mensajes.registrado(Resultado("P00036", [], "B BA34-1", [], False, factura_confirmada=True))
    assert "en borrador" in borrador and "confirmada" in confirmada and "borrador" not in confirmada
