from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

import core.mt5_client as client_module
from core.config import settings
from core.mt5_client import MT5Client, get_mt5_client

SERVER = "Deriv-Demo"


@pytest.fixture(autouse=True)
def reset_singleton(monkeypatch):
    monkeypatch.setattr(client_module, "mt5_client", None)


def fake_mt5(monkeypatch, **funcs):
    """Swap the MetaTrader5 module used by core.mt5_client for a fake with the given functions."""
    fake = SimpleNamespace(last_error=lambda: (-1, "fake error"), **funcs)
    monkeypatch.setattr(client_module, "mt5", fake)
    return fake


def make_client():
    return MT5Client(login_id=1, password="x", server=SERVER, broker="deriv")


def utc(*args):
    return datetime(*args, tzinfo=timezone.utc)


# --- is_connected -------------------------------------------------------------

def test_is_connected_false_when_terminal_not_attached(monkeypatch):
    fake_mt5(monkeypatch, terminal_info=lambda: None)
    assert make_client().is_connected() is False


def test_is_connected_false_when_terminal_offline(monkeypatch):
    fake_mt5(monkeypatch, terminal_info=lambda: SimpleNamespace(connected=False))
    assert make_client().is_connected() is False


def test_is_connected_true_when_terminal_online(monkeypatch):
    fake_mt5(monkeypatch, terminal_info=lambda: SimpleNamespace(connected=True))
    assert make_client().is_connected() is True


def test_connect_fails_when_initialize_fails(monkeypatch):
    fake_mt5(monkeypatch, initialize=lambda: False)
    assert make_client().connect() is False


def test_connect_fails_and_shuts_down_when_login_fails(monkeypatch):
    calls = []
    fake_mt5(
        monkeypatch,
        initialize=lambda: True,
        login=lambda *args, **kwargs: False,
        shutdown=lambda: calls.append("shutdown"),
    )
    assert make_client().connect() is False
    assert calls == ["shutdown"]


def test_connect_succeeds(monkeypatch):
    fake_mt5(monkeypatch, initialize=lambda: True, login=lambda *args, **kwargs: True)
    assert make_client().connect() is True

def test_get_symbols_raises_when_mt5_returns_none(monkeypatch):
    fake_mt5(monkeypatch, symbols_get=lambda: None)
    with pytest.raises(Exception, match="Failed to retrieve symbols"):
        make_client().get_symbols()


def test_get_symbols_returns_symbols(monkeypatch):
    symbols = (SimpleNamespace(name="EURUSD"),)
    fake_mt5(monkeypatch, symbols_get=lambda: symbols)
    assert make_client().get_symbols() == symbols

def test_copy_rates_range_returns_rates(monkeypatch):
    rates = np.array([(1, 1.1)], dtype=[("time", "i8"), ("close", "f8")])
    fake_mt5(monkeypatch, copy_rates_range=lambda *args: rates)
    assert make_client().copy_rates_range("EURUSD", 0, utc(2026, 9, 1), utc(2026, 9, 8)) is rates


def test_copy_rates_range_raises_when_call_fails(monkeypatch):
    fake_mt5(monkeypatch, copy_rates_range=lambda *args: None)
    with pytest.raises(Exception, match="Failed to copy rates for EURUSD.*fake error"):
        make_client().copy_rates_range("EURUSD", 0, utc(2026, 9, 1), utc(2026, 9, 8))


def test_copy_rates_range_raises_when_no_bars(monkeypatch):
    empty = np.array([], dtype=[("time", "i8"), ("close", "f8")])
    fake_mt5(monkeypatch, copy_rates_range=lambda *args: empty)
    with pytest.raises(Exception, match="No rates data available for EURUSD"):
        make_client().copy_rates_range("EURUSD", 0, utc(2026, 9, 1), utc(2026, 9, 8))


# --- get_mt5_client (singleton) -----------------------------------------------

