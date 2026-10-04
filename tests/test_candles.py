from pathlib import Path

import pandas as pd
import pytest

from core.candles import feed

LENGTH = {
    "m15": pd.Timedelta(minutes=15),
    "h1": pd.Timedelta(hours=1),
    "h4": pd.Timedelta(hours=4),
    "d1": pd.Timedelta(days=1),
    "w1": pd.Timedelta(weeks=1),
}
DATA = Path(__file__).resolve().parent.parent / "data" / "deriv"


def bars(tf, start, end):
    """Candles of timeframe `tf` opening every bar length from `start` up to (not including) `end`."""
    times = pd.date_range(pd.Timestamp(start, tz="UTC"), pd.Timestamp(end, tz="UTC"), freq=LENGTH[tf], inclusive="left")
    return pd.DataFrame({"time": times, "open": 1.0, "high": 1.0, "low": 1.0, "close": 1.0})


def empty():
    return bars("h1", "2026-01-01", "2026-01-01")


def run(m15, h1=None, h4=None, d1=None, w1=None):
    return list(feed(m15, h1 if h1 is not None else empty(), h4 if h4 is not None else empty(),
                     d1 if d1 is not None else empty(), w1 if w1 is not None else empty()))


def at(events, when):
    """The candles handed out at clock time `when`. Each TF maps to a list of candles that just closed."""
    target = pd.Timestamp(when, tz="UTC")
    return next(got for now, got in events if now == target)


# --- the clock ----------------------------------------------------------------

def test_one_event_per_m15_candle_in_order():
    m15 = bars("m15", "2026-01-05 00:00", "2026-01-05 02:00")
    events = run(m15)
    assert len(events) == 8
    assert [now for now, _ in events] == list(m15.time + LENGTH["m15"])
    # Every timeframe, M15 included, is a LIST of candles. M15 always has exactly one.
    assert all(len(got["m15"]) == 1 for _, got in events)
    assert all(got["m15"][0].time == now - LENGTH["m15"] for now, got in events)


# --- higher TFs arrive the moment they close ----------------------------------

def test_h1_handed_out_exactly_when_it_closes():
    events = run(bars("m15", "2026-01-05 00:00", "2026-01-05 02:00"), h1=bars("h1", "2026-01-05 00:00", "2026-01-05 02:00"))
    assert "h1" not in at(events, "2026-01-05 00:45")
    assert [c.time for c in at(events, "2026-01-05 01:00")["h1"]] == [pd.Timestamp("2026-01-05 00:00", tz="UTC")]
    assert "h1" not in at(events, "2026-01-05 01:15")


def test_h1_every_4th_tick_and_h4_every_16th():
    day = ("2026-01-05 00:00", "2026-01-06 00:00")
    events = run(bars("m15", *day), h1=bars("h1", *day), h4=bars("h4", *day))
    h1_times = [now for now, got in events if "h1" in got]
    h4_times = [now for now, got in events if "h4" in got]
    assert len(h1_times) == 24 and all(t.minute == 0 for t in h1_times)
    assert len(h4_times) == 6 and all(t.hour % 4 == 0 and t.minute == 0 for t in h4_times)


# --- no lookahead -------------------------------------------------------------

def test_nothing_is_handed_out_before_it_finishes():
    week = ("2026-01-05 00:00", "2026-01-12 00:00")
    events = run(bars("m15", *week), h1=bars("h1", *week), h4=bars("h4", *week),
                 d1=bars("d1", *week), w1=bars("w1", *week))
    for now, got in events:
        assert got["m15"][0].time + LENGTH["m15"] == now
        for tf in ("h1", "h4", "d1", "w1"):
            for candle in got.get(tf, []):
                assert candle.time + LENGTH[tf] <= now, f"{tf} candle {candle.time} handed out early at {now}"


