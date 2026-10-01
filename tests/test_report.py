import json
from dataclasses import replace
from datetime import datetime, timezone

import pytest

from helpers import downtrend
from momento_a.binance import FUTURES, SPOT, BinanceError, BinanceFatalError, Instrument
from momento_a.report import (
    CHART_INTERVALS,
    CHART_LIMIT,
    build_payload,
    chart_intervals,
    fetch_chart_candles,
    render_report,
)
from momento_a.scanner import Criteria, ScanResult, evaluate

BTC = Instrument(SPOT, "BTCUSDT", "BTC", "USDT")
ETH = Instrument(FUTURES, "ETHUSDT", "ETH", "USDT")
WHEN = datetime(2026, 10, 1, 18, 0, tzinfo=timezone.utc)


class FakeClient:
    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail or {}  # {(symbol, interval): exception}

    def candles(self, instrument, interval, limit):
        self.calls.append((instrument.symbol, interval, limit))
        if (instrument.symbol, interval) in self.fail:
            raise self.fail[(instrument.symbol, interval)]
        return downtrend(last_close=85.0, days=10)[0]


def embedded(html):
    start = html.index('<script id="momento-data" type="application/json">') + len('<script id="momento-data" type="application/json">')
    return json.loads(html[start : html.index("</script>", start)])


def test_chart_intervals_include_the_scan_interval_in_order():
    assert chart_intervals("1d") == ["2h", "4h", "8h", "12h", "1d"]
    assert chart_intervals("15m") == ["15m", "2h", "4h", "8h", "12h", "1d"]
    assert chart_intervals("1w") == ["2h", "4h", "8h", "12h", "1d", "1w"]


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


def sample_result():
    strong = replace(evaluate(ETH, downtrend(last_close=88.0)[0], Criteria(), 0), change_24h_pct=4.5, quote_volume_24h=2e6)
    weak = replace(evaluate(BTC, downtrend(last_close=85.0)[0], Criteria(), 0), change_24h_pct=-1.0, quote_volume_24h=9e6)
    return ScanResult(signals=[weak, strong], instruments=[BTC, ETH], analyzed={SPOT: 400, FUTURES: 500})


def test_payload_has_markets_criteria_and_candles_for_the_charts():
    result = sample_result()
    chart_1d = downtrend(last_close=85.0, days=200)[0]
    chart_4h = downtrend(last_close=85.0, days=150)[0]
    criteria = Criteria(tolerance_pct=1.5, max_above_pct=5.0, require_green=False)
    payload = build_payload(result, criteria, WHEN, "fuerza", charts={BTC: {"1d": chart_1d, "4h": chart_4h}})

    assert payload["generado"] == "2026-10-01T18:00:00+00:00"
    assert payload["intervalo"] == "1d" and payload["intervalos_grafico"] == ["2h", "4h", "8h", "12h", "1d"]
    assert payload["criterios"] == {"tolerancia": 1.5, "max_encima": 5.0, "ma99": True, "verde": False, "min_volumen": 0.0}
    assert {k: (m["nombre"], m["analizadas"]) for k, m in payload["mercados"].items()} == {
        "spot": ("Spot", 400), "futures": ("Futuros", 500)}

    (btc,) = payload["mercados"]["spot"]["coincidencias"]
    assert btc["simbolo"] == "BTCUSDT" and btc["mercado"] == "spot" and btc["intervalo"] == "1d"
    assert btc["velas_sobre_ma7"] == 1 and btc["var_24h_pct"] == -1.0 and btc["volumen_24h"] == 9e6
    assert btc["ma7"] < btc["ma25"] < btc["ma99"] and btc["vs_ma7_pct"] > 0
    # Las velas de la temporalidad del escaneo son las del gráfico (más largas); el resto va en «graficos».
    assert len(btc["velas"]) == 200 and btc["velas"][0] == [chart_1d[0].open_time, chart_1d[0].open, chart_1d[0].high,
                                                            chart_1d[0].low, chart_1d[0].close, chart_1d[0].volume]
    assert list(btc["graficos"]) == ["4h"]
    # Sin gráficos descargados se usan las velas del escaneo.
    (eth,) = payload["mercados"]["futures"]["coincidencias"]
    assert len(eth["velas"]) == 120 and eth["graficos"] == {}


def test_render_report_embeds_payload_safely():
    payload = build_payload(sample_result(), Criteria(), WHEN)
    payload["nota"] = "</script><b>x"
    html = render_report(payload)

    assert html.startswith("<!doctype html>") and "<title>Momento-A Scanner</title>" in html
    assert embedded(html) == json.loads(json.dumps(payload))
    assert "</script><b>x" not in html  # el "<" de los datos va escapado


def test_render_report_without_data_is_the_web_app():
    html = render_report(None)
    assert embedded(html) is None
    assert 'id="scan-form"' in html and 'value="1d" selected' in html
    # Los criterios del workflow Scanner, como filtros que se aplican al momento.
    for field in ("f-tol", "f-max", "f-vol", "f-noma99", "f-red", "f-closed"):
        assert f'id="{field}"' in html
