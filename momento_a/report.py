"""Informe con los gráficos de cada moneda encontrada.

La plantilla sirve para dos cosas: con los datos de un escaneo es el informe
(``--html``); sin datos es la versión web, que escanea desde el navegador
(``python -m momento_a.web``).
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from .binance import BinanceClient, BinanceFatalError, Candle, Instrument
from .scanner import FAST, INTERVALS, MID, SLOW, SORT_KEYS, Criteria, ScanResult, Signal

# Temporalidades en las que se mira si también se cumple la condición («Cumple en»).
CHART_INTERVALS = ("2h", "4h", "8h", "12h", "1d")
# Velas de cada gráfico: unos 10 meses en 1D, y el peso de la petición sigue siendo 2.
CHART_LIMIT = 299

TEMPLATE = Path(__file__).with_name("report_template.html")
BODY_MARKER = "<!--BODY-->"
DATA_MARKER = "__MOMENTO_DATA__"

MARKET_NAMES = {"spot": "Spot", "futures": "Futuros"}

ChartCandles = dict[str, list[Candle]]  # temporalidad -> velas


def chart_intervals(interval: str) -> list[str]:
    """Las temporalidades de «Cumple en» más la del escaneo, de menor a mayor."""
    wanted = set(CHART_INTERVALS) | {interval}
    return [tf for tf in INTERVALS if tf in wanted]


def fetch_chart_candles(
    client: BinanceClient,
    instruments: Sequence[Instrument],
    intervals: Sequence[str] = CHART_INTERVALS,
    workers: int = 8,
) -> tuple[dict[Instrument, ChartCandles], dict[str, str]]:
    """Descarga las velas de cada temporalidad para el gráfico de cada par.

    Una temporalidad que falla se omite (y se anota en el segundo valor devuelto);
    el gráfico de ese par mostrará las demás.
    """
    jobs = [(inst, interval) for inst in instruments for interval in intervals]
    charts: dict[Instrument, ChartCandles] = {inst: {} for inst in instruments}
    errors: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(client.candles, inst, interval, CHART_LIMIT) for inst, interval in jobs]
        for (inst, interval), future in zip(jobs, futures):
            try:
                charts[inst][interval] = future.result()
            except BinanceFatalError:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            except Exception as exc:
                errors[f"{inst.market}:{inst.symbol} {interval}"] = str(exc)
    return charts, errors


def rows(candles: Sequence[Candle]) -> list[list[float]]:
    """[apertura en ms, apertura, máximo, mínimo, cierre, volumen] por vela."""
    return [[c.open_time, c.open, c.high, c.low, c.close, c.volume] for c in candles]


def signal_item(signal: Signal, interval: str, charts: Optional[ChartCandles] = None) -> dict:
    """Una moneda encontrada, como la lee la página."""
    inst = signal.instrument
    charts = charts or {}
    return {
        "mercado": inst.market,
        "intervalo": interval,
        "simbolo": inst.symbol,
        "base": inst.base,
        "quote": inst.quote,
        "precio": signal.price,
        "vela_pct": round(signal.change_pct, 4),
        "ma7": signal.ma7,
        "ma25": signal.ma25,
        "ma99": signal.ma99,
        "vs_ma7_pct": round(signal.dist_ma7_pct, 4),
        "ma7_vs_ma25_pct": round(signal.gap_ma7_ma25_pct, 4),
        "vs_ma99_pct": round(signal.dist_ma99_pct, 4),
        "velas_sobre_ma7": signal.bars_above_ma7,
        "var_24h_pct": signal.change_24h_pct,
        "volumen_24h": signal.quote_volume_24h,
        # Velas de la temporalidad del escaneo: las del gráfico si se descargaron (son más).
        "velas": rows(charts.get(interval) or signal.candles),
        # La misma moneda en otras temporalidades: {"2h": [[...], ...], "4h": ...}
        "graficos": {tf: rows(c) for tf, c in charts.items() if tf != interval and c},
    }


def build_payload(
    result: ScanResult,
    criteria: Criteria,
    generated_at: datetime,
    order: str = "fuerza",
    charts: Optional[dict[Instrument, ChartCandles]] = None,
    quote: str = "USDT",
) -> dict:
    """Resultados del escaneo con las velas de cada moneda: lo que leen el informe y el .json."""
    charts = charts or {}
    ordered = sorted(result.signals, key=SORT_KEYS[order])
    return {
        "generado": generated_at.isoformat(timespec="seconds"),
        "intervalo": criteria.interval,
        "intervalos_grafico": chart_intervals(criteria.interval),
        "medias": [FAST, MID, SLOW],
        "criterios": {
            "tolerancia": criteria.tolerance_pct,
            "max_encima": criteria.max_above_pct,
            "ma99": criteria.require_ma99,
            "verde": criteria.require_green,
            "min_volumen": criteria.min_quote_volume,
        },
        "solo_cerradas": criteria.closed_candle,
        "orden": order,
        "ejemplo": False,
        "mercados": {
            market: {
                "nombre": MARKET_NAMES.get(market, market),
                "quote": quote,
                "analizadas": analyzed,
                "coincidencias": [
                    signal_item(s, criteria.interval, charts.get(s.instrument))
                    for s in ordered
                    if s.instrument.market == market
                ],
            }
            for market, analyzed in result.analyzed.items()
        },
    }


def render_report(payload: Optional[dict]) -> str:
    """HTML de la página. Sin ``payload`` es la versión web, lista para escanear."""
    head, body = TEMPLATE.read_text(encoding="utf-8").split(BODY_MARKER)
    # "<" solo aparece dentro de cadenas JSON; escaparlo evita cerrar el <script> que lo contiene.
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")
    body = body.replace(DATA_MARKER, data)
    return (
        '<!doctype html>\n<html lang="es">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
        f"{head.strip()}\n</head>\n<body>\n{body.strip()}\n</body>\n</html>\n"
    )


def write_report(path: Path, payload: Optional[dict]) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(payload), encoding="utf-8")
    return path