def test_unfinished_last_candle_is_never_handed_out():
    # M15 stops at 01:30, so the 01:00 H1 candle (finishes 02:00) is still forming.
    events = run(bars("m15", "2026-01-05 00:00", "2026-01-05 01:30"), h1=bars("h1", "2026-01-05 00:00", "2026-01-05 02:00"))
    handed = [c.time for _, got in events for c in got.get("h1", [])]
    assert handed == [pd.Timestamp("2026-01-05 00:00", tz="UTC")]


def test_wednesday_does_not_see_this_weeks_w1_candle():
    events = run(bars("m15", "2026-01-04 00:00", "2026-01-08 00:00"), w1=bars("w1", "2026-01-04 00:00", "2026-01-11 00:00"))
    assert all("w1" not in got for _, got in events)


# --- weekend gap ----------------------------------------------------------------

def test_candles_closing_during_the_weekend_arrive_at_the_first_tick_after_it():
    # FX: last M15 tick Friday 21:00, next one Sunday 21:15. W1 (Sun 00:00) and Friday's last H4 (Sat 00:00) close in the gap.
    m15 = pd.concat([bars("m15", "2026-01-09 20:00", "2026-01-09 21:00"), bars("m15", "2026-01-11 21:00", "2026-01-11 22:00")])
    h4 = bars("h4", "2026-01-09 20:00", "2026-01-10 00:00")
    w1 = bars("w1", "2026-01-04 00:00", "2026-01-11 00:00")
    events = run(m15, h4=h4, w1=w1)

    first_after_gap = at(events, "2026-01-11 21:15")
    assert [c.time for c in first_after_gap["h4"]] == [pd.Timestamp("2026-01-09 20:00", tz="UTC")]
    assert [c.time for c in first_after_gap["w1"]] == [pd.Timestamp("2026-01-04 00:00", tz="UTC")]
    assert all("w1" not in got for now, got in events if now < pd.Timestamp("2026-01-11 21:15", tz="UTC"))


# --- holes in M15 data ------------------------------------------------------------

def test_two_h1_candles_closing_in_an_m15_hole_are_both_handed_out_in_order():
    # Real case (EURUSD 2024-05-02): M15 missing 09:45-10:30, H1 complete. At 11:00 both the 09:00 and 10:00 H1 have closed.
    m15 = pd.concat([bars("m15", "2026-01-05 09:00", "2026-01-05 09:45"), bars("m15", "2026-01-05 10:45", "2026-01-05 11:00")])
    h1 = bars("h1", "2026-01-05 09:00", "2026-01-05 11:00")
    events = run(m15, h1=h1)
    assert [c.time for c in at(events, "2026-01-05 11:00")["h1"]] == [
        pd.Timestamp("2026-01-05 09:00", tz="UTC"),
        pd.Timestamp("2026-01-05 10:00", tz="UTC"),
    ]


# --- real data ------------------------------------------------------------------

@pytest.mark.skipif(not (DATA / "EURUSD_M15.parquet").exists(), reason="no fetched data")
def test_real_eurusd_every_closed_candle_handed_out_once_and_never_early():
    load = lambda tf: pd.read_parquet(DATA / f"EURUSD_{tf}.parquet")
    m15, h1, h4, d1, w1 = load("M15"), load("H1"), load("H4"), load("D1"), load("W1")
    events = run(m15, h1=h1, h4=h4, d1=d1, w1=w1)
    last_now = events[-1][0]

    for tf, df in [("h1", h1), ("h4", h4), ("d1", d1), ("w1", w1)]:
        handed = [c.time for now, got in events for c in got.get(tf, [])]
        expected = list(df.time[df.time + LENGTH[tf] <= last_now])
        assert handed == expected, f"{tf}: handed {len(handed)}, expected {len(expected)}"
        early = [(now, c.time) for now, got in events for c in got.get(tf, []) if c.time + LENGTH[tf] > now]
        assert not early, f"{tf} handed out early: {early[:3]}"
