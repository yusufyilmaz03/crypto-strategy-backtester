#!/usr/bin/env python3
# scripts/backtest.py
"""Backtest and walk-forward test rule-based crypto strategies on Binance public candles.

    python scripts/backtest.py strategies
    python scripts/backtest.py backtest    --symbols BTC/USDT --timeframe 1d --days 1460 --strategy v9 --params fast=1 slow=50
    python scripts/backtest.py walkforward --symbols BTC/USDT ETH/USDT --timeframe 4h --days 730 --strategy v4
    python scripts/backtest.py check       --strategy-file my_strategy.py

Research and education only. Not financial advice. This tool reads public market
data and simulates trades on past candles; it has no API keys and sends no orders.
"""
import argparse
import importlib.util
import inspect
import json
import math
import os
import sys

sys.dont_write_bytecode = True      # the skill folder stays as installed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pandas as pd  # noqa: E402

import strategy_checks  # noqa: E402
from kripto_bot.analysis.evaluation import evaluate_segment  # noqa: E402
from kripto_bot.analysis.optimize import walk_forward_optimize  # noqa: E402
from kripto_bot.core import data, strategy  # noqa: E402
from kripto_bot.core.engine import EngineConfig  # noqa: E402
from kripto_bot.core.indicators import add_indicators  # noqa: E402
from kripto_bot.core.metrics import expected_max_sharpe, max_drawdown_pct, probabilistic_sharpe  # noqa: E402

DISCLAIMER = ("Research and education only. Not financial advice. Past results, including "
              "out-of-sample results, do not predict future returns.")
CUSTOM_KEY = "custom"
MIN_SEGMENT_CANDLES = 30
MIN_TRADES = 30          # fewer out-of-sample trades than this is too small a sample
MIN_WFO_CANDLES = 300


class UsageError(Exception):
    pass


# ---------- arguments ----------
def _value(text):
    for cast in (int, float):
        try:
            return cast(text)
        except ValueError:
            pass
    return {"true": True, "false": False, "none": None}.get(text.lower(), text)


def parse_params(items):
    params = {}
    for item in items or []:
        if "=" not in item:
            raise UsageError(f"--params expects name=value, got '{item}'")
        name, value = item.split("=", 1)
        params[name] = _value(value)
    return params


def parse_grid(items):
    """name=v1,v2 varies one parameter; a+b=1/2,3/4 varies a and b together."""
    grid = {}
    for item in items or []:
        if "=" not in item:
            raise UsageError(f"--grid expects name=v1,v2 or a+b=1/2,3/4, got '{item}'")
        name, values = item.split("=", 1)
        names = tuple(name.split("+"))
        if len(names) == 1:
            grid[name] = [_value(v) for v in values.split(",")]
        else:
            rows = [tuple(_value(x) for x in v.split("/")) for v in values.split(",")]
            if any(len(r) != len(names) for r in rows):
                raise UsageError(f"--grid {item}: every value needs {len(names)} parts separated by '/'")
            grid[names] = rows
    return grid


def parse_stop(text):
    if str(text).lower() in ("none", "off", "0"):
        return None
    value = float(text)
    if value <= 0:
        raise UsageError("--stop must be 'none' or a positive ATR multiple")
    return value


def end_ms(text):
    if not text:
        return None
    return int(pd.Timestamp(text, tz="UTC").timestamp() * 1000)


