from __future__ import annotations

import datetime as dt
from html import escape

from .registro import Plan, Resultado, nombre_producto
from .validacion import Problema


def _s(monto: float | None) -> str:
    return "—" if monto is None else f"S/ {monto:,.2f}"


def _fecha(iso: str | None) -> str:
    try:
        return dt.date.fromisoformat(iso or "").strftime("%d/%m/%Y")
    except ValueError:
        return escape(iso or "—")


def resumen(plan: Plan, problemas: list[Problema]) -> str:
    b = plan.boleta
    p = b.proveedor
    tipo = {"boleta": "Boleta", "factura": "Factura", "ticket": "Ticket",
            "nota_de_venta": "Nota de venta", "otro": "Comprobante"}[b.tipo]
    proveedor = escape(p.razon_social or p.nombre_comercial or "—")
    if p.nombre_comercial and p.razon_social and p.nombre_comercial != p.razon_social:
        proveedor += f" ({escape(p.nombre_comercial)})"
    estado_prov = "ya existe en Odoo" if plan.proveedor_id else "🆕 se creará"

    filas = [
        f"<b>{tipo} {escape(b.serie_numero or '—')}</b> · {_fecha(b.fecha_emision)}",
        f"Proveedor: {proveedor}",
        f"RUC {escape(p.ruc or '—')} · {estado_prov}",
        "",
    ]
    for lp in plan.lineas:
        l = lp.linea
        marca = "🆕" if lp.producto_id is None else "✔"
        cortado = " ✂️" if l.nombre_cortado else ""
        nombre = escape(lp.producto_nombre or nombre_producto(l))
        descuento = f" −{l.descuento_pct:g}%" if l.descuento_pct else ""
        fila = (
            f"{lp.numero}. {marca} {nombre}{cortado}\n"
            f"    {l.cantidad:g} × {l.precio_unitario:.2f}{descuento} = <b>{l.importe:.2f}</b>"
            f" · costo c/u {lp.costo_unitario:.2f}"
        )
        if lp.producto_id is None:
            categoria = escape(lp.categoria_nombre) if lp.categoria_nombre else "sin categoría"
            venta = f"venta {lp.precio_venta:.2f}" if lp.precio_venta is not None else "sin precio de venta"
            fila += f"\n    📁 {categoria} · {venta}"
        filas.append(fila)
    filas += ["", f"<b>Total: {_s(b.total)}</b>"]
    if plan.con_credito_fiscal:
        filas.append("Factura a nombre de la empresa: el IGV se registra como crédito fiscal.")
    else:
        filas.append("El IGV va incluido en el costo (no da crédito fiscal).")

    errores = [x for x in problemas if x.nivel == "error"]
    avisos = [x for x in problemas if x.nivel == "aviso"] + [Problema("aviso", a) for a in plan.avisos]
    if errores:
        filas += ["", "<b>❌ Hay que corregir antes de registrar:</b>"]
        filas += [f"• {escape(x.mensaje)}" for x in errores]
    if avisos:
        filas += ["", "<b>⚠️ Revisa:</b>"]
        filas += [f"• {escape(x.mensaje)}" for x in avisos]
    if any(lp.linea.nombre_cortado for lp in plan.lineas):
        filas += ["", "✂️ = nombre cortado. Puedes completarlo con «Corregir», por ejemplo "
                      "<code>3 nombre SORBETE ACERO INOX RECTO Y OBLICUO</code>, o registrarlo así."]
    if plan.orden and not plan.ya_registrada:
        filas += ["", f"Esta boleta quedó a medias en la compra {escape(plan.orden['name'])}. "
                      "«Registrar» completa lo que falta."]
    if plan.ya_registrada:
        filas += ["", f"✅ Esta boleta ya está registrada en la compra {escape(plan.orden['name'])}."]
    filas += ["", "🆕 = producto nuevo · ✔ = ya existe en Odoo · 📁 = categoría elegida por la IA "
                  "(cámbiala con «Corregir», por ejemplo <code>3 categoria cocina</code>)"]
    return "\n".join(filas)


def categorias(nombres: list[str]) -> str:
    if not nombres:
        return "No encontré categorías en Odoo."
    return "<b>Categorías de la tienda</b>\n" + "\n".join(f"• {escape(n)}" for n in nombres)


def registrado(r: Resultado) -> str:
    filas = ["✅ <b>Boleta registrada en Odoo</b>", f"Compra: {escape(r.orden)}"]
    if r.recepciones:
        filas.append(f"Recepción hecha (stock sumado): {escape(', '.join(r.recepciones))}")
    if r.factura:
        filas.append(f"Factura de proveedor en borrador: {escape(r.factura)}, con la foto adjunta")
    if r.proveedor_nuevo:
        filas.append("Proveedor nuevo creado.")
    if r.productos_nuevos:
        filas.append(f"Productos nuevos ({len(r.productos_nuevos)}):")
        filas += [f"• {escape(n)}" for n in r.productos_nuevos]
    for a in r.avisos:
        filas.append(f"⚠️ {escape(a)}")
    return "\n".join(filas)
