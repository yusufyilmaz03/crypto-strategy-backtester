# Crypto Strategy Backtester

[![tests](https://github.com/yusufyilmaz03/crypto-strategy-backtester/actions/workflows/tests.yml/badge.svg)](https://github.com/yusufyilmaz03/crypto-strategy-backtester/actions/workflows/tests.yml)

A Python research tool for testing technical trading strategies on historical crypto market data, with paper trading, an order flow for the Binance Spot Testnet and a web control panel.

> **For research and education only.** Real orders are sent only to the Binance Spot Testnet (no real money); live trading is switched off in code. This is not financial advice.

## What it does

- **Market data:** pulls OHLCV candles from Binance through [ccxt](https://github.com/ccxt/ccxt) (public endpoints, no API key needed).
- **Market data cache:** any lookback is downloaded in pages and cached under `data/`; later runs only fetch new candles. Candles that have not closed yet are always dropped.
- **Indicators:** RSI (Wilder), EMA (9/21/50), ATR, Bollinger Bands and Donchian channels, computed with pandas.
- **10 strategies:**
  | Key | Idea |
  |-----|------|
  | v1 | RSI + EMA crossover |
  | v2 | RSI + EMA, filtered on the last 3 candles |
  | v3 | RSI + EMA with ATR-based stop loss |
  | v4 | Bollinger Band mean reversion with an RSI filter |
  | v5 | EMA cross, trading only in the trend direction |
  | v6 | Donchian (20) breakout with an ATR threshold against false breakouts |
  | v7 | Trend filter (EMA21 vs EMA50) + RSI pullback |
  | v8 | Time-series momentum: long while the return over the last L candles is positive |
  | v9 | Moving-average regime: long while SMA(fast) is above SMA(slow) (fast=1 means the price itself) |
  | v10 | Turtle/Donchian: enter on an N-candle high, exit on an M-candle low |

  Plus R1, a cross-sectional rotation into the coins with the strongest momentum (`analysis/rotation.py`).
- **One engine for backtest and paper trading:** the same bar-by-bar engine drives both, so paper results follow the backtest rules exactly (see *Simulation rules*).
- **Multi-backtest:** runs every strategy on every symbol and timeframe and reports in-sample, out-of-sample and walk-forward results with return, buy-and-hold return, win rate, profit factor, max drawdown, Sharpe and exposure.
- **Strategy research:** walk-forward optimization of each strategy's parameters on 15m/1h/4h data, with out-of-sample portfolio results, a cost stress test and a deflated Sharpe ratio to guard against overfitting.
- **Paper trading:** each paper instance processes every newly closed candle. State, trades, signals and the equity curve live in SQLite (`runtime/paper.db`), so a restart resumes where it stopped and first processes the candles it missed.
- **Dashboard:** a Flask app showing paper trading return, win rate, max drawdown and the portfolio equity curve, cumulative PnL, win/loss and per-symbol trade distributions, the latest trade, and a backtest comparison table and chart filterable by segment, strategy, symbol and timeframe.
- **Tests:** pytest suite covering data loading, indicators, look-ahead checks for every strategy, the engine, metrics and the paper loop.

## Project structure

```
├── kripto_bot/
│   ├── __main__.py          # command line: python -m kripto_bot <command>
│   ├── config.py            # defaults: symbols, costs, strategy, risk limits, research settings
│   ├── settings.py          # settings editable from the dashboard (runtime/settings.json)
│   ├── instances.py         # named bot instances (runtime/instances.json)
│   ├── paths.py             # where files live (runtime/, results/, data/)
│   ├── core/                # the trading model
│   │   ├── data.py          #   paginated OHLCV download + local cache, closed candles only
│   │   ├── indicators.py    #   RSI (Wilder), EMA, ATR (Wilder)
│   │   ├── strategy.py      #   signal generators v1–v10 (vectorized)
│   │   ├── engine.py        #   bar-by-bar engine shared by backtests, paper trading and the testnet
│   │   └── metrics.py       #   performance metrics, probabilistic and deflated Sharpe
│   ├── analysis/            # research and evaluation
│   │   ├── evaluation.py    #   in-sample/out-of-sample and walk-forward splits
│   │   ├── optimize.py      #   walk-forward parameter optimization
│   │   ├── rotation.py      #   cross-sectional momentum rotation (R1)
│   │   ├── research.py      #   research runner → results/research_*.csv
│   │   ├── multi_backtest.py#   grid backtest → results/multi_backtest_strategies.csv
│   │   ├── forward_report.py#   forward-test report vs buy-and-hold
│   │   └── holdout_test.py  #   the one-off v9 holdout test
│   ├── trading/             # running bots
│   │   ├── run_realtime.py  #   paper trading on live closed candles
│   │   ├── run_testnet.py   #   order-sending loop on the Binance Spot Testnet
│   │   ├── broker.py        #   order execution (ccxt; testnet by default) and a fake broker for tests
│   │   ├── store.py         #   SQLite persistence (paper and testnet)
│   │   └── testnet_check.py #   end-to-end check of the testnet order flow
│   ├── app/                 # control panel
│   │   ├── run_all.py       #   supervisor: dashboard + one process per enabled instance
│   │   ├── dashboard.py     #   Flask app (templates/, static/)
│   │   ├── playground.py    #   interactive backtests
│   │   └── reporting.py     #   read-side helpers
│   └── tools/               # Binance health check and matplotlib charts
├── tests/                   # pytest suite
├── docs/                    # research pre-registrations and results
├── runtime/                 # (created, not in git) databases, instances, settings, logs, kill switch
├── results/                 # (created, not in git) backtest and research outputs
└── data/                    # (created, not in git) OHLCV cache
```

Everything the bot writes goes to `runtime/`, `results/` or `data/`; the source tree stays clean. Files from the older flat layout are moved there automatically on the first run (an old bot process must be stopped first).

## Getting started

Requirements: Python 3.10+.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Optional: API keys are not needed for market data or paper trading
cp .env.example .env
```

Everything day-to-day runs from the control panel:

```bash
python -m kripto_bot run   # dashboard + every enabled bot instance; open http://127.0.0.1:5002
```

The panel has:

- **Overview**: supervisor and kill switch status, every instance with return, buy-and-hold and drawdown.
- **Instances**: create named strategy setups (paper or testnet; paper ones can use inverse-volatility sizing, rebalanced monthly), start/stop/restart them, and see each one's equity vs buy-and-hold, positions, trades, signals and live log. Several paper instances can run in parallel; one testnet instance at a time (they share one exchange account). An instance's setup is fixed after creation.
- **Playground**: backtest any symbol, timeframe, strategy, parameters and stop on a candlestick chart with trade markers and indicator overlays. Runs are logged; unused holdout data is left out unless requested.
- **Settings**: costs, risk limits, symbol list and research settings with validation (stored in `settings.json`). API keys and the live trading switch can't be changed from the panel.
- **Backtests**: results of the grid backtest.

The supervisor runs one process per enabled instance (see `runtime/instances.json`), restarts crashed ones and writes logs to `runtime/logs/`. Ctrl+C stops everything.

Other commands (`python -m kripto_bot` lists them all):

```bash
python -m kripto_bot backtest          # grid backtest → results/multi_backtest_strategies.csv
python -m kripto_bot backtest --symbols BTC/USDT ETH/USDT --timeframes 5m --strategies v1 v6 --days 30
python -m kripto_bot research          # walk-forward research → results/research_*.csv
python -m kripto_bot forward-report    # forward-test metrics vs buy-and-hold (docs/forward-test.md)
python -m kripto_bot paper --instance forward-v9    # one instance without the supervisor
python -m kripto_bot dashboard         # control panel only
```

Most settings can be changed on the Settings page; the defaults live in `kripto_bot/config.py`.

Run the tests:

```bash
pip install -r requirements-dev.txt
pytest
```

## Simulation rules

- Signals are computed only from closed candles; a signal on candle *t* is filled at the **open of candle *t+1***.
- Stops (ATR × multiplier from the fill price) are checked against each candle's high/low. If a candle opens beyond the stop, the fill is at the open.
- Taker fees apply to both legs and slippage always works against the trade.
- Each position uses the full equity (no leverage, compounding), so results are percentage returns and comparable across assets.
- Long-only by default (`ALLOW_SHORT = False`), since spot markets cannot be shorted. An opposite signal closes the position; it does not reverse it.
- Out-of-sample: the last `OOS_FRACTION` of the history. Walk-forward folds split the second half of the history into consecutive test windows. Indicators use the full preceding history, and trades are simulated only inside each segment.

## Strategy research

`python -m kripto_bot research` runs a walk-forward optimization for every strategy on every symbol and research timeframe (`RESEARCH_TIMEFRAMES` in `kripto_bot/config.py`):

1. The history is split into a first training block and `WFO_FOLDS` consecutive test windows (rolling training window of fixed length).
2. In each fold, every combination of the strategy's parameter grid (`PARAM_GRIDS` in `kripto_bot/core/strategy.py`) and stop multiplier is backtested on the training window. The best by Sharpe, among those with at least `WFO_MIN_TRADES` trades, is run on the next test window. If none qualifies, the fold stays flat.
3. Test windows are chained into one out-of-sample equity curve per symbol. The same windows are re-run with fees and slippage × `COST_STRESS`.
4. Per strategy/timeframe, the symbols' out-of-sample curves form an equal-weight portfolio that is compared with an equal-weight buy-and-hold portfolio. A deflated Sharpe ratio accounts for having picked the best of all candidates.

Phase 2b (trend following and rotation on 4h/1d) was pre-registered in [docs/phase2b-preregistration.md](docs/phase2b-preregistration.md), which also lists the results. The research runner holds out the last `HOLDOUT_FRACTION` of each timeframe; `python -m kripto_bot research --holdout` tests only the finalists, once.

A candidate passes when its portfolio return and cost-stressed return are positive, its Sharpe beats buy-and-hold, it has at least 30 trades and its deflated Sharpe ratio is at least 0.95.

## Findings so far

- **v1–v7 on 1m–4h:** no edge after costs. Walk-forward out-of-sample portfolios trail buy-and-hold, and every candidate turns negative with costs × 1.5.
- **Trend following on 1d (Phase 2b):** the best candidate, v9 (close above SMA50, no stop), returned +36.7% out-of-sample against −26.6% for buy-and-hold, with roughly half the drawdown, but its deflated Sharpe ratio (0.01) is far from the 0.95 bar, so it did not pass.
- **v9 holdout** ([docs/v9-holdout-test.md](docs/v9-holdout-test.md), pre-registered, run once): +7.2% vs −11.4%, Sharpe 0.42 vs 0.07, max drawdown 24.7% vs 48.9%. The robust part of the result is the lower drawdown; the evidence for excess return is still weak.
- **Survivorship-free test** ([docs/v9-survivorship-test.md](docs/v9-survivorship-test.md), pre-registered, run once): on 2018–2022, a period never used before, with a monthly point-in-time top-50 universe that includes delisted coins, v9 returned +676% (Sharpe 1.09, max drawdown 55%) vs −29% for buy-and-hold (0.43, 91%); the bootstrap 90% interval of the Sharpe difference is [0.14, 1.15] and all 8 tested SMA lengths beat buy-and-hold. The same test shows a large survivorship bias in 2022–2026: on a realistic universe v9 was about flat (−9%) while buy-and-hold lost 76%.
- **Inverse-volatility sizing** ([docs/v9-sizing-test.md](docs/v9-sizing-test.md), pre-registered): weighting coins by 1/σ of their last 60 days raised v9's Sharpe from 1.09 to 1.17 (2018–2022) and from 0.14 to 0.45 (2022–2026) with lower drawdowns; both periods had been seen before, so this is supporting evidence only.
- **Exit variants** ([docs/v9-exit-test.md](docs/v9-exit-test.md), pre-registered): a trailing exit (highest close − 3×ATR) and a 2% entry band each beat plain v9 in both periods on Sharpe and drawdown, also with costs × 1.5, but only by 0.02–0.05 Sharpe: a small, consistent refinement. Both run as paper instances next to the forward test.
- A 180-day paper forward test of v9 is running ([docs/forward-test.md](docs/forward-test.md)).

## Testnet trading

A testnet instance trades its strategy with real orders on the [Binance Spot Testnet](https://testnet.binance.vision) (no real money):

1. Create testnet API keys and put them in `.env` as `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET`.
2. `python -m kripto_bot testnet-check` checks the connection; add `--yes` to place a small buy and sell.
3. Start the `testnet-v9` instance (or create one) in the panel. Its state is kept in its own database in `runtime/`.

Safety:

- Signals come from closed mainnet candles; each decision is sent as a market order right after the close, with a deterministic client order id so that a crash can't cause a duplicate order.
- The bot only trades positions it opened itself and checks them against the exchange balance on start. Candles missed while it was down are not traded.
- `TRADING_CAPITAL_USDT` is split equally across symbols; `MAX_ORDER_USDT` caps each order; after a `DAILY_LOSS_LIMIT_PCT` loss no new entries are made until the next UTC day.
- Kill switch: use the switch on the Overview page, or create `runtime/KILL_SWITCH`, to stop new entries; put the word `flatten` in it to close every position.
- Testnet prices and fees differ from mainnet (fees are often zero and order books are thin), so testnet profit and slippage numbers are not meaningful; the testnet only verifies the order flow.
- Live trading is refused unless `TRADING_MODE = "live"` **and** `LIVE_TRADING_ENABLED = True`.

## Running 24/7

The bots act on candle closes (daily candles close at 00:00 UTC), so the supervisor has to be running then. Missed paper candles are caught up later at historical prices; testnet instances skip them.

**On a Mac:** install the supervisor as a background service. It starts at login, restarts after a crash and keeps the Mac from idle sleeping while it is on power (a closed laptop lid still sleeps it):

```bash
python -m kripto_bot service install     # status | restart | uninstall
```

Only one supervisor runs at a time, so stop a manually started one first. The supervisor's own messages and the start/stop/exit events of its services go to `runtime/logs/supervisor.log`; each service's output is in `runtime/logs/<name>.log`, rotated at 10 MB with three older files kept. After an update that changes the service definition, run `service install` again.

**On a server (Docker):** copy the project, create `.env` (API keys, Telegram, and `DASHBOARD_PASSWORD`) and run:

```bash
docker compose up -d --build
```

State stays in `runtime/`, `results/` and `data/` on the host. The panel is published on the server's 127.0.0.1 only; open it through an SSH tunnel (`ssh -L 5002:127.0.0.1:5002 user@server`, then http://127.0.0.1:5002) and log in with any user name and the password. The panel refuses to listen beyond 127.0.0.1 without a password, and Flask's debug mode is never on there.

## Notifications

The supervisor can send Telegram messages: opened and closed positions, testnet orders, testnet warnings and errors, crashed or blocked processes, kill switch changes, and a daily summary of every enabled instance. It reads the bots' databases every 30 seconds, so the bots themselves never wait on Telegram; positions opened on the same candle arrive as one message.

1. Create a bot with [@BotFather](https://t.me/BotFather) and put its token in `.env` as `TELEGRAM_BOT_TOKEN`.
2. Send any message to the bot, then run `python -m kripto_bot telegram-setup`; it prints your chat id.
3. Put the id in `.env` as `TELEGRAM_CHAT_ID`, run `telegram-setup` again for a test message, and restart `python -m kripto_bot run`.

Trade messages, the daily summary and its hour can be switched on the Settings page without a restart. Errors, crashes and the kill switch are always reported.

The bot also answers commands from that chat (and no other): `/status` lists every instance, `/instance` opens one with buttons for its positions, signals and trades. Instances with manual close get a Close button per position; it asks for confirmation and then files the same request as the panel's Close button. While the supervisor runs it reads the bot's updates, so stop it before running `telegram-setup`.

**The panel on a phone:** with [Tailscale](https://tailscale.com) on the Mac and the phone, run `tailscale serve --bg 5002` on the Mac and open the `https://….ts.net` address it prints. Only your own devices can reach it; set `DASHBOARD_PASSWORD` anyway. Details in `docs/telegram-commands-and-mobile.md`.

## Notes and limitations

- Each instance keeps its own state; the dashboard only accepts changes to an instance's description and running state, so results always belong to one setup.
- In paper trading, stops are evaluated when a candle closes (as if a stop order had been resting at the stop price), not tick by tick.
- Times in logs are UTC candle times.
- A second copy of the same instance refuses to start (`*.lock` files in `runtime/`).
- The dashboard listens on 127.0.0.1 by default and requires an `X-Requested-With: kripto-bot` header on every change, so other websites can't drive it. With `DASHBOARD_PASSWORD` set it also requires HTTP basic auth (see *Running 24/7*).
- In the grid backtest strategies run with their default parameters, so its walk-forward folds are plain out-of-sample windows. Parameter optimization happens in the research runner.
