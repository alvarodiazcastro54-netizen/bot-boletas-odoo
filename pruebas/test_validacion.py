import datetime as dt

from boletas.validacion import hay_errores, importe_calculado, ruc_valido, total_calculado, validar

HOY = dt.date(2026, 11, 9)


def errores(problemas):
    return [p.mensaje for p in problemas if p.nivel == "error"]


def test_ruc():
    assert ruc_valido("20101567428")
    assert ruc_valido("20100047218")
    assert not ruc_valido("20101567427")
    assert not ruc_valido("2010156742")
    assert not ruc_valido("30101567428")
    assert not ruc_valido(None)


def test_boleta_real_sin_errores(boleta_hongkong):
    problemas = validar(boleta_hongkong, HOY)
    assert not hay_errores(problemas), errores(problemas)
    assert total_calculado(boleta_hongkong) == 58.30
    cortados = [p for p in problemas if "cortado" in p.mensaje]
    assert len(cortados) == 3


def test_importes_de_cada_linea(boleta_hongkong):
    for linea in boleta_hongkong.lineas:
        assert importe_calculado(linea) == linea.importe


def test_detecta_importe_mal_leido(boleta_hongkong):
    boleta_hongkong.lineas[2].importe = 15.50
    msgs = errores(validar(boleta_hongkong, HOY))
    assert any("Línea 3" in m for m in msgs)
    assert any("suman" in m for m in msgs)


def test_detecta_cantidad_mal_leida(boleta_hongkong):
    boleta_hongkong.lineas[0].cantidad = 3
    assert any("Línea 1" in m for m in errores(validar(boleta_hongkong, HOY)))


def test_fecha_futura_y_faltante(boleta_hongkong):
    boleta_hongkong.fecha_emision = "2026-12-01"
    assert any("futuro" in m for m in errores(validar(boleta_hongkong, HOY)))
    boleta_hongkong.fecha_emision = None
    assert any("fecha" in m for m in errores(validar(boleta_hongkong, HOY)))


def test_ruc_invalido(boleta_hongkong):
    boleta_hongkong.proveedor.ruc = "20101567420"
    assert any("RUC" in m for m in errores(validar(boleta_hongkong, HOY)))


def test_no_es_comprobante(boleta_hongkong):
    boleta_hongkong.es_comprobante = False
    assert hay_errores(validar(boleta_hongkong, HOY))


def test_otros_cargos_y_descuento_global(boleta_hongkong):
    boleta_hongkong.otros_cargos = 0.50
    boleta_hongkong.total = 58.80
    assert not hay_errores(validar(boleta_hongkong, HOY))
    boleta_hongkong.descuento_global = 5.00
    boleta_hongkong.total = 53.80
    assert not hay_errores(validar(boleta_hongkong, HOY))


def test_factura_con_precios_sin_igv(boleta_hongkong):
    boleta_hongkong.tipo = "factura"
    boleta_hongkong.precios_incluyen_igv = False
    boleta_hongkong.igv = None
    boleta_hongkong.total = 68.79
    assert not hay_errores(validar(boleta_hongkong, HOY))


def test_igv_que_no_cuadra_es_aviso(boleta_hongkong):
    boleta_hongkong.igv = 5.00
    problemas = validar(boleta_hongkong, HOY)
    assert not hay_errores(problemas)
    assert any("IGV" in p.mensaje for p in problemas if p.nivel == "aviso")
