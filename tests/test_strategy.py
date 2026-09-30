import pytest

import strategy
from conftest import make_ohlcv
from indicators import add_indicators

KEYS = list(strategy.STRATEGY_DISPATCH)


def _signals(key, df):
    return strategy.get_strategy(key)(add_indicators(df.copy()))


@pytest.mark.parametrize("key", KEYS)
def test_signals_shape_and_values(key):
    df = make_ohlcv(400, seed=1)
    sig = _signals(key, df)
    assert len(sig) == len(df)
    assert set(sig.unique()) <= {"BUY", "SELL", "-"}


@pytest.mark.parametrize("key", KEYS)
@pytest.mark.parametrize("cut", [60, 150, 299])
def test_no_lookahead(key, cut):
    """Changing candles after `cut` must not change any signal up to `cut`."""
    df = make_ohlcv(400, seed=2)
    base = _signals(key, df)

    # Crash and spike scenarios, so any peek at a later candle sees a different direction.
    for factor in (0.5, 2.0):
        future = df.copy()
        future.loc[cut + 1:, ["open", "high", "low", "close"]] *= factor
        changed = _signals(key, future)
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
