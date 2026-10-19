"""Índice de giro: señales de cambio de tendencia y su prueba con el historial del seguimiento.

Fase 1. Para cada moneda guardada en el seguimiento se calculan, tal como estaban en el momento del
escaneo (sin mirar lo que pasó después), señales de cuatro tipos:

- precio y medias (MA7, MA25, MA99, RSI, mínimos más altos...);
- volumen (volumen relativo, volumen en velas verdes y rojas, OBV);
- flujo comprador: qué parte del volumen fueron compras a mercado (el comprador cruzó el libro de
  órdenes), dato que Binance da en cada vela y que sí tiene historial;
- futuros: interés abierto, financiación y ratio long/short (Binance guarda unos 30 días).

Después se mide qué pasó (máximo a 1, 3 y 7 días) y se comprueba qué señales separan las monedas
que subieron de las que no. El libro de órdenes no tiene historial: se empezará a guardar en los
escaneos (fase 2).

El índice v1 (INDEX_V1) junta las cuatro señales que se sostuvieron en esta prueba; la web lo calcula
igual en cada escaneo con velas 1D y lo guarda en el seguimiento. Cada semana se vuelve a medir, sobre
todo con los días que no se usaron para elegirlo (desde V1_DESDE).

    python -m momento_a.giro docs/seguimiento --salida informe-giro
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import random
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Sequence

from .binance import FUTURES, SPOT, BinanceClient, BinanceError, BinanceFatalError, Candle
from .indicators import sma

HOUR_MS = 3_600_000
DAY_MS = 86_400_000

# Señales del índice v0 y hacia dónde se espera que empujen un giro alcista (+1: cuanto más, mejor;
# -1: cuanto menos, mejor). Es la hipótesis de partida; la prueba dice cuáles se sostienen.
HYPOTHESIS = {
    "flujo_24h": +1,         # % del volumen de las últimas 24 h que fueron compras a mercado
    "flujo_tendencia": +1,   # ese % frente al de los últimos 7 días
    "cvd_7d": +1,            # delta de volumen (compras − ventas a mercado) de 7 días, sobre el volumen
    "acumulacion": +1,       # el delta sube mientras el precio de 7 días baja
    "rvol_24h": +1,          # volumen de 24 h frente a la media diaria de 20 días
    "vol_verde_roja": +1,    # volumen de velas verdes frente a rojas (10 días)
    "obv_7d": +1,            # balance de volumen de 7 días, sobre la media
    "pendiente_ma7": +1,     # la MA7 deja de caer
    "brecha_ma7_ma25": +1,   # la MA7 cerca de cruzar la MA25 (menos negativa)
    "minimo_mas_alto": +1,   # el mínimo de 5 días por encima del de los 15 anteriores
    "divergencia_rsi": +1,   # precio con mínimo más bajo y RSI con mínimo más alto
    "vela_pct": +1,          # fuerza de la vela del día hasta el escaneo
    "oi_24h": +1,            # interés abierto de 24 h (futuros)
    "financiacion": -1,      # financiación negativa: cortos pagando, posible cierre forzado
}

# Índice v1: las cuatro señales que separaron las monedas que subieron +20 % antes de caer −10 % en la
# prueba del 19 oct 2026 (sin confundirse con las que solo se movían mucho). La web usa las mismas
# señales, signos y cuenta (report_template.html, «índice de giro»).
INDEX_V1 = {
    "minimo_mas_alto": +1,   # el mínimo de 5 días por encima del de los 15 anteriores
    "long_short": +1,        # más cuentas en largo que en corto (futuros)
    "acumulacion": +1,       # compras a mercado de 7 días mientras el precio de 7 días baja
    "flujo_tendencia": -1,   # compras a mercado de 24 h por debajo de las de 7 días
}
# Primer día de seguimiento que aún no tenía resultado a 7 días cuando se eligieron esas señales: desde
# aquí, el índice v1 se mide con monedas que no influyeron en su elección.
V1_DESDE = "2026-10-12"

# Nombre legible de cada señal para el informe.
LABELS = {
    "flujo_24h": "Compras a mercado 24 h (%)",
    "flujo_7d": "Compras a mercado 7 días (%)",
    "flujo_tendencia": "Compras a mercado: 24 h frente a 7 días",
    "cvd_7d": "Delta de volumen 7 días",
    "acumulacion": "Acumulación (delta sube, precio baja)",
    "rvol_24h": "Volumen relativo 24 h",
    "tendencia_vol": "Volumen 5 días frente a 15",
    "vol_verde_roja": "Volumen velas verdes / rojas",
    "obv_7d": "OBV 7 días",
    "dist_ma7": "Precio frente a MA7 (%)",
    "brecha_ma7_ma25": "MA7 frente a MA25 (%)",
    "pendiente_ma7": "Pendiente de la MA7 (3 días, %)",
    "dist_ma99": "Precio frente a MA99 (%)",
    "caida_30d": "Caída desde el máximo de 30 días (%)",
    "rsi": "RSI 14",
    "divergencia_rsi": "Divergencia alcista del RSI",
    "minimo_mas_alto": "Mínimo más alto (%)",
    "rango_7d": "Rango de 7 días (%)",
    "vela_pct": "Vela del día hasta el escaneo (%)",
    "precio_24h": "Precio 24 h (%)",
    "precio_7d": "Precio 7 días (%)",
    "oi_24h": "Interés abierto 24 h (%)",
    "oi_7d": "Interés abierto 7 días (%)",
    "financiacion": "Financiación (%)",
    "financiacion_3d": "Financiación media 3 días (%)",
    "long_short": "Ratio long/short de cuentas",
    "long_short_3d": "Ratio long/short: cambio 3 días",
    "btc_7d": "BTC 7 días (%)",
    "fuerza_vs_btc": "Moneda frente a BTC, 7 días (%)",
}


# ---------- datos ----------
@dataclass(frozen=True)
class Entry:
    """Una moneda de un día de seguimiento: lo que se sabía al escanear."""

    fecha: str
    mercado: str
    simbolo: str
    precio: float
    hora_ms: int
    estrella: bool = False
    giro: Optional[dict] = field(default=None, compare=False)  # índice de giro que guardó la web al escanear


@dataclass
class SymbolData:
    hourly: list[Candle] = field(default_factory=list)
    daily: list[Candle] = field(default_factory=list)
    oi: list[tuple[int, float]] = field(default_factory=list)          # (ms, interés abierto en USDT)
    funding: list[tuple[int, float]] = field(default_factory=list)     # (ms, financiación en %)
    long_short: list[tuple[int, float]] = field(default_factory=list)  # (ms, ratio long/short de cuentas)


def parse_iso_ms(text: str) -> int:
    from datetime import datetime

    return int(datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp() * 1000)


def load_entries(folder: Path) -> list[Entry]:
    """Monedas de los archivos de seguimiento (AAAA-MM-DD.json), de la más antigua a la más reciente."""
    entries = []
    for path in sorted(Path(folder).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for c in data.get("monedas", []) if isinstance(data, dict) else []:
            try:
                entries.append(Entry(data.get("fecha") or path.stem, c["mercado"], c["simbolo"], float(c["precio"]),
                                     parse_iso_ms(c["hora"]), bool(c.get("estrella")),
                                     c["giro"] if isinstance(c.get("giro"), dict) else None))
            except (KeyError, TypeError, ValueError):
                continue
    return sorted(entries, key=lambda e: e.hora_ms)


def first_appearances(entries: Sequence[Entry]) -> list[Entry]:
    """Cada moneda una vez, la primera vez que salió en el escáner (muestra sin repeticiones)."""
    seen, out = set(), []
    for e in sorted(entries, key=lambda e: e.hora_ms):
        if (e.mercado, e.simbolo) not in seen:
            seen.add((e.mercado, e.simbolo))
            out.append(e)
    return out


# ---------- señales ----------
def pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    return None if a is None or not b else (a - b) / b * 100


def daily_view(data: SymbolData, t_ms: int, price: float) -> list[Candle]:
    """Velas diarias tal como se veían en t: las cerradas antes de ese día y la del día, hasta t.

    La vela del día se rehace con las velas de 1 hora ya cerradas en t y el precio del escaneo; así
    no se usa nada de lo que pasó después.
    """
    day0 = t_ms // DAY_MS * DAY_MS
    closed = [c for c in data.daily if c.open_time + DAY_MS <= day0]
    hours = [c for c in data.hourly if c.open_time >= day0 and c.close_time < t_ms]
    today = next((c for c in data.daily if c.open_time == day0), None)
    open_ = hours[0].open if hours else (today.open if today else price)
    partial = Candle(
        open_time=day0, open=open_, close=price, close_time=t_ms,
        high=max([open_, price] + [c.high for c in hours]), low=min([open_, price] + [c.low for c in hours]),
        quote_volume=sum(c.quote_volume for c in hours), volume=sum(c.volume for c in hours),
        taker_buy_volume=sum(c.taker_buy_volume for c in hours), trades=sum(c.trades for c in hours),
    )
    return closed + [partial]


def rsi(closes: Sequence[float], period: int = 14) -> list[Optional[float]]:
    """RSI de Wilder, alineado con ``closes``."""
    out: list[Optional[float]] = [None] * len(closes)
    if len(closes) <= period:
        return out
    gains = [max(closes[i] - closes[i - 1], 0) for i in range(1, len(closes))]
    losses = [max(closes[i - 1] - closes[i], 0) for i in range(1, len(closes))]
    avg_gain, avg_loss = sum(gains[:period]) / period, sum(losses[:period]) / period
    for i in range(period, len(closes)):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = 100.0 if avg_loss == 0 else 100 - 100 / (1 + avg_gain / avg_loss)
    return out


def _series_before(series: Sequence[tuple[int, float]], t_ms: int) -> list[tuple[int, float]]:
    return [p for p in series if p[0] <= t_ms]


def _change_since(series: Sequence[tuple[int, float]], t_ms: int, ago_ms: int, max_gap_ms: int = 2 * HOUR_MS) -> Optional[float]:
    now = _series_before(series, t_ms)
    then = _series_before(series, t_ms - ago_ms)
    if not now or not then or t_ms - now[-1][0] > max_gap_ms or t_ms - ago_ms - then[-1][0] > max_gap_ms:
        return None
    return pct(now[-1][1], then[-1][1])


def features(entry: Entry, data: SymbolData, btc_hourly: Sequence[Candle] = ()) -> dict[str, Optional[float]]:
    """Señales de una moneda en el momento de su escaneo. None: sin datos suficientes."""
    t, price = entry.hora_ms, entry.precio
    f: dict[str, Optional[float]] = {}
    days = daily_view(data, t, price)
    closes = [c.close for c in days]
    n = len(closes)

    ma7, ma25, ma99 = sma(closes, 7), sma(closes, 25), sma(closes, 99)
    f["dist_ma7"] = pct(price, ma7[-1]) if n >= 7 else None
    f["brecha_ma7_ma25"] = pct(ma7[-1], ma25[-1]) if n >= 25 else None
    f["pendiente_ma7"] = pct(ma7[-1], ma7[-4]) if n >= 10 else None
    f["dist_ma99"] = pct(price, ma99[-1]) if n >= 99 else None
    f["caida_30d"] = pct(price, max(c.high for c in days[-30:])) if n >= 30 else None
    f["vela_pct"] = pct(price, days[-1].open)
    r = rsi(closes)
    f["rsi"] = r[-1]
    if n >= 30 and all(v is not None for v in r[-21:]):
        recent, before = slice(-7, None), slice(-21, -7)
        lower_low = min(c.low for c in days[recent]) < min(c.low for c in days[before])
        higher_rsi = min(r[recent]) > min(r[before])
        f["divergencia_rsi"] = 1.0 if lower_low and higher_rsi else 0.0
    else:
        f["divergencia_rsi"] = None
    if n >= 21:
        low5, low15 = min(c.low for c in days[-5:]), min(c.low for c in days[-20:-5])
        f["minimo_mas_alto"] = pct(low5, low15)
        f["rango_7d"] = pct(max(c.high for c in days[-7:]), min(c.low for c in days[-7:]))
    else:
        f["minimo_mas_alto"] = f["rango_7d"] = None

    # Volumen (en USDT para poder comparar monedas). Días cerrados: todos menos la vela del día.
    closed = days[:-1]
    hours = [c for c in data.hourly if c.close_time < t]
    last24 = [c for c in hours if c.open_time >= t - DAY_MS]
    last7d = [c for c in hours if c.open_time >= t - 7 * DAY_MS]
    mean20 = statistics.fmean(c.quote_volume for c in closed[-20:]) if len(closed) >= 20 else None
    f["rvol_24h"] = sum(c.quote_volume for c in last24) / mean20 if mean20 and len(last24) >= 20 else None
    if len(closed) >= 20:
        v5 = statistics.fmean(c.quote_volume for c in closed[-5:])
        v15 = statistics.fmean(c.quote_volume for c in closed[-20:-5])
        f["tendencia_vol"] = v5 / v15 if v15 else None
        up = sum(c.quote_volume for c in closed[-10:] if c.close >= c.open)
        down = sum(c.quote_volume for c in closed[-10:] if c.close < c.open)
        f["vol_verde_roja"] = min(up / down, 10.0) if down else (10.0 if up else None)
        obv = sum(math.copysign(c.quote_volume, c.close - p.close) for p, c in zip(closed[-8:-1], closed[-7:]) if c.close != p.close)
        f["obv_7d"] = obv / mean20 if mean20 else None
    else:
        f["tendencia_vol"] = f["vol_verde_roja"] = f["obv_7d"] = None

    # Flujo comprador: compras a mercado sobre el volumen (en la moneda base).
    def flow(cs: Sequence[Candle]) -> Optional[float]:
        vol = sum(c.volume for c in cs)
        return sum(c.taker_buy_volume for c in cs) / vol * 100 if vol else None

    f["flujo_24h"] = flow(last24) if len(last24) >= 20 else None
    f["flujo_7d"] = flow(last7d) if len(last7d) >= 7 * 20 else None
    f["flujo_tendencia"] = None if f["flujo_24h"] is None or f["flujo_7d"] is None else f["flujo_24h"] - f["flujo_7d"]
    vol7 = sum(c.volume for c in last7d)
    f["cvd_7d"] = sum(2 * c.taker_buy_volume - c.volume for c in last7d) / vol7 if vol7 and len(last7d) >= 7 * 20 else None
    f["precio_24h"] = pct(price, last24[0].open) if len(last24) >= 20 else None
    f["precio_7d"] = pct(price, last7d[0].open) if len(last7d) >= 7 * 20 else None
    f["acumulacion"] = None if f["cvd_7d"] is None or f["precio_7d"] is None else float(f["cvd_7d"] > 0 and f["precio_7d"] < 0)

    # Futuros
    f["oi_24h"] = _change_since(data.oi, t, DAY_MS)
    f["oi_7d"] = _change_since(data.oi, t, 7 * DAY_MS)
    funding = _series_before(data.funding, t)
    f["financiacion"] = funding[-1][1] if funding and t - funding[-1][0] <= DAY_MS else None
    f["financiacion_3d"] = statistics.fmean(v for _, v in funding[-9:]) if len(funding) >= 9 and t - funding[-1][0] <= DAY_MS else None
    ls = _series_before(data.long_short, t)
    f["long_short"] = ls[-1][1] if ls and t - ls[-1][0] <= 2 * HOUR_MS else None
    f["long_short_3d"] = _change_since(data.long_short, t, 3 * DAY_MS)

    # Contexto: BTC
    btc = [c for c in btc_hourly if t - 7 * DAY_MS <= c.open_time and c.close_time < t]
    f["btc_7d"] = pct(btc[-1].close, btc[0].open) if len(btc) >= 7 * 20 else None
    f["fuerza_vs_btc"] = None if f["btc_7d"] is None or f["precio_7d"] is None else f["precio_7d"] - f["btc_7d"]
    return f


def outcomes(entry: Entry, data: SymbolData, now_ms: int, horizons: Iterable[int] = (1, 3, 7),
             target: float = 20.0, stop: float = 10.0) -> dict[str, Optional[float]]:
    """Qué pasó después del escaneo: máximo y mínimo frente al precio de seguimiento en cada plazo, y si
    subió +``target`` % antes de caer −``stop`` % (si en la misma hora pasan las dos cosas, cuenta la caída)."""
    start = entry.hora_ms // HOUR_MS * HOUR_MS
    after = [c for c in data.hourly if c.open_time >= start]
    last = after[-1].close_time if after else entry.hora_ms
    o: dict[str, Optional[float]] = {}
    for d in horizons:
        window = [c for c in after if c.open_time < entry.hora_ms + d * DAY_MS]
        complete = entry.hora_ms + d * DAY_MS <= min(now_ms, last + 1)
        o[f"max_{d}d"] = pct(max(c.high for c in window), entry.precio) if window and complete else None
        o[f"min_{d}d"] = pct(min(c.low for c in window), entry.precio) if window and complete else None
        o[f"cierre_{d}d"] = pct(window[-1].close, entry.precio) if window and complete else None
        won = None
        if window and complete:
            won = 0.0
            for c in window:
                if pct(c.low, entry.precio) <= -stop:
                    break
                if pct(c.high, entry.precio) >= target:
                    won = 1.0
                    break
        o[f"gana_antes_{d}d"] = won
    return o


# ---------- estadística (sin dependencias) ----------
def ranks(values: Sequence[float]) -> list[float]:
    """Rangos (1..n) con empates promediados."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    out = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            out[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return out


