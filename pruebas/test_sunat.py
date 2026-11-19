import pytest

from boletas.sunat import DatosQR, NoEsCompra, XmlInvalido, aplicar_qr, interpretar_qr, serie_normalizada, xml_a_boleta
from boletas.validacion import hay_errores, validar

NS = (
    'xmlns="urn:oasis:names:specification:ubl:schema:xsd:{raiz}-2" '
    'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
    'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2"'
)


def _impuesto(base, monto, esquema="1000"):
    return (
        f"<cac:TaxTotal><cbc:TaxAmount>{monto}</cbc:TaxAmount><cac:TaxSubtotal>"
        f"<cbc:TaxableAmount>{base}</cbc:TaxableAmount><cbc:TaxAmount>{monto}</cbc:TaxAmount>"
        f"<cac:TaxCategory><cac:TaxScheme><cbc:ID>{esquema}</cbc:ID></cac:TaxScheme></cac:TaxCategory>"
        "</cac:TaxSubtotal></cac:TaxTotal>"
    )


def _linea(n, cantidad, valor, igv, precio_con_igv, descripcion, codigo):
    return (
        f'<cac:InvoiceLine><cbc:ID>{n}</cbc:ID><cbc:InvoicedQuantity unitCode="NIU">{cantidad}'
        f"</cbc:InvoicedQuantity><cbc:LineExtensionAmount>{valor}</cbc:LineExtensionAmount>"
        f"<cac:PricingReference><cac:AlternativeConditionPrice><cbc:PriceAmount>{precio_con_igv}"
        "</cbc:PriceAmount><cbc:PriceTypeCode>01</cbc:PriceTypeCode></cac:AlternativeConditionPrice>"
        f"</cac:PricingReference>{_impuesto(valor, igv)}<cac:Item><cbc:Description><![CDATA[{descripcion}]]>"
        f"</cbc:Description><cac:SellersItemIdentification><cbc:ID>{codigo}</cbc:ID>"
        "</cac:SellersItemIdentification></cac:Item></cac:InvoiceLine>"
    )


def factura_xml(tipo="01", raiz="Invoice") -> bytes:
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<{raiz} {NS.format(raiz=raiz)}>
  <cbc:UBLVersionID>2.1</cbc:UBLVersionID>
  <cbc:ID>F001-123</cbc:ID>
  <cbc:IssueDate>2026-11-10</cbc:IssueDate>
  <cbc:InvoiceTypeCode listID="0101">{tipo}</cbc:InvoiceTypeCode>
  <cbc:DocumentCurrencyCode>PEN</cbc:DocumentCurrencyCode>
  <cac:AccountingSupplierParty><cac:Party>
    <cac:PartyIdentification><cbc:ID schemeID="6">20101567428</cbc:ID></cac:PartyIdentification>
    <cac:PartyName><cbc:Name>HONG KONG MARKET</cbc:Name></cac:PartyName>
    <cac:PartyLegalEntity><cbc:RegistrationName>SB TRADING S.R.LTDA</cbc:RegistrationName>
      <cac:RegistrationAddress><cac:AddressLine><cbc:Line>JR. ANDAHUAYLAS 123</cbc:Line></cac:AddressLine>
      </cac:RegistrationAddress></cac:PartyLegalEntity>
  </cac:Party></cac:AccountingSupplierParty>
  <cac:AccountingCustomerParty><cac:Party>
    <cac:PartyIdentification><cbc:ID schemeID="6">20100047218</cbc:ID></cac:PartyIdentification>
  </cac:Party></cac:AccountingCustomerParty>
  {_impuesto("59.00", "10.62")}{_impuesto("0", "0.50", "7152")}
  <cac:LegalMonetaryTotal><cbc:PayableAmount currencyID="PEN">70.12</cbc:PayableAmount></cac:LegalMonetaryTotal>
  {_linea(1, 2, "50.00", "9.00", "29.50", "TAZA MUG CERAMICA 350ML", "T-350")}
  {_linea(2, 1, "9.00", "1.62", "11.80", "LLAVERO METAL", "LL-1")}
