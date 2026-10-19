import json

import pytest

from momento_a.binance import FUTURES, SPOT, Candle, Instrument
from momento_a.giro import (
    DAY_MS,
    HOUR_MS,
    INDEX_V1,
    V1_DESDE,
    Dataset,
    Entry,
    SymbolData,
    auc,
    book_signals,
    build_dataset,
    daily_view,
    evaluate,
    features,
    first_appearances,
    index_scores,
    load_entries,
    outcomes,
    report_markdown,
    spearman,
    write_outputs,
)

T0 = 1_759_276_800_000  # 2025-10-01 00:00 UTC


def candle(open_time, step, o, c, volume=100.0, buy_share=0.5, high=None):
    return Candle(open_time=open_time, open=o, high=high if high is not None else max(o, c) * 1.001, low=min(o, c) * 0.999,
                  close=c, close_time=open_time + step - 1, quote_volume=volume * c, volume=volume,
                  taker_buy_volume=volume * buy_share, trades=10)


def series(start, step, count, price, buy_share=0.5, volume=100.0):
    """Velas consecutivas; price(i) da el cierre de la vela i (abre en el cierre anterior)."""
    out, prev = [], price(0)
    for i in range(count):
        c = price(i)
        out.append(candle(start + i * step, step, prev, c, volume, buy_share(i) if callable(buy_share) else buy_share))
        prev = c
    return out


def test_auc_and_spearman():
    assert auc([1, 2, 3, 4], [False, False, True, True]) == 1.0
    assert auc([4, 3, 2, 1], [False, False, True, True]) == 0.0
    assert auc([5, 5, 5, 5], [False, True, False, True]) == 0.5
    assert auc([1, 2], [True, True]) is None
    assert spearman([1, 2, 3, 4], [10, 20, 30, 40]) == pytest.approx(1.0)
    assert spearman([1, 2, 3, 4], [4, 3, 2, 1]) == pytest.approx(-1.0)


def test_daily_view_only_uses_what_was_known_at_the_scan():
    day0 = T0 + 30 * DAY_MS
    daily = series(T0, DAY_MS, 31, lambda i: 100 - i)  # la última (day0) ya cerrada, con su máximo final
    daily[-1] = candle(day0, DAY_MS, 70, 200, high=500)  # lo que pasó DESPUÉS del escaneo
    hourly = series(day0, HOUR_MS, 10, lambda i: 70 + i)  # 10 horas del día del escaneo
    scan = day0 + 10 * HOUR_MS + 5 * 60_000  # 10:05
    view = daily_view(SymbolData(hourly=hourly, daily=daily), scan, price=81.0)
    assert len(view) == 31
    today = view[-1]
    assert today.open == 70 and today.close == 81.0 and today.high < 100  # nada del máximo de 500 ni del cierre de 200
    assert today.volume == pytest.approx(10 * 100)


