"""Smoke tests for research/plot_trades.py — the chart is for eyes, so only check that everything is drawn."""
import pandas as pd

from core.detectors import OrderBlock
from core.state_machine import Setup
from research.plot_trades import money_line, plot_context, plot_setup

T = pd.Timestamp("2026-01-05 12:00", tz="UTC")


def make_setup():
    poi = OrderBlock("bullish", "BOS", 1.1020, 1.1000, T - pd.Timedelta(hours=10), T - pd.Timedelta(hours=9), "tapped")
    poi.inducement, poi.inducement_time = 1.1040, T - pd.Timedelta(hours=8)
    block = OrderBlock("bullish", "breaker", 1.1030, 1.1022, T - pd.Timedelta(hours=2), T - pd.Timedelta(hours=1), "valid")
    block.inducement, block.inducement_time = 1.1045, T - pd.Timedelta(minutes=30)
    return Setup(T, "bullish", 1.1030, 1.1010, 1.1100, "h1", poi, block, 1.1010, 0.0005)


def m15_bars():
    times = pd.date_range(T - pd.Timedelta(days=2), T + pd.Timedelta(days=2), freq="15min")
    return pd.DataFrame({"time": times, "open": 1.103, "high": 1.104, "low": 1.102, "close": 1.1035})


def test_chart_has_zones_lines_and_title():
    fig = plot_setup(make_setup(), m15_bars(), symbol="EURUSD", outcome="win", r_result=3.5)
    labels = [a.text for a in fig.layout.annotations]
    assert any("H1 POI (BOS)" in t for t in labels)
    assert any("M15 breaker" in t for t in labels)
    assert any(t.startswith("entry") for t in labels) and any(t.startswith("SL") for t in labels)
    assert any(t.startswith("TP") for t in labels)
    assert "H1 inducement" in labels and "M15 inducement" in labels
    assert "WIN +3.50R" in fig.layout.title.text


def test_weekend_candles_are_not_hidden_when_the_market_trades_24_7():
    # Regression: weekends used to be hidden for every symbol, which hid BTC's Sunday fill + TP.
    fig = plot_setup(make_setup(), m15_bars())            # continuous 24/7 bars, T is a Monday
    assert not fig.layout.xaxis.rangebreaks


def test_inducement_labels_sit_at_the_left_so_they_never_cover_entry_sl_tp():
    # Regression: an inducement at the entry price printed its label on top of the entry label.
    setup = make_setup()
    fig = plot_setup(setup, m15_bars())
    by_text = {a.text: a for a in fig.layout.annotations}
    right_edge = by_text[f"entry {setup.entry:g}"].x
    assert by_text["H1 inducement"].x != right_edge and by_text["M15 inducement"].x != right_edge
    assert {by_text[f"SL {setup.sl:g}"].x, by_text[f"TP {setup.tp:g}"].x} == {right_edge}


def test_money_line_turns_r_into_dollars():
    assert money_line(4.55) == "Risk $100 → +$455"
    assert money_line(-1.0) == "Risk $100 → -$100"
    assert money_line(0.0) == "Risk $100 → +$0"
    assert money_line(2.5, risk_money=1000) == "Risk $1,000 → +$2,500"


def test_title_shows_the_money():
    fig = plot_setup(make_setup(), m15_bars(), outcome="win", r_result=4.55, risk_money=200)
    assert "Risk $200 → +$910" in fig.layout.title.text


def test_only_real_gaps_are_hidden():
    bars = m15_bars()
    gap = (bars.time > T - pd.Timedelta(hours=6)) & (bars.time < T - pd.Timedelta(hours=4))
    fig = plot_setup(make_setup(), bars[~gap])
    hidden = pd.to_datetime(fig.layout.xaxis.rangebreaks[0]["values"], utc=True)
    assert len(hidden) == gap.sum()
    assert hidden.min() > T - pd.Timedelta(hours=6) and hidden.max() < T - pd.Timedelta(hours=4)


def test_only_bars_around_the_setup_are_drawn():
    fig = plot_setup(make_setup(), m15_bars())
    xs = pd.to_datetime(fig.data[0].x, utc=True)
    assert xs.min() >= T - pd.Timedelta(hours=24) and xs.max() <= T + pd.Timedelta(hours=48)


# --- context chart: why the bot took it ----------------------------------------------------------

