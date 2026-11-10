# Example 2: "find the best settings" means walk-forward, and the answer can be no

## Request

> "I buy ETH and SOL on the 4-hour chart when the price drops below the lower
> Bollinger band and RSI is oversold, and sell at the upper band. What are the
> best band width and RSI levels? Maybe with a 2 ATR stop."

## Mapping

- Rule: Bollinger mean reversion with an RSI filter. That is **v4**.
- "Best settings" means parameters will be chosen from results, so
  `walkforward`, never repeated `backtest` runs.
- Grid: v4's built-in grid (bb_mult 1.5/2.0/2.5, rsi_buy 25/30/35/40, rsi_sell
  60/65/70 = 36 sets), searched with and without the stop (`--stops none 2`).
- This was the third strategy idea tested in the session, so `--trials 3`.

## Command

```bash
python scripts/backtest.py walkforward --symbols ETH/USDT SOL/USDT --timeframe 4h --days 730 \
    --end 2026-11-01 --strategy v4 --stops none 2 --trials 3
```

## Output (run on 2026-11-10)

```
data: ETH/USDT 4h: 4380 closed candles, 2024-11-01 00:00:00 -> 2026-10-31 20:00:00
data: SOL/USDT 4h: 4380 closed candles, 2024-11-01 00:00:00 -> 2026-10-31 20:00:00

Walk-forward: v4 | 36 parameter sets x 2 stop settings = 72 per fold | 5 folds, rolling training window, first 40% train-only
Parameters are chosen by Sharpe on each training window and applied to the next test window. Only the test windows are reported as out-of-sample.

Out-of-sample per symbol (TrainSharpe: median in-sample Sharpe of the chosen parameters)
  Symbol  Return%  BuyHold%  StressReturn%  DefaultParams%  Trades  WinRate%  ProfitFactor  MaxDD%  Sharpe  TrainSharpe  TrainPositive% ParamChanges
ETH/USDT   -31.44    -45.53         -33.29          -39.65      21     23.81          0.56   39.40   -1.05         0.49           23.06          3/4
SOL/USDT   -52.46    -47.07         -54.45          -52.16      31     35.48          0.45   64.68   -1.18         0.47           14.17          2/4

Folds
  Symbol  Fold           TestStart             TestEnd                                                  ChosenParams  TrainSharpe  TestTrades  TestReturn%
ETH/USDT     1 2025-08-20 00:00:00 2025-11-15 08:00:00  {'bb_mult': 2.0, 'rsi_buy': 30, 'rsi_sell': 65, 'stop': 2.0}         1.37           9        -0.65
ETH/USDT     2 2025-11-15 12:00:00 2026-02-10 20:00:00  {'bb_mult': 1.5, 'rsi_buy': 25, 'rsi_sell': 60, 'stop': 2.0}         0.75           9       -22.99
ETH/USDT     3 2026-02-11 00:00:00 2026-05-09 08:00:00  {'bb_mult': 2.5, 'rsi_buy': 25, 'rsi_sell': 70, 'stop': 2.0}         0.49           0         0.00
ETH/USDT     4 2026-05-09 12:00:00 2026-08-04 20:00:00  {'bb_mult': 2.5, 'rsi_buy': 25, 'rsi_sell': 70, 'stop': 2.0}         0.49           2        -7.36
ETH/USDT     5 2026-08-05 00:00:00 2026-10-31 20:00:00  {'bb_mult': 2.0, 'rsi_buy': 25, 'rsi_sell': 60, 'stop': 2.0}        -0.06           1        -3.28
SOL/USDT     1 2025-08-20 00:00:00 2025-11-15 08:00:00  {'bb_mult': 2.0, 'rsi_buy': 30, 'rsi_sell': 70, 'stop': 2.0}         2.05           9       -13.96
SOL/USDT     2 2025-11-15 12:00:00 2026-02-10 20:00:00  {'bb_mult': 2.0, 'rsi_buy': 30, 'rsi_sell': 70, 'stop': 2.0}         1.25           9       -36.29
SOL/USDT     3 2026-02-11 00:00:00 2026-05-09 08:00:00  {'bb_mult': 2.0, 'rsi_buy': 25, 'rsi_sell': 70, 'stop': 2.0}         0.20           0         0.00
SOL/USDT     4 2026-05-09 12:00:00 2026-08-04 20:00:00 {'bb_mult': 1.5, 'rsi_buy': 40, 'rsi_sell': 60, 'stop': None}         0.47           7       -10.43
SOL/USDT     5 2026-08-05 00:00:00 2026-10-31 20:00:00 {'bb_mult': 1.5, 'rsi_buy': 40, 'rsi_sell': 60, 'stop': None}         0.23           6        -3.18

Equal-weight out-of-sample portfolio (Sharpe: daily returns, annualized)
Segment  Symbols  Return%  BuyHold%  StressReturn%  Sharpe  BuyHoldSharpe  MaxDD%  BuyHoldMaxDD%  Trades  WinRate%  ProfitFactor  AvgTrade%  PSR  DSR
    OOS        2   -41.63    -45.37         -43.64   -1.19          -0.54   49.29          70.94      52     30.77          0.49      -1.92 0.09 0.01

Warnings:
  - The out-of-sample return is -41.6%: the strategy lost money after costs. Losing less than buy and hold is not an edge by itself.
  - Out-of-sample Sharpe is not above buy and hold: no risk-adjusted advantage over simply holding.
  - Survivorship bias: the symbols were chosen today, so coins that were delisted or collapsed are missing and results look better than a real-time selection would.
  - Median Sharpe drops from 0.48 on the training windows to -1.12 out-of-sample: the optimizer is fitting noise.
  - Only 19% of the parameter sets had a positive Sharpe in training (median across symbols): there is little edge to select from.
  - The chosen parameters changed in 5 of 8 fold-to-fold steps: the optimum is unstable.
  - PSR is 0.09: the probability that the true Sharpe is above zero is below 95%, so the result is not statistically distinguishable from luck.
  - Deflated Sharpe is 0.01 after 3 strategy ideas tried: below 0.95, the best of that many random strategies could look this good.
```

