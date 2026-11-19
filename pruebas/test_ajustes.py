import datetime as dt

import pytest

from boletas import correcciones, mensajes
from boletas.ajustes import ajustar, avisos, marcas_de_paquete, por_unidad
from boletas.modelos import Boleta, Linea
from boletas.registro import Registrador, es_nota
from boletas.validacion import ruc_valido, validar

from conftest import UNIDADES_LUMA as UNIDADES
from test_archivos import _pdf
from test_bot import OdooFalso
from test_bot_archivos import Mensaje, _bot, _mandar

HOY = dt.date(2026, 11, 19)


def linea(descripcion, cantidad, precio, importe, unidad=None, descuento=0.0, monto=None, codigo=None):
    return Linea(
        codigo=codigo,
        descripcion=descripcion,
        nombre_cortado=False,
        cantidad=cantidad,
        unidad=unidad,
        precio_unitario=precio,
        descuento_pct=descuento,
        descuento_monto=monto,
        importe=importe,
        categoria=None,
    )


def compra(lineas, total, tipo="boleta", incluye_igv=True, **extra) -> Boleta:
    datos = {
        "es_comprobante": True,
        "legible": True,
        "tipo": tipo,
        "serie_numero": "B001-00000123",
        "fecha_emision": "2026-11-16",
        "moneda": "PEN",
        "proveedor": {
            "ruc": "20612345679",
            "razon_social": "DISTRIBUIDORA EJEMPLO S.A.C.",
            "nombre_comercial": None,
            "direccion": None,
        },
        "cliente_documento": None,
        "precios_incluyen_igv": incluye_igv,
        "lineas": [l.model_dump() for l in lineas],
        "descuento_global": None,
        "otros_cargos": None,
        "op_gravadas": None,
        "igv": None,
        "total": total,
        "observaciones": None,
    }
    datos.update(extra)
    return Boleta.model_validate(datos)


def errores(boleta):
    return [p.mensaje for p in validar(boleta, HOY) if p.nivel == "error"]


def test_la_lectura_del_bot_tenia_errores(mayorista):
    assert ruc_valido(mayorista.proveedor.ruc)
    assert any(m.startswith("Línea 5: 1 × 4.33 da 4.33") for m in errores(mayorista))
    assert any("suman" in m for m in errores(mayorista))


def test_cuenta_por_unidad_lo_que_venia_por_paquete(mayorista):
    b, hechos = ajustar(mayorista)
    assert [l.cantidad for l in b.lineas] == UNIDADES
    assert [round(l.cantidad * l.precio_unitario, 2) for l in b.lineas] == [l.importe for l in mayorista.lineas]
    assert len(hechos.por_unidad) == 13
    assert errores(b) == []


def test_el_costo_es_lo_que_se_pago_por_unidad(mayorista):
    b, hechos = ajustar(mayorista)
    assert hechos.igv_incluido and b.precios_incluyen_igv
    plan = Registrador(OdooFalso(), sugerir_precio=False).planificar(b)
    costos = [lp.costo_unitario for lp in plan.lineas]
    assert costos[0] == 70.00 and costos[4] == pytest.approx(104 / 24) and costos[15] == 1.00
    assert sum(lp.costo_unitario * lp.linea.cantidad for lp in plan.lineas) == pytest.approx(1519.45)


def test_el_aviso_dice_que_lineas_y_como_volver_al_paquete(mayorista):
    b, hechos = ajustar(mayorista)
    texto = " ".join(avisos(b, hechos))
    assert "Las líneas 5 a 16 y 18 venían por paquete" in texto
    assert "1 paquete de 24 → 24 unidades de S/ 4.33" in texto
    assert "«5 cantidad 1» y «5 precio 104»" in texto
    assert "Las líneas 3 y 4 parecen venir por paquete o caja" in texto
    assert "el IGV está incluido" in texto
    resumen = mensajes.resumen(Registrador(OdooFalso()).planificar(b), validar(b, HOY), "pdf", None, avisos(b, hechos))
    assert "❌" not in resumen and "venían por paquete" in resumen and "24 × 4.33 = 104.00" in resumen


