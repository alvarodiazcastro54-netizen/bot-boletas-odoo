# Bot de boletas para Odoo

Bot de Telegram que registra en **Odoo 19** las compras hechas a un proveedor. Le mandas el comprobante de la
compra y el bot:

1. **Lee el comprobante**, venga como venga: foto (también las HEIC del iPhone), imagen, **PDF**, **XML** del
   comprobante electrónico, **ZIP** con XML y PDF, o varias fotos juntas en un álbum (un ticket largo). Con el XML
   los datos salen exactos y la IA solo elige categorías y nombres; con lo demás lee con IA (OpenAI o Claude)
   proveedor, RUC, serie, fecha, productos, cantidades, precios, descuentos y totales, y si el comprobante trae el
   **QR de SUNAT** corrige con él el RUC, el número, la fecha y el total. Acepta boletas, facturas, tickets y
   **notas de venta** y **cotizaciones** (con o sin RUC) y **liquidaciones de compra** (vendedor con DNI); a las notas de crédito y débito, guías de remisión y recibos por honorarios
   les responde que no son una compra. Del PDF lee hasta 10 páginas; de un álbum, hasta 10 fotos (un ticket
   largo en varias fotos); de un ZIP, todos los comprobantes que traiga.
2. **Revisa las cuentas**: cantidad × precio − descuento = importe, la suma de las líneas = total, RUC válido
   (dígito verificador), fecha posible. Si algo no cuadra, no deja registrar hasta corregirlo, y dice qué
   corrección escribir. Antes arregla solo lo que es seguro:
   - las líneas que vienen **por paquete, caja o docena** (1 × «X 24» con el precio de cada unidad, «2 DOC» con el
     precio de la docena, 24 unidades con el precio del paquete de 12) se cuentan **por unidad**, y avisa cómo
     volver al paquete;
   - un **descuento impreso como línea negativa** pasa a descuento del total;
   - una **bonificación** (importe 0) entra al stock con costo 0;
   - la **percepción** del IGV (2 %, 1 % o 0.5 %) que el total tiene de más va a la factura como otro cargo;
   - en una factura con **dos columnas de precio** (precio unitario con IGV e importe sin IGV, o al revés), usa el
     precio que cuadra con el importe;
   - en una factura con productos **exonerados** (libros) y gravados, cada línea lleva su IGV o no, según la
     columna de afectación o el XML;
   - si en una boleta, ticket o nota las líneas ya suman el total, toma los precios **con IGV incluido**.
3. **Te muestra un resumen** en Telegram con botones: ✅ Registrar, ✏️ Corregir, ❌ Cancelar.
4. Al confirmar, **en Odoo**:
   - busca al proveedor por su RUC (o por su nombre, si es una nota de venta sin RUC) o lo crea;
   - si ese proveedor ya tiene una compra del bot **por el mismo total** en 10 días a la redonda (por ejemplo, la
     nota de venta y ahora su boleta), lo avisa y ofrece **📎 Es la misma compra**: adjunta el comprobante a esa
     compra, le pone el tipo y número de SUNAT y confirma su factura, sin crear otra compra ni sumar stock;
   - crea **cada producto como nuevo** (no busca productos de compras anteriores: así lo pidió la empresa), con su
     costo, su categoría, la categoría de la web, un **nombre claro para la tienda** que propone la IA y un
     **precio de venta sugerido** según los márgenes que UYAY ya usa en esa categoría (se cambia antes de
     registrar con `3 venta 65`);
   - crea la **orden de compra** (con la serie y número de la boleta como referencia) y la confirma;
   - valida la **recepción**, que suma el **stock**;
   - crea la **factura de proveedor** con el tipo de documento (03 Boleta, 01 Factura, 12 Ticket), la fecha y el
     comprobante original adjunto (foto, PDF o XML), y la **confirma** si `CONFIRMAR_FACTURA=si` (así la usa la empresa: no hay que entrar a Odoo).
     Solo la confirma si su total en Odoo es igual al de la boleta; si no, la deja en borrador y avisa. Con `no`
     queda en borrador para que la revise el contador. En modo de prueba nunca se confirma. La de una **nota de
     venta** (con o sin RUC) siempre queda en borrador y sin tipo de documento, porque no es un comprobante de SUNAT.

Si mandas dos veces la misma boleta, avisa que ya está registrada. Si algo falla a la mitad, «Reintentar»
sigue desde donde quedó sin duplicar nada.

