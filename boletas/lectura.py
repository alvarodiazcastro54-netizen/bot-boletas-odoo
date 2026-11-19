from __future__ import annotations

import base64
import copy
import io
import logging
from pathlib import Path

import anthropic
import openai
import pillow_heif
from PIL import Image, ImageOps
from pydantic import ValidationError

from .modelos import Boleta

log = logging.getLogger(__name__)
pillow_heif.register_heif_opener()


def _todo_obligatorio(nodo):
    if isinstance(nodo, dict):
        if nodo.get("type") == "object" and "properties" in nodo:
            nodo["required"] = list(nodo["properties"])
        for valor in nodo.values():
            _todo_obligatorio(valor)
    elif isinstance(nodo, list):
        for valor in nodo:
            _todo_obligatorio(valor)
    return nodo


ESQUEMA = _todo_obligatorio(anthropic.transform_schema(Boleta))


def esquema_con_categorias(categorias: list[str] | None) -> dict:
    if not categorias:
        return ESQUEMA
    esquema = copy.deepcopy(ESQUEMA)
    campo = esquema["$defs"]["Linea"]["properties"]["categoria"]
    campo["anyOf"] = [{"type": "string", "enum": list(categorias)}, {"type": "null"}]
    return esquema


MAX_BYTES = 4_500_000
MAX_LADO = 2400

SISTEMA = """\
Lees comprobantes de compra peruanos (boletas, facturas, tickets y notas de venta) a partir de una foto, \
un PDF o los datos de un XML, y llenas un JSON.

Reglas:
- «tipo»: boleta, factura, ticket (de máquina registradora), nota_de_venta (nota de venta o recibo simple sin \
serie de SUNAT, muchas veces escrito a mano), cotizacion (cotización, presupuesto o proforma), \
liquidacion_de_compra, nota_de_credito, nota_de_debito, guia_de_remision, recibo_por_honorarios u otro.
- En una liquidación de compra (la emite el comprador cuando le compra a una persona sin RUC), «proveedor» es el \
VENDEDOR: su nombre en «razon_social», su DNI en «dni» y «ruc» en null. No pongas los datos de quien la emite. \
Su «total» es el importe total (valor de venta + IGV), no el neto a pagar; la retención de renta no va en \
«otros_cargos».
- Si no hay RUC (por ejemplo en una nota de venta), deja «ruc» en null y copia el nombre del negocio en \
«razon_social».
- Si te dan el texto extraído del PDF, úsalo para copiar números y descripciones con exactitud, con todos sus \
decimales.
- Si te dan varias imágenes, son partes o páginas del mismo comprobante, en orden. Si dos fotos se enciman y una \
línea sale en las dos, cópiala una sola vez.
- Si te dan los datos de un comprobante electrónico (XML), cópialos tal cual: tu trabajo es solo elegir la \
categoría y proponer el nombre para la tienda de cada línea.
- Copia los textos tal como están impresos. No corrijas, no completes ni traduzcas las descripciones.
- Si un dato no se ve (borroso, cortado o tapado por un dedo), pon null y explícalo en «observaciones». \
No lo adivines, aunque puedas calcularlo.
- Números con punto decimal, sin «S/». Las cantidades como número.
- En cada línea: «precio_unitario» es el precio impreso antes del descuento, «descuento_pct» el porcentaje \
de descuento (0 si no hay) e «importe» el monto final de la línea. Si el descuento aparece como monto en \
soles, ponlo en «descuento_monto» y deja «descuento_pct» en 0.
- «cantidad», «unidad», «precio_unitario» e «importe»: cópialos tal como están impresos, aunque la cantidad \
sea de paquetes, cajas o docenas y el precio sea el de cada unidad (por ejemplo «1» de un producto «X 24» a \
4.33 con importe 104.00). No conviertas nada: el bot hace la cuenta.
- «exonerado» es true en las líneas que el comprobante marca sin IGV (exoneradas o inafectas, por ejemplo libros); \
si no lo marca, false.
- Una bonificación, obsequio o regalo del proveedor (importe 0) va como línea con su precio impreso, \
«descuento_pct» 100 e «importe» 0.
- Un descuento sobre toda la compra va en «descuento_global» en positivo, aunque esté impreso como una línea \
con monto negativo.
- Si los importes de las líneas ya suman el total a pagar, los precios incluyen el IGV: \
«precios_incluyen_igv» es true.
- La percepción del IGV (2 %, 1 % o 0.5 %) que se suma al total va en «otros_cargos»; «total» es lo que se paga \
con la percepción incluida.
- Marca «nombre_cortado» cuando la descripción llega al borde del papel o termina a media palabra.
- «fecha_emision» en formato AAAA-MM-DD. Las boletas peruanas usan día/mes/año.
- «cliente_documento» es el DNI o RUC del comprador impreso en el comprobante.
- ICBPER, redondeos y otros cargos que no son productos van en «otros_cargos», no como líneas. También el \
flete, el envío, el delivery, la carreta o la estiba: no son mercadería para revender.
- Si la foto no es un comprobante de compra, pon es_comprobante=false y deja las listas vacías.
- Los productos son mercadería para revender en una tienda. Si te dan la lista de categorías de la tienda, \
elige para cada línea la categoría más específica que le corresponda según la descripción; si ninguna \
encaja, pon null. Si no te dan lista, pon null.
"""