def test_si_se_vende_por_paquete_se_puede_volver_atras(mayorista):
    b, hechos = ajustar(mayorista)
    b, _ = correcciones.aplicar(b, "5 cantidad 1")
    b, _ = correcciones.aplicar(b, "5 precio 104")
    assert errores(b) == []
    assert any("Las líneas 6 a 16 y 18" in a for a in avisos(b, hechos))


def test_la_carreta_pasa_a_otros_cargos(mayorista):
    b, hechos = ajustar(mayorista)
    b, cambio = correcciones.aplicar(b, "cargo 17")
    assert cambio.startswith("La línea 17 (CARRETA) pasa a otros cargos")
    assert b.otros_cargos == 10.00 and len(b.lineas) == 17
    assert errores(b) == []
    assert any("Las líneas 5 a 17 venían" in a for a in avisos(b, hechos))


def test_docena_con_precio_de_la_docena():
    b, hechos = ajustar(
        compra(
            [
                linea("ARETES ACERO QUIRURGICO", 2, 36.00, 72.00, unidad="DOC"),
                linea("COLETEROS SATIN", 1, 18.00, 18.00, unidad="CJA X12"),
            ],
            90.00,
        )
    )
    assert [(l.cantidad, l.precio_unitario) for l in b.lineas] == [(24, 3.00), (12, 1.50)]
    assert errores(b) == []
    texto = " ".join(avisos(b, hechos))
    assert "2 paquetes de 12 → 24 unidades de S/ 3.00" in texto and "«1 cantidad 2» y «1 precio 36»" in texto


def test_caja_con_las_unidades_en_la_descripcion():
    b, _ = ajustar(compra([linea("COLETEROS SATIN CAJA X 12", 1, 18.00, 18.00, unidad="CJA")], 18.00))
    assert (b.lineas[0].cantidad, b.lineas[0].precio_unitario) == (12, 1.50)


def test_marca_sin_unidad_de_envase_solo_sugiere():
    b, hechos = ajustar(compra([linea("ARETES ACERO X12", 2, 36.00, 72.00)], 72.00))
    assert (b.lineas[0].cantidad, b.lineas[0].precio_unitario) == (2, 36.00)
    assert any("«1 paquete 12»" in a for a in avisos(b, hechos))


def test_cantidad_en_unidades_y_precio_de_la_docena():
    b, hechos = ajustar(compra([linea("LAPICERO GEL X12", 24, 6.00, 12.00, unidad="UND")], 12.00))
    assert (b.lineas[0].cantidad, b.lineas[0].precio_unitario) == (24, 0.50)
    assert errores(b) == []
    assert any("24 unidades a S/ 6.00 el paquete de 12 → S/ 0.50 cada una" in a for a in avisos(b, hechos))


def test_paquete_con_descuento():
    b, _ = ajustar(compra([linea("TORNASOL SOMBRAS JARUSA X 24", 1, 4.33, 93.60, descuento=10)], 93.60))
    assert b.lineas[0].cantidad == 24 and b.lineas[0].descuento_pct == 10
    assert errores(b) == []


@pytest.mark.parametrize(
    "descripcion,precio,importe,unidades",
    [
        ("SET BROCHAS X 7", 5.00, 35.00, 7),
        ("LIGAS COLORES X 32", 0.25, 8.00, 32),
        ("PESTAÑAS YOU SECRET x10und", 1.20, 12.00, 10),
    ],
)
def test_paquetes_de_tamano_raro_si_la_descripcion_lo_dice(descripcion, precio, importe, unidades):
    b, _ = ajustar(compra([linea(descripcion, 1, precio, importe)], importe))
    assert b.lineas[0].cantidad == unidades and errores(b) == []


