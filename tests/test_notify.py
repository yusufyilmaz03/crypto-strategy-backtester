import json
import urllib.error

import pandas as pd
import pytest

from kripto_bot import config, instances, settings
from kripto_bot.app import notify
from kripto_bot.core.engine import Position, Trade
from kripto_bot.trading.broker import Fill
from kripto_bot.trading.store import PaperStore, TradingStore

DAY = pd.Timestamp("2026-10-12", tz="UTC").timestamp()


class Recorder:
    def __init__(self):
        self.messages = []

    def send(self, text):
        self.messages.append(text)

    def flush(self, timeout=0):
        pass


@pytest.fixture
def setup(tmp_path):
    path = str(tmp_path / "instances.json")
    paper = instances.create({"name": "fwd", "symbols": ["A/USDT", "B/USDT"], "enabled": True}, path)
    tn = instances.create({"name": "tn", "mode": "testnet"}, path)   # stopped
    tn_db = tn.db_path
    clock = {"now": DAY + 3600}           # 01:00 UTC, before the summary hour
    rec = Recorder()
    w = notify.Watcher(rec, state_file=str(tmp_path / "state.json"), instances_path=path,
                       clock=lambda: clock["now"])
    return w, rec, paper, tn_db, clock


def _open(store, inst, tid, symbol, t):
    store.trade_opened(inst.run_key, inst.timeframe, symbol,
                       Position(tid, "LONG", 1.0, pd.Timestamp(t), 100.0, 0.1, 100.0, None))
    store.conn.commit()


def test_split_message():
    assert notify.split_message("a\nb", limit=10) == ["a\nb"]
    chunks = notify.split_message("\n".join(["x" * 6] * 3) + "\n" + "y" * 25, limit=10)
    assert all(len(c) <= 10 for c in chunks) and "".join(chunks).replace("\n", "") == "x" * 18 + "y" * 25


def test_split_message_keeps_tables_valid():
    text = "head\n" + notify.table(["A", "B"], [[str(i), "x"] for i in range(10)]) + "\nfoot"
    chunks = notify.split_message(text, limit=40)
    assert len(chunks) > 2 and all(len(c) <= 40 for c in chunks)
    assert all(c.count("<pre>") == c.count("</pre>") for c in chunks)
    assert chunks[0].startswith("head\n<pre>") and chunks[-1].endswith("</pre>\nfoot")
    joined = "".join(c.replace("<pre>", "").replace("</pre>", "") for c in chunks)
    assert joined.replace("\n", "") == text.replace("<pre>", "").replace("</pre>", "").replace("\n", "")


def test_summary_table():
    fav = instances.Instance(name="forward-v9", favorite=True)
    rows = [(fav, {"return_pct": 3.21, "buy_hold_pct": -12.34, "max_dd_pct": 8.7, "open_positions": 3}),
            (instances.Instance(name="new"), {"return_pct": None, "buy_hold_pct": None, "max_dd_pct": None,
                                             "open_positions": 0})]
    text = notify.format_summary(rows, "off", "2026-11-01")
    assert ("<pre> Instance   Ret%  B&amp;H% DD% Open\xa0\xa0\xa0\n*forward-v9 +3.2 -12.3 8.7    3\n"
            " new           –     –   –    –</pre>") in text
    assert "* favorite" in text and text.endswith("Kill switch: off")
    assert "No instance is enabled." in notify.format_summary([], "off", "2026-11-01")


def test_advance_reports_rows_of_the_same_candle_once():
    t = "2026-10-12T00:00:00"
    new, cur = notify.advance(None, [(t, "a", 1)])
    assert new == [1]
    new, cur = notify.advance(cur, [(t, "a", 1), (t, "b", 2)])     # b was written in a later transaction
    assert new == [2]
    new, cur = notify.advance(cur, [(t, "a", 1), (t, "b", 2)])
    assert new == []
    new, cur = notify.advance(cur, [(t, "a", 1), ("2026-10-13T00:00:00", "c", 3)])
    assert new == [3] and cur == {"t": "2026-10-13T00:00:00", "ids": ["c"]}


def test_paper_trades(setup, tmp_path):
    w, rec, inst, _, clock = setup
    db = PaperStore()
    _open(db, inst, "old", "A/USDT", "2026-10-01")
    w.poll(force=True)
    assert rec.messages == []                       # history before the first poll is not sent
    _open(db, inst, "t1", "A/USDT", "2026-10-12")
    _open(db, inst, "t2", "B/USDT", "2026-10-12")
    w.poll(force=True)
    assert len(rec.messages) == 1 and rec.messages[0].startswith("📈 <b>fwd</b> - paper - v9 · 1d\nOpened 2\n<pre>")
    assert "\nB        100</pre>" in rec.messages[0]
    db.trade_closed(Trade("t1", "A/USDT", "LONG", pd.Timestamp("2026-10-12"), pd.Timestamp("2026-10-13"),
                          100, 95, 1, 0.2, -5.2, -5.2, "SIGNAL"))
    db.conn.commit()
    w.poll(force=True)
    assert "Closed 1\n<pre>Symbol  Ret%   PnL Exit\xa0\xa0\xa0\nA      -5.20 -5.20 signal</pre>" in rec.messages[-1]
    w.poll(force=True)
    assert len(rec.messages) == 2                   # nothing new, nothing sent

    # cursors survive a supervisor restart
    w2 = notify.Watcher(rec, state_file=w.state_file, instances_path=w.instances_path, clock=w.clock)
    w2.poll(force=True)
    assert len(rec.messages) == 2


