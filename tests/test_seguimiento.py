import json
from types import SimpleNamespace

import pytest

from momento_a.binance import FUTURES, SPOT, BinanceError, Instrument
from momento_a.giro import DAY_MS, HOUR_MS, INDEX_V1, Entry, features, index_scores
from momento_a.seguimiento import book_summary, record_scan, tracking_day
from test_giro import T0, make_symbol


def ms(iso):
    from datetime import datetime

    return int(datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp() * 1000)


def test_tracking_day_is_the_day_the_daily_candle_opens_in_lima():
    # La vela del 23 abre el 23 a las 00:00 UTC, que en Lima es el 22 a las 19:00: es el día 22, como en la web.
    assert tracking_day(ms("2026-11-23T21:37:12.749Z")) == "2026-11-22"
    assert tracking_day(ms("2026-11-24T00:00:00.000Z")) == "2026-11-23"
    assert tracking_day(ms("2026-11-23T23:59:59.999Z")) == "2026-11-22"


def test_book_summary_matches_the_web():
    """Los mismos libros y cifras que la prueba de la web (test_order_book_summary_is_saved_with_each_coin)."""
    sa = {"bids": [["100", "10"], ["99.5", "20"], ["98.5", "6"], ["96", "100"], ["90", "1000"]],
          "asks": [["100.2", "10"], ["101", "50"], ["101.5", "2"], ["104", "10"], ["120", "500"]]}
    assert book_summary(sa, 1000) == {
        "v": 1, "spread": 0.1998, "compras_1": 2990, "ventas_1": 6052, "compras_2": 3581, "ventas_2": 6255,
        "compras_5": 13181, "ventas_5": 7295, "muro_compra": 9600, "muro_compra_dist": -4.096,
        "muro_venta": 5050, "muro_venta_dist": 0.899,
    }
    # 500 niveles que no llegan al −2 %: esas bandas no se guardan; las ventas son el libro entero.
    fa = {"bids": [[f"{100 - i * 0.003:.3f}", "1"] for i in range(500)], "asks": [["100.01", "1"], ["101", "1"]]}
    book = book_summary(fa, 500)
    assert "compras_1" in book and "compras_2" not in book and "compras_5" not in book
    assert {"ventas_1", "ventas_2", "ventas_5"} <= set(book)
    assert book_summary({"bids": [], "asks": [["1", "1"]]}, 500) is None


class FakeClient:
    """Binance simulada para guardar un escaneo: velas de 1 hora de make_symbol, libro y ratio long/short."""

    def __init__(self, data, futures=("AAAUSDT",), book_fails=()):
        self.data = data  # símbolo → SymbolData
        self.futures = futures
        self.book_fails = book_fails
        self.calls = []

    def instruments(self, market, quote="USDT"):
        return [Instrument(FUTURES, s, s[:-4], "USDT") for s in self.futures]

    def candles(self, instrument, interval, limit):
        self.calls.append(("candles", instrument.symbol, interval))
        return [c for c in self.data[instrument.symbol].hourly if c.open_time <= self.now][-limit:]  # las últimas, como Binance

    def depth(self, market, symbol, limit):
        self.calls.append(("depth", market, symbol, limit))
        if symbol in self.book_fails:
            raise BinanceError("sin libro")
        return {"bids": [["9.9", "10"], ["9.8", "20"]], "asks": [["10.1", "10"], ["10.2", "5"]]}

    def futures_data(self, path, params):
        self.calls.append(("long_short", params["symbol"]))
        return [{"timestamp": self.now // HOUR_MS * HOUR_MS, "longShortRatio": "2.5"}]


def test_record_scan_saves_coins_with_their_giro_index_and_order_book(tmp_path):
    scan = T0 + 200 * DAY_MS + 15 * HOUR_MS
    data = {"AAAUSDT": make_symbol(scan, buy_share=0.6), "BBBUSDT": make_symbol(scan, buy_share=0.4)}
    client = FakeClient({k: d for k, (d, _) in data.items()}, book_fails=("BBBUSDT",))
    client.now = scan
    signals = [SimpleNamespace(instrument=Instrument(m, s, s[:-4], "USDT"), price=data[s][1],
                               candles=[c for c in data[s][0].daily if c.open_time <= scan])
               for m, s in ((FUTURES, "AAAUSDT"), (SPOT, "BBBUSDT"))]
    fecha = tracking_day(scan)
    # Ya había un escaneo ese día con BBB: se queda el suyo (su primer precio del día).
    old = {"mercado": SPOT, "simbolo": "BBBUSDT", "base": "BBB", "quote": "USDT", "precio": 1.0, "hora": "x", "intervalo": "1d"}
    (tmp_path / f"{fecha}.json").write_text(json.dumps({"fecha": fecha, "monedas": [old]}), encoding="utf-8")

    assert record_scan(client, signals, tmp_path, "1d", scan, log=lambda m: None) == (fecha, 1, 2)
    day = json.loads((tmp_path / f"{fecha}.json").read_text(encoding="utf-8"))
    assert day["fecha"] == fecha and day["monedas"][0] == old
    aaa = day["monedas"][1]
    assert {k: aaa[k] for k in ("mercado", "simbolo", "base", "quote", "intervalo")} == {
        "mercado": FUTURES, "simbolo": "AAAUSDT", "base": "AAA", "quote": "USDT", "intervalo": "1d"}
    assert aaa["precio"] == data["AAAUSDT"][1] and aaa["hora"].endswith("Z")
    assert aaa["libro"]["compras_2"] == 295 and aaa["libro"]["ventas_2"] == 152  # 9,9×10 + 9,8×20 | 10,1×10 + 10,2×5
    # El índice es el de giro.py: las dos monedas del escaneo, comparadas entre sí.
    f = [features(Entry("", m.instrument.market, m.instrument.symbol, m.price, scan), data[m.instrument.symbol][0])
         for m in signals]
    f[0]["long_short"] = 2.5
    expected = index_scores([{"fecha": "", "senales": x} for x in f], INDEX_V1)
    assert abs(aaa["giro"]["indice"] - expected[0]) <= 0.5 and aaa["giro"]["long_short"] == 2.5
    assert aaa["giro"]["acumulacion"] in (0, 1) and isinstance(aaa["giro"]["acumulacion"], int)
    # El ratio long/short solo se pide para las que tienen contrato perpetuo.
    assert [c for c in client.calls if c[0] == "long_short"] == [("long_short", "AAAUSDT")]
    assert ("depth", SPOT, "BBBUSDT", 1000) in client.calls and ("depth", FUTURES, "AAAUSDT", 500) in client.calls


def test_record_scan_without_daily_candles_saves_no_giro(tmp_path):
    scan = T0 + 200 * DAY_MS + 15 * HOUR_MS
    d, price = make_symbol(scan)
    client = FakeClient({"AAAUSDT": d})
    client.now = scan
    signal = SimpleNamespace(instrument=Instrument(FUTURES, "AAAUSDT", "AAA", "USDT"), price=price,
                             candles=[c for c in d.daily if c.open_time <= scan])
    fecha, added, total = record_scan(client, [signal], tmp_path, "4h", scan, log=lambda m: None)
    coin = json.loads((tmp_path / f"{fecha}.json").read_text(encoding="utf-8"))["monedas"][0]
    assert added == total == 1 and "giro" not in coin and coin["libro"]["spread"] == pytest.approx(2.0)
    assert coin["intervalo"] == "4h" and not [c for c in client.calls if c[0] in ("candles", "long_short")]
