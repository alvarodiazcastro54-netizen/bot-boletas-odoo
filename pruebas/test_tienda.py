from boletas.tienda import Tienda, normalizar


class OdooTienda:
    def __init__(self, con_web=True):
        self.con_web = con_web
        self.escrito = []
        self.consultas = []

    def campos(self, modelo):
        return {"public_categ_ids", "is_published"} if self.con_web else set()

    def buscar_leer(self, modelo, dominio, campos, limite=None, orden=None, contexto=None):
        self.consultas.append((modelo, dominio))
        if modelo == "product.public.category":
            return [
                {"id": 1, "name": "Accesorios", "parent_id": False},
                {"id": 2, "name": "Carteras, Billeteras y Mochilas", "parent_id": [1, "Accesorios"]},
                {"id": 3, "name": "Ganchos y Colletes", "parent_id": [1, "Accesorios"]},
                {"id": 4, "name": "Juguetería y TCG", "parent_id": False},
            ]
        if modelo == "ir.attachment":
            return [{"res_id": 10}, {"res_id": 11}]
        if modelo == "product.template":
            return [{"id": 10, "name": "Cartera"}]
        return []

    def leer(self, modelo, ids, campos):
        filas = {
            10: {
                "id": 10,
                "name": "Cartera negra",
                "list_price": 65.0,
                "is_published": False,
                "website_url": "/shop/cartera-negra-10",
            },
            11: {
                "id": 11,
                "name": "Gancho",
                "list_price": 0.0,
                "is_published": False,
                "website_url": "/shop/gancho-11",
            },
            12: {
                "id": 12,
                "name": "Peluche",
                "list_price": 25.0,
                "is_published": False,
                "website_url": "/shop/peluche-12",
            },
        }
        return [filas[i] for i in ids]

    def escribir(self, modelo, ids, valores):
        self.escrito.append((modelo, ids, valores))


def test_normaliza_tildes_y_mayusculas():
    assert normalizar("Juguetería  y TCG / Peluches") == "jugueteria y tcg / peluches"


def test_categoria_web_igual_a_la_interna():
    tienda = Tienda(OdooTienda())
    assert tienda.categoria_web("Accesorios / Carteras, Billeteras y Mochilas") == 2
    assert tienda.categoria_web("Jugueteria y TCG") == 4


def test_categoria_web_por_el_ultimo_nombre_o_el_padre():
    tienda = Tienda(OdooTienda())
    assert tienda.categoria_web("Bisutería / Ganchos y Colletes") == 3
    assert tienda.categoria_web("Accesorios / Relojes") == 1
    assert tienda.categoria_web("Electrónica") is None and tienda.categoria_web(None) is None


def test_sin_tienda_web_no_hay_categoria():
    assert Tienda(OdooTienda(con_web=False)).categoria_web("Accesorios") is None


def test_solo_publica_lo_que_tiene_foto_y_precio():
    odoo = OdooTienda()
    publicados, pendientes = Tienda(odoo, "https://tienda.ejemplo.pe/").publicar([10, 11, 12])
    assert [e.plantilla_id for e in publicados] == [10]
    assert publicados[0].url == "https://tienda.ejemplo.pe/shop/cartera-negra-10"
    assert {e.plantilla_id: e.faltan for e in pendientes} == {11: ["precio"], 12: ["foto"]}
    assert odoo.escrito == [("product.template", [10], {"is_published": True})]


def test_sin_url_de_la_tienda_no_inventa_enlaces():
    publicados, _ = Tienda(OdooTienda()).publicar([10])
    assert publicados[0].url is None


def test_busca_por_todas_las_palabras():
    odoo = OdooTienda()
    Tienda(odoo).buscar("cartera  hypnotic x")
    modelo, dominio = odoo.consultas[-1]
    assert modelo == "product.template"
    assert dominio == [("name", "ilike", "cartera"), ("name", "ilike", "hypnotic")]
