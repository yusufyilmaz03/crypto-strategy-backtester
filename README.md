# Crypto Strategy Backtester

A Python research tool for testing technical trading strategies on historical crypto market data, with paper trading and a small web dashboard for analyzing the results.

> **For research and education only.** This project does not place real orders and is not financial advice.

## What it does

- **Market data:** pulls OHLCV candles from Binance through [ccxt](https://github.com/ccxt/ccxt) (public endpoints, no API key needed).
- **Market data cache:** any lookback is downloaded in pages and cached under `data/`; later runs only fetch new candles. Candles that have not closed yet are always dropped.
- **Indicators:** RSI (Wilder), EMA (9/21/50), ATR, Bollinger Bands and Donchian channels, computed with pandas.
- **7 strategies:**
  | Key | Idea |
  |-----|------|
  | v1 | RSI + EMA crossover |
  | v2 | RSI + EMA, filtered on the last 3 candles |
  | v3 | RSI + EMA with ATR-based stop loss |
  | v4 | Bollinger Band mean reversion with an RSI filter |
  | v5 | EMA cross, trading only in the trend direction |
  | v6 | Donchian (20) breakout with an ATR threshold against false breakouts |
  | v7 | Trend filter (EMA21 vs EMA50) + RSI pullback |
- **One engine for backtest and paper trading:** the same bar-by-bar engine drives both, so paper results follow the backtest rules exactly (see *Simulation rules*).
- **Multi-backtest:** runs every strategy on every symbol and timeframe and reports in-sample, out-of-sample and walk-forward results with return, buy-and-hold return, win rate, profit factor, max drawdown, Sharpe and exposure.
- **Strategy research:** walk-forward optimization of each strategy's parameters on 15m/1h/4h data, with out-of-sample portfolio results, a cost stress test and a deflated Sharpe ratio to guard against overfitting.
- **Paper trading:** `run_realtime.py` processes each newly closed candle. State, trades, signals and the equity curve live in SQLite (`paper.db`), so a restart resumes where it stopped and first processes the candles it missed.
- **Dashboard:** a Flask app showing paper trading return, win rate, max drawdown and the portfolio equity curve, cumulative PnL, win/loss and per-symbol trade distributions, the latest trade, and a backtest comparison table and chart filterable by segment, strategy, symbol and timeframe.
- **Tests:** pytest suite covering data loading, indicators, look-ahead checks for every strategy, the engine, metrics and the paper loop.

## Project structure

```
├── config.py            # symbols, timeframes, strategy, stop-loss, costs, evaluation settings
├── data.py              # paginated OHLCV download + local cache, closed candles only
├── indicators.py        # RSI (Wilder), EMA, ATR
├── strategy.py          # signal generators v1–v7 (vectorized)
├── engine.py            # bar-by-bar trading engine shared by backtest and paper trading
├── metrics.py           # performance metrics
├── evaluation.py        # in-sample/out-of-sample and walk-forward splits
├── optimize.py          # walk-forward parameter optimization
├── research.py          # strategy research across symbols/timeframes → research_*.csv
├── multi_backtest.py    # grid backtest → multi_backtest_strategies.csv
├── run_realtime.py      # paper trading on live closed candles
├── store.py             # SQLite persistence for paper trading (paper.db)
├── rotation.py          # cross-sectional momentum rotation (R1)
├── forward_report.py    # forward-test report: paper results vs buy-and-hold
├── broker.py            # order execution (ccxt; testnet by default) and a fake broker for tests
├── run_testnet.py       # order-sending loop on Binance Spot Testnet
├── run_all.py           # supervisor: dashboard + one process per enabled instance
├── instances.py         # named bot instances (instances.json)
├── settings.py          # settings editable from the dashboard (settings.json)
├── playground.py        # interactive backtests for the dashboard
├── reporting.py         # read-side helpers for the dashboard
├── testnet_check.py     # end-to-end check of the testnet order flow
├── docs/                # research pre-registrations and results
├── dashboard.py         # Flask control panel (templates/, static/)
├── plot_summary.py      # return bar chart for one strategy
├── plot_multi_chart.py  # profitable out-of-sample combinations chart
└── tests/               # pytest suite
```

Run outputs (`multi_backtest_strategies.csv`, `research_*.csv`, `paper.db`) are written to the working directory and are not tracked by git. Run the backtest or the paper trading loop first so the dashboard has data to show.

## Getting started

Requirements: Python 3.10+.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Optional: API keys are not needed for market data or paper trading
cp .env.example .env
```

Run the pieces:

Everything day-to-day runs from the control panel:

```bash
python run_all.py          # dashboard + every enabled bot instance; open http://127.0.0.1:5002
```

The panel has:

- **Overview**: supervisor and kill switch status, every instance with return, buy-and-hold and drawdown.
- **Instances**: create named strategy setups (paper or testnet), start/stop/restart them, and see each one's equity vs buy-and-hold, positions, trades, signals and live log. Several paper instances can run in parallel; one testnet instance at a time (they share one exchange account). An instance's setup is fixed after creation.
- **Playground**: backtest any symbol, timeframe, strategy, parameters and stop on a candlestick chart with trade markers and indicator overlays. Runs are logged; unused holdout data is left out unless requested.
- **Settings**: costs, risk limits, symbol list and research settings with validation (stored in `settings.json`). API keys and the live trading switch can't be changed from the panel.
- **Backtests**: results of `multi_backtest.py`.

`run_all.py` runs one process per enabled instance (see `instances.json`), restarts crashed ones and writes logs to `logs/`. Ctrl+C stops everything.

Individual scripts:

```bash
python multi_backtest.py   # grid backtest, writes multi_backtest_strategies.csv
python multi_backtest.py --symbols BTC/USDT ETH/USDT --timeframes 5m --strategies v1 v6 --days 30
python research.py         # walk-forward research, writes research_*.csv
python run_realtime.py     # paper trading on live data (forward test, see docs/forward-test.md)
python forward_report.py   # forward-test metrics vs buy-and-hold
python dashboard.py        # http://127.0.0.1:5002
```

Adjust symbols, timeframes, strategy, stop-loss mode, costs, history length and the out-of-sample split in `config.py`.

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

`research.py` runs a walk-forward optimization for every strategy on every symbol and research timeframe (`RESEARCH_TIMEFRAMES` in `config.py`):

1. The history is split into a first training block and `WFO_FOLDS` consecutive test windows (rolling training window of fixed length).
2. In each fold, every combination of the strategy's parameter grid (`PARAM_GRIDS` in `strategy.py`) and stop multiplier is backtested on the training window. The best by Sharpe, among those with at least `WFO_MIN_TRADES` trades, is run on the next test window. If none qualifies, the fold stays flat.
3. Test windows are chained into one out-of-sample equity curve per symbol. The same windows are re-run with fees and slippage × `COST_STRESS`.
4. Per strategy/timeframe, the symbols' out-of-sample curves form an equal-weight portfolio that is compared with an equal-weight buy-and-hold portfolio. A deflated Sharpe ratio accounts for having picked the best of all candidates.

Phase 2b (trend following and rotation on 4h/1d) was pre-registered in [docs/phase2b-preregistration.md](docs/phase2b-preregistration.md), which also lists the results. `research.py` holds out the last `HOLDOUT_FRACTION` of each timeframe; `research.py --holdout` tests only the finalists, once.

A candidate passes when its portfolio return and cost-stressed return are positive, its Sharpe beats buy-and-hold, it has at least 30 trades and its deflated Sharpe ratio is at least 0.95.

## Testnet trading

`run_testnet.py` trades the paper strategy (`STRATEGY`, `STRATEGY_PARAMS`, `TIMEFRAME`) with real orders on the [Binance Spot Testnet](https://testnet.binance.vision) (no real money):

1. Create testnet API keys and put them in `.env` as `BINANCE_TESTNET_API_KEY` / `BINANCE_TESTNET_API_SECRET`.
2. `python testnet_check.py` checks the connection; `python testnet_check.py --yes` places a small buy and sell.
3. `python run_testnet.py` starts the loop. State is kept in `testnet.db`.

Safety:

- Signals come from closed mainnet candles; each decision is sent as a market order right after the close, with a deterministic client order id so that a crash can't cause a duplicate order.
- The bot only trades positions it opened itself and checks them against the exchange balance on start. Candles missed while it was down are not traded.
- `TRADING_CAPITAL_USDT` is split equally across symbols; `MAX_ORDER_USDT` caps each order; after a `DAILY_LOSS_LIMIT_PCT` loss no new entries are made until the next UTC day.
- Kill switch: create a file named `KILL_SWITCH` to stop new entries; put the word `flatten` in it to close every position.
- Testnet prices and fees differ from mainnet (fees are often zero and order books are thin), so testnet profit and slippage numbers are not meaningful; the testnet only verifies the order flow.
- Live trading is refused unless `TRADING_MODE = "live"` **and** `LIVE_TRADING_ENABLED = True`.

## Notes and limitations

- Each instance keeps its own state; the dashboard only accepts changes to an instance's description and running state, so results always belong to one setup.
- In paper trading, stops are evaluated when a candle closes (as if a stop order had been resting at the stop price), not tick by tick.
- Times in logs are UTC candle times.
- A second copy of the same instance refuses to start (`*.lock` files).
- The dashboard listens on 127.0.0.1 only and requires an `X-Requested-With: kripto-bot` header on every change, so other websites can't drive it. Add authentication before exposing it on a network.
- In `multi_backtest.py` strategies run with their default parameters, so its walk-forward folds are plain out-of-sample windows. Parameter optimization happens in `research.py`.
