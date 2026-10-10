from concurrent.futures import ProcessPoolExecutor, as_completed

from core.symbols import SYMBOL_SETTINGS
from research.backtest import backtest_text

help = "Backtest every symbol in core/symbols.py at the same time, one process per symbol."


def add_arguments(parser):
    parser.add_argument("--no-charts", action="store_true", help="skip the charts (faster)")


def handle(args):
    symbols = sorted(SYMBOL_SETTINGS)
    print(f"backtesting {', '.join(symbols)} in parallel...", flush=True)
    # one process per symbol; each returns its results as text, printed whole as soon as it finishes
    with ProcessPoolExecutor(max_workers=len(symbols)) as pool:
        jobs = [pool.submit(backtest_text, symbol, not args.no_charts) for symbol in symbols]
        for job in as_completed(jobs):
            print(job.result(), "\n", flush=True)