def auc(values: Sequence[float], labels: Sequence[bool]) -> Optional[float]:
    """Probabilidad de que una moneda que subió tenga más valor que una que no (0,5 = no separa)."""
    pos = sum(1 for y in labels if y)
    neg = len(labels) - pos
    if not pos or not neg:
        return None
    r = ranks(values)
    return (sum(rk for rk, y in zip(r, labels) if y) - pos * (pos + 1) / 2) / (pos * neg)


def spearman(x: Sequence[float], y: Sequence[float]) -> Optional[float]:
    if len(x) < 3:
        return None
    rx, ry = ranks(x), ranks(y)
    mx, my = statistics.fmean(rx), statistics.fmean(ry)
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    den = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return num / den if den else None


def bootstrap_auc(values: Sequence[float], labels: Sequence[bool], rounds: int = 400, seed: int = 7) -> Optional[tuple[float, float]]:
    """Intervalo del 90 % del AUC remuestreando las monedas."""
    rng = random.Random(seed)
    n, got = len(values), []
    for _ in range(rounds):
        idx = [rng.randrange(n) for _ in range(n)]
        a = auc([values[i] for i in idx], [labels[i] for i in idx])
        if a is not None:
            got.append(a)
    if len(got) < rounds // 2:
        return None
    got.sort()
    return got[int(0.05 * len(got))], got[int(0.95 * len(got)) - 1]


