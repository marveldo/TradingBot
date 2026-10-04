from research.fetch_data import fetch_data

help = "Pull W1/H4/H1/M15 candles from MT5 for a broker server and save them as parquet."


def add_arguments(parser):
    parser.add_argument("--server", required=True, help="MT5 server name, e.g. Deriv-Demo")


def handle(args):
    fetch_data(args.server)