def fake_terminal(monkeypatch, login_ok=True):
    """Fake MT5 that is offline until initialize() runs; counts initialize() calls."""
    state = {"connected": False, "initialize_calls": 0}

    def initialize():
        state["initialize_calls"] += 1
        state["connected"] = True
        return True

    fake_mt5(
        monkeypatch,
        initialize=initialize,
        login=lambda *args, **kwargs: login_ok,
        shutdown=lambda: state.update(connected=False),
        terminal_info=lambda: SimpleNamespace(connected=state["connected"]),
    )
    return state


def test_get_mt5_client_connects_once_and_reuses_instance(monkeypatch):
    state = fake_terminal(monkeypatch)
    first = get_mt5_client(SERVER)
    second = get_mt5_client(SERVER)
    assert first is second
    assert state["initialize_calls"] == 1


def test_get_mt5_client_reconnects_when_connection_dropped(monkeypatch):
    state = fake_terminal(monkeypatch)
    get_mt5_client(SERVER)
    state["connected"] = False
    get_mt5_client(SERVER)
    assert state["initialize_calls"] == 2


def test_get_mt5_client_rejects_a_different_server(monkeypatch):
    fake_terminal(monkeypatch)
    get_mt5_client(SERVER)
    with pytest.raises(ValueError, match="different server"):
        get_mt5_client("Exness-Real")


def test_get_mt5_client_raises_when_login_fails(monkeypatch):
    fake_terminal(monkeypatch, login_ok=False)
    with pytest.raises(ConnectionError, match=SERVER):
        get_mt5_client(SERVER)

# --- trading helpers used by the live bot -------------------------------------

def trading_mt5(monkeypatch, retcode=10009, **funcs):
    sent = []
    fake = fake_mt5(monkeypatch, TRADE_ACTION_PENDING=5, TRADE_ACTION_REMOVE=8, ORDER_TYPE_BUY_LIMIT=2,
                    ORDER_TYPE_SELL_LIMIT=3, ORDER_TIME_SPECIFIED=2, ORDER_FILLING_RETURN=2, TRADE_RETCODE_DONE=10009,
                    order_send=lambda request: sent.append(request) or SimpleNamespace(retcode=retcode, order=777),
                    **funcs)
    return fake, sent


def test_select_symbol_raises_when_mt5_refuses(monkeypatch):
    fake_mt5(monkeypatch, symbol_select=lambda symbol, enable: False)
    with pytest.raises(Exception, match="Failed to select GBPJPY"):
        make_client().select_symbol("GBPJPY")


def test_copy_rates_from_pos_passes_the_timeframe_and_returns_rates(monkeypatch):
    calls = []
    fake_mt5(monkeypatch, TIMEFRAME_H4=16388,
             copy_rates_from_pos=lambda s, tf, start, count: calls.append((s, tf, start, count)) or np.array([1, 2]))
    assert len(make_client().copy_rates_from_pos("EURUSD", "H4", 1, 2)) == 2
    assert calls == [("EURUSD", 16388, 1, 2)]


def test_copy_rates_from_pos_raises_when_empty(monkeypatch):
    fake_mt5(monkeypatch, TIMEFRAME_H4=16388, copy_rates_from_pos=lambda *a: np.array([]))
    with pytest.raises(Exception, match="Failed to copy H4 rates for EURUSD"):
        make_client().copy_rates_from_pos("EURUSD", "H4", 1, 5)


def test_symbol_info_and_balance_raise_when_missing(monkeypatch):
    fake_mt5(monkeypatch, symbol_info=lambda s: None, account_info=lambda: None)
    with pytest.raises(Exception, match="symbol info"):
        make_client().symbol_info("EURUSD")
    with pytest.raises(Exception, match="account info"):
        make_client().account_balance()


def test_account_balance(monkeypatch):
    fake_mt5(monkeypatch, account_info=lambda: SimpleNamespace(balance=1000.0))
    assert make_client().account_balance() == 1000.0


