"""Tests for order blocks + inducement — the OrderBlockDetector in core/detectors.py.

THE INTERFACE these tests expect (you write it in core/detectors.py):

    from core.detectors import OrderBlockDetector, OrderBlock

    detector = OrderBlockDetector()
    changed = detector.update(candle)   # feed ONE closed candle; returns the OBs that were CREATED or CHANGED STATUS
                                        # on this candle (usually [])
    detector.order_blocks               # every OB ever created, in order
    detector.structure                  # the StructureDetector it uses inside (which uses the SwingDetector)

    OrderBlock has:
        direction        "bullish" (a buy zone, below price) or "bearish" (a sell zone, above price)
        high, low        the OB zone = the OB candle's FULL range, wicks included
        time             time of the OB candle
        bos_time         time of the BOS candle that created it
        status           "pending" | "valid" | "swept" | "tapped" | "invalid"
        inducement       the inducement price (None until the OB is valid)
        inducement_time  time of the candle that made the inducement (None until valid)

THE RULES — your 7-step pattern from PLAN.md (bullish shown, bearish is the mirror). A wick always counts.

    1+2  The structure detector reports a BOS → the candle BEFORE the BOS candle is the OB.   status = "pending"
         (Only BOS creates OBs. A CHoCH does not — see ❓ in PLAN.md.)
    3    Pullback: the first RED candle AFTER the BOS candle starts the pullback (one candle is enough).
         Track the pullback's highest high and lowest low (same as a swing pullback).
    4    Continuation: a candle's HIGH goes above the pullback's highest high
         → status = "valid", inducement = the pullback's lowest low (+ its time).
    5    Sweep: a candle's LOW goes below the inducement                       → status = "swept"
    6    Tap: after (or on the same candle as) the sweep, a candle's LOW reaches the OB (low <= OB high)
                                                                               → status = "tapped"
         A tap WITHOUT a sweep first doesn't count (price must take the inducement on the way in).

    Invalid (any time, checked FIRST on every candle): a candle's LOW goes below the OB's LOW → "invalid".
         (That's the CHoCH level — the zone is broken.) Once invalid, an OB never changes again.
    Invalid (before it's valid): the pullback itself reaches the OB (pullback low <= OB high) → "invalid".
         (Price already came back to the OB before any inducement formed.)

    Bearish mirror: a BOS down → the OB is the candle before it; pullback starts at the first GREEN candle;
    continuation = a LOW below the pullback's lowest low; inducement = the pullback's HIGHEST high;
    sweep = a HIGH above the inducement; tap = a HIGH reaching the OB (high >= OB low);
    invalid = a HIGH above the OB's HIGH.

    Order inside update(): first move the EXISTING OBs forward with this candle, THEN feed the structure
    detector and create any new OB. (So the BOS candle itself never counts as part of its own OB's pullback.)
"""
from collections import namedtuple
from pathlib import Path

import pandas as pd
import pytest

from core.detectors import OrderBlockDetector

Candle = namedtuple("Candle", "time open high low close")
START = pd.Timestamp("2026-01-05 00:00", tz="UTC")
DATA = Path(__file__).resolve().parent.parent / "data" / "deriv"


def candles(*ohlc):
    """Hourly candles from (open, high, low, close). Candle i opens at START + i hours."""
    return [Candle(START + pd.Timedelta(hours=i), o, h, l, c) for i, (o, h, l, c) in enumerate(ohlc)]


def hour(i):
    return START + pd.Timedelta(hours=i)


def run(cs):
    """Feed candles one by one. Returns the detector, what each update() returned, and the statuses
    of those OBs AT THAT MOMENT (an OB object keeps changing later, so read its status right away)."""
    detector = OrderBlockDetector()
    returned, statuses = [], []
    for c in cs:
        changed = detector.update(c)
        returned.append(changed)
        statuses.append([ob.status for ob in changed])
    return detector, returned, statuses


