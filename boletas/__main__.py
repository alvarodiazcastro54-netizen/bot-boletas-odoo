from __future__ import annotations

import logging
import sys

from telegram import Update
from telegram.ext import Application, PicklePersistence

from .bot import BotBoletas
from .config import Config, ConfigError
from .lectura import LectorDePrueba, crear_lector
from .odoo import Odoo, OdooError
from .registro import Registrador, RegistroError
from .tienda import Tienda


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    log = logging.getLogger("boletas")

    try:
        config = Config.desde_entorno()
        lector = LectorDePrueba(config.muestra_prueba) if config.muestra_prueba else None
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    except ValueError as exc:
        log.error("MUESTRA_PRUEBA no es una boleta válida: %s", exc)
        return 2
    if not config.usuarios_permitidos:
        log.warning(
            "TELEGRAM_USUARIOS_PERMITIDOS está vacío: nadie podrá usar el bot. "
            "Escríbele /id al bot para saber tu número."
        )

    odoo = Odoo(config.odoo_url, config.odoo_base, config.odoo_clave, empresa_id=config.empresa_id)
    registrador = Registrador(
        odoo,
        config.margen_venta,
        config.categoria_id,
        config.ruc_empresa,
        config.usa_credito_fiscal,
        config.confirmar_factura,
        config.sugerir_precio,
        Tienda(odoo, config.tienda_url),
        config.reusar_productos,
    )
    try:
        empresa = registrador.empresa()
        log.info(
            "Conectado a Odoo %s (base %s). Las compras se registran en «%s» (id %s, RUC %s).",
            config.odoo_url,
            config.odoo_base,
            empresa["name"],
            empresa["id"],
            registrador.ruc_empresa() or "sin RUC",
        )
        if config.confirmar_factura:
            log.info("Facturas de proveedor: se confirman solas si su total cuadra con la boleta.")
        elif config.modo_prueba:
            log.info("Facturas de proveedor: quedan en borrador (en modo de prueba nunca se confirman).")
        else:
            log.info("Facturas de proveedor: quedan en borrador (CONFIRMAR_FACTURA=no).")
        if config.reusar_productos:
            log.info("Productos: se reusan los que ya tienen el mismo código del mismo proveedor.")
        if not config.empresa_id:
            log.warning(
                "ODOO_EMPRESA_ID está vacío: se usa la primera empresa. Si la base tiene varias, "
                "ponlo para no registrar en la equivocada."
            )
    except (OdooError, RegistroError) as exc:
        log.error("No pude conectar con Odoo: %s", getattr(exc, "mensaje", exc))
        return 3

    if lector:
        log.warning(
            "MODO DE PRUEBA: no se usa la IA; toda foto devuelve la boleta de ejemplo (%s).", config.muestra_prueba.name
        )
    else:
        lector = crear_lector(config.ia, config.modelo, config.esfuerzo)
    bot = BotBoletas(lector, registrador, config.usuarios_permitidos)

    config.archivo_estado.parent.mkdir(parents=True, exist_ok=True)
    memoria = PicklePersistence(config.archivo_estado, store_data=None)
    app = (
        Application.builder()
        .token(config.telegram_token)
        .concurrent_updates(True)
        .persistence(memoria)
        .post_init(bot.al_iniciar)
        .build()
    )
    bot.instalar(app)
    log.info(
        "Bot en marcha (IA %s, modelo %s, esfuerzo %s). Ctrl+C para detener.",
        "de prueba" if config.modo_prueba else config.ia,
        config.modelo,
        config.esfuerzo or "sin razonamiento",
    )
    try:
        app.run_polling(allowed_updates=Update.ALL_TYPES)
    finally:
        odoo.cerrar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
