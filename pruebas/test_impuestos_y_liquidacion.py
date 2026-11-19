import pytest

from boletas import correcciones, mensajes
from boletas.ajustes import ajustar, avisos
from boletas.registro import (
    CODIGO_DOCUMENTO,
    Registrador,
    sin_comprobante_de_sunat_confirmable,
)
from boletas.sunat import xml_a_boleta
from boletas.validacion import hay_errores, validar

from test_ajustes import compra, linea
from test_bot import OdooFalso
from test_registro import RUC_EMPRESA, OdooSimulado
from test_registro_compras import OdooConDatos
from test_sunat import NS, _impuesto


def _costos(boleta, odoo=None, **opciones):
    plan = Registrador(odoo or OdooFalso(), sugerir_precio=False, **opciones).planificar(boleta)
    return plan, [lp.costo_unitario for lp in plan.lineas]


def _factura_mixta(marcar=True):
    libro = linea("LIBRO COLOREAR", 10, 8.00, 80.00)
    libro.exonerado = marcar
    return compra(
        [libro, linea("CUADERNO A4", 10, 5.00, 50.00)],
        139.00,
        tipo="factura",
        incluye_igv=False,
        op_gravadas=50.00,
        igv=9.00,
    )


def test_libro_exonerado_marcado_tiene_su_costo_exacto():
    b, _ = ajustar(_factura_mixta())
    assert not hay_errores(validar(b))
    plan, costos = _costos(b)
    assert costos == [pytest.approx(8.00), pytest.approx(5.90)]
    assert "sin IGV" in mensajes.resumen(plan, validar(b), None)


def test_sin_marcas_el_igv_se_reparte():
    b, _ = ajustar(_factura_mixta(marcar=False))
    _, costos = _costos(b)
    assert sum(c * 10 for c in costos) == pytest.approx(139.00) and costos[0] != pytest.approx(8.00)


def test_marcas_que_no_cuadran_con_el_igv_avisan_y_se_reparte():
    boleta = _factura_mixta()
    boleta.lineas[1].exonerado = True
    b, _ = ajustar(boleta)
    assert any("no cuadran con el IGV" in p.mensaje for p in validar(b))
    _, costos = _costos(b)
    assert sum(c * 10 for c in costos) == pytest.approx(139.00)


def test_con_credito_fiscal_la_linea_exonerada_no_lleva_igv():

    boleta = _factura_mixta()
    boleta.cliente_documento = RUC_EMPRESA
    plan, costos = _costos(boleta, OdooSimulado(), usa_credito_fiscal=True)
    assert plan.con_credito_fiscal
    assert plan.lineas[0].impuestos == [] and plan.lineas[1].impuestos == [25]
    assert costos == [pytest.approx(8.00), pytest.approx(5.00)]


def test_corregir_si_una_linea_paga_igv():
    b, cambio = correcciones.aplicar(_factura_mixta(marcar=False), "1 exonerado")
    assert b.lineas[0].exonerado and cambio == "Línea 1: sin IGV (exonerada)."
    b, _ = correcciones.aplicar(b, "1 gravado")
    assert not b.lineas[0].exonerado


def test_xml_marca_la_linea_exonerada():
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice {NS.format(raiz="Invoice")}>
  <cbc:ID>F001-77</cbc:ID><cbc:IssueDate>2026-11-10</cbc:IssueDate>
  <cbc:InvoiceTypeCode listID="0101">01</cbc:InvoiceTypeCode><cbc:DocumentCurrencyCode>PEN</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyIdentification><cbc:ID schemeID="6">20101567428</cbc:ID></cac:PartyIdentification>
    <cac:PartyLegalEntity><cbc:RegistrationName>LIBRERIA</cbc:RegistrationName></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:LegalMonetaryTotal><cbc:PayableAmount currencyID="PEN">80.00</cbc:PayableAmount></cac:LegalMonetaryTotal>
  <cac:InvoiceLine><cbc:ID>1</cbc:ID><cbc:InvoicedQuantity unitCode="NIU">10</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="PEN">80.00</cbc:LineExtensionAmount>
    {_impuesto("80.00", "0.00", "9997")}
    <cac:Item><cbc:Description>LIBRO COLOREAR</cbc:Description></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="PEN">8.00</cbc:PriceAmount></cac:Price></cac:InvoiceLine>