# One full bullish setup, candle by candle (hour = index):
SETUP = [
    (10, 12, 9, 11),            # 0  green, move starts
    (11, 14, 10, 13),           # 1  green, top 14
    (13, 13.5, 11.5, 12),       # 2  red pullback
    (12, 13.8, 11.9, 13.2),     # 3  confirms a bullish swing (high 14) → structure: trend bullish, BOS level 14
    (13.2, 13.4, 12.6, 12.8),   # 4  red — this will be the OB (zone 12.6 – 13.4)
    (12.8, 15, 12.7, 14.9),     # 5  wick 15 > 14 → BOS. OB = candle 4.                        → "pending"
    (14.9, 16, 14.8, 15.9),     # 6  green, keeps going up (no pullback yet)
    (15.9, 15.95, 14.5, 14.7),  # 7  first red after the BOS → pullback, low 14.5 (above the OB high 13.4)
    (14.7, 15.98, 14.6, 15.9),  # 8  wick 15.98 > 15.95 → continuation.        → "valid", inducement 14.5
    (15.9, 15.95, 14.8, 14.9),  # 9  coming back down, 14.8 doesn't reach the inducement
    (14.9, 15.0, 14.2, 14.3),   # 10 wick 14.2 < 14.5 → inducement swept                               → "swept"
    (14.3, 14.4, 13.2, 13.4),   # 11 wick 13.2 <= 13.4 → reaches the OB                                → "tapped"
]
OB_BREAK = (13.4, 13.5, 12.4, 12.5)   # 12 wick 12.4 < 12.6 → OB low broken                           → "invalid"


# --- step 1+2: a BOS creates the OB --------------------------------------------------

def test_no_ob_before_a_bos():
    detector, _, _ = run(candles(*SETUP[:5]))
    assert detector.order_blocks == []


def test_bos_creates_a_pending_ob_from_the_candle_before_it():
    # Candle 5 breaks structure → OB = candle 4, its full range (12.6 – 13.4). Reported by candle 5's update().
    detector, returned, statuses = run(candles(*SETUP[:6]))
    assert len(detector.order_blocks) == 1
    ob = detector.order_blocks[0]
    assert ob.direction == "bullish"
    assert (ob.low, ob.high) == (12.6, 13.4)
    assert ob.time == hour(4) and ob.bos_time == hour(5)
    assert ob.status == "pending"
    assert ob.inducement is None
    assert returned[5] == [ob]


# --- steps 3+4: pullback + continuation make it valid -----------------------------------

def test_ob_stays_pending_without_a_pullback():
    # Only green candles after the BOS → no pullback → no inducement → still pending.
    detector, _, _ = run(candles(*SETUP[:7], (15.9, 16.5, 15.8, 16.4), (16.4, 17, 16.3, 16.9)))
    assert detector.order_blocks[0].status == "pending"


def test_pullback_then_continuation_makes_it_valid_with_the_inducement():
    # Candle 7 = pullback (low 14.5). Candle 8 goes above the pullback high → valid, inducement 14.5 (candle 7).
    detector, returned, statuses = run(candles(*SETUP[:9]))
    ob = detector.order_blocks[0]
    assert ob.status == "valid"
    assert (ob.inducement, ob.inducement_time) == (14.5, hour(7))
    assert returned[6] == [] and returned[7] == []
    assert statuses[8] == ["valid"]


# --- steps 5+6: sweep, then tap ----------------------------------------------------------

def test_inducement_sweep():
    # Candle 9 comes down but stays above 14.5 → nothing. Candle 10 wicks to 14.2 → swept.
    detector, returned, statuses = run(candles(*SETUP[:11]))
    assert returned[9] == []
    assert statuses[10] == ["swept"]
    assert detector.order_blocks[0].status == "swept"


def test_tap_after_the_sweep():
    detector, returned, statuses = run(candles(*SETUP))
    assert statuses[11] == ["tapped"]
    assert detector.order_blocks[0].status == "tapped"


def test_sweep_and_tap_on_the_same_candle():
    # One big red candle takes the inducement (14.5) AND reaches the OB (13.0 <= 13.4) → straight to tapped.
    detector, returned, statuses = run(candles(*SETUP[:10], (14.9, 15.0, 13.0, 13.3)))
    assert statuses[10] == ["tapped"]