def make_symbol(scan_ms, buy_share=0.5, after=lambda h: 1.0, days_before=140):
    """Datos de una moneda: precio bajando un 0,05 % por hora hasta el escaneo y, después, el precio del
    escaneo por ``after(horas desde el escaneo)``. Las velas diarias se forman con las de 1 hora.
    Devuelve los datos y el precio en el escaneo."""
    origin = scan_ms // DAY_MS * DAY_MS - days_before * DAY_MS
    n_before = (scan_ms - origin) // HOUR_MS
    at_scan = 100 * 0.9995 ** (n_before - 1)
    hourly = series(origin, HOUR_MS, int(n_before) + 9 * 24,
                    lambda i: 100 * 0.9995 ** i if i < n_before else at_scan * after(i - n_before), buy_share)
    daily = []
    for d in range(0, len(hourly) // 24):
        hs = hourly[d * 24:(d + 1) * 24]
        daily.append(Candle(open_time=hs[0].open_time, open=hs[0].open, high=max(h.high for h in hs), low=min(h.low for h in hs),
                            close=hs[-1].close, close_time=hs[-1].close_time, quote_volume=sum(h.quote_volume for h in hs),
                            volume=sum(h.volume for h in hs), taker_buy_volume=sum(h.taker_buy_volume for h in hs)))
    return SymbolData(hourly=hourly, daily=daily), at_scan


def test_features_measure_buying_flow_and_outcomes_measure_what_came_after():
    scan = T0 + 200 * DAY_MS + 12 * HOUR_MS
    data, price = make_symbol(scan, buy_share=lambda i: 0.7, after=lambda h: 1.3 if 30 <= h < 40 else 1.0)
    entry = Entry("2025-10-01", SPOT, "AAAUSDT", price, scan)
    f = features(entry, data)
    assert f["flujo_24h"] == pytest.approx(70.0)
    assert f["cvd_7d"] == pytest.approx(0.4)
    assert f["precio_7d"] < 0 and f["acumulacion"] == 1.0  # compras a mercado mientras el precio cae
    assert f["brecha_ma7_ma25"] < 0 and f["dist_ma99"] < 0 and f["rsi"] is not None
    assert f["oi_24h"] is None and f["financiacion"] is None  # sin datos de futuros
    o = outcomes(entry, data, now_ms=scan + 30 * DAY_MS)
    assert o["max_1d"] < 1 < o["max_3d"]  # el salto de +30 % llegó entre las horas 30 y 40
    assert o["max_3d"] == pytest.approx((1.3 * 1.001 - 1) * 100, rel=1e-6)
    assert outcomes(entry, data, now_ms=scan + 2 * DAY_MS)["max_3d"] is None  # todavía no han pasado 3 días
    assert o["gana_antes_3d"] == 1.0 and o["gana_antes_1d"] == 0.0  # +20 % sin caer antes −10 %
    # Si antes de subir cae más de un 10 %, no cuenta como acierto aunque luego llegue al +30 %.
    dip, price2 = make_symbol(scan, after=lambda h: 0.85 if 5 <= h < 10 else 1.3 if 30 <= h < 40 else 1.0)
    o2 = outcomes(Entry("2025-10-01", SPOT, "AAAUSDT", price2, scan), dip, now_ms=scan + 30 * DAY_MS)
    assert o2["max_3d"] > 20 and o2["gana_antes_3d"] == 0.0


def rows_for(signal_values, gains, days):
    return [{"fecha": d, "mercado": SPOT, "simbolo": f"S{i}USDT", "hora_ms": i, "precio": 1.0, "estrella": False,
             "senales": {"flujo_24h": v, "rsi": 50.0 + (i % 3)},
             "resultado": {"max_7d": g, "max_3d": g, "min_7d": -5.0, "gana_antes_7d": float(g >= 20)}}
            for i, (v, g, d) in enumerate(zip(signal_values, gains, days))]


def test_evaluate_finds_the_signal_that_separates_winners():
    days = [f"2025-10-0{1 + i % 4}" for i in range(80)]
    values = [float(i % 20) for i in range(80)]
    gains = [40.0 if v >= 12 else 5.0 for v in values]  # suben las de más compras a mercado
    ev = evaluate(rows_for(values, gains, days), horizon=7, threshold=20, sample="prueba")
    assert ev.n == 80 and ev.winners == sum(g >= 20 for g in gains)
    top = ev.signals[0]
    assert top["senal"] == "flujo_24h" and top["auc"] == pytest.approx(1.0)
    assert ev.index_cv["auc"] == pytest.approx(1.0) and ev.index_cv["senales"][0][0] == "flujo_24h"
    assert [q["acierto"] for q in ev.index_v0["quintiles"]][-1] == 100.0
    assert evaluate(rows_for(values, gains, days), 7, 20, "prueba", mode="antes").signals[0]["auc"] == pytest.approx(1.0)


def v1_rows(days, per_day=10):
    """Monedas con las señales del índice v1; suben las del mínimo más alto y las de compras de 24 h a la baja.
    La web guardó su índice solo en los días desde V1_DESDE."""
    rows = []
    for d in days:
        for i in range(per_day):
            low, flow = float(i), float(per_day - i)
            gain = 40.0 if i >= per_day // 2 else 5.0
            rows.append({"fecha": d, "mercado": SPOT, "simbolo": f"S{i}USDT", "hora_ms": i, "precio": 1.0, "estrella": False,
                         "senales": {"minimo_mas_alto": low, "flujo_tendencia": flow, "long_short": None, "acumulacion": None},
                         "giro_web": i * 10.0 if d >= V1_DESDE else None,
                         "resultado": {"max_7d": gain, "min_7d": -5.0, "gana_antes_7d": float(gain >= 20)}})
    return rows


def test_index_v1_is_measured_on_new_days_and_as_saved_by_the_web():
    assert set(INDEX_V1) == {"minimo_mas_alto", "long_short", "acumulacion", "flujo_tendencia"}
    old_days = ["2026-10-01", "2026-10-02", "2026-10-03"]
    ev = evaluate(v1_rows(old_days + [V1_DESDE, "2026-10-20"]), 7, 20, "prueba", mode="antes")
    assert ev.index_v1["n"] == 50 and ev.index_v1["auc"] == pytest.approx(1.0)
    assert ev.index_v1_new["n"] == 20 and ev.index_v1_new["auc"] == pytest.approx(1.0)  # solo los días nuevos
    assert ev.index_web["n"] == 20 and ev.index_web["auc"] == pytest.approx(1.0)
    # Con pocos días nuevos, el informe dice cuántas monedas faltan en vez de dar un AUC sin sentido.
    few = evaluate(v1_rows(old_days) + v1_rows([V1_DESDE], per_day=6), 7, 20, "prueba", mode="antes")
    assert few.index_v1_new == {"n": 6} and few.index_web == {"n": 6}
    none = evaluate(v1_rows(old_days), 7, 20, "prueba", mode="antes")
    assert none.index_v1_new == {"n": 0} and none.index_web == {"n": 0}
    md = report_markdown(Dataset(v1_rows(old_days), {}), [few, none], 36, "hoy")
    assert "Índice v1 en días nuevos" in md and "Todavía 6 monedas con resultado; hacen falta 15 para medirlo." in md
    assert "Todavía 0 monedas con resultado" in md


def test_book_signals_from_the_summary_saved_by_the_web():
    book = {"v": 1, "spread": 0.12, "compras_1": 300, "ventas_1": 100, "compras_2": 600, "ventas_2": 400,
            "ventas_5": 900, "muro_compra": 50, "muro_compra_dist": -1.2, "muro_venta": 150, "muro_venta_dist": 2.5}
    s = book_signals(book, volume=10_000)
    assert s["libro_1"] == pytest.approx(75.0) and s["libro_2"] == pytest.approx(60.0)
    assert s["libro_5"] is None  # el libro descargado no llegaba al −5 %: no se guardó
    assert s["libro_muros"] == pytest.approx(25.0) and s["libro_spread"] == 0.12
    assert s["libro_profundidad"] == pytest.approx(10.0)  # 1000 USDT a ±2 % sobre 10 000 de volumen
    assert book_signals(book)["libro_profundidad"] is None
    assert set(book_signals(None).values()) == {None}


def test_index_scores_rank_each_coin_within_its_day():
    rows = [{"fecha": "d", "senales": {"minimo_mas_alto": 5.0, "flujo_tendencia": -1.0, "long_short": 2.0, "acumulacion": 1.0}},
            {"fecha": "d", "senales": {"minimo_mas_alto": 1.0, "flujo_tendencia": 3.0, "long_short": None, "acumulacion": 0.0}},
            {"fecha": "d", "senales": {"minimo_mas_alto": 3.0, "flujo_tendencia": 1.0, "long_short": 1.0, "acumulacion": 0.0}},
            {"fecha": "e", "senales": {"minimo_mas_alto": 0.0, "flujo_tendencia": None, "long_short": None, "acumulacion": None}}]
    best, worst, middle, alone = index_scores(rows, INDEX_V1)
    assert best == pytest.approx(100.0)
    assert worst == pytest.approx((0 + 0 + 0.25) / 3 * 100)  # sin ratio long/short: media de las otras tres
    assert middle == pytest.approx((0.5 + 0.5 + 0 + 0.25) / 4 * 100)
    assert alone == pytest.approx(50.0)  # sola en su día


def test_load_entries_and_first_appearances(tmp_path):
    coin = {"mercado": "spot", "simbolo": "AAAUSDT", "base": "AAA", "quote": "USDT", "intervalo": "1d"}
    (tmp_path / "2025-10-01.json").write_text(json.dumps({"fecha": "2025-10-01", "monedas": [
        dict(coin, precio=1.0, hora="2025-10-01T19:00:00.000Z"),
        dict(coin, simbolo="BBBUSDT", precio=2.0, hora="2025-10-01T19:00:00.000Z", estrella=True, estrella_hora="x",
             giro={"v": 1, "indice": 72, "long_short": 2.1}, libro={"v": 1, "spread": 0.2})]}))
    (tmp_path / "2025-10-02.json").write_text(json.dumps({"fecha": "2025-10-02", "monedas": [
        dict(coin, precio=0.9, hora="2025-10-02T19:00:00.000Z"), {"roto": True}]}))
    entries = load_entries(tmp_path)
    assert [(e.simbolo, e.fecha) for e in entries] == [("AAAUSDT", "2025-10-01"), ("BBBUSDT", "2025-10-01"), ("AAAUSDT", "2025-10-02")]
    assert entries[1].estrella and entries[1].giro["indice"] == 72 and entries[0].giro is None
    assert entries[1].libro == {"v": 1, "spread": 0.2} and entries[0].libro is None
    assert [(e.simbolo, e.fecha) for e in first_appearances(entries)] == [("AAAUSDT", "2025-10-01"), ("BBBUSDT", "2025-10-01")]


class FakeClient:
    """Binance simulada: velas de make_symbol y datos de futuros para los símbolos de futuros."""

    fallbacks_used = {}

    def __init__(self, scans):
        self.scans = scans  # símbolo → (hora del escaneo, sube)
        self.calls = []

    def instruments(self, market):
        return [Instrument(FUTURES, "AAAUSDT", "AAA", "USDT")]

    def candles_between(self, market, symbol, interval, start, end):
        self.calls.append((market, symbol, interval))
        if symbol == "BTCUSDT":
            return series(start // HOUR_MS * HOUR_MS, HOUR_MS, (end - start) // HOUR_MS, lambda i: 60000.0)
        scan, up = self.scans[symbol]
        data, _ = make_symbol(scan, buy_share=0.65 if up else 0.4, after=lambda h: 1.5 if up and h > 20 else 1.0)
        return data.hourly if interval == "1h" else data.daily

    def futures_data(self, path, params):
        self.calls.append((FUTURES, params["symbol"], path))
        if "fundingRate" in path:
            return [{"fundingTime": params["startTime"] + i * 8 * HOUR_MS, "fundingRate": "-0.0001"} for i in range(40)]
        key = "sumOpenInterestValue" if "openInterest" in path else "longShortRatio"
        return [{"timestamp": params["startTime"] + i * HOUR_MS, key: str(1000 + i)} for i in range(0, 499)]


def test_build_dataset_and_report(tmp_path):
    scan = T0 + 200 * DAY_MS + 12 * HOUR_MS
    scans = {"AAAUSDT": (scan, True), "BBBUSDT": (scan, False)}
    client = FakeClient(scans)
    price = make_symbol(scan)[1]
    saved = {"v": 1, "indice": 64, "long_short": 1.9}  # lo que guardó la web al escanear
    book = {"v": 1, "spread": 0.1, "compras_2": 3000, "ventas_2": 1000}
    entries = [Entry("2025-10-19", SPOT, s, price, t, giro=saved, libro=book if s == "AAAUSDT" else None) for s, (t, _) in scans.items()]
    data = build_dataset(entries, client, now_ms=scan + 20 * DAY_MS, log=lambda m: None)
    assert [r["simbolo"] for r in data.rows] == ["AAAUSDT", "BBBUSDT"] and not data.errors
    a, b = data.rows
    assert a["senales"]["financiacion"] == pytest.approx(-0.01) and a["senales"]["oi_24h"] is not None
    assert b["senales"]["financiacion"] is None  # BBB no tiene futuros
    # Binance da el ratio long/short de AAA; el de BBB ya no está en Binance y se usa el que guardó la web.
    assert a["senales"]["long_short"] > 1000 and b["senales"]["long_short"] == pytest.approx(1.9)
    assert a["giro_web"] == b["giro_web"] == 64.0 and a["giro_v1"] > b["giro_v1"]
    assert a["senales"]["libro_2"] == pytest.approx(75.0) and a["senales"]["libro_profundidad"] > 0
    assert b["senales"]["libro_2"] is None
    assert a["senales"]["flujo_24h"] > b["senales"]["flujo_24h"] and a["resultado"]["max_7d"] > 20 > b["resultado"]["max_7d"]
    assert ("futures", "BBBUSDT", "/futures/data/openInterestHist") not in client.calls
    md = report_markdown(data, [], len(entries), "hoy")
    assert md.startswith("# Índice de giro") and "2 entradas de seguimiento" in md
    assert "**Libro de órdenes**: guardado en 1 entrada (1 monedas) desde el 2025-10-19." in md
    write_outputs(tmp_path, data, [], md)
    header = (tmp_path / "datos.csv").read_text().splitlines()[0]
    assert (tmp_path / "informe.md").exists() and "flujo_24h" in header and "giro_v1" in header
