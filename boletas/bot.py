from __future__ import annotations

import asyncio
import contextlib
import logging
import time
import uuid
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatAction, ParseMode
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from . import ajustes, archivos, correcciones, mensajes
from .archivos import MAX_PAGINAS, Documento, tipo_de_archivo
from .fotos import FOTO_DE_PRODUCTO, SOLO_PRECIO, FotosYPublicacion
from .lectura import Lector, LecturaError, completar, texto_de_boleta, texto_del_pdf
from .modelos import al_dia
from .odoo import OdooError
from .qr import leer_qr
from .registro import Registrador, RegistroError, referencia
from .sunat import NO_SON_COMPRA, NoEsCompra, XmlInvalido, aplicar_qr
from .validacion import hay_errores, validar

log = logging.getLogger(__name__)

LIMITE_TELEGRAM = 4000
PENDIENTE_ABANDONADO = 6 * 3600
MAX_DESCARGA = 20 * 1024 * 1024
ESPERA_ALBUM = 2.5

AYUDA = (
    "Mándame el <b>comprobante de una compra</b>: foto de la boleta, factura, ticket o nota de venta; "
    "también el <b>PDF</b>, el <b>XML</b> o el <b>ZIP</b> que te manda el proveedor. Si el ticket es largo, "
    "mándalo en varias fotos juntas (álbum). Lo leo, te muestro lo que entendí con el precio de venta sugerido "
    "y, si me confirmas, lo registro en Odoo: proveedor, productos, compra, ingreso al stock y factura de "
    "proveedor con el comprobante adjunto.\n\n"
    "Consejos: foto de frente, con buena luz, sin tapar los totales ni el QR.\n\n"
    "📷 <b>Fotos de los productos:</b> después de registrar, mándame la foto de un producto con el "
    "<b>número de su línea</b> escrito en la foto (por ejemplo <code>3</code>). Una por una, no en álbum.\n\n"
    "🌐 <b>Tienda:</b> te ofrezco publicar los productos; solo se publican con foto y precio. Si no quieres, "
    "quedan solo en Odoo.\n\n"
    "/foto cartera busca un producto para ponerle foto · /sinfoto lista los productos del bot sin foto · "
    "/porpublicar lista los que faltan publicar · /cancelar descarta el comprobante que estás revisando · "
    "/categorias lista las categorías · /id muestra tu número de usuario"
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


def mismo_comprobante(a, b) -> bool:
    if a.serie_numero and b.serie_numero:
        return referencia(a) == referencia(b) and (a.proveedor.ruc or a.proveedor.razon_social) == (
            b.proveedor.ruc or b.proveedor.razon_social
        )
    return False


def nombres_de_adjuntos(adjuntos: list[tuple[str, bytes]], ref: str | None) -> list[tuple[str, bytes]]:
    if not ref:
        return adjuntos
    return [
        (f"{ref}{nombre[len('boleta') :]}" if nombre.startswith("boleta") else nombre, datos)
        for nombre, datos in adjuntos
    ]


class BotBoletas(FotosYPublicacion):
    def __init__(self, lector: Lector, registrador: Registrador, usuarios: frozenset[int]):
        self.lector = lector
        self.registrador = registrador
        self.usuarios = usuarios
        self.candado_odoo = asyncio.Lock()
        self.candado_cola = asyncio.Lock()
        self.albumes: dict[str, dict] = {}

    def autorizado(self, update: Update) -> bool:
        usuario = update.effective_user
        return usuario is not None and usuario.id in self.usuarios

    async def rechazar(self, update: Update) -> None:
        usuario = update.effective_user
        log.warning(
            "Acceso no autorizado de %s (%s)", usuario.id if usuario else "?", usuario.username if usuario else "?"
        )
        if update.effective_message:
            await update.effective_message.reply_text(
                f"No estás autorizado para usar este bot. Tu id es {usuario.id if usuario else '?'}; "
                "pásaselo al administrador."
            )

    @staticmethod
    def teclado(id_boleta: str, plan, problemas) -> InlineKeyboardMarkup:
        if plan.ya_registrada:
            botones = [[InlineKeyboardButton("Cerrar", callback_data=f"can:{id_boleta}")]]
        elif hay_errores(problemas) or plan.errores:
            botones = [
                [
                    InlineKeyboardButton("✏️ Corregir", callback_data=f"cor:{id_boleta}"),
                    InlineKeyboardButton("🔄 Leer de nuevo", callback_data=f"rel:{id_boleta}"),
                ],
                [InlineKeyboardButton("❌ Cancelar", callback_data=f"can:{id_boleta}")],
            ]
        else:
            botones = [
                [InlineKeyboardButton("✅ Registrar", callback_data=f"reg:{id_boleta}")],
                [
                    InlineKeyboardButton("✏️ Corregir", callback_data=f"cor:{id_boleta}"),
                    InlineKeyboardButton("❌ Cancelar", callback_data=f"can:{id_boleta}"),
                ],
            ]
        if plan.parecida and plan.parecida.get("exacta", True) and not plan.ya_registrada:
            botones.insert(0, [InlineKeyboardButton("📎 Es la misma compra", callback_data=f"uni:{id_boleta}")])
        return InlineKeyboardMarkup(botones)

    @staticmethod
    def avisos_extra(pendiente: dict) -> list[str]:
        boleta = pendiente["boleta"]
        extra = ajustes.avisos(boleta, pendiente.get("ajustes"))
        documento = pendiente.get("documento")
        sin_leer = getattr(documento, "paginas_sin_leer", 0) if documento else 0
        if sin_leer and documento.origen == "album":
            extra.append(
                f"Me mandaste {archivos.MAX_FOTOS_ALBUM + sin_leer} fotos y leí las primeras "
                f"{archivos.MAX_FOTOS_ALBUM}: si faltan líneas, mándame las demás en otro álbum o como PDF."
            )
        elif sin_leer:
            extra.append(
                f"El PDF tiene {MAX_PAGINAS + sin_leer} páginas y leí las primeras {MAX_PAGINAS}: si faltan "
                "líneas, mándame el resto de páginas en otro PDF o regístralas a mano."
            )
        return extra

    @staticmethod
    def poner_al_dia(chat_data: dict) -> int:
        cambiados = 0
        for pendiente in [chat_data.get("pendiente"), *(chat_data.get("cola") or [])]:
            if pendiente and pendiente.get("boleta") is not None:
                nueva = al_dia(pendiente["boleta"])
                cambiados += nueva is not pendiente["boleta"]
                pendiente["boleta"] = nueva
        return cambiados

    async def al_iniciar(self, app: Application) -> None:
        cambiados = sum(self.poner_al_dia(datos) for datos in app.chat_data.values())
        if cambiados:
            log.info("Puse al día %s comprobantes guardados con la versión anterior del bot.", cambiados)

    async def mostrar(self, mensaje: Message, pendiente: dict, editar: bool) -> None:
        if pendiente.get("boleta") is not None:
            pendiente["boleta"] = al_dia(pendiente["boleta"])
        if "ajustes" not in pendiente:
            pendiente["boleta"], pendiente["ajustes"] = ajustes.ajustar(pendiente["boleta"])
        boleta = pendiente["boleta"]
        try:
            plan = await asyncio.to_thread(self.registrador.planificar, boleta)
        except (OdooError, RegistroError) as exc:
            texto = f"Leí el comprobante, pero no pude consultar Odoo: {escape(getattr(exc, 'mensaje', str(exc)))}"
            await (mensaje.edit_text(texto) if editar else mensaje.reply_text(texto))
            return
        problemas = validar(boleta)
        documento = pendiente.get("documento")
        partes = partir(
            mensajes.resumen(
                plan,
                problemas,
                documento.origen if documento else None,
                pendiente.get("qr"),
                self.avisos_extra(pendiente),
            )
        )
        teclado = self.teclado(pendiente["id"], plan, problemas)
        anterior = pendiente.get("mensaje_id")
        for parte in partes[:-1]:
            await mensaje.reply_text(parte, parse_mode=ParseMode.HTML)
        if editar and len(partes) == 1:
            enviado = await mensaje.edit_text(partes[-1], parse_mode=ParseMode.HTML, reply_markup=teclado)
        else:
            enviado = await mensaje.reply_text(partes[-1], parse_mode=ParseMode.HTML, reply_markup=teclado)
        pendiente["mensaje_id"] = enviado.message_id if hasattr(enviado, "message_id") else None
        if anterior and anterior != pendiente["mensaje_id"]:
            await self._quitar_botones_de(mensaje, anterior)

    @staticmethod
    async def _quitar_botones_de(mensaje: Message, mensaje_id: int) -> None:
        if not hasattr(mensaje, "get_bot"):
            return
        with contextlib.suppress(TelegramError):
            await mensaje.get_bot().edit_message_reply_markup(
                chat_id=mensaje.chat_id, message_id=mensaje_id, reply_markup=None
            )

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
        for clave in ("pendiente", "esperando_foto", "esperando_precio"):
            context.chat_data.pop(clave, None)
        if context.chat_data.get("cola"):
            await update.effective_message.reply_text("Listo, cancelado. Sigue el próximo comprobante de la cola.")
            await self.siguiente(update.effective_message, context)
            return
        await update.effective_message.reply_text("Listo, cancelado. Mándame otro comprobante cuando quieras.")

    async def siguiente(self, mensaje: Message, context: ContextTypes.DEFAULT_TYPE) -> None:
        async with self.candado_cola:
            cola = context.chat_data.get("cola") or []
            if context.chat_data.get("pendiente") or not cola:
                return
            pendiente = cola.pop(0)
            context.chat_data["pendiente"] = pendiente
        quedan = len(cola)
        aviso = f"📥 Sigue el próximo comprobante{f' (quedan {quedan} en cola)' if quedan else ''}:"
        estado = await mensaje.reply_text(aviso)
        await self.mostrar(estado, pendiente, editar=False)

    @staticmethod
    async def _descargar(adjunto) -> bytes:
        archivo = await adjunto.get_file()
        return bytes(await archivo.download_as_bytearray())

    async def recibir_archivo(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.autorizado(update):
            await self.rechazar(update)
            return
        msg = update.effective_message
        adjunto = msg.photo[-1] if msg.photo else msg.document
        if adjunto is None:
            return
        if adjunto.file_size and adjunto.file_size > MAX_DESCARGA:
            await msg.reply_text("El archivo pesa más de 20 MB. Mándalo como foto (comprimida) o un PDF más liviano.")
            return
        if getattr(msg, "media_group_id", None):
            await self._album(msg, adjunto, context)
            return
        datos = await self._descargar(adjunto)
        es_imagen = bool(msg.photo) or tipo_de_archivo(datos) == "imagen"
        if es_imagen and await self.foto_para_producto(msg, context, datos):
            return
        if not es_imagen and FOTO_DE_PRODUCTO.match(msg.caption or ""):
            await msg.reply_text("La foto de un producto tiene que ser una imagen, no un archivo PDF o XML.")
            return
        await self._leer_archivo(msg, context, datos)

    async def _leer_archivo(self, msg: Message, context: ContextTypes.DEFAULT_TYPE, datos: bytes) -> None:
        if tipo_de_archivo(datos) != "zip":
            await self._leer_comprobante(msg, context, lambda: archivos.preparar(datos))
            return
        try:
            documentos = await asyncio.to_thread(archivos.preparar_todos, datos)
        except (LecturaError, XmlInvalido) as exc:
            await msg.reply_text(f"❌ {escape(str(exc))}")
            return
        except NoEsCompra as exc:
            await msg.reply_text(f"📄 {escape(str(exc))}")
            return
        if len(documentos) > 1:
            await msg.reply_text(f"El ZIP trae {len(documentos)} comprobantes: los leo uno por uno.")
        for documento in documentos:
            await self._leer_comprobante(msg, context, lambda documento=documento: documento)

    async def _album(self, msg: Message, adjunto, context: ContextTypes.DEFAULT_TYPE) -> None:
        grupo = msg.media_group_id
        nuevo = grupo not in self.albumes
        album = self.albumes.setdefault(grupo, {"partes": [], "texto": None})
        album["partes"].append((msg.message_id, adjunto))
        album["texto"] = album["texto"] or msg.caption
        if not nuevo:
            return
        await asyncio.sleep(ESPERA_ALBUM)
        album = self.albumes.pop(grupo, album)
        if FOTO_DE_PRODUCTO.match(album["texto"] or ""):
            await msg.reply_text("Las fotos de los productos mándamelas una por una, cada una con su número.")
            return
        datos = [await self._descargar(a) for _, a in sorted(album["partes"], key=lambda p: p[0])]
        imagenes = [d for d in datos if tipo_de_archivo(d) == "imagen"]
        if len(imagenes) == len(datos):
            await self._leer_comprobante(msg, context, lambda: archivos.desde_album(imagenes))
            return
        otros = [d for d in datos if tipo_de_archivo(d) != "imagen"]
        cuantos = len(otros) + (1 if imagenes else 0)
        await msg.reply_text(
            f"Me mandaste {cuantos} comprobantes juntos: los leo uno por uno y te los muestro en orden."
            if cuantos > 1
            else "Leo el comprobante que me mandaste."
        )
        for archivo in otros:
            await self._leer_archivo(msg, context, archivo)
        if imagenes:
            await self._leer_comprobante(msg, context, lambda: archivos.desde_album(imagenes))

    async def _leer_comprobante(self, msg: Message, context: ContextTypes.DEFAULT_TYPE, preparar) -> None:
        estado = await msg.reply_text("📄 Leyendo el comprobante…")
        await context.bot.send_chat_action(msg.chat_id, ChatAction.TYPING)
        try:
            documento = await asyncio.to_thread(preparar)
        except NoEsCompra as exc:
            await estado.edit_text(f"📄 {escape(str(exc))}")
            return
        except (LecturaError, XmlInvalido) as exc:
            await estado.edit_text(f"❌ {escape(str(exc))}")
            return
        await self.leer_y_mostrar(estado, context, documento)

    async def no_soportado(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.autorizado(update):
            await self.rechazar(update)
            return
        await update.effective_message.reply_text(
            "Solo leo comprobantes en foto, imagen, PDF, XML o ZIP. /ayuda para más detalles."
        )

    async def _categorias(self) -> list[str]:
        try:
            return await asyncio.to_thread(self.registrador.nombres_categorias)
        except OdooError as exc:
            log.warning("No pude leer las categorías: %s", exc.mensaje)
            return []

    async def _leer(self, documento: Documento, categorias: list[str]):
        if documento.boleta is not None:
            try:
                sugerida = await self.lector.leer(None, categorias, texto_de_boleta(documento.boleta))
                return completar(documento.boleta, sugerida), None
            except LecturaError as exc:
                log.warning("No pude elegir categorías para el XML: %s", exc)
                return documento.boleta, None
        texto = texto_del_pdf(documento.texto) if documento.texto else None
        imagenes = documento.imagenes
        boleta = await self.lector.leer(imagenes if len(imagenes) > 1 else documento.imagen, categorias, texto)
        datos_qr = None
        for imagen in reversed(imagenes):
            datos_qr = await asyncio.to_thread(leer_qr, imagen)
            if datos_qr is not None:
                break
        if datos_qr is None:
            return boleta, None
        boleta, cambios = aplicar_qr(boleta, datos_qr)
        return boleta, (True, cambios)

    async def leer_y_mostrar(
        self, estado: Message, context: ContextTypes.DEFAULT_TYPE, documento: Documento, reemplazar: bool = False
    ) -> None:
        try:
            boleta, qr = await self._leer(documento, await self._categorias())
        except NoEsCompra as exc:
            await estado.edit_text(f"📄 {escape(str(exc))}")
            return
        except LecturaError as exc:
            await estado.edit_text(f"❌ {escape(str(exc))}")
            return
        if not boleta.es_comprobante:
            await estado.edit_text(
                "❌ No parece una boleta, factura, ticket ni nota de venta de una compra. Si es la "
                "foto de un producto, mándala con el número de su línea escrito en la foto "
                "(por ejemplo «3»)."
            )
            return
        if boleta.tipo in NO_SON_COMPRA:
            await estado.edit_text(
                f"📄 Es {NO_SON_COMPRA[boleta.tipo]}, no una compra. No lo registro; si hace "
                "falta, regístralo a mano en Odoo."
            )
            return
        boleta, hechos = ajustes.ajustar(boleta)
        pendiente = {
            "id": uuid.uuid4().hex[:12],
            "boleta": boleta,
            "documento": documento,
            "qr": qr,
            "ajustes": hechos,
            "ocupado": False,
            "desde": time.time(),
        }
        apartado = None
        async with self.candado_cola:
            actual = context.chat_data.get("pendiente")
            libre = actual is not None and not actual.get("ocupado")
            if libre and not reemplazar and mismo_comprobante(actual["boleta"], boleta):
                reemplazar = True
            elif libre and not reemplazar and time.time() - actual.get("desde", 0) > PENDIENTE_ABANDONADO:
                context.chat_data.setdefault("cola", []).append(actual)
                apartado = actual
                reemplazar = True
            if actual and not reemplazar:
                cola = context.chat_data.setdefault("cola", [])
                cola.append(pendiente)
                en_cola = len(cola)
            else:
                context.chat_data["pendiente"] = pendiente
                en_cola = 0
        if en_cola:
            numero = referencia(boleta) or boleta.serie_numero or "sin número"
            revisando = referencia(actual["boleta"]) or actual["boleta"].serie_numero or "otro comprobante"
            await estado.edit_text(
                f"📥 Leí {escape(numero)}. Lo dejé en cola ({en_cola} en espera) porque estás "
                f"revisando {escape(revisando)}: te lo muestro apenas lo registres o lo canceles "
                "(❌ Cancelar o /cancelar)."
            )
            return
        await self.mostrar(estado, pendiente, editar=True)
        if apartado:
            numero = referencia(apartado["boleta"]) or apartado["boleta"].serie_numero or "sin número"
            await estado.reply_text(
                f"📥 {escape(numero)} llevaba más de 6 horas sin registrarse: lo pasé a la cola "
                "y te lo muestro después de este."
            )

    async def boton(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        consulta = update.callback_query
        if not self.autorizado(update):
            await consulta.answer("No autorizado", show_alert=True)
            return
        accion, _, dato = (consulta.data or "").partition(":")
        if await self.boton_tienda(consulta, context, accion, dato):
            return
        pendiente = context.chat_data.get("pendiente")
        if not pendiente or pendiente["id"] != dato:
            if any(p.get("id") == dato for p in context.chat_data.get("cola") or []):
                await consulta.answer(
                    "Ese comprobante está en la cola: te lo muestro apenas registres o canceles el "
                    "que estás revisando.",
                    show_alert=True,
                )
            else:
                await consulta.answer("Ese comprobante ya no está activo. Mándalo de nuevo.", show_alert=True)
            await self._quitar_botones(consulta.message)
            return
        if pendiente["ocupado"]:
            await consulta.answer("Espera, estoy trabajando en ese comprobante…")
            return
        await consulta.answer()

        if accion == "can":
            context.chat_data.pop("pendiente", None)
            await self._quitar_botones(consulta.message)
            await consulta.message.reply_text("Cancelado. No se registró nada.")
            await self.siguiente(consulta.message, context)
        elif accion == "cor":
            await consulta.message.reply_text(correcciones.AYUDA, parse_mode=ParseMode.HTML)
        elif accion == "rel":
            await self._quitar_botones(consulta.message)
            estado = await consulta.message.reply_text("📄 Leyendo de nuevo…")
            await self.leer_y_mostrar(estado, context, pendiente["documento"], reemplazar=True)
        elif accion == "reg":
            await self.registrar(consulta.message, context, pendiente)
        elif accion == "uni":
            await self.unir(consulta.message, context, pendiente)

    async def unir(self, mensaje: Message, context: ContextTypes.DEFAULT_TYPE, pendiente: dict) -> None:
        boleta = pendiente["boleta"]
        pendiente["ocupado"] = True
        await self._quitar_botones(mensaje)
        estado = await mensaje.reply_text("⏳ Uniendo el comprobante a la compra que ya está en Odoo…")
        adjuntos = nombres_de_adjuntos(pendiente["documento"].adjuntos, referencia(boleta))
        try:
            async with self.candado_odoo:
                plan = await asyncio.to_thread(self.registrador.planificar, boleta)
                if not plan.parecida:
                    raise RegistroError("Ya no encuentro la compra parecida en Odoo.")
                resultado = await asyncio.to_thread(self.registrador.unir, plan.parecida["id"], boleta, adjuntos)
        except (RegistroError, OdooError) as exc:
            pendiente["ocupado"] = False
            texto = str(exc) if isinstance(exc, RegistroError) else exc.mensaje
            log.error("Fallo al unir %s: %s", referencia(boleta), texto)
            await estado.edit_text(f"❌ No pude unirlo: {escape(texto)}")
            await self.mostrar(estado, pendiente, editar=False)
            return
        context.chat_data.pop("pendiente", None)
        if resultado.productos:
            context.chat_data["ultima"] = {"orden": resultado.orden, "productos": resultado.productos}
        await estado.edit_text(mensajes.unido(resultado), parse_mode=ParseMode.HTML)
        await self.siguiente(mensaje, context)

    async def registrar(self, mensaje: Message, context: ContextTypes.DEFAULT_TYPE, pendiente: dict) -> None:
        boleta = pendiente["boleta"]
        if hay_errores(validar(boleta)):
            await mensaje.reply_text("Todavía hay errores que corregir.")
            return
        pendiente["ocupado"] = True
        await self._quitar_botones(mensaje)
        estado = await mensaje.reply_text("⏳ Registrando en Odoo…")
        adjuntos = nombres_de_adjuntos(pendiente["documento"].adjuntos, referencia(boleta))
        try:
            async with self.candado_odoo:
                resultado = await asyncio.to_thread(self.registrador.registrar, boleta, adjuntos)
        except (RegistroError, OdooError) as exc:
            pendiente["ocupado"] = False
            texto = str(exc) if isinstance(exc, RegistroError) else exc.mensaje
            log.error("Fallo al registrar %s: %s", referencia(boleta), texto)
            teclado = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🔁 Reintentar", callback_data=f"reg:{pendiente['id']}"),
                        InlineKeyboardButton("❌ Cancelar", callback_data=f"can:{pendiente['id']}"),
                    ]
                ]
            )
            await estado.edit_text(
                f"❌ No se pudo terminar el registro: {escape(texto)}\n\n"
                "Lo que ya se hizo queda guardado; «Reintentar» sigue desde ahí sin duplicar.",
                reply_markup=teclado,
            )
            return
        context.chat_data.pop("pendiente", None)
        context.chat_data["ultima"] = {"orden": resultado.orden, "productos": resultado.productos}
        teclado = None
        if resultado.productos:
            teclado = InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("🌐 Publicar", callback_data=f"pub:{resultado.orden}"[:64]),
                        InlineKeyboardButton("Solo en Odoo", callback_data="nopub:"),
                    ]
                ]
            )
        await estado.edit_text(mensajes.registrado(resultado), parse_mode=ParseMode.HTML, reply_markup=teclado)
        await self.siguiente(mensaje, context)

    async def recibir_texto(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.autorizado(update):
            await self.rechazar(update)
            return
        msg = update.effective_message
        pendiente = context.chat_data.get("pendiente")
        esperando = context.chat_data.get("esperando_precio")
        if (
            pendiente
            and esperando
            and esperando["hasta"] > time.time()
            and SOLO_PRECIO.match(msg.text or "")
            and await self.texto_sin_boleta(msg, context)
        ):
            return
        if not pendiente:
            if not await self.texto_sin_boleta(msg, context):
                await msg.reply_text("Mándame el comprobante de una compra. /ayuda para más detalles.")
            return
        if pendiente["ocupado"]:
            await msg.reply_text("Espera, estoy registrando ese comprobante…")
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
        with contextlib.suppress(BadRequest):
            await mensaje.edit_reply_markup(reply_markup=None)

    async def error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.exception("Error no controlado", exc_info=context.error)
        if isinstance(update, Update) and update.effective_message:
            with contextlib.suppress(Exception):
                await update.effective_message.reply_text("Ocurrió un error inesperado. Intenta de nuevo.")

    def instalar(self, app: Application) -> None:
        app.add_handler(CommandHandler(["start", "ayuda", "help"], self.cmd_ayuda))
        app.add_handler(CommandHandler("id", self.cmd_id))
        app.add_handler(CommandHandler("cancelar", self.cmd_cancelar))
        app.add_handler(CommandHandler("categorias", self.cmd_categorias))
        app.add_handler(CommandHandler("sinfoto", self.cmd_sinfoto))
        app.add_handler(CommandHandler("foto", self.cmd_foto))
        app.add_handler(CommandHandler("porpublicar", self.cmd_porpublicar))
        app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, self.recibir_archivo))
        app.add_handler(
            MessageHandler(
                filters.VIDEO | filters.VOICE | filters.AUDIO | filters.VIDEO_NOTE | filters.Sticker.ALL,
                self.no_soportado,
            )
        )
        app.add_handler(CallbackQueryHandler(self.boton))
        app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.recibir_texto))
        app.add_error_handler(self.error)
