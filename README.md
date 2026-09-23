# NOSTRO Algo Terminal

A local, dark-themed "Bloomberg-style" terminal for learning and backtesting algo
trading strategies on NQ and ES futures — open a $50k demo account, pick a strategy,
backtest it, and run a parameter search to "train" it against historical data.

## Run it

```powershell
cd "Real algo trader - nostro"
.\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000
```

Then open http://localhost:8000 in your browser, or just double-click `run.bat`.

Accounts show as a card grid on the home tab — click any card to open a full
detail modal (controls, Apex evaluation progress if applicable, a live
candlestick chart with entry/exit markers, equity curve, trade log, and the
monthly calendar), instead of a single inline panel. The background trainer
now searches 80 parameter combinations per strategy per cycle (up from 30) for
a more thorough, less noisy sense of what's actually profitable.

## Trainer tab — runs 24/7, not just when the page is open

The moment the server starts, a background task (`app/optimizer/trainer_service.py`)
begins training continuously on its own timer (every 10 minutes by default,
alternating NQ and ES) — completely independent of the browser. Each cycle runs
the walk-forward search across all 10 strategies and, if the winner beats the
best result ever seen for that symbol, promotes it to **champion** and persists
it (`champions` table in `data_cache/app.db`), so training survives server
restarts and keeps improving whether or not anyone is looking at it.

Open the **TRAINER** tab and it just shows you what's already there — no
waiting: the live status bar (● pulsing dot, total runs, countdown to the next
cycle, NQ/ES toggle, "⚡ RUN NOW" for an extra cycle on demand) polls every 5s,
and the champion hero auto-plays a candlestick replay of the current best
result — entries/SL/TP appear live, and every trade close pops a big glowing
result card (green win / red loss, $ and %) before playback resumes. If a
better strategy is found while you're watching, a "🏆 NEW CHAMPION" toast fires
and the replay swaps to it automatically. Training history (with a score-over-
time sparkline), the full cross-strategy leaderboard, and a manual
single-strategy search sit below it.

Tune the cadence/aggressiveness in `trainer_service.py`:
`TRAIN_INTERVAL_SECONDS` (default 600s) and `N_ITERATIONS_PER_STRATEGY`
(default 30).

## Offline training — no server needed

`scripts/offline_train.py` (or double-click `train_offline.bat`) calls the exact
same `trainer_service.run_now()` the always-on background loop calls, so it
writes to the same `data_cache/app.db` — anything it finds (a new champion, a
leaderboard entry, updated strategy stats) shows up in the Trainer tab next
time the server runs, no matter which one produced it. Use it to keep training
going while the server (and dashboard) is off — overnight, on a headless
machine, or from Windows Task Scheduler:

```powershell
.venv\Scripts\python.exe scripts\offline_train.py                  # one cycle each of NQ, ES
.venv\Scripts\python.exe scripts\offline_train.py --symbol NQ       # one NQ cycle only
.venv\Scripts\python.exe scripts\offline_train.py --cycles 0        # run forever, alternating (Ctrl+C to stop)
.venv\Scripts\python.exe scripts\offline_train.py --cycles 6 --iterations 150
```

It's also safe to run alongside a live server — SQLite's own busy-timeout
handles the concurrent writes (`app/db.py`) — but running both at once just
means two training cycles competing for the same CPU at once, so it's meant
for when the server *isn't* already running, not in addition to it.

## Apex Evaluation accounts — realistic prop-firm-style pass/fail simulation