# ---------- strategy ----------
def load_custom(path):
    if not os.path.isfile(path):
        raise UsageError(f"strategy file not found: {path}")
    spec = importlib.util.spec_from_file_location("custom_strategy", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    fn = getattr(module, "generate_signals", None)
    if not callable(fn):
        raise UsageError(f"{path} must define generate_signals(df, **params)")
    strategy.STRATEGY_DISPATCH[CUSTOM_KEY] = fn
    strategy.PARAM_GRIDS[CUSTOM_KEY] = dict(getattr(module, "PARAM_GRID", {}) or {})
    return fn


def defaults(fn):
    return {name: p.default for name, p in list(inspect.signature(fn).parameters.items())[1:]
            if p.default is not inspect.Parameter.empty}


def resolve_strategy(a):
    """(key, function, parameters) from --strategy / --strategy-file and --params."""
    if a.strategy_file:
        fn, key = load_custom(a.strategy_file), CUSTOM_KEY
    elif a.strategy:
        if a.strategy not in strategy.STRATEGY_DISPATCH:
            raise UsageError(f"unknown strategy '{a.strategy}' (run the 'strategies' command for the list)")
        key, fn = a.strategy, strategy.get_strategy(a.strategy)
    else:
        raise UsageError("give --strategy v1..v10 or --strategy-file PATH")
    params = parse_params(getattr(a, "params", None))
    known = defaults(fn)
    takes_any = any(p.kind is inspect.Parameter.VAR_KEYWORD for p in inspect.signature(fn).parameters.values())
    unknown = [p for p in params if p not in known and not takes_any]
    if unknown:
        raise UsageError(f"{key} has no parameter {unknown}; it takes {sorted(known)}")
    return key, fn, params


def engine_config(a, stop):
    return EngineConfig(fee_rate=a.fee, slippage_bps=a.slippage_bps, atr_multiplier=stop,
                        trail_atr=a.trail_atr, allow_short=False, initial_equity=a.equity)


def stressed(cfg, k):
    from dataclasses import replace
    return replace(cfg, fee_rate=cfg.fee_rate * k, slippage_bps=cfg.slippage_bps * k)


# ---------- data ----------
def load_frames(a):
    """Closed candles with indicators for every symbol that has data."""
    if a.data_dir:
        data.CACHE_DIR = os.path.abspath(a.data_dir)
    frames, skipped = {}, {}
    for symbol in a.symbols:
        symbol = symbol.upper()
        try:
            df = data.load_ohlcv(symbol, a.timeframe, a.days, now=end_ms(a.end), refresh=not a.offline)
        except (ValueError, ConnectionError) as e:
            skipped[symbol] = str(e)
            continue
        if len(df) < MIN_SEGMENT_CANDLES * 2:
            skipped[symbol] = f"only {len(df)} candles"
            continue
        frames[symbol] = add_indicators(df)
        print(f"data: {symbol} {a.timeframe}: {len(df)} closed candles, "
              f"{df['timestamp'].iloc[0]} -> {df['timestamp'].iloc[-1]}")
    for symbol, why in skipped.items():
        print(f"data: {symbol} skipped ({why})")
    if not frames:
        raise UsageError("no candles for any symbol; check the symbols, the timeframe and the network")
    return frames, skipped


def run_strategy_checks(a, fn, param_sets, frames):
    """Custom strategies must pass the lookahead checks; built-in ones are tested in the repository."""
    if not a.strategy_file:
        return []
    raw = dict(list(frames.items())[:2])
    failures, warnings, notes = strategy_checks.run_checks(fn, param_sets, raw, a.strategy_file)
    print("\nStrategy checks (shape, future changes, prefix):")
    for line in notes:
        print(f"  {line}")
    for line in warnings:
        print(f"  WARNING {line}")
    if failures:
        for line in failures:
            print(f"  FAILED {line}")
        print("\nThe strategy was NOT backtested: fix the lookahead problem first "
              "(see references/pitfalls.md).")
        sys.exit(3)
    print("  passed")
    return [f"strategy source: {w}" for w in warnings]


# ---------- portfolio statistics ----------
def _portfolio(curves):
    """Equal-weight portfolio of per-symbol curves (each rebased), as an equity curve."""
    rets = pd.concat({k: c.pct_change(fill_method=None) for k, c in curves.items()}, axis=1).sort_index()
    return (1 + rets.mean(axis=1, skipna=True).fillna(0)).cumprod()


def _daily_returns(equity):
    return equity.resample("1D").last().dropna().pct_change(fill_method=None).dropna()


def _sharpe_daily(daily):
    if len(daily) < 3 or not daily.std(ddof=1) > 0:
        return float("nan")
    return daily.mean() / daily.std(ddof=1)


def _total(curve):
    return (curve.iloc[-1] / curve.iloc[0] - 1) * 100 if len(curve) and curve.iloc[0] else float("nan")


def trade_stats(trades):
    n = len(trades)
    if not n:
        return {"Trades": 0, "WinRate%": float("nan"), "ProfitFactor": float("nan"), "AvgTrade%": float("nan")}
    pnl = trades["pnl"].astype(float)
    loss = -pnl[pnl <= 0].sum()
    return {"Trades": n, "WinRate%": (pnl > 0).mean() * 100,
            "ProfitFactor": pnl[pnl > 0].sum() / loss if loss > 0 else float("inf"),
            "AvgTrade%": float(trades["return_pct"].astype(float).mean())}


def portfolio_row(label, curves, stress_curves, closes, trades):
    eq, bh = _portfolio(curves), _portfolio(closes)
    d, d_bh = _daily_returns(eq), _daily_returns(bh)
    row = {
        "Segment": label, "Symbols": len(curves), "Start": eq.index[0], "End": eq.index[-1],
        "Return%": _total(eq), "BuyHold%": _total(bh),
        "StressReturn%": _total(_portfolio(stress_curves)),
        "Sharpe": _sharpe_daily(d) * math.sqrt(365), "BuyHoldSharpe": _sharpe_daily(d_bh) * math.sqrt(365),
        "MaxDD%": max_drawdown_pct(eq), "BuyHoldMaxDD%": max_drawdown_pct(bh),
        **trade_stats(trades),
    }
    return row, d


# ---------- warnings ----------
def common_warnings(oos, stress):
    w = []
    if not oos["Return%"] > 0:
        w.append(f"The out-of-sample return is {oos['Return%']:.1f}%: the strategy lost money after costs. "
                 "Losing less than buy and hold is not an edge by itself.")
    if oos["Trades"] < MIN_TRADES:
        w.append(f"Only {oos['Trades']} out-of-sample trades (fewer than {MIN_TRADES}): win rate, profit "
                 "factor and Sharpe are not reliable on a sample this small.")
    if oos["Return%"] > 0 and oos["StressReturn%"] <= 0:
        w.append(f"The out-of-sample return turns negative when fees and slippage are multiplied by {stress}: "
                 "the result depends on optimistic trading costs.")
    if not oos["Sharpe"] > oos["BuyHoldSharpe"]:
        w.append("Out-of-sample Sharpe is not above buy and hold: no risk-adjusted advantage over simply holding.")
    if oos["Return%"] < oos["BuyHold%"] and oos["MaxDD%"] < oos["BuyHoldMaxDD%"]:
        w.append("The return is below buy and hold but the drawdown is smaller: any benefit here is risk "
                 "reduction, not extra return.")
    if oos["ProfitFactor"] == float("inf"):
        w.append("Profit factor is infinite because there was no losing trade: that is a small-sample artifact.")
    w.append("Survivorship bias: the symbols were chosen today, so coins that were delisted or collapsed "
             "are missing and results look better than a real-time selection would.")
    return w


# ---------- output ----------
def show(title, rows, columns=None):
    df = pd.DataFrame(rows)
    if columns:
        df = df[[c for c in columns if c in df.columns]]
    print(f"\n{title}")
    with pd.option_context("display.width", 250, "display.max_columns", 30):
        print(df.to_string(index=False, float_format=lambda x: f"{x:.2f}"))


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, float):
        return None if math.isnan(x) else ("inf" if math.isinf(x) else round(x, 6))
    if isinstance(x, (pd.Timestamp,)) or type(x).__module__.startswith("numpy"):
        return _clean(x.item()) if hasattr(x, "item") and not isinstance(x, pd.Timestamp) else str(x)
    return x


