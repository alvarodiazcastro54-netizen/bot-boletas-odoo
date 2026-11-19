import json
import pathlib

import pytest

from boletas.modelos import Boleta

RAIZ = pathlib.Path(__file__).resolve().parents[1]
MUESTRAS = RAIZ / "muestras"


@pytest.fixture
def boleta_hongkong() -> Boleta:
    datos = json.loads((MUESTRAS / "boleta-01-hongkong.esperado.json").read_text(encoding="utf-8"))
    return Boleta.model_validate(datos)


@pytest.fixture
def foto_hongkong() -> bytes:
    return (MUESTRAS / "boleta-01-hongkong.jpeg").read_bytes()


LINEAS_LUMA = [
    ("CA-2026", "[CA-2026] CALENDARIO DE ADVIENTO 2026 LUMA", 6, 70.00, 420.00),
    ("PK-100", "[PK-100] PACK EMPRENDEDOR 100", 1, 100.00, 100.00),
    ("NL-11", "[NL-11] LUMALOCURA X11productos", 4, 20.00, 80.00),
    ("BEAUTY-BOX", "[BEAUTY-BOX] BEAUTY BOX 13productos", 3, 35.00, 105.00),
    ("AGS-134", "[AGS-134] TORNASOL SOMBRAS JARUSA X 24", 1, 4.33, 104.00),
    ("AGS-290", "[AGS-290] LABIAL TENTACIÓN 2EN1", 1, 3.00, 72.00),
    ("MRB-4770", "[MRB-4770] BALSAMO HELADITO X 24", 1, 1.66, 39.90),
    ("MSB-120", "[MSB-120] BASE BLISTER LUMA X 24", 1, 1.39, 33.25),
    ("MSD-100", "[MSD-100] DELINEADOR LUMA X 24", 1, 2.61, 62.70),
    ("MS-27370", "[MS-27370] SET DE VINCHA Y MUÑEQUERA X12", 1, 3.33, 39.90),
    ("MS-410", "[MS-410] UÑAS BONITA LUMA X 12", 1, 2.85, 34.20),
    ("MS-421", "[MS-421] RIZADOR MACARRON X12", 1, 1.43, 17.10),
    ("PESTA-02", "[PESTA-02] PESTAÑAS YOU SECRET x10und", 1, 1.20, 12.00),
    ("MS-26940", "[MS-26940] NECESER TRICOLOR", 1, 9.03, 108.30),
    ("SE-500#8", "[SE-500#8] SET 4 ISLAND MINI PERFUME 20ml BOX4", 1, 12.00, 144.00),
    ("M046ABC", "[M046ABC] MASCARILLA FACIAL DR LA ROSA x10 SURTIDO", 12, 1.00, 120.00),
    (None, "CARRETA", 1, 10.00, 10.00),
    ("MS-27360", "[MS-27360] BORLA CUTE DOG X12", 1, 1.43, 17.10),
]
UNIDADES_LUMA = [6, 1, 4, 3, 24, 24, 24, 24, 24, 12, 12, 12, 10, 12, 12, 120, 1, 12]


@pytest.fixture
def mayorista() -> Boleta:
    return Boleta.model_validate(
        {
            "es_comprobante": True,
            "legible": True,
            "tipo": "nota_de_venta",
            "serie_numero": "S10234",
            "fecha_emision": "2026-11-16",
            "moneda": "PEN",
            "proveedor": {
                "ruc": "20612345679",
                "razon_social": "DISTRIBUIDORA LUMA S.A.C.",
                "nombre_comercial": "Luma",
                "direccion": None,
            },
            "cliente_documento": None,
            "precios_incluyen_igv": False,
            "lineas": [
                {
                    "codigo": c,
                    "descripcion": d,
                    "nombre_cortado": False,
                    "cantidad": q,
                    "unidad": None,
                    "precio_unitario": p,
                    "descuento_pct": 0,
                    "descuento_monto": None,
                    "importe": i,
                    "categoria": None,
                }
                for c, d, q, p, i in LINEAS_LUMA
            ],
            "descuento_global": None,
            "otros_cargos": None,
            "op_gravadas": None,
            "igv": None,
            "total": 1519.45,
            "observaciones": None,
        }
    )
