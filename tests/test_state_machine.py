"""Tests for the multi-timeframe state machine — core/state_machine.py.

Level and build_setup are tested on hand-made OBs / breaks. The whole StateMachine is checked on real data.
"""
from pathlib import Path

import pandas as pd
import pytest

from core.candles import feed
from core.detectors import Break, OrderBlock
from collections import namedtuple

from core.state_machine import (Campaign, Level, LevelState, PendingEntry, StateMachine, build_setup,
                                continuation_setup, early_tap_step, nearest_block, order_prices, pending_entry_setup,
                                nearest_opposing_ob, pick_target, rr_ok, stop_level)

T = pd.Timestamp("2026-01-05 00:00", tz="UTC")
DATA = Path(__file__).resolve().parent.parent / "data" / "deriv"
BULL, BEAR = "bullish", "bearish"


def ob(direction, low, high, status="tapped"):
    return OrderBlock(direction, "BOS", high, low, T, T, status)


def choch(direction):
    return Break(direction, "CHoCH", 0.0, T)


def bos(direction):
    return Break(direction, "BOS", 0.0, T)


# --- Level: follow → pause at an opposing OB → flip on a lower-TF CHoCH ----------------------------

def test_level_follows_the_parent():
    level = Level("w1", "d1")
    assert level.update(BULL, [], []) == BULL
    assert level.update(BEAR, [], []) == BEAR
    assert level.state == LevelState.FOLLOWING


def test_unknown_parent_means_unknown():
    assert Level("w1", "d1").update(None, [], []) is None


def test_tapped_ob_in_the_same_direction_does_not_pause():
    level = Level("w1", "d1")
    assert level.update(BULL, [ob(BULL, 1.0, 1.1)], []) == BULL


def test_untapped_opposing_ob_does_not_pause():
    level = Level("w1", "d1")
    assert level.update(BULL, [ob(BEAR, 1.2, 1.3, status="swept")], []) == BULL


def test_tapped_opposing_ob_pauses():
    level = Level("w1", "d1")
    assert level.update(BULL, [ob(BEAR, 1.2, 1.3)], []) is None
    assert level.state == LevelState.AT_OPPOSING_OB
    assert level.update(BULL, [], [bos(BULL)]) is None          # a BOS isn't a CHoCH → still paused


def test_lower_tf_choch_at_the_opposing_ob_flips():
    level = Level("w1", "d1")
    level.update(BULL, [ob(BEAR, 1.2, 1.3)], [])
    assert level.update(BULL, [], [choch(BEAR)]) == BEAR
    assert level.state == LevelState.FLIPPED
    assert level.update(BULL, [], []) == BEAR                   # stays flipped while the parent is still bullish


def test_tap_and_choch_on_the_same_tick_flip_straight_away():
    level = Level("w1", "d1")
    assert level.update(BULL, [ob(BEAR, 1.2, 1.3)], [choch(BEAR)]) == BEAR


def test_opposing_ob_broken_while_waiting_means_follow_again():
    level = Level("w1", "d1")
    bear_ob = ob(BEAR, 1.2, 1.3)
    level.update(BULL, [bear_ob], [])
    bear_ob.status = "invalid"
    assert level.update(BULL, [bear_ob], []) == BULL
    assert level.state == LevelState.FOLLOWING


def test_flip_ends_when_the_parent_agrees():
    level = Level("w1", "d1")
    level.update(BULL, [ob(BEAR, 1.2, 1.3)], [choch(BEAR)])
    assert level.update(BEAR, [], []) == BEAR
    assert level.state == LevelState.FOLLOWING


def test_flip_fails_when_the_lower_tf_turns_back():
    level = Level("w1", "d1")
    level.update(BULL, [ob(BEAR, 1.2, 1.3)], [choch(BEAR)])
    assert level.update(BULL, [], [choch(BULL)]) == BULL
    assert level.state == LevelState.FOLLOWING


