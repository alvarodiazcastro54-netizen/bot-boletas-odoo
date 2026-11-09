# Bot de boletas para Odoo

Bot de Telegram que registra en **Odoo 19** las compras hechas a un proveedor. Le mandas la foto de la
boleta o factura de compra y el bot:

1. **Lee la foto** con IA (Claude, de Anthropic) y saca proveedor, RUC, serie, fecha, productos, cantidades,
   precios, descuentos y totales.
2. **Revisa las cuentas**: cantidad × precio − descuento = importe, la suma de las líneas = total, RUC válido
   (dígito verificador), fecha posible. Si algo no cuadra, no deja registrar hasta corregirlo.
3. **Te muestra un resumen** en Telegram con botones: ✅ Registrar, ✏️ Corregir, ❌ Cancelar.
4. Al confirmar, **en Odoo**:
   - busca al proveedor por su RUC o lo crea;
   - busca cada producto por el **código del proveedor** y crea los que faltan, con su costo, su categoría y su
     precio de venta;
   - crea la **orden de compra** (con la serie y número de la boleta como referencia) y la confirma;
   - valida la **recepción**, que suma el **stock**;
   - crea la **factura de proveedor en borrador** con el tipo de documento (03 Boleta, 01 Factura, 12 Ticket),
     la fecha y la foto adjunta.

Si mandas dos veces la misma boleta, avisa que ya está registrada. Si algo falla a la mitad, «Reintentar»
sigue desde donde quedó sin duplicar nada.

## Estado

**Probado:**

- 69 pruebas automáticas. 9 de ellas registran de verdad en un Odoo 19 Community de prueba con la localización
  peruana (`l10n_pe`): proveedor con RUC, productos con su código y costo, compra, recepción con stock, factura
  borrador tipo 03 con la foto, boleta repetida, registro retomado a medias, factura con crédito fiscal,
  categorías y precio de venta.
- El bot en Telegram, de punta a punta, en **modo de prueba**: sin clave de Anthropic, usando la lectura
  revisada a mano de una boleta real (`muestras/`). Registró la compra, el stock y la factura correctamente.

**Falta:**

- Probar la **lectura real con IA**: la empresa todavía no tiene clave de la API de Anthropic.
- Medir la lectura con 10 a 20 boletas reales de tiendas distintas (`evaluar_lectura.py`).
- Instalarlo en AWS, en la instancia EC2 donde corre Odoo (pasos en la sección 5).

## Así se ve en Telegram

```
Boleta B410-00170132 · 05/11/2026
Proveedor: SB TRADING S.R.LTDA (HONG KONG MARKET)
RUC 20101567428 · 🆕 se creará

1. 🆕 SOL SET CORTADOR, CUCHILLO, PELADOR DE FRUTA ABS ✂️
    2 × 22.00 −50% = 22.00 · costo c/u 11.00
    📁 Hogar y Manualidades / Cocina · venta 22.00
...
7. 🆕 LLAVERO METAL
    2 × 6.00 −70% = 3.60 · costo c/u 1.80
    📁 Accesorios / Dijes y Llaveros · venta 3.60

Total: S/ 58.30
El IGV va incluido en el costo (no da crédito fiscal).

⚠️ Revisa:
• Línea 1: el nombre se ve cortado («SOL SET CORTADOR, CUCHILLO, PELADOR DE FRUTA ABS»).

[✅ Registrar]  [✏️ Corregir]  [❌ Cancelar]
```

🆕 producto o proveedor nuevo · ✔ ya existe en Odoo · ✂️ nombre cortado en el papel · 📁 categoría elegida por
la IA.

## Qué necesitas

- Odoo 19 (Community o Enterprise) con **Compras**, **Inventario** y **Facturación**. Para Perú, la
  localización `l10n_pe`.
- Python 3.12, o Docker.
- Un bot de Telegram (gratis).
- Una clave de la API de Anthropic, con saldo. Para probar sin ella existe el modo de prueba.

## 1. Crear el bot de Telegram

1. En Telegram busca **@BotFather**, el que tiene la marca azul de verificado.
2. Envíale `/newbot`.
3. Escribe el nombre que verá la gente, por ejemplo `Boletas de mi tienda`.
4. Escribe el usuario del bot. Tiene que terminar en `bot`, por ejemplo `mitienda_boletas_bot`.
5. BotFather te da un **token** parecido a `123456789:AAH...`. Pégalo en el archivo `.env`:

   ```
   TELEGRAM_TOKEN=123456789:AAH...
   ```

