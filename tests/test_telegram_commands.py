import json

import pandas as pd
import pytest

from kripto_bot import instances
from kripto_bot.app import notify, reporting, telegram_commands
from kripto_bot.app.telegram_commands import CONFIRM_TTL_S, MAX_AGE_S, CommandBot
from kripto_bot.core.engine import Position, Trade
from kripto_bot.trading.store import PaperStore

CHAT = 42
NOW = pd.Timestamp("2026-10-30 12:00", tz="UTC").timestamp()


class FakeApi:
    """Stands in for notify.telegram_call: hands out queued updates and records calls."""

    def __init__(self):
        self.updates, self.calls = [], []

    def __call__(self, token, method, params=None, timeout=15):
        self.calls.append((method, dict(params or {})))
        if method == "getUpdates":
            out, self.updates = self.updates, []
            return out
        return {"message_id": 99} if method == "sendMessage" else True

    def sent(self, method):
        return [p for m, p in self.calls if m == method]

    def last(self):
        """(text, callback data of every button) of the last message sent or edited."""
        p = [p for m, p in self.calls if m in ("sendMessage", "editMessageText")][-1]
        keyboard = json.loads(p.get("reply_markup", '{"inline_keyboard": []}'))["inline_keyboard"]
        return p["text"], [b["callback_data"] for row in keyboard for b in row]


@pytest.fixture
def bot():
    api = FakeApi()
    clock = {"now": NOW}
    b = CommandBot("token", str(CHAT), call=api, prices=lambda symbols: {s: 110.0 for s in symbols},
                   clock=lambda: clock["now"])
    b.api, b.time = api, clock
    return b


def _say(b, text, chat=CHAT, date=NOW):
    b.handle({"update_id": 1, "message": {"message_id": 1, "chat": {"id": chat}, "date": date, "text": text}})


def _press(b, data, chat=CHAT, date=NOW, edit_date=None):
    msg = {"message_id": 7, "chat": {"id": chat}, "date": date}
    if edit_date:
        msg["edit_date"] = edit_date
    b.handle({"update_id": 2, "callback_query": {"id": "q", "data": data, "message": msg}})


def _instance(name="fwd", manual_close=True, enabled=True, symbols=("X/USDT", "Y/USDT")):
    inst = instances.create({"name": name, "symbols": list(symbols), "enabled": enabled,
                             "manual_close": manual_close})
    store = PaperStore()
    store.trade_opened(inst.run_key, inst.timeframe, "X/USDT",
                       Position(f"t-{name}", "LONG", 2.0, pd.Timestamp("2026-10-20"), 100.0, 0.0, 200.0, None))
    store.trade_opened(inst.run_key, inst.timeframe, "Y/USDT",
                       Position(f"u-{name}", "LONG", 1.0, pd.Timestamp("2026-10-01"), 10.0, 0.0, 10.0, None))
    store.trade_closed(Trade(f"u-{name}", "Y/USDT", "LONG", pd.Timestamp("2026-10-01"), pd.Timestamp("2026-10-05"),
                             10.0, 12.0, 1.0, 0.0, 2.0, 20.0, "SIGNAL"))
    store.log_signal(inst.run_key, inst.timeframe, "X/USDT", pd.Timestamp("2026-10-29"), "BUY", 55.0, 100.0,
                     "NONE", "buy at the next open")
    store.conn.commit()
    return inst


def test_only_our_chat_is_answered(bot):
    _say(bot, "/status", chat=7)
    _press(bot, "l", chat=7)
    assert bot.api.calls == []


def test_commands_sent_while_down_are_ignored(bot):
    _say(bot, "/status", date=NOW - MAX_AGE_S - 1)
    assert bot.api.calls == []
    _say(bot, "hello")
    assert "/instance" in bot.api.last()[0]                       # anything else gets the help text


def test_status(bot):
    _instance()
    instances.create({"name": "idle", "favorite": True})
    open(notify.config.KILL_SWITCH_FILE, "w").close()
    _say(bot, "/status@CryptoTesterBot")
    text = bot.api.last()[0]
    assert "🟢 fwd (paper · v9 · 1d): supervisor not running\n  no data yet · 1 open" in text
    assert "⚪️ idle ⭐: stopped" in text and "Kill switch: block" in text


