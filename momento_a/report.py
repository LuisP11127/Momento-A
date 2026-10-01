"""Página HTML con gráficos interactivos de los pares que encuentra el scanner."""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Sequence

from .binance import BinanceClient, BinanceFatalError, Candle, Instrument

CHART_INTERVALS = ("2h", "4h", "8h", "12h", "1d")
# 500 velas: ~41 días en 2h y ~16 meses en 1D, con margen para la MA(99).
CHART_LIMIT = 500

TEMPLATE = Path(__file__).with_name("report_template.html")
DATA_PLACEHOLDER = "/*__DATOS__*/null"

ChartCandles = dict[str, list[Candle]]  # intervalo -> velas


def fetch_chart_candles(
    client: BinanceClient, instruments: Sequence[Instrument], workers: int = 8
) -> tuple[dict[Instrument, ChartCandles], dict[str, str]]:
    """Descarga las velas de cada intervalo de ``CHART_INTERVALS`` para cada par.

    Un intervalo que falla se omite (y se anota en el segundo valor devuelto);
    el gráfico de ese par mostrará los demás.
    """
    jobs = [(inst, interval) for inst in instruments for interval in CHART_INTERVALS]
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


def compact(candles: Sequence[Candle]) -> list[list[float]]:
    """[tiempo (s), apertura, máximo, mínimo, cierre, volumen] por vela."""
    return [[c.open_time // 1000, c.open, c.high, c.low, c.close, c.volume] for c in candles]


def render_html(
    pairs: Sequence[tuple[dict, ChartCandles]],
    summary: Sequence[str],
    quote: str,
    generated_at: str,
) -> str:
    """``pairs``: (registro del par como en el CSV/JSON, velas por intervalo)."""
    data = {
        "generado": generated_at,
        "resumen": list(summary),
        "cotizacion": quote,
        "intervalos": list(CHART_INTERVALS),
        "pares": [
            {**record, "velas": {iv: compact(c) for iv, c in candles.items()}}
            for record, candles in pairs
        ],
    }
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    # Los datos van dentro de un <script>: que un "</" no cierre la etiqueta antes de
    # tiempo y que los separadores de línea Unicode no rompan el JavaScript.
    for raw, escaped in (("</", "<\\/"), (" ", "\\u2028"), (" ", "\\u2029")):
        payload = payload.replace(raw, escaped)
    template = TEMPLATE.read_text(encoding="utf-8")
    return template.replace(DATA_PLACEHOLDER, payload)
