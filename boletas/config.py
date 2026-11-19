from __future__ import annotations

import ipaddress
import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

RAIZ = Path(__file__).resolve().parents[1]
MUESTRA_POR_DEFECTO = RAIZ / "muestras" / "boleta-01-hongkong.esperado.json"
MODELO_CLAUDE = "claude-opus-5-5"
MODELO_OPENAI = "gpt-6-luna"
ESFUERZOS = {
    "openai": ("none", "minimal", "low", "medium", "high", "xhigh", "max"),
    "anthropic": ("low", "medium", "high", "xhigh", "max"),
}


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


def normalizar_ia(valor: str | None) -> str:
    ia = (valor or "").strip().lower() or "anthropic"
    ia = {"claude": "anthropic", "chatgpt": "openai"}.get(ia, ia)
    if ia not in ESFUERZOS:
        raise ConfigError("IA debe ser «openai» (ChatGPT) o «anthropic» (Claude).")
    return ia


def modelo_y_esfuerzo(ia: str, modelo: str | None = None, esfuerzo: str | None = None) -> tuple[str, str | None]:
    if ia == "openai":
        nombre, defecto = "OPENAI_ESFUERZO", MODELO_OPENAI
        modelo = modelo if modelo is not None else os.environ.get("OPENAI_MODELO", "")
        esfuerzo = esfuerzo if esfuerzo is not None else os.environ.get("OPENAI_ESFUERZO", "high")
        valor = esfuerzo.strip().lower() or None
    else:
        nombre, defecto = "CLAUDE_ESFUERZO", MODELO_CLAUDE
        modelo = modelo if modelo is not None else os.environ.get("CLAUDE_MODELO", "")
        esfuerzo = esfuerzo if esfuerzo is not None else os.environ.get("CLAUDE_ESFUERZO", "high")
        valor = esfuerzo.strip().lower() or "high"
    if valor is not None and valor not in ESFUERZOS[ia]:
        opciones = ", ".join(ESFUERZOS[ia])
        vacio = " o quedar vacío (modelos que no razonan: no se manda el esfuerzo)" if ia == "openai" else ""
        raise ConfigError(f"{nombre} debe ser {opciones}{vacio}.")
    return modelo.strip() or defecto, valor


def _es_local(host: str) -> bool:
    if host == "localhost" or host.endswith((".localhost", ".internal", ".local")):
        return True
    if "." not in host and ":" not in host:
        return True
    try:
        return not ipaddress.ip_address(host).is_global
    except ValueError:
        return False


def _url_odoo() -> str:
    url = _obligatoria("ODOO_URL")
    partes = urlparse(url)
    if partes.scheme not in {"http", "https"} or not partes.hostname:
        raise ConfigError("ODOO_URL debe empezar con http:// o https:// (por ejemplo http://localhost:8069).")
    if partes.scheme == "http" and not _es_local(partes.hostname) and not _booleano("ODOO_PERMITIR_HTTP", False):
        raise ConfigError(
            f"ODOO_URL usa http:// hacia {partes.hostname}, que está en internet: la clave de Odoo viajaría sin "
            "cifrar. Usa https://, o corre el bot en el mismo servidor de Odoo con http://localhost:8069. "
            "Si aceptas el riesgo, pon ODOO_PERMITIR_HTTP=si."
        )
    return url


def _muestra_prueba() -> Path:
    valor = os.environ.get("MUESTRA_PRUEBA", "").strip()
    ruta = Path(valor) if valor else MUESTRA_POR_DEFECTO
    if not ruta.is_absolute():
        ruta = RAIZ / ruta
    if not ruta.is_file():
        raise ConfigError(f"MUESTRA_PRUEBA: no existe el archivo {ruta}.")
    return ruta


def _tienda_url() -> str | None:
    url = os.environ.get("TIENDA_URL", "").strip().rstrip("/")
    if not url:
        return None
    if urlparse(url).scheme not in {"http", "https"}:
        raise ConfigError("TIENDA_URL debe empezar con http:// o https:// (por ejemplo https://tienda.ejemplo.pe).")
    return url


def _reusar_productos() -> bool:
    valor = os.environ.get("REUSAR_PRODUCTOS", "").strip().lower()
    if valor in {"", "no", "0", "false"}:
        return False
    if valor in {"codigo", "código", "si", "sí", "1", "true"}:
        return True
    raise ConfigError("REUSAR_PRODUCTOS debe ser «no» o «codigo».")


def _archivo_estado() -> Path:
    valor = os.environ.get("ARCHIVO_ESTADO", "").strip()
    ruta = Path(valor) if valor else RAIZ / "datos" / "estado.pickle"
    return ruta if ruta.is_absolute() else RAIZ / ruta


@dataclass(frozen=True)
class Config:
    telegram_token: str
    usuarios_permitidos: frozenset[int]
    odoo_url: str
    odoo_base: str
    odoo_clave: str
    ia: str
    modelo: str
    esfuerzo: str | None
    margen_venta: float | None
    categoria_id: int | None
    ruc_empresa: str | None
    empresa_id: int | None
    usa_credito_fiscal: bool
    confirmar_factura: bool
    modo_prueba: bool
    muestra_prueba: Path | None = None
    sugerir_precio: bool = True
    tienda_url: str | None = None
    archivo_estado: Path | None = None
    reusar_productos: bool = False

    @classmethod
    def desde_entorno(cls) -> Config:
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
        ia = normalizar_ia(os.environ.get("IA"))
        if not modo_prueba:
            _obligatoria("OPENAI_API_KEY" if ia == "openai" else "ANTHROPIC_API_KEY")
        modelo, esfuerzo = modelo_y_esfuerzo(ia)
        return cls(
            telegram_token=_obligatoria("TELEGRAM_TOKEN"),
            usuarios_permitidos=frozenset(usuarios),
            odoo_url=_url_odoo(),
            odoo_base=_obligatoria("ODOO_BASE"),
            odoo_clave=_obligatoria("ODOO_CLAVE_API"),
            ia=ia,
            modelo=modelo,
            esfuerzo=esfuerzo,
            margen_venta=_opcional_float("MARGEN_VENTA"),
            categoria_id=_opcional_int("ODOO_CATEGORIA_ID"),
            ruc_empresa=os.environ.get("RUC_EMPRESA", "").strip() or None,
            empresa_id=_opcional_int("ODOO_EMPRESA_ID"),
            usa_credito_fiscal=_booleano("USA_CREDITO_FISCAL", False),
            confirmar_factura=_booleano("CONFIRMAR_FACTURA", False) and not modo_prueba,
            modo_prueba=modo_prueba,
            muestra_prueba=_muestra_prueba() if modo_prueba else None,
            sugerir_precio=_booleano("SUGERIR_PRECIO", True),
            tienda_url=_tienda_url(),
            archivo_estado=_archivo_estado(),
            reusar_productos=_reusar_productos(),
        )