def day_percentiles(rows: Sequence[dict], name: str) -> list[Optional[float]]:
    """Posición (0..1) de cada moneda en su día de seguimiento para una señal: compara con las de ese escaneo."""
    out: list[Optional[float]] = [None] * len(rows)
    by_day: dict[str, list[int]] = {}
    for i, row in enumerate(rows):
        if row["senales"].get(name) is not None:
            by_day.setdefault(row["fecha"], []).append(i)
    for idx in by_day.values():
        if len(idx) == 1:
            out[idx[0]] = 0.5
            continue
        r = ranks([rows[i]["senales"][name] for i in idx])
        for i, rk in zip(idx, r):
            out[i] = (rk - 1) / (len(idx) - 1)
    return out


def index_scores(rows: Sequence[dict], weights: dict[str, float]) -> list[Optional[float]]:
    """Índice 0-100: media ponderada de la posición de cada moneda en su día en cada señal (con su signo)."""
    per = {name: day_percentiles(rows, name) for name in weights}
    out = []
    for i in range(len(rows)):
        num = den = 0.0
        for name, w in weights.items():
            p = per[name][i]
            if p is not None and w:
                num += abs(w) * (p if w > 0 else 1 - p)
                den += abs(w)
        out.append(num / den * 100 if den else None)
    return out