</{raiz}>""".encode()


def test_factura_electronica_completa():
    b = xml_a_boleta(factura_xml())
    assert (b.tipo, b.serie_numero, b.fecha_emision, b.moneda) == ("factura", "F001-123", "2026-11-10", "PEN")
    assert b.proveedor.ruc == "20101567428" and b.proveedor.razon_social == "SB TRADING S.R.LTDA"
    assert b.proveedor.nombre_comercial == "HONG KONG MARKET" and b.cliente_documento == "20100047218"
    taza, llavero = b.lineas
    assert (taza.codigo, taza.cantidad, taza.precio_unitario, taza.importe) == ("T-350", 2, 29.5, 59.0)
    assert llavero.importe == 10.62 and llavero.descuento_monto == pytest.approx(1.18)
    assert b.total == 70.12 and b.otros_cargos == pytest.approx(0.5) and b.igv == pytest.approx(10.62)
    assert b.precios_incluyen_igv
    assert not hay_errores(validar(b))


@pytest.mark.parametrize("tipo", ["07", "08"])
def test_nota_de_credito_o_debito_por_su_tipo(tipo):
    with pytest.raises(NoEsCompra):
        xml_a_boleta(factura_xml(tipo))


@pytest.mark.parametrize("raiz,texto", [("CreditNote", "nota de crédito"), ("DespatchAdvice", "guía de remisión")])
def test_documentos_que_no_son_compra(raiz, texto):
    with pytest.raises(NoEsCompra, match=texto):
        xml_a_boleta(factura_xml(raiz=raiz))


@pytest.mark.parametrize(
    "datos",
    [b"<no-cierra", b"<?xml version='1.0'?><Otra/>", b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "b">]><Invoice/>'],
)
def test_xml_que_no_sirve(datos):
    with pytest.raises(XmlInvalido):
        xml_a_boleta(datos)


def test_qr_de_sunat_con_barras():
    datos = interpretar_qr("20101567428|03|B410|170132|8.89|58.30|05/11/2026|1|45678912|resumen|")
    assert datos == DatosQR("20101567428", "03", "B410-00170132", 58.30, "2026-11-05")


def test_qr_con_enlace():
    datos = interpretar_qr(
        "https://e-consulta.pe/v?ruc=20101567428&tipo=01&serie=F001&numero=123&total=70.12&fecha=2026-11-10"
    )
    assert datos == DatosQR("20101567428", "01", "F001-00000123", 70.12, "2026-11-10")


@pytest.mark.parametrize("texto", ["hola", "123|03|B1|1", "https://ejemplo.pe/?q=1"])
def test_qr_que_no_es_de_un_comprobante(texto):
    assert interpretar_qr(texto) is None


def test_el_qr_corrige_lo_que_la_ia_leyo_mal(boleta_hongkong):
    boleta_hongkong.total = 58.0
    boleta_hongkong.proveedor.ruc = "20101567423"
    datos = DatosQR("20101567428", "03", "B410-00170132", 58.30, "2026-11-05")
    corregida, cambios = aplicar_qr(boleta_hongkong, datos)
    assert corregida.total == 58.30 and corregida.proveedor.ruc == "20101567428"
    assert cambios == ["RUC 20101567428 (se leyó 20101567423)", "total 58.30 (se leyó 58.00)"]


def test_qr_que_coincide_no_cambia_nada(boleta_hongkong):
    datos = DatosQR("20101567428", "03", "B410-00170132", 58.30, "2026-11-05")
    assert aplicar_qr(boleta_hongkong, datos)[1] == []


def test_qr_de_una_nota_de_credito(boleta_hongkong):
    with pytest.raises(NoEsCompra):
        aplicar_qr(boleta_hongkong, DatosQR("20101567428", "07", "BC01-00000001", 10.0, "2026-11-05"))


@pytest.mark.parametrize(
    "valor,esperado",
    [("B410-170132", "B410-00170132"), ("b001-00000123", "B001-00000123"), ("001-ABC", "001-ABC"), (None, None)],
)
def test_serie_normalizada(valor, esperado):
    assert serie_normalizada(valor) == esperado
