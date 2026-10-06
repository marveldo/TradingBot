# SMC Trading Bot — Plan

Rules-based multi-timeframe SMC state machine first, LightGBM filter on top later.
Built mostly by hand; Jupyter notebooks for exploration, stable code moved into `core/`.

## Strategy (timeframe chain)

Chain: **W1 → D1 → H4 → H1 → M15**. Every step works the same way (fractal):

> Follow the higher TF's trend. When price reaches an **opposing OB on the higher TF**, stop following
> and wait for a **CHoCH on the lower TF**. If it comes, the direction flips.

| TF | Job |
|----|-----|
| W1 | Big-picture bias + weekly OBs (reversal zones) |
| D1 | Daily trend + daily OBs (reversal zones). D1 bullish but price hits a bearish D1 OB → wait for CHoCH → sells |
| H4 | **Trading direction** + H4 OBs/FVGs as POIs |
| H1 | POIs + **sometimes the entry TF** |
| M15 | Usual confirmation (sweep → CHoCH) and entry |

Same logic at every level:
```
FOLLOWING       → trade with this TF's trend, wait at POIs, confirm on the lower TF
AT_OPPOSING_OB  → price inside an opposing higher-TF OB, waiting for a lower-TF CHoCH
FLIPPED         → CHoCH confirmed → FOLLOWING the other way
```
Code idea: write this ONCE as a generic "level" (higher TF + lower TF) and chain it: W1/D1, D1/H4, H4/H1, H1/M15.
Which lower TF confirms each level lives in config, so it can be tweaked without code changes.

Rules are expected to get tweaked during Stage 2.

## Definitions (spec for `core/detectors.py` — confirmed items only, ❓ = still to define)

- **Liquidity used:** inducement only (no equal highs/lows, PDH/PDL, sessions).
- **Swing = move + pullback + continuation** (pullback can be a single candle):
  - Bullish: market moves up → pulls back (even one red candle) → a candle goes past the pullback → swing confirmed.
    The top before the pullback = swing high; the pullback's low = swing low.
  - Bearish: mirror (move down → pullback up, even one green candle → a candle goes below the pullback).
- **OB + inducement come as one pattern** (bullish shown; bearish is the mirror):
  ```
  1. OB candle            the candle just before the BOS candle
  2. BOS candle           the very next candle breaks structure (above the last swing high)
  3. Pullback             after the BOS, price pulls back (even ONE candle)  ← this pullback = the INDUCEMENT
  4. Continuation         price carries on upward past the pullback
  ── later ──
  5. Price returns down   sweeps the inducement low (step 3) on the way
  6. Taps the OB          (step 1)
  7. Lower-TF CHoCH       → entry (buy)
  ```
  - **Valid OB** = steps 1–4 all happened. No BOS right after it, or no pullback + continuation after the BOS → not an OB.
  - **Inducement** = the low of the step-3 pullback (sell OB: the high of the pullback). It sits between price and the OB.
  - **Tradeable** only after step 5: inducement swept before the OB is tapped. Not swept → skip.
- **Wick counts everywhere** (swing continuation, BOS, CHoCH, sweep): a wick through the level is enough, no close needed. (answered 2026-10-04)
- **"Goes past the pullback" (default):** a wick above the pullback candle's high (bearish: below its low) — from the "green candle shoots past that red" example. ❓ confirm, or is it past the top of the move before the pullback?
- **Pullback (default):** one or more opposite-colour candles (red in an up move, green in a down move). ❓ confirm, or does a same-colour candle making a lower low also count?
- ❓ **OB candle colour:** must it be the opposite colour (bearish candle for a buy OB), or any candle before the BOS candle?
- **OB zone (v1 default):** the OB candle's full range, wicks included. Tests: `tests/test_order_blocks.py`.
  OB status: pending → valid (pullback + continuation, inducement set) → swept → tapped; any time → invalid (wick through the far side of the OB).
  Also invalid: the pullback after the BOS already comes back into the OB before an inducement forms. Tap without a sweep doesn't count.
  Real EURUSD H4 (2019–2026): ~1186 OBs, ~207 reach tapped (~27 setups/yr on one TF).
- ❓ **OB life:** dead after the first tap, or can it be used again? (v1: after the tap it only changes again if it breaks → invalid)
- ❓ **Do CHoCH breaks create OBs too?** (v1: only BOS does — your 7-step pattern says BOS. The reversal OB after a CHoCH matters for the flip logic later.)
- ❓ **OB break by wick or close?** (v1: wick. A close-only break gave a few more setups on H4 — test it in the backtest.)
- **BOS (trend continues):** bullish = the new swing's high goes above the previous swing's high. Bearish = the new swing's low goes below the previous swing's low.
  The level to break is set by the FIRST swing after the last break and stays until broken — a later lower high (bullish) / higher low (bearish) doesn't move it. (answered 2026-10-05)
