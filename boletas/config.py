from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv


class ConfigError(Exception):
    pass


def _obligatoria(nombre: str) -> str:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        raise ConfigError(f"Falta la variable {nombre} (ver .env.example).")
    return valor


def _opcional_float(nombre: str) -> float | None:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        return None
    try:
        return float(valor)
    except ValueError:
        raise ConfigError(f"{nombre} debe ser un número (por ejemplo 0.30).") from None


def _opcional_int(nombre: str) -> int | None:
    valor = os.environ.get(nombre, "").strip()
    if not valor:
        return None
    try:
        return int(valor)
    except ValueError:
        raise ConfigError(f"{nombre} debe ser un número entero.") from None


def _booleano(nombre: str, defecto: bool) -> bool:
    valor = os.environ.get(nombre, "").strip().lower()
    if not valor:
        return defecto
    if valor in {"1", "si", "sí", "true", "yes"}:
        return True
    if valor in {"0", "no", "false"}:
        return False
    raise ConfigError(f"{nombre} debe ser «si» o «no».")


@dataclass(frozen=True)
class Config:
    telegram_token: str
    usuarios_permitidos: frozenset[int]
    odoo_url: str
    odoo_base: str
    odoo_clave: str
    modelo: str
    esfuerzo: str
    margen_venta: float | None
    categoria_id: int | None
    ruc_empresa: str | None
    empresa_id: int | None
    usa_credito_fiscal: bool
    modo_prueba: bool

    @classmethod
    def desde_entorno(cls) -> "Config":
        load_dotenv()
        usuarios = set()
        for parte in os.environ.get("TELEGRAM_USUARIOS_PERMITIDOS", "").replace(";", ",").split(","):
            parte = parte.strip()
            if parte:
                try:
                    usuarios.add(int(parte))
                except ValueError:
                    raise ConfigError(f"TELEGRAM_USUARIOS_PERMITIDOS: «{parte}» no es un id numérico.") from None
        modo_prueba = _booleano("MODO_PRUEBA", False)
        if not modo_prueba:
            _obligatoria("ANTHROPIC_API_KEY")
        esfuerzo = os.environ.get("CLAUDE_ESFUERZO", "high").strip() or "high"
        if esfuerzo not in {"low", "medium", "high", "xhigh", "max"}:
            raise ConfigError("CLAUDE_ESFUERZO debe ser low, medium, high, xhigh o max.")
        return cls(
            telegram_token=_obligatoria("TELEGRAM_TOKEN"),
            usuarios_permitidos=frozenset(usuarios),
            odoo_url=_obligatoria("ODOO_URL"),
            odoo_base=_obligatoria("ODOO_BASE"),
            odoo_clave=_obligatoria("ODOO_CLAVE_API"),
            modelo=os.environ.get("CLAUDE_MODELO", "claude-opus-5-5").strip() or "claude-opus-5-5",
            esfuerzo=esfuerzo,
            margen_venta=_opcional_float("MARGEN_VENTA"),
            categoria_id=_opcional_int("ODOO_CATEGORIA_ID"),
            ruc_empresa=os.environ.get("RUC_EMPRESA", "").strip() or None,
            empresa_id=_opcional_int("ODOO_EMPRESA_ID"),
            usa_credito_fiscal=_booleano("USA_CREDITO_FISCAL", True),
            modo_prueba=modo_prueba,
        )
