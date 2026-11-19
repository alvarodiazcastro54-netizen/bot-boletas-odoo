import pytest

from boletas import mensajes
from boletas.bot import BotBoletas
from boletas.registro import (
    Registrador,
    RegistroError,
    nombre_producto,
    referencia,
    sin_codigo,
)
from boletas.validacion import validar

from test_ajustes import compra, linea
from test_bot import OdooFalso


class OdooConDatos(OdooFalso):
    def __init__(self, compras=None, proveedores=None, monedas=None, empresa_moneda="PEN"):
        self.compras = compras or []
        self.proveedores = proveedores or []
        self.monedas = monedas or {}
        self.empresa_moneda = empresa_moneda
        self.consultas = []
        self.escrito = []
        self.creados = []
        self.factura = {
            "id": 140,
            "name": "BILL/1",
            "state": "draft",
            "ref": "Nota de venta S10234",
            "amount_total": 104.0,
            "l10n_latam_use_documents": True,
        }

    def buscar_leer(self, modelo, dominio, campos, limite=None, orden=None, contexto=None):
        self.consultas.append((modelo, dominio, contexto))
        if modelo == "res.company":
            return [{"id": 2, "name": "UYAY", "vat": "10456789124", "currency_id": [1, self.empresa_moneda]}]
        if modelo == "res.partner":
            return [{"id": 7, "name": "DISTRIBUIDORA EJEMPLO S.A.C."}]
        if modelo == "purchase.order":
            por_numero = any(isinstance(c, tuple) and c[0] == "partner_ref" for c in dominio)
            return [] if por_numero else self.compras
        if modelo == "product.supplierinfo":
            return self.proveedores
        if modelo == "res.currency":
            codigo = dominio[0][2]
            return (
                [{"rate": self.monedas[codigo], "inverse_rate": 1 / self.monedas[codigo]}]
                if codigo in self.monedas
                else []
            )
        if modelo == "l10n_latam.document.type":
            return [{"id": 3}]
        return super().buscar_leer(modelo, dominio, campos, limite, orden, contexto)

    def campos(self, modelo):
        return {"l10n_latam_document_type_id"} if modelo == "account.move" else set()

    def leer(self, modelo, ids, campos):
        if modelo == "purchase.order":
            return [
                {
                    "id": 9,
                    "name": "P00140",
                    "state": "purchase",
                    "picking_ids": [5],
                    "invoice_ids": [140],
                    "order_line": [1],
                    "partner_ref": "S10234",
                }
            ]
        if modelo == "account.move":
            return [dict(self.factura)]
        return []

    def escribir(self, modelo, ids, valores):
        self.escrito.append((modelo, valores))
        if modelo == "account.move":
            self.factura.update({k: v for k, v in valores.items() if k in self.factura})

    def crear(self, modelo, valores):
        self.creados.append((modelo, valores))
        return 1

    def llamar(self, modelo, metodo, **argumentos):
        if metodo == "action_post":
            self.factura["state"] = "posted"
            return True
        return super().llamar(modelo, metodo, **argumentos)


def test_nota_de_venta_con_ruc_no_se_confirma_ni_lleva_tipo_de_documento():
    nota = compra([linea("TORNASOL", 24, 104 / 24, 104.00)], 104.00, tipo="nota_de_venta", serie_numero="S10234")
    odoo = OdooConDatos()
    reg = Registrador(odoo, confirmar_factura=True)
    plan = reg.planificar(nota)
    assert any("no es un comprobante de SUNAT" in a for a in plan.avisos)
    odoo.factura.update(
        {
            "ref": False,
            "invoice_date": False,
            "invoice_line_ids": [],
            "l10n_latam_document_type_id": False,
            "l10n_latam_document_number": False,
        }
    )
    reg._facturar({"id": 9, "invoice_ids": [140]}, nota, [])
    cambios = next(v for m, v in odoo.escrito if m == "account.move")
    assert cambios["ref"] == "Nota de venta S10234 (sin comprobante de SUNAT)"
    assert "l10n_latam_document_type_id" not in cambios


