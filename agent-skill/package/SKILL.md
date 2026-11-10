---
name: crypto-strategy-backtester
description: Backtest or walk-forward test a rule-based crypto trading strategy on Binance public candles and report the results honestly (return vs buy and hold, win rate, profit factor, max drawdown, Sharpe, in-sample vs out-of-sample). Use when the user describes a strategy ("buy when RSI is below 30 and...", "price above the 50-day average", "Donchian breakout") and asks whether it works, asks to backtest, optimize or validate it, or asks to check strategy code for lookahead bias. Research and education only, not financial advice. No API key, no orders.
license: PolyForm-Noncommercial-1.0.0 (noncommercial use only; full terms in LICENSE)
---

# Crypto strategy backtester

Research and education only. Not financial advice. This skill simulates trades on
past candles. It needs no API key, reads no credentials and cannot send an order.

## What it does

`scripts/backtest.py` downloads closed candles from Binance's public market-data
endpoint, runs a long-only strategy through a bar-by-bar engine and prints
metrics. Two test modes:

- `backtest`: fixed parameters, split chronologically into in-sample (IS) and
  out-of-sample (OOS).
- `walkforward`: parameters are chosen on each training window and applied to
  the next test window; only test windows are reported. Use this whenever
  parameters are being chosen or tuned.

Engine rules (the same for every run): signals are computed at a candle's close
and filled at the next candle's open; fees (0.1% per side) and slippage (5 bps)
are charged on every fill; the whole equity goes into each position, no
leverage; a SELL closes the long, it does not short. Unclosed candles are
dropped by the data loader.

## Setup (once)

Python 3.10 or newer. From this skill's folder:

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

Then run every command with `.venv/bin/python scripts/backtest.py ...`. The
only dependencies are pandas and certifi. Candles are cached in
`~/.cache/crypto-strategy-backtester/ohlcv` (change with `--data-dir`); nothing
else is written unless `--json` is passed. Network access is needed for
`data-api.binance.vision` (fallback `api.binance.com`), or use `--offline` with
a filled cache.

## Workflow

1. **Pin down the request.** Symbols (Binance spot pairs such as `BTC/USDT`),
   timeframe, period, entry rule, exit rule, stop. Ask only for what is missing
   and cannot be defaulted; say which defaults were used (1d candles, 730 days,
   no stop, 0.1% fee, 5 bps slippage).
2. **Map the strategy to a built-in first.** Read `references/strategies.md`
   and pick one of v1-v10 with parameters. Most described strategies fit:
   moving-average filters and crosses (v9, v5), RSI with a trend filter
   (v1-v3, v7), Bollinger mean reversion (v4), breakouts (v6, v10), momentum
   (v8). Tell the user which built-in was used and what differs from their
   description, if anything.
3. **Only if no built-in fits, write a custom strategy.** Copy
   `assets/strategy_template.py`, follow its contract, and read
   `references/pitfalls.md` before writing. Run
   `scripts/backtest.py check --strategy-file FILE --symbols ...` and fix every
   failure. The `backtest` and `walkforward` commands rerun the checks and
   refuse a strategy that fails them. Never edit the checks, never work around
   them, and never report results from a strategy that failed.
4. **Choose the test.** One fixed rule the user stated in advance: `backtest`.
   Anything involving "best parameters", "optimize" or comparing variants:
   `walkforward`. Do not run `backtest` repeatedly with different parameters
   and report the best one; that is overfitting by hand.
5. **Run it** (commands below). Use enough history: the first candles are
   indicator warm-up, so a 200-candle average needs far more than 200 candles.
6. **Report** with the template below. Include every warning the tool printed.

## Commands

```bash
# List built-in strategies, parameters, defaults and walk-forward grids
python scripts/backtest.py strategies

# Fixed parameters, IS vs OOS (last 30% of the period is OOS)
python scripts/backtest.py backtest --symbols BTC/USDT ETH/USDT --timeframe 1d --days 1460 \
    --strategy v9 --params fast=1 slow=50

# Walk-forward optimization over the strategy's grid, 5 test windows
python scripts/backtest.py walkforward --symbols BTC/USDT ETH/USDT SOL/USDT --timeframe 4h --days 730 \
    --strategy v4 --stops none 2

# Custom grid: one parameter, or parameters that vary together
python scripts/backtest.py walkforward --symbols BTC/USDT --timeframe 1d --days 1460 \
    --strategy v9 --grid fast+slow=1/50,1/100,1/200

# Custom strategy: check, then test
python scripts/backtest.py check --strategy-file my_strategy.py --symbols BTC/USDT --timeframe 1d
python scripts/backtest.py walkforward --strategy-file my_strategy.py --symbols BTC/USDT ETH/USDT --days 1460
```

