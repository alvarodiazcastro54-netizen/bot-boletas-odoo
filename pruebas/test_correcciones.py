import pytest

from boletas.correcciones import CorreccionInvalida, aplicar


def test_completar_nombre_cortado(boleta_hongkong):
    nueva, cambio = aplicar(boleta_hongkong, "3 nombre SOL SORBETE ACERO INOX 2 PAR RECTO Y OBLICUO + CEPILLO")
    assert nueva.lineas[2].descripcion.endswith("+ CEPILLO")
    assert nueva.lineas[2].nombre_cortado is False
    assert boleta_hongkong.lineas[2].nombre_cortado is True
    assert "Línea 3" in cambio


def test_numeros_con_coma_y_simbolos(boleta_hongkong):
    nueva, _ = aplicar(boleta_hongkong, "1 precio S/ 22,50")
    assert nueva.lineas[0].precio_unitario == 22.50
    nueva, _ = aplicar(nueva, "1 descuento 40%")
    assert nueva.lineas[0].descuento_pct == 40


def test_borrar_linea(boleta_hongkong):
    nueva, _ = aplicar(boleta_hongkong, "borrar 7")
    assert len(nueva.lineas) == 6


def test_campos_de_cabecera(boleta_hongkong):
    nueva, _ = aplicar(boleta_hongkong, "fecha 06/11/2026")
    assert nueva.fecha_emision == "2026-11-06"
    nueva, _ = aplicar(nueva, "ruc 20-10156742-8")
    assert nueva.proveedor.ruc == "20101567428"
    nueva, _ = aplicar(nueva, "numero b410-00170133")
    assert nueva.serie_numero == "B410-00170133"
    nueva, _ = aplicar(nueva, "total 60")
    assert nueva.total == 60


CATEGORIAS = ["Accesorios / Dijes y Llaveros", "Hogar y Manualidades / Cocina",
              "Hogar y Manualidades / Decoración", "Hogar y Manualidades / Descartables y Bolsas",
              "Cumpleaños, Eventos y Disfraces / Bolsas y Cajas de Regalo"]


def test_cambiar_categoria(boleta_hongkong):
    nueva, cambio = aplicar(boleta_hongkong, "4 categoria descartables", CATEGORIAS)
    assert nueva.lineas[3].categoria == "Hogar y Manualidades / Descartables y Bolsas"
    nueva, _ = aplicar(nueva, "7 categoría DECORACION", CATEGORIAS)
    assert nueva.lineas[6].categoria == "Hogar y Manualidades / Decoración"
    nueva, _ = aplicar(nueva, "1 cat Hogar y Manualidades / Cocina", CATEGORIAS)
    assert nueva.lineas[0].categoria == "Hogar y Manualidades / Cocina"


def test_categoria_ambigua_o_inexistente(boleta_hongkong):
    with pytest.raises(CorreccionInvalida, match="varias"):
        aplicar(boleta_hongkong, "1 categoria bolsas", CATEGORIAS)
    with pytest.raises(CorreccionInvalida, match="ninguna"):
        aplicar(boleta_hongkong, "1 categoria electrónica", CATEGORIAS)
    with pytest.raises(CorreccionInvalida):
        aplicar(boleta_hongkong, "1 categoria cocina", None)


@pytest.mark.parametrize("texto", ["hola", "9 cantidad 2", "1 color rojo", "1 cantidad dos", "fecha mañana"])
def test_correcciones_invalidas(boleta_hongkong, texto):
    with pytest.raises(CorreccionInvalida):
        aplicar(boleta_hongkong, texto)
