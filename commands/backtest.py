from core.symbols import SYMBOL_SETTINGS
from research.backtest import run_backtest

help = "Backtest the strategy on saved candles: print win rate + total R and draw a chart per trade."


def add_arguments(parser):
    parser.add_argument("--symbol", action="append", choices=sorted(SYMBOL_SETTINGS),
                        help="symbol to test (repeat for more). Default: every symbol in core/symbols.py")
    parser.add_argument("--no-charts", action="store_true", help="skip the charts (faster)")


def handle(args):
    for symbol in args.symbol or sorted(SYMBOL_SETTINGS):
        trades, text = run_backtest(symbol, charts=not args.no_charts)
        print(text, "\n")
