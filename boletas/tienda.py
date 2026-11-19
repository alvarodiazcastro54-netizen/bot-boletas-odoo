from __future__ import annotations

import time
import unicodedata
from dataclasses import dataclass, field

from .odoo import Odoo

MARCA_DEL_BOT = "Creado por el bot de boletas"
ORIGEN_DEL_BOT = "Bot de boletas"
VIGENCIA = 600


def normalizar(texto: str) -> str:
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto.lower()) if unicodedata.category(c) != "Mn")
    return " ".join(sin_tildes.replace("/", " / ").split())


@dataclass
class EstadoProducto:
    plantilla_id: int
    nombre: str
    precio: float
    publicado: bool
    tiene_foto: bool
    url: str | None = None
    faltan: list[str] = field(default_factory=list)

    @property
    def listo(self) -> bool:
        return not self.faltan


class Tienda:
    def __init__(self, odoo: Odoo, url_tienda: str | None = None):
        self.odoo = odoo
        self.url_tienda = (url_tienda or "").rstrip("/") or None
        self._categorias_web: tuple[float, dict[str, int]] | None = None
        self._campos: dict[str, set[str]] = {}

    def tiene_campo(self, modelo: str, campo: str) -> bool:
        if modelo not in self._campos:
            self._campos[modelo] = self.odoo.campos(modelo)
        return campo in self._campos[modelo]

    def _rutas_web(self) -> dict[str, int]:
        if self._categorias_web and time.monotonic() - self._categorias_web[0] < VIGENCIA:
            return self._categorias_web[1]
        rutas: dict[str, int] = {}
        if self.tiene_campo("product.template", "public_categ_ids"):
            filas = self.odoo.buscar_leer("product.public.category", [], ["id", "name", "parent_id"])
            por_id = {f["id"]: f for f in filas}
            for f in filas:
                partes, actual, vistos = [], f, set()
                while actual and actual["id"] not in vistos:
                    vistos.add(actual["id"])
                    partes.insert(0, actual["name"])
                    actual = por_id.get(actual["parent_id"][0]) if actual["parent_id"] else None
                rutas[normalizar(" / ".join(partes))] = f["id"]
        self._categorias_web = (time.monotonic(), rutas)
        return rutas

    def categoria_web(self, categoria_interna: str | None) -> int | None:
        if not categoria_interna:
            return None
        rutas = self._rutas_web()
        partes = [normalizar(p) for p in categoria_interna.split(" / ")]
        for n in range(len(partes), 0, -1):
            buscada = " / ".join(partes[:n])
            if buscada in rutas:
                return rutas[buscada]
            finales = [i for ruta, i in rutas.items() if ruta.split(" / ")[-1] == partes[n - 1]]
            if len(finales) == 1:
                return finales[0]
        return None

    def _plantillas_de(self, producto_ids: list[int]) -> dict[int, int]:
        if not producto_ids:
            return {}
        filas = self.odoo.leer("product.product", producto_ids, ["product_tmpl_id"])
        return {f["id"]: f["product_tmpl_id"][0] for f in filas if f["product_tmpl_id"]}

    def plantilla_de(self, producto_id: int) -> int | None:
        return self._plantillas_de([producto_id]).get(producto_id)

    def buscar(self, texto: str, limite: int = 10) -> list[dict]:
        palabras = [p for p in texto.split() if len(p) >= 2]
        if not palabras:
            return []
        dominio = [("name", "ilike", p) for p in palabras]
        return self.odoo.buscar_leer("product.template", dominio, ["id", "name"], limite=limite, orden="id desc")

    def del_bot(self, limite: int = 300) -> list[int]:
        marcados = self.odoo.buscar_leer(
            "product.template", [("description", "ilike", MARCA_DEL_BOT)], ["id"], limite=limite, orden="id desc"
        )
        lineas = self.odoo.buscar_leer(
            "purchase.order.line",
            [("order_id.origin", "=", ORIGEN_DEL_BOT)],
            ["product_id"],
            limite=limite * 3,
            orden="id desc",
        )
        plantillas = self._plantillas_de(sorted({l["product_id"][0] for l in lineas if l["product_id"]}))
        ids = {m["id"] for m in marcados} | set(plantillas.values())
        return sorted(ids, reverse=True)[:limite]

    def con_foto(self, plantilla_ids: list[int]) -> set[int]:
        if not plantilla_ids:
            return set()
        adjuntos = self.odoo.buscar_leer(
            "ir.attachment",
            [("res_model", "=", "product.template"), ("res_field", "=", "image_1920"), ("res_id", "in", plantilla_ids)],
            ["res_id"],
        )
        return {a["res_id"] for a in adjuntos}

    def estados(self, plantilla_ids: list[int]) -> list[EstadoProducto]:
        if not plantilla_ids:
            return []
        campos = ["id", "name", "list_price"]
        publicable = self.tiene_campo("product.template", "is_published")
        if publicable:
            campos += ["is_published", "website_url"]
        filas = self.odoo.leer("product.template", plantilla_ids, campos)
        fotos = self.con_foto(plantilla_ids)
        estados = []
        for f in filas:
            e = EstadoProducto(
                f["id"],
                f["name"],
                f["list_price"],
                bool(f.get("is_published")),
                f["id"] in fotos,
                self._url(f.get("website_url")),
            )
            if f["id"] not in fotos:
                e.faltan.append("foto")
            if f["list_price"] <= 0:
                e.faltan.append("precio")
            if not publicable:
                e.faltan.append("tienda web")
            estados.append(e)
        return estados

    def _url(self, ruta: str | None) -> str | None:
        if not ruta:
            return None
        return f"{self.url_tienda}{ruta}" if self.url_tienda else None

    def publicar(self, plantilla_ids: list[int]) -> tuple[list[EstadoProducto], list[EstadoProducto]]:
        estados = self.estados(plantilla_ids)
        listos = [e for e in estados if e.listo and not e.publicado]
        if listos:
            self.odoo.escribir("product.template", [e.plantilla_id for e in listos], {"is_published": True})
            for e in listos:
                e.publicado = True
        ya = [e for e in estados if e.listo and e not in listos]
        pendientes = [e for e in estados if not e.listo]
        return listos + ya, pendientes

    def poner_precio(self, plantilla_id: int, precio: float) -> None:
        self.odoo.escribir("product.template", [plantilla_id], {"list_price": precio})

    def costo_y_categoria(self, plantilla_id: int) -> tuple[float, str | None]:
        fila = self.odoo.leer("product.template", [plantilla_id], ["standard_price", "categ_id"])[0]
        return fila["standard_price"] or 0.0, fila["categ_id"][1] if fila["categ_id"] else None

    def filas_de_margen(self) -> list[tuple[str, float, float]]:
        filas = self.odoo.buscar_leer(
            "product.product",
            [("sale_ok", "=", True), ("list_price", ">", 0)],
            ["categ_id", "list_price", "standard_price"],
        )
        return [(f["categ_id"][1] if f["categ_id"] else "", f["list_price"], f["standard_price"] or 0.0) for f in filas]
