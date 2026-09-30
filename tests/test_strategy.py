import pytest

import strategy
from conftest import make_ohlcv
from indicators import add_indicators

KEYS = list(strategy.STRATEGY_DISPATCH)
# Every strategy with its defaults plus the first and last combination of its grid.
CASES = [(k, p) for k in KEYS
         for p in [{}] + [strategy.param_combinations(k)[i] for i in (0, -1)]]


def _signals(key, df, params=None):
    return strategy.get_strategy(key)(add_indicators(df.copy()), **(params or {}))


@pytest.mark.parametrize("key", KEYS)
def test_signals_shape_and_values(key):
    df = make_ohlcv(400, seed=1)
    sig = _signals(key, df)
    assert len(sig) == len(df)
    assert set(sig.unique()) <= {"BUY", "SELL", "-"}


@pytest.mark.parametrize("key,params", CASES)
@pytest.mark.parametrize("cut", [60, 150, 299])
def test_no_lookahead(key, params, cut):
    """Changing candles after `cut` must not change any signal up to `cut`."""
    df = make_ohlcv(400, seed=2)
    base = _signals(key, df, params)

    # Crash and spike scenarios, so any peek at a later candle sees a different direction.
    for factor in (0.5, 2.0):
        future = df.copy()
        future.loc[cut + 1:, ["open", "high", "low", "close"]] *= factor
        changed = _signals(key, future, params)
        assert base.iloc[:cut + 1].equals(changed.iloc[:cut + 1]), factor


@pytest.mark.parametrize("key", KEYS)
def test_prefix_matches_full_series(key):
    """The signal for a candle is the same whether or not later candles exist."""
    df = make_ohlcv(300, seed=4)
    full = _signals(key, df)
    for i in [30, 100, 200, 299]:
        assert _signals(key, df.iloc[:i + 1]).iloc[-1] == full.iloc[i]


def test_unknown_strategy():
    with pytest.raises(ValueError):
        strategy.get_strategy("v99")


def test_param_combinations():
    assert len(strategy.param_combinations("v1")) == 16
    combos = strategy.param_combinations("v5")
    assert len(combos) == 24
    assert {(c["fast"], c["slow"]) for c in combos} == {(9, 21), (12, 26), (20, 50)}
    assert strategy.param_combinations("unknown") == [{}]


def test_trend_strategies_follow_their_rules():
    import pandas as pd
    close = pd.Series([10.0] * 5 + [11, 12, 13, 14, 15] + [14, 12, 10, 8, 6])
    df = pd.DataFrame({"open": close, "high": close + 0.1, "low": close - 0.1, "close": close})
    df["RSI"] = 50.0

    s = strategy.get_strategy("v8")(df, lookback=3)
    assert s.iloc[7] == "BUY"          # 13 vs 10 three bars earlier
    assert s.iloc[13] == "SELL"        # 8 vs 12

    s = strategy.get_strategy("v9")(df, fast=1, slow=4)
    assert s.iloc[8] == "BUY" and s.iloc[12] == "SELL"

    s = strategy.get_strategy("v10")(df, entry=3, exit_len=2)
    assert s.iloc[5] == "BUY"          # 11 breaks the prior 3-bar high of 10.1
    assert s.iloc[11] == "SELL"        # 12 breaks below the prior 2-bar low of 13.9
