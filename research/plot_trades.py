"""Draw setups so they can be checked by eye (PLAN Stage 2, step 11).

    fig = plot_setup(setup, m15_df, symbol="EURUSD", outcome="win", context_link="01_context.html")
    fig.write_html("trade.html")
    plot_context(setup, {"w1": w1_df, "d1": d1_df, "h4": h4_df, "h1": h1_df}, symbol="EURUSD").write_html("01_context.html")

plot_setup:   the M15 entry: the H1/H4 POI zone + its inducement, the M15 break, the M15 block used for the entry +
              its inducement, and the entry / SL / TP lines from the moment the limit was placed.
plot_context: WHY the bot took it: W1 / D1 / H4 / H1 as they looked when it fired (closed candles only), each with its
              trend, BOS/CHoCH levels and its step of the chain (following / paused / flipped), the POI and any
              opposing OB that flipped the direction.
"""
import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from core.state_machine import Setup

BEFORE = pd.Timedelta(hours=24)     # how much chart to show before the limit was placed
AFTER = pd.Timedelta(hours=48)      # ... and after
RISK_MONEY = 100.0                  # example money risked per trade, to show R as $ (1R = this much)

BUY, SELL = "rgba(38, 166, 154, 0.18)", "rgba(239, 83, 80, 0.18)"


def plot_setup(setup : Setup, m15 : pd.DataFrame, symbol : str = "", outcome : str | None = None,
               r_result : float | None = None, risk_money : float = RISK_MONEY,
               context_link : str | None = None) -> go.Figure:
    start = min(setup.time - BEFORE, setup.entry_ob.time - pd.Timedelta(hours=2))
    end = setup.time + AFTER
    bars = m15[(m15.time >= start) & (m15.time <= end)]

    fig = go.Figure(go.Candlestick(x=bars.time, open=bars.open, high=bars.high, low=bars.low, close=bars.close,
                                   name="M15", showlegend=False))
    buy = setup.direction == "bullish"
    zone_colour = BUY if buy else SELL

    # higher-TF POI: from its OB candle (or the chart start) to the end
    poi = setup.poi
    fig.add_shape(type="rect", x0=max(poi.time, start), x1=end, y0=poi.low, y1=poi.high,
                  fillcolor=zone_colour, line_width=0, layer="below")
    fig.add_annotation(x=max(poi.time, start), y=poi.high, text=f"{setup.poi_tf.upper()} POI ({poi.kind})",
                       showarrow=False, xanchor="left", yanchor="bottom", font_size=11)
    if poi.inducement is not None:
        _hline(fig, max(poi.inducement_time, start), end, poi.inducement, "#9e9e9e", "dot",
               f"{setup.poi_tf.upper()} inducement", label_at="start")

    # M15 entry block + its inducement
    block = setup.entry_ob
    fig.add_shape(type="rect", x0=block.time, x1=end, y0=block.low, y1=block.high,
                  fillcolor=zone_colour.replace("0.18", "0.45"), line=dict(width=1, color="#555"), layer="below")
    fig.add_annotation(x=block.time, y=block.low if buy else block.high, text=f"M15 {block.kind}",
                       showarrow=False, xanchor="left", yanchor="top" if buy else "bottom", font_size=11)
    if block.inducement is not None:
        _hline(fig, block.inducement_time, end, block.inducement, "#ff9800", "dot", "M15 inducement", label_at="start")

    # the M15 CHoCH candle and the moment the limit went in
    fig.add_vline(x=block.bos_time, line=dict(color="#7e57c2", dash="dash", width=1))
    fig.add_annotation(x=block.bos_time, y=1, yref="paper", text="M15 break", showarrow=False, font_size=10)
    fig.add_vline(x=setup.time, line=dict(color="#424242", width=1))

    # entry / SL / TP
    _hline(fig, setup.time, end, setup.entry, "#1e88e5", "solid", f"entry {setup.entry:g}")
    _hline(fig, setup.time, end, setup.sl, "#e53935", "solid", f"SL {setup.sl:g}")
    _hline(fig, setup.time, end, setup.tp, "#43a047", "solid", f"TP {setup.tp:g}")

    title = f"{symbol} {setup.direction} | {setup.poi_tf.upper()} {poi.kind} POI → M15 {block.kind} | {setup.time:%Y-%m-%d %H:%M} UTC"
    if outcome:
        title += f" | {outcome.upper()}" + (f" {r_result:+.2f}R" if r_result is not None else "")
        if r_result is not None:
            title += f" | {money_line(r_result, risk_money)}"
    fig.update_layout(title=title, xaxis_rangeslider_visible=False, height=650, margin=dict(l=40, r=120, t=60, b=40),
                      template="plotly_white")
    if context_link:
        fig.add_annotation(x=1, y=1.06, xref="paper", yref="paper", xanchor="right", showarrow=False, font_size=13,
                           text=f'<a href="{context_link}">HTF context: why the bot took this ↗</a>')
    # hide only the M15 slots that really have no candle (FX weekends, holes) — BTC trades 24/7, so nothing hidden
    breaks = _gap_breaks(bars, start, end, "15min")
    if breaks:
        fig.update_xaxes(rangebreaks=breaks)
    return fig


