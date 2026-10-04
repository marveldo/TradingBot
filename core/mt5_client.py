import MetaTrader5 as mt5
from .config import settings



class MT5Client :

    def __init__(self, login_id: int, password: str, server: str, broker: str):
        self.login_id = login_id
        self.password = password
        self.server = server
        self.broker = broker

    def connect(self) -> bool:
        if not mt5.initialize():
            print("Failed to initialize MT5")
            return False

        authorized = mt5.login(self.login_id, self.password, self.server)
        if not authorized:
            print(f"Failed to connect to MT5 server: {self.server} , Error code: {mt5.last_error()}")
            mt5.shutdown()
            return False

        print(f"Connected to MT5 server: {self.server}")
        return True

    def is_connected(self) -> bool:
        connection_info = mt5.terminal_info()
        if connection_info is not None:
            return connection_info.connected
        return False

    def get_symbols(self):
        symbols = mt5.symbols_get()
        if symbols is None:
            print("Failed to retrieve symbols")
            raise Exception(f"Failed to retrieve symbols from MT5 server Error code: {mt5.last_error()}")
        return symbols

    def copy_rates_range(self, symbol: str, timeframe: int, date_from, date_to):
        rates = mt5.copy_rates_range(symbol, timeframe, date_from, date_to)
        if rates is None:
            raise Exception(f"Failed to copy rates for {symbol} from {date_from} to {date_to} , Error code: {mt5.last_error()}")
        elif len(rates) == 0:
            raise Exception(f"No rates data available for {symbol} from {date_from} to {date_to}")
        return rates

    def get_mt5_timeframe(self, timeframe_str: str) -> int:
        timeframe_attr = f"TIMEFRAME_{timeframe_str}"
        if not hasattr(mt5, timeframe_attr):
            raise ValueError(f"Invalid timeframe: {timeframe_str}")
        return getattr(mt5, timeframe_attr)

    def disconnect(self) -> None:
        mt5.shutdown()
        print("Disconnected from MT5 server")

mt5_client = None


def get_mt5_client(server :str) -> MT5Client:
    global mt5_client
    if mt5_client is None :
        mt5_client =  MT5Client(
            login_id=settings.ACCOUNT_LOGIN_ID,
            password=settings.ACCOUNT_PASSWORD,
            server= server,
            broker=settings.ACCOUNT_BROKER
        )
    if server != mt5_client.server:
       raise ValueError(f"MT5 client is already connected to a different server: {mt5_client.server}")
    if not mt5_client.is_connected():
        if not mt5_client.connect():
            raise ConnectionError(f"Failed to connect to MT5 server: {server}")
    return mt5_client