def test_bearish_mirror_pause_and_flip():
    level = Level("d1", "h4")
    assert level.update(BEAR, [ob(BULL, 1.0, 1.1)], []) is None
    assert level.update(BEAR, [], [choch(BULL)]) == BULL


# --- build_setup: POI → M15 CHoCH → limit at the M15 OB ---------------------------------------------

H4_POI = ob(BULL, 1.1000, 1.1020)                  # tapped bullish H4 OB
M15_OB = ob(BULL, 1.1015, 1.1025, status="pending")  # the M15 OB the CHoCH created


def test_bullish_setup():
    # The M15 reversal started at 1.1010 (inside the POI). Limit at the M15 OB high, SL at the swing low, TP at H4 high.
    s = build_setup(T, BULL, [("h4", H4_POI)], 1.1010, M15_OB, 1.1100)
    assert (s.direction, s.entry, s.sl, s.tp, s.poi_tf) == (BULL, 1.1025, 1.1010, 1.1100, "h4")
    assert s.poi is H4_POI and s.entry_ob is M15_OB


def test_sl_buffer_pushes_the_sl_further_away():
    s = build_setup(T, BULL, [("h4", H4_POI)], 1.1010, M15_OB, 1.1100, sl_buffer=0.0004, atr=0.0008)
    assert s.swing_sl == 1.1010
    assert s.sl == pytest.approx(1.1006)
    assert s.atr == 0.0008


def test_bearish_sl_buffer_goes_above():
    poi = ob(BEAR, 1.1980, 1.2000)
    s = build_setup(T, BEAR, [("h4", poi)], 1.1990, ob(BEAR, 1.1975, 1.1985), 1.1900, sl_buffer=0.0005)
    assert s.sl == pytest.approx(1.1995)


def test_atr_is_the_average_true_range_of_the_last_14_entry_candles():
    from collections import namedtuple
    C = namedtuple("C", "time open high low close")
    machine = StateMachine()
    for i in range(13):
        machine.track_atr(C(T, 1.0, 1.0010, 1.0000, 1.0005))
    assert machine.atr is None                                   # not enough candles yet
    machine.track_atr(C(T, 1.0005, 1.0035, 1.0000, 1.0030))     # range 35 pips
    assert machine.atr == pytest.approx((13 * 0.0010 + 0.0035) / 14)
    machine.track_atr(C(T, 1.0050, 1.0060, 1.0045, 1.0055))     # gap up: true range = 1.0060 - prev close 1.0030
    assert machine.true_ranges[-1] == pytest.approx(0.0030)


def test_sl_goes_beyond_the_m15_ob_if_it_sits_lower_than_the_swing():
    s = build_setup(T, BULL, [("h4", H4_POI)], 1.1018, M15_OB, 1.1100)
    assert s.sl == 1.1015


def test_reversal_starting_above_the_poi_is_not_a_setup():
    assert build_setup(T, BULL, [("h4", H4_POI)], 1.1030, M15_OB, 1.1100) is None


def test_reversal_starting_below_the_poi_is_not_a_setup():
    assert build_setup(T, BULL, [("h4", H4_POI)], 1.0990, M15_OB, 1.1100) is None


def test_reversal_exactly_on_the_poi_edges_counts():
    assert build_setup(T, BULL, [("h4", H4_POI)], 1.1000, M15_OB, 1.1100) is not None
    assert build_setup(T, BULL, [("h4", H4_POI)], 1.1020, ob(BULL, 1.1020, 1.1030), 1.1100) is not None


def test_no_room_to_the_target_is_not_a_setup():
    assert build_setup(T, BULL, [("h4", H4_POI)], 1.1010, M15_OB, 1.1025) is None
    assert build_setup(T, BULL, [("h4", H4_POI)], 1.1010, M15_OB, None) is None


