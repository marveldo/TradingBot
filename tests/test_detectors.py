"""Tests for core/detectors.py — swing detection.

THE INTERFACE these tests expect (you write it in core/detectors.py):

    from core.detectors import SwingDetector, Swing

    detector = SwingDetector()
    new = detector.update(candle)   # feed ONE closed candle; returns a list of swings confirmed BY THIS candle
                                    # (usually [], rarely more than one)
    detector.swings                 # every swing confirmed so far, in order

    A candle is anything with .time .open .high .low .close  (e.g. a row from df.itertuples()).

    Swing has:
        direction       "bullish" or "bearish"
        high, high_time the top of the swing and the time of the candle that made it
        low, low_time   the bottom of the swing and the time of the candle that made it
        confirmed_time  the time of the candle that confirmed the swing

WHY A CLASS FED ONE CANDLE AT A TIME: that's exactly how feed() hands candles over, and how the live bot gets
them. The detector can never peek at future candles, so no lookahead.

THE RULES (from PLAN.md → Definitions). Green = close > open, red = close < open.

    Bullish swing = up move → pullback → continuation
      1. Up move:      one or more green candles.
      2. Pullback:     starts at the first red candle after the up move (ONE red candle is enough).
                       Every candle after that belongs to the pullback until step 3 happens.
      3. Confirmation: a candle whose HIGH (a wick is enough) goes above the highest high of the pullback candles.
      Result:          swing high = highest high from the start of the up move through the pullback
                       swing low  = lowest low of the pullback candles   (this is the inducement level)
      The confirming candle starts the next up move.

      A FAILED move: while in the pullback, if price goes BELOW where the move started, it wasn't a pullback —
      the move failed. Drop it (no swing) and go back to waiting for a new up move. If the failing candle is
      itself green, it starts that new move straight away.
        "Where the move started" = the low of the first green candle of the move,
                                   or, after a swing, that swing's low (the pullback low).

    Bearish swing = exact mirror: red move → pullback starting at the first green candle →
                    a candle whose LOW goes below the lowest low of the pullback.
      Result:       swing low  = lowest low from the start of the down move through the pullback
                    swing high = highest high of the pullback candles
      Failed move:  the pullback goes ABOVE where the down move started (first red candle's high,
                    or after a swing, that swing's high).

    Not decided yet (no tests, so do whatever is simplest): doji candles (close == open).
"""
from collections import namedtuple
from pathlib import Path

import pandas as pd
import pytest

from core.detectors import SwingDetector

Candle = namedtuple("Candle", "time open high low close")
START = pd.Timestamp("2026-01-05 00:00", tz="UTC")
DATA = Path(__file__).resolve().parent.parent / "data" / "deriv"


def candles(*ohlc):
    """Build hourly candles from (open, high, low, close) tuples. Candle i opens at START + i hours."""
    return [Candle(START + pd.Timedelta(hours=i), o, h, l, c) for i, (o, h, l, c) in enumerate(ohlc)]


def hour(i):
    return START + pd.Timedelta(hours=i)


def feed_all(cs):
    """Feed candles one by one. Returns the detector and, per candle, what update() returned."""
    detector = SwingDetector()
    returned = [detector.update(c) for c in cs]
    return detector, returned


# Reusable shapes ---------------------------------------------------------------
GREEN_1 = (10, 12, 9, 11)       # up move
GREEN_2 = (11, 14, 10, 13)      # up move, top of the move = 14
RED_PB = (13, 13.5, 11.5, 12)   # one-candle pullback: high 13.5, low 11.5
GREEN_BREAK = (12, 15, 11.8, 14.5)  # wick 15 goes above the pullback high 13.5 → confirms


# --- nothing happens without the full move → pullback → continuation ------------

def test_only_green_candles_make_no_swing():
    # An up move with no pullback is not a swing yet.
    detector, _ = feed_all(candles(GREEN_1, GREEN_2, (13, 16, 12.5, 15.5)))
    assert detector.swings == []


def test_pullback_alone_does_not_confirm():
    # Up move + red pullback, but nothing has gone past the pullback yet → still no swing.
    detector, _ = feed_all(candles(GREEN_1, GREEN_2, RED_PB))
    assert detector.swings == []


# --- the basic bullish swing --------------------------------------------------------

def test_bullish_swing_with_a_one_candle_pullback():
    # green, green, ONE red, then a green that goes past the red's high → one bullish swing.
    # Swing high = top of the move (14, made by candle 1). Swing low = the red candle's low (11.5, candle 2).
    detector, returned = feed_all(candles(GREEN_1, GREEN_2, RED_PB, GREEN_BREAK))
    assert len(detector.swings) == 1
    s = detector.swings[0]
    assert s.direction == "bullish"
    assert (s.high, s.high_time) == (14, hour(1))
    assert (s.low, s.low_time) == (11.5, hour(2))
    assert s.confirmed_time == hour(3)


