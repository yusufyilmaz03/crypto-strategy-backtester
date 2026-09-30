import pytest

import strategy
from conftest import make_ohlcv
from engine import EngineConfig, run_backtest
from evaluation import evaluate_segment, is_oos_split, walk_forward_splits
from indicators import add_indicators


def test_is_oos_split():
    is_, oos = is_oos_split(1000, 0.3)
    assert is_ == range(0, 700) and oos == range(700, 1000)
    with pytest.raises(ValueError):
        is_oos_split(100, 1.0)


@pytest.mark.parametrize("anchored", [True, False])
def test_walk_forward_splits(anchored):
    splits = walk_forward_splits(1000, 4, min_train_fraction=0.5, anchored=anchored)
    assert len(splits) == 4
    tests = [t for _, t in splits]
    assert tests[0].start == 500 and tests[-1].stop == 1000
    for a, b in zip(tests, tests[1:]):
        assert a.stop == b.start                      # contiguous, no overlap
    for train, test in splits:
        assert train.stop == test.start               # training strictly precedes testing
        assert len(train) == (test.start if anchored else 500)


def test_walk_forward_rejects_tiny_data():
    with pytest.raises(ValueError):
        walk_forward_splits(5, 10)


def test_evaluate_segment_trades_only_inside_segment():
    df = add_indicators(make_ohlcv(800, seed=5))
    sig = strategy.get_strategy("v5")(df)
    cfg = EngineConfig()
    _, oos = is_oos_split(len(df), 0.3)
    m, trades, equity = evaluate_segment(df, sig, oos, cfg, "5m")

    assert len(equity) == len(oos)
    assert (trades.entry_time >= df.timestamp.iloc[oos.start]).all()
    assert m["start"] == df.timestamp.iloc[oos.start] and m["end"] == df.timestamp.iloc[-1]
    # Same as running the engine directly on the slice.
    direct, _ = run_backtest(df.iloc[oos.start:], sig.iloc[oos.start:], cfg)
    assert trades.pnl.tolist() == pytest.approx(direct.pnl.tolist())