6. Arranca el bot (sección 4 en una PC, sección 5 en AWS) y en Telegram envíale `/id`. Te responde con tu número de usuario. Ponlo en `.env`
   y reinicia el bot. Para varias personas, sepáralos con comas:

   ```
   TELEGRAM_USUARIOS_PERMITIDOS=111111111,222222222
   ```

   Si la lista está vacía, **nadie** puede registrar nada.

El token es como una contraseña: no lo subas a GitHub ni lo mandes por chat. Si se filtró, en @BotFather usa
`/revoke`, elige el bot y copia el token nuevo en `.env`. El `.gitignore` ya deja `.env` fuera del repositorio.

## 2. Clave de Anthropic

1. Entra a la consola de Anthropic (console.anthropic.com) y crea una clave en **API Keys**.
2. Carga saldo en **Billing**.
3. Pégala en `.env`:

   ```
   ANTHROPIC_API_KEY=sk-ant-...
   ```

Costo estimado con el modelo por defecto (Claude Opus 5.5, esfuerzo `high`): entre 5 y 10 centavos de dólar
por boleta. Con `CLAUDE_MODELO=claude-haiku-5-5` costaría menos de 1 centavo, pero hay que medir antes si lee
igual de bien (`evaluar_lectura.py`).

**Sin clave:** con `MODO_PRUEBA=si` el bot no llama a la IA. Cualquier foto devuelve la boleta de ejemplo de
`muestras/`. Sirve para probar Telegram y Odoo sin gastar nada.

## 3. Preparar Odoo

1. Crea un **usuario para el bot**, por ejemplo «Bot boletas», con permisos de Compras (usuario),
   Inventario (usuario) y Facturación (facturación).
2. Si la base tiene **varias empresas**, dale acceso solo a la empresa donde se registran las compras.
   Para saber su número, abre *Ajustes → Usuarios y compañías → Compañías* y entra a la empresa: el número es
   el último de la URL. Ponlo en `ODOO_EMPRESA_ID`.
3. Entra con ese usuario y crea su clave en *Preferencias → Seguridad de la cuenta → Nueva clave de API*.
   Para usuarios que no son administradores, Odoo limita cuántos días dura la clave (se cambia en el grupo de
   permisos). Anota cuándo vence.
4. Llena en `.env` `ODOO_URL`, `ODOO_BASE` (nombre de la base de datos) y `ODOO_CLAVE_API`.

Prueba primero en una **copia de la base**: en el gestor de bases de Odoo usa «Duplicar» y marca
«Neutralizar», así no se mandan correos ni se tocan datos reales.

## 4. Probarlo en una PC con Windows

Para probar antes de instalarlo en AWS:

```
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
notepad .env
.venv\Scripts\python -m boletas
```

Al arrancar, el registro dice a qué Odoo y a qué empresa se conectó. Si no puede conectarse, se detiene y dice
por qué.

## 5. Instalarlo en AWS (en la misma instancia EC2 de Odoo)

La idea es que el bot corra **en la misma instancia EC2 donde ya está Odoo**, dentro de Docker. Así:

- **No cuesta más en AWS:** usa la misma máquina. Solo se paga el uso de la API de Anthropic.
- **No hay que abrir ningún puerto:** el bot sale a internet para hablar con Telegram y Anthropic (HTTPS, 443) y
  habla con Odoo por dentro de la máquina (`localhost`). No necesita reglas de entrada en el grupo de seguridad.
- **La clave de Odoo no viaja por internet:** va de la instancia a sí misma.

Esta parte todavía no se probó en la instancia real; los pasos son para Ubuntu (lo más común en EC2).

### 5.1 Entrar a la instancia

En la consola de AWS: *EC2 → Instancias →* elige la instancia de Odoo *→ Conectar → EC2 Instance Connect →
Conectar*. Se abre una terminal en el navegador. También sirve SSH con la llave `.pem`:

```
ssh -i mi-llave.pem ubuntu@IP_DE_LA_INSTANCIA
```

### 5.2 Revisar memoria y agregar swap

El bot usa unos 100 a 150 MB de RAM. En instancias chicas (por ejemplo una t3a.small de 2 GB, donde también
corren Odoo y PostgreSQL) conviene revisar la memoria y, si no hay swap, agregar 2 GB:

