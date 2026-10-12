import logging
import math
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import pandas as pd

from core.candles import feed
from core.config import settings
from core.mt5_client import get_mt5_client
from core.symbols import machine_for
from research.fetch_data import convert_to_dataframe

WARMUP_BARS = {"M15": 25000, "H1": 8000, "H4": 3000, "D1": 2000, "W1": 400}
LIMIT_HOURS = 48
MAGIC = 20261010
CHECK_EVERY_SECONDS = 10

log = logging.getLogger("live")


def closed_bars(client, symbol, timeframe, count):
    return convert_to_dataframe(client.copy_rates_from_pos(symbol, timeframe, 1, count))


def replay(machine, bars):
    setups = []
    for now, closed in feed(bars["M15"], bars["H1"], bars["H4"], bars["D1"], bars["W1"]):
        setups += machine.update(now, closed)
    return setups


def still_open(setup, m15):
    now = m15.time.iloc[-1] + pd.Timedelta(minutes=15)
    if now >= setup.time + pd.Timedelta(hours=LIMIT_HOURS):
        return False
    later = m15[m15.time >= setup.time]
    if setup.direction == "bullish":
        return not (later.low <= setup.entry).any() and not (later.high >= setup.tp).any()
    return not (later.high >= setup.entry).any() and not (later.low <= setup.tp).any()


def warm_up(client, symbol):
    client.select_symbol(symbol)
    bars = {tf: closed_bars(client, symbol, tf, count) for tf, count in WARMUP_BARS.items()}
    machine = machine_for(symbol)
    setups = replay(machine, bars)
    last_seen = {tf: df.time.iloc[-1] for tf, df in bars.items()}
    open_setups = [setup for setup in setups if still_open(setup, bars["M15"])]
    return machine, last_seen, open_setups


def only_new(df, last_time):
    return df[df.time > last_time]


def new_bars(client, symbol, last_seen):
    bars = {tf: only_new(closed_bars(client, symbol, tf, 5), last_seen[tf]) for tf in WARMUP_BARS}
    for tf, df in bars.items():
        if len(df):
            last_seen[tf] = df.time.iloc[-1]
    return bars


def lot_size(info, entry, sl, risk_money):
    loss_per_lot = abs(entry - sl) / info.trade_tick_size * info.trade_tick_value
    steps = math.floor(risk_money / loss_per_lot / info.volume_step)
    volume = round(steps * info.volume_step, 8)
    if volume < info.volume_min:
        return 0.0
    return min(volume, info.volume_max)


def order_prices(setup, digits):
    expires = setup.time + pd.Timedelta(hours=LIMIT_HOURS)
    return dict(buy=setup.direction == "bullish", price=round(setup.entry, digits), sl=round(setup.sl, digits),
                tp=round(setup.tp, digits), expiration=int(expires.timestamp()))


def place_order(client, setup, symbol, risk_percent):
    info = client.symbol_info(symbol)
    risk_money = client.account_balance() * risk_percent / 100
    volume = lot_size(info, setup.entry, setup.sl, risk_money)
    if volume == 0:
        log.warning(f"risk {risk_money:.2f} is below the minimum lot, skipped")
        return None
    try:
        ticket = client.place_limit_order(symbol, volume=volume, magic=MAGIC, comment="smc-bot",
                                          **order_prices(setup, info.digits))
    except Exception as error:
        log.error(str(error))
        return None
    log.info(f"order {ticket} placed, {volume} lots")
    return ticket


def tp_reached(setup, candle):
    if setup.direction == "bullish":
        return candle.high >= setup.tp
    return candle.low <= setup.tp


def cancel_missed_orders(client, waiting, m15):
    for ticket, setup in list(waiting.items()):
        if not client.is_order_pending(ticket):
            del waiting[ticket]
            continue
        if any(tp_reached(setup, candle) for candle in m15.itertuples(index=False)):
            client.cancel_order(ticket)
            log.info(f"order {ticket} cancelled, TP reached before the fill")
            del waiting[ticket]


def describe(symbol, setup):
    return (f"{symbol} {setup.direction} limit {setup.entry:g} SL {setup.sl:g} TP {setup.tp:g} "
            f"({setup.poi_tf.upper()} {setup.poi.kind} POI, M15 {setup.entry_ob.kind})")


def setup_logging(symbol):
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s {symbol} %(message)s", force=True,
                        handlers=[logging.StreamHandler(), logging.FileHandler(f"logs/live_{symbol}.log")])


def handle_setup(client, symbol, setup, trade, risk_percent, waiting, label="SETUP"):
    log.info(f"{label} {describe(symbol, setup)}")
    if trade:
        ticket = place_order(client, setup, symbol, risk_percent)
        if ticket:
            waiting[ticket] = setup


def run_symbol(symbol, trade=False, risk_percent=0.5, server=None):
    setup_logging(symbol)
    client = get_mt5_client(server or settings.ACCOUNT_SERVER)
    log.info(f"{'TRADING' if trade else 'DRY RUN'}, risk {risk_percent}% per trade, warming up")
    machine, last_seen, open_setups = warm_up(client, symbol)
    log.info(f"ready, bias {machine.bias}, {len(open_setups)} setup(s) from the warm-up still open")
    waiting = {}
    for setup in open_setups:
        handle_setup(client, symbol, setup, trade, risk_percent, waiting, label="STILL OPEN")

    while True:
        bars = new_bars(client, symbol, last_seen)
        if len(bars["M15"]):
            cancel_missed_orders(client, waiting, bars["M15"])
            for setup in replay(machine, bars):
                handle_setup(client, symbol, setup, trade, risk_percent, waiting)
        time.sleep(CHECK_EVERY_SECONDS)


def run(symbols, trade=False, risk_percent=0.5, server=None):
    with ProcessPoolExecutor(max_workers=len(symbols)) as pool:
        jobs = [pool.submit(run_symbol, symbol, trade, risk_percent, server) for symbol in symbols]
        for job in as_completed(jobs):
            job.result()