def test_trade_messages_can_be_switched_off(setup, monkeypatch):
    w, rec, inst, _, _ = setup
    db = PaperStore()
    w.poll(force=True)
    settings.save({"NOTIFY_TRADES": False})
    _open(db, inst, "t1", "A/USDT", "2026-10-12")
    w.poll(force=True)
    assert rec.messages == []


def test_testnet_orders_and_alerts(setup):
    w, rec, _, tn_db, _ = setup
    st = TradingStore(tn_db)
    st.event("ERROR", "A/USDT", "old failure")
    st.commit()
    w.poll(force=True)
    assert rec.messages == []
    st.record_order(Fill("kb-1", "1", "A/USDT", "buy", "closed", 0.5, 50.0, 100.0, 0.0), "2026-10-12", 100.0)
    st.event("INFO", "A/USDT", "dust left")
    st.event("ERROR", "B/USDT", "BUY order failed: InsufficientFunds")
    st.commit()
    w.poll(force=True)
    text = "\n".join(rec.messages)
    assert "<pre>Side Symbol Qty Price\xa0\xa0\xa0\nBUY  A      0.5   100</pre>" in text   # no PnL yet, status closed
    assert "🛑 <b>tn</b>: ERROR B/USDT: BUY order failed: InsufficientFunds" in text
    assert "dust" not in text and "old failure" not in text


def test_kill_switch_changes(setup):
    w, rec, *_ = setup
    w.poll(force=True)
    with open(config.KILL_SWITCH_FILE, "w") as f:
        f.write("flatten")
    w.poll(force=True)
    assert "FLATTEN" in rec.messages[-1]
    import os
    os.remove(config.KILL_SWITCH_FILE)
    w.poll(force=True)
    assert "off" in rec.messages[-1]


def test_daily_summary_once_per_day(setup):
    w, rec, inst, _, clock = setup
    w.poll(force=True)
    assert not any("Daily summary" in m for m in rec.messages)
    clock["now"] = DAY + 7 * 3600                 # after NOTIFY_SUMMARY_HOUR_UTC (6)
    w.poll(force=True)
    w.poll(force=True)
    summaries = [m for m in rec.messages if "Daily summary</b> 2026-10-12" in m]
    assert len(summaries) == 1 and "\n fwd " in summaries[0] and "tn" not in summaries[0].split("\n", 1)[1]
    clock["now"] = DAY + 86400 + 7 * 3600
    w.poll(force=True)
    assert "Daily summary</b> 2026-10-13" in rec.messages[-1]


def test_poll_is_throttled(setup):
    w, rec, inst, _, clock = setup
    db = PaperStore()
    w.poll()
    _open(db, inst, "t1", "A/USDT", "2026-10-12")
    clock["now"] += 5
    w.poll()
    assert rec.messages == []
    clock["now"] += notify.POLL_S
    w.poll()
    assert len(rec.messages) == 1


def test_crash_messages_are_rate_limited(setup):
    w, rec, *_ = setup
    for _ in range(10):
        w.service_exited("fwd", 1, 2, 600)
    assert len(rec.messages) == 3                  # 1st, 3rd and 10th quick exit
    w.service_exited("fwd", 1, 4000, 600)          # ran a long time: a fresh crash
    assert len(rec.messages) == 4 and "quick exits" not in rec.messages[-1]
    w.blocked({"tn": "keys missing"})
    w.blocked({"tn": "keys missing"})
    assert rec.messages[-1] == "⛔ <b>tn</b> can't start: keys missing" and len(rec.messages) == 5
    w.blocked({"a<b": "x & y"})
    assert rec.messages[-1] == "⛔ <b>a&lt;b</b> can't start: x &amp; y"


def test_sender_retries_and_never_raises():
    calls, logs = [], []

    def flaky(token, method, params):
        calls.append(params["text"])
        if len(calls) < 2:
            raise notify.TelegramError("Too Many Requests", retry_after=0)
        return {}

    t = notify.Telegram("TOKEN", 42, log=logs.append, call=flaky, pause_s=0)
    t.send("hello")
    t.flush(5)
    assert calls == ["hello", "hello"] and logs == []

    def down(token, method, params):
        raise notify.TelegramError("network error: offline", retry_after=0)

    t = notify.Telegram("TOKEN", 42, log=logs.append, call=down, pause_s=0, retries=2)
    t.send("lost")
    t.flush(5)
    assert logs == ["[notify] Telegram message dropped: network error: offline"]
    assert notify.Telegram("", 42).enabled is False


def test_telegram_errors_hide_the_token(monkeypatch):
    def offline(*a, **k):
        raise urllib.error.URLError("nodename nor servname provided")

    monkeypatch.setattr(notify.net, "urlopen", offline)
    with pytest.raises(notify.TelegramError) as e:
        notify.telegram_call("123:SECRET", "sendMessage", {"chat_id": 1, "text": "x"})
    assert "SECRET" not in str(e.value) and "network error" in str(e.value)


def test_supervisor_reports_exits(tmp_path):
    from kripto_bot.app.run_all import Service
    exits = []
    svc = Service("demo", ["python3", "-c", "import sys; sys.exit(3)"], str(tmp_path),
                  on_exit=lambda *a: exits.append(a))
    svc.check()
    svc.proc.wait()
    svc.check()
    assert exits and exits[0][:2] == ("demo", 3)
