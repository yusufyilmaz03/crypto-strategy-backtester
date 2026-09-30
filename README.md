# Crypto Strategy Backtester

A Python research tool for testing technical trading strategies on historical crypto market data, with paper trading and a small web dashboard for analyzing the results.

> **For research and education only.** This project does not place real orders and is not financial advice.

## What it does

- **Market data:** pulls OHLCV candles from Binance through [ccxt](https://github.com/ccxt/ccxt) (public endpoints, no API key needed).
- **Indicators:** RSI, EMA (9/21/50), ATR, Bollinger Bands and Donchian channels, computed with pandas.
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
- **Multi-backtest:** runs every strategy on every symbol and timeframe (45 pairs × 3 timeframes × 7 strategies, about 1,000 combinations) and writes a summary CSV.
- **Realistic costs:** each trade is charged taker fees and slippage (configurable in basis points).
- **Paper trading:** `run_realtime.py` generates live signals and simulates entries/exits with ATR stops, logging signals and trades to CSV.
- **Dashboard:** a Flask app showing cumulative PnL, win/loss and per-symbol trade distributions, the latest trade, and a backtest comparison table and chart filterable by strategy, symbol and timeframe.

## Project structure

```
├── config.py            # symbols, timeframes, strategy, stop-loss, fee and slippage settings
├── indicators.py        # RSI, EMA, ATR
├── strategy.py          # signal generators v1–v7
├── multi_backtest.py    # grid backtest → multi_backtest_strategies.csv
├── paper_trader.py      # simulated position management
├── run_realtime.py      # live signal loop with paper trading
├── dashboard.py         # Flask dashboard (templates/)
├── plot_summary.py      # PnL bar chart for one strategy
└── plot_multi_chart.py  # profitable combinations chart
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
python run_realtime.py     # live signals + paper trading
python dashboard.py        # http://127.0.0.1:5002
```

Adjust symbols, timeframes, strategy, stop-loss mode, fees and slippage in `config.py`.

## Notes and limitations

- PnL is reported in the quote currency for a position size of 1 unit, so results are not directly comparable across assets with very different prices. Percentage returns per trade would be the next step for a fair comparison.
- Backtests use a limited window of recent candles (up to 800 per symbol/timeframe), so results reflect a short market period.
