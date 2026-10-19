"""Pruebas de la web en un navegador de verdad (Chromium), con Binance y GitHub simulados.

Necesitan Playwright (``pip install playwright`` y ``python -m playwright install chromium``); sin él se
omiten. Variables opcionales:

- ``MOMENTO_CHROMIUM``: ruta de un Chromium ya instalado.
- ``MOMENTO_LWC``: copia local de la librería de gráficos (si no, se descarga del CDN).
"""
from __future__ import annotations

import base64
import functools
import json
import os
import re
import threading
import time
from datetime import date, datetime, timedelta, timezone
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from momento_a.binance import Candle  # noqa: E402
from momento_a.giro import INDEX_V1, Entry, SymbolData, features, index_scores  # noqa: E402
from momento_a.web import main as build_site  # noqa: E402

STEP = {"1m": 60_000, "5m": 300_000, "15m": 900_000, "30m": 1_800_000, "1h": 3_600_000, "2h": 7_200_000,
        "4h": 14_400_000, "6h": 21_600_000, "8h": 28_800_000, "12h": 43_200_000, "1d": 86_400_000, "1w": 604_800_000}
CORS = {"Access-Control-Allow-Origin": "*", "Access-Control-Expose-Headers": "Retry-After"}
MONTHS = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sept", "oct", "nov", "dic"]
LIMA = timezone(timedelta(hours=-5))


def now_ms() -> int:
    return int(time.time() * 1000)


def candle_day(ms: int) -> date:
    """Día de seguimiento en Lima: la fecha local en que abre la vela diaria de Binance (00:00 UTC)."""
    utc = datetime.fromtimestamp(ms / 1000, timezone.utc)
    return datetime(utc.year, utc.month, utc.day, tzinfo=timezone.utc).astimezone(LIMA).date()


def short_date(d: date) -> str:
    return f"{d.day} {MONTHS[d.month - 1]}"


