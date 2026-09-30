# evaluation.py
"""In-sample / out-of-sample splits, walk-forward windows and segment evaluation.

Indicators and signals are computed once on the full history (they only look
backwards), and trades are simulated only inside the evaluated segment, starting
flat. This avoids an indicator warm-up gap at the start of each segment without
leaking future data.
"""
from engine import run_backtest
from metrics import compute_metrics


def is_oos_split(n, oos_fraction=0.3):
    """(in-sample range, out-of-sample range) over n candles, split chronologically."""
    if not 0 < oos_fraction < 1:
        raise ValueError("oos_fraction must be between 0 and 1")
    cut = int(round(n * (1 - oos_fraction)))
    return range(0, cut), range(cut, n)


def walk_forward_splits(n, n_folds, min_train_fraction=0.5, anchored=True):
    """Chronological (train, test) ranges.

    The first `min_train_fraction` of the data is only used for training; the
    remainder is cut into `n_folds` consecutive test windows. With anchored=True
    the training window always starts at 0, otherwise it rolls forward with a
    fixed length.
    """
    first_test = int(round(n * min_train_fraction))
    test_len = (n - first_test) // n_folds
    if n_folds < 1 or first_test < 1 or test_len < 1:
        raise ValueError("not enough data for the requested walk-forward split")
    splits = []
    for k in range(n_folds):
        test_start = first_test + k * test_len
        test_end = n if k == n_folds - 1 else test_start + test_len
        train_start = 0 if anchored else test_start - first_test
        splits.append((range(train_start, test_start), range(test_start, test_end)))
    return splits


def evaluate_segment(df, signals, rows, config, timeframe, symbol=""):
    """Backtest one segment (a range of row positions) and return its metrics."""
    part = df.iloc[rows.start:rows.stop]
    trades, equity = run_backtest(part, signals.iloc[rows.start:rows.stop], config, symbol)
    metrics = compute_metrics(trades, equity, timeframe, config.initial_equity, part["close"])
    metrics["start"] = part["timestamp"].iloc[0] if len(part) else None
    metrics["end"] = part["timestamp"].iloc[-1] if len(part) else None
    return metrics, trades, equity
