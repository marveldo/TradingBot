import pandas as pd

from live import snapshot
from live.snapshot import bot_view, is_behind, view_lines

T = pd.Timestamp("2026-10-12 00:00", tz="UTC")
OB = dict(direction="bullish", kind="BOS", low=4150.0, high=4170.5, time=T, status="TAPPED")


def view(state="FOLLOWING", bias_in="bearish", bias_out="bearish", opposing=None):
    return dict(bias=bias_out,
                levels=[dict(htf="h4", ltf="h1", state=state, bias_in=bias_in, bias_out=bias_out, opposing_ob=opposing)],
                structure={"h4": dict(trend="bearish", bos_level=4100.5, choch_level=4230.0)}, pois={})


def test_following_step_names_the_direction_passed_down():
    assert view_lines(view())[:2] == ["bias: bearish", "  H4->H1: following bearish"]


def test_paused_step_shows_the_opposing_ob():
    lines = view_lines(view("AT_OPPOSING_OB", bias_out=None, opposing=OB))
    assert lines[0] == "bias: None"
    assert lines[1] == "  H4->H1: PAUSED at an opposing OB, waiting for a H1 CHoCH"
    assert lines[2] == "      opposing bullish BOS OB 4150-4170.5 (2026-10-12 00:00)"


def test_flipped_step_shows_both_directions():
    assert view_lines(view("FLIPPED", bias_out="bullish"))[1] == "  H4->H1: FLIPPED bearish -> bullish on a H1 CHoCH"


def test_structure_line_per_timeframe():
    assert "   H4 trend bearish | BOS level 4100.5 | CHoCH level 4230" in view_lines(view())


def test_prices_print_without_float_noise():
    v = view()
    v["structure"]["h1"] = dict(trend="bearish", bos_level=None, choch_level=1.3271600000000001)
    assert "   H1 trend bearish | BOS level None | CHoCH level 1.32716" in view_lines(v)


def bars(freq, count):
    times = pd.date_range(T - pd.Timedelta(freq) * count, periods=count, freq=freq)
    return pd.DataFrame({"time": times, "open": 1.1, "high": 1.2, "low": 1.0, "close": 1.15, "spread": 0})


def test_bot_view_replays_the_warm_up_and_ends_at_the_last_close(monkeypatch):
    history = {"M15": bars("15min", 40), "H1": bars("1h", 10), "H4": bars("4h", 3), "D1": bars("1D", 2),
               "W1": bars("7D", 1)}
    monkeypatch.setattr(snapshot, "load_history", lambda client, symbol: history)
    monkeypatch.setattr(snapshot, "fresh_history", lambda client, symbol: snapshot.load_history(client, symbol))
    view_now, data, now = bot_view(None, "EURUSD")
    assert now == history["M15"].time.iloc[-1] + pd.Timedelta(minutes=15)
    assert set(data) == {"m15", "h1", "h4", "d1", "w1"}
    assert {"bias", "levels", "structure", "pois"} <= set(view_now)


def test_history_is_behind_once_a_whole_candle_closed_after_it():
    close = pd.Timestamp("2026-10-14 19:30", tz="UTC")
    assert not is_behind(close, close + pd.Timedelta(minutes=14))
    assert is_behind(close, close + pd.Timedelta(minutes=15))
    assert is_behind(close, close + pd.Timedelta(days=1))          # e.g. MT5 just opened with yesterday's candles
