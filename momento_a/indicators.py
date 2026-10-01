"""Indicadores técnicos."""
from __future__ import annotations

from typing import Optional, Sequence


def sma(values: Sequence[float], period: int) -> list[Optional[float]]:
    """Media móvil simple (la "MA" de los gráficos de Binance).

    Devuelve una lista alineada con ``values``: ``None`` mientras no haya
    ``period`` datos y, a partir de ahí, la media de los últimos ``period``.
    """
    if period <= 0:
        raise ValueError("period debe ser positivo")
    out: list[Optional[float]] = [None] * len(values)
    for i in range(period - 1, len(values)):
        out[i] = sum(values[i - period + 1 : i + 1]) / period
    return out