# ---------- Binance simulada ----------
def trend_klines(kind: str, tf: str, limit: int, end: int | None = None, listed: int | None = None) -> list:
    """Velas hasta ahora (o hasta end). match: bajista (−1 % por vela) con la última verde +5 % sobre la MA7.
    jumpy: igual, con una vela de −5,7 % tres velas antes de la última. up: alcista.
    listed: velas que tiene el par desde que se listó (por defecto, sin límite)."""
    step = STEP[tf]
    now_last = now_ms() // step
    last = min(now_last, end // step) if end else now_last
    first = now_last - listed + 1 if listed else -10**9
    out = []
    for i in range(max(last - limit + 1, first), last + 1):
        k = i - (now_last - 400)  # la serie no depende de cuántas velas se pidan
        if kind == "up":
            o, c = 100 + k, 101 + k
        else:
            c = 1000 * 0.99 ** k
            o = 1000 * 0.99 ** (k - 1)
            if i == now_last:
                o, c = 1000 * 0.99 ** (k - 1), 1000 * 0.99 ** (k - 1) * 1.05
            elif kind == "jumpy" and i == now_last - 3:
                o = c * 1.06
        out.append([i * step, str(o), str(max(o, c) * 1.002), str(min(o, c) * 0.998), str(c), "1000",
                    i * step + step - 1, str(c * 1000), 10, "1", "1", "0"])
    return out


class Binance:
    """Mercados simulados. kinds: símbolo → tipo de velas; fail: símbolo → fallo («hang» la primera vez,
    «500» la primera vez, «429» la primera vez, «dead» siempre 500); long_short: símbolo de futuros → ratio
    long/short de cuentas (los demás, como en Binance, 400); books: símbolo → libro de órdenes (por defecto,
    uno pequeño)."""

    def __init__(self, spot: dict, futures: dict, fail: dict | None = None, klines=None, price=None, listed=None,
                 long_short=None, books=None):
        self.kinds = {"spot": spot, "futures": futures}
        self.fail = fail or {}
        self.long_short = long_short or {}
        self.books = books or {}
        self.listed = listed or {}
        self.klines = klines or (lambda market, sym, tf, limit, start, end: trend_klines(
            self.kinds[market].get(sym, "up"), tf, limit, end, self.listed.get(sym)))
        self.price = price or (lambda market, sym: 60.5)
        self.seen = {}
        self.calls = []

    def __call__(self, route):
        url = urlparse(route.request.url)
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        market = "futures" if url.hostname.startswith("fapi") else "spot"
        table = self.kinds[market]
        sym = q.get("symbol")
        self.calls.append((market, url.path, sym, q.get("interval"), q.get("limit"), q.get("endTime")))
        if url.path.endswith("/exchangeInfo"):
            body = {"rateLimits": [{"rateLimitType": "REQUEST_WEIGHT", "interval": "MINUTE", "intervalNum": 1,
                                    "limit": 6000 if market == "spot" else 2400}],
                    "symbols": [{"symbol": s, "status": "TRADING", "baseAsset": s[:-4], "quoteAsset": "USDT",
                                 "isSpotTradingAllowed": True, "contractType": "PERPETUAL", "underlyingType": "COIN"}
                                for s in table]}
        elif url.path.endswith("/ticker/24hr"):
            body = [{"symbol": s, "quoteVolume": "2500000.5", "priceChangePercent": "3.25"} for s in table]
        elif url.path.endswith("/ticker/price"):
            body = [{"symbol": s, "price": str(self.price(market, s))} for s in table]
        elif url.path.endswith("/klines"):
            n = self.seen[market + sym] = self.seen.get(market + sym, 0) + 1
            how = self.fail.get(sym)
            if how == "hang" and n == 1:
                return  # nunca responde
            if how == "dead" or (how == "500" and n == 1):
                return route.fulfill(status=500, body="{}", headers=CORS)
            if how == "429" and n == 1:
                return route.fulfill(status=429, body="{}", headers={**CORS, "Retry-After": "1"})
            start = int(q["startTime"]) if "startTime" in q else None
            end = int(q["endTime"]) if "endTime" in q else None
            body = self.klines(market, sym, q["interval"], min(int(q["limit"]), 1000), start, end)
        elif url.path.endswith("/depth"):
            body = self.books.get(sym) or {"bids": [["9.9", "10"], ["9.8", "20"]], "asks": [["10.1", "10"], ["10.2", "5"]]}
        elif url.path.endswith("/globalLongShortAccountRatio") and market == "futures":
            if sym not in self.long_short:
                return route.fulfill(status=400, body='{"code": -1121, "msg": "Invalid symbol."}', headers=CORS)
            hour = now_ms() // 3_600_000 * 3_600_000
            body = [{"symbol": sym, "longShortRatio": str(self.long_short[sym] - 0.1), "timestamp": hour - 3_600_000},
                    {"symbol": sym, "longShortRatio": str(self.long_short[sym]), "timestamp": hour}]
        else:
            return route.fulfill(status=404, body="{}", headers=CORS)
        return route.fulfill(status=200, content_type="application/json", body=json.dumps(body), headers=CORS)


class GitHub:
    """Repositorio simulado: contenidos en memoria y lista de commits."""

    def __init__(self):
        self.files = {}
        self.commits = []

    def __call__(self, route):
        req = route.request
        headers = {"Access-Control-Allow-Origin": "*", "Access-Control-Allow-Headers": "*",
                   "Access-Control-Allow-Methods": "GET, PUT, DELETE", "Content-Type": "application/json"}
        if req.method == "OPTIONS":
            return route.fulfill(status=204, headers=headers, body="")
        m = re.search(r"/contents/(.+)$", urlparse(req.url).path)
        if not m:
            return route.fulfill(status=200, headers=headers, body=json.dumps({"full_name": "o/r", "default_branch": "main"}))
        path = m.group(1)
        if req.method == "GET":
            if path not in self.files:
                return route.fulfill(status=404, headers=headers, body='{"message": "Not Found"}')
            content = base64.b64encode(self.files[path].encode()).decode()
            return route.fulfill(status=200, headers=headers, body=json.dumps({"sha": f"sha{len(self.commits)}", "content": content}))
        data = json.loads(req.post_data)
        self.files[path] = base64.b64decode(data["content"]).decode()
        self.commits.append((path, data["message"]))
        return route.fulfill(status=201, headers=headers, body=json.dumps({"content": {"sha": f"sha{len(self.commits)}"}}))

    def day(self, fecha: str) -> dict:
        return json.loads(self.files[f"docs/seguimiento/{fecha}.json"])


# ---------- página ----------
class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


@pytest.fixture(scope="session")
def site(tmp_path_factory):
    folder = tmp_path_factory.mktemp("sitio")
    tracking = tmp_path_factory.mktemp("seguimiento")
    build_site([str(folder), "--seguimiento", str(tracking)])
    (folder / "bstocks.json").write_text(json.dumps({"simbolos": ["STOCKUSDT"]}), encoding="utf-8")
    server = ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(folder)))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}/index.html"
    server.shutdown()


@pytest.fixture(scope="session")
def browser():
    with sync_api.sync_playwright() as p:
        b = p.chromium.launch(executable_path=os.environ.get("MOMENTO_CHROMIUM") or None)
        yield b
        b.close()


@pytest.fixture
def open_page(browser, site):
    """open_page(binance, github=None, storage=None, mobile=False) → página en la web, con hora de Lima."""
    contexts = []

    def opener(binance, github=None, storage=None, mobile=False, path=""):
        size = {"width": 390, "height": 844} if mobile else {"width": 1440, "height": 900}
        ctx = browser.new_context(viewport=size, timezone_id="America/Lima")
        contexts.append(ctx)
        page = ctx.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        lwc = os.environ.get("MOMENTO_LWC")

        def route(r):
            host = urlparse(r.request.url).hostname
            if "lightweight-charts@" in r.request.url:
                return r.fulfill(path=lwc, content_type="application/javascript", headers=CORS) if lwc else r.continue_()
            if host in ("api.binance.com", "fapi.binance.com"):
                return binance(r)
            if host == "api.github.com" and github:
                return github(r)
            if host == "127.0.0.1":
                return r.continue_()
            return r.abort()

        page.route("**/*", route)
        init = {"momento-a:" + k: json.dumps(v) for k, v in (storage or {}).items()}
        if github:
            init["momento-a:github"] = json.dumps({"owner": "o", "repo": "r", "branch": "main", "token": "t"})
        page.add_init_script(
            "window.__MOMENTO_STALL_MS = 1500;"
            f"for (const [k, v] of Object.entries({json.dumps(init)})) if (localStorage.getItem(k) === null) localStorage.setItem(k, v);")
        page.goto(site + path)
        return page

    yield opener
    for ctx in contexts:
        ctx.close()


