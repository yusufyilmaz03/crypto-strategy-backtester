# kripto_bot/trading/llm_analyst.py
"""The v11 AI analyst as a paper instance (docs/v11-ai-analyst.md).

An instance with strategy "v11" runs this module instead of the strategy engine. Its setup
is in inst.params: model, effort, news (False for the no-news control) and group (symbols per
scheduled call, 1 or 5). Each pass of the loop:

1. runs the closed 1m candles of every symbol through the order simulator; fills go into the
   trades table, so the panel, reports and Telegram show them as for any paper instance;
2. once per UTC day after the close: marks equity and makes the scheduled review;
3. calls the model for a symbol whose review level was reached, within the call limits:
   3 triggered reviews per symbol per day, none within 60 minutes of the symbol's last call.
   A level reached more than 5 minutes ago (candles replayed after a pause) is not made up.

A usage-limit error or a wrong Codex version stops the calls for the rest of the UTC day and
sends a message; so does more than 10% of a day's calls failing (a warning only). Books,
plans and events live in paper.db, so a restart resumes and replays the missed candles.

    python -m kripto_bot.trading.llm_analyst --instance NAME
"""
import argparse
import json
import time
import uuid
from types import SimpleNamespace

import pandas as pd

from kripto_bot import config
from kripto_bot.core import data
from kripto_bot.llm import codex_cli, inputs, review
from kripto_bot.llm import plan as pl
from kripto_bot.llm import provider as p
from kripto_bot.llm.store import LLMStore
from kripto_bot.trading import order_sim as sim
from kripto_bot.trading import store

STRATEGY = "v11"
TIMEFRAME = "1d"
DEFAULTS = {"model": "gpt-6-astra", "effort": "high", "news": True, "group": 5}
MAX_TRIGGERED_PER_DAY = 3
COOLDOWN = pd.Timedelta(minutes=60)
TRIGGER_FRESH = pd.Timedelta(minutes=5)
HISTORY = 3
FAIL_WARN_SHARE = 0.10
REASONS = {"stop": "STOP", "take_profit": "TAKE_PROFIT", "exit": "EXIT"}
FIRST_LOOKBACK = pd.Timedelta(minutes=2)

SCHEMA = """
CREATE TABLE IF NOT EXISTS llm_books (
    instance TEXT, symbol TEXT, book TEXT, updated_at TEXT, PRIMARY KEY (instance, symbol)
);
CREATE TABLE IF NOT EXISTS llm_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, instance TEXT, symbol TEXT, time TEXT, kind TEXT, plan_id INTEGER,
    data TEXT
);
CREATE INDEX IF NOT EXISTS llm_events_instance_symbol ON llm_events (instance, symbol, id);
CREATE TABLE IF NOT EXISTS llm_forecasts (
    instance TEXT, plan_id INTEGER, symbol TEXT, fill_time TEXT, fill_price REAL, stop REAL,
    take_profit REAL, p_tp REAL, PRIMARY KEY (instance, plan_id)
);
"""


def _now():
    return pd.Timestamp.now(tz="UTC")


def setup(inst):
    return {**DEFAULTS, **(inst.params or {})}


def fetch_minutes(symbol, since, now, exchange=None):
    """Closed 1m candles from `since` (open time) up to `now`."""
    df = data.fetch_range(symbol, "1m", int(since.timestamp() * 1000), exchange=exchange)
    df = data.drop_unclosed(df, "1m", int(now.timestamp() * 1000))
    return [sim.Candle(pd.Timestamp(int(r.timestamp), unit="ms", tz="UTC"), r.open, r.high, r.low, r.close)
            for r in df.itertuples()]


def _naive(t):
    """Times in the shared trades and equity tables are naive UTC, like the other instances'."""
    return pd.Timestamp(t).tz_convert("UTC").tz_localize(None)


