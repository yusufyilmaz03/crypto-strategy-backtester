# kripto_bot/app/notify.py
"""Telegram notifications, sent by the supervisor (run_all.py).

The bots themselves don't send anything: a Watcher in the supervisor reads their
databases every POLL_S seconds and reports what is new, so notifications can't
slow down or break trading. Reported:

- opened/closed paper positions, shadow stop hits and testnet orders (NOTIFY_TRADES),
  one message per instance and poll, not one per symbol
- testnet warnings and errors, crashed or blocked processes, kill switch changes
  (always)
- a daily summary of every enabled instance (NOTIFY_DAILY_SUMMARY)

Messages go through a background queue; if Telegram can't be reached they are
retried a few times and then dropped with a line in the supervisor output. The
bot token is never printed.
"""
import json
import os
import queue
import sqlite3
import threading
import time
import urllib.error
import urllib.parse

import pandas as pd

from kripto_bot import config, instances, net, paths, settings
from kripto_bot.app import reporting
from kripto_bot.trading import store

API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000          # Telegram's limit is 4096 characters
POLL_S = 30
MAX_ITEMS = 12          # list at most this many trades per message
STATE_FILE = paths.runtime("notify_state.json")


class TelegramError(RuntimeError):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def telegram_call(token, method, params=None, timeout=15):
    """Call the Bot API. Errors never include the URL, which contains the token."""
    url = API.format(token=token, method=method)
    data = urllib.parse.urlencode(params or {}).encode()
    try:
        with net.urlopen(url, data=data, timeout=timeout) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        try:
            body = json.load(e)
        except Exception:
            raise TelegramError(f"HTTP {e.code}") from None
    except (urllib.error.URLError, OSError) as e:
        raise TelegramError(f"network error: {getattr(e, 'reason', e)}") from None
    if not body.get("ok"):
        raise TelegramError(body.get("description", "request failed"),
                            (body.get("parameters") or {}).get("retry_after"))
    return body["result"]


def split_message(text, limit=MAX_LEN):
    """Split on line breaks into chunks Telegram accepts."""
    chunks, cur = [], ""
    for line in text.split("\n"):
        while len(line) > limit:
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(line[:limit])
            line = line[limit:]
        if cur and len(cur) + 1 + len(line) > limit:
            chunks.append(cur)
            cur = line
        else:
            cur = f"{cur}\n{line}" if cur else line
    if cur:
        chunks.append(cur)
    return chunks


class Telegram:
    """Sends messages to one chat from a background thread."""

    def __init__(self, token, chat_id, log=print, call=telegram_call, pause_s=1.1, retries=3):
        self.token, self.chat_id = token, str(chat_id)
        self.log, self.call, self.pause_s, self.retries = log, call, pause_s, retries
        self.queue = queue.Queue(maxsize=200)
        self._thread = None

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    def send_now(self, text):
        """Send synchronously and raise TelegramError on failure (used by the panel's test button)."""
        for chunk in split_message(text):
            self.call(self.token, "sendMessage", {"chat_id": self.chat_id, "text": chunk,
                                                  "disable_web_page_preview": "true"})

    def send(self, text):
        """Queue a message; never blocks and never raises."""
        if not self.enabled or not text:
            return
        if self._thread is None:
            self._thread = threading.Thread(target=self._worker, daemon=True, name="telegram")
            self._thread.start()
        for chunk in split_message(text):
            try:
                self.queue.put_nowait(chunk)
            except queue.Full:
                self.log("[notify] queue full, message dropped")

    def _worker(self):
        while True:
            chunk = self.queue.get()
            try:
                self._deliver(chunk)
            finally:
                self.queue.task_done()
            time.sleep(self.pause_s)        # Telegram allows about one message per second per chat

    def _deliver(self, chunk):
        for attempt in range(self.retries):
            try:
                self.send_now(chunk)
                return
            except TelegramError as e:
                if attempt == self.retries - 1:
                    self.log(f"[notify] Telegram message dropped: {e}")
                    return
                time.sleep(e.retry_after if e.retry_after is not None else 2 ** attempt * 5)

    def flush(self, timeout=10.0):
        """Wait until queued messages are sent (on shutdown)."""
        end = time.time() + timeout
        while self.queue.unfinished_tasks and time.time() < end:
            time.sleep(0.1)


# ---------------------------------------------------------------- formatting
def _num(x, digits=6):
    return f"{x:.{digits}g}" if x is not None and not pd.isna(x) else "?"


def _pct(x):
    return f"{x:+.2f}%" if x is not None and not pd.isna(x) else "n/a"


def _limit(lines):
    if len(lines) <= MAX_ITEMS:
        return lines
    return lines[:MAX_ITEMS] + [f"  … and {len(lines) - MAX_ITEMS} more"]


def _setup(inst):
    params = " ".join(f"{k}={v}" for k, v in inst.params.items())
    return f"{inst.mode} · {inst.strategy}{' ' + params if params else ''} · {inst.timeframe}"


