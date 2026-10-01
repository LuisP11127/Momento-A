"""Versión web: el escáner completo en una página, que escanea desde el navegador.

    python -m momento_a.web sitio                                  # escribe sitio/index.html
    python -m momento_a.web sitio --bstocks                        # y sitio/bstocks.json (acciones tokenizadas)
    python -m momento_a.web sitio --seguimiento docs/seguimiento   # y sitio/seguimiento.json (todos los días)
"""
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Sequence

from .binance import BinanceClient, is_tokenized_stock
from .report import render_report

DAY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
COIN_FIELDS = ("mercado", "simbolo", "base", "quote", "precio", "hora", "intervalo")


def collect_tracking(folder: Path) -> list[dict]:
    """Une los archivos de seguimiento (uno por día, AAAA-MM-DD.json), del día más reciente al más antiguo.

    La web los guarda en el repositorio; los que no tengan el formato esperado se ignoran.
    """
    days = {}
    for path in sorted(Path(folder).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"Aviso: {path} no es un JSON válido; se ignora.")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("monedas"), list):
            print(f"Aviso: {path} no tiene el formato de seguimiento; se ignora.")
            continue
        fecha = data.get("fecha")
        if not (isinstance(fecha, str) and DAY_RE.match(fecha)):
            fecha = path.stem if DAY_RE.match(path.stem) else None
        if not fecha:
            print(f"Aviso: {path} no indica la fecha; se ignora.")
            continue
        coins = [
            {field: coin.get(field) for field in COIN_FIELDS}
            for coin in data["monedas"]
            if isinstance(coin, dict) and coin.get("mercado") and coin.get("simbolo")
        ]
        days[fecha] = {"fecha": fecha, "monedas": coins}
    return [days[fecha] for fecha in sorted(days, reverse=True)]


def render_app() -> str:
    """Página sin resultados: se escanea desde el propio navegador."""
    return render_report(None)


def stock_symbols(client: Optional[BinanceClient] = None) -> list[str]:
    """Pares spot que Binance etiqueta como acciones tokenizadas (bStocks)."""
    tags = (client or BinanceClient()).asset_tags()
    if tags is None:
        raise RuntimeError("no se pudo consultar la lista de productos de Binance")
    return sorted(symbol for symbol, symbol_tags in tags.items() if is_tokenized_stock(symbol_tags))


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m momento_a.web", description=__doc__.splitlines()[0])
    parser.add_argument("carpeta", nargs="?", default="sitio", help="carpeta de salida (por defecto: sitio)")
    parser.add_argument("--bstocks", action="store_true", help="escribir también bstocks.json")
    parser.add_argument("--seguimiento", metavar="CARPETA",
                        help="unir los archivos de seguimiento de CARPETA en seguimiento.json")
    args = parser.parse_args(argv)

    out = Path(args.carpeta)
    out.mkdir(parents=True, exist_ok=True)
    (out / "index.html").write_text(render_app(), encoding="utf-8")
    print(f"Web escrita en {out / 'index.html'}")
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if args.seguimiento:
        days = collect_tracking(Path(args.seguimiento))
        data = {"generado": now, "dias": days}
        (out / "seguimiento.json").write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                                              encoding="utf-8")
        print(f"{len(days)} días de seguimiento en {out / 'seguimiento.json'}")
    if args.bstocks:
        try:
            symbols = stock_symbols()
        except RuntimeError as exc:
            print(f"Aviso: {exc}; la web avisará de que pueden aparecer acciones tokenizadas.")
            return 0
        data = {"generado": now, "simbolos": symbols}
        (out / "bstocks.json").write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"{len(symbols)} acciones tokenizadas en {out / 'bstocks.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
