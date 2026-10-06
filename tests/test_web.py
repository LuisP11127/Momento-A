import json

from momento_a.web import collect_tracking, main, stock_symbols


def write(path, data):
    path.write_text(json.dumps(data) if not isinstance(data, str) else data, encoding="utf-8")


def test_collect_tracking_merges_days_newest_first(tmp_path):
    coin = {"mercado": "spot", "simbolo": "AAAUSDT", "base": "AAA", "quote": "USDT", "precio": 1.5,
            "hora": "2026-10-01T17:48:22.461Z", "intervalo": "1d", "extra": "se ignora"}
    write(tmp_path / "2026-09-30.json", {"fecha": "2026-09-30", "monedas": [coin]})
    write(tmp_path / "2026-10-01.json", {"monedas": [coin, {"mercado": "spot"}]})  # fecha del nombre; moneda sin símbolo fuera
    write(tmp_path / "roto.json", "{no es json")
    write(tmp_path / "otro.json", {"monedas": [coin]})  # sin fecha
    (tmp_path / "README.md").write_text("# Seguimiento", encoding="utf-8")

    days = collect_tracking(tmp_path)
    assert [d["fecha"] for d in days] == ["2026-10-01", "2026-09-30"]
    assert days[0]["monedas"] == [{k: v for k, v in coin.items() if k != "extra"}]


def test_collect_tracking_keeps_stars(tmp_path):
    base = {"mercado": "futures", "base": "AAA", "quote": "USDT", "precio": 2.0, "hora": "2026-10-06T18:15:34.833Z", "intervalo": "1d"}
    starred = dict(base, simbolo="AAAUSDT", estrella=True, estrella_hora="2026-10-06T18:20:00.000Z")
    unstarred = dict(base, simbolo="BBBUSDT", estrella=False, estrella_hora="2026-10-06T18:21:00.000Z")
    plain = dict(base, simbolo="CCCUSDT", estrella=True)  # sin hora de la marca: no cuenta
    write(tmp_path / "2026-10-05.json", {"fecha": "2026-10-05", "monedas": [starred, unstarred, plain]})

    (day,) = collect_tracking(tmp_path)
    assert day["monedas"][0] == starred
    assert day["monedas"][1] == unstarred  # quitar la estrella también se conserva
    assert "estrella" not in day["monedas"][2] and "estrella_hora" not in day["monedas"][2]


def test_main_writes_app_and_tracking(tmp_path, capsys):
    folder = tmp_path / "seguimiento"
    folder.mkdir()
    write(folder / "2026-10-01.json", {"fecha": "2026-10-01", "monedas": []})
    out = tmp_path / "sitio"

    assert main([str(out), "--seguimiento", str(folder)]) == 0
    html = (out / "index.html").read_text(encoding="utf-8")
    assert '<script id="momento-data" type="application/json">null</script>' in html
    data = json.loads((out / "seguimiento.json").read_text(encoding="utf-8"))
    assert [d["fecha"] for d in data["dias"]] == ["2026-10-01"]
    assert "1 días de seguimiento" in capsys.readouterr().out


class FakeClient:
    def __init__(self, tags):
        self.tags = tags

    def asset_tags(self):
        return self.tags


def test_stock_symbols():
    tags = {"AAPLBUSDT": ["bStocks"], "BTCUSDT": ["Layer1"], "SPYBUSDT": ["bStocks", "ETF"]}
    assert stock_symbols(FakeClient(tags)) == ["AAPLBUSDT", "SPYBUSDT"]