</Invoice>""".encode()
    b = xml_a_boleta(xml)
    assert b.lineas[0].exonerado and b.lineas[0].importe == 80.00 and not hay_errores(validar(b))


def _liquidacion(**extra):
    b = compra(
        [linea("PALTA FUERTE", 50, 4.00, 200.00)],
        200.00,
        tipo="liquidacion_de_compra",
        serie_numero="E001-00000012",
        incluye_igv=True,
    )
    b.proveedor.ruc = None
    b.proveedor.razon_social = "JUAN QUISPE MAMANI"
    b.proveedor.dni = extra.get("dni", "40123456")
    return b


def test_liquidacion_de_compra_se_registra_con_el_dni_del_vendedor():
    b = _liquidacion()
    problemas = validar(b)
    assert not hay_errores(problemas) and any("liquidación de compra" in p.mensaje for p in problemas)
    odoo = OdooConDatos()
    plan = Registrador(odoo).planificar(b)
    assert "DNI 40123456" in mensajes.resumen(plan, problemas, None)
    assert any(m == "res.partner" and ("vat", "=", "40123456") in d for m, d, _ in odoo.consultas)
    Registrador(odoo)._crear_proveedor(b)
    _, valores = odoo.creados[-1]
    assert valores["vat"] == "40123456" and valores["is_company"] is False
    assert "liquidación de compra" in valores["comment"]


def test_liquidacion_sin_dni_avisa_y_se_corrige():
    b = _liquidacion(dni=None)
    assert any("«dni 12345678»" in p.mensaje for p in validar(b))
    b, _ = correcciones.aplicar(b, "dni 40123456")
    assert b.proveedor.dni == "40123456"
    b, _ = correcciones.aplicar(_factura_mixta(), "tipo liquidacion")
    assert b.tipo == "liquidacion_de_compra"


def test_la_factura_de_una_liquidacion_queda_en_borrador():

    assert CODIGO_DOCUMENTO["liquidacion_de_compra"] == "04"
    assert sin_comprobante_de_sunat_confirmable(_liquidacion())


def test_xml_de_liquidacion_toma_al_vendedor_con_dni():
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice {NS.format(raiz="Invoice")}>
  <cbc:ID>E001-12</cbc:ID><cbc:IssueDate>2026-11-10</cbc:IssueDate>
  <cbc:InvoiceTypeCode listID="0101">04</cbc:InvoiceTypeCode><cbc:DocumentCurrencyCode>PEN</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyIdentification><cbc:ID schemeID="6">10456789124</cbc:ID></cac:PartyIdentification>
    <cac:PartyLegalEntity><cbc:RegistrationName>UYAY</cbc:RegistrationName></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty><cac:Party>
    <cac:PartyIdentification><cbc:ID schemeID="1">40123456</cbc:ID></cac:PartyIdentification>
    <cac:PartyLegalEntity><cbc:RegistrationName>JUAN QUISPE MAMANI</cbc:RegistrationName></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingCustomerParty>
  {_impuesto("169.49", "30.51")}
  <cac:LegalMonetaryTotal><cbc:PayableAmount currencyID="PEN">200.00</cbc:PayableAmount></cac:LegalMonetaryTotal>
  <cac:InvoiceLine><cbc:ID>1</cbc:ID><cbc:InvoicedQuantity unitCode="KGM">50</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="PEN">169.49</cbc:LineExtensionAmount>
    {_impuesto("169.49", "30.51")}
    <cac:Item><cbc:Description>PALTA FUERTE</cbc:Description></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="PEN">3.3898</cbc:PriceAmount></cac:Price></cac:InvoiceLine>
</Invoice>""".encode()
    b = xml_a_boleta(xml)
    assert b.tipo == "liquidacion_de_compra" and b.proveedor.dni == "40123456" and b.proveedor.ruc is None
    assert b.proveedor.razon_social == "JUAN QUISPE MAMANI" and not hay_errores(validar(b))


def test_factura_con_precio_sin_igv_e_importe_con_igv():
    b, hechos = ajustar(
        compra(
            [linea("CARTERA", 10, 10.00, 118.00), linea("BILLETERA", 5, 20.00, 118.00)],
            236.00,
            tipo="factura",
            incluye_igv=False,
            op_gravadas=200.00,
            igv=36.00,
        )
    )
    assert [l.precio_unitario for l in b.lineas] == [11.80, 23.60] and b.precios_incluyen_igv
    assert not hay_errores(validar(b))
    assert any("precio unitario sin IGV y el importe con IGV" in a for a in avisos(b, hechos))
    _, costos = _costos(b)
    assert costos == [pytest.approx(11.80), pytest.approx(23.60)]


def test_liquidacion_con_neto_a_pagar_usa_el_importe_total():
    b = compra(
        [linea("CHALINA ALPACA", 30, 25.00, 750.00), linea("CHULLO", 20, 15.00, 300.00)],
        1034.25,
        tipo="liquidacion_de_compra",
        serie_numero="E001-00000012",
        incluye_igv=False,
        op_gravadas=1050.00,
        igv=189.00,
        otros_cargos=-15.75,
    )
    b.proveedor.ruc, b.proveedor.dni, b.proveedor.razon_social = None, "40123456", "JUAN QUISPE MAMANI"
    b, hechos = ajustar(b)
    assert (b.total, b.otros_cargos, b.precios_incluyen_igv, hechos.retencion) == (1239.00, None, False, 15.75)
    assert not hay_errores(validar(b))
    _, costos = _costos(b, usa_credito_fiscal=False)
    assert costos == [pytest.approx(29.50), pytest.approx(17.70)]
    assert any("retención de renta (S/ 15.75)" in a for a in avisos(b, hechos))
    sin_otros = b.model_copy(deep=True)
    sin_otros.total, sin_otros.otros_cargos = 1034.25, None
    assert ajustar(sin_otros)[1].retencion == 15.75


