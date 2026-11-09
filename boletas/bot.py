from __future__ import annotations

import asyncio
import logging
import uuid
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters,
)

from . import correcciones, mensajes
from .lectura import Lector, LecturaError
from .odoo import OdooError
from .registro import Registrador, RegistroError
from .validacion import hay_errores, validar

log = logging.getLogger(__name__)

LIMITE_TELEGRAM = 4000
MAX_DESCARGA = 20 * 1024 * 1024

AYUDA = (
    "Mándame la <b>foto de una boleta</b> de compra. La leo, te muestro lo que entendí "
    "y, si me confirmas, la registro en Odoo: proveedor, productos nuevos, compra, "
    "ingreso al stock y factura de proveedor en borrador con la foto.\n\n"
    "Consejos: foto de frente, con buena luz, sin tapar los totales ni el QR. "
    "Si la mandas como <i>archivo</i> llega con más calidad.\n\n"
    "/cancelar descarta la boleta que estás revisando · /categorias lista las categorías de la tienda · "
    "/id muestra tu número de usuario"
)


def partir(texto: str, limite: int = LIMITE_TELEGRAM) -> list[str]:
    partes, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > limite and actual:
            partes.append(actual)
            actual = ""
        actual = f"{actual}\n{linea}" if actual else linea
    if actual:
        partes.append(actual)
    return partes or [""]


