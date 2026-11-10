# Example 1: a stated rule, mapped to a built-in, fixed-parameter backtest

## Request

> "Does it work to hold BTC and ETH only while the price is above its 50-day
> moving average, and sit in cash otherwise? Last four years."

## Mapping

- Rule: long while close > SMA(50), exit below. That is **v9** with
  `fast=1 slow=50` (`fast=1` is the close itself). No difference from the
  description.
- The user stated one rule and is not asking for tuning, so `backtest`.
- Daily candles, 1460 days, no stop (none was asked for), default costs.
  `--end` is fixed here so the example can be reproduced.

## Command

```bash
python scripts/backtest.py backtest --symbols BTC/USDT ETH/USDT --timeframe 1d --days 1460 \
    --end 2026-11-01 --strategy v9 --params fast=1 slow=50
```

## Output (run on 2026-11-10)

```
data: BTC/USDT 1d: 1460 closed candles, 2022-11-02 00:00:00 -> 2026-10-31 00:00:00
data: ETH/USDT 1d: 1460 closed candles, 2022-11-02 00:00:00 -> 2026-10-31 00:00:00

Backtest: v9 {'fast': 1, 'slow': 50, 'entry_band': 0.0} | stop: none | long-only, signals at the close, fills at the next open
Costs: fee 0.100% per side, slippage 5.0 bps | in-sample before 2025-08-19 07:12, out-of-sample after

Per symbol (Sharpe: per-candle returns, annualized)
  Symbol Segment      Start        End  Return%  BuyHold%  StressReturn%  Trades  WinRate%  ProfitFactor  MaxDD%  Sharpe  Exposure%
BTC/USDT     ALL 2022-11-02 2026-10-31   390.04    284.99         361.51      40     30.00          2.70   26.79    1.36      59.86
BTC/USDT      IS 2022-11-02 2025-08-19   370.72    460.11         353.40      25     40.00          4.28   26.79    1.70      64.77
BTC/USDT     OOS 2025-08-20 2026-10-31     4.10    -32.11           1.79      15     13.33          1.17   22.54    0.26      48.17
ETH/USDT     ALL 2022-11-02 2026-10-31   338.00     48.56         316.84      33     30.30          2.98   37.88    1.07      53.22
ETH/USDT      IS 2022-11-02 2025-08-19   253.76    168.42         239.21      28     28.57          3.32   37.88    1.24      55.28
ETH/USDT     OOS 2025-08-20 2026-10-31    16.02    -47.98          14.98       6     33.33          1.81   29.79    0.51      48.17

Equal-weight portfolio (Sharpe: daily returns, annualized)
Segment  Symbols  Return%  BuyHold%  StressReturn%  Sharpe  BuyHoldSharpe  MaxDD%  BuyHoldMaxDD%  Trades  WinRate%  ProfitFactor  AvgTrade%
    ALL        2   387.28    155.04         361.32    1.31           0.71   31.03          59.93      73     30.14          2.82       5.37
     IS        2   324.11    307.50         307.59    1.59           1.20   31.03          48.01      53     33.96          3.81       6.69
    OOS        2    11.17    -39.72           9.43    0.44          -0.57   24.16          59.93      21     19.05          1.45       1.38

Warnings:
  - Only 21 out-of-sample trades (fewer than 30): win rate, profit factor and Sharpe are not reliable on a sample this small.
  - Survivorship bias: the symbols were chosen today, so coins that were delisted or collapsed are missing and results look better than a real-time selection would.
  - Sharpe fell from 1.59 in-sample to 0.44 out-of-sample: if the parameters were picked after looking at results, this is the overfitting pattern.
  - These parameters were fixed by hand. If they were chosen after trying several values on the same data, the out-of-sample segment is no longer out-of-sample: use the walkforward command.
```

## Report to the user

```
Strategy: v9, long while the close is above SMA(50), flat below (as you described)
Data: BTC/USDT and ETH/USDT, daily, 2022-11-02 to 2026-10-31, 0.1% fee + 5 bps slippage per fill
Test: fixed parameters; in-sample before 2025-08-19, out-of-sample after (last 30%)

                 In-sample    Out-of-sample   Buy & hold (OOS)
Return %         +324.1       +11.2           -39.7
Max drawdown %   31.0         24.2            59.9
Sharpe           1.59         0.44            -0.57
Trades           53           21
Win rate %       34.0         19.1
Profit factor    3.81         1.45
OOS return with costs x1.5: +9.4%

Warnings
- 21 out-of-sample trades is too few to trust the win rate, profit factor or Sharpe.
- Sharpe dropped from 1.59 to 0.44 out-of-sample. In-sample was a strong bull
  market in which buy and hold made +307%, so most of that figure is the market.
- BTC and ETH are today's survivors; the result says nothing about smaller coins.

Verdict: In the last 14 months the filter did what trend filters do: it stayed
out of most of a -40% decline and cut the drawdown from 60% to 24%, ending
slightly positive. In the rising market before that it roughly matched holding.
That is evidence of risk reduction on two coins over one cycle, not of an edge
in returns, and the out-of-sample sample is small.
Research and education only. Not financial advice.
```

Points to notice: the ALL row (+387%) is never the headline; win rate of 19%
is normal for a trend rule and is read together with the profit factor; the
verdict separates "lower drawdown" from "higher return".
