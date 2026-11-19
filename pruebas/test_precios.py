import pytest

from boletas.precios import Margenes, calcular_margenes, margen_para, precio_sugerido, redondear


@pytest.mark.parametrize(
    "precio,esperado",
    [
        (0.33, 0.4),
        (2.04, 2.1),
        (4.99, 5.0),
        (6.2, 6.0),
        (6.3, 6.5),
        (9.6, 9.5),
        (12.4, 10.0),
        (12.5, 15.0),
        (67.44, 65.0),
        (68.0, 70.0),
    ],
)
def test_redondea_como_la_tienda(precio, esperado):
    assert redondear(precio) == pytest.approx(esperado)


@pytest.mark.parametrize(
    "costo,margen,esperado",
    [
        (22.48, 3.0, 65.0),
        (1.10, 1.85, 2.1),
        (3.20, 3.0, 9.5),
        (11.0, 1.0, 15.0),
        (0.0, 2.0, 0.0),
    ],
)
def test_precio_sugerido(costo, margen, esperado):
    assert precio_sugerido(costo, margen) == pytest.approx(esperado)


def test_nunca_sugiere_vender_casi_al_costo():
    assert precio_sugerido(11.0, 1.0) >= 11.0 * 1.1


def _filas():
    filas = [("Accesorios / Carteras", 30.0, 10.0)] * 5 + [("Accesorios / Ganchos", 5.0, 2.0)] * 2
    filas += [("Ropa", 16.0, 10.0)] * 6 + [("", 10.0, 5.0), ("Ropa", 0, 5), ("Ropa", 5, 0)]
    return filas


def test_margenes_por_categoria_con_suficientes_datos():
    margenes = calcular_margenes(_filas())
    assert margenes["Accesorios / Carteras"] == 3.0 and margenes["Ropa"] == 1.6
    assert "Accesorios / Ganchos" not in margenes and margenes["Accesorios"] == 3.0


def test_margen_usa_la_categoria_mas_cercana():
    margenes = calcular_margenes(_filas())
    assert margen_para(margenes, "Accesorios / Carteras") == 3.0
    assert margen_para(margenes, "Accesorios / Ganchos") == 3.0
    assert margen_para(margenes, "Juguetería") == margenes[""]
    assert margen_para({}, None) == 2.0


def test_margenes_se_leen_una_vez():
    lecturas = []
    margenes = Margenes(lambda: lecturas.append(1) or _filas())
    margenes.para("Ropa")
    margenes.para("Accesorios")
    assert len(lecturas) == 1