def test_swing_is_reported_by_the_confirming_candle_and_not_before():
    # update() returns nothing for candles 0-2 and returns the swing exactly on candle 3.
    # This is the no-lookahead check: the swing doesn't exist until the candle that confirms it.
    _, returned = feed_all(candles(GREEN_1, GREEN_2, RED_PB, GREEN_BREAK))
    assert returned[:3] == [[], [], []]
    assert len(returned[3]) == 1 and returned[3][0].direction == "bullish"


def test_a_wick_is_enough_to_confirm():
    # The confirming candle CLOSES below the pullback high (13.2 < 13.5) but its WICK goes above (13.8).
    # Wick counts → swing confirmed.
    detector, _ = feed_all(candles(GREEN_1, GREEN_2, RED_PB, (12, 13.8, 11.9, 13.2)))
    assert len(detector.swings) == 1


def test_confirming_candle_can_be_any_colour():
    # A RED candle whose wick goes above the pullback high still confirms — only the wick matters.
    detector, _ = feed_all(candles(GREEN_1, GREEN_2, RED_PB, (13.4, 14.0, 12.0, 12.5)))
    assert len(detector.swings) == 1


def test_swing_is_reported_only_once():
    # After confirmation, more candles going higher must NOT report the same swing again.
    detector, _ = feed_all(candles(GREEN_1, GREEN_2, RED_PB, GREEN_BREAK, (14.5, 16, 14.2, 15.8), (15.8, 17, 15.5, 16.9)))
    assert len(detector.swings) == 1


# --- pullbacks longer than one candle ----------------------------------------------

def test_inside_candle_does_not_confirm():
    # After the red pullback, a green candle that stays BELOW the pullback high (13.2 < 13.5) doesn't confirm.
    # It just becomes part of the pullback. The next candle that goes above 13.5 confirms.
    detector, returned = feed_all(candles(GREEN_1, GREEN_2, RED_PB, (12, 13.2, 11.8, 13), GREEN_BREAK))
    assert returned[3] == []
    assert len(detector.swings) == 1
    assert detector.swings[0].confirmed_time == hour(4)


def test_multi_candle_pullback_uses_its_lowest_low_and_highest_high():
    # Pullback = two red candles. Lowest low of the pullback = 10.5 (second red) → that's the swing low.
    # Highest high of the pullback = 13.5 (first red) → a wick above 13.5 is needed to confirm.
    first_red, second_red = (13, 13.5, 11.5, 12), (12, 12.5, 10.5, 11)
    not_enough = (11, 13.4, 10.8, 13.2)       # 13.4 is above the second red's high but NOT above 13.5
    detector, returned = feed_all(candles(GREEN_1, GREEN_2, first_red, second_red, not_enough, GREEN_BREAK))
    assert returned[4] == []
    s = detector.swings[0]
    assert (s.low, s.low_time) == (10.5, hour(3))
    assert s.confirmed_time == hour(5)


def test_swing_high_includes_a_pullback_wick_above_the_move():
    # The red candle wicks ABOVE the green move (14.6 > 14) before closing down.
    # The swing high is the real top (14.6, from the red candle), and confirmation needs a wick above 14.6.
    red_with_top_wick = (13, 14.6, 11.5, 12)
    detector, returned = feed_all(candles(GREEN_1, GREEN_2, red_with_top_wick, (12, 14.5, 11.8, 14.4), GREEN_BREAK))
    assert returned[3] == []
    s = detector.swings[0]
    assert (s.high, s.high_time) == (14.6, hour(2))
    assert s.confirmed_time == hour(4)


# --- failed moves (pullback breaks where the move started) ------------------------------

def bullish(detector):
    return [s for s in detector.swings if s.direction == "bullish"]


def test_pullback_below_the_start_of_the_move_kills_it():
    # The move started at the first green candle's low (9). The "pullback" then drops to 8.5 — below 9.
    # That's not a pullback, the move failed → no swing, back to waiting.
    # A brand-new move then forms its own swing, built only from the new candles.
    detector, _ = feed_all(candles(
        GREEN_1,                        # move starts, start low = 9
        GREEN_2,                        # top 14
        RED_PB,                         # pullback
        (12, 12.2, 8.5, 9),             # 8.5 < 9 → move failed, nothing reported
        (9, 10, 8.8, 9.8),              # green → NEW move, start low 8.8, top 10
        (9.8, 9.9, 9.2, 9.3),           # red pullback: high 9.9, low 9.2
        (9.3, 10.5, 9.25, 10.4),        # 10.5 > 9.9 → swing from the NEW move
    ))
    swings = bullish(detector)
    assert len(swings) == 1
    s = swings[0]
    assert (s.high, s.high_time) == (10, hour(4))
    assert (s.low, s.low_time) == (9.2, hour(5))
    assert s.confirmed_time == hour(6)


