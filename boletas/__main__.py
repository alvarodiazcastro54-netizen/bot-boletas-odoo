from __future__ import annotations

import logging
import sys
from pathlib import Path

import anthropic
from telegram import Update
from telegram.ext import Application

from .bot import BotBoletas
from .config import Config, ConfigError
from .lectura import Lector, LectorDePrueba
from .odoo import Odoo, OdooError
from .registro import Registrador, RegistroError


def main() -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("boletas")

    try:
        config = Config.desde_entorno()
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    if not config.usuarios_permitidos:
        log.warning("TELEGRAM_USUARIOS_PERMITIDOS está vacío: nadie podrá usar el bot. "
                    "Escríbele /id al bot para saber tu número.")

    odoo = Odoo(config.odoo_url, config.odoo_base, config.odoo_clave, empresa_id=config.empresa_id)
    registrador = Registrador(odoo, config.margen_venta, config.categoria_id, config.ruc_empresa,
                              config.usa_credito_fiscal)
    try:
        empresa = registrador.empresa()
        log.info("Conectado a Odoo %s (base %s). Las compras se registran en «%s» (id %s, RUC %s).",
                 config.odoo_url, config.odoo_base, empresa["name"], empresa["id"],
                 registrador.ruc_empresa() or "sin RUC")
        if not config.empresa_id:
            log.warning("ODOO_EMPRESA_ID está vacío: se usa la primera empresa. Si la base tiene varias, "
                        "ponlo para no registrar en la equivocada.")
    except (OdooError, RegistroError) as exc:
        log.error("No pude conectar con Odoo: %s", getattr(exc, "mensaje", exc))
        return 3

    if config.modo_prueba:
        ejemplo = Path(__file__).resolve().parents[1] / "muestras" / "boleta-01-hongkong.esperado.json"
        lector = LectorDePrueba(ejemplo)
        log.warning("MODO DE PRUEBA: no se usa la IA; toda foto devuelve la boleta de ejemplo (%s).", ejemplo.name)
    else:
        lector = Lector(anthropic.AsyncAnthropic(max_retries=3, timeout=180.0), config.modelo, config.esfuerzo)
    bot = BotBoletas(lector, registrador, config.usuarios_permitidos)

    app = Application.builder().token(config.telegram_token).concurrent_updates(True).build()
    bot.instalar(app)
    log.info("Bot en marcha (modelo %s, esfuerzo %s). Ctrl+C para detener.", config.modelo, config.esfuerzo)
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        odoo.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
