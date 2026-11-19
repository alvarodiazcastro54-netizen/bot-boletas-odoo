import datetime as dt

import pytest

from boletas import correcciones
from boletas.modelos import Boleta
from boletas.registro import Registrador, RegistroError, marca_de_boleta, referencia
from boletas.validacion import hay_errores, validar

from test_registro import OdooSimulado

HOY = dt.date(2026, 11, 20)


@pytest.fixture
def nota(boleta_hongkong) -> Boleta:
    b = boleta_hongkong.model_copy(deep=True)
    b.tipo = "nota_de_venta"
    b.proveedor.ruc = None
    b.proveedor.razon_social = "BAZAR JUANITA"
    b.proveedor.nombre_comercial = None
    b.serie_numero = None
    return b


def test_nota_de_venta_sin_ruc_se_puede_registrar(nota):
    problemas = validar(nota, HOY)
    assert not hay_errores(problemas)
    assert any("nota de venta" in p.mensaje for p in problemas)


def test_nota_sin_nombre_pide_a_quien_le_compraste(nota):
    nota.proveedor.razon_social = None
    errores = [p.mensaje for p in validar(nota, HOY) if p.nivel == "error"]
    assert errores == [
        "No se leyó a quién le compraste. Escribe el nombre del negocio, por ejemplo «proveedor BAZAR JUANITA»."
    ]


def test_boleta_sin_ruc_sigue_siendo_error(boleta_hongkong):
    boleta_hongkong.proveedor.ruc = None
    errores = [p.mensaje for p in validar(boleta_hongkong, HOY) if p.nivel == "error"]
    assert any("tipo nota" in e for e in errores)


@pytest.mark.parametrize("tipo", ["nota_de_credito", "guia_de_remision", "recibo_por_honorarios"])
def test_lo_que_no_es_compra_no_se_registra(boleta_hongkong, tipo):
    boleta_hongkong.tipo = tipo
    problemas = validar(boleta_hongkong, HOY)
    assert len(problemas) == 1 and hay_errores(problemas) and "no una compra" in problemas[0].mensaje


def test_referencia_y_marca_de_una_nota(nota):
    assert referencia(nota) == "NV-2026-11-05-58.30-015e"
    assert marca_de_boleta(nota) == "comprobante NV-2026-11-05-58.30-015e de BAZAR JUANITA"


def test_referencia_normaliza_el_numero(boleta_hongkong):
    boleta_hongkong.serie_numero = "B410-170132"
    assert referencia(boleta_hongkong) == "B410-00170132"


class OdooProveedores(OdooSimulado):
    def __init__(self, existe=True):
        super().__init__()
        self.existe = existe
        self.creados = []

    def buscar_leer(self, modelo, dominio, campos, limite=None, orden=None, contexto=None):
        if modelo == "res.partner":
            self.consultas.append((modelo, dominio))
            return [{"id": 77, "name": "Bazar Juanita"}] if self.existe else []
        return super().buscar_leer(modelo, dominio, campos, limite, orden, contexto)

    def crear(self, modelo, valores):
        self.creados.append((modelo, valores))
        return 99


def test_busca_al_proveedor_por_nombre_entre_los_que_no_tienen_ruc(nota):
    odoo = OdooProveedores()
    plan = Registrador(odoo).planificar(nota)
    assert plan.proveedor_id == 77
    dominio = next(d for m, d in odoo.consultas if m == "res.partner")
    assert dominio == [("name", "=ilike", "BAZAR JUANITA"), ("vat", "in", [False, ""])]
    assert any("sin RUC" in a for a in plan.avisos)


def test_crea_al_proveedor_solo_con_su_nombre(nota):
    odoo = OdooProveedores(existe=False)
    Registrador(odoo)._crear_proveedor(nota)
    modelo, valores = odoo.creados[0]
    assert modelo == "res.partner" and valores["name"] == "BAZAR JUANITA" and "vat" not in valores
    assert "Proveedor sin RUC" in valores["comment"]


def test_registrar_pide_lo_minimo(nota):
    nota.fecha_emision = None
    with pytest.raises(RegistroError, match="Faltan"):
        Registrador(OdooProveedores()).registrar(nota)


class OdooFactura:
    def __init__(self):
        self.escrito = []

    def llamar(self, *args, **kwargs):
        raise AssertionError("no debe crear la factura: ya existe")

    def campos(self, modelo):
        return {"l10n_latam_document_type_id"}

    def leer(self, modelo, ids, campos):
        return [
            {
                "id": 140,
                "name": "BILL/1",
                "state": "draft",
                "invoice_date": False,
                "ref": False,
                "invoice_line_ids": [],
                "l10n_latam_document_type_id": False,
                "l10n_latam_document_number": False,
                "l10n_latam_use_documents": True,
                "amount_total": 58.3,
            }
        ]

    def escribir(self, modelo, ids, valores):
        self.escrito.append(valores)

    def buscar_leer(self, *args, **kwargs):
        return []

    def crear(self, modelo, valores):
        self.escrito.append(valores)
        return 1


def test_factura_de_una_nota_queda_marcada_y_adjunta_el_comprobante(nota):
    odoo = OdooFactura()
    Registrador(odoo)._facturar({"id": 7, "invoice_ids": [140]}, nota, [("NV.jpg", b"\xff\xd8foto")])
    cambios, adjunto = odoo.escrito
    assert cambios["ref"] == "Nota de venta NV-2026-11-05-58.30-015e (sin comprobante de SUNAT)"
    assert "l10n_latam_document_type_id" not in cambios
    assert adjunto["name"] == "NV.jpg" and adjunto["mimetype"] == "image/jpeg"


def test_adjunta_pdf_y_xml_con_su_tipo(boleta_hongkong):
    odoo = OdooFactura()
    Registrador(odoo)._adjuntar(140, [("a.pdf", b"%PDF-1.4"), ("a.xml", b"<?xml version='1.0'?><Invoice/>")])
    assert [a["mimetype"] for a in odoo.escrito] == ["application/pdf", "application/xml"]


@pytest.mark.parametrize(
    "texto,campo,valor",
    [
        ("3 venta 65", "precio_venta", 65.0),
        ("3 pv 12,50", "precio_venta", 12.5),
        ("3 tienda Set de cortadores", "nombre_tienda", "Set de cortadores"),
    ],
)
def test_correcciones_de_la_tienda(boleta_hongkong, texto, campo, valor):
    b, _ = correcciones.aplicar(boleta_hongkong, texto)
    assert getattr(b.lineas[2], campo) == valor


def test_precio_de_venta_cero_no_vale(boleta_hongkong):
    with pytest.raises(correcciones.CorreccionInvalida):
        correcciones.aplicar(boleta_hongkong, "3 venta 0")


def test_cambiar_a_nota_de_venta(boleta_hongkong):
    b, cambio = correcciones.aplicar(boleta_hongkong, "tipo nota de venta")
    assert b.tipo == "nota_de_venta" and cambio == "Tipo = nota de venta."
    with pytest.raises(correcciones.CorreccionInvalida):
        correcciones.aplicar(boleta_hongkong, "tipo recibo")
