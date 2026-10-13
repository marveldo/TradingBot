import sys

from .config import settings


def load_mt5(platform: str = sys.platform):
    if platform == "win32":
        import MetaTrader5
        return MetaTrader5
    from .mt5_remote import RemoteMT5
    return RemoteMT5(settings.MT5_HOST, settings.MT5_PORT)


mt5 = load_mt5()



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

    def select_symbol(self, symbol: str) -> None:
        if not mt5.symbol_select(symbol, True):
            raise Exception(f"Failed to select {symbol} in Market Watch, Error code: {mt5.last_error()}")

    def copy_rates_from_pos(self, symbol: str, timeframe_str: str, start: int, count: int):
        rates = mt5.copy_rates_from_pos(symbol, self.get_mt5_timeframe(timeframe_str), start, count)
        if rates is None or len(rates) == 0:
            raise Exception(f"Failed to copy {timeframe_str} rates for {symbol}, Error code: {mt5.last_error()}")
        return rates

    def symbol_info(self, symbol: str):
        info = mt5.symbol_info(symbol)
        if info is None:
            raise Exception(f"Failed to get symbol info for {symbol}, Error code: {mt5.last_error()}")
        return info

    def account_balance(self) -> float:
        account = mt5.account_info()
        if account is None:
            raise Exception(f"Failed to get account info, Error code: {mt5.last_error()}")
        return account.balance

    def place_limit_order(self, symbol: str, buy: bool, volume: float, price: float, sl: float, tp: float,
                          expiration: int, magic: int, comment: str) -> int:
        request = dict(
            action=mt5.TRADE_ACTION_PENDING,
            symbol=symbol,
            volume=volume,
            type=mt5.ORDER_TYPE_BUY_LIMIT if buy else mt5.ORDER_TYPE_SELL_LIMIT,
            price=price,
            sl=sl,
            tp=tp,
            type_time=mt5.ORDER_TIME_SPECIFIED,
            expiration=expiration,
            type_filling=mt5.ORDER_FILLING_RETURN,
            magic=magic,
            comment=comment,
        )
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise Exception(f"Order rejected for {symbol}: {result}, Error code: {mt5.last_error()}")
        return result.order

    def pending_orders(self, symbol: str, magic: int) -> list:
        orders = mt5.orders_get(symbol=symbol)
        if orders is None and mt5.last_error()[0] != mt5.RES_S_OK:
            raise Exception(f"Failed to get pending orders for {symbol}, Error code: {mt5.last_error()}")
        return [order for order in orders or () if order.magic == magic]

    def is_order_pending(self, ticket: int) -> bool:
        return bool(mt5.orders_get(ticket=ticket))

    def cancel_order(self, ticket: int) -> None:
        result = mt5.order_send(dict(action=mt5.TRADE_ACTION_REMOVE, order=ticket))
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            raise Exception(f"Failed to cancel order {ticket}: {result}, Error code: {mt5.last_error()}")

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
