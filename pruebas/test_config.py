import pytest

from boletas import config as modulo
from boletas.config import Config, ConfigError

BASE = {
    "TELEGRAM_TOKEN": "t",
    "TELEGRAM_USUARIOS_PERMITIDOS": "1",
    "ODOO_URL": "http://localhost:8069",
    "ODOO_BASE": "b",
    "ODOO_CLAVE_API": "c",
    "IA": "openai",
    "OPENAI_API_KEY": "sk-prueba",
    "OPENAI_MODELO": "",
    "OPENAI_ESFUERZO": "high",
    "CLAUDE_MODELO": "",
    "CLAUDE_ESFUERZO": "",
    "MODO_PRUEBA": "no",
    "MUESTRA_PRUEBA": "",
    "CONFIRMAR_FACTURA": "si",
    "ODOO_PERMITIR_HTTP": "",
}


@pytest.fixture
def entorno(monkeypatch):
    monkeypatch.setattr(modulo, "load_dotenv", lambda: None)
    for nombre, valor in BASE.items():
        monkeypatch.setenv(nombre, valor)

    def poner(**valores):
        for nombre, valor in valores.items():
            monkeypatch.setenv(nombre, valor)
        return Config.desde_entorno()

    return poner


def test_confirma_facturas_fuera_del_modo_de_prueba(entorno):
    c = entorno()
    assert c.confirmar_factura and not c.modo_prueba and c.muestra_prueba is None


def test_por_defecto_sin_credito_fiscal_ni_precio_de_venta(entorno, monkeypatch):
    monkeypatch.delenv("USA_CREDITO_FISCAL", raising=False)
    monkeypatch.delenv("MARGEN_VENTA", raising=False)
    c = entorno()
    assert not c.usa_credito_fiscal and c.margen_venta is None


def test_modo_de_prueba_nunca_confirma_facturas(entorno):
    c = entorno(MODO_PRUEBA="si")
    assert c.modo_prueba and not c.confirmar_factura
    assert c.muestra_prueba == modulo.MUESTRA_POR_DEFECTO


def test_muestra_relativa_a_la_carpeta_del_proyecto(entorno, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    c = entorno(MODO_PRUEBA="si", MUESTRA_PRUEBA=" muestras/boleta-01-hongkong.esperado.json ")
    assert c.muestra_prueba == modulo.RAIZ / "muestras" / "boleta-01-hongkong.esperado.json"


def test_muestra_que_no_existe(entorno):
    with pytest.raises(ConfigError, match="MUESTRA_PRUEBA"):
        entorno(MODO_PRUEBA="si", MUESTRA_PRUEBA="muestras/no-existe.json")


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8069",
        "http://127.0.0.1:8069",
        "http://172.17.0.1:8069",
        "http://10.0.0.5:8069",
        "http://odoo:8069",
        "http://host.docker.internal:8069",
        "http://[::1]:8069",
        "https://8.8.8.8",
        "https://tienda.ejemplo.pe",
    ],
)
def test_url_de_odoo_aceptada(entorno, url):
    assert entorno(ODOO_URL=url).odoo_url == url


@pytest.mark.parametrize("url", ["http://8.8.8.8:8069", "http://tienda.ejemplo.pe"])
def test_http_hacia_internet_no_arranca(entorno, url):
    with pytest.raises(ConfigError, match="sin cifrar"):
        entorno(ODOO_URL=url)
    assert entorno(ODOO_URL=url, ODOO_PERMITIR_HTTP="si").odoo_url == url


@pytest.mark.parametrize("url", ["localhost:8069", "ftp://odoo"])
def test_url_sin_esquema(entorno, url):
    with pytest.raises(ConfigError, match="http:// o https://"):
        entorno(ODOO_URL=url)


@pytest.mark.parametrize("valor,esperado", [("high", "high"), ("NONE", "none"), ("", None), ("minimal", "minimal")])
def test_esfuerzo_de_openai(entorno, valor, esperado):
    assert entorno(OPENAI_ESFUERZO=valor).esfuerzo == esperado


def test_esfuerzo_invalido_lista_las_opciones(entorno):
    with pytest.raises(ConfigError, match="none, minimal, low") as error:
        entorno(OPENAI_ESFUERZO="alto")
    assert "vacío" in str(error.value)
    with pytest.raises(ConfigError, match="CLAUDE_ESFUERZO debe ser low"):
        entorno(IA="claude", ANTHROPIC_API_KEY="sk-ant", CLAUDE_ESFUERZO="minimal")


def test_claude_por_defecto(entorno):
    c = entorno(IA="claude", ANTHROPIC_API_KEY="sk-ant")
    assert (c.ia, c.modelo, c.esfuerzo) == ("anthropic", modulo.MODELO_CLAUDE, "high")
