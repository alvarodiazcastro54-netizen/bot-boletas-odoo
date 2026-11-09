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

## Contenido

- [Estado](#estado)
- [Así se ve en Telegram](#así-se-ve-en-telegram)
- [Qué necesitas](#qué-necesitas)
- [1. Crear el bot de Telegram](#1-crear-el-bot-de-telegram)
- [2. La IA que lee las fotos](#2-la-ia-que-lee-las-fotos)
- [3. Preparar Odoo](#3-preparar-odoo)
- [4. Probarlo en una PC con Windows](#4-probarlo-en-una-pc-con-windows)
- [5. Instalarlo en AWS](#5-instalarlo-en-aws-en-la-misma-instancia-ec2-de-odoo)
- [Configuración (.env)](#configuración-env)
- [Decisiones tomadas a criterio](#decisiones-tomadas-a-criterio)
- [Recomendaciones para la empresa](#recomendaciones-para-la-empresa)
- [Comandos del bot](#comandos-del-bot)
- [Pruebas](#pruebas)
- [Limitaciones conocidas](#limitaciones-conocidas)

## Estado

**Probado:**

- 73 pruebas automáticas. Varias registran de verdad en un **Odoo 19 Community** de prueba con la localización
  peruana (`l10n_pe`): proveedor con RUC, productos con su código y costo, compra, recepción con stock, factura
  borrador tipo 03 con la foto, boleta repetida, registro retomado a medias, factura con crédito fiscal,
  categorías y precio de venta.
- Esas pruebas pasan también con un **usuario de Odoo que no es administrador**, con los permisos de la
  sección 3. Con permisos de «usuario» en Compras falla, como se explica ahí.
- El bot en Telegram, de punta a punta, en **modo de prueba**: sin clave de Anthropic, usando la lectura
  revisada a mano de una boleta real (`muestras/`). Registró la compra, el stock y la factura correctamente.

**Falta:**

- Probar la **lectura real con IA**: la empresa todavía no tiene clave de la API de Anthropic. Qué falta saber y
  cómo medirlo está en la [sección 2](#2-la-ia-que-lee-las-fotos).
- Medir la lectura con 10 a 20 boletas reales de tiendas distintas (`evaluar_lectura.py`).
- Instalarlo en AWS, en la instancia EC2 donde corre Odoo (sección 5). Esos pasos no se han probado todavía en
  la instancia real.
- No se probó con Odoo Enterprise.

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

- **Odoo 19** con **Compras**, **Inventario** y **Facturación**, y para Perú la localización `l10n_pe` con su
  plan contable instalado en la empresa de las compras. Probado en Community.
- Las **categorías de productos** ya creadas en Odoo (la IA elige entre ellas; si no hay, los productos quedan
  sin categoría).
- Python 3.12, o Docker.
- Un bot de Telegram (gratis).
- Una clave de la API de Anthropic, con saldo (sección 2). Para probar sin ella existe el modo de prueba.

## 1. Crear el bot de Telegram

1. En Telegram busca **@BotFather**, el que tiene la marca azul de verificado.
2. Envíale `/newbot`.
3. Escribe el nombre que verá la gente, por ejemplo `Boletas de mi tienda`.
4. Escribe el usuario del bot. Tiene que terminar en `bot`, por ejemplo `mitienda_boletas_bot`.
5. BotFather te da un **token** parecido a `123456789:AAH...`. Pégalo en el archivo `.env`:

   ```
   TELEGRAM_TOKEN=123456789:AAH...
   ```

6. Arranca el bot (sección 4 en una PC, sección 5 en AWS) y en Telegram envíale `/id`. Te responde con tu
   número de usuario. Ponlo en `.env` y **vuelve a arrancar el bot** (en AWS:
   `sudo docker compose up -d --force-recreate`). Para varias personas, sepáralos con comas:

   ```
   TELEGRAM_USUARIOS_PERMITIDOS=111111111,222222222
   ```

   Si la lista está vacía, **nadie** puede registrar nada.

El token es como una contraseña: no lo subas a GitHub ni lo mandes por chat. Si se filtró, en @BotFather usa
`/revoke`, elige el bot, copia el token nuevo en `.env` y vuelve a arrancar el bot. El `.gitignore` ya deja
`.env` fuera del repositorio.

Un mismo token solo puede usarlo **un bot a la vez**. Si dejas corriendo el de prueba en tu PC y arrancas el
del servidor, Telegram rechaza a uno de los dos (error `Conflict` en el registro). Apaga el de la PC antes.

## 2. La IA que lee las fotos

### Qué hace y qué no hace

La IA es **Claude Opus 5.5**, de Anthropic. El bot la usa por internet, a través de la API de Anthropic, solo
para una cosa: **convertir la foto de la boleta en datos**. Le devuelve al bot el proveedor, el RUC, la serie, la
fecha, cada producto con su código, cantidad, precio, descuento e importe, y los totales. También elige la
categoría de cada producto entre las que ya existen en Odoo.

La IA **no toca Odoo ni decide nada**. Después de leer:

1. el bot revisa las cuentas y el RUC por su cuenta (sin IA);
2. muestra el resumen en Telegram;
3. **una persona confirma** con ✅ Registrar. Recién ahí se escribe en Odoo.

La API de Anthropic se paga aparte: una suscripción a Claude (Pro, Max o Team) **no** sirve para esto.

### Estado: todavía no se probó con la IA real

La empresa todavía no tiene clave de la API, así que **nadie ha medido aún qué tan bien lee las boletas
reales**, cuánto cuesta de verdad cada una ni cuánto tarda.

Lo que sí está probado:

- que el bot arma bien el pedido a la API (la foto, las instrucciones, el formato de respuesta, el modelo, el
  respaldo automático). Las pruebas usan una API simulada que no cobra;
- que maneja las fallas: la IA se niega a leer, la respuesta llega cortada, la clave es inválida, no hay saldo
  o no hay conexión. En todos los casos avisa en Telegram y no registra nada;
- todo lo que pasa después de leer, con el **modo de prueba**, que usa la lectura de una boleta real revisada a
  mano.

Por eso, antes de usarlo en serio, hay que **activar la IA y medirla** (los dos pasos siguientes).

### Activarla

1. Entra a la consola de Anthropic (console.anthropic.com) con la cuenta de la empresa.
2. En **API Keys** crea una clave y cópiala: solo se muestra una vez.
3. En **Billing** carga saldo (es prepago). En la misma consola pon un **límite de gasto mensual**.
4. En `.env`:

   ```
   ANTHROPIC_API_KEY=sk-ant-...
   MODO_PRUEBA=no
   ```

5. Vuelve a arrancar el bot y mándale una foto.

### Medir si lee bien, antes de confiar en ella

El script `evaluar_lectura.py` le pasa a la IA cada foto de `muestras/` y compara lo que lee con lo que
**realmente** dice la boleta, escrito a mano.

1. Junta **10 a 20 fotos reales** de boletas de compra: de varias tiendas, alguna arrugada, con poca luz o con
   descuentos, y si hay, alguna escrita a mano.
2. Por cada foto, guarda en `muestras/`:
   - la foto, por ejemplo `boleta-02-tienda.jpeg`;
   - su lectura correcta, `boleta-02-tienda.esperado.json`. Copia `boleta-01-hongkong.esperado.json` y cambia
     los datos por los de la boleta. Lo importante: `precio_unitario` es el precio **antes** del descuento,
     `descuento_pct` el porcentaje, `importe` el monto final de la línea, `nombre_cortado` es `true` si el nombre
     se ve cortado en el papel, y lo que no se ve en la foto va como `null`.
3. Córrelo (gasta API):

   ```
   .venv\Scripts\python evaluar_lectura.py
   ```

   Con `--modelo claude-sonnet-5-5` o `--esfuerzo medium` se prueban otras opciones. Con `--sin-api` se prueba
   el script sin gastar (no mide nada).

4. El resultado se ve así:

   ```
   ✓ boleta-01-hongkong.jpeg
   ✗ boleta-02-tienda.jpeg (el bot lo habría detectado):
       línea 3 cantidad: leyó 2.0, era 3.0
   ✗ boleta-03-mercado.jpeg (EL BOT NO LO HABRÍA DETECTADO):
       línea 1 descripción: «TAZA CERAMICA» ≠ «TAZA CERÁMICA»

   Lecturas perfectas: 18 de 20
   Errores que el bot NO habría detectado: 1
   Costo estimado: US$ 1.2000 en total, US$ 0.0600 por boleta
   ```

   - ✓: leyó todo igual que la boleta.
   - «el bot lo habría detectado»: leyó mal un número, pero las cuentas no cuadran y el bot no habría dejado
     registrar hasta corregirlo.
   - «**EL BOT NO LO HABRÍA DETECTADO**»: el error pasaría si nadie revisa el resumen. Es el número más importante.
   - Las categorías distintas se muestran aparte y no cuentan como error, porque son opinables.

Criterio sugerido para empezar a usarlo: **ningún error de cantidades, precios o totales que el bot no detecte**,
y que los errores restantes sean de letras en los nombres, que se corrigen en Telegram.

### Cuánto cuesta

Anthropic cobra por **tokens** (pedazos de texto o imagen): lo que se le manda (la foto y las instrucciones) y
lo que responde. Estos montos son **estimados** hasta medirlos con `evaluar_lectura.py`, que imprime el costo
real de la prueba:

| Modelo (`CLAUDE_MODELO`) | Precio por millón de tokens (entrada / salida) | Estimado por boleta | 100 boletas al mes |
|---|---|---|---|
| `claude-opus-5-5` (por defecto) | US$ 4 / US$ 20 | 5 a 10 centavos | US$ 5 a 10 |
| `claude-sonnet-5-5` | US$ 2 / US$ 10 | 2 a 5 centavos | US$ 2 a 5 |
| `claude-haiku-5-5` | US$ 0.10 / US$ 0.50 | menos de 1 centavo | menos de US$ 1 |

`CLAUDE_ESFUERZO` también influye: `low` o `medium` piensan menos, cuestan menos y responden más rápido; `high`
(por defecto) lee con más cuidado.

Recomendación: empezar con Opus 5.5 y `high`, medir, y después probar Sonnet y Haiku **con las mismas fotos**.
Cambiar a uno más barato solo si no aparecen errores que el bot no detecte. Un número mal leído termina en el
stock y en la contabilidad, así que no conviene ahorrar a ciegas.

Los precios son los de lista de noviembre de 2026; los vigentes y el gasto real están en la consola de
Anthropic.

### Qué pasa cuando la IA se equivoca

- **Números:** si lee mal una cantidad, un precio o un importe, las cuentas dejan de cuadrar y el bot no deja
  registrar. Se corrige escribiendo, por ejemplo, `3 cantidad 2`, o con el botón 🔄 Leer de nuevo.
- **RUC:** si lee mal un dígito, casi siempre falla el dígito verificador y el bot lo marca.
- **Lo que el bot no puede revisar solo:** letras en el nombre de un producto, el código del proveedor, la
  categoría, o un número de serie mal leído que igual tiene buen formato. Por eso existe el resumen y alguien
  tiene que mirarlo antes de pulsar ✅ Registrar.
- **Serie mal leída:** si lee mal el número de la boleta, el bot no podría avisar si esa boleta ya se registró.
  Revisa el número en el resumen.
- **Lo que no se ve en la foto** (tapado por un dedo, borroso) la IA lo deja vacío en lugar de inventarlo, y el
  bot lo muestra como aviso.

### Respaldo automático

Muy rara vez, un filtro de seguridad de la IA puede negarse a leer una imagen normal. Para ese caso está activado
el respaldo de Anthropic: la misma API reintenta con otro modelo automáticamente, sin hacer nada. Está en
`fallbacks="default"`, `boletas/lectura.py` línea 119.

### Qué se le envía a Anthropic

- La **foto** (achicada si es muy grande), las instrucciones de lectura y los **nombres de las categorías** de
  Odoo.
- **No** se envían datos de Odoo (productos, precios, clientes), ni la clave de Odoo, ni el token de Telegram.
- Según la política de Anthropic para la API, por defecto los datos no se usan para entrenar sus modelos;
  revisen la política vigente en su web. Las boletas de compra casi no traen datos personales, pero tenlo en
  cuenta si alguna trae el DNI o el nombre de una persona.

### Probar sin IA (modo de prueba)

Con `MODO_PRUEBA=si` el bot no llama a la IA ni necesita `ANTHROPIC_API_KEY`. Cualquier foto devuelve la boleta de
ejemplo de `muestras/`, ya revisada a mano. Sirve para probar Telegram y Odoo sin gastar nada.

- Úsalo **solo con una base de prueba**: si lo apuntas a la base real, registra de verdad la boleta de ejemplo.
- Como siempre es la misma boleta, la segunda vez dirá que ya está registrada. Para simular otra compra,
  corrígele el número con `numero B410-00170133`.

## 3. Preparar Odoo

1. Crea un **usuario para el bot**, por ejemplo «Bot boletas», con **estos permisos**:

   | Aplicación | Permiso | Para qué |
   |---|---|---|
   | Compras | **Administrador** | Crear proveedores, sus precios y las órdenes de compra |
   | Productos | **Crear** | Crear productos nuevos |
   | Inventario | Usuario | Validar la recepción |
   | Facturación | Facturación | Crear la factura de proveedor y adjuntar la foto |

   Con «Compras: usuario» **no alcanza**: Odoo no le deja crear proveedores ni productos. El permiso
   «Productos: Crear» en Odoo 19 solo lo trae por defecto el administrador del sistema; hay que marcarlo a
   mano en el usuario.

2. Si la base tiene **varias empresas**, dale acceso solo a la empresa donde se registran las compras.
   Para saber su número, abre *Ajustes → Usuarios y compañías → Compañías* y entra a la empresa: el número es
   el último de la URL. Ponlo en `ODOO_EMPRESA_ID`.
3. Entra con ese usuario y crea su clave en *Preferencias → Seguridad de la cuenta → Nueva clave de API*.

   **Duración de la clave:** para un usuario que no es administrador, Odoo no deja crear claves sin
   vencimiento, y si nadie configuró la duración, el máximo es **1 día**. Antes de crear la clave, un
   administrador debe activar el modo de desarrollador y, en *Ajustes → Usuarios y compañías → Grupos*,
   poner en un grupo del bot el campo **«API Keys maximum duration days»** (por ejemplo 365). Lo más limpio es
   crear un grupo solo para el bot con ese valor. Anota cuándo vence la clave y renuévala antes.

4. Llena en `.env` `ODOO_URL`, `ODOO_BASE` (nombre de la base de datos) y `ODOO_CLAVE_API`.

Además:

- Si en Compras está activada la **doble validación de órdenes**, la compra quedará esperando aprobación y el
  bot avisará; un responsable la aprueba en Odoo y se pulsa «Reintentar».
- Si `odoo.conf` tiene `dbfilter`, debe aceptar el nombre de la base (el bot la indica en cada llamada).
- Prueba primero en una **copia de la base**: en el gestor de bases de Odoo usa «Duplicar» y marca
  «Neutralizar», así no se mandan correos ni se tocan datos reales.

## 4. Probarlo en una PC con Windows

Para probar antes de instalarlo en AWS (necesitas Python 3.12 y Git):

```
git clone https://github.com/alvarodiazcastro54-netizen/bot-boletas-odoo.git
cd bot-boletas-odoo
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
copy .env.example .env
notepad .env
.venv\Scripts\python -m boletas
```

Arráncalo siempre desde la carpeta del proyecto (ahí busca el `.env`). Para detenerlo, `Ctrl+C`; después de
cambiar el `.env`, vuelve a arrancarlo.

Al arrancar, el registro dice a qué Odoo y a qué empresa se conectó. Si no puede conectarse, se detiene y dice
por qué.

Si desde la PC te conectas a un Odoo en internet, que sea por **HTTPS**: con `http://` la clave de la API viaja
sin cifrar.

## 5. Instalarlo en AWS (en la misma instancia EC2 de Odoo)

La idea es que el bot corra **en la misma instancia EC2 donde ya está Odoo**, en su propio contenedor de
Docker (Odoo puede estar instalado directo o también en Docker). Así:

- **No cuesta más en AWS:** usa la misma máquina. Solo se paga el uso de la API de Anthropic.
- **No hay que abrir ningún puerto:** el bot sale a internet para hablar con Telegram y Anthropic (HTTPS, 443) y
  habla con Odoo por dentro de la máquina (`localhost`). No necesita reglas de entrada en el grupo de seguridad.
- **La clave de Odoo no viaja por internet:** va de la instancia a sí misma.

Esta parte todavía no se probó en la instancia real. Los comandos son para Ubuntu (lo más común en EC2); en
Amazon Linux el usuario es `ec2-user` y los paquetes se instalan con `dnf`.

### 5.1 Entrar a la instancia

En la consola de AWS: *EC2 → Instancias →* elige la instancia de Odoo *→ Conectar → EC2 Instance Connect →
Conectar*. Se abre una terminal en el navegador. También sirve SSH con la llave `.pem`:

```
ssh -i mi-llave.pem ubuntu@IP_DE_LA_INSTANCIA
```

EC2 Instance Connect entra por el puerto 22. Si el grupo de seguridad solo deja SSH desde tu IP (lo recomendado
en 5.8), agrega también la lista de prefijos administrada `com.amazonaws.<región>.ec2-instance-connect` o usa
SSH desde tu IP.

### 5.2 Revisar memoria y agregar swap

El bot usa unos 100 a 150 MB de RAM. En instancias chicas (por ejemplo de 2 GB, donde también corren Odoo y
PostgreSQL) conviene revisar la memoria:

```
free -h
```

Si en la fila `Swap` dice `0B`, agrega 2 GB de swap:

```
sudo fallocate -l 2G /swapfile
sudo chmod 600 /swapfile
sudo mkswap /swapfile
sudo swapon /swapfile
echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
```

### 5.3 Docker y Git

**Si Odoo ya corre en Docker, Docker ya está instalado: no lo vuelvas a instalar.** Instalar otro paquete de
Docker encima puede chocar con el que ya está y afectar al contenedor de Odoo. Solo comprueba:

```
sudo docker compose version
```

Si eso falla pero `sudo docker-compose version` responde, la instancia tiene la versión antigua de Compose: usa
`docker-compose` (con guion) en todos los comandos de abajo.

Instala Git si falta:

```
git --version || sudo apt install -y git
```

Solo si la instancia **no** tiene Docker (Odoo instalado directo):

```
sudo apt update
sudo apt install -y docker.io docker-compose-v2
sudo systemctl enable --now docker
```

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
MODO_PRUEBA=no
ODOO_URL=http://localhost:8069
ODOO_BASE=nombre de la base de datos
ODOO_CLAVE_API=la clave del usuario del bot
ODOO_EMPRESA_ID=el número de la empresa de las compras
```

Guarda con `Ctrl+O`, `Enter`, y sal con `Ctrl+X`. `chmod 600` hace que solo tu usuario pueda leer el archivo.

Para la primera vez se recomienda `ODOO_BASE` apuntando a una **copia neutralizada** de la base, y recién
después a la base real.

#### Si Odoo corre en Docker

Primero mira cómo está publicado el contenedor de Odoo:

```
sudo docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Ports}}"
curl -s http://localhost:8069/web/health
```

**Opción A (la de este repositorio):** si en la columna de puertos aparece algo como `0.0.0.0:8069->8069/tcp`
o `127.0.0.1:8069->8069/tcp`, y el `curl` responde `{"status": "pass"}`, deja `ODOO_URL=http://localhost:8069`.
El bot usa `network_mode: host`, así que ve los puertos que la instancia tiene publicados.

**Opción B (por la red de Docker de Odoo):** sirve si el puerto 8069 no está publicado en la instancia, por
ejemplo si más adelante Odoo queda solo detrás de nginx. Busca la red y el nombre del contenedor de Odoo:

```
sudo docker network ls
sudo docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' NOMBRE_DEL_CONTENEDOR_DE_ODOO
```

Cambia `docker-compose.yml` del bot por esto, poniendo el nombre de esa red:

```yaml
services:
  bot-boletas:
    build: .
    env_file: .env
    restart: unless-stopped
    networks:
      - red_de_odoo
    logging:
      driver: json-file
      options:
        max-size: "10m"
        max-file: "3"

networks:
  red_de_odoo:
    external: true
    name: NOMBRE_DE_LA_RED
```

Y en `.env`, `ODOO_URL=http://NOMBRE_DEL_CONTENEDOR_DE_ODOO:8069`.

Ojo: si cambias `docker-compose.yml`, el próximo `git pull` puede chocar con ese cambio. Guárdalo antes con
`git stash` y recupéralo con `git stash pop`.

En los dos casos el bot queda en su propia carpeta y su propio `docker compose`, separado del de Odoo: se puede
actualizar o detener uno sin tocar el otro. No hace falta agregarlo al `docker-compose` de Odoo.

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
| Aplicar cambios del `.env` | `sudo docker compose up -d --force-recreate` |
| Reiniciar sin cambios | `sudo docker compose restart` |
| Detener | `sudo docker compose down` |
| Actualizar a la última versión de GitHub | `git pull && sudo docker compose up -d --build` |
| Ver cuánta memoria usa | `sudo docker stats --no-stream` |

`restart` **no** lee los cambios del `.env`: el contenedor guarda las variables de cuando se creó. Por eso, al
cambiar el `.env`, usa `up -d --force-recreate`.

El bot no guarda datos propios: todo queda en Odoo. Lo único que hay que respaldar es el `.env`.

### 5.7 Si algo falla

| Mensaje en el registro o en Telegram | Qué revisar |
|---|---|
| `Falta la variable …` | Esa línea del `.env` está vacía |
| `Odoo rechazó la clave de la API` | `ODOO_CLAVE_API` o `ODOO_BASE` mal escritos, o la clave venció (sección 3, paso 3) |
| `El usuario del bot no tiene acceso a la empresa …` | En Odoo, dar acceso a esa empresa al usuario del bot, o corregir `ODOO_EMPRESA_ID` |
| `No pude conectar con Odoo (ConnectError)` | Odoo no responde en `ODOO_URL`: revisa el puerto con `curl http://localhost:8069/web/health` y `sudo docker ps`; si Odoo está en Docker sin el puerto publicado, usa la opción B de la sección 5.4 |
| `No puede crear registros 'Contact' (res.partner)` u otro «No puede crear registros …» (en inglés, `You are not allowed to create …`) | Al usuario del bot le faltan permisos (sección 3, paso 1). El mensaje dice qué grupos lo permiten |
| `… necesita aprobación en Odoo` | Doble validación de compras activada: aprobar la compra en Odoo y pulsar «Reintentar» |
| `Odoo pidió confirmar algo al validar la recepción` | El producto pide lote o número de serie, o algo más; validar la recepción a mano en Odoo |
| `La moneda USD no está activa en Odoo` | Activar el dólar en Contabilidad → Configuración → Monedas |
| `Conflict: terminated by other getUpdates request` | Hay dos bots con el mismo token (por ejemplo, el de prueba de tu PC); apaga uno |
| El bot no contesta en Telegram | Token mal copiado, o tu número no está en `TELEGRAM_USUARIOS_PERMITIDOS` (escríbele `/id`) |
| `La IA está ocupada o sin conexión` | Saldo de Anthropic agotado o sin salida a internet; revisa Billing en la consola de Anthropic |
| `La clave de la API de Anthropic no es válida` | `ANTHROPIC_API_KEY` mal copiada o revocada |

### 5.8 Seguridad en AWS

- El bot **no necesita puertos de entrada**: no abras ninguno para él.
- El grupo de seguridad de la instancia solo debería dejar entrar 80 y 443 (y SSH solo desde tu IP o desde
  EC2 Instance Connect). Nunca abrir PostgreSQL (5432) a internet.
- En `odoo.conf`, `list_db = False` y una `admin_passwd` larga, para que el gestor de bases no quede público.
  El bot sigue funcionando así.
- Odoo detrás de HTTPS (nginx + Let's Encrypt).
- Instantáneas automáticas del disco (*EC2 → Elastic Block Store → Administrador del ciclo de vida*) y copias de
  la base de datos, porque el bot escribe en ella.
- Una IP elástica para que la dirección de Odoo no cambie si la instancia se detiene.
- El `.env` tiene tres claves (Telegram, Anthropic y Odoo): no lo copies a otros lados ni lo subas a Git.

## Configuración (`.env`)

| Variable | Para qué | Ejemplo |
|---|---|---|
| `TELEGRAM_TOKEN` | Token de @BotFather | `123456789:AAH...` |
| `TELEGRAM_USUARIOS_PERMITIDOS` | Quiénes pueden usarlo (números de `/id`, separados por comas) | `111111111,222222222` |
| `ANTHROPIC_API_KEY` | Clave para leer las fotos | `sk-ant-...` |
| `MODO_PRUEBA` | `si` = no usa la IA (boleta de ejemplo); solo con base de prueba | `no` |
| `CLAUDE_MODELO` | Modelo que lee las fotos | `claude-opus-5-5` |
| `CLAUDE_ESFUERZO` | `low`, `medium`, `high`, `xhigh` o `max`; más alto lee con más cuidado y cuesta más | `high` |
| `ODOO_URL` | Dirección de Odoo | `http://localhost:8069` |
| `ODOO_BASE` | Nombre de la base de datos | `mi_base` |
| `ODOO_CLAVE_API` | Clave de API del usuario del bot | |
| `ODOO_EMPRESA_ID` | Empresa donde se registra (si hay varias) | `2` |
| `MARGEN_VENTA` | Margen del precio de venta (1.0 = 100 %, 0.5 = 50 %); vacío = precio 0 | `1.0` |
| `ODOO_CATEGORIA_ID` | Categoría (número) si la IA no elige ninguna | vacío |
| `RUC_EMPRESA` | RUC de la empresa (vacío = lo lee de Odoo) | vacío |
| `USA_CREDITO_FISCAL` | `si` = las facturas a nombre de la empresa usan el IGV como crédito fiscal | `no` |

Para `MODO_PRUEBA` y `USA_CREDITO_FISCAL` sirven `si`/`no` (también `true`/`false`, `1`/`0`).

## Decisiones tomadas a criterio

Estas reglas las decidimos nosotros para que el bot funcione, **sin confirmarlas todavía con la empresa** ni con
su contador. Revísenlas antes de usarlo en serio.

| Tema | Qué hace hoy | Por qué | Dónde cambiarlo |
|---|---|---|---|
| **Precio de venta** | Costo × 2 (margen 100 %), redondeado hacia arriba a los 10 céntimos (3.63 → 3.70) | Es el margen habitual en accesorios y regalos; cubre transporte, IGV no recuperado y mermas. Como esas compras suelen tener 50–70 % de descuento, × 2 queda cerca del precio normal del proveedor | `MARGEN_VENTA` en `.env`. El redondeo está en `precio_de_venta()`, `boletas/registro.py` línea 32 |
| **Sin margen** | El precio de venta queda en 0 | Así se nota que falta (Odoo pondría 1.00, que parece real) | `_crear_producto()`, `boletas/registro.py` línea 365 |
| **Crédito fiscal** | Desactivado: el IGV de toda compra va al costo | Si la empresa estuviera en el Nuevo RUS y se tomara crédito fiscal, el registro sería incorrecto. Lo contrario solo obliga al contador a agregar el IGV en la factura borrador | `USA_CREDITO_FISCAL=si` si la Consulta RUC de SUNAT dice que no está en el Nuevo RUS |
| **IGV de las boletas** | Las líneas van sin impuesto y con el precio de la boleta | Una boleta no da derecho a crédito fiscal | `_precios()`, `boletas/registro.py` línea 216 |
| **IGV de las facturas con crédito fiscal** | Precio sin IGV + el impuesto «IGV 18%» de compras de la empresa | Así Odoo registra el crédito fiscal | El impuesto se busca en `impuesto_igv_compra()`, `boletas/registro.py` línea 147 |
| **Costo del producto** | El precio con su descuento (S/ 11.00 al 50 % → S/ 5.50) | Es lo que realmente se pagó | `_precios()`, `boletas/registro.py` línea 216 |
| **Categoría** | La IA elige una de las categorías finales de Odoo (sin subcategorías) | Que los productos entren ordenados a la tienda | Se cambia en Telegram con `3 categoria cocina`. Las instrucciones a la IA están en `SISTEMA`, `boletas/lectura.py` línea 31; la lista, en `categorias()`, `boletas/registro.py` línea 96 |
| **Nombre del producto** | El de la boleta, en mayúsculas, sin la unidad pegada al final («LLAVERO METAL UND» → «LLAVERO METAL») | No inventar nombres | `_UNIDAD_AL_FINAL`, `boletas/registro.py` línea 17 |
| **Nombres cortados** | Se marcan con ✂️; se pueden completar o registrar así | La impresora corta los nombres largos | Se corrigen en Telegram con `3 nombre ...` |
| **Productos repetidos** | Se reconocen por el código del proveedor (o, si no tiene, por el nombre exacto de la boleta) | El nombre cambia entre tiendas; el código no | `_productos_existentes()`, `boletas/registro.py` línea 234 |
| **Productos que ya existen** | No se les cambia el nombre, la categoría ni el precio de venta | No pisar lo que alguien ya ajustó en Odoo | `planificar()` y `registrar()`, `boletas/registro.py` |
| **Unidad de medida** | Los productos nuevos se crean en Unidades | La mayoría de la mercadería se compra por unidad | `_crear_producto()`, `boletas/registro.py` línea 347 |
| **Tipo de producto** | Bien almacenable (lleva stock) | Es mercadería | `_crear_producto()`, `boletas/registro.py` línea 347 |
| **Proveedor nuevo** | Con RUC, razón social, dirección y país Perú; el nombre comercial va en las notas | Datos que trae la boleta | `_crear_proveedor()`, `boletas/registro.py` |
| **Factura de proveedor** | Queda en **borrador** y no se registra el pago | Que la revise y la pague quien lleva la contabilidad | `_facturar()`, `boletas/registro.py` línea 453 |
| **Almacén** | El de la empresa por defecto | Hay un solo almacén | La recepción la crea Odoo al confirmar la compra |
| **Publicación en la web** | Los productos nuevos quedan sin publicar | Les falta foto y revisión | Se publican desde Odoo |
| **Hora de la compra** | 12:00 de Lima (17:00 UTC) del día de la boleta | La boleta trae la fecha; Odoo guarda en UTC | `boletas/registro.py` líneas 402 y 407 |
| **ICBPER y redondeos** | Van como línea aparte en la factura, no en el costo | No son productos | `_facturar()`, `boletas/registro.py` línea 482 |
| **Descuento global** | Se reparte entre los productos | Baja su costo real | `_precios()`, `boletas/registro.py` línea 216 |
| **Tolerancia de las cuentas** | Diferencias de hasta S/ 0.02 se aceptan | Redondeos normales de las boletas | `TOLERANCIA`, `boletas/validacion.py` línea 10 |
| **Tipo de documento** | boleta → 03, factura → 01, ticket → 12 | Tabla 10 de SUNAT | `CODIGO_DOCUMENTO`, `boletas/registro.py` línea 16 |
| **Modelo de IA** | Claude Opus 5.5, esfuerzo `high`, con respaldo automático si el modelo se niega a leer | Prioridad: leer bien los números | `CLAUDE_MODELO`, `CLAUDE_ESFUERZO`; el respaldo es `fallbacks`, `boletas/lectura.py` línea 119 |
| **Tamaño de la foto** | Se achica a 2400 px si es más grande | Límite de la API | `MAX_LADO`, `boletas/lectura.py` línea 29 |

Los números de línea son los de esta versión; si cambian, busca el nombre de la función.

## Recomendaciones para la empresa

1. **Medir la IA con 10 a 20 boletas reales** antes de confiar en el bot, como explica la
   [sección 2](#medir-si-lee-bien-antes-de-confiar-en-ella).
2. **Usar una copia neutralizada de la base** para las primeras pruebas, nunca la base real.
3. **Darle al bot su propio usuario de Odoo**, con acceso solo a la empresa de las compras y solo los permisos
   de la sección 3.
4. **Confirmar el régimen tributario** (Consulta RUC de SUNAT) y ajustar `USA_CREDITO_FISCAL`.
5. **Que el contador revise** las reglas de la tabla anterior y las facturas borrador. Para mercadería suele
   convenir el **costo promedio** en las categorías de productos (en Odoo, el costo estándar no cambia aunque
   se compre más caro o más barato).
6. **Mandar las fotos como archivo** en Telegram (clip → Archivo): llegan sin comprimir. De frente, con luz y sin
   tapar los totales ni el código QR.
7. **Poner un límite de gasto** en la consola de Anthropic y revisar el consumo el primer mes.
8. **Anotar cuándo vence la clave de Odoo** y renovarla antes.
9. **Cuidar el servidor de AWS** antes de poner el bot en producción: ver la sección 5.8.

## Comandos del bot

| Comando | Qué hace |
|---|---|
| (foto) | Lee la boleta y muestra el resumen |
| `/ayuda` (o `/start`) | Explica cómo usarlo |
| `/categorias` | Lista las categorías de la tienda |
| `/cancelar` | Descarta la boleta en revisión |
| `/id` | Muestra tu número de usuario (funciona aunque no estés autorizado) |

Mientras haya una boleta en revisión, cualquier mensaje de texto se toma como corrección (no hace falta pulsar
✏️ Corregir; ese botón solo muestra la ayuda). Una corrección por mensaje:

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

`pruebas/test_odoo_real.py` registra de verdad en un **Odoo 19 de prueba**. Si no lo encuentra, esas pruebas
se saltan. Se configura con:

| Variable | Valor por defecto |
|---|---|
| `ODOO_PRUEBA_URL` | `http://127.0.0.1:8070` |
| `ODOO_PRUEBA_BASE` | `prueba_bot` (se niegan a correr en una base con otro nombre, porque escriben datos) |
| `ODOO_PRUEBA_CLAVE` | clave de API de un usuario de esa base |

Para crear esa base desde el código de Odoo 19:

```
python -m odoo db -c odoo.conf init prueba_bot --country PE --language es_PE
python -m odoo module install -c odoo.conf -d prueba_bot purchase stock l10n_pe
```

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

- Necesita el **RUC del proveedor**: compras sin comprobante o a vendedores sin RUC no se pueden registrar con el
  bot.
- La boleta en revisión se guarda en memoria: si el bot se reinicia antes de registrarla, hay que mandar la foto
  otra vez. Lo ya registrado queda en Odoo.
- Una empresa por bot. Para dos empresas, dos bots con distinto `.env` y distinto token.
- Productos que se compran por kilo, metro u otra unidad se crean igual en Unidades; hay que corregirlos en Odoo.
- No registra el pago de la factura.
- Monedas: soles y dólares (el dólar debe estar activo en Odoo).
- No se ha probado con boletas escritas a mano ni con Odoo Enterprise.
