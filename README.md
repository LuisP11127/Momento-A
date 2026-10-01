# Momento-A
Criptomonedas a punto de subir la MA(7)

Scanner de **todas las criptomonedas de Binance (spot y futuros USDT-M perpetuos)**, en
velas de **1D** por defecto. Busca monedas en un momento muy bajista cuya vela empieza a
tomar fuerza y quiere cambiar la tendencia.

**Web:** https://luisp11127.github.io/Momento-A/

## Qué filtra

Con MA(7), MA(25) y MA(99) (medias móviles simples sobre el cierre, igual que las del
gráfico de Binance), un par aparece si su vela cumple:

| Condición | Por defecto | En la web | En la línea de comandos |
|---|---|---|---|
| Tendencia muy bajista: **MA(7) < MA(25) < MA(99)** | sí | casilla **MA25 < MA99** | `--sin-ma99` exige solo MA(7) < MA(25) |
| **La MA(7) sigue por debajo de la MA(25)** (todavía no hay cruce) | siempre | — | — |
| La vela está **muy cerca o por encima de la MA(7)**: precio ≥ MA(7) − 2 % | 2 % | **Margen bajo MA7** | `--tolerancia 1` |
| La vela toma fuerza: **vela verde** (precio > apertura) | sí | casilla **Vela verde** | `--permitir-roja` |
| Límite de cuánto puede estar por encima de la MA(7) | sin límite | — | `--max-encima 5` |
| Volumen mínimo en las últimas 24 h | 0 | **Vol. mín. 24h** | `--volumen-min 1000000` |
| Temporalidad de las velas | **1D** | **Velas** | `--intervalo 4h` |

Se evalúa la **vela en curso** (las medias incluyen el precio actual, como en el gráfico en
vivo). La vela diaria de Binance abre a las 00:00 UTC.

Se ignoran los pares que no están en negociación, las stablecoins (USDC, FDUSD...), las
acciones tokenizadas de Binance (bStocks) y, en futuros, los contratos trimestrales y los que
no son criptomonedas (índices como BTCDOM). Los pares con menos de 99 velas (listados hace
poco) no se pueden evaluar porque no tienen MA(99).

## Versión web (en el navegador)

La web es el escáner completo en una sola página: pulsas **Escanear** y aparecen las monedas
con sus gráficos. Las peticiones a Binance salen **desde tu navegador y tu conexión**, así que
no hace falta ningún servidor ni que Binance acepte las IP de GitHub.

- **Escanear:** mercado (spot y futuros, solo spot o solo futuros), velas (1D por defecto),
  volumen mínimo en 24 h y los criterios de la tabla de arriba.
- Una **tarjeta por moneda** con su gráfico de velas y las tres medias con los colores de
  Binance (MA(7) amarillo, MA(25) rosa, MA(99) morado), la variación de la vela, cuánto está
  sobre la MA(7), cuánto le falta a la MA(7) para cruzar la MA(25), las velas seguidas sobre
  la MA(7) y el volumen.
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
  genera el workflow **Scanner** o la línea de comandos.

### Seguimiento

Cada escaneo (con **Guardar en seguimiento** marcado) guarda las monedas encontradas, con su
precio en ese momento, en un apartado **«Seguimiento 1 de octubre»**, **«Seguimiento 2 de
octubre»**… Si escaneas varias veces el mismo día, se añaden las monedas nuevas y cada una
conserva el primer precio del día.

En la pestaña **Seguimiento**:

- un apartado por día (el más reciente abierto) con el precio de entonces, el precio actual y
  la variación, y un resumen: media, cuántas suben y bajan, la mejor y la peor. Los precios se
  piden a Binance desde tu navegador y se actualizan cada minuto;
- filtros **Todos / Spot / Futuros / No repetidas** y orden por subida, bajada, moneda u orden
  del escaneo;
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
escaneas sin conexión con GitHub se sube al conectarlo.

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
python -m pytest
```

> Esto es una herramienta de filtrado, no una recomendación de inversión.