def quintiles(scores: Sequence[float], labels: Sequence[bool], gains: Sequence[float]) -> list[dict]:
    order = sorted(range(len(scores)), key=lambda i: scores[i])
    out = []
    for q in range(5):
        idx = order[q * len(order) // 5:(q + 1) * len(order) // 5]
        if idx:
            out.append({"grupo": q + 1, "n": len(idx), "acierto": sum(labels[i] for i in idx) / len(idx) * 100,
                        "max_mediano": statistics.median(gains[i] for i in idx)})
    return out


@dataclass
class Evaluation:
    horizon: int
    threshold: float
    sample: str
    n: int
    winners: int
    signals: list[dict]
    index_v0: Optional[dict]
    index_cv: Optional[dict]
    mode: str = "max"
    index_v1: Optional[dict] = None
    index_v1_new: Optional[dict] = None  # solo días desde V1_DESDE
    index_web: Optional[dict] = None     # el que guardó la web al escanear


def evaluate(rows: Sequence[dict], horizon: int, threshold: float, sample: str, mode: str = "max") -> Optional[Evaluation]:
    """Qué señales separan las monedas que subieron en ``horizon`` días.

    mode "max": el máximo llegó a +``threshold`` %. mode "antes": subió +``threshold`` % antes de caer
    −``threshold``/2 %, que separa la dirección de la simple volatilidad. Para cada señal se calcula también
    si separa las que cayeron −``threshold``/2 %: si lo hace igual de bien, mide volatilidad, no giro.
    """
    key, low_key, won_key = f"max_{horizon}d", f"min_{horizon}d", f"gana_antes_{horizon}d"
    rows = [r for r in rows if r["resultado"].get(key) is not None and r["resultado"].get(low_key) is not None]
    if len(rows) < 10:
        return None
    if mode == "antes":
        labels = [r["resultado"].get(won_key) == 1.0 for r in rows]
    else:
        labels = [r["resultado"][key] >= threshold for r in rows]
    drops = [r["resultado"][low_key] <= -threshold / 2 for r in rows]
    gains = [r["resultado"][key] for r in rows]
    signals = []
    for name in LABELS:
        pcts = day_percentiles(rows, name)
        idx = [i for i, p in enumerate(pcts) if p is not None]
        if len(idx) < 15:
            continue
        lab = [labels[i] for i in idx]
        a = auc([pcts[i] for i in idx], lab)
        if a is None:
            continue
        raw = [rows[i]["senales"][name] for i in idx]
        win = [v for v, y in zip(raw, lab) if y]
        lose = [v for v, y in zip(raw, lab) if not y]
        signals.append({
            "senal": name, "nombre": LABELS[name], "n": len(idx), "auc": a, "ic90": bootstrap_auc([pcts[i] for i in idx], lab),
            "auc_caida": auc([pcts[i] for i in idx], [drops[i] for i in idx]),
            "spearman": spearman(raw, [gains[i] for i in idx]),
            "mediana_suben": statistics.median(win) if win else None, "mediana_resto": statistics.median(lose) if lose else None,
            "hipotesis": HYPOTHESIS.get(name),
        })
    signals.sort(key=lambda s: -abs(s["auc"] - 0.5))

    def summary(scores: Sequence[Optional[float]]) -> Optional[dict]:
        idx = [i for i, s in enumerate(scores) if s is not None]
        if len(idx) < 15:
            return {"n": len(idx)} if idx else None  # todavía pocas monedas para medirlo
        sc, lab, gn = [scores[i] for i in idx], [labels[i] for i in idx], [gains[i] for i in idx]
        return {"n": len(idx), "auc": auc(sc, lab), "ic90": bootstrap_auc(sc, lab), "quintiles": quintiles(sc, lab, gn)}

    index_v0 = summary(index_scores(rows, HYPOTHESIS))
    # El v1 de cada moneda, frente a todas las de su día (como en la web, frente a todas las del escaneo).
    v1 = [r["giro_v1"] for r in rows] if all("giro_v1" in r for r in rows) else index_scores(rows, INDEX_V1)
    index_v1 = summary(v1)
    # Estos dos se miden cada semana: el informe dice cuántas monedas llevan, aunque aún no haya ninguna.
    index_v1_new = summary([s if r["fecha"] >= V1_DESDE else None for s, r in zip(v1, rows)]) or {"n": 0}
    index_web = summary([r.get("giro_web") for r in rows]) or {"n": 0}
    # Índice aprendido, probado día a día con los otros días: elige señales y signos sin ver el día que puntúa.
    days = sorted({r["fecha"] for r in rows})
    cv_scores: list[Optional[float]] = [None] * len(rows)
    picked: dict[str, int] = {}
    for day in days:
        train = [i for i, r in enumerate(rows) if r["fecha"] != day]
        test = [i for i, r in enumerate(rows) if r["fecha"] == day]
        train_rows = [rows[i] for i in train]
        weights = {}
        for name in LABELS:
            pcts = day_percentiles(train_rows, name)
            idx = [k for k, p in enumerate(pcts) if p is not None]
            if len(idx) < 30:
                continue
            a = auc([pcts[k] for k in idx], [labels[train[k]] for k in idx])
            if a is not None and abs(a - 0.5) >= 0.05:
                weights[name] = a - 0.5
                picked[name] = picked.get(name, 0) + 1
        if not weights:
            continue
        both = [rows[i] for i in train + test]
        sc = index_scores(both, weights)
        for k, i in enumerate(test):
            cv_scores[i] = sc[len(train) + k]
    index_cv = summary(cv_scores)
    if index_cv is not None:
        index_cv["senales"] = sorted(picked.items(), key=lambda kv: -kv[1])
        index_cv["dias"] = len(days)
    return Evaluation(horizon, threshold, sample, len(rows), sum(labels), signals, index_v0, index_cv, mode,
                      index_v1, index_v1_new, index_web)


# ---------- descarga ----------
def fetch_symbol(client: BinanceClient, market: str, symbol: str, start_ms: int, end_ms: int, futures_ok: bool,
                 now_ms: int) -> SymbolData:
    data = SymbolData()
    data.hourly = client.candles_between(market, symbol, "1h", start_ms - 30 * DAY_MS, end_ms)
    data.daily = client.candles_between(market, symbol, "1d", start_ms - 130 * DAY_MS, end_ms)
    if futures_ok:
        # Binance solo guarda unos 30 días de interés abierto y ratio long/short.
        since = max(start_ms - 8 * DAY_MS, now_ms - 29 * DAY_MS)
        data.oi = _paged_futures(client, "/futures/data/openInterestHist", symbol, since, end_ms,
                                 lambda p: (int(p["timestamp"]), float(p["sumOpenInterestValue"])))
        data.long_short = _paged_futures(client, "/futures/data/globalLongShortAccountRatio", symbol, since, end_ms,
                                         lambda p: (int(p["timestamp"]), float(p["longShortRatio"])))
        rows = client.futures_data("/fapi/v1/fundingRate", {"symbol": symbol, "startTime": start_ms - 10 * DAY_MS,
                                                             "endTime": end_ms, "limit": 1000})
        data.funding = sorted((int(p["fundingTime"]), float(p["fundingRate"]) * 100) for p in rows)
    return data


def _paged_futures(client: BinanceClient, path: str, symbol: str, start_ms: int, end_ms: int,
                   parse: Callable[[dict], tuple[int, float]]) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    start = start_ms
    while start < end_ms:
        page_end = min(end_ms, start + 499 * HOUR_MS)
        page = client.futures_data(path, {"symbol": symbol, "period": "1h", "startTime": start, "endTime": page_end, "limit": 500})
        out.extend(parse(p) for p in page)
        start = page_end + 1
    return sorted(set(out))


@dataclass
class Dataset:
    rows: list[dict]
    errors: dict[str, str]
    futures_note: str = ""


def build_dataset(entries: Sequence[Entry], client: BinanceClient, now_ms: int, horizon: int = 7, workers: int = 4,
                  log: Callable[[str], None] = print, threshold: float = 20.0) -> Dataset:
    """Descarga los datos de cada moneda y calcula sus señales y resultados."""
    by_symbol: dict[tuple[str, str], list[Entry]] = {}
    for e in entries:
        by_symbol.setdefault((e.mercado, e.simbolo), []).append(e)
    try:
        futures_symbols = {i.symbol for i in client.instruments(FUTURES)}
    except BinanceError as exc:
        futures_symbols = set()
        log(f"Aviso: sin lista de futuros ({exc}).")
    btc_start = min(e.hora_ms for e in entries) - 8 * DAY_MS
    btc = client.candles_between(SPOT, "BTCUSDT", "1h", btc_start, now_ms)

    errors: dict[str, str] = {}
    futures_failed: list[str] = []

    def one(key: tuple[str, str]) -> tuple[tuple[str, str], Optional[SymbolData]]:
        market, symbol = key
        group = by_symbol[key]
        start = min(e.hora_ms for e in group)
        end = min(now_ms, max(e.hora_ms for e in group) + (horizon + 1) * DAY_MS)
        # Los datos de futuros sirven también para la moneda de spot si tiene contrato perpetuo. Si fallan
        # una y otra vez (p. ej. no responden desde donde se ejecuta), se deja de pedirlos.
        fut = symbol in futures_symbols and len(futures_failed) < 8
        try:
            return key, fetch_symbol(client, market, symbol, start, end, fut, now_ms)
        except BinanceFatalError:
            raise
        except BinanceError as exc:
            if fut:  # quizá solo fallan los datos de futuros: se reintenta sin ellos
                try:
                    data = fetch_symbol(client, market, symbol, start, end, False, now_ms)
                    futures_failed.append(f"{symbol}: {exc}")
                    return key, data
                except BinanceError as exc2:
                    exc = exc2
            errors[f"{market}:{symbol}"] = str(exc)
            return key, None

    rows = []
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for key, data in pool.map(one, list(by_symbol)):
            done += 1
            if done % 25 == 0:
                log(f"  {done}/{len(by_symbol)} monedas descargadas")
            if data is None or not data.hourly:
                errors.setdefault(f"{key[0]}:{key[1]}", "sin velas")
                continue
            for e in by_symbol[key]:
                signals = features(e, data, btc)
                saved = e.giro or {}
                # Binance solo guarda 30 días de ratio long/short: el que guardó la web al escanear lo conserva.
                if signals["long_short"] is None and isinstance(saved.get("long_short"), (int, float)):
                    signals["long_short"] = float(saved["long_short"])
                web = saved.get("indice")
                rows.append({"fecha": e.fecha, "mercado": e.mercado, "simbolo": e.simbolo, "hora_ms": e.hora_ms,
                             "precio": e.precio, "estrella": e.estrella, "senales": signals,
                             "giro_web": float(web) if isinstance(web, (int, float)) else None,
                             "resultado": outcomes(e, data, now_ms, target=threshold, stop=threshold / 2)})
    rows.sort(key=lambda r: r["hora_ms"])
    for r, score in zip(rows, index_scores(rows, INDEX_V1)):
        r["giro_v1"] = score
    note = ""
    if futures_failed:
        note = f"Datos de futuros no disponibles para {len(futures_failed)} monedas (p. ej. {futures_failed[0]})."
    return Dataset(rows, errors, note)


# ---------- informe ----------
def strength(a: float) -> str:
    d = abs(a - 0.5)
    return "fuerte" if d >= 0.12 else "moderada" if d >= 0.07 else "débil" if d >= 0.03 else "ninguna"


def fmt(v: Optional[float], digits: int = 2) -> str:
    return "—" if v is None else f"{v:.{digits}f}"


def report_markdown(dataset: Dataset, evaluations: Sequence[Evaluation], entries_count: int, generated: str) -> str:
    rows = dataset.rows
    lines = [
        "# Índice de giro — prueba con el historial del seguimiento",
        "",
        f"Generado el {generated}. {entries_count} entradas de seguimiento, {len({(r['mercado'], r['simbolo']) for r in rows})} monedas "
        f"con datos ({len(rows)} entradas). Las señales se calculan tal como estaban al escanear; el resultado es el máximo "
        "(sobre el precio de seguimiento) en los días siguientes.",
        "",
        "**AUC**: probabilidad de que una moneda que subió tenga la señal más alta (dentro de su día de escaneo) que una que "
        "no subió. 0,50 = no separa; por encima, la señal alta acompaña a las que suben; por debajo, al revés. "
        "El intervalo es del 90 %: si incluye 0,50, la señal puede ser casualidad. **AUC caída**: lo mismo para las "
        "que cayeron; si una señal separa igual de bien las que suben y las que caen, mide volatilidad (movimiento), "
        "no un giro al alza.",
        "",
    ]
    for ev in evaluations:
        base = ev.winners / ev.n * 100
        title = (f"Sube +{ev.threshold:g} % antes de caer −{ev.threshold / 2:g} % en {ev.horizon} días" if ev.mode == "antes"
                 else f"Máximo a {ev.horizon} días ≥ +{ev.threshold:g} %")
        lines += [f"## {title} — {ev.sample}", "", f"{ev.n} monedas; {ev.winners} lo consiguieron ({base:.0f} %).", ""]
        lines += ["| Señal | n | AUC (IC 90 %) | AUC caída | Relación | Mediana: suben / resto | Hipótesis |",
                  "|---|---:|---|---:|---|---|---|"]
        for s in ev.signals:
            ic = f"{s['ic90'][0]:.2f}–{s['ic90'][1]:.2f}" if s["ic90"] else "—"
            sure = s["ic90"] and (s["ic90"][0] > 0.5 or s["ic90"][1] < 0.5)
            direction = "alta→sube" if s["auc"] > 0.5 else "baja→sube"
            hyp = {1: "alta", -1: "baja"}.get(s["hipotesis"] or 0, "—")
            ok = "" if not s["hipotesis"] else (" ✓" if (s["auc"] > 0.5) == (s["hipotesis"] > 0) else " ✗")
            lines.append(f"| {s['nombre']} | {s['n']} | **{s['auc']:.2f}** ({ic}){' •' if sure else ''} | {fmt(s['auc_caida'])} | "
                         f"{strength(s['auc'])}, {direction} | "
                         f"{fmt(s['mediana_suben'])} / {fmt(s['mediana_resto'])} | {hyp}{ok} |")
        lines += ["", "• = el intervalo no incluye 0,50. ✓/✗ = coincide o no con la hipótesis de partida.", ""]
        for title, idx in (("Índice v1 en días nuevos (desde el " + V1_DESDE + ", que no se usaron para elegirlo)", ev.index_v1_new),
                           ("Índice v1 guardado por la web al escanear", ev.index_web),
                           ("Índice v1 en todos los días (salen favorecidos: con ellos se eligieron sus señales)", ev.index_v1),
                           ("Índice v0 (hipótesis de partida, sin ajustar)", ev.index_v0),
                           ("Índice ajustado (probado cada día con los demás días)", ev.index_cv)):
            if not idx:
                continue
            if "auc" not in idx:
                lines += [f"### {title}", "", f"Todavía {idx['n']} {'moneda' if idx['n'] == 1 else 'monedas'} con resultado; "
                          "hacen falta 15 para medirlo.", ""]
                continue
            ic = f"{idx['ic90'][0]:.2f}–{idx['ic90'][1]:.2f}" if idx["ic90"] else "—"
            lines += [f"### {title}", "", f"AUC **{idx['auc']:.2f}** ({ic}), {idx['n']} monedas.", "",
                      "| Grupo (1 = índice más bajo) | Monedas | Llegaron | Máximo mediano |", "|---|---:|---:|---:|"]
            lines += [f"| {q['grupo']} | {q['n']} | {q['acierto']:.0f} % | {q['max_mediano']:+.1f} % |" for q in idx["quintiles"]]
            if idx.get("senales"):
                lines += ["", "Señales elegidas (en cuántos de los " + str(idx["dias"]) + " días): " +
                          ", ".join(f"{LABELS[k]} ({v})" for k, v in idx["senales"][:10]) + "."]
            lines.append("")
    if dataset.futures_note:
        lines += [f"_{dataset.futures_note}_", ""]
    if dataset.errors:
        lines += [f"_Sin datos de {len(dataset.errors)} monedas (retiradas o sin velas): "
                  + ", ".join(sorted(dataset.errors)[:12]) + ("…" if len(dataset.errors) > 12 else "") + "._", ""]
    lines += ["Muestra pequeña y de un solo periodo de mercado: tómalo como orientación, no como prueba definitiva.", ""]
    return "\n".join(lines)


def write_outputs(folder: Path, dataset: Dataset, evaluations: Sequence[Evaluation], markdown: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "informe.md").write_text(markdown, encoding="utf-8")
    names = list(LABELS)
    outs = sorted({k for r in dataset.rows for k in r["resultado"]})
    with (folder / "datos.csv").open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["fecha", "mercado", "simbolo", "precio", "estrella", "giro_web", "giro_v1", *names, *outs])
        for r in dataset.rows:
            score = r.get("giro_v1")
            w.writerow([r["fecha"], r["mercado"], r["simbolo"], r["precio"], int(r["estrella"]), r.get("giro_web"),
                        None if score is None else round(score, 1),
                        *[r["senales"].get(n) for n in names], *[r["resultado"].get(k) for k in outs]])
    (folder / "resumen.json").write_text(json.dumps([ev.__dict__ for ev in evaluations], ensure_ascii=False, indent=1),
                                         encoding="utf-8")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m momento_a.giro", description=__doc__.splitlines()[0])
    parser.add_argument("seguimiento", nargs="?", default="docs/seguimiento", help="carpeta de seguimiento")
    parser.add_argument("--salida", default="informe-giro", help="carpeta del informe (por defecto: informe-giro)")
    parser.add_argument("--horizonte", type=int, default=7, help="días para medir el máximo (por defecto: 7)")
    parser.add_argument("--umbral", type=float, default=20.0, help="subida del máximo que cuenta como acierto (por defecto: 20)")
    parser.add_argument("--hilos", type=int, default=4)
    args = parser.parse_args(argv)

    from datetime import datetime, timezone

    entries = load_entries(Path(args.seguimiento))
    if not entries:
        print(f"No hay entradas de seguimiento en {args.seguimiento}.")
        return 1
    now_ms = int(time.time() * 1000)
    client = BinanceClient()
    print(f"{len(entries)} entradas, {len({(e.mercado, e.simbolo) for e in entries})} monedas. Descargando…")
    try:
        dataset = build_dataset(entries, client, now_ms, args.horizonte, args.hilos, threshold=args.umbral)
    except BinanceFatalError as exc:
        print(f"Error: {exc}")
        return 2
    first = {(e.mercado, e.simbolo, e.hora_ms) for e in first_appearances(entries)}
    first_rows = [r for r in dataset.rows if (r["mercado"], r["simbolo"], r["hora_ms"]) in first]
    first_label = "primera vez que salió cada moneda"
    evaluations = [ev for ev in (
        evaluate(first_rows, args.horizonte, args.umbral, first_label, mode="antes"),
        evaluate(first_rows, args.horizonte, args.umbral, first_label),
        evaluate(dataset.rows, args.horizonte, args.umbral, "todas las entradas (con repeticiones)", mode="antes"),
        evaluate(first_rows, 3, args.umbral / 2, first_label),
    ) if ev is not None]
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    markdown = report_markdown(dataset, evaluations, len(entries), generated)
    write_outputs(Path(args.salida), dataset, evaluations, markdown)
    if client.fallbacks_used:
        print("Bloqueadas desde aquí, se usaron otras: " + ", ".join(
            f"{blocked} → {client.base_urls[m]}" for m, blocked in client.fallbacks_used.items()))
    print(markdown)
    return 0


if __name__ == "__main__":
    sys.exit(main())
