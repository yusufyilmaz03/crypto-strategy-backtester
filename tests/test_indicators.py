import numpy as np
import pandas as pd

from conftest import make_ohlcv
from indicators import add_indicators, rma, rsi


def test_rma_matches_wilder_definition():
    s = pd.Series([np.nan, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0])
    out = rma(s, 3)
    assert out.iloc[:3].isna().all()
    assert out.iloc[3] == 2.0                    # SMA seed of 1, 2, 3
    assert out.iloc[4] == 2.0 + (4.0 - 2.0) / 3  # recursive step


def test_rsi_reference_loop():
    close = make_ohlcv(300, seed=3)["close"]
    got = rsi(close, 14)

    # Straightforward loop implementation of Wilder's RSI.
    d = close.diff().to_numpy()
    g, l = np.clip(d, 0, None), np.clip(-d, 0, None)
    ag, al = g[1:15].mean(), l[1:15].mean()
    expected = {14: 100 - 100 / (1 + ag / al)}
    for i in range(15, len(close)):
        ag = (ag * 13 + g[i]) / 14
        al = (al * 13 + l[i]) / 14
        expected[i] = 100 - 100 / (1 + ag / al)

    assert got.iloc[:14].isna().all()
    for i, v in expected.items():
        assert abs(got.iloc[i] - v) < 1e-9


def test_rsi_bounds_and_monotonic_series():
    rising = pd.Series(np.arange(1.0, 50.0))
    assert (rsi(rising).dropna() == 100).all()
    r = rsi(make_ohlcv(500)["close"]).dropna()
    assert r.between(0, 100).all()


def test_add_indicators_columns():
    df = add_indicators(make_ohlcv(100))
    for col in ["RSI", "EMA_9", "EMA_21", "ATR"]:
        assert col in df.columns
    assert not any(c in df.columns for c in ["H-L", "H-C", "L-C", "TR"])
