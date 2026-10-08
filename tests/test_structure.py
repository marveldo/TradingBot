"""Tests for BOS / CHoCH — the StructureDetector in core/detectors.py.

THE INTERFACE these tests expect (you write it in core/detectors.py):

    from core.detectors import StructureDetector, Break

    structure = StructureDetector()
    new = structure.update(candle)  # feed ONE closed candle; returns the breaks THIS candle caused (usually [])
    structure.breaks                # every break so far, in order
    structure.trend                 # "bullish", "bearish", or None (not known yet)
    structure.bos_level             # price a BOS must go past next   (None = no level right now)
    structure.choch_level           # price a CHoCH must go past next (None = no level right now)

    Break has:
        kind        "BOS" or "CHoCH"
        direction   the trend AFTER the break ("bullish" or "bearish")
        level       the price that got broken
        time        time of the candle that broke it   (the "BOS candle")
    (Which candle is the OB is the OrderBlockDetector's job, not this one's.)

    StructureDetector uses your SwingDetector inside it: every candle goes to the SwingDetector first.

THE RULES (from PLAN.md → Definitions). A wick is always enough.

    Starting trend:  until the first swing is confirmed the trend is unknown and nothing can break.
                     The first swing sets the trend to its direction:
                       bullish swing → bos_level = its high, choch_level = its low
                       bearish swing → bos_level = its low,  choch_level = its high
                     (no break reported for this — it's just the starting point)

    In a BULLISH trend:
      bos_level   = the high of the FIRST bullish swing after the last break (ignore bearish swings while bullish).
                    Once set, it STAYS until price breaks it — a later swing with a lower high does NOT move it
                    (a lower high isn't a valid level to target).
      BOS         = a candle's HIGH goes above bos_level
                    → report BOS, choch_level = the SWING LOW the move started from
                      (= the lowest low between the broken swing high and the BOS candle, BOS candle included),
                      bos_level = None until the next bullish swing gives a new high to break
      CHoCH       = a candle's LOW goes below choch_level (the swing low — NOT the inducement)
                    → report CHoCH, trend = bearish, choch_level = the SWING HIGH the drop started from
                      (= the highest high since choch_level was set, CHoCH candle included),
                      bos_level = None until the next bearish swing gives a new low to break
    Levels only ever come from swing highs/lows — never from candle colours.

    In a BEARISH trend: exact mirror (BOS = low below the first bearish swing's low after the last break,
                        which stays until broken; CHoCH = high above choch_level).

    Order inside update(): first feed the SwingDetector and take in any new swing, THEN check for breaks.
    So the candle that confirms a swing can also break that swing's level straight away.
"""
from collections import namedtuple
from pathlib import Path

import pandas as pd
import pytest

from core.detectors import StructureDetector

Candle = namedtuple("Candle", "time open high low close")
START = pd.Timestamp("2026-01-05 00:00", tz="UTC")
DATA = Path(__file__).resolve().parent.parent / "data" / "deriv"


def candles(*ohlc):
    """Hourly candles from (open, high, low, close). Candle i opens at START + i hours."""
    return [Candle(START + pd.Timedelta(hours=i), o, h, l, c) for i, (o, h, l, c) in enumerate(ohlc)]


def hour(i):
    return START + pd.Timedelta(hours=i)


def run(cs):
    structure = StructureDetector()
    returned = [structure.update(c) for c in cs]
    return structure, returned


# A bullish market, candle by candle (hour = index):
FIRST_SWING = [
    (10, 12, 9, 11),            # 0 green, move starts
    (11, 14, 10, 13),           # 1 green, top 14
    (13, 13.5, 11.5, 12),       # 2 red pullback, low 11.5
    (12, 13.8, 11.9, 13.2),     # 3 wick 13.8 > 13.5 → bullish swing (high 14, low 11.5). Doesn't reach 14.
]
BOS_CANDLE = (13.2, 14.5, 13, 14.3)     # 4 wick 14.5 > 14 → BOS. The move started from the swing low 11.5.


# --- starting the trend ----------------------------------------------------------

def test_nothing_is_known_before_the_first_swing():
    # Two green candles, no swing yet → no trend, no levels, no breaks.
    structure, _ = run(candles(*FIRST_SWING[:2]))
    assert structure.trend is None
    assert structure.bos_level is None and structure.choch_level is None
    assert structure.breaks == []