def test_xml_con_bonificacion():
    gratis = """<cac:InvoiceLine><cbc:ID>2</cbc:ID>
      <cbc:InvoicedQuantity unitCode="NIU">2</cbc:InvoicedQuantity>
      <cbc:LineExtensionAmount currencyID="PEN">16.95</cbc:LineExtensionAmount>
      <cac:PricingReference><cac:AlternativeConditionPrice>
        <cbc:PriceAmount currencyID="PEN">10.00</cbc:PriceAmount><cbc:PriceTypeCode>02</cbc:PriceTypeCode>
      </cac:AlternativeConditionPrice></cac:PricingReference>
      <cac:Item><cbc:Description>LLAVERO DE REGALO</cbc:Description></cac:Item>
      <cac:Price><cbc:PriceAmount currencyID="PEN">0</cbc:PriceAmount></cac:Price></cac:InvoiceLine>"""
    xml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Invoice {NS.format(raiz="Invoice")}>
  <cbc:ID>F001-9</cbc:ID><cbc:IssueDate>2026-11-10</cbc:IssueDate>
  <cbc:InvoiceTypeCode listID="0101">01</cbc:InvoiceTypeCode><cbc:DocumentCurrencyCode>PEN</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyIdentification><cbc:ID schemeID="6">20101567428</cbc:ID></cac:PartyIdentification>
    <cac:PartyLegalEntity><cbc:RegistrationName>SB TRADING</cbc:RegistrationName></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingSupplierParty>
  {_impuesto("50.00", "9.00")}
  <cac:LegalMonetaryTotal><cbc:PayableAmount currencyID="PEN">59.00</cbc:PayableAmount></cac:LegalMonetaryTotal>
  <cac:InvoiceLine><cbc:ID>1</cbc:ID><cbc:InvoicedQuantity unitCode="NIU">2</cbc:InvoicedQuantity>
    <cbc:LineExtensionAmount currencyID="PEN">50.00</cbc:LineExtensionAmount>
    {_impuesto("50.00", "9.00")}
    <cac:PricingReference><cac:AlternativeConditionPrice><cbc:PriceAmount currencyID="PEN">29.50</cbc:PriceAmount>
      <cbc:PriceTypeCode>01</cbc:PriceTypeCode></cac:AlternativeConditionPrice></cac:PricingReference>
    <cac:Item><cbc:Description>TAZA MUG</cbc:Description></cac:Item>
    <cac:Price><cbc:PriceAmount currencyID="PEN">25.00</cbc:PriceAmount></cac:Price></cac:InvoiceLine>
  {gratis}
</Invoice>""".encode()
    b = xml_a_boleta(xml)
    taza, regalo = b.lineas
    assert taza.importe == 59.00 and regalo.importe == 0 and regalo.descuento_pct == 100
    assert regalo.precio_unitario == 10.00 and b.descuento_global is None
    assert not hay_errores(validar(b))


def test_factura_con_precio_con_igv_e_importe_sin_igv():

    boleta = compra(
        [linea("CARTERA HYPNOTIC", 10, 11.80, 100.00), linea("BILLETERA", 5, 23.60, 100.00)],
        236.00,
        tipo="factura",
        incluye_igv=False,
        op_gravadas=200.00,
        igv=36.00,
    )
    b, hechos = ajustar(boleta)
    assert [l.precio_unitario for l in b.lineas] == [10.00, 20.00] and not hay_errores(validar(b))
    assert any("precio unitario con IGV y el importe sin IGV" in a for a in avisos(b, hechos))
    plan = Registrador(OdooFalso(), sugerir_precio=False).planificar(b)
    assert [lp.costo_unitario for lp in plan.lineas] == [pytest.approx(11.80), pytest.approx(23.60)]


def test_libros_exonerados_no_pagan_el_igv_de_toda_la_factura():

    boleta = compra(
        [linea("LIBRO COLOREAR", 10, 8.00, 80.00), linea("CUADERNO A4", 10, 5.00, 50.00)],
        139.00,
        tipo="factura",
        incluye_igv=False,
        op_gravadas=50.00,
        igv=9.00,
    )
    b, _ = ajustar(boleta)
    plan = Registrador(OdooFalso(), sugerir_precio=False).planificar(b)
    assert sum(lp.costo_unitario * lp.linea.cantidad for lp in plan.lineas) == pytest.approx(139.00)


def test_media_docena():

    b, _ = ajustar(compra([linea("COLETERO SATIN", 0.5, 18.00, 9.00, unidad="DOC")], 9.00))
    assert (b.lineas[0].cantidad, b.lineas[0].precio_unitario) == (6, 1.50)