class Analyst:
    def __init__(self, inst, provider=None, db_path=None, notify=print, clock=_now, sleep=time.sleep,
                 snapshot=inputs.snapshot, minutes=fetch_minutes, log=print):
        self.inst, self.name, self.key = inst, inst.name, inst.run_key
        s = setup(inst)
        self.model, self.effort, self.news, self.group = s["model"], s["effort"], bool(s["news"]), int(s["group"])
        self.provider = provider or codex_cli.CodexCLI(self.model, self.effort, config.CODEX_PATH,
                                                       config.CODEX_VERSION, config.CODEX_HOME,
                                                       timeout=config.LLM_TIMEOUT_SEC)
        self.paper = store.PaperStore(db_path)
        self.llm = LLMStore(db_path)
        self.llm.conn.executescript(SCHEMA)
        self.notify, self.clock, self.sleep, self.log = notify, clock, sleep, log
        self.snapshot, self.minutes = snapshot, minutes
        self.symbols = inst.symbol_list
        self.books = {sym: self._load_book(sym) for sym in self.symbols}
        for sym in self.symbols:
            self._save(sym)
        self.last_close = {}
        self.blocked_day = None

    def close(self):
        self.paper.close()
        self.llm.close()

    # ---------- state ----------
    def _load_book(self, symbol):
        row = self.llm.conn.execute("SELECT book FROM llm_books WHERE instance=? AND symbol=?",
                                    (self.name, symbol)).fetchone()
        if row:
            return sim.from_dict(json.loads(row[0]))
        # a new book starts a little before now, so no candle is skipped if the first fetch is empty
        start = (self.clock() - FIRST_LOOKBACK).floor("min") - pd.Timedelta(minutes=1)
        return sim.Book(symbol, float(self.inst.equity), last_candle=start)

    def _save(self, symbol):
        with self.llm.conn:
            self.llm.conn.execute("INSERT OR REPLACE INTO llm_books VALUES (?, ?, ?, ?)",
                                  (self.name, symbol, json.dumps(sim.to_dict(self.books[symbol])),
                                   self.clock().isoformat()))

    def _event(self, symbol, kind, time_, plan_id=None, **data_):
        with self.llm.conn:
            self.llm.conn.execute("INSERT INTO llm_events (instance, symbol, time, kind, plan_id, data) "
                                  "VALUES (?, ?, ?, ?, ?, ?)",
                                  (self.name, symbol, pd.Timestamp(time_).isoformat(), kind, plan_id,
                                   json.dumps(data_, default=str)))

    def _plan(self, plan_id):
        row = self.llm.conn.execute("SELECT plan FROM llm_plans WHERE id=?", (plan_id,)).fetchone()
        return json.loads(row[0]) if row else {}

    def _accepted(self, symbol, n):
        return self.llm.conn.execute(
            "SELECT id, time, plan FROM llm_plans WHERE instance=? AND symbol=? AND accepted=1 ORDER BY id DESC LIMIT ?",
            (self.name, symbol, n)).fetchall()

    # ---------- what the model sees about its own state ----------
    def memo(self, symbol):
        rows = self._accepted(symbol, 1)
        return json.loads(rows[0][2]).get("memo", "") if rows else ""

    def history(self, symbol):
        lines = []
        for pid, t, plan_json in reversed(self._accepted(symbol, HISTORY)):
            plan = json.loads(plan_json)
            what = plan["action"]
            if plan["action"] == "enter":
                e = plan["entry"]
                what += (f" {e['type']}" + (f" {e['price']}" if e["type"] == "limit" else "")
                         + f", stop {plan['stop']}, take-profit {plan.get('take_profit')}")
            elif plan["action"] == "adjust":
                what += f" stop {plan['stop']}, take-profit {plan.get('take_profit')}"
            events = self.llm.conn.execute("SELECT kind, time, data FROM llm_events WHERE instance=? AND plan_id=? "
                                           "AND kind IN ('entry', 'stop', 'take_profit', 'exit', 'expired') ORDER BY id",
                                           (self.name, pid)).fetchall()
            happened = []
            for kind, et, d in events:
                d = json.loads(d)
                if kind in REASONS:
                    happened.append(f"closed by {kind.replace('_', '-')} {d.get('return_pct', 0):+.2f}% on {et[:16]}")
                else:
                    happened.append(f"{'filled' if kind == 'entry' else kind} on {et[:16]}")
            lines.append(f"- {t[:16]} {what} → {'; '.join(happened) or 'nothing filled'}")
        return lines

    def state(self, symbol, last_price):
        b = self.books[symbol]
        lines = []
        if b.position is not None:
            pos = b.position
            stop, tp = (b.levels.stop, b.levels.take_profit) if b.levels else (pos.stop, pos.take_profit)
            lines.append(f"- Position: long since {pos.entry_time:%Y-%m-%d %H:%M} UTC at {pos.entry_price:g}, "
                         f"stop {stop:g}, take-profit {tp if tp is not None else 'none'}, "
                         f"now {100 * (last_price / pos.entry_price - 1):+.2f}%")
        else:
            lines.append("- Position: flat.")
        if b.entry is not None:
            e = b.entry
            lines.append(f"- Pending entry: {e.kind}" + (f" buy at {e.price:g}, expires {e.expires_at:%Y-%m-%d %H:%M} UTC"
                                                       if e.kind == "limit" else "")
                         + f", stop {e.stop:g}, take-profit {e.take_profit if e.take_profit is not None else 'none'}")
        if b.review:
            lines.append("- Review levels: " + ", ".join(f"{x['side']} {x['price']:g}" for x in b.review))
        return lines

    def validator_context(self, inp, last_price):
        b = self.books[inp.symbol]
        position = pending = None
        if b.position is not None:
            stop, tp = (b.levels.stop, b.levels.take_profit) if b.levels else (b.position.stop, b.position.take_profit)
            position = pl.Position(b.position.entry_price, stop, tp)
        if b.entry is not None and b.entry.kind == "limit":
            pending = pl.PendingEntry(b.entry.price, b.entry.stop, b.entry.take_profit)
        return inputs.validator_context(inp, last_price, position, pending)

    # ---------- candles and fills ----------
    def process_candles(self, now):
        """Run new closed 1m candles; returns symbols whose review level was reached just now."""
        triggered = []
        for symbol in self.symbols:
            book = self.books[symbol]
            since = book.last_candle + pd.Timedelta(minutes=1)
            try:
                candles = self.minutes(symbol, since, now)
            except Exception as e:
                self.log(f"[V11 CANDLE ERROR] {symbol}: {type(e).__name__}: {e}")
                continue
            for c in candles:
                for e in sim.step(book, c, config.FEE_RATE_TAKER, config.SLIPPAGE_BPS):
                    self._handle(symbol, e)
                    if e["kind"] == "review" and now - e["time"] <= TRIGGER_FRESH + pd.Timedelta(minutes=1):
                        triggered.append(symbol)
                self.last_close[symbol] = c.close
            if candles:
                self._save(symbol)
        return list(dict.fromkeys(triggered))

    def _handle(self, symbol, e):
        kind, pid = e["kind"], e.get("plan_id")
        book = self.books[symbol]
        if kind == "entry":
            pos = book.position
            if pos is not None and pos.entry_time == e["time"]:
                pos.trade_id = uuid.uuid4().hex[:8]
                plan = self._plan(pid)
                self.paper.trade_opened(self.key, TIMEFRAME, symbol, SimpleNamespace(
                    trade_id=pos.trade_id, side="LONG", entry_time=_naive(pos.entry_time), entry_price=pos.entry_price,
                    qty=pos.qty, signal_rsi=None, stop=pos.stop), grade=plan.get("grade"))
                self.paper.conn.commit()
                if plan.get("p_tp") is not None and pos.take_profit is not None:
                    with self.llm.conn:
                        self.llm.conn.execute("INSERT OR IGNORE INTO llm_forecasts VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                                              (self.name, pid, symbol, pd.Timestamp(e["time"]).isoformat(),
                                               pos.entry_price, pos.stop, pos.take_profit, plan["p_tp"]))
        if kind in REASONS:
            trade_id = e.get("trade_id") or uuid.uuid4().hex[:8]
            if not e.get("trade_id"):     # opened and closed in one candle: record both legs now
                self.paper.trade_opened(self.key, TIMEFRAME, symbol, SimpleNamespace(
                    trade_id=trade_id, side="LONG", entry_time=_naive(e["entry_time"]), entry_price=e["entry_price"],
                    qty=e["qty"], signal_rsi=None, stop=None), grade=self._plan(pid).get("grade"))
            self.paper.trade_closed(SimpleNamespace(
                trade_id=trade_id, exit_time=_naive(e["time"]), exit_price=e["price"], fees=e["fees"], pnl=e["pnl"],
                return_pct=e["return_pct"], reason=REASONS[kind]))
            self.paper.conn.commit()
        self._event(symbol, kind, e["time"], pid, **{k: v for k, v in e.items() if k not in ("kind", "time", "plan_id")})

    # ---------- calls ----------
    def _blocked(self, now):
        return self.blocked_day == now.floor("D")

    def _after_call(self, out, now, what):
        if out.call_kind == p.USAGE_LIMIT:
            self.blocked_day = now.floor("D")
            self.notify(f"⛔ v11 {self.name}: usage limit reached in the {what}; no more calls until the next UTC day.")
        elif out.call_kind == p.VERSION_MISMATCH:
            self.blocked_day = now.floor("D")
            self.notify(f"⛔ v11 {self.name}: Codex is not version {config.CODEX_VERSION}; calls stopped for today.")

    def _apply(self, out, now):
        for symbol, verdict in out.verdicts.items():
            if not verdict.ok:
                continue
            plan = dict(verdict.plan, plan_id=out.plan_ids[symbol])
            if plan["action"] == "enter" and self.inst.exit_only:
                self._event(symbol, "enter_skipped", now, plan["plan_id"], reason="exit only")
                plan = dict(plan, action="hold")
            stored_at = self.clock()
            for e in sim.apply(self.books[symbol], plan, stored_at):
                self._event(symbol, e["kind"], e["time"], plan["plan_id"])
            self._save(symbol)

    def _inputs(self, snap, last_prices=None):
        out = []
        for symbol in self.symbols:
            inp = snap.symbols.get(symbol)
            if inp is None:
                continue
            last = (last_prices or {}).get(symbol, inp.summary["last"])
            inp.state, inp.history, inp.memo = self.state(symbol, last), self.history(symbol), self.memo(symbol)
            out.append(inp)
        return out

    def scheduled(self, now, snap=None):
        if self._blocked(now):
            return
        snap = snap or self.snapshot(self.symbols, now, log=self.log)
        ordered = self._inputs(snap)
        contexts = {i.symbol: self.validator_context(i, i.summary["last"]) for i in ordered}
        mode = "group" if self.group > 1 else "single"
        for start in range(0, len(ordered), self.group):
            group = ordered[start:start + self.group]
            out = review.run(self.provider, self.llm, self.name, mode, snap, group, contexts, kind="scheduled",
                             news=self.news, sleep=self.sleep)
            self._apply(out, now)
            self._after_call(out, now, "scheduled review")
            if self._blocked(now):
                break
        self._warn_failures(now)

    def _calls_today(self, symbol, now, kind=None):
        q = "SELECT time FROM llm_calls WHERE instance=? AND retry=0 AND symbols LIKE ? AND time>=?"
        args = [self.name, f'%"{symbol}"%', now.floor("D").isoformat()]
        if kind:
            q, args = q + " AND kind=?", args + [kind]
        return [pd.Timestamp(t) for (t,) in self.llm.conn.execute(q, args).fetchall()]

    def triggered(self, symbol, now):
        if self._blocked(now):
            self._event(symbol, "review_skipped", now, reason="calls stopped for today")
            return
        if len(self._calls_today(symbol, now, "triggered")) >= MAX_TRIGGERED_PER_DAY:
            self._event(symbol, "review_skipped", now, reason="3 triggered reviews today")
            return
        last_call = self.llm.conn.execute(
            "SELECT MAX(time) FROM llm_calls WHERE instance=? AND symbols LIKE ?", (self.name, f'%"{symbol}"%')).fetchone()[0]
        if last_call and now - pd.Timestamp(last_call) < COOLDOWN:
            self._event(symbol, "review_skipped", now, reason="within 60 minutes of the last call")
            return
        snap = self.snapshot([symbol], now, log=self.log)
        last = self.last_close.get(symbol)
        ordered = self._inputs(snap, {symbol: last} if last else None)
        if not ordered:
            return
        contexts = {i.symbol: self.validator_context(i, last or i.summary["last"]) for i in ordered}
        out = review.run(self.provider, self.llm, self.name, "single", snap, ordered, contexts, kind="triggered",
                         news=self.news, sleep=self.sleep)
        self._apply(out, now)
        self._after_call(out, now, "triggered review")

    def _warn_failures(self, now):
        rows = self.llm.conn.execute("SELECT outcome FROM llm_calls WHERE instance=? AND time>=?",
                                     (self.name, now.floor("D").isoformat())).fetchall()
        failed = sum(r[0] != p.OK for r in rows)
        if rows and failed / len(rows) > FAIL_WARN_SHARE:
            self.notify(f"⚠️ v11 {self.name}: {failed} of {len(rows)} calls failed today.")

    # ---------- daily ----------
    def scheduled_done(self, now):
        return self.llm.conn.execute("SELECT 1 FROM llm_calls WHERE instance=? AND kind='scheduled' AND time>=? LIMIT 1",
                                     (self.name, now.floor("D").isoformat())).fetchone() is not None

    def _marked(self, now):
        day = _naive(now.floor("D") - pd.Timedelta(days=1))
        return self.paper.has_equity(self.key, TIMEFRAME, self.symbols[0], day)

    def mark_equity(self, snap):
        """Equity at the close of the day that just ended, valued at its daily close; the time is
        that daily candle's open, as for the other 1d instances."""
        t = _naive(snap.time.floor("D") - pd.Timedelta(days=1))
        for symbol, inp in snap.symbols.items():
            close = inp.summary["last"]
            self.paper.log_equity(self.key, TIMEFRAME, symbol, t, self.books[symbol].value(close), close)
        self.paper.conn.commit()

    def tick(self, now=None):
        """One pass: candles first, so the books are at the close before the daily work."""
        now = now or self.clock()
        triggered = self.process_candles(now)
        marked, done = self._marked(now), self.scheduled_done(now) or self._blocked(now)
        if not (marked and done):
            snap = self.snapshot(self.symbols, now, log=self.log)
            if not marked:
                self.mark_equity(snap)
            if not done:
                self.scheduled(now, snap)
        for symbol in triggered:
            self.triggered(symbol, now)


