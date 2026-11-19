from __future__ import annotations

import datetime as dt
from html import escape

from .registro import Plan, Resultado, nombre_producto, referencia, sin_ruc
from .tienda import EstadoProducto
from .validacion import Problema

TIPOS = {
    "boleta": "Boleta",
    "factura": "Factura",
    "ticket": "Ticket",
    "nota_de_venta": "Nota de venta",
    "cotizacion": "Cotización",
    "liquidacion_de_compra": "Liquidación de compra",
    "nota_de_credito": "Nota de crédito",
    "nota_de_debito": "Nota de débito",
    "guia_de_remision": "Guía de remisión",
    "recibo_por_honorarios": "Recibo por honorarios",
    "otro": "Comprobante",
}
ORIGENES = {
    "xml": "📄 Datos tomados del XML del comprobante electrónico (exactos).",
    "pdf": "📄 Leído del PDF.",
    "album": "🖼 Leí las fotos del álbum juntas, como un solo comprobante.",
}


def _s(monto: float | None) -> str:
    return "—" if monto is None else f"S/ {monto:,.2f}"


def _fecha(iso: str | None) -> str:
    try:
        return dt.date.fromisoformat(iso or "").strftime("%d/%m/%Y")
    except ValueError:
        return escape(iso or "—")


def _proveedor(plan: Plan) -> list[str]:
    p = plan.boleta.proveedor
    nombre = escape(p.razon_social or p.nombre_comercial or "—")
    if p.nombre_comercial and p.razon_social and p.nombre_comercial != p.razon_social:
        nombre += f" ({escape(p.nombre_comercial)})"
    estado = "ya existe en Odoo" if plan.proveedor_id else "🆕 se creará"
    ruc = f"RUC {escape(p.ruc)}" if p.ruc else (f"DNI {escape(p.dni)}" if getattr(p, "dni", None) else "sin RUC")
    return [f"Proveedor: {nombre}", f"{ruc} · {estado}"]


def _linea(lp) -> str:
    l = lp.linea
    marca = "🆕" if lp.producto_id is None else "✔"
    cortado = " ✂️" if l.nombre_cortado else ""
    descuento = f" −{l.descuento_pct:g}%" if l.descuento_pct else ""
    if lp.producto_id is not None:
        nombre = escape(lp.producto_nombre or nombre_producto(l))
        return (
            f"{lp.numero}. {marca} {nombre}{cortado}\n"
            f"    {l.cantidad:g} × {l.precio_unitario:.2f}{descuento} = <b>{l.importe:.2f}</b>"
        )
    nombre = escape(lp.nombre_tienda or nombre_producto(l))
    fila = f"{lp.numero}. {marca} <b>{nombre}</b>{cortado}"
    if lp.nombre_tienda and lp.nombre_tienda != nombre_producto(l):
        fila += f"\n    en la boleta: {escape(l.descripcion)}"
    fila += f"\n    {l.cantidad:g} × {l.precio_unitario:.2f}{descuento} = {l.importe:.2f}"
    if getattr(l, "exonerado", False):
        fila += " · sin IGV"
    categoria = escape(lp.categoria_nombre) if lp.categoria_nombre else "sin categoría"
    venta = f" → venta <b>{_s(lp.precio_venta)}</b>" if lp.precio_venta else " → venta sin precio"
    extranjera = f" (US$ {lp.costo_unitario:,.2f})" if lp.tipo_de_cambio != 1.0 else ""
    fila += f"\n    📁 {categoria} · costo {_s(lp.costo_soles)}{extranjera}{venta}"
    return fila


