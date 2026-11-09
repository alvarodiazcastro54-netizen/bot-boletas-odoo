from __future__ import annotations

import base64
import copy
import io
import logging
from pathlib import Path

import anthropic
from PIL import Image, ImageOps
from pydantic import ValidationError

from .modelos import Boleta

log = logging.getLogger(__name__)

ESQUEMA = anthropic.transform_schema(Boleta)


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
Lees comprobantes de pago peruanos (boletas, facturas y tickets) a partir de una foto y llenas un JSON.

Reglas:
- Copia los textos tal como están impresos. No corrijas, no completes ni traduzcas las descripciones.
- Si un dato no se ve (borroso, cortado o tapado por un dedo), pon null y explícalo en «observaciones». \
No lo adivines, aunque puedas calcularlo.
- Números con punto decimal, sin «S/». Las cantidades como número.
- En cada línea: «precio_unitario» es el precio impreso antes del descuento, «descuento_pct» el porcentaje \
de descuento (0 si no hay) e «importe» el monto final de la línea. Si el descuento aparece como monto en \
soles, ponlo en «descuento_monto» y deja «descuento_pct» en 0.
- Marca «nombre_cortado» cuando la descripción llega al borde del papel o termina a media palabra.
- «fecha_emision» en formato AAAA-MM-DD. Las boletas peruanas usan día/mes/año.
- «cliente_documento» es el DNI o RUC del comprador impreso en el comprobante.
- ICBPER, redondeos y otros cargos que no son productos van en «otros_cargos», no como líneas.
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


class LectorDePrueba:
    AVISO = "MODO DE PRUEBA: no se leyó tu foto; son los datos de la boleta de ejemplo."

    def __init__(self, archivo_json: str | Path):
        self.archivo = Path(archivo_json)
        Boleta.model_validate_json(self.archivo.read_text(encoding="utf-8"))

    async def leer(self, foto: bytes, categorias: list[str] | None = None) -> Boleta:
        preparar_imagen(foto)
        boleta = Boleta.model_validate_json(self.archivo.read_text(encoding="utf-8"))
        boleta.observaciones = f"{self.AVISO} {boleta.observaciones or ''}".strip()
        return boleta


class Lector:
    def __init__(self, cliente: anthropic.AsyncAnthropic, modelo: str, esfuerzo: str = "high"):
        self.cliente = cliente
        self.modelo = modelo
        self.esfuerzo = esfuerzo
        self.ultimo_uso: tuple[int, int] = (0, 0)

    async def leer(self, foto: bytes, categorias: list[str] | None = None) -> Boleta:
        datos, media_type = preparar_imagen(foto)
        pedido = "Lee este comprobante."
        if categorias:
            pedido += "\n\nCategorías de la tienda:\n" + "\n".join(f"- {c}" for c in categorias)
        contenido = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": media_type,
                    "data": base64.standard_b64encode(datos).decode("ascii"),
                },
            },
            {"type": "text", "text": pedido},
        ]
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
            respuesta._request_id, respuesta.model, respuesta.stop_reason,
            respuesta.usage.input_tokens, respuesta.usage.output_tokens,
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
