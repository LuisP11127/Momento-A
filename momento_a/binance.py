"""Cliente mínimo de la API pública de Binance (spot y futuros USDT-M).

Solo usa endpoints públicos de datos de mercado: no hace falta API key.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

import requests

SPOT_URL = "https://api.binance.com"
FUTURES_URL = "https://fapi.binance.com"

SPOT = "spot"
FUTURES = "futures"
MARKETS = (SPOT, FUTURES)

# Binance bloquea api.binance.com y fapi.binance.com desde algunos países (por
# ejemplo, EE. UU., donde están los servidores de GitHub Actions) con HTTP 451.
# Estas direcciones sirven los mismos datos públicos y sí responden desde ahí.
FALLBACK_URLS = {SPOT: "https://data-api.binance.vision", FUTURES: "https://www.binance.com"}

# 150 velas bastan para la MA(99) y mantienen el peso de la petición en 2.
KLINES_LIMIT = 150

# Monedas estables y fiat: no tienen tendencia, así que no se escanean.
STABLECOINS = frozenset(
    {
        "USDT", "USDC", "FDUSD", "TUSD", "BUSD", "DAI", "USDP", "PAX", "USDS",
        "USDSB", "USDE", "PYUSD", "USD1", "XUSD", "BFUSD", "RLUSD", "USDF",
        "AEUR", "EURI", "EUR", "GBP", "TRY", "BRL", "ARS", "MXN", "JPY",
    }
)


class BinanceError(RuntimeError):
    """Error al pedir datos de un par concreto (se salta ese par)."""


class BinanceFatalError(BinanceError):
    """Error que afecta a todo el escaneo (bloqueo geográfico, ban de IP...)."""


@dataclass(frozen=True)
class Instrument:
    market: str
    symbol: str
    base: str
    quote: str

    @property
    def url(self) -> str:
        if self.market == SPOT:
            return f"https://www.binance.com/es/trade/{self.base}_{self.quote}?type=spot"
        return f"https://www.binance.com/es/futures/{self.symbol}"


@dataclass(frozen=True)
class Candle:
    open_time: int
    open: float
    high: float
    low: float
    close: float
    close_time: int
    quote_volume: float
    volume: float = 0.0  # en la moneda base, como el volumen del gráfico de Binance

    @classmethod
    def from_kline(cls, k: list[Any]) -> "Candle":
        return cls(
            open_time=int(k[0]),
            open=float(k[1]),
            high=float(k[2]),
            low=float(k[3]),
            close=float(k[4]),
            close_time=int(k[6]),
            quote_volume=float(k[7]),
            volume=float(k[5]),
        )


class BinanceClient:
    def __init__(
        self,
        spot_url: str = SPOT_URL,
        futures_url: str = FUTURES_URL,
        timeout: float = 15.0,
        max_retries: int = 4,
    ):
        self.base_urls = {SPOT: spot_url.rstrip("/"), FUTURES: futures_url.rstrip("/")}
        self.timeout = timeout
        self.max_retries = max_retries
        # Mercados que han tenido que pasar a FALLBACK_URLS por un bloqueo 451.
        self.fallbacks_used: dict[str, str] = {}
        self._local = threading.local()
        self._lock = threading.Lock()

    def _session(self) -> requests.Session:
        # Una sesión por hilo: requests.Session no garantiza ser thread-safe.
        session = getattr(self._local, "session", None)
        if session is None:
            session = requests.Session()
            session.headers["User-Agent"] = "momento-a-scanner"
            self._local.session = session
        return session

    def _switch_to_fallback(self, market: str, blocked_base: str) -> bool:
        """Tras un 451 en ``blocked_base``, pasa a la URL alternativa (si queda alguna)."""
        fallback = FALLBACK_URLS[market]
        with self._lock:
            if self.base_urls[market] != blocked_base:
                return True  # otro hilo ya cambió de URL
            if blocked_base == fallback:
                return False
            self.base_urls[market] = fallback
            self.fallbacks_used[market] = blocked_base
            return True

    def _get(self, market: str, path: str, params: Optional[dict] = None) -> Any:
        base = self.base_urls[market]
        url = base + path
        problem = ""
        wait = 0
        for attempt in range(self.max_retries + 1):
            if attempt:
                time.sleep(wait or min(2 ** attempt, 30))
            wait = 0
            try:
                resp = self._session().get(url, params=params, timeout=self.timeout)
            except requests.RequestException as exc:
                problem = str(exc)
                continue
            if resp.status_code == 200:
                return resp.json()
            if resp.status_code == 451:
                if self._switch_to_fallback(market, base):
                    return self._get(market, path, params)
                raise BinanceFatalError(
                    f"Binance bloquea las peticiones desde tu ubicación (HTTP 451) en {url}."
                )
            if resp.status_code == 418:
                raise BinanceFatalError(
                    "Binance ha baneado temporalmente tu IP por exceso de peticiones "
                    "(HTTP 418). Espera unos minutos y usa menos --hilos."
                )
            if resp.status_code == 429 or resp.status_code >= 500:
                problem = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After", "")
                if retry_after.isdigit():
                    wait = int(retry_after)
                continue
            raise BinanceError(f"{url}: HTTP {resp.status_code} {resp.text[:200]}")
        raise BinanceError(f"{url}: {problem} (tras {self.max_retries} reintentos)")

    def instruments(self, market: str, quote: str = "USDT") -> list[Instrument]:
        """Pares en negociación de ``market`` cotizados en ``quote``."""
        quote = quote.upper()
        if market == SPOT:
            data = self._get(SPOT, "/api/v3/exchangeInfo")
        else:
            data = self._get(FUTURES, "/fapi/v1/exchangeInfo")

        result = []
        for s in data["symbols"]:
            if s.get("status") != "TRADING" or s.get("quoteAsset") != quote:
                continue
            if s.get("baseAsset") in STABLECOINS:
                continue
            if market == SPOT:
                if not s.get("isSpotTradingAllowed", True):
                    continue
            else:
                # Solo perpetuos sobre criptomonedas (fuera trimestrales,
                # índices como BTCDOM y activos tradicionales).
                if s.get("contractType") != "PERPETUAL":
                    continue
                if s.get("underlyingType", "COIN") != "COIN":
                    continue
            result.append(Instrument(market, s["symbol"], s["baseAsset"], quote))
        return sorted(result, key=lambda i: i.symbol)

    def candles(self, instrument: Instrument, interval: str, limit: int = KLINES_LIMIT) -> list[Candle]:
        """Velas de ``interval`` (1d, 4h...), de la más antigua a la actual (que puede estar en curso)."""
        path = "/api/v3/klines" if instrument.market == SPOT else "/fapi/v1/klines"
        params = {"symbol": instrument.symbol, "interval": interval, "limit": limit}
        return [Candle.from_kline(k) for k in self._get(instrument.market, path, params)]

    def daily_candles(self, instrument: Instrument, limit: int = KLINES_LIMIT) -> list[Candle]:
        return self.candles(instrument, "1d", limit)