def resumen(
    plan: Plan,
    problemas: list[Problema],
    origen: str | None = None,
    qr: tuple[bool, list[str]] | None = None,
    ajustes: list[str] | None = None,
) -> str:
    b = plan.boleta
    numero = referencia(b) or b.serie_numero or "—"
    filas = [f"🧾 <b>{TIPOS.get(b.tipo, 'Comprobante')} {escape(numero)}</b> · {_fecha(b.fecha_emision)}"]
    filas += _proveedor(plan)
    if origen in ORIGENES:
        filas.append(ORIGENES[origen])
    if qr and qr[0]:
        if qr[1]:
            filas.append("🔎 Corregí con el QR de SUNAT: " + "; ".join(escape(c) for c in qr[1]) + ".")
        else:
            filas.append("✅ El QR de SUNAT coincide (RUC, número, fecha y total).")
    filas.append("")
    filas += [_linea(lp) for lp in plan.lineas]
    filas += ["", f"<b>Total: {_s(b.total)}</b>"]
    if plan.con_credito_fiscal:
        filas.append("Factura a nombre de la empresa: el IGV se registra como crédito fiscal.")

    errores = [x for x in problemas if x.nivel == "error"] + [Problema("error", e) for e in plan.errores]
    avisos = [x for x in problemas if x.nivel == "aviso"]
    avisos += [Problema("aviso", a) for a in (ajustes or []) + plan.avisos]
    if plan.parecida and plan.parecida.get("exacta", True):
        p = plan.parecida
        avisos.insert(
            0,
            Problema(
                "aviso",
                (
                    f"🔁 Ya registré la compra {p['name']} de este proveedor por "
                    f"S/ {p.get('amount_total') or 0:,.2f} el {_fecha((p.get('date_order') or '')[:10])} "
                    f"({p.get('partner_ref') or 'sin número'}). Si es la misma compra (por ejemplo, primero la nota de "
                    "venta y ahora su boleta), toca «📎 Es la misma compra» para no duplicar el stock."
                ),
            ),
        )
    elif plan.parecida:
        p = plan.parecida
        avisos.insert(
            0,
            Problema(
                "aviso",
                (
                    f"🔁 Ojo: el {_fecha((p.get('date_order') or '')[:10])} registré la compra {p['name']} de "
                    f"este proveedor desde una nota o cotización ({p.get('partner_ref') or 'sin número'}, "
                    f"S/ {p.get('amount_total') or 0:,.2f}), y este comprobante es por {_s(b.total)}. Si es la misma "
                    f"compra con otro monto, no la registres otra vez: corrige la compra {p['name']} en Odoo, o "
                    "cancélala ahí y registra este comprobante."
                ),
            ),
        )
    if errores:
        filas += ["", "<b>❌ Hay que corregir antes de registrar:</b>"]
        filas += [f"• {escape(x.mensaje)}" for x in errores]
    if avisos:
        filas += ["", "<b>⚠️ Revisa:</b>"]
        filas += [f"• {escape(x.mensaje)}" for x in avisos]
    if any(lp.linea.nombre_cortado for lp in plan.lineas):
        filas += ["", "✂️ = nombre cortado en la boleta. Puedes completarlo con «Corregir»."]
    if plan.orden and not plan.ya_registrada:
        filas += [
            "",
            f"Esta boleta quedó a medias en la compra {escape(plan.orden['name'])}. «Registrar» completa lo que falta.",
        ]
    if plan.ya_registrada:
        filas += ["", f"✅ Esta boleta ya está registrada en la compra {escape(plan.orden['name'])}."]
        if sin_ruc(b):
            filas.append(
                "Si es otra nota del mismo puesto, del mismo día y con los mismos montos, ponle un número "
                "para distinguirla, por ejemplo <code>numero 2</code>."
            )
    elif plan.productos_nuevos:
        filas += [
            "",
            "Los precios de venta salen de los márgenes que ya usa UYAY en cada categoría. Para "
            "cambiar uno escribe <code>3 venta 65</code>; para cambiar el nombre en la tienda, "
            "<code>3 tienda Cartera negra</code>.",
        ]
    return "\n".join(filas)


def categorias(nombres: list[str]) -> str:
    if not nombres:
        return "No encontré categorías en Odoo."
    return "<b>Categorías de la tienda</b>\n" + "\n".join(f"• {escape(n)}" for n in nombres)


def recortar(nombre: str, largo: int = 40) -> str:
    return nombre if len(nombre) <= largo else nombre[: largo - 1] + "…"


