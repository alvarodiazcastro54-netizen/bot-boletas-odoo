import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from boletas.config import MODELO_CLAUDE, ConfigError
from evaluar_lectura import categorias_distintas, comparar, elegir_lectura


@pytest.fixture
def entorno(monkeypatch):
    for nombre, valor in {
        "IA": "openai",
        "OPENAI_MODELO": "gpt-6.1-sol",
        "OPENAI_ESFUERZO": "medium",
        "CLAUDE_MODELO": "",
        "CLAUDE_ESFUERZO": "",
    }.items():
        monkeypatch.setenv(nombre, valor)


def test_usa_el_modelo_y_el_esfuerzo_del_env(entorno):
    assert elegir_lectura(None, None, None) == ("openai", "gpt-6.1-sol", "medium")


def test_las_opciones_ganan_al_env(entorno):
    assert elegir_lectura("anthropic", None, None) == ("anthropic", MODELO_CLAUDE, "high")
    assert elegir_lectura(None, "gpt-6-luna", "") == ("openai", "gpt-6-luna", None)


def test_esfuerzo_que_esa_ia_no_tiene(entorno):
    with pytest.raises(ConfigError, match="CLAUDE_ESFUERZO"):
        elegir_lectura("anthropic", None, "minimal")


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