def finish(a, result):
    for symbol, why in result.get("skipped", {}).items():
        result["warnings"].insert(0, f"{symbol} is not in the results: {why}")
    print("\nWarnings:")
    for line in result["warnings"]:
        print(f"  - {line}")
    print(f"\n{DISCLAIMER}")
    if a.json:
        result["disclaimer"] = DISCLAIMER
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(_clean(result), f, indent=2, default=str)
        print(f"JSON written to {a.json}")


def settings(a, key, extra):
    return {"symbols": [s.upper() for s in a.symbols], "timeframe": a.timeframe, "days": a.days, "end": a.end,
            "strategy": key, "strategy_file": a.strategy_file, "fee_rate": a.fee, "slippage_bps": a.slippage_bps,
            "cost_stress": a.cost_stress, "trail_atr": a.trail_atr, "initial_equity": a.equity,
            "long_only": True, "fill": "next candle open", **extra}


# ---------- commands ----------
SYMBOL_COLUMNS = ["Symbol", "Segment", "Start", "End", "Return%", "BuyHold%", "StressReturn%", "Trades",
                  "WinRate%", "ProfitFactor", "MaxDD%", "Sharpe", "Exposure%"]
PORTFOLIO_COLUMNS = ["Segment", "Symbols", "Return%", "BuyHold%", "StressReturn%", "Sharpe", "BuyHoldSharpe",
                     "MaxDD%", "BuyHoldMaxDD%", "Trades", "WinRate%", "ProfitFactor", "AvgTrade%"]


