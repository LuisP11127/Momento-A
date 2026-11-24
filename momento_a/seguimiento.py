"""Guardar un escaneo en el seguimiento, como hace la web al escanear.

Cada moneda que cumple los filtros se añade a ``docs/seguimiento/AAAA-MM-DD.json`` con su precio y la hora del
escaneo, su índice de giro v1 (con velas 1D) y una foto de su libro de órdenes. El día es el de la vela diaria
en la hora de Lima, como en la web: la vela abre a las 00:00 UTC, que son las 19:00 del día anterior en Lima.
Si la moneda ya estaba guardada ese día, se queda la primera (su primer precio del día).

Lo usa el workflow «Escaneo diario»: ``python -m momento_a ... --seguimiento docs/seguimiento``.
"""
from __future__ import annotations

import json
import math
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable, Optional, Sequence

from .binance import FUTURES, SPOT, BinanceClient, BinanceError, BinanceFatalError, Candle, Instrument
from .giro import DAY_MS, INDEX_V1, Entry, SymbolData, features, index_scores

LIMA = timezone(timedelta(hours=-5))  # Perú no cambia de hora en verano
GIRO_HOURS = 200  # velas de 1 hora: los 7 días de las señales de flujo, con margen
DEPTH_LIMITS = {SPOT: 1000, FUTURES: 500}  # niveles del libro que se piden (los mismos que la web)
BOOK_BANDS = (1, 2, 5)