def format_paper(inst, opened, closed):
    lines = [f"📈 {inst.name} ({_setup(inst)})"]
    if opened:
        lines.append(f"Opened {len(opened)}:")
        lines += _limit([f"  {r['symbol']} @ {_num(r['entry_price'])}" for r in opened])
    if closed:
        lines.append(f"Closed {len(closed)}:")
        lines += _limit([f"  {r['symbol']} {_pct(r['return_pct'])} ({r['reason'] or 'signal'})" for r in closed])
    return "\n".join(lines)


def format_stops(inst, events):
    lines = [f"⚠️ {inst.name} ({_setup(inst)}): stop hit (shadow stop)"]
    lines += _limit([f"  {e['symbol']} stop {_num(e['stop'])} at {pd.Timestamp(e['minute_time']):%Y-%m-%d %H:%M} UTC, "
                     f"est. fill {_num(e['est_fill'])} ({_pct((e['est_fill'] / e['stop'] - 1) * 100)} vs stop)"
                     for e in events])
    lines.append(f"Paper closes the position at the {inst.timeframe} close.")
    return "\n".join(lines)


def format_orders(inst, orders):
    lines = [f"🧪 {inst.name} ({_setup(inst)}): {len(orders)} order(s)"]
    rows = []
    for o in orders:
        text = f"  {str(o['side']).upper()} {o['symbol']} {_num(o['filled'])} @ {_num(o['average'])} [{o['status']}]"
        if o.get("pnl") is not None and not pd.isna(o["pnl"]):
            text += f" PnL {o['pnl']:+.2f} USDT"
        if o.get("error"):
            text += f" error: {o['error']}"
        rows.append(text)
    return "\n".join(lines + _limit(rows))


def format_events(inst, events):
    icon = {"ERROR": "🛑", "WARN": "⚠️"}
    rows = [f"{icon.get(e['level'], '•')} {inst.name}: {e['level']}"
            f"{' ' + e['symbol'] if e['symbol'] else ''}: {e['message']}" for e in events]
    return "\n".join(_limit(rows))


def format_summary(items, kill_state, day):
    lines = [f"📊 Daily summary {day}"]
    for inst, s in items:
        if s["return_pct"] is None:
            lines.append(f"• {inst.name} ({_setup(inst)}): no data yet")
            continue
        dd = f"max DD {s['max_dd_pct']:.1f}%" if s["max_dd_pct"] is not None else ""
        if s["buy_hold_max_dd_pct"] is not None:
            dd += f" (B&H {s['buy_hold_max_dd_pct']:.1f}%)"
        win = f", win {s['win_rate_pct']:.0f}%" if s["win_rate_pct"] is not None else ""
        lines.append(f"• {inst.name} ({_setup(inst)}), {s['days']} days\n"
                     f"  {_pct(s['return_pct'])} vs B&H {_pct(s['buy_hold_pct'])} · {dd}\n"
                     f"  {s['open_positions']} open · {s['closed_trades']} closed{win}")
    if len(lines) == 1:
        lines.append("No instance is enabled.")
    lines.append(f"Kill switch: {kill_state}")
    return "\n".join(lines)


# ---------------------------------------------------------------- watching
def _rows(db, sql, params=()):
    if not os.path.exists(db):
        return []
    conn = sqlite3.connect(db, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(sql, params)]
    except sqlite3.Error:
        return []
    finally:
        conn.close()


def advance(cursor, items):
    """New items since the cursor. items: [(time, id, row)]. The cursor keeps the
    latest time and the ids seen at that time, so rows written in several
    transactions for the same candle are each reported once."""
    cursor = cursor or {"t": "", "ids": []}
    seen = set(cursor["ids"])
    new = [row for t, i, row in items if t > cursor["t"] or (t == cursor["t"] and i not in seen)]
    if items:
        top = max(t for t, _, _ in items)
        if top > cursor["t"]:
            cursor = {"t": top, "ids": sorted(i for t, i, _ in items if t == top)}
        else:
            cursor = {"t": cursor["t"], "ids": sorted(seen | {i for t, i, _ in items if t == top})}
    return new, cursor


def kill_state(path=None):
    path = path or config.KILL_SWITCH_FILE
    if not os.path.exists(path):
        return "off"
    with open(path) as f:
        return "flatten" if "flatten" in f.read().lower() else "block"


def option(key):
    """Current value of a setting: panel override, else config default (read every time,
    so changes on the Settings page apply without a restart)."""
    return settings.load_overrides().get(key, getattr(config, key))