def test_first_pullback_candle_can_kill_the_move_too():
    # The move started at 9. The very FIRST red candle of the pullback already wicks to 8.7 — below 9.
    # The move failed right there, so the next candle going above that red's high is NOT a swing.
    detector, _ = feed_all(candles(
        GREEN_1,                        # move starts, start low = 9
        GREEN_2,                        # top 14
        (13, 13.5, 8.7, 9),             # first pullback candle, 8.7 < 9 → move failed
        GREEN_BREAK,                    # 15 > 13.5, but there's no live move to confirm → just starts a new move
    ))
    assert bullish(detector) == []


def test_after_a_swing_the_next_move_starts_from_the_swing_low():
    # Swing 1 has low 11.5. The next move starts from 11.5.
    # Its pullback drops to 11.0 — below 11.5 → that move failed, so no second swing,
    # even though a later candle goes above the old pullback high.
    detector, _ = feed_all(candles(
        GREEN_1, GREEN_2, RED_PB, GREEN_BREAK,  # swing 1: low 11.5
        (14.5, 17, 14.3, 16.8),                 # next move, top 17
        (16.8, 16.9, 11.0, 11.2),               # pullback to 11.0 < 11.5 → failed
        (11.2, 11.4, 10.8, 11.0),               # red, still waiting
        (11.0, 17.5, 10.9, 17.2),               # green, goes above 16.9 — but that move is dead; this just starts a new one
    ))
    assert len(bullish(detector)) == 1


# --- the bearish mirror ---------------------------------------------------------------

def test_bearish_swing_is_the_mirror():
    # red, red, ONE green pullback, then a candle whose wick goes BELOW the green's low → bearish swing.
    # Swing low = bottom of the move (6, candle 1). Swing high = the green pullback's high (8.5, candle 2).
    detector, returned = feed_all(candles(
        (10, 10.5, 8, 8.5),     # red
        (8.5, 9, 6, 6.5),       # red, bottom of the move = 6
        (6.5, 8.5, 6.8, 8),     # ONE green pullback: low 6.8, high 8.5
        (8, 8.2, 5, 5.5),       # wick 5 goes below the pullback low 6.8 → confirms
    ))
    assert len(detector.swings) == 1
    s = detector.swings[0]
    assert s.direction == "bearish"
    assert (s.low, s.low_time) == (6, hour(1))
    assert (s.high, s.high_time) == (8.5, hour(2))
    assert s.confirmed_time == hour(3)
    assert returned[:3] == [[], [], []]


# --- a trend = a chain of swings -------------------------------------------------------

def test_uptrend_staircase_gives_two_bullish_swings():
    # Up, pullback, break, up, pullback, break → two bullish swings, each with a higher high and higher low.
    # The confirming candle of swing 1 starts the up move of swing 2.
    detector, _ = feed_all(candles(
        GREEN_1, GREEN_2, RED_PB, GREEN_BREAK,      # swing 1: high 14, low 11.5, confirmed at candle 3
        (14.5, 17, 14.3, 16.8),                     # up move continues, top = 17
        (16.8, 16.9, 15, 15.4),                     # one red pullback: high 16.9, low 15
        (15.4, 18, 15.2, 17.8),                     # wick 18 > 16.9 → swing 2 confirmed
    ))
    bullish = [s for s in detector.swings if s.direction == "bullish"]
    assert len(bullish) == 2
    first, second = bullish
    assert (first.high, first.low) == (14, 11.5)
    assert (second.high, second.low) == (17, 15)
    assert second.high > first.high and second.low > first.low


# --- real data sanity -------------------------------------------------------------------

@pytest.mark.skipif(not (DATA / "EURUSD_H4.parquet").exists(), reason="no fetched data")
def test_real_eurusd_h4_swings_make_sense():
    # On real H4 data, every swing must obey basic logic:
    #   - high is above low
    #   - both points come from candles BEFORE (or at) the confirming candle → no lookahead
    #   - swings come out in time order
    #   - there are LOTS of both kinds (a real market pulls back all the time, ~7 years of H4 here)
    #   - the detector never gets stuck: no 2-month stretch without a bullish (or bearish) swing
    df = pd.read_parquet(DATA / "EURUSD_H4.parquet")
    detector = SwingDetector()
    for row in df.itertuples(index=False):
        detector.update(row)

    swings = detector.swings
    for direction in ("bullish", "bearish"):
        times = pd.Series([s.confirmed_time for s in swings if s.direction == direction])
        assert len(times) > 500, f"only {len(times)} {direction} swings"
        assert times.diff().max() < pd.Timedelta(days=60), f"{direction} detector stuck for {times.diff().max()}"
    for s in swings:
        assert s.direction in ("bullish", "bearish")
        assert s.high > s.low
        assert s.high_time <= s.confirmed_time and s.low_time <= s.confirmed_time
    confirmed = [s.confirmed_time for s in swings]
    assert confirmed == sorted(confirmed)
