from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

import anthropic
from dotenv import load_dotenv

from boletas.lectura import Lector, LecturaError
from boletas.modelos import Boleta
from boletas.validacion import hay_errores, validar

MUESTRAS = pathlib.Path(__file__).parent / "muestras"
CAMPOS = ["tipo", "serie_numero", "fecha_emision", "moneda", "total", "precios_incluyen_igv"]
CAMPOS_LINEA = ["codigo", "cantidad", "precio_unitario", "descuento_pct", "importe"]


def comparar(leida: Boleta, esperada: Boleta) -> list[str]:
    fallas = []
    for campo in CAMPOS:
        if getattr(leida, campo) != getattr(esperada, campo):
            fallas.append(f"{campo}: leyó {getattr(leida, campo)!r}, era {getattr(esperada, campo)!r}")
    if leida.proveedor.ruc != esperada.proveedor.ruc:
        fallas.append(f"ruc: leyó {leida.proveedor.ruc!r}, era {esperada.proveedor.ruc!r}")
    if len(leida.lineas) != len(esperada.lineas):
        fallas.append(f"líneas: leyó {len(leida.lineas)}, eran {len(esperada.lineas)}")
    for n, (a, b) in enumerate(zip(leida.lineas, esperada.lineas), start=1):
        for campo in CAMPOS_LINEA:
            if getattr(a, campo) != getattr(b, campo):
                fallas.append(f"línea {n} {campo}: leyó {getattr(a, campo)!r}, era {getattr(b, campo)!r}")
        if a.descripcion != b.descripcion:
            fallas.append(f"línea {n} descripción: «{a.descripcion}» ≠ «{b.descripcion}»")
    return fallas


def categorias_distintas(leida: Boleta, esperada: Boleta) -> list[str]:
    return [f"línea {n}: eligió «{a.categoria}», se esperaba «{b.categoria}»"
            for n, (a, b) in enumerate(zip(leida.lineas, esperada.lineas), start=1)
            if b.categoria and a.categoria != b.categoria]


async def main() -> int:
    load_dotenv()
    args = argparse.ArgumentParser()
    args.add_argument("--modelo", default="claude-opus-5-5")
    args.add_argument("--esfuerzo", default="high")
    opciones = args.parse_args()
    lector = Lector(anthropic.AsyncAnthropic(max_retries=3), opciones.modelo, opciones.esfuerzo)
    archivo_categorias = MUESTRAS / "categorias-uyay.txt"
    categorias = [c.strip() for c in archivo_categorias.read_text(encoding="utf-8").splitlines() if c.strip()] \
        if archivo_categorias.exists() else None

    fotos = sorted(p for p in MUESTRAS.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    perfectas = 0
    for foto in fotos:
        esperado = foto.with_suffix(".esperado.json")
        if not esperado.exists():
            print(f"— {foto.name}: sin .esperado.json, se salta")
            continue
        esperada = Boleta.model_validate(json.loads(esperado.read_text(encoding="utf-8")))
        try:
            leida = await lector.leer(foto.read_bytes(), categorias)
        except LecturaError as exc:
            print(f"✗ {foto.name}: {exc}")
            continue
        for c in categorias_distintas(leida, esperada):
            print(f"  · {foto.name} categoría {c}")
        fallas = comparar(leida, esperada)
        detectado = hay_errores(validar(leida))
        if not fallas:
            perfectas += 1
            print(f"✓ {foto.name}")
        else:
            aviso = "el bot lo habría detectado" if detectado else "EL BOT NO LO HABRÍA DETECTADO"
            print(f"✗ {foto.name} ({aviso}):")
            for f in fallas:
                print(f"    {f}")
    print(f"\n{perfectas}/{len(fotos)} lecturas perfectas")
    return 0 if perfectas == len(fotos) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