**Fotos de los productos y tienda:** después de registrar, el bot ofrece **publicar** los productos en la tienda
web (🌐 Publicar / Solo en Odoo). Solo publica los que tienen foto y precio; la foto se manda con el **número de su
línea** escrito en ella (por ejemplo `3`). Si no quieres publicar, todo queda solo en Odoo
([detalles](#fotos-de-los-productos)).

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

- 381 pruebas automáticas (19/11/2026). 22 de ellas registran de verdad en un **Odoo 19 Community** de prueba con la
  localización peruana (`l10n_pe`): proveedor con RUC, productos con su código y costo, compra, recepción con
  stock, factura borrador tipo 03 con la foto, boleta repetida, registro retomado a medias, factura con crédito
  fiscal, categorías, **nota de venta sin RUC**, **factura electrónica leída del XML** (confirmada y con el XML
  adjunto), compras por paquete con la factura exacta, **nota de venta con RUC** unida después a su boleta (sin
  duplicar el stock), compra en **dólares** con el tipo de cambio de Odoo, productos reusados por código, dos notas
  sin RUC del mismo día y monto, y **liquidación de compra** con el DNI del vendedor y el tipo 04.
- Lectura de archivos: PDF, XML UBL 2.1, ZIP (ignora la respuesta de SUNAT), HEIC, álbumes y el QR de SUNAT, con
  archivos generados en las pruebas.
- Esas pruebas pasan también con un **usuario de Odoo que no es administrador**, con los permisos de la
  sección 3. Con permisos de «usuario» en Compras falla, como se explica ahí.
- El bot en Telegram, de punta a punta, en **modo de prueba**: sin IA, usando la lectura
  revisada a mano de una boleta real (`muestras/`). Registró la compra, el stock y la factura correctamente.
- **Lectura real con OpenAI** (`gpt-6-luna`): leyó bien la boleta de `muestras/`, una cotización real de un
  mayorista de cosméticos (18 líneas, 13 por caja: 336 unidades con costos exactos y la carreta como otro cargo) y
  tres comprobantes de prueba (factura con dos columnas de precio, factura con libros exonerados y liquidación de
  compra). Cada lectura usa unos 7 000 tokens de entrada y entre 1 000 y 4 000 de salida.

**Falta:**

- Medir la lectura con 10 a 20 boletas reales de tiendas distintas (`evaluar_lectura.py`): 2 boletas no
  bastan para confiar en ella. Cómo medirlo está en la [sección 2](#2-la-ia-que-lee-las-fotos).
- La lectura con Claude (Anthropic) no se ha probado con la API real: la empresa no tiene clave de Anthropic.
- No se probó con Odoo Enterprise.
- La **publicación en la tienda** y la **categoría de la web** no se probaron contra Odoo real: el Odoo de prueba
  no tiene el módulo de la tienda. Odoo solo pide permiso de escritura sobre el producto para publicar.
- No se probó con PDF, XML ni QR de comprobantes reales de proveedores; solo con archivos generados.

## Así se ve en Telegram

```
Boleta B410-00170132 · 05/11/2026
Proveedor: SB TRADING S.R.LTDA (HONG KONG MARKET)
RUC 20101567428 · 🆕 se creará

1. 🆕 SOL SET CORTADOR, CUCHILLO, PELADOR DE FRUTA ABS ✂️
    2 × 22.00 −50% = 22.00 · costo c/u 11.00
    📁 Hogar y Manualidades / Cocina
...
7. 🆕 LLAVERO METAL
    2 × 6.00 −70% = 3.60 · costo c/u 1.80
    📁 Accesorios / Dijes y Llaveros

Total: S/ 58.30
El IGV va incluido en el costo (no da crédito fiscal).

⚠️ Revisa:
• Línea 1: el nombre se ve cortado («SOL SET CORTADOR, CUCHILLO, PELADOR DE FRUTA ABS»).

[✅ Registrar]  [✏️ Corregir]  [❌ Cancelar]
```

🆕 producto o proveedor nuevo · ✔ producto que ya creó un intento anterior de esta misma boleta (al reintentar) ·
✂️ nombre cortado en el papel · 📁 categoría elegida por la IA.

## Qué necesitas

- **Odoo 19** con **Compras**, **Inventario** y **Facturación**, y para Perú la localización `l10n_pe` con su
  plan contable instalado en la empresa de las compras. Probado en Community.
- Las **categorías de productos** ya creadas en Odoo (la IA elige entre ellas; si no hay, los productos quedan
  sin categoría).
- Python 3.12, o Docker.
- Un bot de Telegram (gratis).
- Una clave de la API de **OpenAI** o de **Anthropic** (sección 2). Para probar sin ella existe el modo de prueba.

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

El bot puede leer las fotos con **OpenAI** (los modelos de ChatGPT) o con **Claude** (Anthropic). Se elige con
`IA` en el `.env`. La empresa usa OpenAI; lo que sigue sobre Claude vale si algún día se cambia.

### Qué hace y qué no hace

El bot usa la IA por internet, a través de su API, solo para una cosa: **convertir la foto de la boleta en
datos**. Le devuelve al bot el proveedor, el RUC, la serie, la fecha, cada producto con su código, cantidad,
precio, descuento e importe, y los totales. También elige la categoría de cada producto entre las que ya existen
en Odoo.

La IA **no toca Odoo ni decide nada**. Después de leer:

1. el bot revisa las cuentas y el RUC por su cuenta (sin IA);
2. muestra el resumen en Telegram;
3. **una persona confirma** con ✅ Registrar. Recién ahí se escribe en Odoo.

### Con OpenAI (ChatGPT) — la que usa la empresa

1. En **platform.openai.com** → *API keys* crea una clave (empieza con `sk-`). Una suscripción a ChatGPT Plus
   **no** sirve: la API se paga aparte.
2. En *Settings → Billing* carga saldo (prepago) y pon un límite de gasto mensual.
3. En `.env`:

   ```
   IA=openai
   OPENAI_API_KEY=sk-...
   OPENAI_MODELO=gpt-6-luna
   OPENAI_ESFUERZO=high
   MODO_PRUEBA=no
   ```

4. Vuelve a arrancar el bot.

- **Modelo:** `gpt-6-luna`, el más barato de la familia GPT-6. Leyó perfecto las 2 boletas de `muestras/`.
  Según precios publicados en noviembre de 2026 (no confirmados en la página oficial) cuesta US$ 0.10 / US$ 0.50
  por millón de tokens: **menos de medio centavo de dólar por boleta**. Si lee mal boletas difíciles, prueba
  uno más capaz (por ejemplo `gpt-6.1-sol`) con `evaluar_lectura.py --ia openai --modelo gpt-6.1-sol`.
- El 12/11/2026 la cuenta no tenía saldo y aun así `gpt-6-luna` respondía, pero `gpt-6.1-sol` no
  (`credit_balance_exhausted`). Carga saldo de todos modos: sin él, el uso gratuito puede acabarse en cualquier
  momento y el bot avisará «La cuenta de OpenAI no tiene saldo».
- `OPENAI_ESFUERZO`: `none`, `minimal`, `low`, `medium`, `high`, `xhigh` o `max`. `none` es para modelos que
  razonan y se quiere que no lo hagan. Si el modelo **no razona** (por ejemplo `gpt-4.1`), déjalo **vacío**: así
  el bot no manda el esfuerzo, que con esos modelos da error. Si OpenAI rechaza el modelo o el esfuerzo, el bot
  avisa «OpenAI rechazó la configuración del bot», no «prueba con otra foto».
- El bot usa la API de *Responses* con **salida estructurada estricta**: la respuesta siempre tiene la forma de
  una boleta. Con `store=false`, OpenAI no guarda la respuesta para mostrarla después en su panel.
- El código está en `LectorOpenAI`, `boletas/lectura.py`.

### Con Claude (Anthropic)

El modelo por defecto es **Claude Opus 5.5**. La API de Anthropic se paga aparte: una suscripción a Claude
(Pro, Max o Team) **no** sirve para esto.

### Estado de Claude: no se probó con la API real

La empresa no tiene clave de la API de Anthropic, así que **nadie ha medido qué tan bien lee Claude las
boletas reales**, cuánto cuesta de verdad cada una ni cuánto tarda.

Lo que sí está probado:

- que el bot arma bien el pedido a la API (la foto, las instrucciones, el formato de respuesta, el modelo, el
  respaldo automático). Las pruebas usan una API simulada que no cobra;
- que maneja las fallas: la IA se niega a leer, la respuesta llega cortada, la clave es inválida, no hay saldo
  o no hay conexión. En todos los casos avisa en Telegram y no registra nada;
- todo lo que pasa después de leer, con el **modo de prueba**, que usa la lectura de una boleta real revisada a
  mano.

Por eso, antes de usar Claude en serio, hay que **activarlo y medirlo** (los dos pasos siguientes).

### Activar Claude

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

   Usa la IA de `IA` en el `.env`; `--ia openai` o `--ia anthropic` la cambia. Con
   `--modelo gpt-6.1-sol`, `--modelo claude-sonnet-5-5` o `--esfuerzo medium` se prueban otras opciones. Con `--sin-api` se prueba
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

### Cuánto cuesta con Claude

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

### Respaldo automático (solo Claude)

Muy rara vez, un filtro de seguridad de la IA puede negarse a leer una imagen normal. Para ese caso está activado
el respaldo de Anthropic: la misma API reintenta con otro modelo automáticamente, sin hacer nada. Está en
`fallbacks="default"`, `boletas/lectura.py`.

### Qué se le envía a la IA

- La **foto** (achicada si es muy grande), las instrucciones de lectura y los **nombres de las categorías** de
  Odoo.
- **No** se envían datos de Odoo (productos, precios, clientes), ni la clave de Odoo, ni el token de Telegram.
- Según las políticas de OpenAI y de Anthropic para sus API, por defecto los datos no se usan para entrenar sus
  modelos; revisen la política vigente en sus webs. Las boletas de compra casi no traen datos personales, pero tenlo en
  cuenta si alguna trae el DNI o el nombre de una persona.

### Probar sin IA (modo de prueba)

Con `MODO_PRUEBA=si` el bot no llama a la IA ni necesita `OPENAI_API_KEY` ni `ANTHROPIC_API_KEY`. Cualquier foto devuelve la boleta de
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
sin cifrar. Por eso el bot **no arranca** con `http://` hacia una dirección de internet (sí con `localhost`, una
IP privada o el nombre de un contenedor). Si aceptas el riesgo para una prueba corta, pon `ODOO_PERMITIR_HTTP=si`.

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
IA=openai
OPENAI_API_KEY=sk-...
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
| `ODOO_URL usa http:// hacia …, que está en internet` | En el servidor usa `ODOO_URL=http://localhost:8069`; desde fuera, `https://` |
| `MUESTRA_PRUEBA: no existe el archivo …` | La ruta es relativa a la carpeta del proyecto; en Docker la imagen no lleva las fotos de `muestras/` |
| `La factura quedó en borrador: en Odoo suma … y la boleta dice …` | Revisar la factura en Odoo, corregir la diferencia y confirmarla a mano |
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
| `La clave de la API de OpenAI no es válida` | `OPENAI_API_KEY` mal copiada o revocada |
| `La cuenta de OpenAI no tiene saldo para este modelo` | Carga saldo en platform.openai.com → Billing |
| `El modelo de OpenAI configurado no existe` | `OPENAI_MODELO` mal escrito o no habilitado en la cuenta |

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
- El `.env` tiene tres claves (Telegram, la IA y Odoo): no lo copies a otros lados ni lo subas a Git.
- La imagen de Docker no lleva las fotos de `muestras/`: ver `.dockerignore`.

## Configuración (`.env`)

| Variable | Para qué | Ejemplo |
|---|---|---|
| `TELEGRAM_TOKEN` | Token de @BotFather | `123456789:AAH...` |
| `TELEGRAM_USUARIOS_PERMITIDOS` | Quiénes pueden usarlo (números de `/id`, separados por comas) | `111111111,222222222` |
| `IA` | Qué IA lee las fotos: `openai` (ChatGPT) o `anthropic` (Claude). Vacío = `anthropic` | `openai` |
| `OPENAI_API_KEY` | Clave de OpenAI (si `IA=openai`) | `sk-...` |
| `OPENAI_MODELO` | Modelo de OpenAI que lee las fotos | `gpt-6-luna` |
| `OPENAI_ESFUERZO` | Razonamiento: `none`, `minimal` a `max`; vacío para modelos que no razonan (no se manda) | `high` |
| `ANTHROPIC_API_KEY` | Clave de Anthropic (si `IA=anthropic`) | `sk-ant-...` |
| `MODO_PRUEBA` | `si` = no usa la IA (boleta de ejemplo); solo con base de prueba | `no` |
| `MUESTRA_PRUEBA` | Boleta que devuelve el modo de prueba (vacío = la de Hong Kong). Ruta relativa a la carpeta del proyecto; si no existe, el bot no arranca | `muestras/boleta-01-hongkong.esperado.json` |
| `CLAUDE_MODELO` | Modelo de Claude que lee las fotos | `claude-opus-5-5` |
| `CLAUDE_ESFUERZO` | `low`, `medium`, `high`, `xhigh` o `max`; más alto lee con más cuidado y cuesta más | `high` |
| `ODOO_URL` | Dirección de Odoo. Con `http://` solo hacia `localhost`, una IP privada o un contenedor | `http://localhost:8069` |
| `ODOO_PERMITIR_HTTP` | `si` = deja usar `http://` hacia internet (la clave viaja sin cifrar) | `no` |
| `ODOO_BASE` | Nombre de la base de datos | `mi_base` |
| `ODOO_CLAVE_API` | Clave de API del usuario del bot | |
| `ODOO_EMPRESA_ID` | Empresa donde se registra (si hay varias) | `2` |
| `SUGERIR_PRECIO` | `si` = sugiere el precio de venta con el margen mediano que ya usa la tienda en esa categoría (precio ÷ costo de sus productos), redondeado como la tienda; `no` = queda en 0 | `si` |
| `MARGEN_VENTA` | Si se llena, usa siempre esa ganancia en vez de los márgenes de la tienda (1.0 = 100 %, 0.5 = 50 %) | vacío |
| `TIENDA_URL` | Dirección pública de la tienda, para mandar el enlace de cada producto publicado | `https://tienda.ejemplo.pe` |
| `ARCHIVO_ESTADO` | Dónde guarda el bot lo que estaba haciendo (comprobante en revisión, última compra) para no olvidarlo al reiniciarse. Vacío = `datos/estado.pickle` | vacío |
| `ODOO_CATEGORIA_ID` | Categoría (número) si la IA no elige ninguna | vacío |
| `RUC_EMPRESA` | RUC de la empresa (vacío = lo lee de Odoo) | vacío |
| `USA_CREDITO_FISCAL` | `si` = las facturas a nombre de la empresa usan el IGV como crédito fiscal. Vacío = `no` (UYAY no usa crédito fiscal) | `no` |
| `CONFIRMAR_FACTURA` | `si` = al pulsar ✅ Registrar la factura de proveedor queda confirmada; `no` = en borrador. Si su total no es igual al de la boleta o si Odoo no deja confirmarla, queda en borrador y el bot lo avisa. Con `MODO_PRUEBA=si` nunca se confirma | `si` |
| `REUSAR_PRODUCTOS` | `no` = cada compra crea sus productos (lo decidió la empresa); `codigo` = si el mismo proveedor ya vendió un producto con ese código, se usa ese producto y se le suma el stock | `no` |

Para `MODO_PRUEBA`, `SUGERIR_PRECIO`, `USA_CREDITO_FISCAL`, `CONFIRMAR_FACTURA` y `ODOO_PERMITIR_HTTP` sirven `si`/`no` (también
`true`/`false`, `1`/`0`).

## Decisiones tomadas a criterio

Las marcadas **(confirmado)** las decidió la empresa. Las demás las decidimos nosotros para que el bot funcione,
sin confirmarlas todavía con la empresa ni con su contador: revísenlas antes de usarlo en serio.

| Tema | Qué hace hoy | Por qué | Dónde cambiarlo |
|---|---|---|---|
| **Precio de venta (confirmado)** | Se sugiere con el margen mediano de la tienda en esa categoría (si no hay 5 productos con costo, la categoría madre; si no, el de toda la tienda; si no, ×2) y se redondea como la tienda: hasta S/ 5 a los 10 céntimos hacia arriba, hasta S/ 10 a 50 céntimos, desde S/ 10 a múltiplos de 5; nunca menos del costo + 10 %. El usuario lo acepta o lo cambia | Lo decidió la empresa el 17/11 | `SUGERIR_PRECIO`, `boletas/precios.py` |
| **Crédito fiscal (confirmado)** | No se usa: el IGV de toda compra va al costo | UYAY no usa crédito fiscal | `USA_CREDITO_FISCAL` en `.env` |
| **IGV de las boletas** | Las líneas van sin impuesto y con el precio de la boleta | Una boleta no da derecho a crédito fiscal | `_precios()`, `boletas/registro.py` |
| **IGV de las facturas con crédito fiscal** | Solo con `USA_CREDITO_FISCAL=si` (UYAY no lo usa): precio sin IGV + el impuesto «IGV 18%» de compras de la empresa | Así Odoo registra el crédito fiscal | El impuesto se busca en `impuesto_igv_compra()`, `boletas/registro.py` |
| **Costo del producto** | El precio con su descuento (S/ 11.00 al 50 % → S/ 5.50) | Es lo que realmente se pagó | `_precios()`, `boletas/registro.py` |
| **Categoría** | La IA elige una de las categorías finales de Odoo (sin subcategorías) | Que los productos entren ordenados a la tienda | Se cambia en Telegram con `3 categoria cocina`. Las instrucciones a la IA están en `SISTEMA`, `boletas/lectura.py`; la lista, en `categorias()`, `boletas/registro.py` |
| **Nombre del producto** | El que propone la IA para la tienda (palabras completas, sin códigos); si no propone, el de la boleta sin la unidad («LLAVERO METAL UND» → «LLAVERO METAL») | El cliente ve el nombre en la tienda | Se cambia en Telegram con `3 tienda ...`; `nombre_en_tienda()`, `boletas/registro.py` |
| **Nombres cortados** | Se marcan con ✂️; se pueden completar o registrar así | La impresora corta los nombres largos | Se corrigen en Telegram con `3 nombre ...` |
| **Productos nuevos (confirmado)** | Cada boleta crea **todos** sus productos como nuevos; no se buscan en compras anteriores. Si el mismo artículo se compra en dos boletas, quedan dos productos (en la tienda salen repetidos y con el stock partido) | La empresa lo prefirió así: reconocer productos ya registrados sería muy complejo | Con `REUSAR_PRODUCTOS=codigo` se reusan los que traen **el mismo código del mismo proveedor** (seguro: no se adivina por el nombre); `_productos_por_codigo()`, `boletas/registro.py` |
| **Paquetes, cajas y docenas** | Se registran **por unidad**: 1 × «X 24» a S/ 4.33 con importe 104 → 24 unidades de S/ 4.333; «2 DOC» a S/ 36 → 24 de S/ 3. Solo si las cuentas lo confirman y el tamaño es común (2 a 144) o está en la descripción. Si solo la descripción dice «X 12» y las cuentas cuadran, no cambia nada y sugiere `3 paquete 12` (puede ser un kit que se vende entero) | La tienda vende por unidad: así el stock y el precio sugerido salen por unidad | `por_unidad()`, `boletas/ajustes.py`; se vuelve atrás con `3 cantidad 1` y `3 precio 104` |
| **Nota de venta o cotización con RUC** | Se registra como compra, pero la factura de proveedor queda en borrador, sin tipo de documento y con «sin comprobante de SUNAT». A la cotización (presupuesto o proforma) además se le avisa que no prueba que la compra se hizo | No son comprobantes de SUNAT; registrarlas como boleta 03 inventaría un documento | `es_nota()`, `boletas/registro.py` |
| **Compra parecida** | Mismo proveedor, mismo total (± S/ 0.05) y 10 días a la redonda → aviso y botón 📎 Es la misma compra | Evita registrar dos veces la nota de venta y su boleta | `compra_parecida()` y `unir()`, `boletas/registro.py` |
| **Compras en dólares** | La compra queda en USD; el costo del producto, en soles con el tipo de cambio de Odoo del día de la boleta. Sin tipo de cambio en Odoo no deja registrar | El costo de los productos está en soles | `tipo_de_cambio()`, `boletas/registro.py` |
| **Bonificaciones** | Entran al stock con costo 0 (también las «operaciones gratuitas» del XML) | No se pagaron | `ajustar()`, `boletas/ajustes.py`; `_linea()`, `boletas/sunat.py` |
| **IGV al costo en facturas** | Las líneas exoneradas o inafectas (marcadas en la factura con «EXO», «INA» o la columna de afectación, o en el XML) no llevan IGV y las gravadas llevan el 18 %. Si la factura no las marca, o las marcas no cuadran con su IGV, el IGV de la factura se reparte entre todas las líneas | Así los libros no pagan un IGV que no se cobró y la suma de los costos es lo pagado | `factor_de_igv_de_la_linea()`, `boletas/registro.py`; se corrige con `3 exonerado` / `3 gravado` |
| **Liquidación de compra** | El proveedor es el vendedor, con su DNI (se busca y se crea por su DNI). Tipo de documento 04. El costo es el importe total (valor + IGV, porque UYAY paga ese IGV a SUNAT y no usa crédito fiscal); la retención de renta no es costo. La factura de proveedor queda en borrador | Las retenciones de IGV y renta las declara el contador | `es_liquidacion()`, `boletas/registro.py`; `_retencion_de_la_liquidacion()`, `boletas/ajustes.py` |
| **Comprobante abandonado** | Si el que estaba en revisión lleva más de 6 horas sin registrarse, el nuevo toma su lugar y el viejo pasa a la cola | Que un comprobante olvidado no tape a los nuevos | `PENDIENTE_ABANDONADO`, `boletas/bot.py` |
| **Reintentar sin duplicar** | Dentro de una boleta, dos líneas con el mismo código son un solo producto. Si un registro falló a la mitad, «Reintentar» reusa los productos que **esa misma boleta** ya alcanzó a crear | Cada producto guarda en sus notas internas «Creado por el bot de boletas: comprobante (serie) del RUC (RUC)», y solo esos se buscan | `_productos_de_la_boleta()` y `marca_de_boleta()`, `boletas/registro.py` |
| **Unidad de medida** | Los productos nuevos se crean en Unidades | La mayoría de la mercadería se compra por unidad | `_crear_producto()`, `boletas/registro.py` |
| **Tipo de producto** | Bien almacenable (lleva stock) | Es mercadería | `_crear_producto()`, `boletas/registro.py` |
| **Proveedor nuevo** | Con RUC, razón social, dirección y país Perú; el nombre comercial va en las notas | Datos que trae la boleta | `_crear_proveedor()`, `boletas/registro.py` |
| **Compras sin RUC (confirmado)** | Las notas de venta sin RUC se registran con el proveedor solo con su nombre (si ya existe uno sin RUC con ese nombre, se reusa). La referencia es «NV-fecha-total-huella», donde la huella sale de sus líneas: así la misma nota no se duplica y dos notas distintas del mismo día y monto no se confunden. La factura de proveedor queda en borrador con la nota «sin comprobante de SUNAT» | Lo decidió la empresa el 17/11 | `sin_ruc()`, `referencia()`, `boletas/registro.py` |
| **Factura de proveedor** | Por defecto queda en **borrador**; la empresa usa `CONFIRMAR_FACTURA=si` y queda **confirmada**, solo si su total en Odoo es igual al de la boleta. En ambos casos no se registra el pago | En borrador, la revisa quien lleva la contabilidad. Confirmada, no hay que entrar a Odoo, pero un error de lectura queda en la contabilidad: para corregirlo hay que pasarla a borrador en Odoo («Restablecer a borrador») | `CONFIRMAR_FACTURA` en `.env`; `_confirmar_factura()`, `boletas/registro.py` |
| **Almacén** | El de la empresa por defecto | Hay un solo almacén | La recepción la crea Odoo al confirmar la compra |
| **Publicación en la web (confirmado)** | Después de registrar, el bot **ofrece** publicar; solo publica lo que tiene foto y precio. Si no se acepta, queda solo en Odoo | Que nada salga a la venta sin foto o a S/ 0 | `Tienda.publicar()`, `boletas/tienda.py` |
| **Hora de la compra** | 12:00 de Lima (17:00 UTC) del día de la boleta | La boleta trae la fecha; Odoo guarda en UTC |, `boletas/registro.py` |
| **ICBPER, percepción, flete y redondeos** | Van como línea aparte en la factura, no en el costo. La línea del flete o la carreta se pasa ahí con `cargo 17`; un monto suelto, con `otros 33.60` | No son productos | `_facturar()`, `boletas/registro.py` |
| **Descuento global** | Se reparte entre los productos | Baja su costo real | `_precios()`, `boletas/registro.py` |
| **Tolerancia de las cuentas** | Diferencias de hasta S/ 0.02 se aceptan | Redondeos normales de las boletas | `TOLERANCIA`, `boletas/validacion.py` |
| **Tipo de documento** | boleta → 03, factura → 01, ticket → 12 | Tabla 10 de SUNAT | `CODIGO_DOCUMENTO`, `boletas/registro.py` |
| **Modelo de IA** | Claude Opus 5.5, esfuerzo `high`, con respaldo automático si el modelo se niega a leer | Prioridad: leer bien los números | `CLAUDE_MODELO`, `CLAUDE_ESFUERZO`; el respaldo es `fallbacks`, `boletas/lectura.py` |
| **Tamaño de la foto** | Se achica a 2400 px si es más grande | Límite de la API | `MAX_LADO`, `boletas/lectura.py` |

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
| (foto, imagen, PDF, XML o ZIP) | Lee el comprobante y muestra el resumen. Varias fotos juntas (álbum, hasta 10) se leen como un solo comprobante; varios PDF, XML o ZIP juntos, o un ZIP con varios comprobantes, se leen uno por uno. Si llega uno mientras revisas otro, queda **en cola** y aparece apenas registras o cancelas el actual. Lo que no es un comprobante (por ejemplo, la foto de un producto sin su número) no entra a la cola |
| (foto con un número, por ejemplo `3` o `foto 3`) | Pone esa foto como imagen del producto de la línea 3 de la **última boleta registrada** en ese chat. Si ya tenía foto, la reemplaza |
| `/ayuda` (o `/start`) | Explica cómo usarlo |
| `/foto cartera` | Busca cualquier producto por su nombre para ponerle foto |
| `/sinfoto` | Lista con botones los productos que registró el bot (también los de antes) y todavía no tienen foto (los 10 más recientes). Tocas uno y mandas su foto |
| `/porpublicar` | Lista los productos del bot sin publicar y qué les falta (foto o precio); tocas uno para publicarlo o mandar su foto |
| `/categorias` | Lista las categorías de la tienda |
| `/cancelar` | Descarta el comprobante en revisión (pasa al siguiente de la cola) y deja de esperar una foto o un precio |
| `/id` | Muestra tu número de usuario (funciona aunque no estés autorizado) |

Mientras haya una boleta en revisión, cualquier mensaje de texto se toma como corrección (no hace falta pulsar
✏️ Corregir; ese botón solo muestra la ayuda). Una corrección por mensaje:

```
3 nombre SORBETE ACERO INOX RECTO Y OBLICUO + CEPILLO
3 cantidad 2        3 precio 11        3 descuento 50        3 importe 16.50
3 codigo 5509007    3 categoria cocina                       borrar 3
3 paquete 12        cargo 3 (flete o carreta: va a la factura, no es producto)
descuento total 15  otros 33.60 (percepción, flete)          igv incluido     igv aparte
3 exonerado         3 gravado (la línea 3 no paga IGV, o sí)  dni 40123456     tipo liquidacion
3 venta 65          3 tienda Sorbete de acero inoxidable con cepillo
ruc 20101567428     proveedor SB TRADING SRL     numero B410-00170132
fecha 05/11/2026    total 58.30              tipo nota
```

Si las cuentas no cuadran, el error dice qué corrección probar (por ejemplo «descuento total 8.30» o
«igv incluido»).

`3 precio` cambia el precio de **compra** que leyó la IA; `3 venta` cambia el precio de **venta** en la tienda.

### Fotos de los productos

Al terminar de registrar, el bot lista los productos con su número de línea y ofrece publicarlos:

```
✅ Registrado en Odoo
Compra: P00041
...
Productos nuevos: 3
1. Gancho mariposa con perlas dorado
2. Lapicero capibara de goma
3. Cartera Hypnotic Laura negro

🌐 ¿Los publico en la tienda? ...
[🌐 Publicar]  [Solo en Odoo]
```

- **🌐 Publicar** publica los que ya tienen foto y precio, y avisa qué les falta a los demás. Desde ese momento,
  cada foto que mandes con su número se pone y el producto se publica solo.
- **Solo en Odoo** no publica nada; se pueden publicar después desde Odoo o con `/porpublicar`.
- Si a un producto le falta el precio, el bot lo pide y sugiere uno con los márgenes de la tienda.
- **Foto con número:** escribe el número en el **texto de la foto**. Vale para la **última compra registrada** en ese
  chat; el bot la recuerda aunque se reinicie (`ARCHIVO_ESTADO`).
- **Cualquier producto, cualquier día:** `/foto cartera` lo busca por nombre; `/sinfoto` lista los del bot sin foto.
  Tocas uno y la siguiente foto (sin escribir nada) va a ese producto. Espera 10 minutos; «❌ Cancelar» o `/cancelar`
  dejan de esperar.
- Manda las fotos de productos **una por una**: un álbum se toma como un comprobante.
- El bot endereza la foto, la achica a 1920 px y la guarda en el producto (`image_1920` de la plantilla).
- El usuario del bot necesita poder **modificar productos** en Odoo; con eso también puede publicarlos.
- El código está en `boletas/fotos.py` (Telegram) y `boletas/tienda.py` (Odoo).

## Pruebas

```
.venv\Scripts\python -m pip install -r requirements-dev.txt
.venv\Scripts\python -m pytest
.venv\Scripts\python -m ruff check
.venv\Scripts\python -m ruff format
```

`pytest.ini` dice dónde están las pruebas y `ruff.toml` fija el estilo del código (líneas de 120, imports
ordenados). Las pruebas están por tema: `test_ajustes.py` (paquetes, bonificaciones, percepción, IGV),
`test_impuestos_y_liquidacion.py`, `test_registro_compras.py` (lo que se registra en Odoo), `test_cola.py` (varios
comprobantes a la vez), `test_fotos_de_productos.py`, etc.

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
  bot.py           Telegram: comprobantes, álbumes, botones, correcciones
  fotos.py         Telegram: fotos de productos, /foto, /sinfoto, /porpublicar y publicar
  archivos.py      foto, HEIC, PDF, XML, ZIP y álbum → imágenes para la IA y archivos para adjuntar
  ajustes.py       paquetes por unidad, bonificaciones, descuentos, percepción e IGV incluido
  sunat.py         XML UBL 2.1, QR de SUNAT y documentos que no son compra
  qr.py            busca el QR en la imagen
  precios.py       márgenes de la tienda y precio de venta sugerido
  tienda.py        categoría de la web, búsqueda, productos del bot y publicación
  lectura.py       lectura con OpenAI o Claude, y modo de prueba
  modelos.py       estructura de una boleta (también es el esquema para la IA)
  validacion.py    RUC, cuentas, fechas
  correcciones.py  «3 nombre ...», «borrar 3», etc.
  registro.py      proveedor, productos, compra, recepción y factura en Odoo
  odoo.py          cliente de la API JSON-2 de Odoo 19
  mensajes.py      textos del bot
pruebas/           pruebas automáticas, por tema
pytest.ini         configuración de las pruebas
ruff.toml          estilo del código (ruff check y ruff format)
muestras/          boleta de ejemplo, su lectura revisada y categorías de la tienda
evaluar_lectura.py mide la lectura con fotos reales (gasta API)
```

## Limitaciones conocidas

- Sin RUC solo se registran **notas de venta**, **cotizaciones** y **liquidaciones de compra** (con el DNI del
  vendedor); un comprobante de SUNAT sin RUC legible hay que corregirlo a mano (`ruc ...`) o marcarlo como nota
  (`tipo nota`).
- La foto con número solo va a la última compra registrada en el chat; para las anteriores están `/foto` y
  `/sinfoto`.
- Del PDF se leen hasta 10 páginas y de un álbum hasta 10 fotos; si hay más, el bot lo avisa.
- Un álbum de fotos se toma como **un** comprobante: varias boletas distintas hay que mandarlas por separado o como
  PDF.
- Una empresa por bot. Para dos empresas, dos bots con distinto `.env` y distinto token.
- Productos que se compran por kilo, metro u otra unidad se crean igual en Unidades; hay que corregirlos en Odoo.
- No registra el pago de la factura.
- Monedas: soles y dólares (el dólar debe estar activo en Odoo y con su tipo de cambio del día). En otra moneda
  (yuanes, euros) el bot no deja registrar.
- En una factura con productos gravados y exonerados que **no** marca cuáles son exonerados, el IGV se reparte entre
  todas las líneas: el total de los costos es exacto, pero el de cada producto es aproximado. Se corrige con
  `3 exonerado`.
- No se ha probado con notas escritas a mano reales ni con Odoo Enterprise.
