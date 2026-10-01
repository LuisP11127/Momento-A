"""Filtro por medias móviles (velas de 1D por defecto).

Busca criptomonedas en tendencia bajista (MA7 < MA25, y por defecto también
MA25 < MA99) cuya vela empieza a tomar fuerza: está muy cerca o por encima de
la MA(7) y es verde, mientras la MA(7) sigue debajo de la MA(25).
"""
from __future__ import annotations

import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Iterable, Optional, Sequence

from .binance import KLINES_LIMIT, SPOT, BinanceClient, BinanceError, BinanceFatalError, Candle, Instrument
from .binance import is_tokenized_stock
from .indicators import sma

FAST, MID, SLOW = 7, 25, 99

# Temporalidades de Binance que se pueden escanear, de menor a mayor.
INTERVALS = ("15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h", "1d", "1w")


@dataclass(frozen=True)
class Criteria:
    # Temporalidad de las velas.
    interval: str = "1d"
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
    # Volumen mínimo en las últimas 24 h, en la moneda de cotización.
    min_quote_volume: float = 0.0
    # Evaluar la última vela cerrada en vez de la vela en curso.
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
    bars_above_ma7: int  # velas seguidas (incluida la evaluada) cerrando >= MA(7)
    change_24h_pct: Optional[float] = None
    quote_volume_24h: Optional[float] = None
    # Velas del escaneo (para el gráfico del informe).
    candles: tuple[Candle, ...] = field(default=(), repr=False, compare=False)


@dataclass
class ScanResult:
    signals: list[Signal]
    instruments: list[Instrument]
    errors: dict[str, str] = field(default_factory=dict)
    # Pares con historial suficiente para la MA(99), por mercado.
    analyzed: dict[str, int] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


def pct(value: float, reference: float) -> float:
    return (value - reference) / reference * 100.0


def usable_candles(candles: Sequence[Candle], criteria: Criteria, now_ms: int) -> Sequence[Candle]:
    """Las velas que se evalúan: sin la vela en curso si se pide la última cerrada."""
    if criteria.closed_candle and candles and candles[-1].close_time > now_ms:
        return candles[:-1]
    return candles


def evaluate(
    instrument: Instrument,
    candles: Sequence[Candle],
    criteria: Criteria,
    now_ms: Optional[int] = None,
) -> Optional[Signal]:
    """Devuelve la señal si la última vela cumple los criterios, si no ``None``."""
    if now_ms is None:
        now_ms = int(time.time() * 1000)
    candles = usable_candles(candles, criteria, now_ms)
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

    bars_above = 0
    for close, ma in zip(reversed(closes), reversed(ma7s)):
        if ma is None or close < ma:
            break
        bars_above += 1

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
        bars_above_ma7=bars_above,
        candles=tuple(candles),
    )


SORT_KEYS: dict[str, Callable[[Signal], Any]] = {
    "fuerza": lambda s: -s.change_pct,  # vela más fuerte primero
    "ma7": lambda s: abs(s.dist_ma7_pct),  # más pegadas a la MA(7) primero
    "cruce": lambda s: -s.gap_ma7_ma25_pct,  # MA(7) más cerca de cruzar la MA(25) primero
    "bajista": lambda s: s.dist_ma99_pct,  # más hundidas respecto a la MA(99) primero
    "volumen": lambda s: -(s.quote_volume_24h or 0.0),
    "variacion": lambda s: -(s.change_24h_pct if s.change_24h_pct is not None else -math.inf),
    "simbolo": lambda s: s.instrument.symbol,
}


def _float(value) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _without_stocks(client: BinanceClient, instruments: list[Instrument], warnings: list[str]) -> list[Instrument]:
    """Quita de spot las acciones tokenizadas de Binance (bStocks): no son criptomonedas."""
    tags = client.asset_tags()
    if tags is None:
        warnings.append("no se pudo consultar qué pares son acciones tokenizadas (bStocks); pueden aparecer")
        return instruments
    return [inst for inst in instruments if not is_tokenized_stock(tags.get(inst.symbol))]


def scan(
    client: BinanceClient,
    markets: Iterable[str],
    quote: str,
    criteria: Criteria,
    workers: int = 8,
    on_progress: Optional[Callable[[int, int], None]] = None,
) -> ScanResult:
    markets = list(markets)
    instruments: list[Instrument] = []
    warnings: list[str] = []
    stats: dict[Instrument, tuple[Optional[float], Optional[float]]] = {}  # (variación 24h, volumen 24h)
    for market in markets:
        listed = client.instruments(market, quote)
        if market == SPOT:
            listed = _without_stocks(client, listed, warnings)
        try:
            tickers = client.tickers_24h(market)
        except BinanceFatalError:
            raise
        except BinanceError as exc:
            warnings.append(f"{market}: sin datos de las últimas 24 h ({exc})")
            tickers = {}
        for inst in listed:
            ticker = tickers.get(inst.symbol, {})
            change, volume = _float(ticker.get("priceChangePercent")), _float(ticker.get("quoteVolume"))
            # El volumen se filtra antes de descargar velas: ahorra peticiones.
            if criteria.min_quote_volume > 0 and (volume or 0.0) < criteria.min_quote_volume:
                continue
            instruments.append(inst)
            stats[inst] = (change, volume)

    now_ms = int(time.time() * 1000)
    signals: list[Signal] = []
    errors: dict[str, str] = {}
    analyzed = {market: 0 for market in markets}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        pending = {pool.submit(client.candles, inst, criteria.interval, KLINES_LIMIT): inst for inst in instruments}
        for done, future in enumerate(as_completed(pending), 1):
            inst = pending[future]
            try:
                candles = future.result()
                if len(usable_candles(candles, criteria, now_ms)) >= SLOW:
                    analyzed[inst.market] += 1
                signal = evaluate(inst, candles, criteria, now_ms)
            except BinanceFatalError:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            except Exception as exc:  # un par con datos raros no tumba el escaneo
                errors[f"{inst.market}:{inst.symbol}"] = str(exc)
            else:
                if signal:
                    change, volume = stats[inst]
                    signals.append(replace(signal, change_24h_pct=change, quote_volume_24h=volume))
            if on_progress:
                on_progress(done, len(instruments))
    return ScanResult(signals=signals, instruments=instruments, errors=errors, analyzed=analyzed, warnings=warnings)