CONTEXT_TFS = [("w1", pd.Timedelta(weeks=1), 52), ("d1", pd.Timedelta(days=1), 90),
               ("h4", pd.Timedelta(hours=4), 120), ("h1", pd.Timedelta(hours=1), 120)]   # (tf, candle length, candles shown)


LIVE_POI_TFS = ["d1", "h4", "h1"]                                   # top-down order
LIVE_POI_DASH = {"d1": "solid", "h4": "dash", "h1": "dot"}


def plot_context(setup : Setup, data : dict[str, pd.DataFrame], symbol : str = "") -> go.Figure:
    """W1 / D1 / H4 / H1 as the bot saw them when the setup fired, with the chain's state on each."""
    ctx = setup.context or {}
    levels = {l["htf"]: l for l in ctx.get("levels", [])}
    structure = ctx.get("structure", {})
    fig = make_subplots(rows=len(CONTEXT_TFS), cols=1, vertical_spacing=0.06,
                        subplot_titles=[_context_title(tf, structure.get(tf, {}), levels.get(tf)) for tf, _, _ in CONTEXT_TFS])
    zone = BUY if setup.direction == "bullish" else SELL
    for row, (tf, length, n) in enumerate(CONTEXT_TFS, start=1):
        df = data[tf]
        bars = df[df.time + length <= setup.time].tail(n)          # only candles that had CLOSED → no lookahead
        if bars.empty:
            continue
        start, end = bars.time.iloc[0], setup.time
        fig.add_trace(go.Candlestick(x=bars.time, open=bars.open, high=bars.high, low=bars.low, close=bars.close,
                                     name=tf.upper(), showlegend=False), row=row, col=1)
        st = structure.get(tf, {})
        for key, colour, label in (("bos_level", "#43a047", "BOS level"), ("choch_level", "#e53935", "CHoCH level")):
            if st.get(key) is not None:
                fig.add_shape(type="line", x0=start, x1=end, y0=st[key], y1=st[key], row=row, col=1,
                              line=dict(color=colour, dash="dash", width=1))
                fig.add_annotation(x=end, y=st[key], text=label, showarrow=False, xanchor="left", row=row, col=1,
                                   font=dict(size=9, color=colour))
        # the POI on every panel, so you can see where it sits on each TF (outlined on its own TF)
        poi = setup.poi
        own_tf = tf == setup.poi_tf
        fig.add_shape(type="rect", x0=max(poi.time, start), x1=end, y0=poi.low, y1=poi.high, row=row, col=1,
                      fillcolor=zone, layer="below",
                      line=dict(width=1.5 if own_tf else 1, color="#555" if own_tf else "#999", dash="solid" if own_tf else "dot"))
        fig.add_annotation(x=max(poi.time, start), y=poi.high, showarrow=False, xanchor="left", yanchor="bottom",
                           row=row, col=1, font_size=10,
                           text=f"POI ({poi.kind})" if own_tf else f"{setup.poi_tf.upper()} POI ({poi.kind})")
        # every live POI from D1 down to H1: on its own panel and every panel below it (top-down view)
        for poi_tf in LIVE_POI_TFS[:LIVE_POI_TFS.index(tf) + 1] if tf in LIVE_POI_TFS else []:
            for z in ctx.get("pois", {}).get(poi_tf, []):
                if z["time"] == setup.poi.time and (z["low"], z["high"]) == (setup.poi.low, setup.poi.high):
                    continue                                    # the trade's own POI, already drawn
                colour = "rgba(38, 166, 154, 0.9)" if z["direction"] == "bullish" else "rgba(239, 83, 80, 0.9)"
                fig.add_shape(type="rect", x0=max(z["time"], start), x1=end, y0=z["low"], y1=z["high"], row=row, col=1,
                              fillcolor="rgba(0,0,0,0)", layer="below",
                              line=dict(width=1, color=colour, dash=LIVE_POI_DASH[poi_tf]))
                fig.add_annotation(x=end, y=z["high"] if z["direction"] == "bearish" else z["low"], showarrow=False,
                                   xanchor="left", row=row, col=1, font=dict(size=8, color=colour),
                                   text=f"{poi_tf.upper()} {z['kind']} ({z['status']})")
        opposing = (levels.get(tf) or {}).get("opposing_ob")
        if opposing:
            fig.add_shape(type="rect", x0=max(opposing["time"], start), x1=end, y0=opposing["low"], y1=opposing["high"],
                          row=row, col=1, fillcolor="rgba(255, 193, 7, 0.25)", line=dict(width=1, color="#ffa000"), layer="below")
            fig.add_annotation(x=max(opposing["time"], start), y=opposing["high"], showarrow=False, xanchor="left",
                               yanchor="bottom", row=row, col=1, font_size=10,
                               text=f"opposing {opposing['direction']} {opposing['kind']} OB")
        fig.add_vline(x=setup.time, line=dict(color="#424242", width=1), row=row, col=1)
        breaks = _gap_breaks(bars, start, end, length) if tf in ("h4", "h1") else None
        if breaks:
            fig.update_xaxes(rangebreaks=breaks, row=row, col=1)
        fig.update_xaxes(rangeslider_visible=False, row=row, col=1)
    fig.update_layout(height=1500, template="plotly_white", margin=dict(l=40, r=110, t=90, b=40),
                      title=f"{symbol} {setup.direction} at {setup.time:%Y-%m-%d %H:%M} UTC | why: "
                            f"final direction {ctx.get('bias')} | POI on {setup.poi_tf.upper()} ({setup.poi.kind})")
    return fig


