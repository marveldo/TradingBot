import logging
import math
import time
from concurrent.futures import ProcessPoolExecutor, as_completed

import MetaTrader5 as mt5
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
    rates = mt5.copy_rates_from_pos(symbol, client.get_mt5_timeframe(timeframe), 1, count)
    if rates is None or len(rates) == 0:
        raise RuntimeError(f"no {timeframe} bars for {symbol}: {mt5.last_error()}")
    return convert_to_dataframe(rates)


def replay(machine, bars):
    setups = []
    for now, closed in feed(bars["M15"], bars["H1"], bars["H4"], bars["D1"], bars["W1"]):
        setups += machine.update(now, closed)
    return setups


def warm_up(client, symbol):
    mt5.symbol_select(symbol, True)
    bars = {tf: closed_bars(client, symbol, tf, count) for tf, count in WARMUP_BARS.items()}
    machine = machine_for(symbol)
    replay(machine, bars)
    last_seen = {tf: df.time.iloc[-1] for tf, df in bars.items()}
    return machine, last_seen


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


def order_request(setup, symbol, volume, digits):
    buy = setup.direction == "bullish"
    expires = setup.time + pd.Timedelta(hours=LIMIT_HOURS)
    return dict(
        action=mt5.TRADE_ACTION_PENDING,
        symbol=symbol,
        volume=volume,
        type=mt5.ORDER_TYPE_BUY_LIMIT if buy else mt5.ORDER_TYPE_SELL_LIMIT,
        price=round(setup.entry, digits),
        sl=round(setup.sl, digits),
        tp=round(setup.tp, digits),
        type_time=mt5.ORDER_TIME_SPECIFIED,
        expiration=int(expires.timestamp()),
        type_filling=mt5.ORDER_FILLING_RETURN,
        magic=MAGIC,
        comment="smc-bot",
    )


def place_order(setup, symbol, risk_percent):
    info = mt5.symbol_info(symbol)
    risk_money = mt5.account_info().balance * risk_percent / 100
    volume = lot_size(info, setup.entry, setup.sl, risk_money)
    if volume == 0:
        log.warning(f"{symbol}: risk {risk_money:.2f} is below the minimum lot, skipped")
        return None
    result = mt5.order_send(order_request(setup, symbol, volume, info.digits))
    if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
        log.error(f"{symbol}: order rejected {result} {mt5.last_error()}")
        return None
    log.info(f"{symbol}: order {result.order} placed, {volume} lots")
    return result.order


def tp_reached(setup, candle):
    if setup.direction == "bullish":
        return candle.high >= setup.tp
    return candle.low <= setup.tp


def cancel_missed_orders(waiting, symbol, m15):
    for ticket, setup in list(waiting.items()):
        if not mt5.orders_get(ticket=ticket):
            del waiting[ticket]
            continue
        if any(tp_reached(setup, candle) for candle in m15.itertuples(index=False)):
            mt5.order_send(dict(action=mt5.TRADE_ACTION_REMOVE, order=ticket))
            log.info(f"{symbol}: order {ticket} cancelled, TP reached before the fill")
            del waiting[ticket]


def describe(symbol, setup):
    return (f"{symbol} {setup.direction} limit {setup.entry:g} SL {setup.sl:g} TP {setup.tp:g} "
            f"({setup.poi_tf.upper()} {setup.poi.kind} POI, M15 {setup.entry_ob.kind})")


def setup_logging(symbol):
    logging.basicConfig(level=logging.INFO, format=f"%(asctime)s {symbol} %(message)s", force=True,
                        handlers=[logging.StreamHandler(), logging.FileHandler(f"logs/live_{symbol}.log")])


def run_symbol(symbol, trade=False, risk_percent=0.5, server=None):
    setup_logging(symbol)
    client = get_mt5_client(server or settings.ACCOUNT_SERVER)
    log.info(f"{'TRADING' if trade else 'DRY RUN'}, risk {risk_percent}% per trade, warming up")
    machine, last_seen = warm_up(client, symbol)
    log.info(f"ready, bias {machine.bias}")
    waiting = {}

    while True:
        bars = new_bars(client, symbol, last_seen)
        if len(bars["M15"]):
            cancel_missed_orders(waiting, symbol, bars["M15"])
            for setup in replay(machine, bars):
                log.info(f"SETUP {describe(symbol, setup)}")
                if trade:
                    ticket = place_order(setup, symbol, risk_percent)
                    if ticket:
                        waiting[ticket] = setup
        time.sleep(CHECK_EVERY_SECONDS)


def run(symbols, trade=False, risk_percent=0.5, server=None):
    with ProcessPoolExecutor(max_workers=len(symbols)) as pool:
        jobs = [pool.submit(run_symbol, symbol, trade, risk_percent, server) for symbol in symbols]
        for job in as_completed(jobs):
            job.result()