def cmd_backtest(a):
    key, fn, params = resolve_strategy(a)
    stop = parse_stop(a.stop)
    cfg = engine_config(a, stop)
    frames, skipped = load_frames(a)
    used = {**defaults(fn), **params}
    notes = run_strategy_checks(a, fn, [("given parameters", params)], frames)

    # One chronological cut for every symbol: the last oos_fraction of the common period.
    t0 = min(df["timestamp"].iloc[0] for df in frames.values())
    t1 = max(df["timestamp"].iloc[-1] for df in frames.values())
    cut_time = t0 + (t1 - t0) * (1 - a.oos_fraction)

    rows = []
    parts = {seg: {"eq": {}, "stress": {}, "close": {}, "trades": []} for seg in ("ALL", "IS", "OOS")}
    for symbol, df in frames.items():
        signals = fn(df, **params)
        cut = int(df["timestamp"].searchsorted(cut_time))
        for seg, rng in (("ALL", range(0, len(df))), ("IS", range(0, cut)), ("OOS", range(cut, len(df)))):
            if len(rng) < MIN_SEGMENT_CANDLES:
                continue
            m, trades, equity = evaluate_segment(df, signals, rng, cfg, a.timeframe, symbol)
            _, _, eq_stress = evaluate_segment(df, signals, rng, stressed(cfg, a.cost_stress), a.timeframe, symbol)
            rows.append({
                "Symbol": symbol, "Segment": seg, "Start": m["start"], "End": m["end"],
                "Return%": m["return_pct"], "BuyHold%": m["buy_hold_pct"],
                "StressReturn%": (eq_stress.iloc[-1] / cfg.initial_equity - 1) * 100,
                "Trades": m["trades"], "WinRate%": m["win_rate_pct"], "ProfitFactor": m["profit_factor"],
                "MaxDD%": m["max_drawdown_pct"], "Sharpe": m["sharpe"], "Exposure%": m["exposure_pct"],
            })
            p = parts[seg]
            p["eq"][symbol], p["stress"][symbol] = equity, eq_stress
            p["close"][symbol] = df.iloc[rng.start:rng.stop].set_index("timestamp")["close"]
            p["trades"].append(trades)

    portfolio = []
    for seg, p in parts.items():
        if p["eq"]:
            row, _ = portfolio_row(seg, p["eq"], p["stress"], p["close"], pd.concat(p["trades"], ignore_index=True))
            portfolio.append(row)
    by_seg = {r["Segment"]: r for r in portfolio}

    stop_text = "none" if stop is None else f"ATR x {stop}"
    print(f"\nBacktest: {key} {used} | stop: {stop_text} | long-only, signals at the close, fills at the next open")
    print(f"Costs: fee {a.fee * 100:.3f}% per side, slippage {a.slippage_bps} bps | "
          f"in-sample before {cut_time:%Y-%m-%d %H:%M}, out-of-sample after")
    show("Per symbol (Sharpe: per-candle returns, annualized)", rows, SYMBOL_COLUMNS)
    show("Equal-weight portfolio (Sharpe: daily returns, annualized)", portfolio, PORTFOLIO_COLUMNS)

    warnings = list(notes)
    if "OOS" not in by_seg:
        warnings.append("No out-of-sample segment: the history is too short. Ask for more days.")
    else:
        oos = by_seg["OOS"]
        warnings += common_warnings(oos, a.cost_stress)
        is_ = by_seg.get("IS")
        if is_ and is_["Sharpe"] > 0 and not oos["Sharpe"] > is_["Sharpe"] / 2:
            warnings.append(f"Sharpe fell from {is_['Sharpe']:.2f} in-sample to {oos['Sharpe']:.2f} out-of-sample: "
                            "if the parameters were picked after looking at results, this is the overfitting pattern.")
    warnings.append("These parameters were fixed by hand. If they were chosen after trying several values on the "
                    "same data, the out-of-sample segment is no longer out-of-sample: use the walkforward command.")
    finish(a, {"command": "backtest", "settings": settings(a, key, {
        "params": used, "stop_atr": stop, "oos_fraction": a.oos_fraction, "oos_start": str(cut_time)}),
        "skipped": skipped, "per_symbol": rows, "portfolio": portfolio, "warnings": warnings})


