# Momento-A
Criptomonedas a punto de subir la MA(7)

Scanner de **todas las criptomonedas de Binance (spot y futuros USDT-M perpetuos)**, en
velas de **1D** por defecto. Busca monedas en un momento muy bajista cuya vela empieza a
tomar fuerza y quiere cambiar la tendencia.

**Web:** https://luisp11127.github.io/Momento-A/

## Qué filtra

Con MA(7), MA(25) y MA(99) (medias móviles simples sobre el cierre, igual que las del
gráfico de Binance), un par aparece si su vela cumple:

| Condición | Por defecto | Filtro en la web | En la línea de comandos / workflow |
|---|---|---|---|
| Tendencia muy bajista: **MA(7) < MA(25) < MA(99)** | sí | **No exigir MA25 < MA99** | `--sin-ma99` exige solo MA(7) < MA(25) |
| **La MA(7) sigue por debajo de la MA(25)** (todavía no hay cruce) | siempre | — | — |
| La vela está **muy cerca o por encima de la MA(7)**: precio ≥ MA(7) − 2 % | 2 % | **Margen bajo MA7** | `--tolerancia 1` |
| Límite de cuánto puede estar por encima de la MA(7) | sin límite | **Máx. sobre MA7** | `--max-encima 5` |
| La vela toma fuerza: **vela verde** (precio > apertura) | sí | **Incluir velas rojas** | `--permitir-roja` |
| Volumen mínimo en las últimas 24 h | 0 | **Vol. mín. 24h** | `--volumen-min 1000000` |
| Vela evaluada | la vela en curso | **Vela cerrada** | `--vela-cerrada` |
| Temporalidad de las velas | **1D** | **Velas** (al escanear) | `--intervalo 4h` |

Por defecto se evalúa la **vela en curso** (las medias incluyen el precio actual, como en el
gráfico en vivo). La vela diaria de Binance abre a las 00:00 UTC.

Se ignoran los pares que no están en negociación, las stablecoins (USDC, FDUSD...), las
acciones tokenizadas de Binance (bStocks) y, en futuros, los contratos trimestrales y los que
no son criptomonedas (índices como BTCDOM). Los pares con menos de 99 velas (listados hace
poco) no se pueden evaluar porque no tienen MA(99).

## Versión web (en el navegador)

La web es el escáner completo en una sola página: pulsas **Escanear** y aparecen las monedas
con sus gráficos. Las peticiones a Binance salen **desde tu navegador y tu conexión**, así que
no hace falta ningún servidor ni que Binance acepte las IP de GitHub.

- **Escanear:** mercado (spot y futuros, solo spot o solo futuros) y velas (1D por defecto).
  Cada petición a Binance tiene un tiempo máximo y se reintenta si no responde o da error; las
  que siguen fallando se reintentan al final, y el aviso dice cuántos pares no respondieron. Todo
  lo que la página pide a Binance comparte el límite de peticiones por minuto, y si hay que
  esperar, la barra lo dice con los segundos que faltan. El escaneo solo baja el gráfico
  completo de la temporalidad escaneada; las demás se piden al ver cada tarjeta.
- **Filtros:** margen bajo la MA7, máximo sobre la MA7, volumen mínimo en 24 h, no exigir
  MA25 < MA99, incluir velas rojas y vela cerrada (los criterios de la tabla de arriba). La
  lista **se actualiza al momento mientras escribes**, sin volver a escanear: el escaneo
  guarda todas las monedas con MA(7) < MA(25) y los filtros eligen entre ellas. **Restablecer**
  vuelve a los valores por defecto, y la página recuerda los filtros para la próxima vez.
- **Acumulación: velas entre ± X % durante al menos N días** (casilla para activarlo). Deja solo
  las monedas que, justo antes de la vela evaluada (la que toma fuerza), tuvieron al menos N
  velas seguidas que subieron o bajaron como mucho un X % de la apertura al cierre. Por
  ejemplo, con 4 % y 5 días: una moneda con velas de +3,5 %, −3 %, +1 %, −0,5 % y +2 % pasa; si
  una de esas velas fue de +9 %, no. Se suma a los demás filtros, la tarjeta dice cuántas velas
  lleva acumulando y el gráfico sombrea esas velas. Con velas que no son 1D cuenta velas en vez
  de días.
