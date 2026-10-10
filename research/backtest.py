"""Backtest one symbol: replay its candles through the state machine, simulate every trade, print the results,
and draw a chart for each trade.

    python management.py backtest --symbol EURUSD

Steps:
    1. load_candles()   read data/<broker>/<SYMBOL>/<TF>.parquet
    2. find_setups()    replay the candles through the symbol's state machine (core/symbols.py)
    3. simulate()       follow each setup on M15: does the limit fill, then SL or TP first?
    4. results_text()   win rate, total R, drawdown, a line per year
    5. draw_charts()    an entry chart + an HTF context chart per trade, in data/charts/<SYMBOL>/

Simulation rules (kept simple on purpose):
    - the limit waits at most LIMIT_HOURS; if price reaches the TP first, the trade is skipped ("missed")
    - once filled: if a candle touches both SL and TP, count it as a loss (the safe assumption)
    - the spread on the fill candle is charged once, in R
"""
from dataclasses import dataclass

import pandas as pd

from core.candles import feed
from core.config import settings
from core.symbols import machine_for, settings_for
from research.fetch_data import candle_file
from research.plot_trades import plot_context, plot_setup

LIMIT_HOURS = 48
TIMEFRAMES = ["M15", "H1", "H4", "D1", "W1"]


@dataclass
class Trade:
    setup: object          # the Setup from the state machine
    result: str            # "win", "loss", "missed" (TP before fill), "expired" (never filled), "open"
    rr: float              # how many R the TP is worth: TP distance / SL distance
    r: float               # what the trade made in R after the spread (0 if it never filled)


# --- 1. load -----------------------------------------------------------------------------------------------

def load_candles(symbol):
    """{"m15": DataFrame, "h1": ..., "w1": ...} from data/<broker>/<SYMBOL>/<TF>.parquet"""
    return {tf.lower(): pd.read_parquet(candle_file(symbol, tf)) for tf in TIMEFRAMES}


# --- 2. replay ---------------------------------------------------------------------------------------------

def find_setups(symbol, candles):
    """Feed the candles one M15 step at a time (like live) and return the trades the bot would have placed."""
    machine = machine_for(symbol)
    steps = feed(candles["m15"], candles["h1"], candles["h4"], candles["d1"], candles["w1"])
    for now, closed_candles in steps:
        machine.update(now, closed_candles)
    return [s for s in machine.setups if s.kind == "first"]


# --- 3. simulate -------------------------------------------------------------------------------------------

def simulate(setup, m15, point=0.0):
    """Walk the M15 candles after the setup and decide what happened to the trade."""
    buy = setup.direction == "bullish"
    risk = abs(setup.entry - setup.sl)
    rr = abs(setup.tp - setup.entry) / risk
    give_up_at = setup.time + pd.Timedelta(hours=LIMIT_HOURS)

    filled = False
    for candle in m15[m15.time >= setup.time].itertuples(index=False):
        hit_entry = candle.low <= setup.entry if buy else candle.high >= setup.entry
        hit_sl = candle.low <= setup.sl if buy else candle.high >= setup.sl
        hit_tp = candle.high >= setup.tp if buy else candle.low <= setup.tp

        if not filled:
            if candle.time >= give_up_at:
                return Trade(setup, "expired", rr, 0.0)
            if hit_tp:
                return Trade(setup, "missed", rr, 0.0)
            if hit_entry:
                filled = True
                spread_r = getattr(candle, "spread", 0) * point / risk
                if hit_sl:                                       # filled and stopped on the same candle
                    return Trade(setup, "loss", rr, -1.0 - spread_r)
            continue

        if hit_sl:                                               # SL checked first: the safe assumption
            return Trade(setup, "loss", rr, -1.0 - spread_r)
        if hit_tp:
            return Trade(setup, "win", rr, rr - spread_r)
    return Trade(setup, "open", rr, 0.0)


# --- 4. results --------------------------------------------------------------------------------------------

def summarize(trades):
    """The numbers for the closed trades (wins + losses)."""
    closed = [t for t in trades if t.result in ("win", "loss")]
    if not closed:
        return dict(trades=0, win_rate=0.0, median_rr=0.0, per_trade=0.0, total_r=0.0, max_drawdown=0.0)
    r = pd.Series([t.r for t in closed])
    equity = r.cumsum()
    return dict(
        trades=len(closed),
        win_rate=sum(t.result == "win" for t in closed) / len(closed),
        median_rr=pd.Series([t.rr for t in closed]).median(),
        per_trade=r.mean(),
        total_r=r.sum(),
        max_drawdown=(equity - equity.cummax()).min(),
    )


def results_text(symbol, trades):
    """The results as text (returned, not printed, so parallel runs don't mix their lines)."""
    s = summarize(trades)
    lines = [
        f"===== {symbol} =====",
        f"setups {len(trades)} | missed (TP before fill) {sum(t.result == 'missed' for t in trades)} "
        f"| expired {sum(t.result == 'expired' for t in trades)}",
        f"trades {s['trades']} | win rate {s['win_rate']:.1%} | median RR {s['median_rr']:.2f} "
        f"| per trade {s['per_trade']:+.3f}R | total {s['total_r']:+.1f}R | max drawdown {s['max_drawdown']:.1f}R",
        "by year:",
    ]
    for year in sorted({t.setup.time.year for t in trades}):
        y = summarize([t for t in trades if t.setup.time.year == year])
        lines.append(f"  {year}: {y['trades']:3} trades | win rate {y['win_rate']:6.1%} | {y['total_r']:+6.1f}R")
    return "\n".join(lines)


# --- 5. charts ---------------------------------------------------------------------------------------------

def draw_charts(symbol, trades, candles):
    """Entry chart + linked HTF context chart for every closed trade, in data/charts/<SYMBOL>/"""
    folder = settings.BASE_DIR / "data" / "charts" / symbol
    folder.mkdir(parents=True, exist_ok=True)
    closed = [t for t in trades if t.result in ("win", "loss")]
    for n, t in enumerate(closed, start=1):
        name = f"{n:03d}_{t.setup.time:%Y-%m-%d_%H%M}_{t.result}"
        plot_context(t.setup, candles, symbol=symbol).write_html(folder / f"{name}_context.html",
                                                                  include_plotlyjs="cdn")
        plot_setup(t.setup, candles["m15"], symbol=symbol, outcome=t.result, r_result=t.r,
                   context_link=f"{name}_context.html").write_html(folder / f"{name}.html", include_plotlyjs="cdn")
    return f"{len(closed)} charts in {folder}"


# --- all together --------------------------------------------------------------------------------------------

def run_backtest(symbol, charts=True):
    """Backtest one symbol. Returns (trades, results text)."""
    candles = load_candles(symbol)
    point = settings_for(symbol)["point"]
    setups = find_setups(symbol, candles)
    trades = [simulate(s, candles["m15"], point) for s in setups]
    text = results_text(symbol, trades)
    if charts:
        text += "\n" + draw_charts(symbol, trades, candles)
    return trades, text


def backtest_text(symbol, charts=True):
    """Just the results text. Used by `backtest_all`: each process runs this for one symbol."""
    return run_backtest(symbol, charts)[1]