def test_avisa_de_una_compra_parecida_y_la_une():
    boleta = compra([linea("TORNASOL", 24, 104 / 24, 104.00)], 104.00, serie_numero="B001-00000777")
    parecida = {
        "id": 9,
        "name": "P00140",
        "partner_ref": "S10234",
        "amount_total": 104.0,
        "date_order": "2026-11-16 17:00:00",
        "state": "purchase",
        "picking_ids": [5],
        "invoice_ids": [140],
        "order_line": [1],
    }
    odoo = OdooConDatos(compras=[parecida])
    reg = Registrador(odoo, confirmar_factura=True)
    plan = reg.planificar(boleta)
    assert plan.parecida["name"] == "P00140"
    texto = mensajes.resumen(plan, validar(boleta), None)
    assert "Ya registré la compra P00140" in texto and "Es la misma compra" in texto
    teclado = BotBoletas.teclado("x", plan, validar(boleta))
    assert teclado.inline_keyboard[0][0].callback_data == "uni:x"

    r = reg.unir(9, boleta, [("B001-00000777.pdf", b"%PDF-1.4")])
    factura = next(v for m, v in odoo.escrito if m == "account.move")
    assert factura["l10n_latam_document_number"] == "B001-00000777" and factura["l10n_latam_document_type_id"] == 3
    assert ("purchase.order", {"partner_ref": "B001-00000777"}) in odoo.escrito
    assert r.factura_confirmada and r.orden == "P00140" and not r.productos_nuevos
    assert any(m == "ir.attachment" for m, _ in odoo.creados)
    assert "No creé otra compra ni sumé stock" in mensajes.unido(r)


def test_una_compra_oficial_de_otro_monto_no_es_parecida():
    boleta = compra([linea("TORNASOL", 24, 104 / 24, 104.00)], 104.00)
    otra = {"id": 9, "name": "P00140", "partner_ref": "B001-00000555", "amount_total": 250.0}
    assert Registrador(OdooConDatos(compras=[otra])).planificar(boleta).parecida is None


def test_cotizacion_de_otro_monto_avisa_sin_ofrecer_unir():
    boleta = compra([linea("TORNASOL", 24, 104 / 24, 104.00)], 104.00)
    cotizacion = {
        "id": 9,
        "name": "P00140",
        "partner_ref": "S10234",
        "amount_total": 250.0,
        "date_order": "2026-11-15 17:00:00",
    }
    plan = Registrador(OdooConDatos(compras=[cotizacion])).planificar(boleta)
    assert plan.parecida["exacta"] is False
    texto = mensajes.resumen(plan, validar(boleta), None)
    assert "desde una nota o cotización (S10234, S/ 250.00)" in texto and "corrige la compra P00140" in texto
    botones = [b.callback_data for fila in BotBoletas.teclado("x", plan, validar(boleta)).inline_keyboard for b in fila]
    assert "uni:x" not in botones and "reg:x" in botones


def test_compra_en_dolares_usa_el_tipo_de_cambio_de_odoo():
    boleta = compra([linea("AUDIFONOS BLUETOOTH", 10, 8.50, 85.00)], 85.00, moneda="USD")
    odoo = OdooConDatos(monedas={"USD": 0.27})
    plan = Registrador(odoo, sugerir_precio=False).planificar(boleta)
    lp = plan.lineas[0]
    assert lp.costo_unitario == 8.50 and lp.costo_soles == pytest.approx(8.50 / 0.27)
    assert not plan.errores and any("tipo de cambio" in a for a in plan.avisos)
    contexto = next(c for m, _, c in odoo.consultas if m == "res.currency")
    assert contexto == {"date": "2026-11-16"}
    assert "(US$ 8.50)" in mensajes.resumen(plan, validar(boleta), None)