- Una **tarjeta por moneda** con su gráfico de velas y las tres medias con los colores de
  Binance (MA(7) amarillo, MA(25) rosa, MA(99) morado), la variación de la vela, cuánto está
  sobre la MA(7), cuánto le falta a la MA(7) para cruzar la MA(25), las velas seguidas sobre
  la MA(7) y el volumen.
- **Estrella** (☆ junto a ampliar): fija la moneda. En **Seguimiento** sale la primera de su día;
  si la moneda no se había guardado (escaneo sin **Guardar en seguimiento**), se añade con su
  precio del escaneo. Se guarda como el resto del seguimiento (navegador y GitHub).
- **Cumple en** marca en qué temporalidades (2h, 4h, 8h, 12h y 1D) se cumple también la
  condición.
- Filtros **Todos / Spot / Futuros / No repetidas** (cada moneda una vez; si está en los dos
  mercados se queda la de spot), **Orden** (vela más fuerte, más cerca del cruce MA7/MA25, más
  pegadas a la MA7, más bajistas, volumen o variación 24h, símbolo) y **Buscar moneda**.
- **Gráfico: 15m … 1D … 1W** cambia la temporalidad de todos los gráficos (por defecto 1D) y
  pide a Binance las velas del momento. **Tamaño S / M / L** cambia el tamaño de las tarjetas.
- En la cuadrícula, la rueda del ratón desplaza la página. Al pulsar una tarjeta (o su botón
  de ampliar) el gráfico se abre a **pantalla completa**:
  - **la rueda del ratón o el pellizco acercan y alejan**, y arrastrando te mueves en el tiempo;
  - botones `−` / `+` y «ver todo el historial» (teclas `-`, `+` y `0`);
  - al pasar el ratón se ven apertura, máximo, mínimo, cierre y el valor de cada media;
  - selector de temporalidad propio (un punto marca dónde también se cumple la condición);
  - `←` / `→` pasan a la moneda anterior o siguiente y `Esc` cierra;
  - enlace directo al par en Binance.
- **Cargar informe** (o arrastrar un archivo a la página) abre el `.html` o el `.json` que
  genera el workflow **Scanner** o la línea de comandos. Un informe solo trae las monedas que
  cumplían sus criterios, así que ahí los filtros empiezan en esos criterios y solo pueden
  quitar monedas.

### Seguimiento

Cada escaneo (con **Guardar en seguimiento** marcado) guarda las monedas que cumplen los
filtros en ese momento, con su precio, en un apartado **«Seguimiento 1 de octubre»**, **«Seguimiento 2 de
octubre»**… Si escaneas varias veces el mismo día, se añaden las monedas nuevas y cada una
conserva el primer precio del día.

Cada apartado es **la vela diaria de Binance**: cambia a las **00:00 UTC**, cuando abre la vela
nueva, y se nombra con la fecha en que abre en tu hora, igual que la muestra Binance con tu
zona horaria. En Lima (UTC−5) la vela abre a las **7:00 pm**:

| Escaneo (hora de Lima) | Vela de Binance | Apartado |
|---|---|---|
| 1 de octubre, 2:39 pm | abrió el 30 de septiembre a las 7:00 pm | «Seguimiento 30 de septiembre» |
| 1 de octubre, 7:30 pm | abrió el 1 de octubre a las 7:00 pm | «Seguimiento 1 de octubre» |
| 2 de octubre, 8:00 am | sigue la del 1 de octubre | «Seguimiento 1 de octubre» |

Los gráficos y las horas se muestran en tu zona horaria, como en Binance.

En la pestaña **Seguimiento**:

