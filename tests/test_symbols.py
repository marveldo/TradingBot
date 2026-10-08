"""Tests for core/symbols.py — per-symbol strategy settings."""
import pytest

from core.symbols import SYMBOL_SETTINGS, machine_for, settings_for


def test_every_symbol_has_a_point():
    assert all(s.get("point", 0) > 0 for s in SYMBOL_SETTINGS.values())


def test_everyone_waits_for_the_inducement_and_targets_the_htf_ob():
    for symbol in SYMBOL_SETTINGS:
        m = machine_for(symbol)
        assert (m.entry_mode, m.target_mode) == ("inducement", "htf_ob")


def test_gold_trades_any_block_breakers_included():
    m = machine_for("XAUUSD")
    assert (m.entry_blocks, m.early_taps) == ("nearest", True)
    assert all(d.flip_blocks and d.single_use for d in m.detectors.values())
    assert m.point == 0.01


def test_eurusd_uses_plain_order_blocks():
    m = machine_for("EURUSD")
    assert (m.entry_blocks, m.early_taps) == ("choch", False)
    assert all(not d.flip_blocks and not d.single_use for d in m.detectors.values())


def test_min_rr_per_symbol():
    assert {s: machine_for(s).min_rr for s in SYMBOL_SETTINGS} == {"EURUSD": 1.5, "XAUUSD": 1.5, "BTCUSD": 1.0}


def test_overrides_win():
    assert machine_for("EURUSD", target_mode="liquidity").target_mode == "liquidity"


def test_settings_are_a_copy():
    settings_for("XAUUSD")["point"] = 999
    assert SYMBOL_SETTINGS["XAUUSD"]["point"] == 0.01


def test_unknown_symbol_says_where_to_add_it():
    with pytest.raises(KeyError, match="core/symbols.py"):
        machine_for("GBPUSD")