def cmd_walkforward(a):
    key, fn, _ = resolve_strategy(a)
    if a.grid:
        strategy.PARAM_GRIDS[key] = parse_grid(a.grid)
    combos = strategy.param_combinations(key)
    stops = [parse_stop(s) for s in a.stops]
    cfg = engine_config(a, None)
    frames, skipped = load_frames(a)
    notes = run_strategy_checks(a, fn, [(str(c) or "defaults", c) for c in combos], frames)
    if combos == [{}]:
        notes.append("The strategy has no parameter grid, so nothing is optimized: each fold just runs the "
                     "default parameters. Pass --grid to search.")

    rows, folds, results = [], [], {}
    for symbol, df in frames.items():
        if len(df) < MIN_WFO_CANDLES:
            skipped[symbol] = f"only {len(df)} candles (walk-forward needs {MIN_WFO_CANDLES})"
            print(f"walk-forward: {symbol} skipped ({skipped[symbol]})")
            continue
        try:
            res = walk_forward_optimize(df, key, a.timeframe, cfg, n_folds=a.folds,
                                        min_train_fraction=a.min_train_fraction, anchored=a.anchored,
                                        min_trades=a.min_train_trades, stops=stops, cost_multiplier=a.cost_stress)
        except ValueError as e:
            skipped[symbol] = str(e)
            continue
        results[symbol] = res
        o = res["oos"]
        chosen = [f["params"] for f in res["folds"]]
        changes = sum(x != y for x, y in zip(chosen, chosen[1:]))
        train = pd.Series([f["train_sharpe"] for f in res["folds"]], dtype=float)
        rows.append({
            "Symbol": symbol, "Return%": o["return_pct"], "BuyHold%": o["buy_hold_pct"],
            "StressReturn%": res["high_cost_return_pct"], "DefaultParams%": res["default_return_pct"],
            "Trades": o["trades"], "WinRate%": o["win_rate_pct"], "ProfitFactor": o["profit_factor"],
            "MaxDD%": o["max_drawdown_pct"], "Sharpe": o["sharpe"], "TrainSharpe": train.median(),
            "TrainPositive%": res["train_positive_share"] * 100, "ParamChanges": f"{changes}/{len(chosen) - 1}",
        })
        for k, f in enumerate(res["folds"], 1):
            folds.append({"Symbol": symbol, "Fold": k, "TestStart": f["test_start"], "TestEnd": f["test_end"],
                          "ChosenParams": f["params"] if f["params"] is not None else "flat (too few train trades)",
                          "TrainSharpe": f["train_sharpe"], "TestTrades": f["test_trades"],
                          "TestReturn%": f["test_return_pct"]})
    if not results:
        raise UsageError("no symbol had enough history for a walk-forward test; ask for more days")

    closes = {s: frames[s].set_index("timestamp")["close"].loc[r["oos_equity"].index[0]:] for s, r in results.items()}
    all_trades = [r["oos_trades"] for r in results.values() if len(r["oos_trades"])]
    trades = pd.concat(all_trades, ignore_index=True) if all_trades else pd.DataFrame(columns=["pnl", "return_pct"])
    port, daily = portfolio_row("OOS", {s: r["oos_equity"] for s, r in results.items()},
                                {s: r["high_cost_equity"] for s, r in results.items()}, closes, trades)
    psr = probabilistic_sharpe(daily, 0.0)
    port["PSR"] = psr
    if a.trials > 1 and len(daily) > 2:
        # Luck benchmark: the best of `trials` skill-less candidates, each Sharpe estimate
        # having a standard error of about 1/sqrt(observations).
        port["DSR"] = probabilistic_sharpe(daily, expected_max_sharpe(a.trials, 1 / math.sqrt(len(daily))))

    table = pd.DataFrame(rows)
    n_trials = len(combos) * len(stops)
    print(f"\nWalk-forward: {key} | {len(combos)} parameter sets x {len(stops)} stop settings = {n_trials} per fold | "
          f"{a.folds} folds, {'anchored' if a.anchored else 'rolling'} training window, "
          f"first {a.min_train_fraction:.0%} train-only")
    print("Parameters are chosen by Sharpe on each training window and applied to the next test window. "
          "Only the test windows are reported as out-of-sample.")
    show("Out-of-sample per symbol (TrainSharpe: median in-sample Sharpe of the chosen parameters)", rows)
    show("Folds", folds)
    show("Equal-weight out-of-sample portfolio (Sharpe: daily returns, annualized)", [port],
         PORTFOLIO_COLUMNS + ["PSR", "DSR"])

    warnings = list(notes) + common_warnings(port, a.cost_stress)
    med_train, med_oos = table["TrainSharpe"].median(), table["Sharpe"].median()
    if med_train > 0 and not med_oos > med_train / 2:
        warnings.append(f"Median Sharpe drops from {med_train:.2f} on the training windows to {med_oos:.2f} "
                        "out-of-sample: the optimizer is fitting noise.")
    if table["TrainPositive%"].median() < 50:
        warnings.append(f"Only {table['TrainPositive%'].median():.0f}% of the parameter sets had a positive Sharpe "
                        "in training (median across symbols): there is little edge to select from.")
    total_changes = sum(int(r["ParamChanges"].split("/")[0]) for r in rows)
    total_steps = sum(int(r["ParamChanges"].split("/")[1]) for r in rows)
    if total_steps and total_changes / total_steps > 0.5:
        warnings.append(f"The chosen parameters changed in {total_changes} of {total_steps} fold-to-fold steps: "
                        "the optimum is unstable.")
    if (table["Return%"] <= table["DefaultParams%"]).mean() > 0.5:
        warnings.append("In most symbols the optimized parameters did not beat the default parameters "
                        "out-of-sample: the optimization added nothing.")
    if not psr >= 0.95:
        warnings.append(f"PSR is {psr:.2f}: the probability that the true Sharpe is above zero is below 95%, "
                        "so the result is not statistically distinguishable from luck.")
    if a.trials > 1 and not port.get("DSR", 0) >= 0.95:
        warnings.append(f"Deflated Sharpe is {port.get('DSR', float('nan')):.2f} after {a.trials} strategy ideas "
                        "tried: below 0.95, the best of that many random strategies could look this good.")
    if a.trials <= 1:
        warnings.append("If this is not the first strategy idea tested on this data, rerun with --trials N "
                        "(the number of ideas tried so far) to get the deflated Sharpe.")
    finish(a, {"command": "walkforward", "settings": settings(a, key, {
        "grid": {"+".join(k) if isinstance(k, tuple) else k: v for k, v in strategy.PARAM_GRIDS.get(key, {}).items()},
        "stops": stops, "folds": a.folds, "min_train_fraction": a.min_train_fraction, "anchored": a.anchored,
        "min_train_trades": a.min_train_trades, "trials": a.trials}),
        "skipped": skipped, "per_symbol": rows, "folds": folds, "portfolio": [port], "warnings": warnings})