class LecturaError(Exception):
    pass


def preparar_imagen(datos: bytes) -> tuple[bytes, str]:
    try:
        original = Image.open(io.BytesIO(datos))
        formato = (original.format or "").upper()
        rotada = original.getexif().get(0x0112, 1) not in (1, None)
        imagen = ImageOps.exif_transpose(original)
    except Exception as exc:
        raise LecturaError("No pude abrir la imagen. Mándala como foto JPG o PNG.") from exc

    tipos = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}
    grande = len(datos) > MAX_BYTES or max(imagen.size) > MAX_LADO
    if formato in tipos and not grande and not rotada:
        return datos, tipos[formato]

    imagen.thumbnail((MAX_LADO, MAX_LADO))
    salida = io.BytesIO()
    imagen.convert("RGB").save(salida, format="JPEG", quality=90)
    return salida.getvalue(), "image/jpeg"


def preparar_foto_producto(datos: bytes, lado: int = 1920) -> bytes:
    try:
        imagen = ImageOps.exif_transpose(Image.open(io.BytesIO(datos)))
    except Exception as exc:
        raise LecturaError("No pude abrir la imagen. Mándala como foto JPG o PNG.") from exc
    imagen.thumbnail((lado, lado))
    if imagen.mode in ("RGBA", "LA", "P"):
        con_alfa = imagen.convert("RGBA")
        imagen = Image.new("RGB", con_alfa.size, "white")
        imagen.paste(con_alfa, mask=con_alfa.getchannel("A"))
    salida = io.BytesIO()
    imagen.convert("RGB").save(salida, format="JPEG", quality=90)
    return salida.getvalue()


class LectorDePrueba:
    AVISO = "MODO DE PRUEBA: no se leyó tu foto; son los datos de la boleta de ejemplo."

    def __init__(self, archivo_json: str | Path):
        self.archivo = Path(archivo_json)
        Boleta.model_validate_json(self.archivo.read_text(encoding="utf-8"))

    async def leer(
        self, foto: bytes | list[bytes] | None, categorias: list[str] | None = None, texto: str | None = None
    ) -> Boleta:
        for imagen in _imagenes(foto):
            preparar_imagen(imagen)
        boleta = Boleta.model_validate_json(self.archivo.read_text(encoding="utf-8"))
        boleta.observaciones = f"{self.AVISO} {boleta.observaciones or ''}".strip()
        return boleta


def _imagenes(foto: bytes | list[bytes] | None) -> list[bytes]:
    if not foto:
        return []
    return list(foto) if isinstance(foto, list) else [foto]


def _pedido(categorias: list[str] | None, texto: str | None = None) -> str:
    pedido = "Lee este comprobante."
    if texto:
        pedido += f"\n\n{texto}"
    if categorias:
        pedido += "\n\nCategorías de la tienda:\n" + "\n".join(f"- {c}" for c in categorias)
    return pedido


def texto_del_pdf(texto: str) -> str:
    return f"Texto extraído del PDF:\n{texto}"


def texto_de_boleta(boleta: Boleta) -> str:
    p = boleta.proveedor
    filas = [
        "Datos exactos del comprobante electrónico (XML):",
        f"Tipo: {boleta.tipo} · Serie y número: {boleta.serie_numero} · Fecha: {boleta.fecha_emision} · "
        f"Moneda: {boleta.moneda}",
        f"Emisor: {p.razon_social} ({p.nombre_comercial or 'sin nombre comercial'}) · RUC {p.ruc} · {p.direccion}",
        f"Cliente: {boleta.cliente_documento} · Precios con IGV: sí",
        "Líneas (código | descripción | cantidad | unidad | precio unitario | descuento | importe):",
    ]
    for n, l in enumerate(boleta.lineas, start=1):
        filas.append(
            f"{n}. {l.codigo or '-'} | {l.descripcion} | {l.cantidad:g} | {l.unidad or '-'} | "
            f"{l.precio_unitario} | {l.descuento_monto or 0} | {l.importe}"
        )
    filas.append(
        f"Descuento global: {boleta.descuento_global} · Otros cargos: {boleta.otros_cargos} · "
        f"Op. gravadas: {boleta.op_gravadas} · IGV: {boleta.igv} · Total: {boleta.total}"
    )
    return "\n".join(filas)


