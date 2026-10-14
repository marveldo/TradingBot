"""The bot's view of a symbol right now: the same warm-up as live/bot.py, then StateMachine.snapshot().

    view, bars, now = bot_view(client, "XAUUSD")
    print("\n".join(view_lines(view)))
"""
import time

import pandas as pd

from core.mt5_client import mt5
from core.symbols import machine_for
from live.bot import load_history, replay

M15 = pd.Timedelta(minutes=15)
SYNC_ATTEMPTS = 12
SYNC_WAIT_SECONDS = 5

STEP = {"FOLLOWING": "following {bias_in}",
        "AT_OPPOSING_OB": "PAUSED at an opposing OB, waiting for a {ltf} CHoCH",
        "FLIPPED": "FLIPPED {bias_in} -> {bias_out} on a {ltf} CHoCH"}


def is_behind(last_close, last_tick):
    """A whole M15 candle has closed since the last one we got: MT5 is still downloading (e.g. just started)."""
    return last_tick - last_close >= M15


def fresh_history(client, symbol):
    for _ in range(SYNC_ATTEMPTS):
        bars = load_history(client, symbol)
        last_close = bars["M15"].time.iloc[-1] + M15
        last_tick = pd.Timestamp(mt5.symbol_info_tick(symbol).time, unit="s", tz="UTC")
        if not is_behind(last_close, last_tick):
            return bars
        print(f"{symbol}: MT5 candles end {last_close:%Y-%m-%d %H:%M}, last tick {last_tick:%H:%M}, waiting for MT5")
        time.sleep(SYNC_WAIT_SECONDS)
    raise RuntimeError(f"{symbol}: MT5 history still behind the last tick after {SYNC_ATTEMPTS} tries")


def bot_view(client, symbol):
    """Warm up like the live bot and return (snapshot, bars by lower-case TF, the time the last M15 closed)."""
    bars = fresh_history(client, symbol)
    machine = machine_for(symbol)
    replay(machine, bars)
    now = bars["M15"].time.iloc[-1] + M15
    return machine.snapshot(), {tf.lower(): df for tf, df in bars.items()}, now


def price(value):
    return None if value is None else f"{value:g}"


def zone(ob):
    return f"{ob['direction']} {ob['kind']} OB {ob['low']:g}-{ob['high']:g} ({ob['time']:%Y-%m-%d %H:%M})"


def view_lines(view):
    """The snapshot as text: the final bias, each step of the chain, and each TF's structure."""
    lines = [f"bias: {view['bias']}"]
    for level in view["levels"]:
        step = STEP[level["state"]].format(bias_in=level["bias_in"], bias_out=level["bias_out"],
                                           ltf=level["ltf"].upper())
        lines.append(f"  {level['htf'].upper()}->{level['ltf'].upper()}: {step}")
        if level["opposing_ob"]:
            lines.append(f"      opposing {zone(level['opposing_ob'])}")
    for tf in ("w1", "d1", "h4", "h1", "m15"):
        st = view["structure"].get(tf)
        if st:
            lines.append(f"  {tf.upper():>3} trend {st['trend']} | BOS level {price(st['bos_level'])} | "
                         f"CHoCH level {price(st['choch_level'])}")
    return lines