```
free -h
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 5.3 Instalar Docker

En Ubuntu:

```
sudo apt update
sudo apt install -y docker.io docker-compose-v2 git
sudo systemctl enable --now docker
```

En Amazon Linux 2023 se instala con `sudo dnf install -y docker git` y el complemento de Compose según la
documentación de Docker.

Si `docker compose version` responde, está listo.

### 5.4 Descargar el bot y configurarlo

```
cd ~
git clone https://github.com/alvarodiazcastro54-netizen/bot-boletas-odoo.git
cd bot-boletas-odoo
cp .env.example .env
chmod 600 .env
nano .env
```

En `nano` llena como mínimo:

```
TELEGRAM_TOKEN=el token de @BotFather
TELEGRAM_USUARIOS_PERMITIDOS=los números de /id
ANTHROPIC_API_KEY=sk-ant-...
ODOO_URL=http://localhost:8069
ODOO_BASE=nombre de la base de datos
ODOO_CLAVE_API=la clave del usuario del bot
ODOO_EMPRESA_ID=el número de la empresa de las compras
```

Guarda con `Ctrl+O`, `Enter`, y sal con `Ctrl+X`. `chmod 600` hace que solo tu usuario pueda leer el archivo.

`ODOO_URL=http://localhost:8069` funciona si Odoo está instalado directamente en la instancia o en Docker con el
puerto 8069 publicado, porque el bot usa `network_mode: host` (ve los puertos de la propia máquina). Si Odoo
usa otro puerto, cámbialo ahí.

Para la primera vez se recomienda `ODOO_BASE` apuntando a una **copia neutralizada** de la base, y recién
después a la base real.

### 5.5 Arrancarlo

```
sudo docker compose up -d --build
sudo docker compose logs -f
```

En el registro debe aparecer «Conectado a Odoo … Las compras se registran en «…»» y «Bot en marcha». Sal del
registro con `Ctrl+C` (el bot sigue corriendo).

El bot se reinicia solo si falla o si se reinicia la instancia (`restart: unless-stopped` y Docker habilitado
al arranque).

### 5.6 Uso diario en el servidor

| Para | Comando (dentro de `~/bot-boletas-odoo`) |
|---|---|
| Ver el registro | `sudo docker compose logs -f --tail 100` |
| Reiniciar (por ejemplo, después de cambiar el `.env`) | `sudo docker compose restart` |
| Detener | `sudo docker compose down` |
| Actualizar a la última versión de GitHub | `git pull && sudo docker compose up -d --build` |
| Ver cuánta memoria usa | `sudo docker stats --no-stream` |

### 5.7 Si algo falla

| Mensaje en el registro | Qué revisar |
|---|---|
| `Falta la variable …` | Esa línea del `.env` está vacía |
| `Odoo rechazó la clave de la API` | `ODOO_CLAVE_API` o `ODOO_BASE` mal escritos, o la clave venció |
| `El usuario del bot no tiene acceso a la empresa …` | En Odoo, dar acceso a esa empresa al usuario del bot, o corregir `ODOO_EMPRESA_ID` |
| `No pude conectar con Odoo (ConnectError)` | Odoo no responde en `ODOO_URL`: revisa el puerto con `curl http://localhost:8069/web/health` |
| El bot no contesta en Telegram | Token mal copiado, o tu número no está en `TELEGRAM_USUARIOS_PERMITIDOS` (escríbele `/id`) |
| `La IA está ocupada o sin conexión` | Saldo de Anthropic agotado o sin salida a internet; revisa Billing en la consola de Anthropic |

### 5.8 Seguridad en AWS

- El bot **no necesita puertos de entrada**: no abras ninguno para él.
- El grupo de seguridad de la instancia solo debería dejar entrar 80 y 443 (y SSH solo desde tu IP o usar
  EC2 Instance Connect). Nunca abrir PostgreSQL (5432) a internet.
