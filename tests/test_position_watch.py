import pandas as pd
import pytest

from kripto_bot import instances
from kripto_bot.app import notify, reporting
from kripto_bot.core.engine import Engine, EngineConfig, Position, Trade
from kripto_bot.trading import position_watch, run_realtime
from kripto_bot.trading.position_watch import StopWatch
from kripto_bot.trading.store import PaperStore

LAST = pd.Timestamp("2026-10-29")          # last processed 1d candle
PERIOD = LAST + pd.Timedelta(days=1)       # the forming one
DETECTED = pd.Timestamp("2026-10-30 00:05:30", tz="UTC")


def minutes(rows, start=PERIOD):
    """rows: [(open, low)] of consecutive 1m candles from `start`."""
    return pd.DataFrame({"timestamp": pd.date_range(start, periods=len(rows), freq="1min"),
                         "open": [o for o, _ in rows], "high": [o + 1 for o, _ in rows],
                         "low": [lo for _, lo in rows], "close": [o for o, _ in rows], "volume": 1.0})


class FakeFetch:
    def __init__(self, frame):
        self.frame, self.calls = frame, []

    def __call__(self, symbol, timeframe, since):
        self.calls.append((symbol, timeframe, since))
        return self.frame[self.frame.timestamp >= since].reset_index(drop=True)


class Runner:
    def __init__(self, engine, symbol="X/USDT", last_time=LAST):
        self.symbol, self.engine, self.last_time = symbol, engine, last_time


def in_position(stop=95.0, entry_time="2026-10-20"):
    e = Engine(EngineConfig(atr_multiplier=2.0))
    e.position = Position("t1", "LONG", 1.0, pd.Timestamp(entry_time), 100.0, 0.1, 1000.0, stop)
    return e


def watch(tmp_path, runners, frame):
    store = PaperStore(str(tmp_path / "paper.db"))
    fetch = FakeFetch(frame)
    return StopWatch(runners, store, "run", "1d", fetch=fetch, clock=lambda: DETECTED), store, fetch


def test_entry_stop_matches_the_stop_the_engine_sets():
    e = Engine(EngineConfig(atr_multiplier=2.0))
    e.pending = ("ENTER", "LONG", 5.0, 50.0)
    planned = e.entry_stop(100.0)
    e.on_bar(PERIOD, 100.0, 101.0, 99.9, 100.5, "-", 5.0, 50.0)
    assert e.position.stop == pytest.approx(planned)
    assert e.entry_stop(100.0) is None                     # in a position: nothing pending


def test_first_hit_is_recorded_once_with_the_estimated_fill(tmp_path):
    frame = minutes([(100, 99), (99, 98), (96, 94.5), (94, 93)])
    w, store, _ = watch(tmp_path, [Runner(in_position())], frame)
    w.check()
    w.check()
    ev = store.read("stop_events")
    assert len(ev) == 1
    row = ev.iloc[0]
    assert pd.Timestamp(row.minute_time) == PERIOD + pd.Timedelta(minutes=2)
    assert row.est_fill == 95.0 and row.minute_open == 96 and row.minute_low == 94.5   # open above stop: fill at the stop
    assert pd.Timestamp(row.entry_time) == pd.Timestamp("2026-10-20")


def test_a_gap_below_the_stop_fills_at_the_open(tmp_path):
    w, store, _ = watch(tmp_path, [Runner(in_position())], minutes([(100, 99), (93, 92)]))
    w.check()
    assert store.read("stop_events").est_fill.tolist() == [93.0]


def test_minutes_already_checked_are_not_fetched_again(tmp_path):
    fetch_frame = minutes([(100, 99), (99, 98)])
    w, store, fetch = watch(tmp_path, [Runner(in_position())], fetch_frame)
    w.check()
    fetch.frame = minutes([(100, 99), (99, 98), (97, 94)])
    w.check()
    assert fetch.calls[0][2] == PERIOD and fetch.calls[1][2] == PERIOD + pd.Timedelta(minutes=2)
    assert fetch.calls[0][1] == "1m"
    assert len(store.read("stop_events")) == 1


def test_minutes_of_the_next_main_candle_are_ignored(tmp_path):
    late = minutes([(94, 90)], start=PERIOD + pd.Timedelta(days=1))
    w, store, _ = watch(tmp_path, [Runner(in_position())], pd.concat([minutes([(100, 99)]), late]))
    w.check()
    assert store.read("stop_events").empty


def test_nothing_to_watch(tmp_path):
    exiting = in_position()
    exiting.pending = ("EXIT", "SIGNAL")                  # sold at this candle's open
    no_stop = in_position(stop=None)
    flat = Engine(EngineConfig(atr_multiplier=2.0))
    fresh = Runner(in_position(), last_time=None)         # first run: no candle processed yet
    w, store, fetch = watch(tmp_path, [Runner(exiting), Runner(no_stop), Runner(flat), fresh],
                            minutes([(100, 50)]))
    w.check()
    assert fetch.calls == [] and store.read("stop_events").empty


def test_pending_entry_is_watched_from_its_open(tmp_path):
    e = Engine(EngineConfig(atr_multiplier=2.0))
    e.pending = ("ENTER", "LONG", 5.0, 50.0)
    stop = e.entry_stop(100.0)                            # ~90
    w, store, _ = watch(tmp_path, [Runner(e)], minutes([(100, 95), (95, 91), (91, 89)]))
    w.check()
    ev = store.read("stop_events").iloc[0]
    assert pd.Timestamp(ev.entry_time) == PERIOD          # the trade's entry time once the engine opens it
    assert ev.stop == pytest.approx(stop) and ev.est_fill == pytest.approx(stop)