- un apartado por día (el más reciente abierto) con estas columnas:

  | Columna | Qué es |
  |---|---|
  | **Moneda** | con su estrella y su mercado |
  | **Precio 5 oct** | el precio de seguimiento: el de la moneda al escanear (fijo); el título lleva la fecha del día |
  | **Precio actual** | el de Binance ahora, actualizado cada minuto |
  | **Mínimo** | el precio más bajo **desde el escaneo**, con su % frente al precio de seguimiento |
  | **Máximo** | el precio más alto **desde el escaneo**, con su % frente al precio de seguimiento |
  | **Rumbo al 50** | cuántos días tardó el máximo en llegar a **+50 %** sobre el precio de seguimiento, y la fecha (por ejemplo, «3 días · 8 oct»); esa fila se pinta de verde. «—» mientras no llegue |
  | **Variación** | precio actual frente al precio de seguimiento |
  | **Escaneo** | hora del escaneo y temporalidad |

  y un resumen: media, cuántas suben y bajan, la mejor, la peor, cuántas tienen estrella y
  cuántas llegaron al +50 %. El
  mínimo y el máximo salen de las velas de Binance desde el minuto del escaneo (el precio de
  seguimiento también cuenta) y se recalculan cada 5 minutos y con **Actualizar precios** (solo
  se piden las velas nuevas);
- las monedas con **estrella** van primero en su día; la estrella se pone o se quita en la
  propia fila;
- filtros **Todos / Spot / Futuros / No repetidas** y orden por subida, bajada, mayor máximo,
  peor mínimo, rumbo al 50 (las que llegaron antes), moneda u orden del escaneo;
- al pulsar una moneda se abre su gráfico (1D por defecto) con una línea en el precio del
  seguimiento y una flecha en la vela de ese día;
- **Borrar** quita un día.

**Dónde se guarda.** Siempre en el navegador donde escaneas. Para tenerlo también en el
repositorio, y verlo igual en el móvil y en el ordenador, pulsa **Guardar también en GitHub**
y pega una clave de GitHub (*fine-grained token*) creada en
<https://github.com/settings/personal-access-tokens/new> con:

- **Repository access → Only select repositories →** este repositorio;
- **Permissions → Repository permissions → Contents → Read and write**.

La clave se guarda solo en ese navegador y solo se envía a GitHub. Cada día queda como un
archivo `docs/seguimiento/AAAA-MM-DD.json`, y el workflow **Web** los une en
`seguimiento.json` al publicar la página (en otros dispositivos aparece en un minuto). Lo que
escaneas sin conexión con GitHub se sube al conectarlo. Las estrellas se suben unos segundos
después del último cambio, en un solo guardado, y si un escaneo repetido no cambia nada no se
crea ningún commit.

**Hasta cuándo se guarda.** En el repositorio, **para siempre**: cada día es un archivo que
solo se borra con **Borrar**. GitHub no los caduca, y a unos 20 KB por día el repositorio tardaría
más de cien años en llegar a sus límites. El navegador guarda una copia de hasta unos 2,5 MB (meses
de escaneos); si se llena, olvida primero los días más antiguos que ya están en GitHub, que se
siguen viendo desde allí. Sin GitHub conectado, esa copia del navegador es la única, así que
borrar los datos del navegador la borra. La clave de GitHub caduca en la fecha que elegiste al
crearla: entonces la página avisa de que no es válida o ha caducado, sigue guardando en el
navegador y lo sube todo al conectar una clave nueva.

## Workflows de GitHub

| Workflow | Qué hace | Cuándo |
|---|---|---|
| **Scanner** | Pasa los tests y hace un escaneo real desde los servidores de GitHub. Resultado en el resumen de la ejecución e informe con los gráficos (`.html`, `.json`, `.csv`) en **Artifacts → momento-a**. | A mano en **Actions → Scanner → Run workflow** (con los filtros como opciones), y en cada pull request y cada cambio en `main`. |
| **Web** | Publica la web en GitHub Pages con la lista de acciones tokenizadas al día y el seguimiento guardado en el repositorio. | En cada cambio en `main`, una vez al día y a mano. |

Para que **Web** publique la página, GitHub Pages tiene que estar activado con
**Settings → Pages → Build and deployment → Source: GitHub Actions**.

Los servidores de GitHub están en EE. UU., donde Binance bloquea `api.binance.com` y
`fapi.binance.com` (error 451). El scanner lo detecta y pasa solo a `data-api.binance.vision`
(spot) y `www.binance.com` (futuros), que sirven los mismos datos públicos; lo indica en el
resultado.

## Línea de comandos

Necesitas Python 3.9 o superior.

```bash
pip install -r requirements.txt
python -m momento_a
```

Ejemplo de salida:

