import pytest
import requests

from momento_a import binance
from momento_a.binance import FUTURES, SPOT, BinanceClient, BinanceError, BinanceFatalError, Instrument


class FakeResponse:
    def __init__(self, status, payload=None, headers=None):
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = str(payload)

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeSession:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.responses.pop(0)


def client_with(responses):
    client = BinanceClient()
    session = FakeSession(responses)
    client._session = lambda: session
    return client, session


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(binance.time, "sleep", lambda s: None)


def test_spot_instruments_filters_quote_status_and_stablecoins():
    info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "baseAsset": "BTC", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
            {"symbol": "ETHBTC", "status": "TRADING", "baseAsset": "ETH", "quoteAsset": "BTC", "isSpotTradingAllowed": True},
            {"symbol": "LUNAUSDT", "status": "BREAK", "baseAsset": "LUNA", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
            {"symbol": "FDUSDUSDT", "status": "TRADING", "baseAsset": "FDUSD", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
            {"symbol": "ADAUSDT", "status": "TRADING", "baseAsset": "ADA", "quoteAsset": "USDT", "isSpotTradingAllowed": True},
        ]
    }
    client, session = client_with([FakeResponse(200, info)])
    assert [i.symbol for i in client.instruments(SPOT, "usdt")] == ["ADAUSDT", "BTCUSDT"]
    assert session.calls[0][0] == "https://api.binance.com/api/v3/exchangeInfo"


def test_futures_instruments_only_crypto_perpetuals():
    info = {
        "symbols": [
            {"symbol": "BTCUSDT", "status": "TRADING", "contractType": "PERPETUAL", "baseAsset": "BTC", "quoteAsset": "USDT", "underlyingType": "COIN"},
            {"symbol": "BTCUSDT_261225", "status": "TRADING", "contractType": "CURRENT_QUARTER", "baseAsset": "BTC", "quoteAsset": "USDT", "underlyingType": "COIN"},
            {"symbol": "BTCDOMUSDT", "status": "TRADING", "contractType": "PERPETUAL", "baseAsset": "BTCDOM", "quoteAsset": "USDT", "underlyingType": "INDEX"},
            {"symbol": "1000PEPEUSDT", "status": "TRADING", "contractType": "PERPETUAL", "baseAsset": "1000PEPE", "quoteAsset": "USDT", "underlyingType": "COIN"},
            {"symbol": "OLDUSDT", "status": "SETTLING", "contractType": "PERPETUAL", "baseAsset": "OLD", "quoteAsset": "USDT", "underlyingType": "COIN"},
        ]
    }
    client, session = client_with([FakeResponse(200, info)])
    assert [i.symbol for i in client.instruments(FUTURES, "USDT")] == ["1000PEPEUSDT", "BTCUSDT"]
    assert session.calls[0][0] == "https://fapi.binance.com/fapi/v1/exchangeInfo"


def test_daily_candles_parses_klines_and_uses_market_endpoint():
    kline = [1700006400000, "10.0", "12.0", "9.5", "11.0", "1000", 1700092799999, "10500.5", 42, "1", "1", "0"]
    client, session = client_with([FakeResponse(200, [kline])])
    inst = Instrument(FUTURES, "ETHUSDT", "ETH", "USDT")
    (candle,) = client.daily_candles(inst)
    assert (candle.open, candle.close, candle.quote_volume) == (10.0, 11.0, 10500.5)
    url, params = session.calls[0]
    assert url == "https://fapi.binance.com/fapi/v1/klines"
    assert params["interval"] == "1d" and params["limit"] >= 100


def test_retries_rate_limit_then_succeeds():
    client, session = client_with([FakeResponse(429, headers={"Retry-After": "1"}), FakeResponse(200, [])])
    assert client.daily_candles(Instrument(SPOT, "BTCUSDT", "BTC", "USDT")) == []
    assert len(session.calls) == 2


def test_gives_up_after_max_retries():
    client, _ = client_with([FakeResponse(503)] * 10)
    client.max_retries = 2
    with pytest.raises(BinanceError, match="HTTP 503"):
        client.daily_candles(Instrument(SPOT, "BTCUSDT", "BTC", "USDT"))


def test_geo_block_switches_spot_to_data_api():
    client, session = client_with([FakeResponse(451), FakeResponse(200, {"symbols": []})])
    assert client.instruments(SPOT) == []
    assert session.calls[1][0] == "https://data-api.binance.vision/api/v3/exchangeInfo"
    assert client.fallbacks_used == {SPOT: "https://api.binance.com"}


def test_geo_block_switches_futures_to_www():
    client, session = client_with([FakeResponse(451), FakeResponse(200, [])])
    client.daily_candles(Instrument(FUTURES, "BTCUSDT", "BTC", "USDT"))
    assert session.calls[1][0] == "https://www.binance.com/fapi/v1/klines"
    # Las siguientes peticiones van directamente a la dirección alternativa.
    session.responses.append(FakeResponse(200, []))
    client.daily_candles(Instrument(FUTURES, "ETHUSDT", "ETH", "USDT"))
    assert session.calls[2][0] == "https://www.binance.com/fapi/v1/klines"


def test_geo_block_on_fallback_too_is_fatal():
    client, _ = client_with([FakeResponse(451), FakeResponse(451)])
    with pytest.raises(BinanceFatalError, match="451"):
        client.instruments(SPOT)


def test_instrument_urls():
    assert Instrument(SPOT, "BTCUSDT", "BTC", "USDT").url.endswith("/trade/BTC_USDT?type=spot")
    assert Instrument(FUTURES, "BTCUSDT", "BTC", "USDT").url.endswith("/futures/BTCUSDT")


def test_tickers_24h_by_symbol_and_market_endpoint():
    client, session = client_with([FakeResponse(200, [{"symbol": "BTCUSDT", "quoteVolume": "5"}, {"x": 1}])])
    assert client.tickers_24h(FUTURES) == {"BTCUSDT": {"symbol": "BTCUSDT", "quoteVolume": "5"}}
    assert session.calls[0][0] == "https://fapi.binance.com/fapi/v1/ticker/24hr"


def test_asset_tags_from_binance_products():
    products = {"data": [{"s": "AAPLBUSDT", "tags": ["bStocks"]}, {"s": "BTCUSDT", "tags": None}, "raro"]}
    client, session = client_with([FakeResponse(200, products)])
    assert client.asset_tags() == {"AAPLBUSDT": ["bStocks"], "BTCUSDT": []}
    assert session.calls[0][0] == binance.PRODUCTS_URL
    # Cualquier fallo de esa web se trata como «sin datos».
    client, _ = client_with([FakeResponse(403, {})])
    assert client.asset_tags() is None
    client, _ = client_with([FakeResponse(200, {"data": "otro formato"})])
    assert client.asset_tags() is None
