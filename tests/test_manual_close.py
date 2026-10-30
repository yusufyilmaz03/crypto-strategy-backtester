import sqlite3

import pandas as pd
import pytest

from kripto_bot import instances
from kripto_bot.app import dashboard, reporting
from kripto_bot.core.engine import Engine, EngineConfig, Position, Trade
from kripto_bot.trading import position_watch
from kripto_bot.trading.position_watch import ManualClose
from kripto_bot.trading.store import PaperStore

LAST = pd.Timestamp("2026-10-29")
PERIOD = LAST + pd.Timedelta(days=1)
NOW = PERIOD + pd.Timedelta(hours=10, minutes=30, seconds=12)
H = {"X-Requested-With": "kripto-bot"}


class Runner:
    def __init__(self, engine, symbol="X/USDT", last_time=LAST, key="run", timeframe="1d"):
        self.symbol, self.engine, self.last_time, self.key, self.timeframe = symbol, engine, last_time, key, timeframe

    def exit_watchable(self):
        from kripto_bot.trading.run_realtime import SymbolRunner
        return SymbolRunner.exit_watchable(self)


def _engine(position=True, pending=None, store=None):
    """Engine with the trade callbacks SymbolRunner gives it (writing to `store`)."""
    e = Engine(EngineConfig(atr_multiplier=None, slippage_bps=0, fee_rate=0),
               on_close=store.trade_closed if store else None)
    if position:
        e.position = Position("t1", "LONG", 2.0, pd.Timestamp("2026-10-20"), 100.0, 0.0, 200.0, None, high=120.0)
    e.pending = pending
    return e


def _minute(open_):
    return lambda s, tf, since: pd.DataFrame({"timestamp": [PERIOD], "open": [open_], "high": [open_],
                                              "low": [open_], "close": [open_], "volume": [1.0]})


def _watch(store, runner, price=110.0, now=NOW, fetch=None):
    return ManualClose([runner], store, "run", "1d", price=lambda s: price, fetch=fetch or _minute(100.0),
                       clock=lambda: now)


def test_close_request_sells_at_the_market_and_waits_to_reenter(tmp_path):
    store = PaperStore(str(tmp_path / "p.db"))
    e = _engine(store=store)
    store.trade_opened("run", "1d", "X/USDT", e.position)
    assert store.request_close("run", "1d", "X/USDT")
    assert not store.request_close("run", "1d", "X/USDT")       # one waiting request per symbol
    _watch(store, Runner(e)).check()
    t = store.read("trades").iloc[0]
    assert (t.exit_price, t.reason, t.pnl) == (110.0, "MANUAL", 20.0)
    assert pd.Timestamp(t.exit_time) == NOW.floor("s")
    assert e.position is None and e.reentry_above == 120.0       # next BUY waits for a reset
    req = store.read("close_requests").iloc[0]
    assert req.done_at is not None and req.result == "sold at 110"
    assert store.load_state("X/USDT", "run", "1d")[0]["position"] is None
    assert store.read("signals").action.tolist() == ["manual close from the panel"]


def test_pending_entry_is_filled_at_the_open_then_sold(tmp_path):
    store = PaperStore(str(tmp_path / "p.db"))
    e = _engine(position=False, pending=("ENTER", "LONG", None, None))
    store.request_close("run", "1d", "X/USDT")
    _watch(store, Runner(e)).check()
    t = e.trades[-1]
    assert (t.entry_price, t.exit_price, t.entry_time) == (100.0, 110.0, PERIOD)


def test_nothing_to_close(tmp_path):
    store = PaperStore(str(tmp_path / "p.db"))
    exiting = _engine(pending=("EXIT", "SIGNAL"))
    for symbol in ("X/USDT", "Y/USDT", "Z/USDT"):
        store.request_close("run", "1d", symbol)
    w = ManualClose([Runner(exiting), Runner(_engine(position=False), "Y/USDT")], store, "run", "1d",
                    price=lambda s: 1 / 0, clock=lambda: NOW)
    w.check()
    results = dict(store.read("close_requests")[["symbol", "result"]].values)
    assert results == {"X/USDT": "already sold at the 1d open", "Y/USDT": "no position",
                       "Z/USDT": "not traded by this instance"}
    assert exiting.position is not None                          # the engine sells it at the open as planned


def test_waits_while_the_closed_candle_isnt_processed(tmp_path):
    store = PaperStore(str(tmp_path / "p.db"))
    e = _engine()
    store.request_close("run", "1d", "X/USDT")
    _watch(store, Runner(e), now=PERIOD + pd.Timedelta(days=1, seconds=2)).check()
    assert e.position is not None and store.close_requests("run", "1d")


def test_price_errors_are_retried(tmp_path, capsys):
    store = PaperStore(str(tmp_path / "p.db"))
    e = _engine()
    store.request_close("run", "1d", "X/USDT")

    def down(symbol):
        raise ConnectionError("down")

    ManualClose([Runner(e)], store, "run", "1d", price=down, clock=lambda: NOW).check()
    assert e.position is not None and store.close_requests("run", "1d")
    assert "CLOSE ERROR" in capsys.readouterr().out


