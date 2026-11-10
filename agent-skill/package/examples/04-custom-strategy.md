# Example 4: a custom strategy, the lookahead checks, and a strategy that fails them

## Request

> "Backtest a MACD strategy on BTC, ETH and SOL daily: buy when MACD crosses
> above its signal line, but only if the price is above the 200-day average.
> Sell when MACD crosses back down. Try the usual MACD settings."

## Mapping

- None of v1-v10 computes MACD or its signal line, and v5 (EMA cross) has no
  signal-line cross. This needs a **custom strategy**.
- Read `references/pitfalls.md`, copy `assets/strategy_template.py` (which
  implements exactly this rule) to `macd_trend.py`.
- "Try the usual settings" means choosing between variants: `walkforward` with
  a small grid declared in the file (`PARAM_GRID`: MACD 12/26 or 8/21, trend
  average 100 or 200 = 4 sets).
- A daily MACD strategy trades rarely, so `--min-train-trades 3` instead of 10.

## Step 1: check

```bash
python scripts/backtest.py check --strategy-file macd_trend.py --symbols BTC/USDT ETH/USDT \
    --timeframe 1d --days 1460 --end 2026-11-01
```

```
Strategy checks (shape, future changes, prefix):
  [given parameters] synthetic candles: ok (6 BUY, 19 SELL)
  [given parameters] synthetic candles (2): ok (0 BUY, 22 SELL)
  [given parameters] BTC/USDT candles: ok (34 BUY, 49 SELL)
  [given parameters] ETH/USDT candles: ok (28 BUY, 48 SELL)
  5 parameter sets, 20 runs in total: ok
  passed

Passing means no lookahead was found, not that the strategy is profitable.
```

## Step 2: walk-forward

```bash
python scripts/backtest.py walkforward --strategy-file macd_trend.py --symbols BTC/USDT ETH/USDT SOL/USDT \
    --timeframe 1d --days 1460 --end 2026-11-01 --min-train-trades 3
```

```
Walk-forward: custom | 4 parameter sets x 1 stop settings = 4 per fold | 5 folds, rolling training window, first 40% train-only

Out-of-sample per symbol (TrainSharpe: median in-sample Sharpe of the chosen parameters)
  Symbol  Return%  BuyHold%  StressReturn%  DefaultParams%  Trades  WinRate%  ProfitFactor  MaxDD%  Sharpe  TrainSharpe  TrainPositive% ParamChanges
BTC/USDT   -20.71     11.93         -22.83          -27.53      18     33.33          0.65   37.55   -0.42         0.84           70.00          2/4
ETH/USDT   -29.33    -38.73         -31.32          -25.39      19     21.05          0.74   47.12   -0.43         0.28           50.00          2/4
SOL/USDT    11.61    -39.47           9.30          -12.38      14     42.86          1.41   38.26    0.30         1.81           85.00          1/4

Equal-weight out-of-sample portfolio (Sharpe: daily returns, annualized)
Segment  Symbols  Return%  BuyHold%  StressReturn%  Sharpe  BuyHoldSharpe  MaxDD%  BuyHoldMaxDD%  Trades  WinRate%  ProfitFactor  AvgTrade%  PSR
    OOS        3   -11.17    -19.15         -13.41   -0.14           0.14   33.50          64.31      51     31.37          0.90      -0.31 0.41

Warnings:
  - The out-of-sample return is -11.2%: the strategy lost money after costs. Losing less than buy and hold is not an edge by itself.
  - Out-of-sample Sharpe is not above buy and hold: no risk-adjusted advantage over simply holding.
  - Survivorship bias: the symbols were chosen today, so coins that were delisted or collapsed are missing and results look better than a real-time selection would.
  - Median Sharpe drops from 0.84 on the training windows to -0.42 out-of-sample: the optimizer is fitting noise.
  - PSR is 0.41: the probability that the true Sharpe is above zero is below 95%, so the result is not statistically distinguishable from luck.
  - If this is not the first strategy idea tested on this data, rerun with --trials N (the number of ideas tried so far) to get the deflated Sharpe.
```

## Report to the user

```
Strategy: custom, MACD crossing above its signal line while the close is above SMA(trend);
          exit when MACD crosses back below. Code: macd_trend.py (passed the lookahead checks)
Data: BTC/USDT, ETH/USDT, SOL/USDT, daily, 2022-11-02 to 2026-10-31, 0.1% fee + 5 bps slippage per fill
Test: walk-forward, 5 test windows from 2024-06-08; 4 parameter sets
      (MACD 12/26 or 8/21, trend SMA 100 or 200) chosen on each training window

                              Training (chosen sets)   Out-of-sample   Buy & hold (OOS)
Return %                      n/a                      -11.2           -19.2
Max drawdown %                n/a                      33.5            64.3
Sharpe                        0.84 (median)            -0.14           0.14
Trades                                                 51
Win rate %                                             31.4
Profit factor                                          0.90
OOS return with costs x1.5: -13.4%
PSR 0.41

Warnings
- Negative out-of-sample return and a Sharpe below buy and hold.
- Settings that scored a Sharpe of 0.84 in training scored -0.42 afterwards.
- Results differ by coin (SOL +12%, BTC -21%, ETH -29%); with 14 to 19 trades
  each, none of those per-coin figures is reliable.

Verdict: The MACD cross with a 200-day filter did not show an edge on these
three coins over the last 2.4 years; its only visible effect is a smaller
drawdown than holding.
Research and education only. Not financial advice.
```

## What a failed check looks like

`examples/lookahead_strategy.py` buys when the next candle closes higher. Its
"backtest" would be spectacular. The tool refuses to produce one:

```bash
python scripts/backtest.py backtest --strategy-file examples/lookahead_strategy.py --symbols BTC/USDT --days 1460
```

```
Strategy checks (shape, future changes, prefix):
  WARNING line 14: shift() with a negative period reads later candles: next_close = close.shift(-1)
  FAILED [given parameters] synthetic candles: lookahead: changing candles after row 175 changed the signal at row 175 ('SELL' -> 'BUY')

The strategy was NOT backtested: fix the lookahead problem first (see references/pitfalls.md).
```

Exit code 3, no metrics. The right response is to fix the rule (here: compare
the close with the previous close, `close.shift(1)`) and run `check` again,
then tell the user what was wrong. Never bypass the check, and never report
numbers from a run that failed it.