Options shared by `backtest` and `walkforward`:

| Option | Meaning | Default |
|---|---|---|
| `--symbols` | Binance spot pairs | required |
| `--timeframe` | 1m 3m 5m 15m 30m 1h 2h 4h 6h 8h 12h 1d 3d 1w | 1d |
| `--days` | history length before `--end` | 730 |
| `--end YYYY-MM-DD` | fix the window's end to make a run reproducible | now |
| `--strategy` / `--strategy-file` | built-in key, or a custom file | one is required |
| `--fee`, `--slippage-bps` | cost per side, adverse slippage per fill | 0.001, 5 |
| `--cost-stress` | cost multiplier of the stress run | 1.5 |
| `--trail-atr X` | trailing exit at highest close since entry minus X x ATR | off |
| `--json FILE` | write the full result as JSON | off |
| `--offline`, `--data-dir` | cache only; cache location | off |

`backtest` only: `--params name=value ...`, `--stop none|X` (ATR multiple),
`--oos-fraction` (0.3). `walkforward` only: `--grid`, `--stops none 2 3`,
`--folds` (5), `--min-train-fraction` (0.4), `--anchored`,
`--min-train-trades` (10), `--trials N` (strategy ideas tried so far, for the
deflated Sharpe).

Exit codes: 0 done, 2 bad arguments or no data, 3 the custom strategy failed
the lookahead checks (no results are produced).

## Reporting results

Definitions are in `references/metrics.md`. Always report, from the
"Equal-weight portfolio" table (and per symbol when there are few symbols):

```
Strategy: <key and parameters, or "custom: <rule in one sentence>">
Data: <symbols>, <timeframe>, <start> to <end>, costs <fee> + <slippage>
Test: <backtest IS/OOS split at <date> | walk-forward, N folds, grid of M>

                 In-sample    Out-of-sample   Buy & hold (OOS)
Return %         ...          ...             ...
Max drawdown %   ...          ...             ...
Sharpe           ...          ...             ...
Trades           ...          ...
Win rate %       ...          ...
Profit factor    ...          ...
OOS return with costs x1.5: ...

Warnings: <every warning the tool printed, in plain words>
Verdict: <one or two sentences: what the evidence supports and what it does not>
Research and education only. Not financial advice.
```

Rules for the write-up:

- OOS numbers lead. Never present the ALL or IS row as the result.
- For `walkforward` there is no IS return to show: report the median
  `TrainSharpe` next to the OOS Sharpe, and PSR (and DSR when `--trials` was
  given).
- Always put buy and hold next to the strategy. A strategy that made 40% while
  holding made 120% did not "work".
- Say what kind of evidence it is: a small sample, one market regime, symbols
  picked today (survivorship bias). Fewer than 30 OOS trades means the win rate
  and profit factor are noise.
- Do not call a strategy profitable, safe or ready to trade, and do not suggest
  position sizes or amounts. If the user asks whether to trade it, say that
  this is not financial advice and describe what the test does and does not
  show.
- If the result is bad, say so plainly. Most simple strategies do not beat buy
  and hold after costs; that is a valid and useful result.

## Files

- `scripts/backtest.py`: the command line (strategies, backtest, walkforward, check).
- `scripts/strategy_checks.py`: lookahead checks for custom strategies.
- `scripts/kripto_bot/`: engine, indicators, strategies v1-v10, metrics,
  walk-forward optimization, public-candle loader.
- `references/strategies.md`: v1-v10, parameters, how to map a description.
- `references/metrics.md`: what each number means and how it is computed.
- `references/pitfalls.md`: lookahead bias, unclosed candles, overfitting,
  survivorship bias, costs; required reading before writing strategy code.
- `assets/strategy_template.py`: starting point for a custom strategy.
- `examples/`: four worked scenarios with real commands and output.

## Limits

Long-only spot, one position per symbol, full equity per trade. No shorting,
leverage, futures, funding, order book, partial fills or portfolio sizing. It
does not place orders, on a testnet or anywhere else, and must not be extended
to do so.
