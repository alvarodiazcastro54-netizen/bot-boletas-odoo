from __future__ import annotations

import asyncio
import logging
import re
import time
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from . import mensajes
from .lectura import LecturaError, preparar_foto_producto
from .odoo import OdooError
from .precios import precio_sugerido

log = logging.getLogger(__name__)

FOTO_DE_PRODUCTO = re.compile(r"^\s*(?:foto\s*)?(\d{1,3})\s*$", re.IGNORECASE)
PRECIO_DE_LINEA = re.compile(r"^\s*(\d{1,3})\s+(?:venta|pv|precio)\s+(?:s/\s*)?([\d.,]+)\s*$", re.IGNORECASE)
SOLO_PRECIO = re.compile(r"^\s*(?:s/\s*)?([\d]+(?:[.,]\d{1,2})?)\s*$", re.IGNORECASE)
ESPERA_DE_FOTO = 600
MAXIMO_EN_LISTA = 10


def _monto(texto: str) -> float | None:
    try:
        valor = float(texto.replace(",", "."))
    except ValueError:
        return None
    return valor if valor > 0 else None


class FotosYPublicacion:
    @property
    def tienda(self):
        return self.registrador.tienda

    async def _odoo(self, funcion, *args):
        async with self.candado_odoo:
            return await asyncio.to_thread(funcion, *args)

    def _recordar(self, context: ContextTypes.DEFAULT_TYPE, productos: list[dict]) -> None:
        mostrados = context.chat_data.setdefault("mostrados", {})
        for p in productos:
            mostrados[p["id"]] = p.get("nombre") or p.get("name")

    @staticmethod
    def _teclado_publicar(plantilla_id: int) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [
                    InlineKeyboardButton("🌐 Publicar", callback_data=f"pubp:{plantilla_id}"),
                    InlineKeyboardButton("Solo en Odoo", callback_data="nopub:"),
                ]
            ]
        )

    async def cmd_sinfoto(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.autorizado(update):
            await self.rechazar(update)
            return
        msg = update.effective_message
        try:
            lista, total = await asyncio.to_thread(self.registrador.productos_sin_foto)
        except OdooError as exc:
            await msg.reply_text(f"❌ No pude consultar Odoo: {exc.mensaje}")
            return
        if not lista:
            await msg.reply_text("✅ Todos los productos que registró el bot tienen foto.")
            return
        self._recordar(context, lista)
        texto = f"📷 Productos sin foto: {total}. Toca uno y después mándame su foto."
        if total > len(lista):
            texto += f"\nTe muestro los {len(lista)} más recientes; al ponerles foto aparecen los siguientes."
        botones = [[InlineKeyboardButton(mensajes.boton_sin_foto(p), callback_data=f"fot:{p['id']}")] for p in lista]
        await msg.reply_text(texto, reply_markup=InlineKeyboardMarkup(botones))

    async def cmd_foto(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.autorizado(update):
            await self.rechazar(update)
            return
        msg = update.effective_message
        texto = " ".join(context.args or []).strip()
        if not texto:
            await msg.reply_text("Escribe qué producto buscas, por ejemplo: /foto cartera hypnotic")
            return
        try:
            encontrados = await asyncio.to_thread(self.tienda.buscar, texto, MAXIMO_EN_LISTA)
        except OdooError as exc:
            await msg.reply_text(f"❌ No pude buscar en Odoo: {exc.mensaje}")
            return
        if not encontrados:
            await msg.reply_text(f"No encontré productos con «{texto}». Prueba con otra palabra.")
            return
        self._recordar(context, encontrados)
        botones = [
            [InlineKeyboardButton(f"📷 {mensajes.recortar(p['name'])}", callback_data=f"fot:{p['id']}")]
            for p in encontrados
        ]
        await msg.reply_text(
            f"Encontré {len(encontrados)}. Toca el producto y después mándame su foto.",
            reply_markup=InlineKeyboardMarkup(botones),
        )

    async def cmd_porpublicar(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not self.autorizado(update):
            await self.rechazar(update)
            return
        msg = update.effective_message
        try:
            ids = await asyncio.to_thread(self.tienda.del_bot)
            estados = [e for e in await asyncio.to_thread(self.tienda.estados, ids) if not e.publicado]
        except OdooError as exc:
            await msg.reply_text(f"❌ No pude consultar Odoo: {exc.mensaje}")
            return
        if not estados:
            await msg.reply_text("✅ Todos los productos que registró el bot ya están publicados.")
            return
        estados.sort(key=lambda e: e.plantilla_id, reverse=True)
        mostrados = estados[:MAXIMO_EN_LISTA]
        self._recordar(context, [{"id": e.plantilla_id, "nombre": e.nombre} for e in mostrados])
        botones = []
        for e in mostrados:
            if e.listo:
                botones.append(
                    [InlineKeyboardButton(f"🌐 {mensajes.recortar(e.nombre)}", callback_data=f"pubp:{e.plantilla_id}")]
                )
            elif "foto" in e.faltan:
                botones.append(
                    [InlineKeyboardButton(f"📷 {mensajes.recortar(e.nombre)}", callback_data=f"fot:{e.plantilla_id}")]
                )
            else:
                botones.append(
                    [InlineKeyboardButton(f"💲 {mensajes.recortar(e.nombre)}", callback_data=f"pubp:{e.plantilla_id}")]
                )
        await msg.reply_text(
            mensajes.por_publicar(mostrados, len(estados)),
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(botones),
        )

    async def esperar_foto(self, mensaje: Message, context: ContextTypes.DEFAULT_TYPE, plantilla_id: int) -> None:
        nombre = context.chat_data.get("mostrados", {}).get(plantilla_id) or "ese producto"
        context.chat_data["esperando_foto"] = {
            "plantilla": plantilla_id,
            "nombre": nombre,
            "hasta": time.time() + ESPERA_DE_FOTO,
        }
        teclado = InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancelar", callback_data="fotno:")]])
        await mensaje.reply_text(f"📷 Mándame la foto de «{nombre}». Te espero 10 minutos.", reply_markup=teclado)

    async def _subir_foto(self, msg: Message, datos: bytes, plantilla_id: int) -> bool:
        try:
            foto = await asyncio.to_thread(preparar_foto_producto, datos)
            await self._odoo(self.registrador.poner_foto_en_plantilla, plantilla_id, foto)
        except LecturaError as exc:
            await msg.reply_text(f"❌ {exc}")
            return False
        except OdooError as exc:
            log.error("No pude poner la foto (plantilla %s): %s", plantilla_id, exc.mensaje)
            await msg.reply_text(f"❌ No pude poner la foto en Odoo: {exc.mensaje}")
            return False
        return True

    async def foto_para_producto(self, msg: Message, context: ContextTypes.DEFAULT_TYPE, datos: bytes) -> bool:
        numero = FOTO_DE_PRODUCTO.match(msg.caption or "")
        if numero:
            await self.foto_de_producto(msg, context, datos, int(numero.group(1)))
            return True
        esperando = context.chat_data.get("esperando_foto")
        if not esperando or esperando["hasta"] <= time.time():
            return False
        if await self._subir_foto(msg, datos, esperando["plantilla"]):
            context.chat_data.pop("esperando_foto", None)
            await self._despues_de_la_foto(msg, context, esperando["plantilla"], esperando["nombre"])
        return True

    async def foto_de_producto(
        self, msg: Message, context: ContextTypes.DEFAULT_TYPE, datos: bytes, numero: int
    ) -> None:
        ultima = context.chat_data.get("ultima")
        if not ultima or not ultima["productos"]:
            await msg.reply_text(
                "No tengo una compra recién registrada en este chat. Para ponerle foto a otro "
                "producto usa /foto (por ejemplo /foto cartera) o /sinfoto."
            )
            return
        productos = {n: (nombre, producto_id) for n, nombre, producto_id in ultima["productos"]}
        if numero not in productos:
            await msg.reply_text(
                f"La compra {ultima['orden']} no tiene la línea {numero}: "
                f"sus líneas van de la {min(productos)} a la {max(productos)}."
            )
            return
        nombre, producto_id = productos[numero]
        try:
            plantilla_id = await asyncio.to_thread(self.tienda.plantilla_de, producto_id)
        except OdooError as exc:
            await msg.reply_text(f"❌ No pude consultar Odoo: {exc.mensaje}")
            return
        if plantilla_id and await self._subir_foto(msg, datos, plantilla_id):
            await self._despues_de_la_foto(msg, context, plantilla_id, f"{nombre} (línea {numero})")

    async def _despues_de_la_foto(
        self, msg: Message, context: ContextTypes.DEFAULT_TYPE, plantilla_id: int, nombre: str
    ) -> None:
        if plantilla_id in context.chat_data.get("por_publicar", set()):
            await msg.reply_text(f"📷 Foto puesta: {nombre}.")
            await self.publicar(msg, context, [plantilla_id])
            return
        await msg.reply_text(
            f"📷 Foto puesta: {nombre}. ¿Lo publico en la tienda?", reply_markup=self._teclado_publicar(plantilla_id)
        )

    async def publicar(
        self,
        mensaje: Message,
        context: ContextTypes.DEFAULT_TYPE,
        plantilla_ids: list[int],
        numeros: dict[int, int] | None = None,
    ) -> None:
        try:
            publicados, pendientes = await self._odoo(self.tienda.publicar, plantilla_ids)
        except OdooError as exc:
            log.error("No pude publicar %s: %s", plantilla_ids, exc.mensaje)
            await mensaje.reply_text(f"❌ No pude publicar en la tienda: {exc.mensaje}")
            return
        por_publicar = context.chat_data.setdefault("por_publicar", set())
        por_publicar.difference_update(e.plantilla_id for e in publicados)
        por_publicar.update(e.plantilla_id for e in pendientes)
        await mensaje.reply_text(
            mensajes.publicacion(publicados, pendientes, numeros),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )
        sin_precio = [e for e in pendientes if e.faltan == ["precio"]]
        if len(plantilla_ids) == 1 and sin_precio:
            await self._pedir_precio(mensaje, context, sin_precio[0].plantilla_id, sin_precio[0].nombre)
        elif len(plantilla_ids) == 1 and pendientes and "foto" in pendientes[0].faltan:
            self._recordar(context, [{"id": pendientes[0].plantilla_id, "nombre": pendientes[0].nombre}])
            await self.esperar_foto(mensaje, context, pendientes[0].plantilla_id)

    async def _pedir_precio(
        self, mensaje: Message, context: ContextTypes.DEFAULT_TYPE, plantilla_id: int, nombre: str
    ) -> None:
        sugerido = None
        try:
            costo, categoria = await asyncio.to_thread(self.tienda.costo_y_categoria, plantilla_id)
            if costo > 0:
                sugerido = precio_sugerido(costo, await asyncio.to_thread(self.registrador.margenes.para, categoria))
        except OdooError as exc:
            log.warning("No pude calcular el precio sugerido de %s: %s", plantilla_id, exc.mensaje)
        context.chat_data["esperando_precio"] = {
            "plantilla": plantilla_id,
            "nombre": nombre,
            "hasta": time.time() + ESPERA_DE_FOTO,
        }
        texto = f"💲 ¿A cuánto se vende «{escape(nombre)}»? Escríbeme el precio, por ejemplo <code>45</code>."
        teclado = None
        if sugerido:
            texto += f"\nSegún los márgenes de UYAY te sugiero S/ {sugerido:,.2f}."
            teclado = InlineKeyboardMarkup(
                [[InlineKeyboardButton(f"✅ Usar S/ {sugerido:,.2f}", callback_data=f"pre:{plantilla_id}:{sugerido}")]]
            )
        await mensaje.reply_text(texto, parse_mode=ParseMode.HTML, reply_markup=teclado)

    async def _poner_precio_y_publicar(
        self, mensaje: Message, context: ContextTypes.DEFAULT_TYPE, plantilla_id: int, precio: float
    ) -> None:
        try:
            await self._odoo(self.tienda.poner_precio, plantilla_id, precio)
        except OdooError as exc:
            await mensaje.reply_text(f"❌ No pude poner el precio en Odoo: {exc.mensaje}")
            return
        context.chat_data.pop("esperando_precio", None)
        await mensaje.reply_text(f"💲 Precio de venta: S/ {precio:,.2f}.")
        if plantilla_id in context.chat_data.get("por_publicar", set()):
            await self.publicar(mensaje, context, [plantilla_id])

    async def boton_tienda(self, consulta, context: ContextTypes.DEFAULT_TYPE, accion: str, dato: str) -> bool:
        if accion == "fot":
            await consulta.answer()
            await self.esperar_foto(consulta.message, context, int(dato))
        elif accion == "fotno":
            context.chat_data.pop("esperando_foto", None)
            await consulta.answer()
            await self._quitar_botones(consulta.message)
            await consulta.message.reply_text("Listo, ya no espero la foto.")
        elif accion == "nopub":
            await consulta.answer()
            await self._quitar_botones(consulta.message)
            await consulta.message.reply_text(
                "👍 Quedan solo en Odoo, sin publicar. Cuando quieras, publícalos desde Odoo o con /porpublicar."
            )
        elif accion == "pub":
            await consulta.answer()
            await self._quitar_botones(consulta.message)
            await self._publicar_la_ultima(consulta.message, context, dato)
        elif accion == "pubp":
            await consulta.answer()
            await self._quitar_botones(consulta.message)
            context.chat_data.setdefault("por_publicar", set()).add(int(dato))
            await self.publicar(consulta.message, context, [int(dato)])
        elif accion == "pre":
            plantilla, _, precio = dato.partition(":")
            await consulta.answer()
            await self._quitar_botones(consulta.message)
            await self._poner_precio_y_publicar(consulta.message, context, int(plantilla), float(precio))
        else:
            return False
        return True

    async def _publicar_la_ultima(self, mensaje: Message, context: ContextTypes.DEFAULT_TYPE, orden: str = "") -> None:
        ultima = context.chat_data.get("ultima")
        if orden and (not ultima or ultima["orden"] != orden):
            try:
                productos = await asyncio.to_thread(self.registrador.productos_de_compra, orden)
            except OdooError as exc:
                await mensaje.reply_text(f"❌ No pude consultar Odoo: {exc.mensaje}")
                return
            ultima = {"orden": orden, "productos": productos}
        if not ultima or not ultima["productos"]:
            await mensaje.reply_text("Ya no tengo esa compra a mano. Usa /porpublicar.")
            return
        producto_ids = [p for _, _, p in ultima["productos"]]
        try:
            plantillas = await asyncio.to_thread(self.tienda._plantillas_de, producto_ids)
        except OdooError as exc:
            await mensaje.reply_text(f"❌ No pude consultar Odoo: {exc.mensaje}")
            return
        numeros = {plantillas[p]: n for n, _, p in ultima["productos"] if p in plantillas}
        context.chat_data.setdefault("por_publicar", set()).update(numeros)
        await self.publicar(mensaje, context, list(numeros), numeros)

    async def texto_sin_boleta(self, msg: Message, context: ContextTypes.DEFAULT_TYPE) -> bool:
        texto = msg.text or ""
        esperando = context.chat_data.get("esperando_precio")
        if esperando and esperando["hasta"] > time.time() and (m := SOLO_PRECIO.match(texto)):
            precio = _monto(m.group(1))
            if precio:
                await self._poner_precio_y_publicar(msg, context, esperando["plantilla"], precio)
                return True
        m = PRECIO_DE_LINEA.match(texto)
        ultima = context.chat_data.get("ultima")
        if not m or not ultima or not ultima["productos"]:
            return False
        numero, precio = int(m.group(1)), _monto(m.group(2))
        productos = {n: producto_id for n, _, producto_id in ultima["productos"]}
        if numero not in productos or not precio:
            await msg.reply_text(f"La compra {ultima['orden']} no tiene la línea {numero}, o el precio no es válido.")
            return True
        try:
            plantilla_id = await asyncio.to_thread(self.tienda.plantilla_de, productos[numero])
        except OdooError as exc:
            await msg.reply_text(f"❌ No pude consultar Odoo: {exc.mensaje}")
            return True
        if plantilla_id:
            await self._poner_precio_y_publicar(msg, context, plantilla_id, precio)
        return True
