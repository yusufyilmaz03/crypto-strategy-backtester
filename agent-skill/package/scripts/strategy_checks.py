# scripts/strategy_checks.py
"""Checks a custom strategy must pass before it is backtested.

A strategy is a function `generate_signals(df, **params)` that returns one of
"BUY", "SELL" or "-" for every candle. The signal of candle i may only depend
on candles 0..i. These checks look for the usual ways that rule gets broken:

  shape        one signal per candle, only the three allowed values
  future       changing candles after a cut must not change signals up to the cut
  prefix       the signal of a candle is the same whether or not later candles exist
  static scan  source patterns that often mean lookahead (warnings only)

The first three run on synthetic candles and, when given, on the real candles of
the backtest, because a rule that never fires on synthetic data proves nothing.
"""
import ast
import re

import numpy as np
import pandas as pd

from kripto_bot.core.indicators import add_indicators

ALLOWED = {"BUY", "SELL", "-"}
PRICE_COLS = ["open", "high", "low", "close"]
MAX_REAL_ROWS = 3000

STATIC_PATTERNS = [
    (r"\.shift\(\s*-", "shift() with a negative period reads later candles"),
    (r"center\s*=\s*True", "a centered rolling window reads later candles"),
    (r"\bbfill\b|backfill|method\s*=\s*['\"]bfill", "backfilling copies later values into earlier candles"),
    (r"\.iloc\[\s*-1\s*\]|\.max\(\)|\.min\(\)|\.mean\(\)|\.std\(\)|\.quantile\(",
     "a whole-series statistic (or the last row) uses every candle, including later ones; "
     "use rolling()/expanding() instead"),
    (r"\b(import|from)\s+(os|sys|subprocess|socket|requests|urllib|http|shutil)\b",
     "the strategy imports a module it should not need (signals only need pandas/numpy)"),
]