def test_compra_en_dolares_sin_tipo_de_cambio_no_se_registra():
    boleta = compra([linea("AUDIFONOS BLUETOOTH", 10, 8.50, 85.00)], 85.00, moneda="USD")
    reg = Registrador(OdooConDatos(monedas={"USD": 1.0}))
    plan = reg.planificar(boleta)
    assert plan.errores and "tipo de cambio" in plan.errores[0]
    assert "❌" in mensajes.resumen(plan, validar(boleta), None)
    assert BotBoletas.teclado("x", plan, validar(boleta)).inline_keyboard[0][0].text == "✏️ Corregir"
    with pytest.raises(RegistroError, match="tipo de cambio"):
        reg.registrar(boleta)


def test_reusa_productos_por_codigo_solo_si_se_pide():
    boleta = compra([linea("[AGS-134] TORNASOL", 24, 104 / 24, 104.00, codigo="AGS-134")], 104.00)
    existente = [
        {
            "product_code": "AGS-134",
            "product_id": [55, "Sombras tornasol Jarusa"],
            "product_tmpl_id": [50, "Sombras tornasol Jarusa"],
            "product_name": "TORNASOL",
        }
    ]
    odoo = OdooConDatos(proveedores=existente)
    assert Registrador(odoo, reusar_productos=True).planificar(boleta).lineas[0].producto_id == 55
    sin_marca = OdooConDatos(proveedores=[])
    assert Registrador(sin_marca).planificar(boleta).lineas[0].producto_id is None


def test_quita_el_codigo_repetido_del_nombre():
    l = linea("[MS-410] UÑAS BONITA LUMA X 12", 12, 2.85, 34.20, codigo="MS-410")
    assert sin_codigo(l) == "UÑAS BONITA LUMA X 12" and nombre_producto(l) == "UÑAS BONITA LUMA X 12"
    otro = linea("[OFERTA] CARTERA", 1, 10, 10, codigo="CT-1")
    assert sin_codigo(otro) == "[OFERTA] CARTERA"


def test_dos_notas_sin_ruc_del_mismo_dia_y_monto_tienen_referencias_distintas():
    a = compra([linea("VINCHA PERLAS", 6, 5.00, 30.00)], 30.00, tipo="nota_de_venta", serie_numero=None)
    b = compra([linea("ARETES", 3, 10.00, 30.00)], 30.00, tipo="nota_de_venta", serie_numero=None)
    for nota in (a, b):
        nota.proveedor.ruc = None
    assert referencia(a) != referencia(b) and referencia(a).startswith("NV-2026-11-16-30.00-")
    assert referencia(a) == referencia(a.model_copy(deep=True))


def test_otra_moneda_no_se_registra():
    boleta = compra([linea("PULSERA", 100, 2.50, 250.00)], 250.00, moneda="OTRA")
    assert any("no es soles ni dólares" in p.mensaje for p in validar(boleta) if p.nivel == "error")


def test_compra_por_internet_sin_ruc_dice_que_hacer():
    boleta = compra([linea("ORGANIZADOR", 3, 15.00, 45.00)], 45.00, tipo="otro")
    boleta.proveedor.ruc = None
    assert any("compra por internet" in p.mensaje and "tipo nota" in p.mensaje for p in validar(boleta))


def test_corregir_un_nombre_no_cambia_la_referencia_de_la_nota():
    a = compra([linea("VINCHA", 6, 5.00, 30.00)], 30.00, tipo="nota_de_venta", serie_numero=None)
    a.proveedor.ruc = None
    b = a.model_copy(deep=True)
    b.lineas[0].descripcion = "VINCHA PERLAS"
    c = a.model_copy(deep=True)
    c.lineas[0].cantidad, c.lineas[0].precio_unitario = 3, 10.00
    assert referencia(a) == referencia(b) != referencia(c)