def test_manual_close_is_an_instance_setting(tmp_path):
    store = PaperStore(str(tmp_path / "p.db"))
    w = position_watch.for_instance(instances.Instance(name="p", timeframe="5m", manual_close=True), [], store)
    assert [type(x).__name__ for x in w.watches] == ["ManualClose"]
    assert position_watch.for_instance(instances.Instance(name="p", timeframe="5m"), [], store) is None
    path = str(tmp_path / "i.json")
    assert instances.create({"name": "mc", "manual_close": True}, path).manual_close is True
    assert instances.create({"name": "plain"}, path).manual_close is False
    with pytest.raises(instances.InstanceError, match="paper"):
        instances.create({"name": "tn", "mode": "testnet", "manual_close": True}, path)
    with pytest.raises(instances.InstanceError, match="can't be changed"):
        instances.update("plain", path, manual_close=True)


# ---------------------------------------------------------------- panel
@pytest.fixture
def client():
    dashboard.app.config["TESTING"] = True
    return dashboard.app.test_client()


def _open_position(inst, symbol="X/USDT"):
    store = PaperStore()
    store.trade_opened(inst.run_key, inst.timeframe, symbol,
                       Position(f"t-{inst.name}", "LONG", 2.0, pd.Timestamp("2026-10-20"), 100.0, 0.0, 200.0, None))
    store.conn.commit()
    return store


def test_close_endpoint(client):
    instances.create({"name": "pp", "symbols": ["X/USDT"], "enabled": True, "manual_close": True})
    instances.create({"name": "off", "symbols": ["X/USDT"], "manual_close": True})
    instances.create({"name": "no-close", "symbols": ["X/USDT"], "enabled": True})
    _open_position(instances.get("pp"))
    _open_position(instances.get("no-close"))
    refused = client.post("/api/instances/no-close/close", json={"symbol": "X/USDT"}, headers=H)
    assert refused.status_code == 400 and "without manual close" in refused.get_json()["error"]
    assert client.post("/api/instances/pp/close", json={"symbol": "X/USDT"}, headers=H).status_code == 200
    again = client.post("/api/instances/pp/close", json={"symbol": "X/USDT"}, headers=H)
    assert again.status_code == 400 and "already" in again.get_json()["error"]
    assert client.post("/api/instances/pp/close", json={"symbol": "Y/USDT"}, headers=H).status_code == 400
    assert client.post("/api/instances/off/close", json={"symbol": "X/USDT"}, headers=H).status_code == 400
    assert client.post("/api/instances/forward-v9/close", json={"symbol": "X/USDT"}).status_code == 403   # CSRF
    pos = client.get("/api/instances/pp/positions").get_json()
    assert pos[0]["close_requested"] is True and pos[0]["price"] is None   # no network in tests


def test_tables_show_current_prices(client, monkeypatch):
    inst = instances.create({"name": "pp", "symbols": ["X/USDT", "Y/USDT"], "enabled": True})
    store = _open_position(inst)
    store.trade_opened(inst.run_key, inst.timeframe, "Y/USDT",
                       Position("t2", "LONG", 1.0, pd.Timestamp("2026-10-01"), 10.0, 0.0, 10.0, None))
    store.trade_closed(Trade("t2", "Y/USDT", "LONG", pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-05"),
                             10.0, 12.0, 1.0, 0.0, 2.0, 20.0, "SIGNAL"))
    store.conn.commit()
    calls = []
    monkeypatch.setattr(reporting, "_fetch_tickers",
                        lambda symbols: calls.append(symbols) or {"X/USDT": {"last": 111.0}, "Y/USDT": {"last": 13.0}})
    trades = {r["symbol"]: r for r in client.get("/api/instances/pp/trades").get_json()}
    assert trades["X/USDT"]["price"] == 111.0 and trades["Y/USDT"]["price"] is None   # closed: no price
    assert client.get("/api/instances/pp/positions").get_json()[0]["price"] == 111.0
    assert len(calls) == 1                                                # cached for both tables
    summary = client.get("/api/instances/pp").get_json()["summary"]
    assert summary["realized_pnl"] == 2.0


def test_positions_survive_a_database_without_close_requests(tmp_path):
    db = str(tmp_path / "old.db")
    inst = instances.Instance(name="p", run_key="run")
    store = PaperStore(db)
    store.trade_opened("run", "1d", "X/USDT", Position("t1", "LONG", 1.0, pd.Timestamp("2026-10-20"), 1.0, 0.0, 1.0, None))
    store.conn.commit()
    store.conn.execute("DROP TABLE close_requests")
    store.conn.commit()
    pos = reporting.positions(inst, db)
    assert pos.symbol.tolist() == ["X/USDT"] and not pos.close_requested.any()


def test_current_prices_cache_and_failure():
    calls = []

    def fetch(symbols):
        calls.append(symbols)
        return {s: {"last": 1.0} for s in symbols}

    clock = {"t": 1000.0}
    assert reporting.current_prices(["A/USDT"], fetch=fetch, clock=lambda: clock["t"]) == {"A/USDT": 1.0}
    reporting.current_prices(["A/USDT"], fetch=fetch, clock=lambda: clock["t"] + 5)
    assert len(calls) == 1
    reporting.current_prices(["A/USDT", "B/USDT"], fetch=fetch, clock=lambda: clock["t"] + 6)   # new symbol
    clock["t"] += 100
    reporting.current_prices(["A/USDT"], fetch=fetch, clock=lambda: clock["t"])                 # expired
    assert len(calls) == 3
    assert reporting.current_prices(["A/USDT"], fetch=lambda s: 1 / 0, clock=lambda: clock["t"] + 999) == {}