def run_loop(analyst, check_every_s=10):
    """A pass a few seconds after every minute, by the wall clock."""
    last_minute = None
    while True:
        now = analyst.clock()
        minute = now.floor("min")
        if minute != last_minute and now - minute >= pd.Timedelta(seconds=3):
            last_minute = minute
            try:
                analyst.tick(now)
            except Exception as e:      # one bad pass must not end the instance; the next one retries
                analyst.log(f"[V11 ERROR] {type(e).__name__}: {e}")
        time.sleep(check_every_s)


def main():
    from kripto_bot import instances
    from kripto_bot.app import notify
    ap = argparse.ArgumentParser(description="Run one v11 AI analyst instance (paper).")
    ap.add_argument("--instance", required=True)
    a = ap.parse_args()
    inst = instances.get(a.instance)
    if inst.strategy != STRATEGY:
        raise SystemExit(f"{inst.name} is not a v11 instance")
    lock = store.single_instance(store.DB_FILE, inst.name)  # noqa: F841 (held until exit)
    tg = notify.Telegram(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID)
    analyst = Analyst(inst, notify=lambda text: (print(text), tg.send(text) if tg.enabled else None))
    s = setup(inst)
    print(f"🧠 v11 analyst '{inst.name}': {s['model']} effort {s['effort']}, news {'on' if s['news'] else 'off'}, "
          f"{s['group']} symbol(s) per scheduled call, {len(analyst.symbols)} symbols, "
          f"{inst.equity:g} USDT per symbol")
    run_loop(analyst)


if __name__ == "__main__":
    main()
