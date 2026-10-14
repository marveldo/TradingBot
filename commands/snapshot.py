import webbrowser

from core.config import settings
from core.mt5_client import get_mt5_client
from core.symbols import SYMBOL_SETTINGS
from live.snapshot import bot_view, view_lines
from research.plot_trades import plot_now

help = "Show the bot's view right now (bias chain, structure, live POIs): text + a chart in data/charts/now/."


def add_arguments(parser):
    parser.add_argument("--symbol", action="append", choices=sorted(SYMBOL_SETTINGS), required=True,
                        help="symbol to look at (repeat for more)")
    parser.add_argument("--server", help="MT5 server. Default: ACCOUNT_SERVER from .env")
    parser.add_argument("--no-chart", action="store_true", help="text only (e.g. on the VPS, which has no browser)")


def handle(args):
    client = get_mt5_client(args.server or settings.ACCOUNT_SERVER)
    folder = settings.BASE_DIR / "data" / "charts" / "now"
    for symbol in args.symbol:
        view, bars, now = bot_view(client, symbol)
        print(f"===== {symbol} at {now:%Y-%m-%d %H:%M} UTC =====")
        print("\n".join(view_lines(view)))
        if args.no_chart:
            continue
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{symbol}.html"
        plot_now(view, bars, now, symbol=symbol).write_html(path, include_plotlyjs="cdn")
        print(f"chart: {path}")
        webbrowser.open(path.as_uri())