- **CHoCH (trend changes):** bullish trend, but price breaks a swing **low** instead → CHoCH (now bearish). Bearish trend, but price breaks a swing **high** → CHoCH (now bullish).
- **CHoCH level = the low at the OB** (the low the move started from), NOT the inducement low. Bullish: price sweeping the inducement is just the sweep; price breaking below the OB low → CHoCH (bearish). Bearish: mirror (break above the OB high). (answered 2026-10-04)
- **Swings/structure per TF:** each TF's swings, BOS, OBs come only from that TF's own candles (one H4 swing contains many M15 swings).
- **When a higher-TF break is noticed:** v1 = (a) on that TF's candle close (e.g. H4 BOS seen when the H4 candle closes). Later idea: (b) check higher-TF levels against M15 wicks for faster reaction in volatile markets — make it a config switch and backtest both.
- 🅿️ **Parked for v2 — breaker blocks & mitigation blocks** (failed OBs that flip role). Get the basic OB setup backtested first; define these after.
- ❓ **Several pullbacks after the BOS:** if price pulls back twice before continuing, is the inducement the first pullback, or the one closest to the OB?
- ❓ **FVGs:** used at all, or OB + inducement only?

## Project layout

```
smc-bot/
├── pyproject.toml          # `pip install -e .` so notebooks can `from core import ...`
├── .env                    # MT5 login/password/server — never commit
├── management.py           # `python management.py <command>` — runs anything in commands/
├── commands/               # one file per command (help, add_arguments, handle); found via pkgutil
│   └── fetch_data.py       # → research.fetch_data.fetch_data(server)
├── data/<broker>/          # <SYMBOL>_<TF>.parquet + setups.parquet
├── notebooks/              # exploration: check detectors on charts, try features/models
├── core/                   # SHARED by research and live bot
│   ├── config.py           # pydantic Settings: .env credentials, SYMBOLS, TIMEFRAMES, paths → `settings`
│   ├── mt5_client.py       # MT5 connection singleton (get_client()) + validation checks
│   ├── candles.py          # which HTF bars are CLOSED as of time T (no lookahead)
│   ├── detectors.py        # swings, BOS/CHoCH, sweeps, FVG, OB
│   ├── bias.py             # bias per TF (W1, H4, H1)
│   ├── state_machine.py    # regime + setup logic (one instance per symbol)
│   ├── features.py         # builds the feature row when a setup fires
│   └── risk.py             # lot size from SL distance, daily-loss / max-trades checks
├── research/               # OFFLINE
│   ├── fetch_data.py
│   ├── backtest.py
│   ├── label.py
│   └── train.py
├── live/
│   └── bot.py              # uses core/mt5_client.py for bars + orders
├── models/
│   └── smc_filter.txt
├── logs/                   # live setups, predictions, trades
└── tests/
    ├── test_candles.py     # lookahead check
    └── test_detectors.py   # small hand-made candle sequences with known answers
```

**Golden rule:** backtest and live bot import the same `core/` code. Only the data source differs.

**Second rule: new broker or new symbol = edit config, re-run, done. No code changes.**
Current: Deriv demo (`Deriv-Demo`). Also Exness (demo + real). Later maybe a prop firm (e.g. GoatFunded).
- The **MT5 server name** is the key (`Deriv-Demo`, ...). It's passed as `--server`; MT5 itself knows demo vs real.
- `.env`: `ACCOUNT_LOGIN_ID`, `ACCOUNT_PASSWORD`, `ACCOUNT_SERVER`, `ACCOUNT_BROKER` → loaded by `core/config.py` (`from core.config import settings`).
- `settings.SYMBOLS` is a plain list. If a broker renames symbols (Exness `EURUSDm`), edit the list or match via `symbol_info()`.
- Data is stored per broker (demo + live share a feed): `data/<broker>/<SYMBOL>_<TF>.parquet`, so feeds never mix.
- Re-backtest flow: switch account in `.env` → `python management.py fetch_data --server X` → backtest command (later).

`core/mt5_client.py` (singleton, via `get_client()`):
- Same process = one MT5 connection. Asking for a **different server** than the live connection → raise, never return the old one.
- Before returning the existing instance, check it's still alive (`terminal_info().connected`), reconnect if not.
- Close with `mt5.shutdown()` on exit (`atexit`).
- Backtest never uses it — it reads parquet only.

Validation on first connect (fail loudly, never continue):
1. `account_info().server` == requested server (terminal logged into the right account?)
2. Every symbol in `settings.SYMBOLS` exists (`symbol_info()` not `None`).
3. `terminal_info().maxbars` not capped.
4. Every pull: not `None`, not empty, first bar near the requested start date.