class Watcher:
    def __init__(self, sender, state_file=None, instances_path=None, paper_db=None,
                 poll_s=POLL_S, clock=time.time, log=print):
        self.sender = sender
        self.state_file = state_file or STATE_FILE
        self.instances_path = instances_path
        self.paper_db = paper_db
        self.poll_s, self.clock, self.log = poll_s, clock, log
        self.last_poll = 0.0
        self.failures = {}            # service name -> consecutive quick exits
        self.blocked_sent = set()
        self.state = self._load()

    # ----- state
    def _load(self):
        try:
            with open(self.state_file) as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}

    def _save(self):
        tmp = f"{self.state_file}.tmp"
        with open(tmp, "w") as f:
            json.dump(self.state, f, indent=1)
        os.replace(tmp, self.state_file)

    # ----- supervisor hooks
    def started(self, names):
        self.sender.send(f"▶️ Crypto Tester Bot supervisor started. Running: {', '.join(names) or 'no instances'}")

    def stopped(self):
        self.sender.send("⏹ Crypto Tester Bot supervisor stopped. Bots are not running.")

    def service_exited(self, name, code, ran_s, stable_after_s):
        n = 1 if ran_s >= stable_after_s else self.failures.get(name, 0) + 1
        self.failures[name] = n
        if n in (1, 3) or n % 10 == 0:
            again = f" ({n} quick exits in a row)" if n > 1 else ""
            self.sender.send(f"💥 {name} exited with code {code} after {ran_s:.0f}s{again}; "
                             "the supervisor restarts it. See its log on the panel.")

    def blocked(self, blocked):
        for name in set(blocked) - self.blocked_sent:
            self.sender.send(f"⛔ {name} can't start: {blocked[name]}")
        self.blocked_sent = set(blocked)

    # ----- polling
    def poll(self, blocked=None, force=False):
        now = self.clock()
        if not force and now - self.last_poll < self.poll_s:
            return
        self.last_poll = now
        try:
            self.blocked(blocked or {})
            self._kill()
            for inst in instances.load_all(self.instances_path).values():
                if inst.mode == "paper":
                    self._paper(inst)
                else:
                    self._testnet(inst)
            self._daily(now)
        except Exception as e:      # a reporting problem must never stop the supervisor
            self.log(f"[notify] {type(e).__name__}: {e}")
        finally:
            self._save()

    def _cursors(self, inst):
        return self.state.setdefault("instances", {}).setdefault(inst.name, {})

    def _paper(self, inst):
        cur = self._cursors(inst)
        rows = _rows(self.paper_db or store.DB_FILE,
                     "SELECT trade_id, symbol, entry_time, exit_time, entry_price, return_pct, reason "
                     "FROM trades WHERE strategy=? AND timeframe=?", (inst.run_key, inst.timeframe))
        entries = [(r["entry_time"], r["trade_id"], r) for r in rows if r["entry_time"]]
        exits = [(r["exit_time"], r["trade_id"], r) for r in rows if r["exit_time"]]
        first = "entries" not in cur
        opened, cur["entries"] = advance(cur.get("entries"), entries)
        closed, cur["exits"] = advance(cur.get("exits"), exits)
        if not first and (opened or closed) and option("NOTIFY_TRADES"):
            self.sender.send(format_paper(inst, opened, closed))
        stops = _rows(self.paper_db or store.DB_FILE,
                      "SELECT id, symbol, stop, minute_time, est_fill FROM stop_events "
                      "WHERE strategy=? AND timeframe=? AND id > ? ORDER BY id",
                      (inst.run_key, inst.timeframe, cur.get("stop_id", 0)))
        first = "stop_id" not in cur
        cur["stop_id"] = stops[-1]["id"] if stops else cur.get("stop_id", 0)
        if not first and stops and option("NOTIFY_TRADES"):
            self.sender.send(format_stops(inst, stops))

    def _testnet(self, inst):
        cur = self._cursors(inst)
        db = inst.db_path
        orders = _rows(db, "SELECT * FROM orders")
        first = "orders" not in cur
        new_orders, cur["orders"] = advance(cur.get("orders"),
                                            [(o["created_at"] or "", o["client_id"], o) for o in orders])
        events = _rows(db, "SELECT id, time, level, symbol, message FROM events WHERE id > ? ORDER BY id",
                       (cur.get("event_id", 0),))
        if events:
            cur["event_id"] = events[-1]["id"]
        if first:
            return
        if new_orders and option("NOTIFY_TRADES"):
            self.sender.send(format_orders(inst, sorted(new_orders, key=lambda o: o["created_at"] or "")))
        alerts = [e for e in events if e["level"] in ("WARN", "ERROR")]
        if alerts:
            self.sender.send(format_events(inst, alerts))

    def _kill(self):
        state = kill_state()
        before = self.state.get("kill_switch")
        self.state["kill_switch"] = state
        if before is not None and state != before:
            text = {"off": "✅ Kill switch is off: entries are allowed again.",
                    "block": "🛑 Kill switch ON: no new entries.",
                    "flatten": "🛑 Kill switch FLATTEN: testnet positions are being closed."}[state]
            self.sender.send(text)

    def _daily(self, now):
        if not option("NOTIFY_DAILY_SUMMARY"):
            return
        ts = pd.Timestamp(now, unit="s", tz="UTC")
        day = ts.strftime("%Y-%m-%d")
        if ts.hour < int(option("NOTIFY_SUMMARY_HOUR_UTC")) or self.state.get("summary_day") == day:
            return
        self.state["summary_day"] = day
        items = [(i, reporting.summary(i, self.paper_db))
                 for i in instances.load_all(self.instances_path).values() if i.enabled]
        self.sender.send(format_summary(items, kill_state(), day))


def from_config(log=print):
    return Telegram(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, log=log)
