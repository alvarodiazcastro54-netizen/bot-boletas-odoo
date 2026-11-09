from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import time

import anthropic
from dotenv import load_dotenv

from boletas.lectura import Lector, LectorDePrueba, LecturaError
from boletas.modelos import Boleta
from boletas.validacion import hay_errores, validar

MUESTRAS = pathlib.Path(__file__).parent / "muestras"
CAMPOS = ["tipo", "serie_numero", "fecha_emision", "moneda", "total", "precios_incluyen_igv"]
CAMPOS_LINEA = ["codigo", "cantidad", "precio_unitario", "descuento_pct", "importe"]
PRECIOS_USD_POR_MILLON = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


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
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv()
    args = argparse.ArgumentParser(description="Mide qué tan bien lee la IA las fotos de muestras/.")
    args.add_argument("--modelo", default="claude-opus-5-5")
    args.add_argument("--esfuerzo", default="high", choices=["low", "medium", "high", "xhigh", "max"])
    args.add_argument("--sin-api", action="store_true",
                      help="no llama a la IA (usa la boleta de ejemplo); sirve para probar el script")
    opciones = args.parse_args()

    if opciones.sin_api:
        lector = LectorDePrueba(MUESTRAS / "boleta-01-hongkong.esperado.json")
        print("Sin API: toda foto devuelve la boleta de ejemplo. Esto no mide la IA.\n")
    else:
        lector = Lector(anthropic.AsyncAnthropic(max_retries=3), opciones.modelo, opciones.esfuerzo)
        print(f"Modelo {opciones.modelo}, esfuerzo {opciones.esfuerzo}\n")
    archivo_categorias = MUESTRAS / "categorias-uyay.txt"
    categorias = [c.strip() for c in archivo_categorias.read_text(encoding="utf-8").splitlines() if c.strip()] \
        if archivo_categorias.exists() else None

    fotos = sorted(p for p in MUESTRAS.iterdir() if p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"})
    evaluadas = perfectas = no_detectadas = categorias_mal = 0
    tokens_entrada = tokens_salida = 0
    inicio = time.monotonic()
    for foto in fotos:
        esperado = foto.with_suffix(".esperado.json")
        if not esperado.exists():
            print(f"— {foto.name}: sin .esperado.json, se salta")
            continue
        evaluadas += 1
        esperada = Boleta.model_validate(json.loads(esperado.read_text(encoding="utf-8")))
        try:
            leida = await lector.leer(foto.read_bytes(), categorias)
        except LecturaError as exc:
            print(f"✗ {foto.name}: {exc}")
            continue
        if isinstance(lector, Lector):
            tokens_entrada += lector.ultimo_uso[0]
            tokens_salida += lector.ultimo_uso[1]
        distintas = categorias_distintas(leida, esperada)
        categorias_mal += len(distintas)
        fallas = comparar(leida, esperada)
        if not fallas:
            perfectas += 1
            print(f"✓ {foto.name}")
        else:
            detectado = hay_errores(validar(leida))
            if not detectado:
                no_detectadas += 1
            aviso = "el bot lo habría detectado" if detectado else "EL BOT NO LO HABRÍA DETECTADO"
            print(f"✗ {foto.name} ({aviso}):")
            for f in fallas:
                print(f"    {f}")
        for c in distintas:
            print(f"    · categoría {c}")

    print(f"\nLecturas perfectas: {perfectas} de {evaluadas}")
    print(f"Errores que el bot NO habría detectado: {no_detectadas}")
    print(f"Categorías distintas a las esperadas: {categorias_mal} (no cuentan como error de lectura)")
    print(f"Tiempo: {time.monotonic() - inicio:.0f} s")
    if isinstance(lector, Lector) and evaluadas:
        precios = PRECIOS_USD_POR_MILLON.get(opciones.modelo)
        print(f"Tokens: {tokens_entrada} de entrada y {tokens_salida} de salida")
        if precios:
            costo = (tokens_entrada * precios[0] + tokens_salida * precios[1]) / 1_000_000
            print(f"Costo estimado: US$ {costo:.4f} en total, US$ {costo / evaluadas:.4f} por boleta "
                  "(precios de lista de noviembre de 2026; el costo real está en la consola de Anthropic)")
    return 0 if evaluadas and perfectas == evaluadas and no_detectadas == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