```
Velas 1D · vela en curso · 2026-10-01 18:40 UTC · 1017 pares (SPOT 492, FUT 525)
Criterios: MA7 < MA25 < MA99 · precio ≥ MA7 −2% · vela verde
Coincidencias: 4

MERCADO  PAR             PRECIO  VELA %  vs MA7 %  MA7/MA25 %  vs MA99 %  VELAS≥MA7  VOL 24H (USDT)
-------  ------------  --------  ------  --------  ----------  ---------  ---------  --------------
FUT      1000PEPEUSDT  0.008800   +7.32     +3.53       -8.88     -32.34          1          840.0M
SPOT     SOLUSDT       136.0000   +3.66     +0.51       -9.22     -34.64          1          312.0M
FUT      SOLUSDT       136.0000   +3.66     +0.51       -9.22     -34.64          1            2.1B
SPOT     ARBUSDT         0.4180   +1.95     -0.91       -9.38     -35.71          0           45.0M
```

(Los números del ejemplo son ilustrativos.)

| Columna | Significado |
|---|---|
| VELA % | Variación de la vela evaluada (precio actual frente a la apertura). Es la "fuerza". |
| vs MA7 % | Distancia del precio a la MA(7). Negativo = todavía debajo, positivo = ya encima. |
| MA7/MA25 % | Distancia de la MA(7) a la MA(25). Siempre negativa; cuanto más cerca de 0, más cerca del cruce alcista. |
| vs MA99 % | Distancia del precio a la MA(99). Cuanto más negativa, más bajista es la tendencia. |
| VELAS≥MA7 | Velas seguidas cerrando sobre la MA(7). `1` = la cruza en esta vela; `0` = aún está justo debajo. |
| VOL 24H | Volumen de las últimas 24 h, en la moneda de cotización. |

### Opciones útiles

```bash
# Solo futuros, ordenado por lo cerca que está la MA(7) de cruzar la MA(25)
python -m momento_a --mercado futuros --orden cruce

# Velas de 4h en vez de 1D
python -m momento_a --intervalo 4h

# Solo spot, más estricto (máximo 1 % por debajo de la MA7 y no más de 5 % por encima)
python -m momento_a --mercado spot --tolerancia 1 --max-encima 5

# Filtrar monedas con poco volumen y guardar los resultados
python -m momento_a --volumen-min 5000000 --csv resultados.csv

# Informe con los gráficos (ábrelo en el navegador) y el mismo informe en JSON
python -m momento_a --html informe.html --json informe.json

# Ver todas las opciones
python -m momento_a --help
```

Órdenes disponibles (`--orden`): `fuerza` (por defecto, la vela más fuerte primero), `cruce`
(MA(7) más cerca de la MA(25)), `ma7` (las más pegadas a la MA(7)), `bajista` (las más
alejadas por debajo de la MA(99)), `volumen` y `variacion` (de las últimas 24 h) y `simbolo`.

El CSV es la tabla con los valores de MA(7), MA(25) y MA(99) y el enlace al par en Binance. El
JSON y el HTML llevan además las velas de cada moneda en 2h, 4h, 8h, 12h y 1D para los
gráficos, y se abren en la web con **Cargar informe**.

Para regenerar la web en local: `python -m momento_a.web sitio` (escribe `sitio/index.html`).

### Problemas de conexión

- **HTTP 451**: Binance bloquea su API desde algunos países; el scanner pasa solo a las
  direcciones alternativas (ver [Workflows de GitHub](#workflows-de-github)).
- **HTTP 418 / 429**: demasiadas peticiones. El scanner espera y reintenta solo; si vuelve a
  pasar, baja las descargas en paralelo con `--hilos 4`.

Solo usa datos públicos de mercado, no necesita API key.

## Tests

```bash
pip install -r requirements-dev.txt
python -m playwright install chromium   # para las pruebas de la web
python -m pytest
```

Además de las del scanner en Python, `tests/test_web_browser.py` abre la web en Chromium con
Binance y GitHub simulados y comprueba el escaneo (peticiones que no responden, errores y
pausas de Binance), el guardado en GitHub tras escanear, las estrellas, las columnas del
seguimiento, «Rumbo al 50», el filtro de acumulación y la vista en el móvil. El workflow
**Scanner** las pasa en cada cambio. Sin Playwright instalado se omiten.

> Esto es una herramienta de filtrado, no una recomendación de inversión.
