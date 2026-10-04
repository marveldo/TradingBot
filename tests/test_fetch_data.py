import shutil
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

import research.fetch_data as fetch_module
from core.config import settings
from research.fetch_data import convert_to_dataframe, fetch_data

SERVER = "Deriv-Demo"
RATES_DTYPE = [("time", "i8"), ("open", "f8"), ("high", "f8"), ("low", "f8"), ("close", "f8")]


def make_rates(n=3):
    start = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp())
    rows = [(start + i * 900, 1.1, 1.2, 1.0, 1.15) for i in range(n)]
    return np.array(rows, dtype=RATES_DTYPE)


@pytest.fixture
def out_dir(tmp_path, monkeypatch):
    """Point fetch_data at a throwaway folder, and delete it after the test."""
    folder = tmp_path / "deriv"
    monkeypatch.setattr(fetch_module, "data_dir", folder)
    yield folder
    shutil.rmtree(folder, ignore_errors=True)
    assert not folder.exists()


class FakeClient:
    def __init__(self, fail_on=None):
        self.fail_on = fail_on
        self.calls = []
        self.disconnected = False

    def disconnect(self):
        self.disconnected = True

    def get_mt5_timeframe(self, timeframe):
        return f"TF_{timeframe}"

    def copy_rates_range(self, symbol, timeframe, date_from, date_to):
        self.calls.append((symbol, timeframe))
        if (symbol, timeframe) == self.fail_on:
            raise Exception(f"Failed to copy rates for {symbol}")
        return make_rates()


@pytest.fixture
def fake_client(monkeypatch):
    client = FakeClient()
    monkeypatch.setattr(fetch_module, "get_mt5_client", lambda server: client)
    return client


# --- convert_to_dataframe -----------------------------------------------------

def test_convert_to_dataframe_keeps_columns():
    df = convert_to_dataframe(make_rates())
    assert list(df.columns) == ["time", "open", "high", "low", "close"]
    assert len(df) == 3


def test_convert_to_dataframe_turns_time_into_utc_datetime():
    df = convert_to_dataframe(make_rates())
    assert pd.api.types.is_datetime64_any_dtype(df["time"])
    assert str(df["time"].dt.tz) == "UTC"
    assert df["time"].iloc[0] == pd.Timestamp("2026-09-01 00:00:00", tz="UTC")
    assert df["time"].iloc[1] == pd.Timestamp("2026-09-01 00:15:00", tz="UTC")


# --- data_dir -----------------------------------------------------------------

def test_data_dir_is_inside_the_data_folder():
    assert fetch_module.data_dir == settings.BASE_DIR / "data" / settings.ACCOUNT_BROKER


# --- fetch_data (fake MT5) ----------------------------------------------------

def test_fetch_data_saves_one_file_per_symbol_and_timeframe(out_dir, fake_client, monkeypatch):
    monkeypatch.setattr(settings, "SYMBOLS", ["EURUSD", "XAUUSD"])
    monkeypatch.setattr(settings, "TIMEFRAMES", ["H4", "M15"])

    fetch_data(SERVER)

    saved = sorted(p.name for p in out_dir.iterdir())
    assert saved == ["EURUSD_H4.parquet", "EURUSD_M15.parquet", "XAUUSD_H4.parquet", "XAUUSD_M15.parquet"]
    assert fake_client.calls == [("EURUSD", "TF_H4"), ("EURUSD", "TF_M15"), ("XAUUSD", "TF_H4"), ("XAUUSD", "TF_M15")]
    assert fake_client.disconnected


def test_fetch_data_saved_file_reads_back_the_same(out_dir, fake_client, monkeypatch):
    monkeypatch.setattr(settings, "SYMBOLS", ["EURUSD"])
    monkeypatch.setattr(settings, "TIMEFRAMES", ["H4"])

    fetch_data(SERVER)

    df = pd.read_parquet(out_dir / "EURUSD_H4.parquet")
    pd.testing.assert_frame_equal(df, convert_to_dataframe(make_rates()), check_dtype=False)


def test_fetch_data_stops_when_a_pull_fails(out_dir, monkeypatch):
    client = FakeClient(fail_on=("EURUSD", "TF_M15"))
    monkeypatch.setattr(fetch_module, "get_mt5_client", lambda server: client)
    monkeypatch.setattr(settings, "SYMBOLS", ["EURUSD", "XAUUSD"])
    monkeypatch.setattr(settings, "TIMEFRAMES", ["H4", "M15"])

    with pytest.raises(Exception, match="Failed to copy rates for EURUSD"):
        fetch_data(SERVER)

    assert sorted(p.name for p in out_dir.iterdir()) == ["EURUSD_H4.parquet"]
    assert client.disconnected


# --- real terminal (pytest -m mt5) ----------------------------------------------

@pytest.mark.mt5
def test_live_fetch_data_writes_a_real_file(out_dir, monkeypatch):
    monkeypatch.setattr(settings, "SYMBOLS", ["EURUSD"])
    monkeypatch.setattr(settings, "TIMEFRAMES", ["H4"])
    monkeypatch.setattr(settings, "START_DATE", datetime(2026, 9, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(settings, "END_DATE", datetime(2026, 9, 8, tzinfo=timezone.utc))

    fetch_data(settings.ACCOUNT_SERVER)

    df = pd.read_parquet(out_dir / "EURUSD_H4.parquet")
    assert len(df) > 0
    assert str(df["time"].dt.tz) == "UTC"
    assert df["time"].min() >= pd.Timestamp("2026-09-01", tz="UTC")
    assert df["time"].max() <= pd.Timestamp("2026-09-08", tz="UTC")