def bars_every(freq, days):
    times = pd.date_range(T - pd.Timedelta(days=days), T + pd.Timedelta(days=2), freq=freq)
    return pd.DataFrame({"time": times, "open": 1.103, "high": 1.104, "low": 1.102, "close": 1.1035})


def context_setup():
    setup = make_setup()
    setup.context = dict(
        bias="bullish",
        levels=[dict(htf="w1", ltf="d1", state="FOLLOWING", bias_in="bullish", bias_out="bullish", opposing_ob=None),
                dict(htf="d1", ltf="h4", state="FLIPPED", bias_in="bearish", bias_out="bullish",
                     opposing_ob=dict(direction="bullish", kind="CHoCH", low=1.090, high=1.095,
                                      time=T - pd.Timedelta(days=20), status="tapped")),
                dict(htf="h4", ltf="h1", state="FOLLOWING", bias_in="bullish", bias_out="bullish", opposing_ob=None)],
        structure={tf: dict(trend="bullish", bos_level=1.12, choch_level=1.09) for tf in ("w1", "d1", "h4", "h1", "m15")})
    return setup


def context_data():
    return {"w1": bars_every("7D", 400), "d1": bars_every("1D", 120), "h4": bars_every("4h", 30), "h1": bars_every("1h", 10)}


def test_context_chart_titles_explain_each_step_of_the_chain():
    fig = plot_context(context_setup(), context_data(), symbol="EURUSD")
    titles = [a.text for a in fig.layout.annotations]
    assert any(t.startswith("W1 — trend bullish | W1→D1: following bullish") for t in titles)
    assert any("D1→H4: FLIPPED bearish → bullish" in t for t in titles)
    assert any(t.startswith("H1 — trend bullish") for t in titles)
    assert any("opposing bullish CHoCH OB" in t for t in titles)
    assert any(t == "POI (BOS)" for t in titles)


def test_context_chart_draws_the_poi_on_every_panel():
    fig = plot_context(context_setup(), context_data())
    titles = [a.text for a in fig.layout.annotations]
    assert titles.count("POI (BOS)") == 1                 # its own TF (H1)
    assert titles.count("H1 POI (BOS)") == 3              # W1, D1, H4
    poi = context_setup().poi
    zones = [s for s in fig.layout.shapes if s.type == "rect" and (s.y0, s.y1) == (poi.low, poi.high)]
    assert len(zones) == 4


def test_context_chart_draws_every_live_poi_top_down():
    setup = context_setup()
    zone = lambda kind, low: dict(direction="bearish", kind=kind, low=low, high=low + 0.002, time=T - pd.Timedelta(days=3),
                                  status="valid")
    setup.context["pois"] = {"d1": [zone("BOS", 1.110)], "h4": [zone("breaker", 1.120)], "h1": [zone("mitigation", 1.130)]}
    titles = [a.text for a in plot_context(setup, context_data()).layout.annotations]
    assert titles.count("D1 BOS (valid)") == 3            # D1, H4, H1 panels
    assert titles.count("H4 breaker (valid)") == 2        # H4, H1 panels
    assert titles.count("H1 mitigation (valid)") == 1     # H1 panel only


def test_context_chart_does_not_draw_the_trade_poi_twice():
    setup = context_setup()
    poi = setup.poi
    setup.context["pois"] = {"d1": [], "h4": [], "h1": [dict(direction="bullish", kind="BOS", low=poi.low, high=poi.high,
                                                            time=poi.time, status="tapped")]}
    titles = [a.text for a in plot_context(setup, context_data()).layout.annotations]
    assert "H1 BOS (tapped)" not in titles


def test_context_chart_only_shows_candles_closed_before_the_setup():
    fig = plot_context(context_setup(), context_data())
    lengths = {"W1": pd.Timedelta(weeks=1), "D1": pd.Timedelta(days=1), "H4": pd.Timedelta(hours=4), "H1": pd.Timedelta(hours=1)}
    for trace in fig.data:
        last_open = pd.to_datetime(trace.x, utc=True).max()
        assert last_open + lengths[trace.name] <= T, f"{trace.name} shows a candle that had not closed yet"


def test_entry_chart_links_to_its_context_chart():
    fig = plot_setup(make_setup(), m15_bars(), context_link="01_context.html")
    assert any('href="01_context.html"' in a.text for a in fig.layout.annotations)
