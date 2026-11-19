import datetime as dt
import re

from telegram.ext import Application

from boletas import mensajes
from boletas.bot import BotBoletas, partir
from boletas.registro import Registrador, Resultado
from boletas.validacion import validar


class OdooFalso:
    def buscar_leer(self, modelo, dominio, campos, limite=None, orden=None, contexto=None):
        if modelo == "res.company":
            return [{"id": 2, "name": "UYAY", "vat": "10456789124", "currency_id": [1, "PEN"]}]
        if modelo == "product.category":
            return [
                {"id": 10, "complete_name": "Hogar y Manualidades / Cocina"},
                {"id": 11, "complete_name": "Accesorios / Dijes y Llaveros"},
            ]
        return []

    def llamar(self, modelo, metodo, **argumentos):
        return {str(i): False for i in argumentos.get("ids", [])}

    def campos(self, modelo):
        return set()


def _plan(boleta):
    return Registrador(OdooFalso()).planificar(boleta)


def test_resumen_de_la_boleta_real(boleta_hongkong):
    plan = _plan(boleta_hongkong)
    texto = mensajes.resumen(plan, validar(boleta_hongkong, dt.date(2026, 11, 9)))
    assert "Boleta B410-00170132" in texto and "05/11/2026" in texto
    assert "SB TRADING S.R.LTDA (HONG KONG MARKET)" in texto
    assert texto.count("🆕") >= 8
    assert texto.count("✂️") == 4
    assert "S/ 58.30" in texto
    assert "costo S/ 11.00 → venta <b>S/ 20.00</b>" in texto and "costo S/ 1.80 → venta <b>S/ 3.60</b>" in texto
    assert texto.count("📁 Hogar y Manualidades / Cocina") == 6
    assert re.search(r"📁 Accesorios / Dijes y Llaveros · costo", texto)
    assert "3 venta 65" in texto
    assert "❌" not in texto
    assert "no existe en Odoo" not in texto


def test_resumen_muestra_el_nombre_para_la_tienda_y_el_de_la_boleta(boleta_hongkong):
    boleta_hongkong.lineas[6].nombre_tienda = "Llavero de metal"
    texto = mensajes.resumen(_plan(boleta_hongkong), [])
    assert "<b>Llavero de metal</b>" in texto and "en la boleta: LLAVERO METAL" in texto


def test_resumen_dice_de_donde_salieron_los_datos(boleta_hongkong):
    plan = _plan(boleta_hongkong)
    assert "XML del comprobante electrónico" in mensajes.resumen(plan, [], "xml")
    assert "El QR de SUNAT coincide" in mensajes.resumen(plan, [], "foto", (True, []))
    corregido = mensajes.resumen(plan, [], "foto", (True, ["total 58.30 (se leyó 58.00)"]))
    assert "Corregí con el QR de SUNAT: total 58.30 (se leyó 58.00)." in corregido


def test_resumen_con_margen(boleta_hongkong):
    plan = Registrador(OdooFalso(), margen_venta=1.0).planificar(boleta_hongkong)
    texto = mensajes.resumen(plan, [])
    assert "venta <b>S/ 22.00</b>" in texto
    assert "venta <b>S/ 3.60</b>" in texto


def test_categoria_que_no_existe(boleta_hongkong):
    boleta_hongkong.lineas[0].categoria = "Electrónica"
    plan = _plan(boleta_hongkong)
    assert plan.lineas[0].categoria_id is None
    assert any("Electrónica" in a for a in plan.avisos)


def test_teclado_segun_el_caso(boleta_hongkong):
    plan = _plan(boleta_hongkong)
    ok = BotBoletas.teclado("abc", plan, validar(boleta_hongkong, dt.date(2026, 11, 9)))
    assert ok.inline_keyboard[0][0].callback_data == "reg:abc"
    boleta_hongkong.total = 99
    mal = BotBoletas.teclado("abc", plan, validar(boleta_hongkong, dt.date(2026, 11, 9)))
    datos = [b.callback_data for fila in mal.inline_keyboard for b in fila]
    assert "reg:abc" not in datos and "cor:abc" in datos


def test_html_escapado(boleta_hongkong):
    boleta_hongkong.lineas[0].descripcion = "SET <b>PROMO</b> & CIA"
    texto = mensajes.resumen(_plan(boleta_hongkong), [])
    assert "&lt;b&gt;PROMO&lt;/b&gt; &amp; CIA" in texto


def test_partir_textos_largos():
    texto = "\n".join(f"línea {i} " + "x" * 80 for i in range(200))
    partes = partir(texto)
    assert len(partes) > 1 and all(len(p) <= 4000 for p in partes)
    assert "\n".join(partes) == texto


def test_mensaje_de_registro():
    r = Resultado("P00007", ["WH/IN/00007"], "BOL B410-00170132", ["LLAVERO METAL"], True)
    texto = mensajes.registrado(r)
    assert "P00007" in texto and "WH/IN/00007" in texto and "LLAVERO METAL" in texto
    assert "con el comprobante adjunto" in texto


def test_instala_los_manejadores():
    app = Application.builder().token("123456:prueba").build()
    BotBoletas(lector=None, registrador=None, usuarios=frozenset({1})).instalar(app)
    assert sum(len(h) for h in app.handlers.values()) == 11
