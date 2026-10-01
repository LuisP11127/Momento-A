from momento_a.binance import Candle

DAY_MS = 86_400_000
START_MS = 1_700_006_400_000  # 2023-11-15 00:00 UTC


def make_candles(closes, last_open=None, in_progress=True, volume=1_000_000.0):
    """Velas diarias consecutivas; cada una abre en el cierre de la anterior.

    ``last_open`` fuerza la apertura de la última vela. Devuelve las velas y
    un ``now_ms`` situado dentro de la última vela (si ``in_progress``) o
    justo después de su cierre.
    """
    candles = []
    for i, close in enumerate(closes):
        open_ = closes[i - 1] if i else close
        if i == len(closes) - 1 and last_open is not None:
            open_ = last_open
        open_time = START_MS + i * DAY_MS
        candles.append(
            Candle(
                open_time=open_time,
                open=open_,
                high=max(open_, close),
                low=min(open_, close),
                close=close,
                close_time=open_time + DAY_MS - 1,
                quote_volume=volume,
            )
        )
    last_open_time = candles[-1].open_time
    now_ms = last_open_time + DAY_MS // 2 if in_progress else last_open_time + DAY_MS
    return candles, now_ms


def downtrend(last_close, days=120, last_open=None):
    """Caída de 1 por día desde 200 y una última vela que cierra en ``last_close``.

    Cierres previos a la última vela: ..., 87, 86, 85, 84, 83, 82.
    MA(7) con la última vela = (507 + last_close) / 7.
    """
    closes = [200.0 - i for i in range(days - 1)] + [last_close]
    return make_candles(closes, last_open=last_open)