def test_pending_entry_without_its_open_minute_is_skipped(tmp_path):
    e = Engine(EngineConfig(atr_multiplier=2.0))
    e.pending = ("ENTER", "LONG", 5.0, 50.0)
    w, store, _ = watch(tmp_path, [Runner(e)], minutes([(91, 80)], start=PERIOD + pd.Timedelta(minutes=5)))
    w.check()
    assert store.read("stop_events").empty


def test_restart_does_not_record_a_trade_twice(tmp_path):
    frame = minutes([(96, 94)])
    w, store, _ = watch(tmp_path, [Runner(in_position())], frame)
    w.check()
    again = StopWatch([Runner(in_position())], store, "run", "1d", fetch=FakeFetch(frame))
    again.check()
    assert len(store.read("stop_events")) == 1
    assert again.fetch.calls == []


def test_a_fetch_error_doesnt_stop_the_other_symbols(tmp_path, capsys):
    frame = minutes([(96, 94)])

    def fetch(symbol, timeframe, since):
        if symbol == "BAD/USDT":
            raise ConnectionError("down")
        return frame

    store = PaperStore(str(tmp_path / "paper.db"))
    w = StopWatch([Runner(in_position(), "BAD/USDT"), Runner(in_position(), "OK/USDT")], store, "run", "1d", fetch=fetch)
    w.check()
    assert store.read("stop_events").symbol.tolist() == ["OK/USDT"]
    assert "WATCH ERROR" in capsys.readouterr().out


@pytest.mark.parametrize("mode,timeframe,stop,expected", [
    ("paper", "1d", "tight", True), ("paper", "4h", "loose", True), ("paper", "1d", "none", False),
    ("paper", "5m", "tight", False), ("paper", "1h", "tight", False), ("testnet", "1d", "none", False)])
def test_eligible(mode, timeframe, stop, expected):
    inst = instances.Instance(name="x", mode=mode, timeframe=timeframe, stop=stop)
    assert position_watch.eligible(inst) is expected


def test_sleep_calls_on_minute_once_a_minute(monkeypatch):
    start = 1_000_000_000_000 // 3_600_000 * 3_600_000      # an hour boundary
    clock = {"now": start}
    calls = []
    monkeypatch.setattr(run_realtime, "now_ms", lambda: clock["now"])
    monkeypatch.setattr(run_realtime.time, "sleep", lambda s: clock.__setitem__("now", clock["now"] + int(s * 1000) + 1))
    run_realtime.sleep_until_next_close("1h", on_minute=lambda: calls.append(clock["now"]))
    assert 58 <= len(calls) <= 60
    assert all(b - a >= 59_000 for a, b in zip(calls, calls[1:]))
    assert all((t - run_realtime.CLOSE_DELAY_S * 1000) % 60_000 < 1000 for t in calls)   # right after a 1m close


def _stop_setup(tmp_path):
    path = str(tmp_path / "instances.json")
    inst = instances.create({"name": "tight", "stop": "tight", "symbols": ["X/USDT"], "enabled": True}, path)
    store = PaperStore()
    return inst, store, path


def test_report_joins_the_engine_exit(tmp_path):
    inst, store, _ = _open_and_hit(tmp_path)
    store.trade_closed(Trade("t1", "X/USDT", "LONG", pd.Timestamp("2026-10-20"), PERIOD, 100.0, 94.95, 1.0,
                             0.2, -5.25, -0.5, "STOP"))
    store.conn.commit()
    df = reporting.stop_events(inst)
    assert len(df) == 1 and df.exit_price.iloc[0] == 94.95 and df.reason.iloc[0] == "STOP"
    assert reporting.stop_events(instances.Instance(name="tn", mode="testnet")).empty


def _open_and_hit(tmp_path):
    inst, store, path = _stop_setup(tmp_path)
    p = Position("t1", "LONG", 1.0, pd.Timestamp("2026-10-20"), 100.0, 0.1, 1000.0, 95.0)
    store.trade_opened(inst.run_key, inst.timeframe, "X/USDT", p)
    with store.candle():
        store.log_stop_event(inst.run_key, inst.timeframe, "X/USDT", p.entry_time, 95.0,
                             PERIOD + pd.Timedelta(minutes=2), 96.0, 94.5, 95.0, DETECTED)
    return inst, store, path


class Recorder:
    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)


def test_stop_hits_are_notified_once(tmp_path):
    inst, store, path = _stop_setup(tmp_path)
    rec = Recorder()
    w = notify.Watcher(rec, state_file=str(tmp_path / "state.json"), instances_path=path, clock=lambda: 0)
    w.poll(force=True)                                    # first poll only sets the cursors
    with store.candle():
        store.log_stop_event(inst.run_key, inst.timeframe, "X/USDT", pd.Timestamp("2026-10-20"), 95.0,
                             PERIOD + pd.Timedelta(minutes=2), 96.0, 94.5, 95.0, DETECTED)
    w.poll(force=True)
    w.poll(force=True)
    stops = [m for m in rec.messages if "stop hit" in m]
    assert len(stops) == 1
    assert "X/USDT stop 95 at 2026-10-30 00:02 UTC, est. fill 95 (+0.00% vs stop)" in stops[0]
    assert "1d close" in stops[0]
