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
RETRY_SECONDS = 10



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


def history(client, symbol, timeframe, count, attempts=5):
    for attempt in range(attempts):
        try:
            return closed_bars(client, symbol, timeframe, count)
        except Exception as error:
            if attempt == attempts - 1:
                raise
            logger(symbol).warning(f"{timeframe} history not ready ({error}), retrying")
            time.sleep(RETRY_SECONDS)


def warm_up(client, symbol):
    client.select_symbol(symbol)
    bars = {tf: history(client, symbol, tf, count) for tf, count in WARMUP_BARS.items()}
    machine = machine_for(symbol)
    setups = replay(machine, bars)
    last_seen = {tf: df.time.iloc[-1] for tf, df in bars.items()}
    open_setups = [setup for setup in setups if still_open(setup, bars["M15"])]
    return machine, last_seen, open_setups


def only_new(df, last_time):
    return df[df.time > last_time]


def bars_since(client, symbol, timeframe, last_time):
    count = 5
    while True:
        df = closed_bars(client, symbol, timeframe, count)
        if df.time.iloc[0] <= last_time or count >= WARMUP_BARS[timeframe]:
            return only_new(df, last_time)
        count *= 2


def new_bars(client, symbol, last_seen):
    m15 = bars_since(client, symbol, "M15", last_seen["M15"])
    if len(m15) == 0:
        return {"M15": m15}
    higher = {tf: bars_since(client, symbol, tf, last_seen[tf]) for tf in WARMUP_BARS if tf != "M15"}
    return {"M15": m15, **higher}


def mark_seen(last_seen, bars):
    for tf, df in bars.items():
        if len(df):
            last_seen[tf] = df.time.iloc[-1]


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
    log = logger(symbol)
    try:
        info = client.symbol_info(symbol)
        risk_money = client.account_balance() * risk_percent / 100
        volume = lot_size(info, setup.entry, setup.sl, risk_money)
        if volume == 0:
            log.warning(f"risk {risk_money:.2f} is below the minimum lot, skipped")
            return None
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


def cancel_missed_orders(client, symbol, waiting, m15):
    for ticket, setup in list(waiting.items()):
        if not client.is_order_pending(ticket):
            del waiting[ticket]
            continue
        if any(tp_reached(setup, candle) for candle in m15.itertuples(index=False)):
            client.cancel_order(ticket)
            logger(symbol).info(f"order {ticket} cancelled, TP reached before the fill")
            del waiting[ticket]


def describe(symbol, setup):
    return (f"{symbol} {setup.direction} limit {setup.entry:g} SL {setup.sl:g} TP {setup.tp:g} "
            f"({setup.poi_tf.upper()} {setup.poi.kind} POI, M15 {setup.entry_ob.kind})")


def logger(symbol):
    return logging.getLogger(f"live.{symbol}")


def setup_logging(symbol):
    log = logger(symbol)
    formatter = logging.Formatter(f"%(asctime)s {symbol} %(message)s")
    log.handlers = [logging.StreamHandler(), logging.FileHandler(f"logs/live_{symbol}.log")]
    for handler in log.handlers:
        handler.setFormatter(formatter)
    log.setLevel(logging.INFO)
    log.propagate = False


def handle_setup(client, symbol, setup, trade, risk_percent, waiting, label="SETUP"):
    logger(symbol).info(f"{label} {describe(symbol, setup)}")
    if trade:
        ticket = place_order(client, setup, symbol, risk_percent)
        if ticket:
            waiting[ticket] = setup


def existing_ticket(orders, setup, digits):
    prices = order_prices(setup, digits)
    wanted = (prices["price"], prices["sl"], prices["tp"])
    for order in orders:
        if tuple(round(value, digits) for value in (order.price_open, order.sl, order.tp)) == wanted:
            return order.ticket
    return None


def resume_open_setups(client, symbol, open_setups, trade, risk_percent, waiting):
    orders = client.pending_orders(symbol, MAGIC) if trade else []
    digits = client.symbol_info(symbol).digits
    for setup in open_setups:
        ticket = existing_ticket(orders, setup, digits)
        if ticket:
            logger(symbol).info(f"ADOPTED order {ticket} {describe(symbol, setup)}")
            waiting[ticket] = setup
        else:
            handle_setup(client, symbol, setup, trade, risk_percent, waiting, label="STILL OPEN")


def check(client, symbol, machine, last_seen, waiting, trade, risk_percent):
    bars = new_bars(client, symbol, last_seen)
    if len(bars["M15"]) == 0:
        return
    cancel_missed_orders(client, symbol, waiting, bars["M15"])
    setups = replay(machine, bars)
    mark_seen(last_seen, bars)
    for setup in setups:
        handle_setup(client, symbol, setup, trade, risk_percent, waiting)
    candle = bars["M15"].iloc[-1]
    logger(symbol).info(f"M15 {candle.time:%Y-%m-%d %H:%M} closed at {candle.close:g} | bias {machine.bias} "
             f"| {len(bars['M15'])} new bar(s) | {len(waiting)} order(s) waiting")


def reconnect(client, symbol):
    try:
        if not client.is_connected() and client.connect():
            logger(symbol).info("reconnected")
    except Exception as error:
        logger(symbol).error(f"reconnect failed: {error}")


def start_symbol(client, symbol, trade, risk_percent):
    setup_logging(symbol)
    log = logger(symbol)
    log.info(f"{'TRADING' if trade else 'DRY RUN'}, risk {risk_percent}% per trade, warming up")
    machine, last_seen, open_setups = warm_up(client, symbol)
    log.info(f"ready, bias {machine.bias}, {len(open_setups)} setup(s) from the warm-up still open")
    waiting = {}
    resume_open_setups(client, symbol, open_setups, trade, risk_percent, waiting)
    return machine, last_seen, waiting


def check_safely(client, symbol, machine, last_seen, waiting, trade, risk_percent):
    try:
        check(client, symbol, machine, last_seen, waiting, trade, risk_percent)
    except Exception as error:
        logger(symbol).error(f"check failed: {error}")
        reconnect(client, symbol)


def run_symbol(symbol, trade=False, risk_percent=0.5, server=None):
    client = get_mt5_client(server or settings.ACCOUNT_SERVER)
    machine, last_seen, waiting = start_symbol(client, symbol, trade, risk_percent)
    while True:
        check_safely(client, symbol, machine, last_seen, waiting, trade, risk_percent)
        time.sleep(CHECK_EVERY_SECONDS)


def run(symbols, trade=False, risk_percent=0.5, server=None):
    with ProcessPoolExecutor(max_workers=len(symbols)) as pool:
        jobs = [pool.submit(run_symbol, symbol, trade, risk_percent, server) for symbol in symbols]
        for job in as_completed(jobs):
            job.result()


def run_in_one_process(symbols, trade=False, risk_percent=0.5, server=None):
    client = get_mt5_client(server or settings.ACCOUNT_SERVER)
    states = {symbol: start_symbol(client, symbol, trade, risk_percent) for symbol in symbols}
    while True:
        for symbol, (machine, last_seen, waiting) in states.items():
            check_safely(client, symbol, machine, last_seen, waiting, trade, risk_percent)
        time.sleep(CHECK_EVERY_SECONDS)