def test_first_swing_sets_the_starting_trend_without_a_break():
    # The first swing is bullish → trend bullish, BOS level = its high (14), CHoCH level = its low (11.5).
    # Nothing is reported as a break — it's only the starting point.
    structure, returned = run(candles(*FIRST_SWING))
    assert structure.trend == "bullish"
    assert structure.bos_level == 14
    assert structure.choch_level == 11.5
    assert returned == [[], [], [], []]


# --- BOS ------------------------------------------------------------------------------

def test_bullish_bos_when_a_wick_goes_above_the_last_swing_high():
    # Candle 4 wicks to 14.5, above the swing high 14 → BOS.
    # The move up started from the swing low 11.5 (candle 2) → that's the CHoCH level, NOT candle 3's low (11.9).
    structure, returned = run(candles(*FIRST_SWING, BOS_CANDLE))
    assert len(returned[4]) == 1
    b = returned[4][0]
    assert (b.kind, b.direction, b.level) == ("BOS", "bullish", 14)
    assert b.time == hour(4)
    assert structure.trend == "bullish"
    assert structure.choch_level == 11.5


def test_a_lower_high_swing_does_not_move_the_bos_level():
    # BOS level is 14. Price makes a LOWER high (13.8), pulls back, continues → a new bullish swing with high 13.8.
    # That lower high is NOT a valid level: the BOS level stays 14.
    # So 13.9 is not a BOS; only a wick above 14 is.
    structure, returned = run(candles(
        *FIRST_SWING,                   # 0-3: swing (high 14, low 11.5) → bos_level 14; candle 3's high is 13.8
        (13.2, 13.6, 12.9, 13.5),       # 4 green, stays under 14
        (13.5, 13.55, 12.5, 12.6),      # 5 red pullback (high 13.55, low 12.5) — above the CHoCH level 11.5
        (12.6, 13.7, 12.55, 13.6),      # 6 wick 13.7 > 13.55 → new bullish swing with the LOWER high 13.8
        (13.6, 13.9, 13.5, 13.85),      # 7 wick 13.9: above 13.8 but below 14 → no BOS
        (13.85, 14.2, 13.8, 14.1),      # 8 wick 14.2 > 14 → BOS of the REAL high
    ))
    assert structure.bos_level is None          # used up by the BOS on candle 8
    assert returned[6] == [] and returned[7] == []
    assert len(returned[8]) == 1
    b = returned[8][0]
    assert (b.kind, b.direction, b.level) == ("BOS", "bullish", 14)
    assert structure.choch_level == 11.5        # lowest low since the 14 high was made


def test_no_second_bos_until_a_new_swing_high_exists():
    # After the BOS there's no new swing high yet, so going even higher (15) is NOT another BOS.
    structure, _ = run(candles(*FIRST_SWING, BOS_CANDLE, (14.3, 15, 14.1, 14.8)))
    assert [b.kind for b in structure.breaks] == ["BOS"]
    assert structure.bos_level is None


# --- CHoCH ----------------------------------------------------------------------------

def test_dipping_below_the_pullback_but_above_the_swing_low_is_not_a_choch():
    # After the BOS, price pulls back to 12.5, then dips to 12.0 — still above the swing low (11.5).
    # Taking out the pullback (inducement) is just the sweep. No CHoCH.
    structure, _ = run(candles(*FIRST_SWING, BOS_CANDLE, (14.3, 14.4, 12.5, 12.6), (12.6, 12.7, 12.0, 12.1)))
    assert [b.kind for b in structure.breaks] == ["BOS"]
    assert structure.trend == "bullish"


def test_dipping_exactly_to_the_swing_low_is_not_a_choch():
    # A wick to exactly 11.5 doesn't go BELOW the swing low → no CHoCH.
    structure, _ = run(candles(*FIRST_SWING, BOS_CANDLE, (14.3, 14.4, 12.5, 12.6), (12.6, 12.7, 11.5, 11.6)))
    assert [b.kind for b in structure.breaks] == ["BOS"]