- En `odoo.conf`, `list_db = False` y una `admin_passwd` larga, para que el gestor de bases no quede público.
- Odoo detrás de HTTPS (nginx + Let's Encrypt).
- Instantáneas automáticas del disco (*EC2 → Elastic Block Store → Administrador del ciclo de vida*) y copias de
  la base de datos, porque el bot escribe en ella.
- Una IP elástica para que la dirección de Odoo no cambie si la instancia se detiene.

## Configuración (`.env`)

| Variable | Para qué | Ejemplo |
|---|---|---|
| `TELEGRAM_TOKEN` | Token de @BotFather | `123456789:AAH...` |
| `TELEGRAM_USUARIOS_PERMITIDOS` | Quiénes pueden usarlo (números de `/id`) | `111111111,222222222` |
| `ANTHROPIC_API_KEY` | Clave para leer las fotos | `sk-ant-...` |
| `MODO_PRUEBA` | `si` = no usa la IA (boleta de ejemplo) | `no` |
| `CLAUDE_MODELO` | Modelo que lee las fotos | `claude-opus-5-5` |
| `CLAUDE_ESFUERZO` | `low`, `medium`, `high`, `xhigh` o `max` | `high` |
| `ODOO_URL` | Dirección de Odoo | `http://localhost:8069` |
| `ODOO_BASE` | Nombre de la base de datos | `mi_base` |
| `ODOO_CLAVE_API` | Clave de API del usuario del bot | |
| `ODOO_EMPRESA_ID` | Empresa donde se registra (si hay varias) | `2` |
| `MARGEN_VENTA` | Margen del precio de venta (1.0 = 100 %) | `1.0` |
| `ODOO_CATEGORIA_ID` | Categoría si la IA no elige ninguna | vacío |
| `RUC_EMPRESA` | RUC de la empresa (vacío = lo lee de Odoo) | vacío |
| `USA_CREDITO_FISCAL` | `si` = las facturas a nombre de la empresa usan el IGV como crédito fiscal | `no` |

## Decisiones tomadas a criterio

Estas reglas las decidimos nosotros para que el bot funcione, **sin confirmarlas todavía con la empresa** ni con
su contador. Revísenlas antes de usarlo en serio.

| Tema | Qué hace hoy | Por qué | Dónde cambiarlo |
|---|---|---|---|
| **Precio de venta** | Costo × 2 (margen 100 %), redondeado hacia arriba a los 10 céntimos (3.63 → 3.70) | Es el margen habitual en accesorios y regalos; cubre transporte, IGV no recuperado y mermas. Como esas compras suelen tener 50–70 % de descuento, × 2 queda cerca del precio normal del proveedor | `MARGEN_VENTA` en `.env` (0.5 = 50 %). El redondeo está en `precio_de_venta()`, `boletas/registro.py` línea 32 |
| **Sin margen** | El precio de venta queda en 0 | Así se nota que falta (Odoo pondría 1.00, que parece real) | `_crear_producto()`, `boletas/registro.py` línea 358 |
| **Crédito fiscal** | Desactivado: el IGV de toda compra va al costo | Si la empresa estuviera en el Nuevo RUS y se tomara crédito fiscal, el registro sería incorrecto. Lo contrario solo obliga al contador a agregar el IGV en la factura borrador | `USA_CREDITO_FISCAL=si` si la Consulta RUC de SUNAT dice que no está en el Nuevo RUS |
| **IGV de las boletas** | Las líneas van sin impuesto y con el precio de la boleta | Una boleta no da derecho a crédito fiscal | `_precios()`, `boletas/registro.py` línea 209 |
| **Costo del producto** | El precio con su descuento (S/ 11.00 al 50 % → S/ 5.50) | Es lo que realmente se pagó | `_precios()`, `boletas/registro.py` línea 209 |
| **Categoría** | La IA elige una de las categorías finales de Odoo (sin subcategorías) | Que los productos entren ordenados a la tienda | Se cambia en Telegram con `3 categoria cocina`. Las instrucciones a la IA están en `SISTEMA`, `boletas/lectura.py` línea 31; la lista, en `categorias()`, `boletas/registro.py` línea 96 |
| **Nombre del producto** | El de la boleta, en mayúsculas, sin la unidad pegada al final («LLAVERO METAL UND» → «LLAVERO METAL») | No inventar nombres | `_UNIDAD_AL_FINAL`, `boletas/registro.py` línea 17 |
| **Nombres cortados** | Se marcan con ✂️; se pueden completar o registrar así | La impresora corta los nombres largos | Se corrigen en Telegram con `3 nombre ...` |
| **Productos repetidos** | Se reconocen por el código del proveedor (o, si no tiene, por el nombre exacto de la boleta) | El nombre cambia entre tiendas; el código no | `_productos_existentes()`, `boletas/registro.py` |
| **Factura de proveedor** | Queda en **borrador** | Que la revise quien lleva la contabilidad | `_facturar()`, `boletas/registro.py` línea 446 |
| **Publicación en la web** | Los productos nuevos quedan sin publicar | Les falta foto y revisión | Se publican desde Odoo |
| **Hora de la compra** | 12:00 de Lima (17:00 UTC) del día de la boleta | La boleta trae la fecha; Odoo guarda en UTC | `boletas/registro.py` líneas 395 y 400 |
| **ICBPER y redondeos** | Van como línea aparte en la factura, no en el costo | No son productos | `_facturar()`, `boletas/registro.py` |
| **Descuento global** | Se reparte entre los productos | Baja su costo real | `_precios()`, `boletas/registro.py` línea 209 |
| **Tolerancia de las cuentas** | Diferencias de hasta S/ 0.02 se aceptan | Redondeos normales de las boletas | `TOLERANCIA`, `boletas/validacion.py` línea 10 |
| **Tipo de documento** | boleta → 03, factura → 01, ticket → 12 | Tabla 10 de SUNAT | `CODIGO_DOCUMENTO`, `boletas/registro.py` línea 16 |
| **Modelo de IA** | Claude Opus 5.5, esfuerzo `high`, con respaldo automático si el modelo se niega a leer | Prioridad: leer bien los números | `CLAUDE_MODELO`, `CLAUDE_ESFUERZO`; el respaldo es `fallbacks`, `boletas/lectura.py` línea 118 |
| **Tamaño de la foto** | Se achica a 2400 px si es más grande | Límite de la API | `MAX_LADO`, `boletas/lectura.py` línea 29 |

Los números de línea son los de esta versión; si cambian, busca el nombre de la función.

## Recomendaciones para la empresa

1. **Probar con 10 a 20 boletas reales** antes de confiar en el bot: guarda cada foto en `muestras/` con su
   `.esperado.json` escrito a mano y corre `evaluar_lectura.py`. Incluye boletas de varias tiendas y, si hay,
   alguna escrita a mano.
2. **Usar una copia neutralizada de la base** para las primeras pruebas, nunca la base real.
3. **Darle al bot su propio usuario de Odoo**, con acceso solo a la empresa de las compras y solo a Compras,
   Inventario y Facturación.
4. **Confirmar el régimen tributario** (Consulta RUC de SUNAT) y ajustar `USA_CREDITO_FISCAL`.
5. **Que el contador revise** las reglas de la tabla anterior y las facturas borrador. Para mercadería suele
   convenir el **costo promedio** en las categorías de productos (en Odoo, el costo estándar no cambia aunque
   se compre más caro o más barato).
6. **Mandar las fotos como archivo** en Telegram (clip → Archivo): llegan sin comprimir. De frente, con luz y sin
   tapar los totales ni el código QR.
7. **Cuidar el servidor de AWS** antes de poner el bot en producción: ver la sección 5.8.

## Comandos del bot

| Comando | Qué hace |
|---|---|
| (foto) | Lee la boleta y muestra el resumen |
| `/ayuda` | Explica cómo usarlo |
| `/categorias` | Lista las categorías de la tienda |
| `/cancelar` | Descarta la boleta en revisión |
| `/id` | Muestra tu número de usuario (funciona aunque no estés autorizado) |

Correcciones (después de ✏️ Corregir), una por mensaje:

```
3 nombre SORBETE ACERO INOX RECTO Y OBLICUO + CEPILLO
3 cantidad 2        3 precio 11        3 descuento 50        3 importe 16.50
3 codigo 5509007    3 categoria cocina                       borrar 3
ruc 20101567428     proveedor SB TRADING SRL     numero B410-00170132
fecha 05/11/2026    total 58.30
```

## Pruebas

```
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest pruebas
```

`pruebas/test_odoo_real.py` necesita un **Odoo 19 de prueba** corriendo en `http://127.0.0.1:8070` con una
base llamada `prueba_bot` (módulos `purchase`, `stock` y `l10n_pe`). La clave va en la variable
`ODOO_PRUEBA_CLAVE`. Si ese Odoo no está, esas pruebas se saltan. Se niegan a correr en una base con otro
nombre, porque escriben datos.

## Estructura

```
boletas/
  __main__.py      arranque (python -m boletas)
  config.py        lee el .env
  bot.py           Telegram: fotos, botones, correcciones
  lectura.py       lectura de la foto con Claude y modo de prueba
  modelos.py       estructura de una boleta (también es el esquema para la IA)
  validacion.py    RUC, cuentas, fechas
  correcciones.py  «3 nombre ...», «borrar 3», etc.
  registro.py      proveedor, productos, compra, recepción y factura en Odoo
  odoo.py          cliente de la API JSON-2 de Odoo 19
  mensajes.py      textos del bot
pruebas/           pruebas automáticas
muestras/          boleta de ejemplo, su lectura revisada y categorías de la tienda
evaluar_lectura.py mide la lectura con fotos reales (gasta API)
```

## Limitaciones conocidas

- La boleta en revisión se guarda en memoria: si el bot se reinicia antes de registrarla, hay que mandar la foto
  otra vez. Lo ya registrado queda en Odoo.
- Una empresa por bot. Para dos empresas, dos bots con distinto `.env`.
- Monedas: soles y dólares (el dólar debe estar activo en Odoo).
- No se ha probado con boletas escritas a mano.