def boton_sin_foto(producto: dict, largo: int = 40) -> str:
    nombre = recortar(producto["nombre"], largo)
    return f"📷 {nombre} · {producto['serie']}" if producto.get("serie") else f"📷 {nombre}"


def registrado(r: Resultado) -> str:
    filas = ["✅ <b>Registrado en Odoo</b>", f"Compra: {escape(r.orden)}"]
    if r.recepciones:
        filas.append(f"Ingreso al stock: {escape(', '.join(r.recepciones))}")
    if r.factura is not None:
        estado = "confirmada" if r.factura_confirmada else "en borrador"
        numero = escape(r.factura) if r.factura else "todavía sin número"
        filas.append(f"Factura de proveedor {estado}: {numero}, con el comprobante adjunto")
    if r.proveedor_nuevo:
        filas.append("Proveedor nuevo creado.")
    if r.productos:
        filas.append(f"Productos nuevos: {len(r.productos_nuevos)}")
        filas += [f"{n}. {escape(nombre)}" for n, nombre, _ in r.productos]
        filas += [
            "",
            "🌐 ¿Los publico en la tienda? Para publicarse, cada producto necesita su foto: "
            "mándamela con su número (por ejemplo «1»). Si no, quedan solo en Odoo.",
        ]
    elif r.productos_nuevos:
        filas.append(f"Productos nuevos ({len(r.productos_nuevos)}):")
        filas += [f"• {escape(n)}" for n in r.productos_nuevos]
    for a in r.avisos:
        filas.append(f"⚠️ {escape(a)}")
    return "\n".join(filas)


def unido(r: Resultado) -> str:
    estado = "confirmada" if r.factura_confirmada else "en borrador"
    filas = [
        "📎 <b>Uní este comprobante a la compra que ya estaba registrada</b>",
        f"Compra: {escape(r.orden)} · factura de proveedor {estado}: {escape(r.factura or '—')}",
        "No creé otra compra ni sumé stock.",
    ]
    if r.productos:
        filas.append(
            f"📷 Las fotos con número van a los productos de la compra {escape(r.orden)} "
            f"(líneas 1 a {len(r.productos)})."
        )
    filas += [f"⚠️ {escape(a)}" for a in r.avisos]
    return "\n".join(filas)


def _faltan(e: EstadoProducto) -> str:
    return " y ".join({"foto": "la foto", "precio": "el precio", "tienda web": "la tienda web"}[f] for f in e.faltan)


def publicacion(
    publicados: list[EstadoProducto], pendientes: list[EstadoProducto], numeros: dict[int, int] | None = None
) -> str:
    numeros = numeros or {}
    filas = []
    for e in publicados:
        enlace = f"\n    {escape(e.url)}" if e.url else ""
        filas.append(f"🌐 Publicado: {escape(e.nombre)} a {_s(e.precio)}{enlace}")
    for e in pendientes:
        numero = f"{numeros[e.plantilla_id]}. " if e.plantilla_id in numeros else ""
        filas.append(f"⏳ {numero}{escape(e.nombre)}: falta {_faltan(e)}")
    if any("foto" in e.faltan for e in pendientes):
        filas.append("📷 Mándame la foto de cada uno con su número; apenas la tenga, lo publico.")
    if any(e.faltan == ["precio"] for e in pendientes):
        filas.append("💲 Para ponerle precio escribe por ejemplo <code>2 venta 45</code>.")
    return "\n".join(filas) or "No hay nada que publicar."


def por_publicar(estados: list[EstadoProducto], total: int) -> str:
    filas = [f"<b>Productos del bot sin publicar: {total}</b>"]
    if total > len(estados):
        filas.append(f"Te muestro los {len(estados)} más recientes.")
    for e in estados:
        estado = "listo para publicar" if e.listo else f"falta {_faltan(e)}"
        filas.append(f"• {escape(e.nombre)} · {_s(e.precio)} · {estado}")
    filas.append("Toca uno: si está listo lo publico; si le falta la foto, me la mandas.")
    return "\n".join(filas)