def scan(page, timeout=60_000):
    page.click("#scan-go")
    page.wait_for_function("() => /Escaneo terminado/.test(document.getElementById('scan-status').textContent)", timeout=timeout)


def cards(page):
    return page.eval_on_selector_all("#grid .card .sym", "els => els.map(e => e.firstChild.textContent)")


def tracking_rows(page):
    return page.eval_on_selector_all(
        "#track-days .day:first-child tbody tr",
        "rs => rs.map(r => ({ coin: r.querySelector('.coin').firstChild.textContent, star: r.classList.contains('starred'),"
        " hit: r.classList.contains('hit50'), r50: r.querySelector('.c-r50').textContent }))")


# ---------- pruebas ----------
def test_scan_survives_hanging_failing_and_limited_requests(open_page):
    """Una petición que no responde, un 500, un 429 y un par que nunca responde no dejan el escaneo parado."""
    binance = Binance(
        spot={"BTCUSDT": "up", "SAUSDT": "match", "HANGUSDT": "match", "DEADUSDT": "match", "STOCKUSDT": "match"},
        futures={"ETHUSDT": "up", "FAUSDT": "match", "FLAKYUSDT": "match", "LIMITUSDT": "match"},
        fail={"HANGUSDT": "hang", "DEADUSDT": "dead", "FLAKYUSDT": "500", "LIMITUSDT": "429"})
    page = open_page(binance)
    page.uncheck("#scan-track")
    scan(page)
    assert sorted(cards(page)) == ["FA", "FLAKY", "HANG", "LIMIT", "SA"]  # sin STOCK (bStocks) ni las alcistas
    assert "1 par no respondió" in page.text_content("#notice")
    assert not [c for c in binance.calls if c[1].endswith("/depth")]  # sin «Guardar en seguimiento», sin libro de órdenes
    # Durante el escaneo solo se baja el gráfico completo de su temporalidad; el resto, al ver cada tarjeta.
    charts = {c[3] for c in binance.calls if c[1].endswith("/klines") and c[4] == "299"}
    assert "1d" in charts
    page.wait_for_function("() => document.querySelectorAll('#grid .tf-chip.on').length >= 5 * 4")
    assert page.errors == []


def test_saving_to_github_after_a_scan_does_not_freeze_and_skips_empty_commits(open_page):
    """Antes, con GitHub conectado y nada pendiente al abrir, la subida tras el escaneo bloqueaba la página."""
    github = GitHub()
    page = open_page(Binance(spot={"SAUSDT": "match"}, futures={"FAUSDT": "match"}), github=github)
    scan(page)
    expected = candle_day(now_ms())
    page.wait_for_function("() => /y en GitHub/.test(document.getElementById('scan-status').textContent)", timeout=15_000)
    assert f"«Seguimiento {expected.day} de" in page.text_content("#scan-status")
    assert [m for _, m in github.commits] == [f"Seguimiento {expected.isoformat()} (2 monedas)"]
    scan(page)  # mismo resultado: no hay nada nuevo que subir
    page.wait_for_function("() => /y en GitHub/.test(document.getElementById('scan-status').textContent)", timeout=15_000)
    assert len(github.commits) == 1
    assert page.errors == []


def test_stars_pin_coins_first_in_tracking_and_sync_to_github(open_page):
    github = GitHub()
    page = open_page(Binance(spot={"SAUSDT": "match", "SBUSDT": "match"}, futures={"FAUSDT": "match"}), github=github)
    scan(page)
    page.wait_for_function("() => /y en GitHub/.test(document.getElementById('scan-status').textContent)", timeout=15_000)
    star = page.locator('#grid .star[aria-label$=" FAUSDT"]')
    star.click()
    assert star.get_attribute("aria-pressed") == "true"
    star.focus()
    page.keyboard.press("Enter")  # la estrella no abre el gráfico
    page.keyboard.press("Enter")
    assert star.get_attribute("aria-pressed") == "true" and page.is_hidden("#viewer")
    assert len(github.commits) == 1  # las estrellas se suben unos segundos después del último cambio, juntas
    page.wait_for_timeout(5000)
    assert github.commits[-1][1].endswith("(3 monedas, 1 con estrella)")
    fecha = candle_day(now_ms()).isoformat()
    starred = [c["simbolo"] for c in github.day(fecha)["monedas"] if c.get("estrella")]
    assert starred == ["FAUSDT"]

    page.click("#views button[data-view='track']")
    page.wait_for_selector("#track-days tbody tr")
    heads = page.eval_on_selector_all("#track-days .day:first-child thead th", "ths => ths.map(t => t.textContent)")
    assert heads[2:] == ["Precio actual", "Mínimo", "Máximo", "Rumbo al 50", "Variación", "Escaneo"]
    rows = tracking_rows(page)
    assert rows[0]["coin"] == "FA" and rows[0]["star"] and not any(r["star"] for r in rows[1:])
    # Desde la tabla: la última pasa arriba y el foco sigue en su estrella.
    last = rows[-1]["coin"]
    page.locator("#track-days .day:first-child tbody tr").last.locator(".star").click()
    rows = tracking_rows(page)
    assert {rows[0]["coin"], rows[1]["coin"]} == {"FA", last} and rows[0]["star"] and rows[1]["star"]
    assert page.evaluate("() => document.activeElement.classList.contains('star')")
    assert page.errors == []