def _context_title(tf : str, st : dict, level : dict | None) -> str:
    text = f"{tf.upper()} — trend {st.get('trend')}"
    if level:
        step = f"{level['htf'].upper()}→{level['ltf'].upper()}"
        if level["state"] == "FOLLOWING":
            text += f" | {step}: following {level['bias_in']}"
        elif level["state"] == "AT_OPPOSING_OB":
            text += f" | {step}: PAUSED at an opposing OB (waiting for a {level['ltf'].upper()} CHoCH)"
        else:
            text += f" | {step}: FLIPPED {level['bias_in']} → {level['bias_out']} ({level['ltf'].upper()} CHoCH at an opposing OB)"
    return text


def _gap_breaks(bars : pd.DataFrame, start, end, freq) -> list[dict] | None:
    """Rangebreaks for the candle slots that have no candle (weekends, holes). None when nothing is missing."""
    freq = pd.Timedelta(freq)
    slots = pd.date_range(pd.Timestamp(start).floor(freq), end, freq=freq)
    missing = slots.difference(pd.DatetimeIndex(bars.time))
    if not len(missing):
        return None
    return [dict(values=missing, dvalue=int(freq.total_seconds() * 1000))]


def _hline(fig, x0, x1, y, colour, dash, label, label_at="end"):
    """label_at="end": right of the line (entry / SL / TP), "start": just above its left end (inducements),
    so labels at nearly the same price don't print on top of each other."""
    fig.add_shape(type="line", x0=x0, x1=x1, y0=y, y1=y, line=dict(color=colour, dash=dash, width=1.5))
    if label_at == "end":
        fig.add_annotation(x=x1, y=y, text=label, showarrow=False, xanchor="left", font=dict(size=10, color=colour))
    else:
        fig.add_annotation(x=x0, y=y, text=label, showarrow=False, xanchor="left", yanchor="bottom",
                           font=dict(size=10, color=colour))


def money_line(r_result : float, risk_money : float = RISK_MONEY) -> str:
    """'Risk $100 → +$455' for +4.55R. A trade that never filled risked nothing."""
    gained = r_result * risk_money
    sign = "+" if gained >= 0 else "-"
    return f"Risk ${risk_money:,.0f} → {sign}${abs(gained):,.0f}"