@pytest.mark.parametrize(
    "cantidad,precio,importe",
    [
        (1, 4.37, 104.00),
        (1, 7.00, 77.00),
        (2, 5.00, 15.00),
        (1.5, 4.00, 24.00),
        (1, 3.00, 33.00),
    ],
)
def test_no_inventa_paquetes(cantidad, precio, importe):
    assert por_unidad(linea("PRODUCTO CUALQUIERA", cantidad, precio, importe)) is None


@pytest.mark.parametrize(
    "descripcion,marcas",
    [
        ("TOALLA 70 X 140", set()),
        ("CUADERNO A4 X 100 HOJAS", set()),
        ("PERFUME X 100ML", set()),
        ("LUMALOCURA X11productos", {11}),
        ("BEAUTY BOX 13productos", {13}),
        ("COLETEROS DOCENA", {12}),
        ("RIZADOR MACARRON X12", {12}),
    ],
)
def test_marcas_de_paquete(descripcion, marcas):
    assert marcas_de_paquete(linea(descripcion, 1, 1, 1)) == marcas


def test_descuento_impreso_como_linea_negativa():
    b, hechos = ajustar(
        compra([linea("CARTERA HYPNOTIC LAURA", 3, 49.90, 149.70), linea("DESCUENTO", 1, -15.00, -15.00)], 134.70)
    )
    assert len(b.lineas) == 1 and b.descuento_global == 15.00 and errores(b) == []
    assert any("«DESCUENTO» (−15.00)" in a for a in avisos(b, hechos))
    plan = Registrador(OdooFalso(), sugerir_precio=False).planificar(b)
    assert plan.lineas[0].costo_unitario == pytest.approx(134.70 / 3)


def test_percepcion_del_2_por_ciento():
    b, hechos = ajustar(
        compra(
            [linea("SHAMPOO SACHET X 24", 50, 24.00, 1200.00), linea("ACONDICIONADOR X 24", 20, 24.00, 480.00)],
            1713.60,
            tipo="factura",
        )
    )
    assert b.otros_cargos == 33.60 and hechos.percepcion == (33.60, 0.02) and errores(b) == []
    assert any("percepción del 2 %" in a for a in avisos(b, hechos))


def test_bonificacion_entra_con_costo_cero():
    b, hechos = ajustar(
        compra([linea("POLVO COMPACTO", 12, 8.00, 96.00), linea("POLVO COMPACTO (BONIFICACION)", 1, 8.00, 0.00)], 96.00)
    )
    assert b.lineas[1].descuento_pct == 100 and errores(b) == []
    assert any("La línea 2 es una bonificación" in a for a in avisos(b, hechos))
    plan = Registrador(OdooFalso(), sugerir_precio=False).planificar(b)
    assert plan.lineas[1].costo_unitario == 0


def test_factura_con_el_subtotal_leido_como_total_sigue_siendo_error():
    b, hechos = ajustar(
        compra([linea("PERFUME ISLAND 100ML", 6, 25.42, 152.52)], 152.52, tipo="factura", incluye_igv=False)
    )
    assert not b.precios_incluyen_igv and not hechos.igv_incluido
    (mensaje,) = errores(b)
    assert "suman 179.97" in mensaje and "«igv incluido»" in mensaje
    b, _ = correcciones.aplicar(b, "igv incluido")
    assert errores(b) == []


def test_factura_con_el_igv_impreso_que_ya_cuadra():
    b, hechos = ajustar(
        compra(
            [linea("PERFUME ISLAND 100ML", 6, 25.42, 152.52)],
            152.52,
            tipo="factura",
            incluye_igv=False,
            op_gravadas=129.25,
            igv=23.27,
        )
    )
    assert b.precios_incluyen_igv and hechos.igv_incluido