def synthetic(n=700, seed=0):
    """Random-walk daily candles with alternating up and down regimes, so trend,
    breakout and mean-reversion rules all get a chance to fire."""
    rng = np.random.default_rng(seed)
    drift = np.where((np.arange(n) // 90) % 2 == 0, 0.004, -0.004)
    close = 100 * np.exp(np.cumsum(rng.normal(drift, 0.03, n)))
    open_ = np.r_[close[0], close[:-1]] * (1 + rng.normal(0, 0.002, n))
    high = np.maximum(open_, close) * (1 + np.abs(rng.normal(0, 0.01, n)))
    low = np.minimum(open_, close) * (1 - np.abs(rng.normal(0, 0.01, n)))
    return pd.DataFrame({
        "timestamp": pd.date_range("2022-01-01", periods=n, freq="1D"),
        "open": open_, "high": high, "low": low, "close": close,
        "volume": rng.uniform(100, 1000, n),
    })


def _raw(df):
    """OHLCV columns only, positional index: what the data loader returns."""
    return df[["timestamp", *PRICE_COLS, "volume"]].reset_index(drop=True)


def signals(fn, df, params):
    """Signals as a plain list, computed the way the backtest computes them."""
    out = fn(add_indicators(_raw(df).copy()), **params)
    return out


def check_shape(fn, df, params):
    try:
        sig = signals(fn, df, params)
    except Exception as e:     # the strategy's own error is the finding
        return [f"the strategy raised {type(e).__name__}: {e}"], None
    if not isinstance(sig, pd.Series):
        return [f"generate_signals must return a pandas Series, got {type(sig).__name__}"], None
    problems = []
    if len(sig) != len(df):
        problems.append(f"expected {len(df)} signals (one per candle), got {len(sig)}")
    extra = set(map(str, sig.unique())) - ALLOWED
    if extra:
        problems.append(f"signals must be 'BUY', 'SELL' or '-'; found {sorted(extra)[:5]}")
    return problems, sig


def check_future(fn, df, params, cuts):
    """Crash, spike and volume changes after `cut` must leave signals up to `cut` alone."""
    df = _raw(df)
    base = list(map(str, signals(fn, df, params)))
    for cut in cuts:
        for factor in (0.5, 2.0):
            future = df.copy()
            future.loc[cut + 1:, PRICE_COLS] *= factor
            future.loc[cut + 1:, "volume"] *= 3.0
            changed = list(map(str, signals(fn, future, params)))
            if base[:cut + 1] != changed[:cut + 1]:
                first = next(i for i in range(cut + 1) if base[i] != changed[i])
                return [f"lookahead: changing candles after row {cut} changed the signal at row {first} "
                        f"('{base[first]}' -> '{changed[first]}')"]
    return []


def check_prefix(fn, df, params, points):
    """The last signal of a truncated history must equal the full-history signal there."""
    df = _raw(df)
    full = list(map(str, signals(fn, df, params)))
    for i in points:
        last = str(signals(fn, df.iloc[:i + 1], params).iloc[-1])
        if last != full[i]:
            return [f"lookahead: with history ending at row {i} the signal is '{last}', "
                    f"with the full history it is '{full[i]}'"]
    return []


def _docstring_lines(source):
    lines = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.ClassDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                lines.update(range(first.lineno, first.end_lineno + 1))
    return lines


def static_scan(path):
    """Warnings for source patterns that often mean lookahead. Comments and docstrings are ignored."""
    warnings = []
    with open(path, encoding="utf-8") as f:
        source = f.read()
    skip = _docstring_lines(source)
    for no, line in enumerate(source.splitlines(), 1):
        if no in skip:
            continue
        code = line.split("#", 1)[0]
        windowed = re.search(r"rolling\(|ewm\(|expanding\(", code)
        for pattern, why in STATIC_PATTERNS:
            if "whole-series" in why and windowed:
                continue
            if re.search(pattern, code):
                warnings.append(f"line {no}: {why}: {code.strip()[:80]}")
    return warnings


def _points(n, k=5):
    lo = min(60, n // 3)
    return sorted({int(x) for x in np.linspace(lo, n - 1, k)})


def run_checks(fn, param_sets, real_frames=None, source_path=None):
    """Run every check. Returns (failures, warnings, notes); the strategy may only be
    backtested when `failures` is empty."""
    failures, warnings, notes = [], [], []
    if source_path:
        warnings += static_scan(source_path)

    frames = [("synthetic candles", synthetic(seed=1)), ("synthetic candles (2)", synthetic(seed=7))]
    for name, df in (real_frames or {}).items():
        frames.append((f"{name} candles", df.tail(MAX_REAL_ROWS)))

    fired, passed = False, 0
    for label, params in param_sets:
        for name, df in frames:
            if len(df) < 120:
                notes.append(f"{name}: only {len(df)} candles, skipped")
                continue
            where = f"[{label}] {name}"
            problems, sig = check_shape(fn, df, params)
            if problems:
                failures += [f"{where}: {p}" for p in problems]
                break
            counts = sig.astype(str).value_counts()
            fired = fired or counts.get("BUY", 0) > 0
            n = len(df)
            try:
                found = check_future(fn, df, params, cuts=[n // 4, n // 2, n - 2]) \
                    or check_prefix(fn, df, params, _points(n))
            except Exception as e:
                found = [f"the strategy raised {type(e).__name__} on a changed or shortened history: {e}"]
            if found:
                failures += [f"{where}: {p}" for p in found]
                break
            passed += 1
            if label == param_sets[0][0]:      # detail for the first parameter set, a count for the rest
                notes.append(f"{where}: ok ({counts.get('BUY', 0)} BUY, {counts.get('SELL', 0)} SELL)")
    if not failures and len(param_sets) > 1:
        notes.append(f"{len(param_sets)} parameter sets, {passed} runs in total: ok")
    if not failures and not fired:
        warnings.append("the strategy produced no BUY signal on any checked data, "
                        "so the lookahead checks had nothing to test; check the entry rule")
    return failures, warnings, notes