## Stage 1: data ✅ (done 2026-10-03)
Deriv server time = UTC (checked). 12 files in `data/deriv/` (EURUSD/XAUUSD/BTCUSD × W1/H4/H1/M15, 2019 → now), `time` saved as UTC.
Run: `python management.py fetch_data --server Deriv-Demo`. Last row of each file may still be a forming candle.
1. Connect with the `MetaTrader5` package (Windows only; terminal must be running and logged in).
2. First: pull EURUSD M15, print first/last timestamp + row count → how deep is the history?
3. Pull **W1, H4, H1, M15** per symbol natively from MT5 (not resampled) so candles match the broker charts.
4. Convert broker server time to UTC (find the broker's offset, incl. DST).
5. Save to `data/<broker>/<SYMBOL>_<TF>.parquet` (needs `pyarrow`).

Notes:
- Set MT5 *Tools → Options → Charts → Max bars in chart* to Unlimited, restart MT5.
- MT5 returns `None` on failure instead of raising — check it, read `mt5.last_error()`.
- Same broker for backtest and live. Dukascopy only if MT5 M15 history is too short.

## Stage 2: rules-only backtest
6. Feed candles one at a time (exactly as live): M15 close → update LTF; H1/H4/W1 close → recompute bias.
7. **Lookahead trap:** MT5 stamps bars with their OPEN time. A higher-TF bar is only visible once `bar_open + bar_length <= current_time`. Otherwise the backtest sees the future.
8. State machine: regime → H4 direction → wait for H4/H1 POI → M15 sweep → M15 CHoCH → entry.
9. Simulate each trade with spread and slippage until SL/TP/timeout.
10. Report expectancy, profit factor, max drawdown, win rate.
11. Plot trades and check them by eye. Fix detectors until the bot sees what you see.

**Checkpoint:** does raw SMC have an edge after costs? If not, fix the rules before touching ML.

**Prop-firm check (later, only when a prop account is chosen):** a separate pass over the trade list/equity curve, not part of the strategy logic.
- Copy the exact rules for the specific account from the firm's site into `core/config.py` (daily loss %, max drawdown %, static vs trailing, how daily loss is measured (balance or equity, at what server time), profit target, min days, news/weekend/lot rules).
- Replay the backtest equity day by day: would the account have breached? Use the same rules in `core/risk.py` live so the bot stops before the firm stops it.
- GoatFunded (checked 2026-10-01, re-check before buying — help.goatfundedtrader.com):
  - EAs allowed if the code is your own (may be asked to show it). No HFT, no gold-arbitrage, no third-party/bought EAs.
  - VPS banned on One Step / Two Step / Instant Premium / Pay Later bought from 12 Aug 2026. Allowed on Instant GOAT / Instant Hero.
  - Don't pass with the bot then trade manually on funded (or vice versa).
  - No copy trading / signal services.

## Stage 3: ML filter
12. One feature row per setup: biases (W1/H4/H1 + aligned with W1?), regime, distance to nearest weekly POI, sweep size in ATR, displacement, FVG/OB quality, session, room to next liquidity, ATR regime, spread.
13. Label with the triple barrier: 1 = TP first, 0 = SL first.
14. Save to `data/setups.parquet`.
15. Train: purged walk-forward CV. Baselines: take-every-setup and logistic regression. Main: LightGBM (Optuna on validation folds only, SHAP to sanity-check). Choose a probability threshold.
16. **Checkpoint:** does the filtered strategy beat take-every-setup after costs? If yes, save with `booster.save_model("models/smc_filter.txt")` (not pickle).

## Stage 4: paper trade
17. Run `live/bot.py` on an MT5 demo for weeks or months.
18. Compare live trades with what the backtest would have done over the same period.

## Stage 5: live loop
```
startup: connect MT5, load model, warm up bars (~500 for H4/H1/M15, ~100 for W1)
loop:
  wait for M15 close
  per symbol:
    W1 closed → update weekly bias + weekly POIs
    H4/H1 closed → update bias, POIs, regime
    update M15 detectors + state machine
    setup fires → features → model.predict → threshold + risk checks → order
    manage open trades
  kill switch: daily loss limit, max open trades, connection health
```

## Stage 6: ongoing
19. Log every setup, prediction, and trade.
20. Retrain on a schedule (e.g. monthly); replace the model only if it wins on walk-forward.
21. If a drawdown goes beyond the worst historical one, stop and investigate.

## Open questions
1. While price sits inside an opposing higher-TF OB but the lower TF hasn't CHoCH'd yet: keep taking old-direction trades, or pause? (Pausing is the safer default.)
2. ~~Does D1 play a role?~~ Yes: D1 OBs are reversal zones, same flip logic as W1 (answered 2026-10-03).
3. **The hard part: when is H1 the entry TF instead of M15?** Write down what makes you take an H1 entry (clean H1 CHoCH with displacement? no M15 setup forming? spread/session?). If no clear rule: code both, log `entry_tf`, let the backtest + ML decide.
4. Which lower TF confirms each level's flip? Default guess: the next one down (W1→D1, D1→H4, H4→H1, H1→M15). Earlier note said weekly flip confirms on H4 — check.
5. Homework: review ~20 past trades, write down W1/D1/H4/H1 bias, which OB price was at, entry TF (H1 or M15) and why, and what you did. Clear pattern → coded rule; no pattern → ML feature.

## Study list
- Phase 1: Python, pandas, NumPy, Plotly, state machines, backtesting concepts, vectorbt (optional), MT5 API on demo
- Phase 2: scikit-learn, LightGBM, SHAP, Optuna; meta-labeling, triple barrier, purged walk-forward CV, leakage
- Phase 3 (optional): PyTorch or TensorFlow/Keras; Go for live execution
- Reading: *Think Like a Programmer* (Spraul); *Advances in Financial Machine Learning* (López de Prado), chapters 3, 4, 7
