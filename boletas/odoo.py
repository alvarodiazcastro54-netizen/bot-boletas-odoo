from __future__ import annotations

import logging
from typing import Any

import httpx

log = logging.getLogger(__name__)


class OdooError(Exception):
    def __init__(self, mensaje: str, estado: int | None = None, tipo: str | None = None):
        super().__init__(mensaje)
        self.mensaje = mensaje
        self.estado = estado
        self.tipo = tipo


class Odoo:
    def __init__(self, url: str, base: str, clave: str, timeout: float = 60.0,
                 transporte: httpx.BaseTransport | None = None, empresa_id: int | None = None):
        self.empresa_id = empresa_id
        self.contexto = {"allowed_company_ids": [empresa_id]} if empresa_id else {}
        self.http = httpx.Client(
            base_url=url.rstrip("/"),
            headers={
                "Authorization": f"bearer {clave}",
                "X-Odoo-Database": base,
                "Content-Type": "application/json",
                "User-Agent": "bot-boletas",
            },
            timeout=timeout,
            transport=transporte,
        )

    def cerrar(self) -> None:
        self.http.close()

    def llamar(self, modelo: str, metodo: str, **argumentos: Any) -> Any:
        ruta = f"/json/2/{modelo}/{metodo}"
        if self.contexto:
            argumentos["context"] = {**self.contexto, **(argumentos.get("context") or {})}
        try:
            r = self.http.post(ruta, json=argumentos)
        except httpx.HTTPError as exc:
            raise OdooError(f"No pude conectar con Odoo ({exc.__class__.__name__}).") from exc
        if r.status_code == 200:
            return r.json()
        try:
            cuerpo = r.json()
            mensaje = cuerpo.get("message") or r.text
            tipo = cuerpo.get("name")
        except ValueError:
            mensaje, tipo = r.text[:300], None
        log.error("Odoo %s %s.%s: %s", r.status_code, modelo, metodo, mensaje)
        if r.status_code == 401:
            mensaje = "Odoo rechazó la clave de la API (revisa ODOO_CLAVE_API y ODOO_BASE)."
        raise OdooError(mensaje, r.status_code, tipo)

    def buscar_leer(self, modelo: str, dominio: list, campos: list[str], limite: int | None = None,
                    orden: str | None = None, contexto: dict | None = None) -> list[dict]:
        args: dict[str, Any] = {"domain": dominio, "fields": campos}
        if limite is not None:
            args["limit"] = limite
        if orden:
            args["order"] = orden
        if contexto:
            args["context"] = contexto
        return self.llamar(modelo, "search_read", **args)

    def leer(self, modelo: str, ids: list[int], campos: list[str]) -> list[dict]:
        return self.llamar(modelo, "read", ids=ids, fields=campos)

    def crear(self, modelo: str, valores: dict, contexto: dict | None = None) -> int:
        args: dict[str, Any] = {"vals_list": [valores]}
        if contexto:
            args["context"] = contexto
        ids = self.llamar(modelo, "create", **args)
        return ids[0]

    def escribir(self, modelo: str, ids: list[int], valores: dict) -> None:
        self.llamar(modelo, "write", ids=ids, vals=valores)

    def campos(self, modelo: str) -> set[str]:
        return set(self.llamar(modelo, "fields_get", attributes=["type"]))