## Report to the user

```
Strategy: v4, Bollinger(20) mean reversion with an RSI filter (your rule)
Data: ETH/USDT and SOL/USDT, 4h, 2024-11-01 to 2026-10-31, 0.1% fee + 5 bps slippage per fill
Test: walk-forward, 5 test windows from 2025-08-20; 36 parameter sets x {no stop, 2 ATR stop}
      chosen on each training window, reported only on the following test window

                              Training (chosen sets)   Out-of-sample   Buy & hold (OOS)
Return %                      n/a                      -41.6           -45.4
Max drawdown %                n/a                      49.3            70.9
Sharpe                        0.48 (median)            -1.19           -0.54
Trades                                                 52
Win rate %                                             30.8
Profit factor                                          0.49
OOS return with costs x1.5: -43.6%
PSR 0.09, deflated Sharpe 0.01 (3 ideas tried)

Warnings
- It lost money in every test window that traded, on both coins.
- The settings that looked best in training (Sharpe mostly between 0.2 and 2)
  lost afterwards, and the "best" settings changed in 5 of 8 steps. Only about
  a fifth of all settings were even positive in training.
- Optimizing barely mattered: ETH -31% against -40% with the default settings,
  SOL -52% against -52%.

Verdict: There are no "best settings" to recommend. Whatever band width and RSI
level the optimizer picked, buying dips below the band on 4h candles lost money
out-of-sample in this period, with and without the stop. Any setting that looks
good on the full chart is a fit to the past.
Research and education only. Not financial advice.
```

Points to notice: the user asked for the best parameters and the honest answer
is that the search found nothing stable; no parameter set is handed over
anyway. A negative result is reported as plainly as a positive one.
