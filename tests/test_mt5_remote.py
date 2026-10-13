import sys
import threading
from collections import namedtuple
from types import ModuleType

import numpy as np
import pytest
from rpyc.core import SlaveService
from rpyc.utils.server import ThreadedServer

from core.mt5_remote import RemoteMT5

SymbolInfo = namedtuple("SymbolInfo", "name digits point")
OrderSendResult = namedtuple("OrderSendResult", "retcode order request")
TradeRequest = namedtuple("TradeRequest", "symbol price")


def fake_metatrader5():
    module = ModuleType("MetaTrader5")
    module.TIMEFRAME_H4 = 16388
    module.symbol_info = lambda symbol: SymbolInfo(symbol, 5, 0.00001)
    module.last_error = lambda: (1, "Success")
    module.orders_get = lambda symbol: (SymbolInfo(symbol, 5, 0.00001),)
    module.copy_rates_from_pos = lambda symbol, timeframe, start, count: np.array(
        [(1_700_000_000 + i, 1.1, 1.2, 1.0, 1.15) for i in range(count)],
        dtype=[("time", "<i8"), ("open", "<f8"), ("high", "<f8"), ("low", "<f8"), ("close", "<f8")])
    module.order_send = lambda request: OrderSendResult(10009, 42, TradeRequest(request["symbol"], request["price"]))
    return module


@pytest.fixture
def remote(monkeypatch):
    monkeypatch.setitem(sys.modules, "MetaTrader5", fake_metatrader5())
    server = ThreadedServer(SlaveService, hostname="localhost", port=0, protocol_config={"allow_all_attrs": True})
    threading.Thread(target=server.start, daemon=True).start()
    client = RemoteMT5("localhost", server.port)
    yield client
    client.connection.close()
    server.close()


def test_constants_come_back_as_plain_values(remote):
    assert remote.TIMEFRAME_H4 == 16388


def test_named_results_become_attribute_objects(remote):
    info = remote.symbol_info("EURUSD")
    assert (info.name, info.digits, info.point) == ("EURUSD", 5, 0.00001)


def test_tuples_stay_tuples(remote):
    assert remote.last_error()[0] == 1
    assert remote.orders_get(symbol="EURUSD")[0].name == "EURUSD"


def test_rates_arrive_as_a_local_numpy_array(remote):
    rates = remote.copy_rates_from_pos("EURUSD", 16388, 1, 3)
    assert isinstance(rates, np.ndarray) and len(rates) == 3 and rates["close"][0] == 1.15


def test_order_requests_are_sent_as_real_dicts(remote):
    result = remote.order_send({"symbol": "EURUSD", "price": 1.1})
    assert (result.retcode, result.order, result.request.symbol) == (10009, 42, "EURUSD")