def tracking_day(t_ms: int) -> str:
    """Día de seguimiento de un escaneo: la fecha en Lima en que abrió su vela diaria."""
    candle_open = datetime.fromtimestamp(t_ms // DAY_MS * DAY_MS / 1000, timezone.utc)
    return candle_open.astimezone(LIMA).date().isoformat()


def iso_ms(t_ms: int) -> str:
    """Hora en el formato que guarda la web (ISO en UTC, con milisegundos)."""
    return datetime.fromtimestamp(t_ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def round_half_up(value: float) -> int:
    """Como Math.round de la web (Python redondea 0,5 al par)."""
    return int(math.floor(value + 0.5))


# ---------- libro de órdenes ----------
def book_summary(book: dict, limit: int) -> Optional[dict]:
    """Resumen del libro de órdenes, el mismo que guarda la web (bookSummary en report_template.html).

    USDT en compras y ventas a ±1, 2 y 5 % del precio medio (una banda solo si el libro descargado llega a su
    borde, o es el libro entero), el spread y la orden más grande de cada lado a menos del 5 %.
    """
    def side(levels) -> list[tuple[float, float]]:
        out = []
        for level in levels if isinstance(levels, list) else []:
            try:
                p, q = float(level[0]), float(level[1])
            except (TypeError, ValueError, IndexError):
                continue
            if p > 0 and q >= 0:
                out.append((p, q))
        return out

    bids, asks = side((book or {}).get("bids")), side((book or {}).get("asks"))
    if not bids or not asks:
        return None
    mid = (bids[0][0] + asks[0][0]) / 2
    out: dict = {"v": 1, "spread": round((asks[0][0] - bids[0][0]) / mid * 100, 4)}

    def inside(levels, edge, buy):
        return [(p, q) for p, q in levels if (p >= edge if buy else p <= edge)]

    def reaches(levels, edge, buy):
        return len(levels) < limit or (levels[-1][0] <= edge if buy else levels[-1][0] >= edge)

    for pct in BOOK_BANDS:
        for name, levels, buy in (("compras_", bids, True), ("ventas_", asks, False)):
            edge = mid * (1 + (-pct if buy else pct) / 100)
            if reaches(levels, edge, buy):
                out[f"{name}{pct}"] = round_half_up(sum(p * q for p, q in inside(levels, edge, buy)))
    for name, levels, buy in (("muro_compra", bids, True), ("muro_venta", asks, False)):
        near = inside(levels, mid * (1 + (-5 if buy else 5) / 100), buy)
        if near:
            p, q = max(near, key=lambda level: level[0] * level[1])
            out[name] = round_half_up(p * q)
            out[f"{name}_dist"] = round((p - mid) / mid * 100, 3)
    return out


def fetch_book(client: BinanceClient, market: str, symbol: str) -> Optional[dict]:
    try:
        return book_summary(client.depth(market, symbol, DEPTH_LIMITS[market]), DEPTH_LIMITS[market])
    except BinanceFatalError:
        raise
    except BinanceError:
        return None


# ---------- índice de giro ----------
def fetch_long_short(client: BinanceClient, symbol: str) -> list[tuple[int, float]]:
    rows = client.futures_data("/futures/data/globalLongShortAccountRatio", {"symbol": symbol, "period": "1h", "limit": 3})
    return sorted((int(r["timestamp"]), float(r["longShortRatio"])) for r in rows)


def giro_signals(client: BinanceClient, instrument: Instrument, price: float, daily: Sequence[Candle], t_ms: int,
                 with_long_short: bool) -> dict:
    """Las cuatro señales del índice v1 de una moneda, con las mismas cuentas que la prueba (giro.features)."""
    market, symbol = instrument.market, instrument.symbol
    try:
        hourly = client.candles(instrument, "1h", GIRO_HOURS)
    except BinanceFatalError:
        raise
    except BinanceError:
        hourly = []
    long_short: list[tuple[int, float]] = []
    if with_long_short:
        try:
            long_short = fetch_long_short(client, symbol)
        except BinanceFatalError:
            raise
        except (BinanceError, KeyError, TypeError, ValueError):
            long_short = []
    f = features(Entry("", market, symbol, price, t_ms), SymbolData(hourly=hourly, daily=list(daily), long_short=long_short))
    return {name: f[name] for name in INDEX_V1}


def giro_scores(signals: list[dict]) -> list[Optional[dict]]:
    """Índice 0-100 de cada moneda frente a las demás del escaneo, y sus señales redondeadas como en la web."""
    scores = index_scores([{"fecha": "", "senales": s} for s in signals], INDEX_V1)
    out: list[Optional[dict]] = []
    for s, score in zip(signals, scores):
        if score is None:
            out.append(None)
            continue
        g = {"v": 1, "indice": round_half_up(score)}
        for name, digits in (("minimo_mas_alto", 2), ("long_short", 4), ("acumulacion", None), ("flujo_tendencia", 3)):
            if s.get(name) is not None:
                g[name] = int(s[name]) if digits is None else round(s[name], digits)
        out.append(g)
    return out


# ---------- guardar ----------
def merge_day(path: Path, fecha: str, coins: list[dict]) -> tuple[dict, int]:
    """Une las monedas al archivo del día: las que ya estaban se quedan como estaban."""
    existing: list[dict] = []
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            existing = [c for c in data.get("monedas", []) if isinstance(c, dict) and c.get("mercado") and c.get("simbolo")]
        except (OSError, ValueError, AttributeError):
            existing = []
    have = {(c["mercado"], c["simbolo"]) for c in existing}
    new = [c for c in coins if (c["mercado"], c["simbolo"]) not in have]
    return {"fecha": fecha, "monedas": existing + new}, len(new)


def record_scan(client: BinanceClient, signals, folder: Path, interval: str, now_ms: int,
                log: Callable[[str], None] = print, workers: int = 8) -> tuple[str, int, int]:
    """Guarda las monedas del escaneo en el seguimiento. Devuelve el día, las añadidas y el total del día."""
    fecha = tracking_day(now_ms)
    hora = iso_ms(now_ms)
    try:
        futures_symbols = {i.symbol for i in client.instruments(FUTURES)}
    except BinanceFatalError:
        raise
    except BinanceError:
        futures_symbols = set()
    giro_on = interval == "1d"
    done = 0

    def extras(s) -> tuple[Optional[dict], Optional[dict]]:
        nonlocal done
        inst = s.instrument
        giro = giro_signals(client, inst, s.price, s.candles, now_ms, inst.symbol in futures_symbols) if giro_on else None
        book = fetch_book(client, inst.market, inst.symbol)
        done += 1
        if done % 50 == 0:
            log(f"  índice de giro y libro de órdenes: {done}/{len(signals)}")
        return giro, book

    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(extras, signals))
    books = [book for _, book in results]
    giros = giro_scores([giro for giro, _ in results]) if giro_on else [None] * len(signals)
    coins = []
    for s, giro, book in zip(signals, giros, books):
        coin = {"mercado": s.instrument.market, "simbolo": s.instrument.symbol, "base": s.instrument.base,
                "quote": s.instrument.quote, "precio": s.price, "hora": hora, "intervalo": interval}
        if giro:
            coin["giro"] = giro
        if book:
            coin["libro"] = book
        coins.append(coin)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{fecha}.json"
    day, added = merge_day(path, fecha, coins)
    path.write_text(json.dumps(day, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    return fecha, added, len(day["monedas"])
