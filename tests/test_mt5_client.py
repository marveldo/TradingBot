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
