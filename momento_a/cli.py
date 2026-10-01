"""Línea de comandos: ``python -m momento_a``."""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from datetime import datetime, timezone
from typing import Optional, Sequence

from .binance import FUTURES, FUTURES_URL, SPOT, SPOT_URL, BinanceClient, BinanceError
from .scanner import SORT_KEYS, Criteria, ScanResult, Signal, scan

MARKET_CHOICES = {"spot": (SPOT,), "futuros": (FUTURES,), "ambos": (SPOT, FUTURES)}
MARKET_LABELS = {SPOT: "SPOT", FUTURES: "FUT"}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m momento_a",
        description=(
            "Scanner de Binance (spot y futuros) en velas de 1D. Muestra las criptomonedas "
            "en tendencia bajista (MA7 < MA25 < MA99) cuya vela está muy cerca o por encima "
            "de la MA(7) mientras la MA(7) sigue debajo de la MA(25)."
        ),
    )
    p.add_argument("--mercado", choices=MARKET_CHOICES, default="ambos",
                   help="mercados a escanear (por defecto: ambos)")
    p.add_argument("--cotizacion", default="USDT", metavar="MONEDA",
                   help="moneda de cotización de los pares (por defecto: USDT)")
    p.add_argument("--tolerancia", type=float, default=2.0, metavar="PCT",
                   help="%% máximo por debajo de la MA(7) para contar como 'muy cerca' (por defecto: 2)")
    p.add_argument("--max-encima", type=float, default=None, metavar="PCT",
                   help="descarta velas más de PCT %% por encima de la MA(7) (por defecto: sin límite)")
    p.add_argument("--sin-ma99", action="store_true",
                   help="no exigir MA25 < MA99 (basta con MA7 < MA25)")
    p.add_argument("--permitir-roja", action="store_true",
                   help="incluir también velas rojas (por defecto solo verdes)")
    p.add_argument("--vela-cerrada", action="store_true",
                   help="evaluar la última vela cerrada en vez de la del día en curso")
    p.add_argument("--volumen-min", type=float, default=0.0, metavar="N",
                   help="volumen mínimo de la última vela cerrada, en la moneda de cotización")
    p.add_argument("--orden", choices=SORT_KEYS, default="fuerza",
                   help="fuerza: vela más fuerte · ma7: más pegadas a la MA7 · cruce: MA7 más "
                        "cerca de la MA25 · bajista: más lejos de la MA99 · volumen")
    p.add_argument("--top", type=int, default=0, metavar="N", help="mostrar solo los N primeros")
    p.add_argument("--csv", metavar="FICHERO", help="guardar los resultados en CSV")
    p.add_argument("--json", metavar="FICHERO", help="guardar los resultados en JSON")
    p.add_argument("--markdown", metavar="FICHERO",
                   help="guardar un informe en Markdown (lo usa el resumen de GitHub Actions)")
    p.add_argument("--hilos", type=int, default=8, help="descargas en paralelo (por defecto: 8)")
    p.add_argument("--spot-url", default=SPOT_URL, help=f"URL base de spot (por defecto: {SPOT_URL})")
    p.add_argument("--futuros-url", default=FUTURES_URL,
                   help=f"URL base de futuros (por defecto: {FUTURES_URL})")
    return p


def fmt_price(value: float) -> str:
    if value >= 1000:
        return f"{value:,.2f}"
    if value >= 1:
        return f"{value:.4f}"
    if value <= 0:
        return f"{value:g}"
    decimals = min(-math.floor(math.log10(value)) + 3, 12)  # 4 cifras significativas
    return f"{value:.{decimals}f}"


def fmt_volume(value: float) -> str:
    for limit, suffix in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if value >= limit:
            return f"{value / limit:.1f}{suffix}"
    return f"{value:.0f}"


