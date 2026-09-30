import pytest

import run_realtime
import strategy
from conftest import make_ohlcv
from engine import EngineConfig, run_backtest
from indicators import add_indicators
from store import PaperStore

FULL = make_ohlcv(900, seed=11)


class FakeFeed:
    """Serves the candles that have 'closed' so far (the last `limit` of them)."""

    def __init__(self, n):
        self.n = n

    def __call__(self, symbol, timeframe, limit=200, **kw):
        return FULL.iloc[:self.n].tail(limit).reset_index(drop=True)


@pytest.fixture
def feed(monkeypatch):
    f = FakeFeed(400)
    monkeypatch.setattr(run_realtime, "fetch_recent", f)
    # "now" follows the feed so the catch-up window is computed from the fake clock
    monkeypatch.setattr(run_realtime, "now_ms",
                        lambda: int(FULL.timestamp.iloc[f.n - 1].value // 10**6) + 5 * 60_000)
    return f


def backtest_from(start, cfg):
    df = add_indicators(FULL.copy())
    sig = strategy.get_strategy("v5")(df)
    trades, _ = run_backtest(df.iloc[start:], sig.iloc[start:], cfg)
    return trades[trades.reason != "END"]


def test_paper_loop_matches_backtest_and_is_stored(tmp_path, feed):
    store = PaperStore(str(tmp_path / "paper.db"))
    cfg = EngineConfig()
    runner = run_realtime.SymbolRunner("X/USDT", "5m", "v5", cfg, store)

    runner.update()                      # first call only records the starting point
    assert runner.engine.trades == []
    start = feed.n
    while feed.n < len(FULL):
        feed.n = min(feed.n + 3, len(FULL))
        runner.update()

    expected = backtest_from(start, cfg)
    assert len(expected) > 0
    assert [t.pnl for t in runner.engine.trades] == pytest.approx(expected.pnl.tolist())

    trades = store.read("trades")
    closed = trades[trades.exit_time.notna()]
    assert closed.pnl.tolist() == pytest.approx(expected.pnl.tolist())
    assert trades.exit_time.isna().sum() == (1 if runner.engine.position else 0)
    assert len(store.read("equity")) == len(FULL) - start
    signals = store.read("signals")
    assert len(signals) > 0 and set(signals.signal) <= {"BUY", "SELL"}


def test_restart_resumes_and_catches_up(tmp_path, feed):
    db = str(tmp_path / "paper.db")
    cfg = EngineConfig()
    runner = run_realtime.SymbolRunner("X/USDT", "5m", "v5", cfg, PaperStore(db))
    runner.update()
    start = feed.n
    while feed.n < 650:
        feed.n += 1
        runner.update()
    before = list(runner.engine.trades)

    feed.n = 720                         # bot was down for 70 candles
    restarted = run_realtime.SymbolRunner("X/USDT", "5m", "v5", cfg, PaperStore(db))
    assert restarted.last_time == FULL.timestamp.iloc[649]
    while feed.n < len(FULL):
        restarted.update()
        feed.n += 1
    restarted.update()

    expected = backtest_from(start, cfg)
    got = before + restarted.engine.trades
    assert [t.pnl for t in got] == pytest.approx(expected.pnl.tolist())


def test_changing_strategy_starts_fresh(tmp_path, feed):
    db = str(tmp_path / "paper.db")
    r1 = run_realtime.SymbolRunner("X/USDT", "5m", "v5", EngineConfig(), PaperStore(db))
    r1.update()
    r2 = run_realtime.SymbolRunner("X/USDT", "5m", "v1", EngineConfig(), PaperStore(db))
    assert r2.last_time is None and r2.engine.equity == EngineConfig().initial_equity