def completar(exacta: Boleta, de_la_ia: Boleta) -> Boleta:
    b = exacta.model_copy(deep=True)
    if len(de_la_ia.lineas) != len(b.lineas):
        return b
    for linea, sugerida in zip(b.lineas, de_la_ia.lineas, strict=True):
        linea.categoria = sugerida.categoria
        linea.nombre_tienda = sugerida.nombre_tienda
    return b


def _error_de_configuracion(exc: openai.BadRequestError) -> bool:
    param = exc.param or ""
    return exc.code in {"unsupported_parameter", "unsupported_value"} or param.startswith(
        ("reasoning", "model", "text", "max_output_tokens")
    )


class _LectorConIA:
    def __init__(
        self, cliente: anthropic.AsyncAnthropic | openai.AsyncOpenAI, modelo: str, esfuerzo: str | None = "high"
    ):
        self.cliente = cliente
        self.modelo = modelo
        self.esfuerzo = esfuerzo
        self.ultimo_uso: tuple[int, int] = (0, 0)


class Lector(_LectorConIA):
    async def leer(
        self, foto: bytes | list[bytes] | None, categorias: list[str] | None = None, texto: str | None = None
    ) -> Boleta:
        self.ultimo_uso = (0, 0)
        contenido: list[dict] = []
        for imagen in _imagenes(foto):
            datos, media_type = preparar_imagen(imagen)
            contenido.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": media_type,
                        "data": base64.standard_b64encode(datos).decode("ascii"),
                    },
                }
            )
        contenido.append({"type": "text", "text": _pedido(categorias, texto)})
        try:
            respuesta = await self.cliente.beta.messages.create(
                model=self.modelo,
                max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
                output_config={
                    "effort": self.esfuerzo,
                    "format": {"type": "json_schema", "schema": esquema_con_categorias(categorias)},
                },
                system=SISTEMA,
                messages=[{"role": "user", "content": contenido}],
            )
        except anthropic.AuthenticationError as exc:
            log.error("Clave de Anthropic rechazada: %s", exc)
            raise LecturaError("La clave de la API de Anthropic no es válida. Avisa al administrador.") from exc
        except anthropic.PermissionDeniedError as exc:
            log.error("Permiso denegado por Anthropic: %s", exc)
            raise LecturaError("La cuenta de Anthropic no tiene permiso para este modelo.") from exc
        except anthropic.BadRequestError as exc:
            log.error("Solicitud rechazada por Anthropic: %s", exc)
            raise LecturaError("La IA no pudo procesar esta imagen. Prueba con otra foto.") from exc
        except (anthropic.RateLimitError, anthropic.APIConnectionError) as exc:
            log.warning("Anthropic no disponible: %s", exc)
            raise LecturaError("La IA está ocupada o sin conexión. Intenta en un minuto.") from exc
        except anthropic.APIStatusError as exc:
            log.error("Error %s de Anthropic: %s", exc.status_code, exc)
            raise LecturaError("La IA falló al leer la boleta. Intenta en un minuto.") from exc

        self.ultimo_uso = (respuesta.usage.input_tokens, respuesta.usage.output_tokens)
        log.info(
            "Lectura %s: modelo=%s stop=%s tokens in=%s out=%s",
            respuesta._request_id,
            respuesta.model,
            respuesta.stop_reason,
            respuesta.usage.input_tokens,
            respuesta.usage.output_tokens,
        )
        if respuesta.stop_reason == "refusal":
            raise LecturaError("La IA no quiso leer esta imagen.")
        if respuesta.stop_reason == "max_tokens":
            raise LecturaError("La lectura salió incompleta. Intenta de nuevo.")
        textos: list[str] = []
        for bloque in respuesta.content:
            if bloque.type == "fallback":
                textos = []
            elif bloque.type == "text":
                textos.append(bloque.text)
        try:
            return Boleta.model_validate_json("".join(textos))
        except ValidationError as exc:
            log.error("JSON inválido de la IA (%s): %s", respuesta._request_id, exc)
            raise LecturaError("La IA no devolvió datos válidos. Intenta de nuevo.") from exc


