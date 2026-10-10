"""Tests for research/backtest.py — the trade simulation and the summary numbers."""
import pandas as pd
import pytest

from core.detectors import OrderBlock
from core.state_machine import Setup
from research.backtest import Trade, results_text, simulate, summarize

T = pd.Timestamp("2026-01-05 12:00", tz="UTC")


def setup(direction="bullish", entry=1.1025, sl=1.1010, tp=1.1070):
    ob = OrderBlock(direction, "BOS", 1.1020, 1.1000, T, T, "tapped")
    return Setup(T, direction, entry, sl, tp, "h1", ob, ob, sl)


def m15(*ohlc, spread=0):
    """M15 candles from T onwards: (open, high, low, close)."""
    times = [T + pd.Timedelta(minutes=15 * i) for i in range(len(ohlc))]
    return pd.DataFrame({"time": times, "open": [c[0] for c in ohlc], "high": [c[1] for c in ohlc],
                         "low": [c[2] for c in ohlc], "close": [c[3] for c in ohlc], "spread": spread})


def test_buy_fills_then_hits_tp():
    # candle 0 dips to the entry (1.1025), candle 1 reaches the TP (1.1070) → win, RR 3
    t = simulate(setup(), m15((1.1030, 1.1035, 1.1024, 1.1030), (1.1030, 1.1071, 1.1029, 1.1065)))
    assert (t.result, t.rr, t.r) == ("win", pytest.approx(3.0), pytest.approx(3.0))


def test_buy_fills_then_hits_sl():
    t = simulate(setup(), m15((1.1030, 1.1035, 1.1024, 1.1030), (1.1030, 1.1032, 1.1009, 1.1012)))
    assert (t.result, t.r) == ("loss", -1.0)


def test_candle_touching_sl_and_tp_counts_as_a_loss():
    t = simulate(setup(), m15((1.1030, 1.1035, 1.1024, 1.1030), (1.1030, 1.1080, 1.1005, 1.1040)))
    assert t.result == "loss"


def test_tp_before_fill_is_missed():
    t = simulate(setup(), m15((1.1030, 1.1075, 1.1028, 1.1070)))
    assert (t.result, t.r) == ("missed", 0.0)


def test_never_filled_expires():
    candles = m15(*[(1.1030, 1.1035, 1.1028, 1.1030)] * (48 * 4 + 1))   # 48h of candles that never reach 1.1025
    assert simulate(setup(), candles).result == "expired"


def test_sell_side():
    # sell: entry 1.1000, SL 1.1015, TP 1.0955 (RR 3). Candle 0 rallies to the entry, candle 1 drops to the TP.
    t = simulate(setup("bearish", 1.1000, 1.1015, 1.0955), m15((1.0990, 1.1001, 1.0985, 1.0995),
                                                               (1.0995, 1.0997, 1.0954, 1.0960)))
    assert (t.result, t.r) == ("win", pytest.approx(3.0))


def test_spread_is_charged_in_r():
    # spread 30 points x 0.00001 = 0.0003 = 0.2R (risk is 0.0015)
    t = simulate(setup(), m15((1.1030, 1.1035, 1.1024, 1.1030), (1.1030, 1.1032, 1.1009, 1.1012), spread=30),
                 point=0.00001)
    assert t.r == pytest.approx(-1.2)


def test_summary_numbers():
    trades = [Trade(None, "win", 3.0, 3.0), Trade(None, "loss", 2.0, -1.0), Trade(None, "loss", 4.0, -1.0),
              Trade(None, "missed", 3.0, 0.0)]
    s = summarize(trades)
    assert s["trades"] == 3                                  # missed trades don't count
    assert s["win_rate"] == pytest.approx(1 / 3)
    assert s["total_r"] == pytest.approx(1.0) and s["per_trade"] == pytest.approx(1 / 3)
    assert s["median_rr"] == 3.0
    assert s["max_drawdown"] == pytest.approx(-2.0)          # +3 → +2 → +1


def test_summary_with_no_trades():
    assert summarize([Trade(None, "missed", 3.0, 0.0)])["trades"] == 0


def test_results_text_shows_win_rate_total_and_years():
    a, b = setup(), setup()
    b.time = T + pd.Timedelta(days=400)
    text = results_text("EURUSD", [Trade(a, "win", 3.0, 3.0), Trade(b, "loss", 2.0, -1.0)])
    assert "===== EURUSD =====" in text
    assert "win rate 50.0%" in text and "total +2.0R" in text
    assert "2026:" in text and "2027:" in text
