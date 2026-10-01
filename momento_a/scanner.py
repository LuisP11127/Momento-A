"""Filtro por medias móviles en velas de 1D.

Busca criptomonedas en tendencia bajista (MA7 < MA25, y por defecto también
MA25 < MA99) cuya vela diaria empieza a tomar fuerza: está muy cerca o por
encima de la MA(7) y es verde, mientras la MA(7) sigue debajo de la MA(25).
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional, Sequence

from .binance import BinanceClient, BinanceFatalError, Candle, Instrument
from .indicators import sma

FAST, MID, SLOW = 7, 25, 99


@dataclass(frozen=True)
class Criteria:
    # Cuánto puede estar el precio por debajo de la MA(7) (en %) y seguir
    # contando como "muy cerca".
    tolerance_pct: float = 2.0
    # Si se indica, descarta las velas que ya se alejaron más de este % por
    # encima de la MA(7).
    max_above_pct: Optional[float] = None
    # "Muy bajista": además de MA7 < MA25, exige MA25 < MA99.
    require_ma99: bool = True
    # La vela tiene que ser verde (cierre/precio actual > apertura).
    require_green: bool = True
    # Volumen mínimo (en la moneda de cotización) de la última vela cerrada.
    min_quote_volume: float = 0.0
    # Evaluar la última vela cerrada en vez de la vela del día en curso.
    closed_candle: bool = False


@dataclass(frozen=True)
class Signal:
    instrument: Instrument
    candle_open_time: int
    price: float
    change_pct: float  # variación de la vela: (cierre - apertura) / apertura
    ma7: float
    ma25: float
    ma99: float
    dist_ma7_pct: float  # precio respecto a la MA(7)
    gap_ma7_ma25_pct: float  # MA(7) respecto a la MA(25): < 0, cuanto más cerca de 0 más cerca del cruce
    dist_ma99_pct: float  # precio respecto a la MA(99): cuanto más negativo, más bajista
    days_above_ma7: int  # velas seguidas (incluida la evaluada) cerrando >= MA(7)
    quote_volume: float  # volumen de la última vela cerrada


@dataclass
class ScanResult:
    signals: list[Signal]
    instruments: list[Instrument]
    errors: dict[str, str] = field(default_factory=dict)


def pct(value: float, reference: float) -> float:
    return (value - reference) / reference * 100.0


def evaluate(
    instrument: Instrument,
    candles: Sequence[Candle],
    criteria: Criteria,
    now_ms: Optional[int] = None,
) -> Optional[Signal]:
    """Devuelve la señal si la última vela cumple los criterios, si no ``None``."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    if criteria.closed_candle and candles and candles[-1].close_time > now_ms:
        candles = candles[:-1]
    if len(candles) < SLOW:
        return None  # sin historial suficiente para la MA(99)

    closes = [c.close for c in candles]
    ma7s = sma(closes, FAST)
    ma7, ma25, ma99 = ma7s[-1], sma(closes, MID)[-1], sma(closes, SLOW)[-1]
    last = candles[-1]

    # Tendencia bajista: la MA(7) sigue por debajo de la MA(25)...
    if not ma7 < ma25:
        return None
    # ...y, en modo "muy bajista", la MA(25) también por debajo de la MA(99).
    if criteria.require_ma99 and not ma25 < ma99:
        return None

    # La vela está muy cerca o por encima de la MA(7).
    dist_ma7 = pct(last.close, ma7)
    if dist_ma7 < -criteria.tolerance_pct:
        return None
    if criteria.max_above_pct is not None and dist_ma7 > criteria.max_above_pct:
        return None

    # La vela toma fuerza.
    if criteria.require_green and not last.close > last.open:
        return None

    last_closed = next((c for c in reversed(candles) if c.close_time <= now_ms), last)
    if last_closed.quote_volume < criteria.min_quote_volume:
        return None

    days_above = 0
    for close, ma in zip(reversed(closes), reversed(ma7s)):
        if ma is None or close < ma:
            break
        days_above += 1

    return Signal(
        instrument=instrument,
        candle_open_time=last.open_time,
        price=last.close,
        change_pct=pct(last.close, last.open),
        ma7=ma7,
        ma25=ma25,
        ma99=ma99,
        dist_ma7_pct=dist_ma7,
        gap_ma7_ma25_pct=pct(ma7, ma25),
        dist_ma99_pct=pct(last.close, ma99),
        days_above_ma7=days_above,
        quote_volume=last_closed.quote_volume,
    )


SORT_KEYS: dict[str, Callable[[Signal], float]] = {
    "fuerza": lambda s: -s.change_pct,  # vela más fuerte primero
    "ma7": lambda s: abs(s.dist_ma7_pct),  # más pegadas a la MA(7) primero
    "cruce": lambda s: -s.gap_ma7_ma25_pct,  # MA(7) más cerca de cruzar la MA(25) primero
    "bajista": lambda s: s.dist_ma99_pct,  # más hundidas respecto a la MA(99) primero
    "volumen": lambda s: -s.quote_volume,
}


def scan(
    client: BinanceClient,
    markets: Iterable[str],
    quote: str,
    criteria: Criteria,
    workers: int = 8,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> ScanResult:
    instruments: list[Instrument] = []
    for market in markets:
        instruments.extend(client.instruments(market, quote))

    now_ms = int(time.time() * 1000)
    signals: list[Signal] = []
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(client.daily_candles, inst): inst for inst in instruments}
        for done, future in enumerate(as_completed(pending), 1):
            inst = pending[future]
            try:
                signal = evaluate(inst, future.result(), criteria, now_ms)
            except BinanceFatalError:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            except Exception as exc:  # un par con datos raros no tumba el escaneo
                errors[f"{inst.market}:{inst.symbol}"] = str(exc)
            else:
                if signal:
                    signals.append(signal)
            if on_progress:
                on_progress(done, len(instruments))
    return ScanResult(signals=signals, instruments=instruments, errors=errors)