class LectorOpenAI(_LectorConIA):
    async def leer(
        self, foto: bytes | list[bytes] | None, categorias: list[str] | None = None, texto: str | None = None
    ) -> Boleta:
        self.ultimo_uso = (0, 0)
        contenido: list[dict] = []
        for foto_pagina in _imagenes(foto):
            datos, media_type = preparar_imagen(foto_pagina)
            imagen = f"data:{media_type};base64,{base64.standard_b64encode(datos).decode('ascii')}"
            contenido.append({"type": "input_image", "image_url": imagen, "detail": "high"})
        contenido.append({"type": "input_text", "text": _pedido(categorias, texto)})
        opciones: dict = {}
        if self.esfuerzo:
            opciones["reasoning"] = {"effort": self.esfuerzo}
        try:
            respuesta = await self.cliente.responses.create(
                model=self.modelo,
                instructions=SISTEMA,
                input=[{"role": "user", "content": contenido}],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "boleta",
                        "strict": True,
                        "schema": esquema_con_categorias(categorias),
                    }
                },
                max_output_tokens=16000,
                store=False,
                **opciones,
            )
        except openai.AuthenticationError as exc:
            log.error("Clave de OpenAI rechazada: %s", exc)
            raise LecturaError("La clave de la API de OpenAI no es válida. Avisa al administrador.") from exc
        except openai.PermissionDeniedError as exc:
            log.error("Permiso denegado por OpenAI: %s", exc)
            raise LecturaError("La cuenta de OpenAI no tiene permiso para este modelo.") from exc
        except openai.NotFoundError as exc:
            log.error("Modelo de OpenAI no encontrado: %s", exc)
            raise LecturaError(
                "El modelo de OpenAI configurado no existe o no está habilitado. Avisa al administrador."
            ) from exc
        except openai.BadRequestError as exc:
            log.error("Solicitud rechazada por OpenAI: %s", exc)
            if _error_de_configuracion(exc):
                raise LecturaError(
                    "OpenAI rechazó la configuración del bot (OPENAI_MODELO u OPENAI_ESFUERZO). Avisa al administrador."
                ) from exc
            raise LecturaError("La IA no pudo procesar esta imagen. Prueba con otra foto.") from exc
        except openai.RateLimitError as exc:
            if exc.type == "insufficient_quota" or exc.code in {"insufficient_quota", "credit_balance_exhausted"}:
                log.error("OpenAI sin saldo: %s", exc)
                raise LecturaError(
                    "La cuenta de OpenAI no tiene saldo para este modelo. Avisa al administrador."
                ) from exc
            log.warning("OpenAI ocupado: %s", exc)
            raise LecturaError("La IA está ocupada. Intenta en un minuto.") from exc
        except openai.APIConnectionError as exc:
            log.warning("OpenAI no disponible: %s", exc)
            raise LecturaError("La IA está ocupada o sin conexión. Intenta en un minuto.") from exc
        except openai.APIStatusError as exc:
            log.error("Error %s de OpenAI: %s", exc.status_code, exc)
            raise LecturaError("La IA falló al leer la boleta. Intenta en un minuto.") from exc

        uso = respuesta.usage
        self.ultimo_uso = (uso.input_tokens, uso.output_tokens) if uso else (0, 0)
        log.info(
            "Lectura %s: modelo=%s estado=%s tokens in=%s out=%s",
            respuesta.id,
            respuesta.model,
            respuesta.status,
            *self.ultimo_uso,
        )

        rechazo = any(
            getattr(parte, "type", None) == "refusal"
            for item in respuesta.output
            if getattr(item, "type", None) == "message"
            for parte in item.content
        )
        motivo = respuesta.incomplete_details.reason if respuesta.incomplete_details else None
        if rechazo or motivo == "content_filter":
            raise LecturaError("La IA no quiso leer esta imagen.")
        if respuesta.status == "incomplete":
            raise LecturaError("La lectura salió incompleta. Intenta de nuevo.")
        if respuesta.status != "completed":
            log.error("Respuesta de OpenAI en estado %s: %s", respuesta.status, respuesta.error)
            raise LecturaError("La IA falló al leer la boleta. Intenta en un minuto.")
        try:
            return Boleta.model_validate_json(respuesta.output_text)
        except ValidationError as exc:
            log.error("JSON inválido de la IA (%s): %s", respuesta.id, exc)
            raise LecturaError("La IA no devolvió datos válidos. Intenta de nuevo.") from exc


def crear_lector(ia: str, modelo: str, esfuerzo: str | None, timeout: float = 180.0) -> Lector | LectorOpenAI:
    if ia == "openai":
        return LectorOpenAI(openai.AsyncOpenAI(max_retries=3, timeout=timeout), modelo, esfuerzo)
    return Lector(anthropic.AsyncAnthropic(max_retries=3, timeout=timeout), modelo, esfuerzo or "high")
