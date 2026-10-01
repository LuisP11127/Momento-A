import pytest

from helpers import downtrend, make_candles
from momento_a.binance import FUTURES, SPOT, BinanceError, BinanceFatalError, Instrument
from momento_a.indicators import sma
from momento_a.scanner import Criteria, evaluate, scan

BTC = Instrument(SPOT, "BTCUSDT", "BTC", "USDT")


def test_sma():
    assert sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert sma([1, 2], 3) == [None, None]
    with pytest.raises(ValueError):
        sma([1, 2, 3], 0)


def test_bearish_candle_crossing_above_ma7_is_signal():
    candles, now = downtrend(last_close=85.0)  # MA7 = 592 / 7 = 84.57
    signal = evaluate(BTC, candles, Criteria(), now)

    assert signal is not None
    assert signal.ma7 == pytest.approx(592 / 7)
    assert signal.ma7 < signal.ma25 < signal.ma99
    assert signal.dist_ma7_pct == pytest.approx((85 - 592 / 7) / (592 / 7) * 100)
    assert signal.change_pct == pytest.approx((85 - 82) / 82 * 100)
    assert signal.gap_ma7_ma25_pct < 0
    assert signal.dist_ma99_pct < 0
    assert signal.days_above_ma7 == 1  # acaba de cruzar: las velas previas estaban debajo


def test_candle_just_below_ma7_within_tolerance():
    candles, now = downtrend(last_close=83.5)  # MA7 = 84.36 → -1.02 %
    assert evaluate(BTC, candles, Criteria(tolerance_pct=2.0), now) is not None
    assert evaluate(BTC, candles, Criteria(tolerance_pct=0.5), now) is None


def test_candle_far_below_ma7_is_rejected():
    candles, now = downtrend(last_close=81.0, last_open=80.0)  # verde, pero -3.4 % bajo la MA7
    assert evaluate(BTC, candles, Criteria(), now) is None


def test_red_candle_rejected_unless_allowed():
    candles, now = downtrend(last_close=85.0, last_open=86.0)
    assert evaluate(BTC, candles, Criteria(), now) is None
    assert evaluate(BTC, candles, Criteria(require_green=False), now) is not None


def test_max_above_limits_candles_that_ran_too_far():
    candles, now = downtrend(last_close=90.0)  # ~ +5.4 % sobre la MA7
    assert evaluate(BTC, candles, Criteria(), now) is not None
    assert evaluate(BTC, candles, Criteria(max_above_pct=3.0), now) is None


def test_uptrend_is_rejected():
    candles, now = make_candles([100.0 + i for i in range(120)])
    assert evaluate(BTC, candles, Criteria(require_ma99=False, require_green=False), now) is None


def test_ma99_requirement_for_very_bearish_trend():
    # Subida larga y corrección de 9 días: MA7 < MA25 pero MA25 > MA99.
    closes = [100.0 + i for i in range(110)] + [209.0 - 5 * k for k in range(1, 10)] + [180.0]
    candles, now = make_candles(closes)
    assert evaluate(BTC, candles, Criteria(), now) is None
    signal = evaluate(BTC, candles, Criteria(require_ma99=False), now)
    assert signal is not None and signal.ma25 > signal.ma99


def test_not_enough_history_for_ma99():
    candles, now = downtrend(last_close=85.0, days=98)
    assert evaluate(BTC, candles, Criteria(require_ma99=False), now) is None


def test_volume_filter_uses_last_closed_candle():
    candles, now = downtrend(last_close=85.0)
    assert evaluate(BTC, candles, Criteria(min_quote_volume=1_000_000), now) is not None
    assert evaluate(BTC, candles, Criteria(min_quote_volume=1_000_001), now) is None


def test_closed_candle_mode_ignores_candle_in_progress():
    # La vela en curso cruza la MA7, pero la última cerrada está lejos de ella.
    candles, now = downtrend(last_close=85.0)
    assert evaluate(BTC, candles, Criteria(), now) is not None
    assert evaluate(BTC, candles, Criteria(closed_candle=True), now) is None

    # Si ya cerró, la misma vela sí cuenta en modo vela cerrada.
    after_close = candles[-1].close_time + 1
    assert evaluate(BTC, candles, Criteria(closed_candle=True), after_close) is not None


def test_days_above_ma7_counts_consecutive_candles():
    closes = [200.0 - i for i in range(115)] + [89.0, 91.0, 93.0, 95.0]
    candles, now = make_candles(closes)
    signal = evaluate(BTC, candles, Criteria(), now)
    assert signal is not None
    assert signal.days_above_ma7 == 4


class FakeClient:
    def __init__(self, data, fail=None):
        self.data = data  # {Instrument: candles}
        self.fail = fail or {}  # {symbol: exception}

    def instruments(self, market, quote):
        return [i for i in self.data if i.market == market and i.quote == quote]

    def daily_candles(self, instrument):
        if instrument.symbol in self.fail:
            raise self.fail[instrument.symbol]
        return self.data[instrument]


def test_scan_collects_signals_and_errors():
    hit, _ = downtrend(last_close=85.0)
    miss, _ = make_candles([100.0 + i for i in range(120)])
    eth_fut = Instrument(FUTURES, "ETHUSDT", "ETH", "USDT")
    sol = Instrument(SPOT, "SOLUSDT", "SOL", "USDT")
    xrp = Instrument(SPOT, "XRPUSDT", "XRP", "USDT")
    client = FakeClient(
        {BTC: hit, eth_fut: hit, sol: miss, xrp: hit},
        fail={"XRPUSDT": BinanceError("timeout")},
    )
    result = scan(client, [SPOT, FUTURES], "USDT", Criteria(), workers=2)

    assert sorted((s.instrument.market, s.instrument.symbol) for s in result.signals) == [
        (FUTURES, "ETHUSDT"),
        (SPOT, "BTCUSDT"),
    ]
    assert len(result.instruments) == 4
    assert list(result.errors) == ["spot:XRPUSDT"]


def test_scan_aborts_on_fatal_error():
    hit, _ = downtrend(last_close=85.0)
    client = FakeClient({BTC: hit}, fail={"BTCUSDT": BinanceFatalError("HTTP 451")})
    with pytest.raises(BinanceFatalError):
        scan(client, [SPOT], "USDT", Criteria(), workers=1)