On the Accounts/home tab, "NEW ACCOUNT" has an **APEX EVALUATION** option
alongside the standard $50k demo. Pick a size tier ($25k/$50k/$100k/$150k) and
it's created with that tier's real profit target and max drawdown
(`app/accounts/apex_rules.py` — the exact numbers you specified; edit `TIERS`
there if Apex's published numbers ever change). The rule enforced is
**intraday trailing drawdown**: the failure floor trails your peak equity
(including unrealized P&L on an open position) in real time, and locks at the
starting balance once you're profitable enough that it would otherwise exceed
it — Apex's actual documented behavior.

Two ways to use it:
- **📊 BACKTEST EVALUATION (preview)** — instantly simulates the assigned
  strategy against recent history through the same rule engine and tells you
  if/when it would have passed or failed. Clearly labeled as a preview —
  nothing here is written to the account's real trade history.
- **⚡ GO LIVE** — the real thing, forward in time, using the same live engine
  as standard accounts, with the added drawdown/target checks every tick and a
  forced flatten of any open position at 4:59pm ET (Apex's daily rule). It
  stops itself automatically the moment it passes or fails.

The account detail panel shows a profit-target progress bar, a drawdown-
headroom bar (current equity vs. floor vs. peak), and — for **every** account,
not just Apex ones — a **monthly trade calendar**: green days you made money,
red days you lost, click a day to see its trades. Prev/next month to page
through history.

Realism fixes that apply everywhere (not just eval accounts), in
`app/backtest/engine.py`: every trade's entry/exit/stop/target now snaps to
NQ/ES's real 0.25-point tick size, and stop-loss fills assume one tick of
adverse slippage (targets fill clean, since they're a resting limit, not a
stop) — so results reflect prices you could actually get, not clean floats.

(If `python`/`pip` misbehave in your terminal, it's because `PYTHONHOME` is set
incorrectly in your Windows environment variables — either unset it, or always use
the venv's own interpreter directly as above, which sidesteps it.)

## Accounts tab — home dashboard + live paper trading

The landing tab is a real dashboard now: total equity, today's P&L ($ and %),
open position count, live account count, and today's trade count across every
account, up top. Below that, a self-computed Fear & Greed gauge for NQ (RSI +
momentum + volatility blended into one 0-100 score — see
`app/data/fear_greed.py`; there's no reliable free official index for futures,
so this is our own) and a live feed of today's trades across all accounts.

Open a $50k demo account, assign it a strategy + symbol, then **⚡ GO LIVE** —
this is real (paper) forward trading, not a backtest. A background loop
(`app/accounts/live_engine.py`, running continuously since server start, same
as the trainer) checks every 60 seconds for a new price bar on that account's
symbol and asks "if I ran this strategy's normal backtest through right now,
would a trade have just started on the very last bar?" — if so, it opens a
position; an open position is checked against each new bar for its stop/target.
The account detail panel shows the open position with a live-ticking unrealized
P&L (mark-to-market against the latest price every ~15s). **STOP LIVE**
force-closes at the current price and keeps the trade in history. None of the
10 strategies needed to be rewritten for this — it reuses their existing
backtest logic as-is.
- **Strategies tab** — the strategy library, 10 in total:
  - `ny_open_breakout` — breaks out of the first N-minute NY-open range (your original example)
  - `orb_fade` — fades a failed breakout of that same opening range
  - `vwap_reversion` — mean-reverts large deviations from session VWAP
  - `ema_crossover` — fast/slow EMA crossover trend following
  - `donchian_breakout` — N-bar channel breakout
  - `rsi_reversal` — RSI oversold/overbought mean reversion
  - `bollinger_breakout` — momentum continuation outside the Bollinger Band
  - `prev_day_range_breakout` — breaks of the previous session's high/low
  - `macd_cross` — MACD/signal line crossover
  - `inside_bar_breakout` — breakout of an inside-bar consolidation

  Every strategy exposes its knobs (see `app/strategies/*.py`), and the 8
  indicator-based ones share one generic trade simulator
  (`app/strategies/engine_utils.py`) so adding an 11th only means writing the
  entry-signal logic, not a new backtest loop.
- **Backtest tab** — run any strategy against NQ/ES history: equity curve, trade
  log, metrics (win rate, profit factor, drawdown, Sharpe), AND a candlestick
  chart of the run with entry arrows (▲ long / ▼ short, labeled with entry price,
  stop-loss, and take-profit) and exit dots (green = win, red = loss).
- **Optimizer tab** — the "self-training" piece, two modes:
  - single-strategy random search: backtests many parameter sets on a training
    window, re-validates survivors on a held-out test window (walk-forward, no
    shuffling), ranks by out-of-sample score.
  - **Train All 10 Strategies** — the real trainer: runs that same search across
    every strategy in the library and merges everything into one cross-strategy
    leaderboard, so instead of hand-picking a strategy to tune, it tells you which
    strategy *and* parameter set actually held up out-of-sample.
  Either way, watch the gap between train and test metrics — a strategy with a bad
  train score and a great test score usually just got lucky on a small sample, not
  "found an edge". Any leaderboard row can be promoted straight into a new $50k
  demo account with one click.
- **Chart tab** — raw NQ/ES candles.

## Data source

Price data comes directly from Yahoo Finance's public chart endpoint (free, no
API key, ~60 days of intraday history for NQ=F/ES=F, cached locally as parquet
under `data_cache/`). This is what both the backtester and the "run on historical
data" demo-account flow use.

## Adding a new strategy

Strategies are plain Python, not black-box models — that's what keeps the
optimizer's output explainable. To add one:

1. Create a `Strategy` subclass (in `app/strategies/signal_strategies.py` if it
   fits the generic bar-by-bar pattern, otherwise its own file — see
   `ny_open_breakout.py` for a from-scratch example).
2. Declare its tunable knobs as `ParamSpec`s (name, default, min, max, step).
3. Implement `generate_trades(df, params) -> list[Trade]`. If it's signal-based
   (an indicator crossing a threshold), just build an `entries` series (+1/-1/0)
   and hand it to `app.strategies.engine_utils.simulate_trades` along with an ATR
   series for stop/target sizing — see any class in `signal_strategies.py`.
4. Register it in `app/strategies/registry.py`.

It immediately shows up in the Strategies/Backtest/Optimizer tabs — no frontend
changes needed.

## Optional future upgrade: a real broker feed via Interactive Brokers

"Live" trading today polls free delayed Yahoo bars every 60s — real, but not a
real broker connection. If you ever want actual IBKR paper-trading data/fills
instead: open a free account at interactivebrokers.com, install IB Gateway,
enable API access on the paper port (7497), `pip install ib_async`, and swap
`app/accounts/live_engine.py`'s data source for IBKR quotes — the DB schema,
ledger, and dashboard don't need to change, since they don't know or care
where fills come from. Needs your own IBKR credentials, so it isn't built yet.

## Known limitations to keep in mind

- Free intraday Yahoo data only goes back ~60 days for 5m/15m/30m bars, so
  backtests/optimizer runs are working with a fairly small sample — treat results
  as directional, not definitive.
- The optimizer's random search over ~300 iterations currently takes something
  like 30-60 seconds (it's not yet parallelized across CPU cores) — fine for now,
  but worth revisiting if you start running much larger searches.
- Backtest fills assume you get filled exactly at the breakout/stop/target price
  with a flat $5 commission — real slippage will be worse, especially on stops.
"# Jordan-termnial" 
