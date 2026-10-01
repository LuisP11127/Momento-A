import json

import pytest

from helpers import downtrend
from momento_a.binance import FUTURES, SPOT, BinanceError, BinanceFatalError, Instrument
from momento_a.report import CHART_INTERVALS, CHART_LIMIT, TEMPLATE, fetch_chart_candles, render_html

BTC = Instrument(SPOT, "BTCUSDT", "BTC", "USDT")
ETH = Instrument(FUTURES, "ETHUSDT", "ETH", "USDT")


class FakeClient:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}  # {(symbol, interval): exception}

    def candles(self, instrument, interval, limit):
        self.calls.append((instrument.symbol, interval, limit))
        if (instrument.symbol, interval) in self.fail:
            raise self.fail[(instrument.symbol, interval)]
        return downtrend(last_close=85.0, days=10)[0]


def embedded_data(html):
    start = html.index("window.MOMENTO_DATA = ") + len("window.MOMENTO_DATA = ")
    end = html.index(";\n", start)
    return json.loads(html[start:end])


def test_fetches_every_interval_for_every_pair():
    client = FakeClient()
    charts, errors = fetch_chart_candles(client, [BTC, ETH], workers=3)

    assert errors == {}
    assert set(charts) == {BTC, ETH}
    assert tuple(charts[BTC]) == CHART_INTERVALS
    assert sorted(client.calls) == sorted((s, iv, CHART_LIMIT) for s in ("BTCUSDT", "ETHUSDT") for iv in CHART_INTERVALS)


def test_failed_interval_is_skipped():
    client = FakeClient(fail={("BTCUSDT", "4h"): BinanceError("timeout")})
    charts, errors = fetch_chart_candles(client, [BTC], workers=2)
    assert "4h" not in charts[BTC] and "1d" in charts[BTC]
    assert list(errors) == ["spot:BTCUSDT 4h"]


def test_fatal_error_stops_chart_download():
    client = FakeClient(fail={("BTCUSDT", "2h"): BinanceFatalError("HTTP 451")})
    with pytest.raises(BinanceFatalError):
        fetch_chart_candles(client, [BTC], workers=1)


def test_render_html_embeds_compact_candles_and_escapes_script_tags():
    candles = downtrend(last_close=85.0, days=3)[0]
    record = {"mercado": "SPOT", "par": "BTCUSDT", "nota": "</script><b>x"}
    html = render_html([(record, {"1d": candles})], ["Velas 1D", "Criterios: MA7 < MA25"], "USDT", "2026-10-01 18:00 UTC")

    data = embedded_data(html)
    assert data["intervalos"] == list(CHART_INTERVALS)
    assert data["resumen"] == ["Velas 1D", "Criterios: MA7 < MA25"]
    (pair,) = data["pares"]
    assert pair["nota"] == "</script><b>x"
    first = candles[0]
    assert pair["velas"]["1d"][0] == [first.open_time // 1000, first.open, first.high, first.low, first.close, first.volume]
    # El "</script>" de los datos no aparece sin escapar: solo los de la plantilla.
    template = TEMPLATE.read_text(encoding="utf-8")
    assert html.count("</script>") == template.count("</script>")
