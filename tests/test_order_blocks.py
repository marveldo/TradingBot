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
        kind             "BOS" or "CHoCH" — the break that created it, or "breaker" / "mitigation" for a flipped OB
        high, low        the OB zone = BODY to WICK of the OB candle (the wick on the far side is left out):
                           bullish: high = top of the body max(open, close), low = the candle's low
                           bearish: low = bottom of the body min(open, close), high = the candle's high
        time             time of the OB candle
        bos_time         time of the break candle (BOS or CHoCH) that created it
        status           "pending" | "valid" | "swept" | "tapped" | "spent" | "invalid"
        inducement       the inducement price (None until the OB is valid)
        inducement_time  time of the candle that made the inducement (None until valid)

THE RULES — your 7-step pattern from PLAN.md (bullish shown, bearish is the mirror). A wick always counts.

    1+2  The structure detector reports a BOS or a CHoCH → the OB is the LAST OPPOSITE-COLOUR candle before the
         break candle: bullish break → last RED candle, bearish break → last GREEN candle (the candle the move
         started from). Its direction = the break's direction (a bearish CHoCH makes a bearish OB). status = "pending"
         (No opposite-colour candle seen yet → no OB.)
         A CHoCH OB then goes through exactly the same steps 3-6 (pullback → inducement → sweep → tap).
    3    Pullback: the first RED candle AFTER the BOS candle starts the pullback (one candle is enough).
         Track the pullback's highest high and lowest low (same as a swing pullback).
    4    Continuation: a candle's HIGH goes above the pullback's highest high
         → status = "valid", inducement = the pullback's lowest low (+ its time).
    5    Sweep: a candle's LOW goes below the inducement                       → status = "swept"
    6    Tap: after (or on the same candle as) the sweep, a candle's LOW reaches the OB (low <= OB high)
                                                                               → status = "tapped"
         A tap WITHOUT a sweep first doesn't count (price must take the inducement on the way in).

    7    Spent: after the tap, a candle's HIGH goes back above the inducement → status = "spent". The reaction
         happened, so the zone is used up (single-use) and is no longer a POI. A spent zone can still break → invalid.

    Invalid (any time, checked FIRST on every candle): a candle's LOW goes below the OB's LOW → "invalid".
         The zone is broken. Once invalid, an OB never changes again.
         (This is NOT the same as a CHoCH: the CHoCH level is the swing low, which can be lower than the OB.)
    Invalid (before it's valid): the pullback itself reaches the OB (pullback low <= OB high) → "invalid".
         (Price already came back to the OB before any inducement formed.)

    Bearish mirror: a BOS/CHoCH down → the OB is the candle before it; pullback starts at the first GREEN candle;
    continuation = a LOW below the pullback's lowest low; inducement = the pullback's HIGHEST high;
    sweep = a HIGH above the inducement; tap = a HIGH reaching the OB (high >= OB low);
    invalid = a HIGH above the OB's HIGH.

    Breaker / mitigation blocks (flipped OBs):
         A BOS/CHoCH OB that gets BROKEN (price through its far side — NOT the pullback-before-inducement kind of
         invalid), followed by a CHoCH the other way on this TF before any other break → it flips:
         a NEW OB from the same candle, opposite direction, zone rebuilt body-to-wick for the new direction
         (bearish breaker = body bottom → wick high), kind = "breaker" if price went past the OB's break candle high
         (bullish OB; bearish: low) before breaking it — it swept a high first — else "mitigation".
         It starts "pending" on the CHoCH candle and needs its own pullback → inducement → sweep → tap, like any OB.
         A BOS (or another CHoCH) before the flip clears the list of broken OBs. Flipped blocks never flip again.

    Order inside update(): first move the EXISTING OBs forward with this candle, THEN feed the structure
    detector and create any new OB (the break's OB first, then any flipped blocks).
    (So the BOS candle itself never counts as part of its own OB's pullback.)
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
    (13.2, 13.4, 12.6, 12.8),   # 4  red — this will be the OB. Zone = body top 13.2 → wick low 12.6
    (12.8, 15, 12.7, 14.9),     # 5  wick 15 > 14 → BOS. OB = candle 4.                        → "pending"
    (14.9, 16, 14.8, 15.9),     # 6  green, keeps going up (no pullback yet)
    (15.9, 15.95, 14.5, 14.7),  # 7  first red after the BOS → pullback, low 14.5 (above the OB high 13.4)
    (14.7, 15.98, 14.6, 15.9),  # 8  wick 15.98 > 15.95 → continuation.        → "valid", inducement 14.5
    (15.9, 15.95, 14.8, 14.9),  # 9  coming back down, 14.8 doesn't reach the inducement
    (14.9, 15.0, 14.2, 14.3),   # 10 wick 14.2 < 14.5 → inducement swept                               → "swept"
    (14.3, 14.4, 13.1, 13.4),   # 11 wick 13.1 <= 13.2 → reaches the OB body                           → "tapped"
]
OB_BREAK = (13.4, 13.5, 12.4, 12.5)   # 12 wick 12.4 < 12.6 → OB low broken → "invalid".
                                      #    Still above the swing low 11.5 (candle 2) → NOT a CHoCH
CHOCH = (13.4, 13.5, 11.4, 11.5)      # 12 wick 11.4 < 11.5 → OB invalid AND a bearish CHoCH
                                      #    → new bearish OB = candle 8 (last green), zone 14.7 (body bottom) – 15.98 (wick)


# --- step 1+2: a BOS creates the OB --------------------------------------------------

def test_no_ob_before_a_bos():
    detector, _, _ = run(candles(*SETUP[:5]))
    assert detector.order_blocks == []


def test_bos_creates_a_pending_ob_from_the_last_red_candle_before_it():
    # Candle 5 breaks structure → OB = candle 4, body top to wick low (12.6 – 13.2). Reported by candle 5's update().
    detector, returned, statuses = run(candles(*SETUP[:6]))
    assert len(detector.order_blocks) == 1
    ob = detector.order_blocks[0]
    assert (ob.direction, ob.kind) == ("bullish", "BOS")
    assert (ob.low, ob.high) == (12.6, 13.2)
    assert ob.time == hour(4) and ob.bos_time == hour(5)
    assert ob.status == "pending"
    assert ob.inducement is None
    assert returned[5] == [ob]


def test_ob_is_the_last_red_candle_even_with_green_candles_in_between():
    # Candle 4 made green: 2 (red) → 3, 4 (green) → 5 BOS. The OB is candle 2, the last red before the move:
    # body top 13.0 → wick low 11.5.
    green_4 = (12.8, 13.4, 12.6, 13.2)
    detector, returned, _ = run(candles(*SETUP[:4], green_4, SETUP[5]))
    ob = detector.order_blocks[0]
    assert ob.time == hour(2) and ob.bos_time == hour(5)
    assert (ob.low, ob.high) == (11.5, 13.0)
    assert returned[5] == [ob]


def test_bearish_ob_is_the_last_green_candle():
    green_4 = (12.8, 13.4, 12.6, 13.2)
    detector, _, _ = run(candles(*[mirror(x) for x in [*SETUP[:4], green_4, SETUP[5]]]))
    ob = detector.order_blocks[0]
    assert ob.direction == "bearish" and ob.time == hour(2)
    assert (ob.low, ob.high) == (pytest.approx(17.0), pytest.approx(18.5))


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


def test_wick_into_the_ob_candles_upper_wick_is_not_a_tap():
    # After the sweep, a wick to 13.3: inside candle 4's upper wick (13.2 – 13.4) but not its body → still swept.
    detector, returned, statuses = run(candles(*SETUP[:11], (14.3, 14.4, 13.3, 13.5)))
    assert returned[11] == []
    assert detector.order_blocks[0].status == "swept"


def test_sweep_and_tap_on_the_same_candle():
    # One big red candle takes the inducement (14.5) AND reaches the OB (13.0 <= 13.2) → straight to tapped.
    detector, returned, statuses = run(candles(*SETUP[:10], (14.9, 15.0, 13.0, 13.3)))
    assert statuses[10] == ["tapped"]


# --- switches ---------------------------------------------------------------------------------------------

def test_flip_blocks_off_means_no_breakers_or_mitigation():
    detector = OrderBlockDetector(flip_blocks=False)
    for c in candles(*SETUP, CHOCH):
        detector.update(c)
    assert [ob.kind for ob in detector.order_blocks] == ["BOS", "CHoCH"]


def test_single_use_off_means_a_tapped_zone_stays_tapped():
    detector = OrderBlockDetector(single_use=False)
    for c in candles(*SETUP, (14.3, 14.6, 14.2, 14.55)):
        detector.update(c)
    assert detector.order_blocks[0].status == "tapped"


# --- swing target: the liquidity left behind ---------------------------------------------------------

def test_swing_target_is_the_high_price_ran_to_before_sweeping_the_inducement():
    # Valid on candle 8 (high 15.98). Candle 9 high 15.95, candle 10 sweeps (high 15.0) → target stays 15.98.
    detector, _, _ = run(candles(*SETUP[:11]))
    assert detector.order_blocks[0].swing_target == 15.98


def test_swing_target_follows_a_higher_high_before_the_sweep():
    # A candle after the continuation goes higher (16.5) before price comes back → that is the target.
    detector, _, _ = run(candles(*SETUP[:9], (15.9, 16.5, 15.0, 15.2), SETUP[10]))
    assert detector.order_blocks[0].swing_target == 16.5


def test_swing_target_is_frozen_once_the_inducement_is_swept():
    detector, _, _ = run(candles(*SETUP, (13.4, 14.4, 13.3, 14.3)))
    assert detector.order_blocks[0].swing_target == 15.98


def test_bearish_swing_target_is_the_low_price_rallied_from():
    detector, _, _ = run(candles(*[mirror(x) for x in SETUP[:11]]))
    assert detector.order_blocks[0].swing_target == pytest.approx(30 - 15.98)


# --- spent: single-use zones ---------------------------------------------------------------------------

def test_tapped_ob_is_spent_once_price_leaves_back_above_the_inducement():
    # Tapped on candle 11. Candle 12 bounces to 14.4 (still under the inducement 14.5) → still tapped.
    # Candle 13 goes to 14.6 > 14.5 → spent.
    detector, returned, statuses = run(candles(*SETUP, (13.4, 14.4, 13.3, 14.3), (14.3, 14.6, 14.2, 14.55)))
    ob = detector.order_blocks[0]
    assert returned[12] == []
    assert statuses[13] == ["spent"]
    assert ob.status == "spent"


def test_wick_exactly_to_the_inducement_does_not_spend_it():
    detector, _, _ = run(candles(*SETUP, (13.4, 14.5, 13.3, 14.3)))
    assert detector.order_blocks[0].status == "tapped"


def test_spent_ob_can_still_break_and_become_invalid():
    detector, _, statuses = run(candles(*SETUP, (14.3, 14.6, 14.2, 14.55), (14.55, 14.6, 12.4, 12.5)))
    assert statuses[12] == ["spent"]
    assert detector.order_blocks[0].status == "invalid" and detector.order_blocks[0].broken


def test_bearish_ob_is_spent_once_price_leaves_back_below_the_inducement():
    detector, _, statuses = run(candles(*[mirror(x) for x in (*SETUP, (14.3, 14.6, 14.2, 14.55))]))
    assert statuses[12] == ["spent"]


# --- invalid ---------------------------------------------------------------------------------

def test_breaking_the_ob_low_after_the_tap_invalidates_it():
    # 12.4 breaks the OB low (12.6) but not the swing low (11.5) → OB invalid, no CHoCH, no new OB.
    detector, returned, statuses = run(candles(*SETUP, OB_BREAK))
    assert statuses[12] == ["invalid"]
    assert len(detector.order_blocks) == 1


def test_a_choch_candle_invalidates_the_old_ob_then_creates_a_new_one():
    # Candle 12 kills the old OB first, THEN its CHoCH creates a new OB, THEN the old OB flips into a breaker.
    detector, returned, statuses = run(candles(*SETUP, CHOCH))
    assert returned[12][0] is detector.order_blocks[0]
    assert statuses[12] == ["invalid", "pending", "pending"]
    assert [ob.kind for ob in returned[12]] == ["BOS", "CHoCH", "breaker"]


def test_breaking_the_ob_low_before_it_is_valid_invalidates_it():
    # Right after the BOS, price drops straight through the OB low (12.5 < 12.6) → invalid.
    detector, _, _ = run(candles(*SETUP[:6], (14.9, 15, 12.5, 12.55)))
    assert detector.order_blocks[0].status == "invalid"


def test_pullback_reaching_the_ob_before_continuation_invalidates_it():
    # The first pullback after the BOS already comes back into the OB body (13.1 <= 13.2) before any inducement formed.
    detector, _, _ = run(candles(*SETUP[:7], (15.9, 15.95, 13.1, 13.5)))
    assert detector.order_blocks[0].status == "invalid"


def test_invalid_is_final():
    # After it's invalid, nothing changes it again — not even price coming back up and down.
    detector, returned, statuses = run(candles(*SETUP, OB_BREAK, (12.5, 14, 12.45, 13.9), (13.9, 14, 13.0, 13.1)))
    assert detector.order_blocks[0].status == "invalid"
    assert all(ob is not detector.order_blocks[0] for ob in returned[13] + returned[14])


# --- CHoCH creates an OB too ----------------------------------------------------------------------

def test_choch_creates_a_pending_ob_from_the_last_green_candle():
    # Candle 12 breaks the swing low 11.5 → CHoCH, trend bearish. Candles 9-11 are red, so the OB = candle 8
    # (green, 14.7 → 15.9, high 15.98): bearish zone = body bottom 14.7 → wick high 15.98.
    detector, returned, _ = run(candles(*SETUP, CHOCH))
    assert len(detector.order_blocks) == 3          # old OB, the CHoCH OB, the breaker
    ob = detector.order_blocks[1]
    assert (ob.direction, ob.kind) == ("bearish", "CHoCH")
    assert (ob.low, ob.high) == (14.7, 15.98)
    assert ob.time == hour(8) and ob.bos_time == hour(12)
    assert ob.status == "pending" and ob.inducement is None
    assert returned[12][1] is ob


def test_choch_ob_gets_its_inducement_like_any_other_ob():
    # 13: first green after the CHoCH → pullback, high 12.2 (below the OB low 14.7, so the OB survives)
    # 14: low 11.2 < pullback low 11.45 → continuation → valid, inducement 12.2 (candle 13)
    detector, returned, _ = run(candles(*SETUP, CHOCH, (11.5, 12.2, 11.45, 12.1), (12.1, 12.15, 11.2, 11.3)))
    ob = detector.order_blocks[1]
    assert ob.status == "valid"
    assert (ob.inducement, ob.inducement_time) == (12.2, hour(13))
    assert ob in returned[14]


# --- breaker / mitigation blocks -----------------------------------------------------------------------

def test_broken_ob_flips_into_a_bearish_breaker_on_the_choch():
    # OB = candle 4 (13.2 → 12.8, wicks 13.4 / 12.6). After its BOS (candle 5, high 15) price ran to 16 → swept that
    # high first. Candle 12 breaks the OB AND makes a bearish CHoCH → bearish BREAKER, pending, no inducement yet.
    # Zone rebuilt for a SELL zone: body bottom 12.8 → wick high 13.4.
    detector, returned, _ = run(candles(*SETUP, CHOCH))
    breaker = detector.order_blocks[2]
    assert (breaker.direction, breaker.kind) == ("bearish", "breaker")
    assert (breaker.low, breaker.high) == (12.8, 13.4)
    assert breaker.time == hour(4) and breaker.bos_time == hour(12)
    assert breaker.status == "pending" and breaker.inducement is None


def test_breaker_needs_inducement_sweep_then_tap():
    # 13 green pullback (high 12.2, below the zone) → 14 continuation down → valid, inducement 12.2
    # 15 wick 12.3 > 12.2 → swept (12.3 doesn't reach the zone low 12.8)
    # 16 wick 12.9 >= 12.8 → tapped (a sell zone now)
    detector, returned, _ = run(candles(*SETUP, CHOCH,
                                        (11.5, 12.2, 11.45, 12.1), (12.1, 12.15, 11.2, 11.3),
                                        (11.3, 12.3, 11.25, 12.25), (12.25, 12.9, 12.2, 12.4)))
    breaker = detector.order_blocks[2]
    assert (breaker.inducement, breaker.inducement_time) == (12.2, hour(13))
    assert breaker in returned[14] and breaker in returned[15] and breaker in returned[16]
    assert breaker.status == "tapped"


def test_wick_into_the_breakers_new_far_side_wick_does_not_break_it():
    # 13.3 is inside the old candle's upper wick (13.2-13.4) — part of the sell zone now, so the breaker survives
    # (the pullback-before-inducement rule kills it instead, but it is NOT "broken").
    detector, _, _ = run(candles(*SETUP, CHOCH, (11.5, 13.3, 11.45, 12.6)))
    assert not detector.order_blocks[2].broken


def test_breaker_retest_without_inducement_is_invalid():
    # Straight after the flip, the first pullback runs back into the zone (12.9 >= 12.8) → no inducement → invalid.
    detector, _, _ = run(candles(*SETUP, CHOCH, (11.5, 12.9, 11.45, 12.6)))
    assert detector.order_blocks[2].status == "invalid"


def test_ob_broken_without_sweeping_a_high_first_is_a_mitigation_block():
    # BOS on candle 5 (high 15), then candle 6 never goes above 15 and drops through the OB (11.4) and the
    # swing low 11.5 → CHoCH → MITIGATION block.
    detector, _, _ = run(candles(*SETUP[:6], (14.9, 14.95, 11.4, 11.5)))
    flipped = [ob for ob in detector.order_blocks if ob.kind in ("breaker", "mitigation")]
    assert [(ob.kind, ob.direction, ob.low, ob.high) for ob in flipped] == [("mitigation", "bearish", 12.8, 13.4)]


def test_broken_ob_without_a_choch_does_not_flip():
    detector, _, _ = run(candles(*SETUP, OB_BREAK))
    assert detector.order_blocks[0].broken
    assert not any(ob.kind in ("breaker", "mitigation") for ob in detector.order_blocks)


def test_ob_killed_by_its_pullback_is_not_broken_and_never_flips():
    detector, _, _ = run(candles(*SETUP[:7], (15.9, 15.95, 13.1, 13.5)))
    assert detector.order_blocks[0].status == "invalid" and not detector.order_blocks[0].broken


def test_obs_can_be_pickled_even_when_fed_pandas_rows():
    # The backtest caches setups with pickle; pandas' itertuples rows can't be pickled, so OBs must not hold them.
    import pickle
    df = pd.DataFrame(candles(*SETUP, CHOCH))
    detector = OrderBlockDetector()
    for row in df.itertuples(index=False):
        detector.update(row)
    restored = pickle.loads(pickle.dumps(detector.order_blocks))
    assert [(ob.kind, ob.low, ob.high) for ob in restored] == [(ob.kind, ob.low, ob.high) for ob in detector.order_blocks]


def test_bullish_breaker_mirror():
    detector, _, _ = run(candles(*[mirror(x) for x in (*SETUP, CHOCH)]))
    flipped = [ob for ob in detector.order_blocks if ob.kind == "breaker"]
    assert len(flipped) == 1 and flipped[0].direction == "bullish"
    # mirrored candle 4 = green 16.8 → 17.2 (wicks 17.4 / 16.6) → BUY zone: wick low 16.6 → body top 17.2
    assert (flipped[0].low, flipped[0].high) == (pytest.approx(16.6), pytest.approx(17.2))


# --- bearish mirror -------------------------------------------------------------------------------

def mirror(ohlc):
    """Flip a candle upside down around 30: highs become lows, green becomes red."""
    o, h, l, c = ohlc
    return (30 - o, 30 - l, 30 - h, 30 - c)


def test_bearish_mirror_of_the_whole_setup():
    # The same setup upside down: BOS down → OB = green candle 4 (16.8 → 17.2, high 17.4),
    # zone = body bottom 16.8 → wick high 17.4. Inducement 15.5, swept, then tapped.
    detector, returned, statuses = run(candles(*[mirror(x) for x in SETUP]))
    ob = detector.order_blocks[0]
    assert ob.direction == "bearish"
    assert (ob.low, ob.high) == (pytest.approx(16.8), pytest.approx(17.4))
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
    #   - both BOS and CHoCH make OBs
    #   - every OB: low <= high (a doji with no wick on the far side can be a single price), OB candle before its break
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
    assert set(statuses) <= {"pending", "valid", "swept", "tapped", "spent", "invalid"}
    assert len(ever_tapped) > 100
    assert {"BOS", "CHoCH", "breaker", "mitigation"} <= {ob.kind for ob in obs}
    for ob in obs:
        assert ob.low <= ob.high
        assert ob.time < ob.bos_time
        if ob.inducement is not None:
            assert ob.inducement_time > ob.bos_time
            if ob.direction == "bullish":
                assert ob.inducement > ob.high
            else:
                assert ob.inducement < ob.low
