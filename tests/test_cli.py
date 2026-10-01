import csv
import json

from helpers import downtrend, make_candles
from momento_a.binance import FUTURES, SPOT, BinanceFatalError, Instrument
from momento_a.cli import RECORD_FIELDS, fmt_price, fmt_volume, main


class FakeClient:
    def __init__(self, data, error=None):
        self.data = data
        self.error = error
        self.intervals = set()

    def instruments(self, market, quote):
        if self.error:
            raise self.error
        return [i for i in self.data if i.market == market and i.quote == quote]

    def candles(self, instrument, interval, limit):
        self.intervals.add(interval)
        return self.data[instrument]

    def tickers_24h(self, market):
        return {i.symbol: {"quoteVolume": "2500000", "priceChangePercent": "1.25"} for i in self.data if i.market == market}

    def asset_tags(self):
        return {}


def embedded(html):
    marker = '<script id="momento-data" type="application/json">'
    start = html.index(marker) + len(marker)
    return json.loads(html[start : html.index("</script>", start)])


def sample_client():
    strong, _ = downtrend(last_close=88.0)
    weak, _ = downtrend(last_close=85.0)
    uptrend, _ = make_candles([100.0 + i for i in range(120)])
    return FakeClient(
        {
            Instrument(SPOT, "AAAUSDT", "AAA", "USDT"): weak,
            Instrument(FUTURES, "BBBUSDT", "BBB", "USDT"): strong,
            Instrument(SPOT, "CCCUSDT", "CCC", "USDT"): uptrend,
        }
    )


def test_prints_table_sorted_by_strength(capsys):
    assert main([], client=sample_client()) == 0
    out = capsys.readouterr().out
    assert "3 pares (SPOT 2, FUT 1)" in out
    assert "MA7 < MA25 < MA99" in out
    assert "Coincidencias: 2" in out
    assert "VELAS≥MA7" in out and "VOL 24H (USDT)" in out and "2.5M" in out
    assert out.index("BBBUSDT") < out.index("AAAUSDT")  # la vela más fuerte primero
    assert "CCCUSDT" not in out


def test_interval_option_is_used_for_the_scan(capsys):
    client = sample_client()
    assert main(["--intervalo", "4h"], client=client) == 0
    assert client.intervals == {"4h"}
    assert "Velas 4h · vela en curso" in capsys.readouterr().out


def test_market_filter_and_exports(tmp_path, capsys):
    csv_path, json_path = tmp_path / "r.csv", tmp_path / "r.json"
    assert main(["--mercado", "spot", "--csv", str(csv_path), "--json", str(json_path)], client=sample_client()) == 0
    capsys.readouterr()

    with open(csv_path, newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    assert [r["par"] for r in rows] == ["AAAUSDT"]
    assert tuple(rows[0]) == RECORD_FIELDS
    assert rows[0]["url"] == "https://www.binance.com/es/trade/AAA_USDT?type=spot"

    assert rows[0]["velas_sobre_ma7"] == "1" and rows[0]["volumen_24h"] == "2500000.0"

    # El JSON es el informe completo (con velas): la web lo abre con «Cargar informe».
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert list(payload["mercados"]) == ["spot"]
    (item,) = payload["mercados"]["spot"]["coincidencias"]
    assert item["simbolo"] == "AAAUSDT" and item["ma7"] < item["ma25"]
    assert set(item["graficos"]) == {"2h", "4h", "8h", "12h"} and item["velas"]


def test_markdown_report_links_pairs_and_mentions_fallback(tmp_path, capsys):
    client = sample_client()
    client.fallbacks_used = {FUTURES: "https://fapi.binance.com"}
    client.base_urls = {FUTURES: "https://www.binance.com"}
    md_path = tmp_path / "r.md"
    assert main(["--markdown", str(md_path)], client=client) == 0
    out = capsys.readouterr().out
    md = md_path.read_text(encoding="utf-8")

    assert "datos de FUT tomados de https://www.binance.com" in out
    assert md.startswith("## Momento-A · scanner MA 1D")
    assert "- Coincidencias: 2" in md
    assert "| [BBBUSDT](https://www.binance.com/es/futures/BBBUSDT) | " in md
    assert "CCCUSDT" not in md


def test_html_report_has_charts_for_each_match(tmp_path, capsys):
    html_path = tmp_path / "reportes" / "momento-a.html"
    assert main(["--html", str(html_path)], client=sample_client()) == 0
    assert "Informe con gráficos guardado" in capsys.readouterr().out

    data = embedded(html_path.read_text(encoding="utf-8"))
    assert data["intervalo"] == "1d" and data["criterios"]["tolerancia"] == 2.0
    assert {k: [c["simbolo"] for c in m["coincidencias"]] for k, m in data["mercados"].items()} == {
        "spot": ["AAAUSDT"], "futures": ["BBBUSDT"]}
    assert data["mercados"]["spot"]["analizadas"] == 2


def test_no_matches_message(capsys):
    assert main(["--tolerancia", "0", "--max-encima", "0.1"], client=sample_client()) == 0
    assert "Ningún par cumple" in capsys.readouterr().out


def test_fatal_error_exit_code(capsys):
    assert main([], client=FakeClient({}, error=BinanceFatalError("HTTP 451"))) == 1
    assert "HTTP 451" in capsys.readouterr().err


def test_formatters():
    assert fmt_price(62345.123) == "62,345.12"
    assert fmt_price(1.23456) == "1.2346"
    assert fmt_price(0.000012345) == "0.00001234"
    assert fmt_volume(1_234_567_890) == "1.2B"
    assert fmt_volume(2_500_000) == "2.5M"
    assert fmt_volume(999) == "999"
    assert fmt_volume(None) == "-"