def test_bullish_to_bearish_choch_when_price_breaks_the_swing_low():
    # Candle 6 wicks to 11.4, below the swing low 11.5 → CHoCH, trend now bearish.
    # The drop started from the top of the leg, candle 4's high 14.5 → that's the new CHoCH level.
    structure, returned = run(candles(*FIRST_SWING, BOS_CANDLE, (14.3, 14.4, 12.5, 12.6), (12.6, 12.7, 11.4, 11.6)))
    assert len(returned[6]) == 1
    b = returned[6][0]
    assert (b.kind, b.direction, b.level) == ("CHoCH", "bearish", 11.5)
    assert b.time == hour(6)
    assert structure.trend == "bearish"
    assert structure.choch_level == 14.5
    assert structure.bos_level is None


def test_after_a_choch_the_next_bos_needs_a_swing_in_the_new_direction():
    # After the bearish CHoCH: green pullback (7), then candle 8 drops to 11.0.
    # Candle 8 confirms a bearish swing (high 12.4, low 11.4) → bos_level = 11.4 —
    # and the same candle's wick (11.0) already goes below it → bearish BOS on candle 8.
    # The drop started from the swing high 12.4 (candle 7) → new CHoCH level.
    structure, returned = run(candles(
        *FIRST_SWING, BOS_CANDLE,
        (14.3, 14.4, 12.5, 12.6),       # 5
        (12.6, 12.7, 11.4, 11.6),       # 6 CHoCH
        (11.6, 12.4, 11.7, 12.2),       # 7 green pullback
        (12.2, 12.3, 11.0, 11.1),       # 8 confirms bearish swing AND breaks its low
    ))
    assert returned[7] == []
    assert len(returned[8]) == 1
    b = returned[8][0]
    assert (b.kind, b.direction, b.level) == ("BOS", "bearish", 11.4)
    assert structure.choch_level == 12.4       # the swing high (candle 7)
    assert [x.kind for x in structure.breaks] == ["BOS", "CHoCH", "BOS"]


# --- the bearish mirror -----------------------------------------------------------------

BEAR_FIRST_SWING = [
    (10, 10.5, 8, 8.5),         # 0 red, move starts
    (8.5, 9, 6, 6.5),           # 1 red, bottom 6
    (6.5, 8.5, 6.8, 8),         # 2 green pullback, high 8.5
    (8, 8.2, 6.2, 6.4),         # 3 wick 6.2 < 6.8 → bearish swing (high 8.5, low 6). Doesn't reach 6.
]


def test_bearish_mirror_start_bos_and_choch():
    structure, returned = run(candles(
        *BEAR_FIRST_SWING,
        (6.4, 6.5, 5.5, 5.6),       # 4 wick 5.5 < 6 → bearish BOS → choch_level = swing high 8.5 (not candle 3's 8.2)
        (5.6, 7.0, 5.6, 6.9),       # 5 green, 7.0 < 8.5 → nothing
        (6.9, 8.6, 6.8, 8.5),       # 6 wick 8.6 > 8.5 → CHoCH to bullish → choch_level = bottom of the leg 5.5
    ))
    assert structure.breaks[0].kind == "BOS" and structure.breaks[0].direction == "bearish"
    assert structure.breaks[0].level == 6
    assert returned[5] == []
    b = returned[6][0]
    assert (b.kind, b.direction, b.level) == ("CHoCH", "bullish", 8.5)
    assert structure.trend == "bullish"
    assert structure.choch_level == 5.5


# --- real data sanity ---------------------------------------------------------------------

@pytest.mark.skipif(not (DATA / "EURUSD_H4.parquet").exists(), reason="no fetched data")
def test_real_eurusd_h4_structure_makes_sense():
    # On 7 years of real H4:
    #   - plenty of both BOS and CHoCH
    #   - a BOS never changes the trend; a CHoCH always flips it
    #   - breaks come out in time order
    df = pd.read_parquet(DATA / "EURUSD_H4.parquet")
    structure = StructureDetector()
    trend_before = []
    for row in df.itertuples(index=False):
        before = structure.trend
        for _ in structure.update(row):
            trend_before.append(before)

    breaks = structure.breaks
    kinds = [b.kind for b in breaks]
    assert kinds.count("BOS") > 100 and kinds.count("CHoCH") > 100
    for b, before in zip(breaks, trend_before):
        if b.kind == "BOS":
            assert b.direction == before
        else:
            assert b.direction != before
    times = [b.time for b in breaks]
    assert times == sorted(times)
