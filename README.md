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
- **Paper trading:** `run_realtime.py` processes each newly closed candle and logs signals and trades to CSV.
- **Dashboard:** a Flask app showing cumulative PnL, win/loss and per-symbol trade distributions, the latest trade, and a backtest comparison table and chart filterable by segment, strategy, symbol and timeframe.
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
├── multi_backtest.py    # grid backtest → multi_backtest_strategies.csv
├── run_realtime.py      # paper trading on live closed candles
├── dashboard.py         # Flask dashboard (templates/)
├── plot_summary.py      # return bar chart for one strategy
├── plot_multi_chart.py  # profitable out-of-sample combinations chart
└── tests/               # pytest suite
```

Run outputs (`multi_backtest_strategies.csv`, `signals_log.csv`, `trades_log.csv`) are written to the working directory and are not tracked by git. Run the backtest or the realtime loop first so the dashboard has data to show.

## Getting started

Requirements: Python 3.10+.

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt

# Optional: API keys are not needed for market data or paper trading
cp .env.example .env
```

Run the pieces:

```bash
python multi_backtest.py   # grid backtest, writes multi_backtest_strategies.csv
python multi_backtest.py --symbols BTC/USDT ETH/USDT --timeframes 5m --strategies v1 v6 --days 30
python run_realtime.py     # paper trading on live data
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

## Notes and limitations

- Paper positions live in memory only and are lost when `run_realtime.py` restarts.
- In paper trading, stops are evaluated when a candle closes (as if a stop order had been resting at the stop price), not tick by tick.
- Times in logs are UTC candle times.
- Strategy parameters are not optimized yet, so walk-forward folds are plain out-of-sample windows.