# --- invalid ---------------------------------------------------------------------------------

def test_breaking_the_ob_low_after_the_tap_invalidates_it():
    detector, returned, statuses = run(candles(*SETUP, OB_BREAK))
    assert statuses[12] == ["invalid"]


def test_breaking_the_ob_low_before_it_is_valid_invalidates_it():
    # Right after the BOS, price drops straight through the OB low (12.5 < 12.6) → invalid.
    detector, _, _ = run(candles(*SETUP[:6], (14.9, 15, 12.5, 12.55)))
    assert detector.order_blocks[0].status == "invalid"


def test_pullback_reaching_the_ob_before_continuation_invalidates_it():
    # The first pullback after the BOS already comes back into the OB (13.3 <= 13.4) before any inducement formed.
    detector, _, _ = run(candles(*SETUP[:7], (15.9, 15.95, 13.3, 13.5)))
    assert detector.order_blocks[0].status == "invalid"


def test_invalid_is_final():
    # After it's invalid, nothing changes it again — not even price coming back up and down.
    detector, returned, statuses = run(candles(*SETUP, OB_BREAK, (12.5, 14, 12.45, 13.9), (13.9, 14, 13.0, 13.1)))
    assert detector.order_blocks[0].status == "invalid"
    assert all(ob is not detector.order_blocks[0] for ob in returned[13] + returned[14])


# --- bearish mirror -------------------------------------------------------------------------------

def mirror(ohlc):
    """Flip a candle upside down around 30: highs become lows, green becomes red."""
    o, h, l, c = ohlc
    return (30 - o, 30 - l, 30 - h, 30 - c)


def test_bearish_mirror_of_the_whole_setup():
    # The same setup upside down: BOS down → OB zone 16.6 – 17.4, inducement 15.5, swept, then tapped.
    detector, returned, statuses = run(candles(*[mirror(x) for x in SETUP]))
    ob = detector.order_blocks[0]
    assert ob.direction == "bearish"
    assert (ob.low, ob.high) == (pytest.approx(16.6), pytest.approx(17.4))
    assert ob.time == hour(4) and ob.bos_time == hour(5)
    assert ob.inducement == pytest.approx(15.5) and ob.inducement_time == hour(7)
    assert statuses[8] == ["valid"]
    assert statuses[10] == ["swept"]
    assert statuses[11] == ["tapped"]


# --- real data sanity ---------------------------------------------------------------------------------

@pytest.mark.skipif(not (DATA / "EURUSD_H4.parquet").exists(), reason="no fetched data")
def test_real_eurusd_h4_order_blocks_make_sense():
    # On 7 years of real H4:
    #   - lots of OBs, and plenty get all the way to "tapped" (= a setup). Counted from what update() reports,
    #     NOT from the final status: an OB that got tapped and later broke ends as "invalid", but it still was a setup.
    #   - every OB: low < high, OB candle before its BOS candle
    #   - every OB with an inducement: inducement formed after the BOS, on the right side of the OB
    #     (bullish: above the OB high, bearish: below the OB low)
    df = pd.read_parquet(DATA / "EURUSD_H4.parquet")
    detector = OrderBlockDetector()
    ever_tapped = set()
    for row in df.itertuples(index=False):
        for ob in detector.update(row):
            if ob.status == "tapped":
                ever_tapped.add(id(ob))

    obs = detector.order_blocks
    statuses = [ob.status for ob in obs]
    assert len(obs) > 100
    assert set(statuses) <= {"pending", "valid", "swept", "tapped", "invalid"}
    assert len(ever_tapped) > 100
    for ob in obs:
        assert ob.low < ob.high
        assert ob.time < ob.bos_time
        if ob.inducement is not None:
            assert ob.inducement_time > ob.bos_time
            if ob.direction == "bullish":
                assert ob.inducement > ob.high
            else:
                assert ob.inducement < ob.low
