from types import SimpleNamespace

import pandas as pd
import pytest

from core.detectors import OrderBlock
from core.state_machine import Setup
from live import bot
from live.bot import LIMIT_HOURS, bars_since, existing_ticket, lot_size, only_new, order_prices, still_open, tp_reached

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


def m15_after_setup(*lows_highs, start=T):
    times = [start + pd.Timedelta(minutes=15 * i) for i in range(len(lows_highs))]
    return pd.DataFrame({"time": times, "low": [l for l, h in lows_highs], "high": [h for l, h in lows_highs]})


def test_warm_up_setup_still_open_when_price_stayed_away():
    assert still_open(setup(), m15_after_setup((1.1030, 1.1050), (1.1028, 1.1060)))


def test_warm_up_setup_already_filled_is_not_placed():
    assert not still_open(setup(), m15_after_setup((1.1030, 1.1050), (1.1025, 1.1040)))


def test_warm_up_setup_whose_tp_was_reached_is_not_placed():
    assert not still_open(setup(), m15_after_setup((1.1030, 1.1050), (1.1040, 1.1100)))


def test_warm_up_setup_older_than_the_limit_is_not_placed():
    old = setup()
    old.time = T - pd.Timedelta(hours=LIMIT_HOURS)
    assert not still_open(old, m15_after_setup((1.1030, 1.1050)))


def test_sell_warm_up_setup_still_open():
    assert still_open(setup("bearish", 1.1000, 1.1015, 1.0950), m15_after_setup((1.0960, 1.0990)))
    assert not still_open(setup("bearish", 1.1000, 1.1015, 1.0950), m15_after_setup((1.0960, 1.1001)))


def order(ticket, price, sl, tp):
    return SimpleNamespace(ticket=ticket, price_open=price, sl=sl, tp=tp)


def test_existing_order_for_the_same_setup_is_adopted():
    orders = [order(1, 1.1000, 1.0950, 1.1100), order(2, 1.10255, 1.10095, 1.11)]
    assert existing_ticket(orders, setup(), 5) == 2


def test_no_existing_order_for_a_different_setup():
    assert existing_ticket([order(1, 1.10255, 1.10095, 1.12)], setup(), 5) is None


def m15_bars(count):
    return pd.DataFrame({"time": [T + pd.Timedelta(minutes=15 * i) for i in range(count)]})


def test_bars_since_fetches_more_after_a_gap(monkeypatch):
    history = m15_bars(100)
    monkeypatch.setattr(bot, "closed_bars", lambda client, symbol, tf, count: history.tail(count))
    missed = bars_since(None, "EURUSD", "M15", history.time.iloc[59])
    assert list(missed.time) == list(history.time.iloc[60:])


def test_bars_since_with_no_gap_returns_only_the_new_bars(monkeypatch):
    history = m15_bars(100)
    monkeypatch.setattr(bot, "closed_bars", lambda client, symbol, tf, count: history.tail(count))
    assert list(bars_since(None, "EURUSD", "M15", history.time.iloc[97]).time) == list(history.time.iloc[98:])


def test_a_failed_check_does_not_skip_bars(monkeypatch):
    last_seen = {tf: T for tf in bot.WARMUP_BARS}
    new = {tf: m15_bars(3) for tf in bot.WARMUP_BARS}
    monkeypatch.setattr(bot, "new_bars", lambda client, symbol, seen: new)

    def disconnected(*args):
        raise ConnectionError("terminal gone")

    monkeypatch.setattr(bot, "cancel_missed_orders", disconnected)
    with pytest.raises(ConnectionError):
        bot.check(None, "EURUSD", None, last_seen, {}, False, 0.5)
    assert last_seen == {tf: T for tf in bot.WARMUP_BARS}


def test_check_safely_logs_and_reconnects_instead_of_crashing(monkeypatch):
    reconnected = []

    def disconnected(*args):
        raise ConnectionError("terminal gone")

    monkeypatch.setattr(bot, "check", disconnected)
    monkeypatch.setattr(bot, "reconnect", lambda client, symbol: reconnected.append(symbol))
    bot.check_safely(None, "EURUSD", None, {}, {}, False, 0.5)
    assert reconnected == ["EURUSD"]


def test_history_retries_until_mt5_has_downloaded_it(monkeypatch):
    answers = [Exception("Terminal: Call failed"), Exception("Terminal: Call failed"), "bars"]

    def flaky(client, symbol, timeframe, count):
        answer = answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer

    monkeypatch.setattr(bot, "closed_bars", flaky)
    monkeypatch.setattr(bot, "RETRY_SECONDS", 0)
    assert bot.history(None, "BTCUSD", "W1", 400) == "bars"


def test_history_gives_up_after_the_last_attempt(monkeypatch):
    def broken(client, symbol, timeframe, count):
        raise Exception("Terminal: Call failed")

    monkeypatch.setattr(bot, "closed_bars", broken)
    monkeypatch.setattr(bot, "RETRY_SECONDS", 0)
    with pytest.raises(Exception, match="Call failed"):
        bot.history(None, "BTCUSD", "W1", 400, attempts=3)


def test_only_m15_is_fetched_while_nothing_new_has_closed(monkeypatch):
    asked = []

    def nothing_new(client, symbol, timeframe, last_time):
        asked.append(timeframe)
        return m15_bars(0)

    monkeypatch.setattr(bot, "bars_since", nothing_new)
    bars = bot.new_bars(None, "EURUSD", {tf: T for tf in bot.WARMUP_BARS})
    assert asked == ["M15"] and len(bars["M15"]) == 0


def test_every_timeframe_is_fetched_once_a_new_m15_bar_closes(monkeypatch):
    asked = []

    def one_new(client, symbol, timeframe, last_time):
        asked.append(timeframe)
        return m15_bars(1)

    monkeypatch.setattr(bot, "bars_since", one_new)
    bars = bot.new_bars(None, "EURUSD", {tf: T for tf in bot.WARMUP_BARS})
    assert sorted(asked) == sorted(bot.WARMUP_BARS) and set(bars) == set(bot.WARMUP_BARS)