def test_picks_the_first_poi_that_contains_the_reversal():
    h1_poi = ob(BULL, 1.1005, 1.1012)
    s = build_setup(T, BULL, [("h4", ob(BULL, 1.2000, 1.2100)), ("h1", h1_poi)], 1.1010, M15_OB, 1.1100)
    assert s.poi is h1_poi and s.poi_tf == "h1"


def test_bearish_setup():
    poi = ob(BEAR, 1.1980, 1.2000)
    m15 = ob(BEAR, 1.1975, 1.1985, status="pending")
    s = build_setup(T, BEAR, [("h4", poi)], 1.1990, m15, 1.1900)
    assert (s.entry, s.sl, s.tp) == (1.1975, 1.1990, 1.1900)


# --- campaigns: continuation entries until the target is taken ---------------------------------------

C = namedtuple("C", "time open high low close")
LATER = T + pd.Timedelta(hours=2)


def bull_campaign():
    return Campaign(BULL, 1.1100, "h1", H4_POI, T)


def test_continuation_limit_at_a_new_valid_m15_ob():
    m15_ob = OrderBlock(BULL, "BOS", 1.1060, 1.1050, LATER, LATER, "valid")
    s = continuation_setup(LATER, bull_campaign(), m15_ob, 1.1045, sl_buffer=0.0003, atr=0.0006)
    assert (s.kind, s.entry, s.swing_sl, s.tp) == ("continuation", 1.1060, 1.1045, 1.1100)
    assert s.sl == pytest.approx(1.1042)
    assert s.poi is H4_POI and s.poi_tf == "h1"


def test_no_continuation_from_an_ob_older_than_the_campaign():
    old = OrderBlock(BULL, "CHoCH", 1.1060, 1.1050, T, T, "valid")
    assert continuation_setup(LATER, bull_campaign(), old, 1.1045) is None


def test_no_continuation_against_the_campaign_direction():
    bear_ob = OrderBlock(BEAR, "BOS", 1.1060, 1.1050, LATER, LATER, "valid")
    assert continuation_setup(LATER, bull_campaign(), bear_ob, 1.1065) is None


def test_no_continuation_when_the_ob_sits_at_or_above_the_target():
    high_ob = OrderBlock(BULL, "BOS", 1.1100, 1.1090, LATER, LATER, "valid")
    assert continuation_setup(LATER, bull_campaign(), high_ob, 1.1085) is None


def test_bearish_continuation():
    c = Campaign(BEAR, 1.1900, "h1", ob(BEAR, 1.1980, 1.2000), T)
    m15_ob = OrderBlock(BEAR, "BOS", 1.1950, 1.1940, LATER, LATER, "valid")
    s = continuation_setup(LATER, c, m15_ob, 1.1955)
    assert (s.entry, s.sl, s.tp) == (1.1940, 1.1955, 1.1900)


def test_campaign_ends_when_price_takes_the_target():
    c = bull_campaign()
    assert not c.is_over(C(LATER, 1.1090, 1.1099, 1.1080, 1.1095), BULL)
    assert c.is_over(C(LATER, 1.1090, 1.1100, 1.1080, 1.1095), BULL)        # wick exactly to the target


def test_bearish_campaign_ends_at_its_target():
    c = Campaign(BEAR, 1.1900, "h1", ob(BEAR, 1.1980, 1.2000), T)
    assert not c.is_over(C(LATER, 1.1910, 1.1920, 1.1901, 1.1905), BEAR)
    assert c.is_over(C(LATER, 1.1910, 1.1920, 1.1900, 1.1905), BEAR)


def test_campaign_ends_when_price_takes_out_the_first_setups_swing():
    c = Campaign(BULL, 1.1100, "h1", ob(BULL, 1.1000, 1.1020), T, invalidation=1.1010)
    assert not c.is_over(C(LATER, 1.1030, 1.1040, 1.1010, 1.1035), BULL)    # exactly on the swing low → alive
    assert c.is_over(C(LATER, 1.1030, 1.1040, 1.1009, 1.1035), BULL)


