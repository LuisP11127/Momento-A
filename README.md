# Momento-A
Criptomonedas a punto de subir la MA(7)

Scanner de **todas las criptomonedas de Binance (spot y futuros USDT-M perpetuos)** en
velas de **1D**. Busca monedas en un momento muy bajista cuya vela diaria empieza a tomar
fuerza y quiere cambiar la tendencia.

## Qué filtra

Con MA(7), MA(25) y MA(99) (medias móviles simples sobre el cierre, igual que las del
gráfico de Binance), un par aparece si su vela de 1D cumple:

| Condición | Por defecto | Cómo cambiarla |
|---|---|---|
| Tendencia muy bajista: **MA(7) < MA(25) < MA(99)** | sí | `--sin-ma99` para exigir solo MA(7) < MA(25) |
| **La MA(7) sigue por debajo de la MA(25)** (todavía no hay cruce) | siempre | — |
| La vela está **muy cerca o por encima de la MA(7)**: precio ≥ MA(7) − 2 % | 2 % | `--tolerancia 1` |
| La vela toma fuerza: **vela verde** (precio > apertura) | sí | `--permitir-roja` |
| Límite de cuánto puede estar por encima de la MA(7) | sin límite | `--max-encima 5` |
| Volumen mínimo de la última vela cerrada | 0 | `--volumen-min 1000000` |

Se evalúa la **vela del día en curso** (las medias incluyen el precio actual, como en el
gráfico en vivo). La vela diaria de Binance abre a las 00:00 UTC. Para usar solo velas ya
cerradas: `--vela-cerrada`.

Se ignoran los pares que no están en negociación, las stablecoins (USDC, FDUSD...) y, en
futuros, los contratos trimestrales y los que no son criptomonedas (índices como BTCDOM).
Los pares con menos de 99 velas diarias (listados hace poco) no se pueden evaluar porque no
tienen MA(99).

## Instalación

Necesitas Python 3.9 o superior.

```bash
pip install -r requirements.txt
```

## Uso

```bash
python -m momento_a
```

Ejemplo de salida:

```
Velas 1D · vela del día en curso · 1012 pares (SPOT 418, FUT 594)
Criterios: MA7 < MA25 < MA99 · precio ≥ MA7 −2% · vela verde
Coincidencias: 4

MERCADO  PAR             PRECIO  VELA %  vs MA7 %  MA7/MA25 %  vs MA99 %  DÍAS≥MA7  VOL 1D (USDT)
-------  ------------  --------  ------  --------  ----------  ---------  --------  -------------
FUT      1000PEPEUSDT  0.008800   +7.32     +3.53       -8.88     -32.34         1         840.0M
SPOT     SOLUSDT       136.0000   +3.66     +0.51       -9.22     -34.64         1         312.0M
FUT      SOLUSDT       136.0000   +3.66     +0.51       -9.22     -34.64         1           2.1B
SPOT     ARBUSDT         0.4180   +1.95     -0.91       -9.38     -35.71         0          45.0M
```

(Los números del ejemplo son ilustrativos.)

| Columna | Significado |
|---|---|
| VELA % | Variación de la vela de hoy (precio actual frente a la apertura). Es la "fuerza". |
| vs MA7 % | Distancia del precio a la MA(7). Negativo = todavía debajo, positivo = ya encima. |
| MA7/MA25 % | Distancia de la MA(7) a la MA(25). Siempre negativa; cuanto más cerca de 0, más cerca del cruce alcista. |
| vs MA99 % | Distancia del precio a la MA(99). Cuanto más negativa, más bajista es la tendencia. |
| DÍAS≥MA7 | Velas seguidas cerrando sobre la MA(7). `1` = la cruza hoy por primera vez; `0` = aún está justo debajo. |
| VOL 1D | Volumen de la última vela diaria cerrada, en la moneda de cotización. |

### Opciones útiles

```bash
# Solo futuros, ordenado por lo cerca que está la MA(7) de cruzar la MA(25)
python -m momento_a --mercado futuros --orden cruce

# Solo spot, más estricto (máximo 1 % por debajo de la MA7 y no más de 5 % por encima)
python -m momento_a --mercado spot --tolerancia 1 --max-encima 5

# Filtrar monedas con poco volumen y guardar los resultados
python -m momento_a --volumen-min 5000000 --csv resultados.csv --json resultados.json

# Ver todas las opciones
python -m momento_a --help
```

Órdenes disponibles (`--orden`): `fuerza` (por defecto, la vela más fuerte primero), `ma7`
(las más pegadas a la MA(7)), `cruce` (MA(7) más cerca de la MA(25)), `bajista` (las más
alejadas por debajo de la MA(99)) y `volumen`.

El CSV/JSON incluye además los valores de MA(7), MA(25) y MA(99) y el enlace al par en
Binance.

## Problemas de conexión

- **HTTP 451**: Binance bloquea su API desde algunos países (por ejemplo, EE. UU.). Para
  spot puedes usar el espejo público de datos:
  `python -m momento_a --spot-url https://data-api.binance.vision`. Los futuros no tienen
  espejo; desde esos países solo funcionará `--mercado spot` con ese espejo.
- **HTTP 418 / 429**: demasiadas peticiones. El scanner espera y reintenta solo; si
  vuelve a pasar, baja las descargas en paralelo con `--hilos 4`.

Solo usa datos públicos de mercado, no necesita API key.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

> Esto es una herramienta de filtrado, no una recomendación de inversión.
