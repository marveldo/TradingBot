# SMC Bot

A Smart Money Concepts trading bot for MetaTrader 5. It reads market structure across W1 → D1 → H4 → H1 → M15,
finds order blocks (plus breakers and mitigation blocks), and enters on an M15 CHoCH out of a higher-timeframe POI.
The same `core/` code runs the backtest and the live bot, so live sees exactly what the backtest saw.

The full plan, rules and findings are in [PLAN.md](PLAN.md).

## Setup

Windows only (the `MetaTrader5` package needs the MT5 terminal installed, open and logged in).

```powershell
python -m venv .venv
.venv\Scripts\activate
uv pip install -r requirements.txt
```

Copy `sample.env` to `.env` and fill in your account:

```
ACCOUNT_LOGIN_ID=12345678
ACCOUNT_PASSWORD=your-password
ACCOUNT_SERVER=Deriv-Demo
ACCOUNT_BROKER=deriv
```

In MT5, set *Tools → Options → Charts → Max bars in chart* to **Unlimited**, then restart the terminal.

## Commands

Everything runs through `management.py`:

| Command | What it does |
|---|---|
| `python management.py fetch_data --server Deriv-Demo` | Download W1/D1/H4/H1/M15 candles for every symbol in `core/config.py` |
| `python management.py backtest --symbol EURUSD` | Backtest one symbol: win rate, total R, drawdown, a line per year, and a chart per trade |
| `python management.py backtest_all` | Backtest every symbol in `core/symbols.py` at once, one process per symbol |
| `python management.py live` | Live bot, **dry run**: warms up on history, then logs every setup it finds |
| `python management.py live --trade --risk 0.5` | Live bot placing **real orders**, risking 0.5% of the balance per trade |

Add `--no-charts` to either backtest for numbers only. `--symbol` can be repeated (`--symbol EURUSD --symbol XAUUSD`).

Always dry-run on a demo account before using `--trade`.

## Adding a symbol

1. Add it to `SYMBOLS` in `core/config.py` and run `fetch_data`.
2. Add a line to `SYMBOL_SETTINGS` in `core/symbols.py`. `point` must match `mt5.symbol_info(symbol).point`
   (EURUSD `0.00001`, JPY pairs `0.001`, gold `0.01`).
3. Run `python management.py backtest --symbol <SYMBOL>` and keep it only if it holds up across the years.

## Strategy in short

- **Bias:** start from the W1 trend. Each step (W1→D1, D1→H4, H4→H1) follows the direction above, pauses when price
  taps an opposing higher-timeframe OB, and flips when the lower timeframe confirms with a CHoCH.
- **POI:** an H4/H1 order block in the trade direction, tapped after its inducement was swept.
- **Entry:** an M15 CHoCH that starts inside the POI, then a limit on the M15 block once it has an inducement.
- **Stop:** beyond the M15 swing the reversal started from, plus the spread.
- **Target:** the nearest opposing H1/H4 order block. Setups below each symbol's minimum RR are skipped.

Per-symbol switches (breakers and mitigation blocks, single-use zones, early taps, minimum RR) live in
`core/symbols.py`.

## Project layout

```
core/            shared by backtest and live bot
  candles.py       feeds candles in time order, higher timeframes only once closed (no lookahead)
  detectors/       swings, structure (BOS/CHoCH), order blocks, breakers and mitigation blocks
  state_machine.py the bias chain and the entry rules
  symbols.py       per-symbol settings
  mt5_client.py    everything that talks to MT5
  config.py        .env settings, symbols, timeframes, dates
research/        offline: fetch_data, backtest, trade charts (plot_trades)
live/bot.py      the live bot
commands/        the management.py commands
data/<broker>/<SYMBOL>/<TF>.parquet   downloaded candles (not in git)
data/charts/<SYMBOL>/                 backtest charts (not in git)
logs/live_<SYMBOL>.log                live bot logs
```

## Tests

```powershell
pytest -m "not mt5"     # everything except the tests that need a running MT5 terminal
pytest                  # all, including the MT5 terminal tests
```

## Status

Backtested 2019 → October 2026 on EURUSD, GBPUSD, USDJPY, USDCAD, BTCUSD and XAUUSD. The settings were chosen on
that same history, so expect live results to be lower. Next: paper trade on demo, then compare with the backtest.