def fmt_day(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def describe(criteria: Criteria) -> str:
    parts = ["MA7 < MA25 < MA99" if criteria.require_ma99 else "MA7 < MA25"]
    near = f"precio ≥ MA7 −{criteria.tolerance_pct:g}%"
    if criteria.max_above_pct is not None:
        near += f" y ≤ MA7 +{criteria.max_above_pct:g}%"
    parts.append(near)
    if criteria.require_green:
        parts.append("vela verde")
    if criteria.min_quote_volume:
        parts.append(f"volumen ≥ {fmt_volume(criteria.min_quote_volume)}")
    return " · ".join(parts)


def table_headers(quote: str) -> list[str]:
    return ["MERCADO", "PAR", "PRECIO", "VELA %", "vs MA7 %", "MA7/MA25 %",
            "vs MA99 %", "DÍAS≥MA7", f"VOL 1D ({quote})"]


def table_row(s: Signal) -> list[str]:
    return [
        MARKET_LABELS[s.instrument.market],
        s.instrument.symbol,
        fmt_price(s.price),
        f"{s.change_pct:+.2f}",
        f"{s.dist_ma7_pct:+.2f}",
        f"{s.gap_ma7_ma25_pct:+.2f}",
        f"{s.dist_ma99_pct:+.2f}",
        str(s.days_above_ma7),
        fmt_volume(s.quote_volume),
    ]


def render_table(signals: Sequence[Signal], quote: str) -> str:
    headers = table_headers(quote)
    rows = [table_row(s) for s in signals]
    widths = [max(len(h), *(len(r[i]) for r in rows)) for i, h in enumerate(headers)]
    # Las dos primeras columnas son texto (izquierda); el resto, números (derecha).
    def line(cells: Sequence[str]) -> str:
        return "  ".join(c.ljust(w) if i < 2 else c.rjust(w) for i, (c, w) in enumerate(zip(cells, widths)))
    return "\n".join([line(headers), line(["-" * w for w in widths]), *(line(r) for r in rows)])


def render_markdown(signals: Sequence[Signal], quote: str, summary: Sequence[str]) -> str:
    """Informe en Markdown (para el resumen de GitHub Actions); cada par enlaza a Binance."""
    lines = ["## Momento-A · scanner MA 1D", "", *(f"- {line}" for line in summary), ""]
    if not signals:
        lines.append("Ningún par cumple los criterios ahora mismo.")
        return "\n".join(lines) + "\n"
    headers = table_headers(quote)
    lines.append("| " + " | ".join(headers) + " |")
    lines.append("|" + "|".join([":--", ":--"] + ["--:"] * (len(headers) - 2)) + "|")
    for s in signals:
        row = table_row(s)
        row[1] = f"[{row[1]}]({s.instrument.url})"
        lines.append("| " + " | ".join(row) + " |")
    return "\n".join(lines) + "\n"


RECORD_FIELDS = (
    "mercado", "par", "base", "cotizacion", "fecha_vela", "precio", "vela_pct", "ma7", "ma25",
    "ma99", "vs_ma7_pct", "ma7_vs_ma25_pct", "vs_ma99_pct", "dias_sobre_ma7", "volumen_1d", "url",
)


def signal_record(s: Signal) -> dict:
    return {
        "mercado": MARKET_LABELS[s.instrument.market],
        "par": s.instrument.symbol,
        "base": s.instrument.base,
        "cotizacion": s.instrument.quote,
        "fecha_vela": fmt_day(s.candle_open_time),
        "precio": s.price,
        "vela_pct": round(s.change_pct, 4),
        "ma7": s.ma7,
        "ma25": s.ma25,
        "ma99": s.ma99,
        "vs_ma7_pct": round(s.dist_ma7_pct, 4),
        "ma7_vs_ma25_pct": round(s.gap_ma7_ma25_pct, 4),
        "vs_ma99_pct": round(s.dist_ma99_pct, 4),
        "dias_sobre_ma7": s.days_above_ma7,
        "volumen_1d": s.quote_volume,
        "url": s.instrument.url,
    }


def write_csv(path: str, signals: Sequence[Signal]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RECORD_FIELDS)
        writer.writeheader()
        writer.writerows(signal_record(s) for s in signals)


def write_json(path: str, signals: Sequence[Signal]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump([signal_record(s) for s in signals], f, ensure_ascii=False, indent=2)


def progress(done: int, total: int) -> None:
    sys.stderr.write(f"\rDescargando velas 1D: {done}/{total}")
    if done == total:
        sys.stderr.write("\n")
    sys.stderr.flush()


def main(argv: Optional[Sequence[str]] = None, client: Optional[BinanceClient] = None) -> int:
    args = build_parser().parse_args(argv)
    criteria = Criteria(
        tolerance_pct=args.tolerancia,
        max_above_pct=args.max_encima,
        require_ma99=not args.sin_ma99,
        require_green=not args.permitir_roja,
        min_quote_volume=args.volumen_min,
        closed_candle=args.vela_cerrada,
    )
    client = client or BinanceClient(spot_url=args.spot_url, futures_url=args.futuros_url)
    quote = args.cotizacion.upper()

    try:
        result: ScanResult = scan(
            client,
            MARKET_CHOICES[args.mercado],
            quote,
            criteria,
            workers=max(1, args.hilos),
            on_progress=progress if sys.stderr.isatty() else None,
        )
    except BinanceError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    signals = sorted(result.signals, key=SORT_KEYS[args.orden])
    shown = signals[: args.top] if args.top > 0 else signals

    per_market = ", ".join(
        f"{MARKET_LABELS[m]} {sum(i.market == m for i in result.instruments)}"
        for m in MARKET_CHOICES[args.mercado]
    )
    candle = "última vela cerrada" if criteria.closed_candle else "vela del día en curso"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    summary = [
        f"Velas 1D · {candle} · {now} · {len(result.instruments)} pares ({per_market})",
        f"Criterios: {describe(criteria)}",
        f"Coincidencias: {len(signals)}",
    ]
    for market, blocked in getattr(client, "fallbacks_used", {}).items():
        summary.append(
            f"{blocked} no está disponible desde esta ubicación (HTTP 451); "
            f"datos de {MARKET_LABELS[market]} tomados de {client.base_urls[market]}"
        )
    print("\n".join(summary) + "\n")
    if shown:
        print(render_table(shown, quote))
    else:
        print("Ningún par cumple los criterios ahora mismo.")

    if result.errors:
        print(f"\nAviso: {len(result.errors)} pares no se pudieron analizar:", file=sys.stderr)
        for name, msg in list(result.errors.items())[:5]:
            print(f"  {name}: {msg}", file=sys.stderr)

    if args.csv:
        write_csv(args.csv, signals)
        print(f"\nCSV guardado en {args.csv}")
    if args.json:
        write_json(args.json, signals)
        print(f"JSON guardado en {args.json}")
    if args.markdown:
        with open(args.markdown, "w", encoding="utf-8") as f:
            f.write(render_markdown(shown, quote, summary))
    return 0