def test_no_toca_una_factura_con_precios_sin_igv(boleta_hongkong):
    boleta_hongkong.tipo = "factura"
    boleta_hongkong.precios_incluyen_igv = False
    boleta_hongkong.total = 68.79
    b, hechos = ajustar(boleta_hongkong)
    assert not b.precios_incluyen_igv and not hechos.igv_incluido and not hechos.por_unidad
    assert not any("IGV" in a for a in avisos(b, hechos))


def test_boleta_que_ya_cuadra_queda_igual(boleta_hongkong):
    b, hechos = ajustar(boleta_hongkong)
    assert b == boleta_hongkong and not hechos.por_unidad and not hechos.igv_incluido


@pytest.mark.parametrize(
    "texto,campo,valor",
    [
        ("descuento total 15", "descuento_global", 15.0),
        ("descuento global 7,50", "descuento_global", 7.5),
        ("otros 33.60", "otros_cargos", 33.6),
        ("percepción 20", "otros_cargos", 20.0),
        ("igv incluido", "precios_incluyen_igv", True),
        ("igv aparte", "precios_incluyen_igv", False),
    ],
)
def test_correcciones_de_la_compra(boleta_hongkong, texto, campo, valor):
    b, _ = correcciones.aplicar(boleta_hongkong, texto)
    assert getattr(b, campo) == valor


@pytest.mark.parametrize("texto", ["1 paquete 12", "1 x 12", "1 caja de 12"])
def test_correccion_por_paquete(boleta_hongkong, texto):
    antes = boleta_hongkong.lineas[0]
    b, cambio = correcciones.aplicar(boleta_hongkong, texto)
    assert b.lineas[0].cantidad == antes.cantidad * 12
    assert b.lineas[0].precio_unitario == pytest.approx(antes.precio_unitario / 12)
    assert errores(b) == [] and "paquetes de 12" in cambio


def test_paquete_de_una_unidad_no_vale(boleta_hongkong):
    with pytest.raises(correcciones.CorreccionInvalida):
        correcciones.aplicar(boleta_hongkong, "1 paquete 1")


def test_pista_cuando_falta_un_descuento(boleta_hongkong):
    boleta_hongkong.total = 50.00
    assert any("«descuento total 8.30»" in m for m in errores(boleta_hongkong))
    boleta_hongkong.total = 60.00
    assert any("«otros 1.70»" in m for m in errores(boleta_hongkong))


def test_el_bot_ajusta_apenas_lee_el_pdf(mayorista):

    bot = _bot(mayorista)
    msg = Mensaje(_pdf())
    chat_data = _mandar(bot, msg)
    boleta = chat_data["pendiente"]["boleta"]
    assert [l.cantidad for l in boleta.lineas] == UNIDADES
    texto = "\n".join(msg.respuestas)
    assert "venían por paquete" in texto and "Hay que corregir" not in texto


def test_unidad_box_con_las_piezas():
    b, _ = ajustar(compra([linea("TORNASOL SOMBRAS JARUSA", 1, 104.00, 104.00, unidad="BOX 24")], 104.00))
    assert b.lineas[0].cantidad == 24 and b.lineas[0].precio_unitario == pytest.approx(104 / 24)


def test_cotizacion_avisa_que_no_prueba_la_compra(mayorista):

    mayorista.tipo = "cotizacion"
    b, _ = ajustar(mayorista)
    avisos_ = [p.mensaje for p in validar(b, HOY) if p.nivel == "aviso"]
    assert any("Es una cotización" in a for a in avisos_) and errores(b) == [] and es_nota(b)
    plan = Registrador(OdooFalso()).planificar(b)
    assert any(a.startswith("Cotización: no es un comprobante de SUNAT") for a in plan.avisos)
    assert "🧾 <b>Cotización S10234</b>" in mensajes.resumen(plan, validar(b, HOY), "pdf")
    b, _ = correcciones.aplicar(b, "tipo presupuesto")
    assert b.tipo == "cotizacion"
