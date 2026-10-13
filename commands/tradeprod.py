from core.symbols import SYMBOL_SETTINGS
from live.bot import run_in_one_process

help = "Run the live bot for every symbol in a single process, for small servers like a 1 GB VPS. Dry run unless --trade."


def add_arguments(parser):
    parser.add_argument("--symbol", action="append", choices=sorted(SYMBOL_SETTINGS),
                        help="symbol to run (repeat for more). Default: every symbol in core/symbols.py")
    parser.add_argument("--trade", action="store_true", help="place real orders on the logged-in account")
    parser.add_argument("--risk", type=float, default=0.5, help="percent of the balance risked per trade")
    parser.add_argument("--server", help="MT5 server. Default: ACCOUNT_SERVER from .env")


def handle(args):
    run_in_one_process(args.symbol or sorted(SYMBOL_SETTINGS), trade=args.trade, risk_percent=args.risk,
                       server=args.server)