def test_rumbo_al_50_shows_days_and_date_and_paints_the_row(open_page):
    """El máximo de AAA pasa de +50 % sobre su precio de seguimiento dos días después del escaneo; BBB no llega."""
    midnight = now_ms() // STEP["1d"] * STEP["1d"]
    scanned = midnight - 4 * STEP["1d"] + 6 * 3_600_000
    crossed = midnight - 2 * STEP["1d"] + 12 * 3_600_000
    fecha = candle_day(scanned)

    def price(sym, t):
        if sym == "AAAUSDT":
            return 15.0 if t >= crossed else 10.0
        return 11.0

    def klines(market, sym, tf, limit, start, end):
        step = STEP[tf]
        t = (start // step * step) if start else (now_ms() // step - limit + 1) * step
        out = []
        while t <= now_ms() and len(out) < limit:
            o, c = price(sym, t), price(sym, min(t + step - 1, now_ms()))
            out.append([t, str(o), str(max(o, c)), str(min(o, c)), str(c), "1", t + step - 1, "1", 1, "1", "1", "0"])
            t += step
        return out

    binance = Binance(spot={"AAAUSDT": "match", "BBBUSDT": "match"}, futures={}, klines=klines,
                      price=lambda market, sym: price(sym, now_ms()))
    coin = {"mercado": "spot", "quote": "USDT", "precio": 10.0, "hora": datetime.fromtimestamp(scanned / 1000, timezone.utc)
            .isoformat(timespec="milliseconds").replace("+00:00", "Z"), "intervalo": "1d"}
    day = {"fecha": fecha.isoformat(), "monedas": [dict(coin, simbolo="AAAUSDT", base="AAA"), dict(coin, simbolo="BBBUSDT", base="BBB")]}
    page = open_page(binance, storage={"seguimiento": {fecha.isoformat(): day}}, path="#seguimiento")
    page.wait_for_function("() => /calculados/.test(document.getElementById('track-ext').textContent)", timeout=30_000)
    rows = {r["coin"]: r for r in tracking_rows(page)}
    reached = candle_day(crossed)
    assert rows["AAA"]["hit"] and rows["AAA"]["r50"] == f"{(reached - fecha).days} días· {short_date(reached)}"
    assert not rows["BBB"]["hit"] and rows["BBB"]["r50"] == "—"
    assert "1 llegó al +50%" in page.text_content("#track-days .day:first-child .day-stats")
    assert page.errors == []


def test_accumulation_filter_keeps_coins_with_small_candles(open_page):
    """Acumulación: velas entre ±4 % durante al menos 5 días antes de la vela evaluada."""
    page = open_page(Binance(spot={"SAUSDT": "match", "JUMPYUSDT": "jumpy"}, futures={"FAUSDT": "match"}))
    page.uncheck("#scan-track")
    scan(page)
    assert sorted(cards(page)) == ["FA", "JUMPY", "SA"]
    assert page.text_content("#f-acum-unit") == "días"
    page.check("#f-acum")
    page.fill("#f-acum-pct", "4")
    page.fill("#f-acum-n", "5")
    page.wait_for_function("() => document.querySelectorAll('#grid .card').length === 2")
    assert sorted(cards(page)) == ["FA", "SA"]  # JUMPY tuvo una vela de −5,7 % hace tres días
    assert re.search(r"Acumulación \d+ días ±4%", page.text_content("#grid .card"))
    assert "acumulación ±4% ≥ 5 días" in page.text_content("#cond")
    page.fill("#f-acum-pct", "0.5")  # las velas bajan un 1 %: ninguna cumple
    page.wait_for_function("() => document.querySelectorAll('#grid .card').length === 0")
    page.uncheck("#f-acum")
    page.wait_for_function("() => document.querySelectorAll('#grid .card').length === 3")
    assert page.errors == []


def test_tracking_fits_on_a_phone(open_page):
    fecha = candle_day(now_ms()).isoformat()
    hora = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    long = {"mercado": "futures", "simbolo": "1000BANANAS31USDT", "base": "1000BANANAS31", "quote": "USDT",
            "precio": 0.00012345, "hora": hora, "intervalo": "1d", "estrella": True, "estrella_hora": hora}
    storage = {"seguimiento": {fecha: {"fecha": fecha, "monedas": [long]}},
               "seguimiento-extremos": {f"{fecha}|futures:1000BANANAS31USDT": {"min": 0.00009876, "max": 0.00023456, "hasta": now_ms()}}}
    flat = lambda market, sym, tf, limit, start, end: [[now_ms() - 60_000, "0.0002", "0.0002", "0.0002", "0.0002", "1", now_ms(), "1", 1, "1", "1", "0"]]
    binance = Binance(spot={}, futures={"1000BANANAS31USDT": "match"}, klines=flat, price=lambda market, sym: 0.0002)
    page = open_page(binance, storage=storage, mobile=True, path="#seguimiento")
    page.wait_for_function("() => /calculados/.test(document.getElementById('track-ext').textContent)", timeout=30_000)
    assert page.is_visible("#track-days .c-r50.hit")  # +50 %: cuarta línea de la fila en el móvil
    overflow = page.evaluate("""() => [...document.querySelectorAll('.day-body td')].filter((td) => {
        const r = td.closest('.day-body').getBoundingClientRect(), t = td.getBoundingClientRect();
        return t.width && t.right > r.right + 0.5; }).length""")
    assert overflow == 0
    assert page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth")
    assert page.errors == []


def local_days(page) -> dict:
    return page.evaluate("() => JSON.parse(localStorage.getItem('momento-a:seguimiento') || '{}')")


def starred(days: dict, fecha: str, symbol: str) -> bool:
    return any(c["simbolo"] == symbol and c.get("estrella") for c in days.get(fecha, {}).get("monedas", []))


def test_viewer_star_marks_the_coin_only_on_the_day_it_is_set(open_page):
    """SA salió hace dos días y vuelve a salir hoy: la estrella de hoy no toca el día anterior, ni al revés."""
    older_ms = now_ms() - 2 * STEP["1d"]
    older = candle_day(older_ms).isoformat()
    today = candle_day(now_ms()).isoformat()
    hora = datetime.fromtimestamp(older_ms / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    old_sa = {"mercado": "spot", "simbolo": "SAUSDT", "base": "SA", "quote": "USDT", "precio": 30.0, "hora": hora, "intervalo": "1d"}
    github = GitHub()
    page = open_page(Binance(spot={"SAUSDT": "match", "SBUSDT": "match"}, futures={}), github=github,
                     storage={"seguimiento": {older: {"fecha": older, "monedas": [old_sa]}}})
    scan(page)
    page.click('#grid .card[data-key="spot:SAUSDT"]')
    star = page.locator("#v-star .star")
    assert star.get_attribute("aria-pressed") == "false"
    star.click()
    assert star.get_attribute("aria-pressed") == "true"
    assert page.get_attribute('#grid .card[data-key="spot:SAUSDT"] .star', "aria-pressed") == "true"  # la tarjeta también
    days = local_days(page)
    assert starred(days, today, "SAUSDT") and not starred(days, older, "SAUSDT")
    page.keyboard.press("Escape")

    # En el seguimiento: estrella solo en el día de hoy.
    page.click("#views button[data-view='track']")
    page.wait_for_selector(f'[data-day="{today}"] tbody tr')
    assert "starred" in page.locator(f'[data-day="{today}"] tr[data-key="spot:SAUSDT"]').get_attribute("class").split()
    page.click(f'[data-day="{older}"] .day-toggle')
    old_row = page.locator(f'[data-day="{older}"] tr[data-key="spot:SAUSDT"]')
    assert "starred" not in (old_row.get_attribute("class") or "")
    # Desde el gráfico del día anterior, la estrella es la de ese día.
    old_row.click()
    assert "null" not in page.text_content("#v-stats")
    star = page.locator("#v-star .star")
    assert star.get_attribute("aria-pressed") == "false"
    star.click()
    days = local_days(page)
    assert starred(days, older, "SAUSDT") and starred(days, today, "SAUSDT")
    star.click()
    days = local_days(page)
    assert not starred(days, older, "SAUSDT") and starred(days, today, "SAUSDT")
    page.keyboard.press("Escape")
    page.wait_for_timeout(5000)
    in_github = {f: [c["simbolo"] for c in json.loads(text)["monedas"] if c.get("estrella")] for f, text in github.files.items()}
    assert in_github[f"docs/seguimiento/{today}.json"] == ["SAUSDT"]
    assert in_github.get(f"docs/seguimiento/{older}.json", []) == []
    assert page.errors == []


def test_viewer_loads_the_whole_history(open_page):
    """El par lleva 2500 velas diarias: el visor empieza con 1000 y carga las anteriores hasta la primera."""
    binance = Binance(spot={"OLDUSDT": "match"}, futures={}, listed={"OLDUSDT": 2500})
    page = open_page(binance)
    page.uncheck("#scan-track")
    scan(page)
    page.click('#grid .card[data-key="spot:OLDUSDT"]')
    page.wait_for_function("() => /1000 velas desde/.test(document.getElementById('v-hist').textContent)")
    assert "null" not in page.text_content("#v-stats")  # sin acumulación no queda un hueco con «null»
    page.mouse.move(2, 2)  # fuera del gráfico: la leyenda es la de la última vela (+5 % de apertura a cierre)
    page.wait_for_timeout(200)
    assert "Var +5.00%" in page.text_content("#v-legend")
    older = lambda: [c for c in binance.calls if c[1].endswith("/klines") and c[5]]
    assert older() == []
    for _ in range(8):  # alejar hasta el borde izquierdo: se piden las anteriores
        page.keyboard.press("-")
        page.wait_for_timeout(120)
    for _ in range(50):
        if older():
            break
        page.wait_for_timeout(100)
    assert older(), "al llegar al borde izquierdo no se pidieron las velas anteriores"
    page.keyboard.press("0")  # ver todo el historial
    page.wait_for_function("() => /Todo el historial: desde el/.test(document.getElementById('v-hist').textContent)")
    assert [c[4] for c in older()] == ["1000", "1000"]
    first = datetime.fromtimestamp((now_ms() // STEP["1d"] - 2499) * STEP["1d"] / 1000, timezone.utc)
    assert str(first.year) in page.text_content("#v-hist")
    assert page.evaluate("() => document.querySelector('#v-legend').textContent.length > 0")
    assert page.errors == []


def test_section_of_coins_that_reached_50_with_starred_first(open_page):
    """Sección «Llegaron al +50%»: AAA y CCC salieron los dos días y llegaron; BBB y DDD no. Cada moneda sale en su
    día más antiguo (el 1) y, además, CCC en el día 2, en el que tiene estrella."""
    midnight = now_ms() // STEP["1d"] * STEP["1d"]
    scanned = {1: midnight - 5 * STEP["1d"] + 6 * 3_600_000, 2: midnight - 4 * STEP["1d"] + 6 * 3_600_000}
    crossed = {"AAAUSDT": midnight - 3 * STEP["1d"] + 12 * 3_600_000, "CCCUSDT": midnight - 2 * STEP["1d"] + 12 * 3_600_000}
    fechas = {n: candle_day(ms).isoformat() for n, ms in scanned.items()}

    def price(sym, t):
        return (16.0 if t >= crossed[sym] else 10.0) if sym in crossed else 11.0

    def klines(market, sym, tf, limit, start, end):
        step = STEP[tf]
        t = (start // step * step) if start else (now_ms() // step - limit + 1) * step
        out = []
        while t <= now_ms() and len(out) < limit:
            o, c = price(sym, t), price(sym, min(t + step - 1, now_ms()))
            out.append([t, str(o), str(max(o, c)), str(min(o, c)), str(c), "1", t + step - 1, "1", 1, "1", "1", "0"])
            t += step
        return out

    def coin(sym, n, **extra):
        hora = datetime.fromtimestamp(scanned[n] / 1000, timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
        return dict({"mercado": "spot", "simbolo": sym, "base": sym[:-4], "quote": "USDT", "precio": 10.0, "hora": hora, "intervalo": "1d"}, **extra)

    star_time = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    days = {fechas[1]: {"fecha": fechas[1], "monedas": [coin("AAAUSDT", 1), coin("BBBUSDT", 1), coin("CCCUSDT", 1)]},
            fechas[2]: {"fecha": fechas[2], "monedas": [coin("AAAUSDT", 2), coin("CCCUSDT", 2, estrella=True, estrella_hora=star_time),
                                                       coin("DDDUSDT", 2)]}}
    binance = Binance(spot={s: "match" for s in ("AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT")}, futures={}, klines=klines,
                      price=lambda market, sym: price(sym, now_ms()))
    page = open_page(binance, storage={"seguimiento": days}, path="#seguimiento")
    page.wait_for_selector("#track-view button")
    page.click("#track-view button:has-text('Llegaron al +50%')")
    page.wait_for_function("() => /calculados/.test(document.getElementById('track-ext').textContent)", timeout=30_000)
    page.wait_for_selector(".day.hits tbody tr[data-key]")

    def hit_rows():
        return page.eval_on_selector_all(".day.hits tbody tr", "rs => rs.map(r => r.classList.contains('group') ? r.textContent"
                                         " : r.querySelector('.coin').firstChild.textContent + (r.classList.contains('starred') ? '★' : '')"
                                         " + ' ' + r.dataset.fecha)")

    rows = hit_rows()
    assert rows[:2] == ["★ Con estrella · 1", f"CCC★ {fechas[2]}"]  # el día con estrella, aunque CCC se repita
    assert rows[2] == "Sin estrella · 2" and sorted(rows[3:]) == [f"AAA {fechas[1]}", f"CCC {fechas[1]}"]  # su día más antiguo
    assert len(rows) == 5  # ni AAA del día 2 (repetida), ni BBB ni DDD (no llegaron)
    assert page.text_content("#track-view button[aria-pressed='true'] .count") == "3"
    heads = page.eval_on_selector_all(".day.hits thead th", "ths => ths.map(t => t.textContent)")
    assert heads[:3] == ["Moneda", "Día", "Precio seguimiento"] and "Rumbo al 50" in heads
    # Estrella desde la sección: AAA sube con las de estrella, solo en su día.
    page.locator('.day.hits tr[data-key="spot:AAAUSDT"] .star').click()
    rows = hit_rows()
    assert rows[0] == "★ Con estrella · 2" and sorted(rows[1:3]) == [f"AAA★ {fechas[1]}", f"CCC★ {fechas[2]}"]
    stored = local_days(page)
    assert starred(stored, fechas[1], "AAAUSDT") and starred(stored, fechas[2], "CCCUSDT")
    # El gráfico de una moneda de la sección lleva la estrella de su día.
    page.click(f'.day.hits tr[data-key="spot:CCCUSDT"][data-fecha="{fechas[2]}"]')
    assert page.get_attribute("#v-star .star", "aria-pressed") == "true"
    page.keyboard.press("Escape")
    # No se borra nada: la vista por días sigue con sus dos días y sus cuatro monedas.
    page.click("#track-view button:has-text('Por día')")
    assert page.eval_on_selector_all("#track-days .day", "ds => ds.map(d => d.dataset.day)") == [fechas[2], fechas[1]]
    # La vista elegida se recuerda.
    page.click("#track-view button:has-text('Llegaron al +50%')")
    page.reload()
    page.wait_for_selector(".day.hits")
    assert page.errors == []


def test_giro_index_matches_the_python_test_and_is_saved_in_tracking(open_page):
    """El índice de giro de la web sale igual que el de momento_a.giro con las mismas velas: AAA (spot y futuros)
    y DDD tienen ratio long/short; BBB y CCC no tienen futuros. Las señales varían de una moneda a otra."""
    hour = 3_600_000
    spec = {  # dip: mínimo de 4 días cerrados; compras a mercado antes y en las últimas 30 h; precio hace 7 días
        "AAAUSDT": {"dip": 0.97, "old": 0.60, "new": 0.52, "before": 1.10},
        "BBBUSDT": {"dip": 1.00, "old": 0.45, "new": 0.55, "before": 1.10},
        "CCCUSDT": {"dip": 0.90, "old": 0.55, "new": 0.50, "before": 0.90},
        "DDDUSDT": {"dip": 0.95, "old": 0.50, "new": 0.62, "before": 1.10},
    }
    served = {}  # (mercado, símbolo, temporalidad) → velas que dio Binance (las de más velas)

    def klines(market, sym, tf, limit, start, end):
        out = trend_klines("match" if sym in spec else "up", tf, limit, end)
        if sym in spec and tf == "1d":
            for k in out[-5:-1]:  # el mínimo de los últimos días cerrados (la vela en curso no cambia)
                k[3] = str(float(k[3]) * spec[sym]["dip"])
        if sym in spec and tf == "1h":
            # Velas de 1 hora: hoy, al precio del escaneo; antes, al de hace 7 días. Volumen 1000; compras a mercado
            # según la parte de las últimas 30 horas o la anterior.
            price = float(trend_klines("match", "1d", 1)[-1][4])
            day0, now_last = now_ms() // STEP["1d"] * STEP["1d"], now_ms() // hour
            out = []
            for i in range(now_last - limit + 1, now_last + 1):
                p = price if i * hour >= day0 else price * spec[sym]["before"]
                share = spec[sym]["new"] if i > now_last - 30 else spec[sym]["old"]
                out.append([i * hour, str(p), str(p), str(p), str(p), "1000", i * hour + hour - 1, str(p * 1000), 10,
                            str(1000 * share), str(p * 1000 * share), "0"])
        key = (market, sym, tf)
        if len(out) >= len(served.get(key, [])):
            served[key] = out
        return out

    binance = Binance(spot={"AAAUSDT": "match", "BBBUSDT": "match", "CCCUSDT": "match", "BTCUSDT": "up"},
                      futures={"AAAUSDT": "match", "DDDUSDT": "match"}, klines=klines,
                      long_short={"AAAUSDT": 2.2, "DDDUSDT": 1.3})
    page = open_page(binance)
    scan(page)
    shown = dict(page.eval_on_selector_all("#grid .card", "cs => cs.map(c => [c.dataset.key, c.querySelector('.giro').textContent])"))
    assert sorted(shown) == ["futures:AAAUSDT", "futures:DDDUSDT", "spot:AAAUSDT", "spot:BBBUSDT", "spot:CCCUSDT"]
    asked = {c[2] for c in binance.calls if c[1].endswith("/globalLongShortAccountRatio")}
    assert asked == {"AAAUSDT", "DDDUSDT"}  # solo las que tienen contrato perpetuo

    # Lo mismo con momento_a.giro, con las velas que dio Binance y la hora y el precio guardados en el seguimiento.
    (day,) = local_days(page).values()
    saved = {c["mercado"] + ":" + c["simbolo"]: c for c in day["monedas"]}
    assert sorted(saved) == sorted(shown)
    rows = []
    for key, c in saved.items():
        market, sym = key.split(":")
        t = int(datetime.fromisoformat(c["hora"].replace("Z", "+00:00")).timestamp() * 1000)
        candles = lambda tf: [Candle.from_kline(k) for k in served[(market, sym, tf)]]
        ls = binance.long_short.get(sym)
        data = SymbolData(hourly=candles("1h"), daily=candles("1d"),
                          long_short=[] if ls is None else [(t // hour * hour, ls)])
        f = features(Entry(day["fecha"], market, sym, c["precio"], t), data)
        g = c["giro"]
        assert g["minimo_mas_alto"] == pytest.approx(f["minimo_mas_alto"], abs=0.006), key
        assert g["flujo_tendencia"] == pytest.approx(f["flujo_tendencia"], abs=0.15), key
        assert g["acumulacion"] == f["acumulacion"] and g.get("long_short") == f["long_short"], key
        rows.append({"fecha": day["fecha"], "key": key, "senales": f})
    for row, expected in zip(rows, index_scores(rows, INDEX_V1)):
        assert abs(saved[row["key"]]["giro"]["indice"] - expected) <= 0.5 + 1e-9, row["key"]
        assert shown[row["key"]] == f"Giro {saved[row['key']]['giro']['indice']}"
    assert {saved[k]["giro"]["acumulacion"] for k in saved} == {0, 1}  # las señales de verdad cambian entre monedas
    assert len({saved[k]["giro"]["indice"] for k in saved}) >= 3

    # Orden por el índice, detalle en el visor y columna del seguimiento.
    page.select_option("#sort", "giro")
    order = page.eval_on_selector_all("#grid .card .giro", "gs => gs.map(g => +g.textContent.replace('Giro ', ''))")
    assert order == sorted(order, reverse=True)
    page.click('#grid .card[data-key="spot:BBBUSDT"]')
    stats = page.text_content("#v-stats")
    assert "Long/short sin datos" in stats and "Acumulación no" in stats and "Compras 24 h +" in stats
    page.keyboard.press("Escape")
    page.click("#views button[data-view='track']")
    page.wait_for_selector("#track-days .day:first-child tbody tr .giro")
    tags = page.eval_on_selector_all("#track-days .day:first-child tbody tr", "rs => rs.map(r => [r.dataset.key, r.querySelector('.giro').textContent])")
    assert dict(tags) == shown
    assert page.errors == []


def test_no_giro_index_with_other_candles(open_page):
    """El índice se probó con velas 1D: con otras temporalidades no se calcula ni se piden sus datos."""
    binance = Binance(spot={"SAUSDT": "match"}, futures={"FAUSDT": "match"})
    page = open_page(binance, storage={"scan": {"market": "ambos", "interval": "4h", "track": True}})
    scan(page)
    assert sorted(cards(page)) == ["FA", "SA"] and page.locator("#grid .giro").count() == 0
    assert not [c for c in binance.calls if c[3] == "1h" or c[1].endswith("/globalLongShortAccountRatio")]
    assert all("giro" not in c for day in local_days(page).values() for c in day["monedas"])
    assert page.errors == []


def test_order_book_summary_is_saved_with_each_coin(open_page):
    """En cada escaneo que se guarda, un resumen del libro de órdenes de cada moneda: USDT en compras y ventas a ±1, 2
    y 5 % del precio medio, spread y la orden más grande de cada lado. En FA el libro descargado (500 niveles, el
    máximo que se pide en futuros) no llega al −2 %: esas bandas no se guardan."""
    sa = {"bids": [["100", "10"], ["99.5", "20"], ["98.5", "6"], ["96", "100"], ["90", "1000"]],
          "asks": [["100.2", "10"], ["101", "50"], ["101.5", "2"], ["104", "10"], ["120", "500"]]}
    fa = {"bids": [[f"{100 - i * 0.003:.3f}", "1"] for i in range(500)], "asks": [["100.01", "1"], ["101", "1"]]}
    binance = Binance(spot={"SAUSDT": "match"}, futures={"FAUSDT": "match"}, books={"SAUSDT": sa, "FAUSDT": fa})
    page = open_page(binance)
    scan(page)
    assert {(c[0], c[2], c[4]) for c in binance.calls if c[1].endswith("/depth")} == {("spot", "SAUSDT", "1000"), ("futures", "FAUSDT", "500")}
    coins = {c["simbolo"]: c for day in local_days(page).values() for c in day["monedas"]}
    # Precio medio 100,1: ±1 % es de 99,099 a 101,101; ±2 %, de 98,098 a 102,102; ±5 %, de 95,095 a 105,105.
    assert coins["SAUSDT"]["libro"] == {
        "v": 1, "spread": 0.1998,
        "compras_1": 2990, "ventas_1": 6052,      # 100×10 + 99,5×20 | 100,2×10 + 101×50
        "compras_2": 3581, "ventas_2": 6255,      # + 98,5×6 | + 101,5×2
        "compras_5": 13181, "ventas_5": 7295,     # + 96×100 | + 104×10 (las de 90 y 120 quedan fuera)
        "muro_compra": 9600, "muro_compra_dist": -4.096, "muro_venta": 5050, "muro_venta_dist": 0.899,
    }
    fa_book = coins["FAUSDT"]["libro"]
    assert "compras_1" in fa_book and "compras_2" not in fa_book and "compras_5" not in fa_book
    assert {"ventas_1", "ventas_2", "ventas_5"} <= set(fa_book)  # del lado de las ventas está el libro entero
    assert page.locator("#grid .card").count() == 2 and "libro" not in page.text_content("#grid").lower()
    assert page.errors == []