def test_bearish_campaign_ends_when_price_takes_out_the_swing_high():
    c = Campaign(BEAR, 1.1900, "h1", ob(BEAR, 1.1980, 1.2000), T, invalidation=1.1990)
    assert not c.is_over(C(LATER, 1.1960, 1.1990, 1.1950, 1.1955), BEAR)
    assert c.is_over(C(LATER, 1.1960, 1.1991, 1.1950, 1.1955), BEAR)


def test_campaign_ends_when_the_poi_breaks():
    poi = ob(BULL, 1.1000, 1.1020)
    c = Campaign(BULL, 1.1100, "h1", poi, T)
    candle = C(LATER, 1.1030, 1.1040, 1.1025, 1.1035)
    assert not c.is_over(candle, BULL)
    poi.status = "invalid"
    assert c.is_over(candle, BULL)


def test_campaign_ends_when_the_direction_changes():
    c = bull_campaign()
    assert c.is_over(C(LATER, 1.1050, 1.1060, 1.1040, 1.1055), BEAR)
    assert c.is_over(C(LATER, 1.1050, 1.1060, 1.1040, 1.1055), None)       # paused counts too


# --- inducement entry: CHoCH OB or breaker, whichever gets its inducement first --------------------------

def m15_block(kind, low, high, inducement=None, status="pending"):
    block = OrderBlock(BULL, kind, high, low, T, T, status)
    block.inducement = inducement
    return block


def pending(*candidates, leg_extreme=1.1010, target=1.1100):
    return PendingEntry(BULL, "h1", H4_POI, list(candidates), leg_extreme, target, T)


def test_no_limit_until_a_block_has_its_inducement():
    p = pending(m15_block("CHoCH", 1.1015, 1.1025), m15_block("breaker", 1.1030, 1.1040))
    assert p.ready() == []
    assert pending_entry_setup(LATER, p) is None


def test_limit_goes_on_the_breaker_when_its_inducement_comes_first():
    choch_ob = m15_block("CHoCH", 1.1015, 1.1025)
    breaker = m15_block("breaker", 1.1030, 1.1040, inducement=1.1050, status="valid")
    s = pending_entry_setup(LATER, pending(choch_ob, breaker))
    assert s.entry_ob is breaker
    assert (s.entry, s.swing_sl, s.tp, s.kind) == (1.1040, 1.1010, 1.1100, "first")   # breaker: SL beyond the swing


def test_limit_goes_on_the_choch_ob_when_its_inducement_comes_first():
    choch_ob = m15_block("CHoCH", 1.1015, 1.1025, inducement=1.1035, status="valid")
    breaker = m15_block("breaker", 1.1030, 1.1040, status="invalid")     # pullback killed it
    s = pending_entry_setup(LATER, pending(choch_ob, breaker))
    assert s.entry_ob is choch_ob and s.entry == 1.1025


def test_both_ready_at_once_takes_the_one_price_reaches_first():
    lower = m15_block("CHoCH", 1.1015, 1.1025, inducement=1.1045, status="valid")
    upper = m15_block("breaker", 1.1030, 1.1040, inducement=1.1045, status="valid")
    assert pending_entry_setup(LATER, pending(lower, upper)).entry_ob is upper


def test_pending_ends_when_price_reaches_the_target_first():
    p = pending(m15_block("CHoCH", 1.1015, 1.1025))
    assert not p.is_over(C(LATER, 1.1050, 1.1099, 1.1040, 1.1090), BULL)
    assert p.is_over(C(LATER, 1.1050, 1.1100, 1.1040, 1.1090), BULL)


def test_pending_ends_when_every_block_is_dead_or_direction_changes():
    a, b = m15_block("CHoCH", 1.1015, 1.1025), m15_block("breaker", 1.1030, 1.1040)
    p = pending(a, b)
    candle = C(LATER, 1.1050, 1.1060, 1.1040, 1.1055)
    assert p.is_over(candle, BEAR)
    a.status = "invalid"
    assert not p.is_over(candle, BULL)
    b.status = "invalid"
    assert p.is_over(candle, BULL)