def cmd_check(a):
    if not a.strategy_file:
        raise UsageError("check needs --strategy-file PATH")
    _, fn, params = resolve_strategy(a)
    sets = [("given parameters", params)]
    sets += [(str(c), c) for c in strategy.param_combinations(CUSTOM_KEY) if c]
    frames = {}
    if a.symbols:
        frames, _ = load_frames(a)
    run_strategy_checks(a, fn, sets, frames)
    print("\nPassing means no lookahead was found, not that the strategy is profitable.")


def cmd_strategies(a):
    out = []
    for key, fn in strategy.STRATEGY_DISPATCH.items():
        grid = {"+".join(k) if isinstance(k, tuple) else k: v for k, v in strategy.PARAM_GRIDS.get(key, {}).items()}
        out.append({"key": key, "description": strategy.STRATEGY_INFO.get(key, ""), "defaults": defaults(fn),
                    "parameters": strategy.PARAM_INFO.get(key, {}), "walkforward_grid": grid})
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)
        print(f"JSON written to {a.json}")
        return
    for s in out:
        print(f"\n{s['key']}: {s['description']}")
        print(f"  defaults: {s['defaults']}")
        for name, text in s["parameters"].items():
            print(f"    {name}: {text}")
        print(f"  walk-forward grid: {s['walkforward_grid']}")
    print(f"\nTimeframes: {', '.join(data.TIMEFRAMES)}")