class BotBoletas:
    def __init__(self, lector: Lector, registrador: Registrador, usuarios: frozenset[int]):
        self.lector = lector
        self.registrador = registrador
        self.usuarios = usuarios
        self.candado_odoo = asyncio.Lock()

    def autorizado(self, update: Update) -> bool:
        usuario = update.effective_user
        return usuario is not None and usuario.id in self.usuarios

    async def rechazar(self, update: Update) -> None:
        usuario = update.effective_user
        log.warning("Acceso no autorizado de %s (%s)", usuario.id if usuario else "?",
                    usuario.username if usuario else "?")
        if update.effective_message:
            await update.effective_message.reply_text(
                f"No estás autorizado para usar este bot. Tu id es {usuario.id if usuario else '?'}; "
                "pásaselo al administrador.")

    @staticmethod
    def teclado(id_boleta: str, plan, problemas) -> InlineKeyboardMarkup:
        if plan.ya_registrada:
            botones = [[InlineKeyboardButton("Cerrar", callback_data=f"can:{id_boleta}")]]
        elif hay_errores(problemas):
            botones = [[InlineKeyboardButton("✏️ Corregir", callback_data=f"cor:{id_boleta}"),
                        InlineKeyboardButton("🔄 Leer de nuevo", callback_data=f"rel:{id_boleta}")],
                       [InlineKeyboardButton("❌ Cancelar", callback_data=f"can:{id_boleta}")]]
        else:
            botones = [[InlineKeyboardButton("✅ Registrar", callback_data=f"reg:{id_boleta}")],
                       [InlineKeyboardButton("✏️ Corregir", callback_data=f"cor:{id_boleta}"),
                        InlineKeyboardButton("❌ Cancelar", callback_data=f"can:{id_boleta}")]]
        return InlineKeyboardMarkup(botones)

    async def mostrar(self, mensaje: Message, pendiente: dict, editar: bool) -> None:
        boleta = pendiente["boleta"]
        try:
            plan = await asyncio.to_thread(self.registrador.planificar, boleta)
        except OdooError as exc:
            texto = f"Leí la boleta, pero no pude consultar Odoo: {escape(exc.mensaje)}"
            await (mensaje.edit_text(texto) if editar else mensaje.reply_text(texto))
            return
        problemas = validar(boleta)
        partes = partir(mensajes.resumen(plan, problemas))
        teclado = self.teclado(pendiente["id"], plan, problemas)
        for parte in partes[:-1]:
            await mensaje.reply_text(parte, parse_mode=ParseMode.HTML)
        if editar and len(partes) == 1:
            enviado = await mensaje.edit_text(partes[-1], parse_mode=ParseMode.HTML, reply_markup=teclado)
        else:
            enviado = await mensaje.reply_text(partes[-1], parse_mode=ParseMode.HTML, reply_markup=teclado)
        pendiente["mensaje_id"] = enviado.message_id if hasattr(enviado, "message_id") else None

    async def cmd_ayuda(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.autorizado(update):
            await self.rechazar(update)
            return
        await update.effective_message.reply_text(AYUDA, parse_mode=ParseMode.HTML)

    async def cmd_id(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        await update.effective_message.reply_text(f"Tu id de Telegram es {update.effective_user.id}.")

    async def cmd_categorias(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.autorizado(update):
            await self.rechazar(update)
            return
        for parte in partir(mensajes.categorias(await self._categorias())):
            await update.effective_message.reply_text(parte, parse_mode=ParseMode.HTML)

    async def cmd_cancelar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.autorizado(update):
            await self.rechazar(update)
            return
        context.chat_data.pop("pendiente", None)
        await update.effective_message.reply_text("Listo, descarté la boleta. Mándame otra cuando quieras.")

    async def recibir_foto(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.autorizado(update):
            await self.rechazar(update)
            return
        msg = update.effective_message
        adjunto = msg.photo[-1] if msg.photo else msg.document
        if adjunto.file_size and adjunto.file_size > MAX_DESCARGA:
            await msg.reply_text("La imagen pesa más de 20 MB. Mándala como foto (comprimida).")
            return
        estado = await msg.reply_text("📄 Leyendo la boleta…")
        await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
        archivo = await adjunto.get_file()
        foto = bytes(await archivo.download_as_bytearray())
        await self.leer_y_mostrar(estado, context, foto)

    async def _categorias(self) -> list[str]:
        try:
            return await asyncio.to_thread(self.registrador.nombres_categorias)
        except OdooError as exc:
            log.warning("No pude leer las categorías: %s", exc.mensaje)
            return []

    async def leer_y_mostrar(self, estado: Message, context: ContextTypes.DEFAULT_TYPE, foto: bytes) -> None:
        try:
            boleta = await self.lector.leer(foto, await self._categorias())
        except LecturaError as exc:
            await estado.edit_text(f"❌ {escape(str(exc))}")
            return
        pendiente = {"id": uuid.uuid4().hex[:12], "boleta": boleta, "foto": foto, "ocupado": False}
        context.chat_data["pendiente"] = pendiente
        await self.mostrar(estado, pendiente, editar=True)

    async def boton(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        consulta = update.callback_query
        if not self.autorizado(update):
            await consulta.answer("No autorizado", show_alert=True)
            return
        accion, _, id_boleta = (consulta.data or "").partition(":")
        pendiente = context.chat_data.get("pendiente")
        if not pendiente or pendiente["id"] != id_boleta:
            await consulta.answer("Esa boleta ya no está activa. Manda la foto de nuevo.", show_alert=True)
            await self._quitar_botones(consulta.message)
            return
        if pendiente["ocupado"]:
            await consulta.answer("Espera, estoy trabajando en esa boleta…")
            return
        await consulta.answer()

        if accion == "can":
            context.chat_data.pop("pendiente", None)
            await self._quitar_botones(consulta.message)
            await consulta.message.reply_text("Cancelado. No se registró nada.")
        elif accion == "cor":
            await consulta.message.reply_text(correcciones.AYUDA, parse_mode=ParseMode.HTML)
        elif accion == "rel":
            await self._quitar_botones(consulta.message)
            estado = await consulta.message.reply_text("📄 Leyendo de nuevo…")
            await self.leer_y_mostrar(estado, context, pendiente["foto"])
        elif accion == "reg":
            await self.registrar(consulta.message, context, pendiente)

    async def registrar(self, mensaje: Message, context: ContextTypes.DEFAULT_TYPE, pendiente: dict) -> None:
        boleta = pendiente["boleta"]
        if hay_errores(validar(boleta)):
            await mensaje.reply_text("Todavía hay errores que corregir.")
            return
        pendiente["ocupado"] = True
        await self._quitar_botones(mensaje)
        estado = await mensaje.reply_text("⏳ Registrando en Odoo…")
        nombre_foto = f"{boleta.serie_numero or 'boleta'}.jpg"
        try:
            async with self.candado_odoo:
                resultado = await asyncio.to_thread(
                    self.registrador.registrar, boleta, pendiente["foto"], nombre_foto)
        except (RegistroError, OdooError) as exc:
            pendiente["ocupado"] = False
            texto = str(exc) if isinstance(exc, RegistroError) else exc.mensaje
            log.error("Fallo al registrar %s: %s", boleta.serie_numero, texto)
            teclado = InlineKeyboardMarkup([[
                InlineKeyboardButton("🔁 Reintentar", callback_data=f"reg:{pendiente['id']}"),
                InlineKeyboardButton("❌ Cancelar", callback_data=f"can:{pendiente['id']}"),
            ]])
            await estado.edit_text(
                f"❌ No se pudo terminar el registro: {escape(texto)}\n\n"
                "Lo que ya se hizo queda guardado; «Reintentar» sigue desde ahí sin duplicar.",
                reply_markup=teclado)
            return
        context.chat_data.pop("pendiente", None)
        await estado.edit_text(mensajes.registrado(resultado), parse_mode=ParseMode.HTML)

    async def recibir_texto(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.autorizado(update):
            await self.rechazar(update)
            return
        msg = update.effective_message
        pendiente = context.chat_data.get("pendiente")
        if not pendiente:
            await msg.reply_text("Mándame la foto de una boleta. /ayuda para más detalles.")
            return
        if pendiente["ocupado"]:
            await msg.reply_text("Espera, estoy registrando esa boleta…")
            return
        try:
            boleta, cambio = correcciones.aplicar(pendiente["boleta"], msg.text or "", await self._categorias())
        except correcciones.CorreccionInvalida as exc:
            await msg.reply_text(f"{escape(str(exc))}\n\n{correcciones.AYUDA}", parse_mode=ParseMode.HTML)
            return
        pendiente["boleta"] = boleta
        aviso = await msg.reply_text(f"✏️ {escape(cambio)}")
        await self.mostrar(aviso, pendiente, editar=False)

    @staticmethod
    async def _quitar_botones(mensaje: Message | None) -> None:
        if mensaje is None:
            return
        try:
            await mensaje.edit_reply_markup(reply_markup=None)
        except BadRequest:
            pass

    async def error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.exception("Error no controlado", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            try:
                await update.effective_message.reply_text("Ocurrió un error inesperado. Intenta de nuevo.")
            except Exception:
                pass

    def instalar(self, app: Application) -> None:
        app.add_handler(CommandHandler(["start", "ayuda", "help"], self.cmd_ayuda))
        app.add_handler(CommandHandler("id", self.cmd_id))
        app.add_handler(CommandHandler("cancelar", self.cmd_cancelar))
        app.add_handler(CommandHandler("categorias", self.cmd_categorias))
        app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, self.recibir_foto))
        app.add_handler(CallbackQueryHandler(self.boton))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.recibir_texto))
        app.add_error_handler(self.error)