# --- immediate entry: the block price reaches first ------------------------------------------------------

def test_immediate_entry_picks_the_highest_buy_block():
    choch_ob, breaker = m15_block("CHoCH", 1.1015, 1.1025), m15_block("breaker", 1.1030, 1.1040)
    assert nearest_block(BULL, [choch_ob, breaker]) is breaker


def test_immediate_entry_picks_the_lowest_sell_block():
    lower = OrderBlock(BEAR, "CHoCH", 1.1960, 1.1950, T, T)
    upper = OrderBlock(BEAR, "breaker", 1.1980, 1.1970, T, T)
    assert nearest_block(BEAR, [upper, lower]) is lower


def test_immediate_entry_with_only_the_choch_ob():
    choch_ob = m15_block("CHoCH", 1.1015, 1.1025)
    assert nearest_block(BULL, [choch_ob]) is choch_ob
    assert nearest_block(BULL, []) is None


# --- where the SL goes ----------------------------------------------------------------------------------

def test_breaker_sl_goes_beyond_the_swing_like_every_entry():
    breaker = m15_block("breaker", 1.1030, 1.1040)
    assert stop_level(BULL, breaker, leg_extreme=1.1010) == 1.1010


def test_bearish_breaker_sl_goes_above_the_swing():
    breaker = OrderBlock(BEAR, "breaker", 1.1980, 1.1970, T, T)
    assert stop_level(BEAR, breaker, leg_extreme=1.2000) == 1.2000


def test_choch_ob_and_mitigation_sl_stay_beyond_the_swing():
    assert stop_level(BULL, m15_block("CHoCH", 1.1015, 1.1025), leg_extreme=1.1010) == 1.1010
    assert stop_level(BULL, m15_block("mitigation", 1.1030, 1.1040), leg_extreme=1.1010) == 1.1010
    assert stop_level(BULL, m15_block("CHoCH", 1.1005, 1.1025), leg_extreme=1.1010) == 1.1005   # block lower than the swing


def test_immediate_breaker_setup_uses_the_swing_sl():
    breaker = m15_block("breaker", 1.1030, 1.1040)
    s = build_setup(T, BULL, [("h4", H4_POI)], 1.1010, breaker, 1.1100)
    assert (s.entry, s.swing_sl) == (1.1040, 1.1010)


# --- spread space on entry + SL --------------------------------------------------------------------------

def test_buy_entry_moves_up_and_sl_moves_down_by_the_spread():
    entry, swing_sl, sl = order_prices(BULL, m15_block("CHoCH", 1.1015, 1.1025), 1.1010, spread=0.0002)
    assert (entry, swing_sl, sl) == (pytest.approx(1.1027), 1.1010, pytest.approx(1.1008))


def test_sell_entry_moves_down_and_sl_moves_up_by_the_spread():
    block = OrderBlock(BEAR, "CHoCH", 1.1985, 1.1975, T, T)
    entry, swing_sl, sl = order_prices(BEAR, block, 1.1990, spread=0.0002)
    assert (entry, swing_sl, sl) == (pytest.approx(1.1973), 1.1990, pytest.approx(1.1992))


def test_spread_and_atr_buffer_add_up_on_the_sl():
    _, _, sl = order_prices(BULL, m15_block("CHoCH", 1.1015, 1.1025), 1.1010, sl_buffer=0.0003, spread=0.0002)
    assert sl == pytest.approx(1.1005)


def test_breaker_with_spread():
    entry, swing_sl, sl = order_prices(BULL, m15_block("breaker", 1.1030, 1.1040), 1.1010, spread=0.0001)
    assert (entry, swing_sl, sl) == (pytest.approx(1.1041), 1.1010, pytest.approx(1.1009))


