"""Per-symbol strategy settings. Each symbol trades the same state machine; only these switches differ, because
instruments respect different structures (e.g. gold reacts to breakers, EURUSD to plain order blocks).

New symbol = add a line here (at least its `point`, the price step from MT5 symbol_info().point). Anything not set
uses the state machine's defaults (the first version: OBs, CHoCH-OB entry, next H1 swing as TP).

    machine = machine_for("XAUUSD")
"""
from .state_machine import StateMachine

# Everyone: M15 CHoCH → wait for the M15 inducement → limit; TP at the nearest opposing H1/H4 OB.
# Earlier results (immediate entry, H1-swing TP) are in PLAN.md / git history.
COMMON = dict(entry_mode="inducement", target_mode="htf_ob")

SYMBOL_SETTINGS : dict[str, dict] = {
    "EURUSD": dict(COMMON, point=0.00001, min_rr=1.5),                  # plain order blocks
    "XAUUSD": dict(COMMON, point=0.01, min_rr=1.5, breakers_and_mitigation=True, single_use=True, early_taps=True,
                   entry_blocks="nearest"),
    "BTCUSD": dict(COMMON, point=0.001, min_rr=1.0),
    "GBPUSD": dict(COMMON, point=0.00001 , min_rr=1.5, breakers_and_mitigation=True, single_use=True, early_taps=True,
                   entry_blocks="nearest"),
    "USDJPY": dict(COMMON ,point=0.001 , min_rr =1.5),

    "USDCAD": dict(COMMON, point=0.00001, min_rr=1.0, breakers_and_mitigation=True, single_use=False,
                   early_taps=True, entry_blocks="nearest")
  
}


def settings_for(symbol : str) -> dict:
    if symbol not in SYMBOL_SETTINGS:
        raise KeyError(f"{symbol} has no entry in SYMBOL_SETTINGS (core/symbols.py) — add at least its point")
    return dict(SYMBOL_SETTINGS[symbol])


def machine_for(symbol : str, **overrides) -> StateMachine:
    """A StateMachine with this symbol's settings. `overrides` win (e.g. continuations=True for research)."""
    return StateMachine(**{**settings_for(symbol), **overrides})
