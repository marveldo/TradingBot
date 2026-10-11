from types import SimpleNamespace

import pandas as pd
import pytest

from core.detectors import OrderBlock
from core.state_machine import Setup
from live.bot import LIMIT_HOURS, lot_size, only_new, order_prices, tp_reached

T = pd.Timestamp("2026-01-05 12:00", tz="UTC")
EURUSD = SimpleNamespace(trade_tick_size=0.00001, trade_tick_value=1.0, volume_step=0.01, volume_min=0.01,
                         volume_max=100.0)


def setup(direction="bullish", entry=1.10255, sl=1.10095, tp=1.11000):
    ob = OrderBlock(direction, "BOS", 1.1020, 1.1000, T, T, "tapped")
    return Setup(T, direction, entry, sl, tp, "h1", ob, ob, sl)


def test_lot_size_risks_the_right_money():
    assert lot_size(EURUSD, 1.10255, 1.10095, 100.0) == pytest.approx(0.62)


def test_lot_size_rounds_down_to_the_volume_step():
    assert lot_size(EURUSD, 1.10255, 1.10095, 99.0) == pytest.approx(0.61)


def test_lot_size_below_the_minimum_is_zero():
    assert lot_size(EURUSD, 1.10255, 1.10095, 1.0) == 0.0


def test_lot_size_is_capped_at_the_maximum():
    assert lot_size(EURUSD, 1.10255, 1.10254, 10_000_000.0) == 100.0


def test_buy_setup_prices_and_expiry():
    prices = order_prices(setup(), 5)
    assert prices == dict(buy=True, price=1.10255, sl=1.10095, tp=1.11,
                          expiration=int((T + pd.Timedelta(hours=LIMIT_HOURS)).timestamp()))


def test_sell_setup_is_not_a_buy():
    assert order_prices(setup("bearish", 1.1000, 1.1015, 1.0950), 5)["buy"] is False


def test_prices_are_rounded_to_the_symbols_digits():
    assert order_prices(setup(entry=1.102554999, sl=1.100951, tp=1.110004), 5)["price"] == 1.10255


def test_only_new_keeps_bars_after_the_last_one_seen():
    df = pd.DataFrame({"time": [T, T + pd.Timedelta(minutes=15), T + pd.Timedelta(minutes=30)]})
    assert list(only_new(df, T).time) == [T + pd.Timedelta(minutes=15), T + pd.Timedelta(minutes=30)]


def test_tp_reached_for_buy_and_sell():
    candle = SimpleNamespace(high=1.1100, low=1.0990)
    assert tp_reached(setup(), candle)
    assert tp_reached(setup("bearish", 1.1000, 1.1015, 1.0990), candle)
    assert not tp_reached(setup(tp=1.1101), candle)