def test_instance_views(bot):
    _instance()
    instances.update("fwd", favorite=True)
    _say(bot, "/instance")
    text, buttons = bot.api.last()
    assert "Pick an instance" in text and "i|fwd" in buttons
    labels = [b["text"] for row in json.loads(bot.api.sent("sendMessage")[-1]["reply_markup"])["inline_keyboard"]
              for b in row]
    assert "🟢 fwd ⭐" in labels
    _press(bot, "i|fwd")
    assert bot.api.sent("answerCallbackQuery")
    text, buttons = bot.api.last()
    assert text.startswith("fwd ⭐ (paper · v9") and "Manual close: on" in text
    assert buttons[:3] == ["p|fwd", "s|fwd", "t|fwd"]
    _press(bot, "p|fwd")
    text, buttons = bot.api.last()
    assert "X/USDT: entry 100 on 2026-10-20 00:00 · now 110 (+10.00%)" in text
    assert "Y/USDT" not in text and [b for b in buttons if b.startswith("c|")] == ["c|1"]
    _press(bot, "s|fwd")
    assert "X/USDT BUY @ 100: buy at the next open" in bot.api.last()[0]
    _press(bot, "t|fwd")
    assert "Y/USDT +20.00% (SIGNAL), PnL +2.00" in bot.api.last()[0]
    _press(bot, "i|gone")
    assert "no instance named gone" in bot.api.last()[0]


def test_no_close_buttons_without_manual_close(bot):
    _instance(manual_close=False)
    _press(bot, "p|fwd")
    assert not [b for b in bot.api.last()[1] if b.startswith("c|")]


def test_close_asks_first_then_files_the_request(bot):
    inst = _instance()
    _press(bot, "p|fwd")
    _press(bot, "c|1")
    text, buttons = bot.api.last()
    assert "Close X/USDT on fwd" in text and buttons == ["y|1", "p|fwd"]
    assert not reporting.positions(inst).close_requested.any()    # nothing happens before Yes
    _press(bot, "y|1", edit_date=NOW - 5)
    assert "Close of X/USDT requested" in bot.api.last()[0]
    pos = reporting.positions(inst).set_index("symbol")
    assert bool(pos.loc["X/USDT", "close_requested"])
    _press(bot, "y|1", edit_date=NOW - 5)                         # a second tap
    assert "already requested" in bot.api.last()[0]
    _press(bot, "p|fwd")
    text, buttons = bot.api.last()
    assert "close requested" in text and "c|1" not in buttons


def test_late_confirmation_is_refused(bot):
    inst = _instance()
    _press(bot, "p|fwd")
    _press(bot, "y|1", edit_date=NOW - CONFIRM_TTL_S - 1)
    assert "Too late" in bot.api.last()[0]
    assert not reporting.positions(inst).close_requested.any()


def test_buttons_from_before_a_restart_expire(bot):
    _instance()
    _press(bot, "y|1")
    assert "expired" in bot.api.last()[0]


def test_close_refused_when_the_instance_is_stopped(bot):
    _instance(enabled=False)
    _press(bot, "p|fwd")
    _press(bot, "y|1")
    assert "start the instance first" in bot.api.last()[0]


def test_refresh_without_changes_is_not_an_error(bot):
    _instance()

    def call(token, method, params=None, timeout=15):
        if method == "editMessageText":
            raise notify.TelegramError("Bad Request: message is not modified")
        return True

    bot.call = call
    _press(bot, "i|fwd")                                          # no exception


def test_poll_confirms_updates_and_survives_bad_ones(bot):
    logged = []
    bot.log = logged.append
    bot.api.updates = [{"update_id": 10, "message": {"chat": {"id": CHAT}, "date": NOW, "text": "/status"}},
                       {"update_id": 11, "callback_query": {"id": "q", "data": "i|x"}}]   # no message: ignored
    bot.poll_once(timeout=0)
    assert bot.offset == 12 and "Status" in bot.api.last()[0] and logged == []
    bot.poll_once(timeout=0)
    assert bot.api.sent("getUpdates")[-1]["offset"] == 12


def test_conflicts_are_logged_once(bot, monkeypatch):
    logged = []
    bot.log = logged.append
    rounds = iter(range(3))

    def call(token, method, params=None, timeout=15):
        if method == "getUpdates":
            if next(rounds, None) is None:
                bot.stop()
            raise notify.TelegramError("Conflict: terminated by other getUpdates request")
        return True

    bot.call = call
    monkeypatch.setattr(bot._stop, "wait", lambda s: None)
    bot._run()
    assert len(logged) == 1 and "telegram-setup" in logged[0]


def test_disabled_without_token():
    assert not telegram_commands.CommandBot("", "1").enabled