def test_place_limit_order_sends_a_buy_limit_and_returns_the_ticket(monkeypatch):
    fake, sent = trading_mt5(monkeypatch)
    ticket = make_client().place_limit_order("EURUSD", buy=True, volume=0.5, price=1.1, sl=1.09, tp=1.12,
                                             expiration=123, magic=1, comment="smc-bot")
    assert ticket == 777
    assert sent[0]["type"] == fake.ORDER_TYPE_BUY_LIMIT and sent[0]["action"] == fake.TRADE_ACTION_PENDING
    assert (sent[0]["price"], sent[0]["sl"], sent[0]["tp"], sent[0]["volume"]) == (1.1, 1.09, 1.12, 0.5)


def test_place_limit_order_sell(monkeypatch):
    fake, sent = trading_mt5(monkeypatch)
    make_client().place_limit_order("EURUSD", buy=False, volume=0.5, price=1.1, sl=1.11, tp=1.08,
                                    expiration=123, magic=1, comment="smc-bot")
    assert sent[0]["type"] == fake.ORDER_TYPE_SELL_LIMIT


def test_place_limit_order_raises_when_rejected(monkeypatch):
    trading_mt5(monkeypatch, retcode=10016)
    with pytest.raises(Exception, match="Order rejected for EURUSD"):
        make_client().place_limit_order("EURUSD", buy=True, volume=0.5, price=1.1, sl=1.09, tp=1.12,
                                        expiration=123, magic=1, comment="smc-bot")


def test_pending_order_check_and_cancel(monkeypatch):
    fake, sent = trading_mt5(monkeypatch, orders_get=lambda ticket: [SimpleNamespace(ticket=ticket)])
    client = make_client()
    assert client.is_order_pending(777)
    client.cancel_order(777)
    assert sent == [dict(action=fake.TRADE_ACTION_REMOVE, order=777)]


def test_no_pending_order(monkeypatch):
    fake_mt5(monkeypatch, orders_get=lambda ticket: ())
    assert not make_client().is_order_pending(777)


# --- real terminal (pytest -m mt5) ----------------------------------------------

@pytest.fixture
def live_client():
    client = get_mt5_client(settings.ACCOUNT_SERVER)
    yield client
    client.disconnect()


@pytest.mark.mt5
def test_live_connects(live_client):
    assert live_client.is_connected()


@pytest.mark.mt5
def test_live_pulls_eurusd_h4(live_client):
    rates = live_client.copy_rates_range("EURUSD", client_module.mt5.TIMEFRAME_H4, utc(2026, 9, 1), utc(2026, 9, 8))
    assert len(rates) > 0


@pytest.mark.mt5
def test_live_unknown_symbol_raises(live_client):
    with pytest.raises(Exception, match="Failed to copy rates for EURUSDX"):
        live_client.copy_rates_range("EURUSDX", client_module.mt5.TIMEFRAME_H4, utc(2026, 9, 1), utc(2026, 9, 8))

# --- choosing the MT5 package per OS ------------------------------------------

def test_load_mt5_on_windows_is_the_official_package():
    module = client_module.load_mt5("win32")
    assert module.__name__ == "MetaTrader5" and hasattr(module, "TIMEFRAME_H4")


def test_load_mt5_on_linux_connects_to_the_wine_server(monkeypatch):
    import core.mt5_remote as remote_module
    monkeypatch.setattr(remote_module, "RemoteMT5", lambda host, port: SimpleNamespace(host=host, port=port))
    bridge = client_module.load_mt5("linux")
    assert (bridge.host, bridge.port) == (settings.MT5_HOST, settings.MT5_PORT)


def test_pending_orders_keeps_only_the_bots_magic(monkeypatch):
    orders = (SimpleNamespace(ticket=1, magic=7), SimpleNamespace(ticket=2, magic=99))
    fake_mt5(monkeypatch, orders_get=lambda symbol: orders, RES_S_OK=1)
    assert [o.ticket for o in make_client().pending_orders("EURUSD", 7)] == [1]


def test_pending_orders_raises_on_a_real_error(monkeypatch):
    fake_mt5(monkeypatch, orders_get=lambda symbol: None, RES_S_OK=1)
    with pytest.raises(Exception):
        make_client().pending_orders("EURUSD", 7)