def test_spread_space_is_the_average_spread_times_point():
    machine = StateMachine(point=0.00001)
    assert machine.spread_space == 0.0                     # no candles yet
    machine.spreads.extend([10, 20, 30])                   # points
    assert machine.spread_space == pytest.approx(0.0002)
    assert StateMachine().spread_space == 0.0              # no point given → no spread space


def test_snapshot_lists_live_pois_per_tf():
    snap = StateMachine().snapshot()
    assert set(snap["pois"]) == {"d1", "h4", "h1"} and all(v == [] for v in snap["pois"].values())


# --- seeing an H1/H4 tap on M15, before the higher-TF candle closes ------------------------------------

def valid_poi():
    poi = OrderBlock(BULL, "BOS", 1.1020, 1.1000, T, T, "valid")
    poi.inducement = 1.1040
    return poi


def test_m15_candle_above_the_inducement_does_nothing():
    assert early_tap_step(valid_poi(), C(LATER, 1.1050, 1.1060, 1.1041, 1.1045), False) == (False, False, False)


def test_m15_sweep_then_a_later_m15_tap():
    poi = valid_poi()
    swept, tapped, broken = early_tap_step(poi, C(LATER, 1.1050, 1.1060, 1.1035, 1.1045), False)
    assert (swept, tapped, broken) == (True, False, False)
    assert early_tap_step(poi, C(LATER, 1.1030, 1.1035, 1.1020, 1.1025), swept) == (True, True, False)   # exactly the top


def test_m15_tap_without_a_sweep_is_impossible_for_a_bullish_poi():
    # reaching the zone means passing the inducement on the way → the same candle sweeps and taps
    assert early_tap_step(valid_poi(), C(LATER, 1.1050, 1.1060, 1.1015, 1.1030), False) == (True, True, False)


def test_m15_candle_through_the_zone_bottom_is_broken_not_a_tap():
    assert early_tap_step(valid_poi(), C(LATER, 1.1050, 1.1060, 1.0999, 1.1030), False)[2] is True


def test_bearish_early_tap():
    poi = OrderBlock(BEAR, "BOS", 1.2000, 1.1980, T, T, "valid")
    poi.inducement = 1.1960
    assert early_tap_step(poi, C(LATER, 1.1950, 1.1965, 1.1940, 1.1955), False) == (True, False, False)
    assert early_tap_step(poi, C(LATER, 1.1950, 1.1980, 1.1940, 1.1955), True) == (True, True, False)
    assert early_tap_step(poi, C(LATER, 1.1950, 1.2001, 1.1940, 1.1955), True)[2] is True


# --- minimum RR ------------------------------------------------------------------------------------------

def test_rr_ok_on_and_around_the_boundary():
    assert rr_ok(1.1000, 1.0990, 1.1015, 1.5)          # exactly 1.5:1 → allowed
    assert not rr_ok(1.1000, 1.0990, 1.1014, 1.5)      # 1.4:1 → skipped
    assert rr_ok(1.1000, 1.0990, 1.1001, 0.0)          # 0 = no filter


def test_min_rr_skips_a_small_target_in_build_setup():
    # entry 1.1025, SL 1.1010 (risk 0.0015). TP 1.1040 = 1:1.
    assert build_setup(T, BULL, [("h4", H4_POI)], 1.1010, M15_OB, 1.1040, min_rr=1.5) is None
    assert build_setup(T, BULL, [("h4", H4_POI)], 1.1010, M15_OB, 1.1040, min_rr=1.0) is not None


def test_min_rr_in_the_inducement_entry():
    block = m15_block("CHoCH", 1.1015, 1.1025, inducement=1.1035, status="valid")
    assert pending_entry_setup(LATER, pending(block, target=1.1040), min_rr=1.5) is None
    assert pending_entry_setup(LATER, pending(block, target=1.1050), min_rr=1.5) is not None


