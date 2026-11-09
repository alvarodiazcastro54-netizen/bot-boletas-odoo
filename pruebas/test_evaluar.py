import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from evaluar_lectura import categorias_distintas, comparar


def test_lectura_identica(boleta_hongkong):
    assert comparar(boleta_hongkong, boleta_hongkong.model_copy(deep=True)) == []


def test_detecta_cada_diferencia(boleta_hongkong):
    leida = boleta_hongkong.model_copy(deep=True)
    leida.total = 58.80
    leida.proveedor.ruc = "20101567420"
    leida.lineas[2].cantidad = 2
    leida.lineas[6].descripcion = "LLAVERO METALICO"
    fallas = comparar(leida, boleta_hongkong)
    assert any(f.startswith("total") for f in fallas)
    assert any(f.startswith("ruc") for f in fallas)
    assert any(f.startswith("línea 3 cantidad") for f in fallas)
    assert any(f.startswith("línea 7 descripción") for f in fallas)


def test_lineas_de_mas_o_de_menos(boleta_hongkong):
    leida = boleta_hongkong.model_copy(deep=True)
    leida.lineas.pop()
    assert any(f.startswith("líneas") for f in comparar(leida, boleta_hongkong))


def test_categoria_aparte(boleta_hongkong):
    leida = boleta_hongkong.model_copy(deep=True)
    leida.lineas[3].categoria = "Hogar y Manualidades / Organización"
    assert comparar(leida, boleta_hongkong) == []
    assert len(categorias_distintas(leida, boleta_hongkong)) == 1
