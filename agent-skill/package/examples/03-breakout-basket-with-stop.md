# Example 3: a basket, a stop, an invalid symbol, and an in-sample result that fades

## Request

> "Test a 20-day breakout on the big coins: buy a new 20-day high, sell on a
> 10-day low, 2 ATR stop. BTC, ETH, SOL, BNB, XRP, DOGE and NOPE."

## Mapping

- Rule: breakout above the previous 20-candle high, exit below the previous
  10-candle low. That is **v10** with `entry=20 exit_len=10`. (v6 would add an
  ATR buffer and use one length for both sides, which is not what was asked.)
- The stop is an engine option: `--stop 2`.
- One stated rule, no tuning: `backtest`.

## Command

```bash
python scripts/backtest.py backtest --symbols BTC/USDT ETH/USDT SOL/USDT BNB/USDT XRP/USDT DOGE/USDT NOPE/USDT \
    --timeframe 1d --days 1460 --end 2026-11-01 --strategy v10 --params entry=20 exit_len=10 --stop 2
```

## Output (run on 2026-11-10, per-symbol table shortened to the OOS rows)

```
data: BTC/USDT 1d: 1460 closed candles, 2022-11-02 00:00:00 -> 2026-10-31 00:00:00
...
data: NOPE/USDT skipped (Binance rejected the request for NOPEUSDT: {"code":-1121,"msg":"Invalid symbol."})

Backtest: v10 {'entry': 20, 'exit_len': 10} | stop: ATR x 2.0 | long-only, signals at the close, fills at the next open
Costs: fee 0.100% per side, slippage 5.0 bps | in-sample before 2025-08-19 07:12, out-of-sample after

Per symbol (Sharpe: per-candle returns, annualized)
   Symbol Segment      Start        End  Return%  BuyHold%  StressReturn%  Trades  WinRate%  ProfitFactor  MaxDD%  Sharpe  Exposure%
 BTC/USDT     OOS 2025-08-20 2026-10-31    -6.58    -32.11          -7.49       7     28.57          0.73   20.55   -0.15      37.90
 ETH/USDT     OOS 2025-08-20 2026-10-31   -44.62    -47.98         -45.27       9     11.11          0.07   44.62   -1.73      29.45
 SOL/USDT     OOS 2025-08-20 2026-10-31    -6.40    -49.16          -7.35       7     42.86          0.71   30.20   -0.05      31.96
 BNB/USDT     OOS 2025-08-20 2026-10-31    24.91    -19.00          23.98       5     40.00          3.68   32.60    0.67      52.28
 XRP/USDT     OOS 2025-08-20 2026-10-31   -19.03    -57.58         -19.71       6     16.67          0.38   32.33   -0.42      19.63
DOGE/USDT     OOS 2025-08-20 2026-10-31   -13.11    -65.88         -13.73       5     40.00          0.44   27.92   -0.22      24.20

Equal-weight portfolio (Sharpe: daily returns, annualized)
Segment  Symbols  Return%  BuyHold%  StressReturn%  Sharpe  BuyHoldSharpe  MaxDD%  BuyHoldMaxDD%  Trades  WinRate%  ProfitFactor  AvgTrade%
    ALL        6   160.26    175.12         152.49    0.95           0.72   30.78          63.34     130     37.69          1.66       7.71
     IS        6   185.29    380.89         179.26    1.32           1.23   28.25          46.79      92     41.30          2.20      11.53
    OOS        6   -11.09    -45.41         -11.90   -0.34          -0.64   21.71          63.34      39     28.21          0.59      -1.83

Warnings:
  - NOPE/USDT is not in the results: Binance rejected the request for NOPEUSDT: {"code":-1121,"msg":"Invalid symbol."}
  - The out-of-sample return is -11.1%: the strategy lost money after costs. Losing less than buy and hold is not an edge by itself.
  - Survivorship bias: the symbols were chosen today, so coins that were delisted or collapsed are missing and results look better than a real-time selection would.
  - Sharpe fell from 1.32 in-sample to -0.34 out-of-sample: if the parameters were picked after looking at results, this is the overfitting pattern.
  - These parameters were fixed by hand. If they were chosen after trying several values on the same data, the out-of-sample segment is no longer out-of-sample: use the walkforward command.
```

## Report to the user

```
Strategy: v10, buy a close above the previous 20-day high, exit below the previous 10-day low, 2 ATR stop
Data: BTC, ETH, SOL, BNB, XRP, DOGE vs USDT (NOPE/USDT does not exist on Binance spot and was left out),
      daily, 2022-11-02 to 2026-10-31, 0.1% fee + 5 bps slippage per fill
Test: fixed parameters; in-sample before 2025-08-19, out-of-sample after (last 30%)

                 In-sample    Out-of-sample   Buy & hold (OOS)
Return %         +185.3       -11.1           -45.4
Max drawdown %   28.3         21.7            63.3
Sharpe           1.32         -0.34           -0.64
Trades           92           39
Win rate %       41.3         28.2
Profit factor    2.20         0.59
OOS return with costs x1.5: -11.9%

Warnings
- Out-of-sample the strategy lost 11%. It lost less than holding (-45%) because
  it was in the market only about a third of the time, not because the
  breakouts worked: profit factor 0.59, five of six coins negative.
- In-sample it made +185%, but holding made +381% in the same period. The
  breakout rule lagged a bull market and then lost in a falling one.
- Six large coins chosen today: survivorship bias applies.

Verdict: No evidence that 20-day breakouts with a 2 ATR stop add value on these
coins. The only consistent effect is a smaller drawdown from being in cash more
often.
Research and education only. Not financial advice.
```

Points to notice: the invalid symbol is reported, not silently dropped; a
negative out-of-sample return is not dressed up as "beat buy and hold by 34
points"; the in-sample figure is put next to its own benchmark. If the user
now asks "what about a 55-day breakout?", that is tuning: switch to
`walkforward` (v10's grid covers 20/10, 55/20, 55/10, 100/50, 100/20) and count
it as a second idea.