def test_min_rr_is_a_machine_setting():
    assert StateMachine().min_rr == 0.0 and StateMachine(min_rr=2.0).min_rr == 2.0


# --- TP at the nearest opposing higher-TF OB ---------------------------------------------------------------

def test_buy_targets_the_closest_bearish_htf_ob_above_price():
    live = {"h1": [OrderBlock(BEAR, "BOS", 1.1080, 1.1070, T, T), OrderBlock(BULL, "BOS", 1.1065, 1.1060, T, T)],
            "h4": [OrderBlock(BEAR, "breaker", 1.1120, 1.1100, T, T), OrderBlock(BEAR, "BOS", 1.1040, 1.1030, T, T)]}
    # 1.1030-1.1040 is below price (1.1050) → ignored; the bullish H1 OB is not opposing → ignored
    assert nearest_opposing_ob(live, BULL, 1.1050) == 1.1070


def test_sell_targets_the_closest_bullish_htf_ob_below_price():
    live = {"h1": [OrderBlock(BULL, "BOS", 1.1010, 1.1000, T, T)], "h4": [OrderBlock(BULL, "CHoCH", 1.0980, 1.0970, T, T)]}
    assert nearest_opposing_ob(live, BEAR, 1.1050) == 1.1010


def test_no_opposing_ob_means_no_target():
    assert nearest_opposing_ob({"h1": [], "h4": []}, BULL, 1.1050) is None
    assert nearest_opposing_ob({"h1": [OrderBlock(BEAR, "BOS", 1.1080, 1.1070, T, T)]}, BULL, None) is None


def test_htf_ob_mode_in_pick_target():
    live = {"h1": [OrderBlock(BEAR, "BOS", 1.1080, 1.1070, T, T)]}
    assert pick_target(H4_POI, BULL, "htf_ob", StateMachine().detectors["h1"].structure, live, 1.1050) == 1.1070


def test_dead_obs_leave_the_live_list():
    m = StateMachine()
    ob = OrderBlock(BEAR, "BOS", 1.1080, 1.1070, T, T)
    m.live_obs["h1"], m.live_ids = [ob], {id(ob)}
    ob.status = "invalid"
    m.detectors["h1"].update = lambda candle: [ob]          # pretend the H1 candle changed this OB
    m.update(LATER, {"h1": [C(LATER, 1.1, 1.2, 1.0, 1.1)]})
    assert m.live_obs["h1"] == []


# --- defaults = the first version; the later ideas are switches ------------------------------------------

def test_defaults_are_the_first_version():
    m = StateMachine()
    assert (m.target_mode, m.entry_blocks, m.early_taps, m.spread_space) == ("liquidity", "choch", False, 0.0)
    assert all(not d.breakers_and_mitigation and not d.single_use for d in m.detectors.values())


def test_switches_turn_the_later_ideas_on():
    m = StateMachine(breakers_and_mitigation=True, single_use=True, early_taps=True, entry_blocks="nearest", target_mode="poi_swing")
    assert (m.target_mode, m.entry_blocks, m.early_taps) == ("poi_swing", "nearest", True)
    assert all(d.breakers_and_mitigation and d.single_use for d in m.detectors.values())


def test_early_taps_off_ignores_m15_taps():
    m = StateMachine()
    poi = valid_poi()
    m.watching["h1"][id(poi)] = (poi, False)
    m.update(LATER, {"m15": [C(LATER, 1.1050, 1.1060, 1.1015, 1.1030)]})    # sweeps + taps on M15
    assert m.active_pois["h1"] == []


def test_early_taps_on_sees_the_m15_tap():
    m = StateMachine(early_taps=True)
    poi = valid_poi()
    m.watching["h1"][id(poi)] = (poi, False)
    m.update(LATER, {"m15": [C(LATER, 1.1050, 1.1060, 1.1015, 1.1030)]})
    assert m.active_pois["h1"] == [poi]


