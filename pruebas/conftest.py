import json
import pathlib
import sys

import pytest

RAIZ = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from boletas.modelos import Boleta

MUESTRAS = RAIZ / "muestras"


@pytest.fixture
def boleta_hongkong() -> Boleta:
    datos = json.loads((MUESTRAS / "boleta-01-hongkong.esperado.json").read_text(encoding="utf-8"))
    return Boleta.model_validate(datos)


@pytest.fixture
def foto_hongkong() -> bytes:
    return (MUESTRAS / "boleta-01-hongkong.jpeg").read_bytes()