# ---------- entry point ----------
def build_parser():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    def common(p, symbols_required=True):
        p.add_argument("--symbols", nargs="+", required=symbols_required, default=[] if not symbols_required else None,
                       help="Binance spot pairs, e.g. BTC/USDT ETH/USDT")
        p.add_argument("--timeframe", default="1d", help=f"candle size: {', '.join(data.TIMEFRAMES)} (default 1d)")
        p.add_argument("--days", type=float, default=730, help="days of history before --end (default 730)")
        p.add_argument("--end", help="last day of the window, YYYY-MM-DD UTC (default: now); fix it to reproduce a run")
        p.add_argument("--strategy", help="built-in strategy key, v1..v10")
        p.add_argument("--strategy-file", help="Python file defining generate_signals(df, **params)")
        p.add_argument("--fee", type=float, default=0.001, help="fee per side as a fraction (default 0.001 = 0.1%%)")
        p.add_argument("--slippage-bps", type=float, default=5.0, help="adverse slippage per fill in bps (default 5)")
        p.add_argument("--cost-stress", type=float, default=1.5, help="cost multiplier of the stress run (default 1.5)")
        p.add_argument("--trail-atr", type=float, help="trailing exit: highest close since entry minus ATR x this")
        p.add_argument("--equity", type=float, default=1000.0, help="starting equity per symbol (default 1000)")
        p.add_argument("--data-dir", help="candle cache folder (default ~/.cache/crypto-strategy-backtester/ohlcv)")
        p.add_argument("--offline", action="store_true", help="use cached candles only, no network")
        p.add_argument("--json", help="also write the full result to this JSON file")

    p = sub.add_parser("backtest", help="fixed parameters, in-sample vs out-of-sample")
    common(p)
    p.add_argument("--params", nargs="*", help="strategy parameters, e.g. fast=1 slow=50")
    p.add_argument("--stop", default="none", help="stop-loss distance as an ATR multiple, or none (default none)")
    p.add_argument("--oos-fraction", type=float, default=0.3, help="last share of the period kept out-of-sample")
    p.set_defaults(run=cmd_backtest)

    p = sub.add_parser("walkforward", help="walk-forward optimization: choose on train, report on test")
    common(p)
    p.add_argument("--grid", nargs="*", help="parameter grid, e.g. rsi_buy=30,35 fast+slow=9/21,12/26 "
                                             "(default: the strategy's built-in grid)")
    p.add_argument("--stops", nargs="+", default=["none"], help="stop settings to search: none and/or ATR multiples")
    p.add_argument("--folds", type=int, default=5, help="number of test windows (default 5)")
    p.add_argument("--min-train-fraction", type=float, default=0.4, help="first share used for training only")
    p.add_argument("--anchored", action="store_true", help="training window always starts at the first candle")
    p.add_argument("--min-train-trades", type=int, default=10, help="min training trades to trust a parameter set")
    p.add_argument("--trials", type=int, default=1, help="strategy ideas tried so far on this data (deflated Sharpe)")
    p.set_defaults(run=cmd_walkforward)

    p = sub.add_parser("check", help="lookahead checks for a custom strategy file")
    common(p, symbols_required=False)
    p.add_argument("--params", nargs="*", help="strategy parameters to check")
    p.set_defaults(run=cmd_check)

    p = sub.add_parser("strategies", help="list the built-in strategies, parameters and grids")
    p.add_argument("--json", help="write the list to this JSON file")
    p.set_defaults(run=cmd_strategies)
    return ap


def main(argv=None):
    a = build_parser().parse_args(argv)
    try:
        if getattr(a, "timeframe", None):
            data.timeframe_ms(a.timeframe)
        if a.command == "backtest" and not 0 < a.oos_fraction < 1:
            raise UsageError("--oos-fraction must be between 0 and 1")
        a.run(a)
    except (UsageError, ValueError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