# --- TP from the POI's swing ------------------------------------------------------------------------------

def test_poi_swing_mode_targets_the_pois_swing():
    poi = OrderBlock(BULL, "breaker", 1.1020, 1.1000, T, T, "tapped")
    poi.swing_target = 1.1090
    assert pick_target(poi, BULL, "poi_swing", StateMachine().detectors["h1"].structure) == 1.1090


def test_liquidity_mode_still_uses_the_tf_swing():
    poi = OrderBlock(BULL, "BOS", 1.1020, 1.1000, T, T, "tapped")
    poi.swing_target = 1.1090
    assert pick_target(poi, BULL, "liquidity", StateMachine().detectors["h1"].structure) is None   # no H1 trend yet


# --- spent POIs stop being POIs ----------------------------------------------------------------------

def test_spent_early_tapped_poi_is_dropped_from_the_active_list():
    machine = StateMachine()
    poi = OrderBlock(BULL, "BOS", 1.1020, 1.1000, T, T, "tapped")
    poi.inducement = 1.1040
    machine.active_pois["h1"] = [poi]
    machine.early_tapped.add(id(poi))
    poi.status = "spent"
    machine.update(LATER, {})
    assert machine.active_pois["h1"] == []


# --- snapshot: why the bot took a trade ----------------------------------------------------------------

def test_snapshot_lists_every_level_and_every_tf():
    machine = StateMachine()
    snap = machine.snapshot()
    assert [(l["htf"], l["ltf"]) for l in snap["levels"]] == [("w1", "d1"), ("d1", "h4"), ("h4", "h1")]
    assert set(snap["structure"]) == {"w1", "d1", "h4", "h1", "m15"}
    assert all(l["state"] == "FOLLOWING" and l["opposing_ob"] is None for l in snap["levels"])


def test_snapshot_shows_a_paused_level_and_its_opposing_ob():
    machine = StateMachine()
    machine.level_inputs = [BULL, BULL, BULL]
    machine.levels[1].update(BULL, [ob(BEAR, 1.2, 1.3)], [])
    snap = machine.snapshot()
    d1_h4 = snap["levels"][1]
    assert (d1_h4["state"], d1_h4["bias_in"], d1_h4["bias_out"]) == ("AT_OPPOSING_OB", BULL, None)
    assert (d1_h4["opposing_ob"]["direction"], d1_h4["opposing_ob"]["low"], d1_h4["opposing_ob"]["high"]) == (BEAR, 1.2, 1.3)


# --- the whole machine on real data -------------------------------------------------------------------

@pytest.mark.skipif(not (DATA / "EURUSD_M15.parquet").exists(), reason="no fetched data")
def test_real_eurusd_setups_make_sense():
    load = lambda tf: pd.read_parquet(DATA / f"EURUSD_{tf}.parquet")
    machine = StateMachine(continuations=True)
    biases = []
    for now, candles in feed(load("M15"), load("H1"), load("H4"), load("D1"), load("W1")):
        for s in machine.update(now, candles):
            biases.append(machine.bias)

    setups = machine.setups
    assert len(setups) > 20
    assert [s.time for s in setups] == sorted(s.time for s in setups)
    firsts = [s for s in setups if s.kind == "first"]
    assert len({id(s.poi) for s in firsts}) == len(firsts)          # each POI starts one campaign
    assert any(s.kind == "continuation" for s in setups)
    for s, bias in zip(setups, biases):
        assert s.direction == bias
        assert s.poi.direction == s.direction
        assert s.poi.time < s.time and s.entry_ob.time < s.time      # nothing from the future
        if s.kind == "continuation":
            assert s.entry_ob.inducement is not None
        assert s.context["bias"] == s.direction and s.context["levels"][-1]["bias_out"] == s.direction
        if s.direction == BULL:
            assert s.sl < s.entry < s.tp
        else:
            assert s.tp < s.entry < s.sl
