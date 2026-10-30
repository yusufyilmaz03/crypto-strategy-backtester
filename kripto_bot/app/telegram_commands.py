# kripto_bot/app/telegram_commands.py
"""Telegram commands, answered by the supervisor (run_all.py) next to the notifications.

A background thread long-polls getUpdates, so nothing has to be reachable from the
internet. Only the chat in TELEGRAM_CHAT_ID is answered; anything else is ignored.

    /status     every instance: process state, return vs buy-and-hold, open positions
                (⭐ marks the panel's favorites, here and in the instance list)
    /instance   pick an instance, then its positions, signals and trades (buttons)
    /help       this list

Instances with manual close get a Close button per position. It asks for confirmation,
then files the same request as the panel's Close button (app/manual_close.py): the
instance's process sells and the notifier reports the trade. A confirmation pressed
more than CONFIRM_TTL_S after it was asked is refused, and close buttons stop working
when the supervisor restarts, so an old message can't close anything.

Only one program may read a bot's updates at a time: while the supervisor runs,
`telegram-setup` gets a conflict error (and vice versa).
"""
import json
import threading
import time

import pandas as pd

from kripto_bot import config, instances
from kripto_bot.app import manual_close, notify, reporting

POLL_TIMEOUT_S = 50     # long polling: Telegram holds the request until something arrives
MAX_AGE_S = 120         # commands older than this (sent while the supervisor was down) are ignored
CONFIRM_TTL_S = 120
ROWS = 10               # signals and trades per view
MAX_POSITIONS = 40
COMMANDS = [("status", "All instances at a glance"),
            ("instance", "Positions, signals and trades of one instance"),
            ("help", "What this bot can do")]
HELP = ("Crypto Tester Bot\n"
        "/status: all instances at a glance\n"
        "/instance: pick an instance for its positions, signals and trades\n"
        "/instance NAME: open one directly\n"
        "Instances with manual close get a Close button per position.")


def _button(text, data):
    return {"text": text, "callback_data": data}


def _name(inst):
    return f"{inst.name} ⭐" if inst.favorite else inst.name


def _date(t):
    return pd.Timestamp(t).strftime("%Y-%m-%d %H:%M") if t is not None and not pd.isna(t) else "?"


class CommandBot:
    def __init__(self, token, chat_id, call=notify.telegram_call, instances_path=None, paper_db=None,
                 prices=None, clock=time.time, log=print):
        self.token, self.chat_id = token, str(chat_id)
        self.call, self.instances_path, self.paper_db = call, instances_path, paper_db
        self.prices = prices or reporting.current_prices
        self.clock, self.log = clock, log
        self.offset = None
        self.targets = {}           # close button id -> (instance name, symbol); in memory on purpose
        self._ids = {}              # (instance name, symbol) -> close button id
        self._last_error = None
        self._stop = threading.Event()
        self._thread = None

    @property
    def enabled(self):
        return bool(self.token and self.chat_id)

    # ----- thread
    def start(self):
        if self.enabled and self._thread is None:
            self._thread = threading.Thread(target=self._run, daemon=True, name="telegram-commands")
            self._thread.start()

    def stop(self):
        self._stop.set()

    def _run(self):
        try:
            self.call(self.token, "setMyCommands",
                      {"commands": json.dumps([{"command": c, "description": d} for c, d in COMMANDS])})
        except notify.TelegramError as e:
            self.log(f"[commands] setMyCommands failed: {e}")
        while not self._stop.is_set():
            try:
                self.poll_once()
                self._last_error = None
            except Exception as e:      # never stops the supervisor; say it once, then retry quietly
                text = str(e)
                if "Conflict" in text:
                    text += " (another program reads this bot's updates: telegram-setup or a second supervisor?)"
                if text != self._last_error:
                    self.log(f"[commands] {text}")
                self._last_error = text
                self._stop.wait(30)

    def poll_once(self, timeout=POLL_TIMEOUT_S):
        params = {"timeout": timeout, "allowed_updates": json.dumps(["message", "callback_query"])}
        if self.offset is not None:
            params["offset"] = self.offset
        for update in self.call(self.token, "getUpdates", params, timeout=timeout + 15):
            self.offset = update["update_id"] + 1
            try:
                self.handle(update)
            except Exception as e:
                self.log(f"[commands] {type(e).__name__}: {e}")

    # ----- updates
    def _ours(self, message):
        return str(((message or {}).get("chat") or {}).get("id")) == self.chat_id

    def handle(self, update):
        if "callback_query" in update:
            return self._pressed(update["callback_query"])
        msg = update.get("message")
        if not self._ours(msg) or self.clock() - msg.get("date", 0) > MAX_AGE_S:
            return
        word, *args = (msg.get("text") or "").split() or [""]
        command = word[1:].split("@")[0].lower() if word.startswith("/") else ""
        if command == "status":
            self._send(self.status_text())
        elif command == "instance" and args:
            self._send(*self.view("i", args[0]))
        elif command == "instance":
            self._send(*self.view("l", ""))
        else:
            self._send(HELP)

    def _pressed(self, query):
        msg = query.get("message")
        if not self._ours(msg):
            return
        kind, _, arg = (query.get("data") or "").partition("|")
        text, keyboard = self.view(kind, arg, msg)
        self.call(self.token, "answerCallbackQuery", {"callback_query_id": query["id"]})
        try:
            self.call(self.token, "editMessageText",
                      {"chat_id": self.chat_id, "message_id": msg["message_id"], "text": text[:notify.MAX_LEN],
                       "reply_markup": json.dumps({"inline_keyboard": keyboard})})
        except notify.TelegramError as e:
            if "not modified" not in str(e):     # Refresh with nothing new
                raise

    def _send(self, text, keyboard=None):
        params = {"chat_id": self.chat_id, "text": text[:notify.MAX_LEN], "disable_web_page_preview": "true"}
        if keyboard:
            params["reply_markup"] = json.dumps({"inline_keyboard": keyboard})
        self.call(self.token, "sendMessage", params)

    # ----- views: (text, inline keyboard)
    def view(self, kind, arg, msg=None):
        if kind == "l":
            return self._list()
        if kind in ("c", "y"):
            target = self.targets.get(arg)
            if target is None:
                return "This button has expired (the supervisor restarted). Open the instance again.", self._back()
            return self._confirm(arg, *target) if kind == "c" else self._close(arg, msg, *target)
        try:
            inst = instances.get(arg, self.instances_path)
        except instances.InstanceError:
            return f"There is no instance named {arg}.", self._back()
        views = {"i": self._instance, "p": self._positions, "s": self._signals, "t": self._trades}
        return views[kind](inst) if kind in views else self._list()

    def _back(self, inst=None):
        row = [_button("« Instances", "l")]
        if inst is not None:
            row.insert(0, _button("« " + inst.name, f"i|{inst.name}"))
        return [row]

    def _list(self):
        items = instances.load_all(self.instances_path).values()
        if not items:
            return "No instances yet. Create one on the panel.", []
        rows = [[_button(f"{'🟢' if i.enabled else '⚪️'} {_name(i)}", f"i|{i.name}")] for i in items]
        return "Pick an instance:", rows

    def _instance(self, inst):
        s = reporting.summary(inst, self.paper_db)
        state = reporting.service_status(inst.name)
        lines = [f"{_name(inst)} ({notify._setup(inst)})",
                 f"Process: {state['state']}{' (' + state['note'] + ')' if state.get('note') else ''}"]
        if s["return_pct"] is None:
            lines.append("No data yet.")
        else:
            dd = f"max DD {s['max_dd_pct']:.1f}%" if s["max_dd_pct"] is not None else ""
            if s["buy_hold_max_dd_pct"] is not None:
                dd += f" (B&H {s['buy_hold_max_dd_pct']:.1f}%)"
            win = f", win {s['win_rate_pct']:.0f}%" if s["win_rate_pct"] is not None else ""
            lines += [f"{notify._pct(s['return_pct'])} vs B&H {notify._pct(s['buy_hold_pct'])} · {dd}",
                      f"{s['days']} days · {s['open_positions']} open · {s['closed_trades']} closed{win}",
                      f"Realized PnL {s['realized_pnl']:+.2f} USDT"]
        if inst.manual_close:
            lines.append("Manual close: on")
        keyboard = [[_button(f"Positions ({s['open_positions']})", f"p|{inst.name}"),
                     _button("Signals", f"s|{inst.name}"), _button("Trades", f"t|{inst.name}")],
                    [_button("↻ Refresh", f"i|{inst.name}"), _button("« Instances", "l")]]
        return "\n".join(lines), keyboard

    def _positions(self, inst):
        pos = reporting.positions(inst, self.paper_db)
        if pos.empty:
            return f"{inst.name}: no open positions.", self._back(inst)
        prices = self.prices(set(pos["symbol"]))
        lines = [f"{inst.name}: {len(pos)} open position(s)"]
        closable = []
        for _, p in pos.head(MAX_POSITIONS).iterrows():
            now = prices.get(p["symbol"])
            change = f" ({notify._pct((now / p['entry_price'] - 1) * 100)})" if now and p["entry_price"] else ""
            line = (f"{p['symbol']}: entry {notify._num(p['entry_price'])} on {_date(p['entry_time'])}"
                    f" · now {notify._num(now)}{change}")
            if p.get("stop") is not None and not pd.isna(p.get("stop")):
                line += f" · stop {notify._num(p['stop'])}"
            if p.get("close_requested"):
                line += " · close requested"
            elif inst.mode == "paper" and inst.manual_close:
                closable.append(p["symbol"])
            lines.append(line)
        if len(pos) > MAX_POSITIONS:
            lines.append(f"… and {len(pos) - MAX_POSITIONS} more on the panel")
        lines.append("Change: last price vs entry, before fees.")
        buttons = [_button(f"Close {s.split('/')[0]}", f"c|{self._target(inst.name, s)}") for s in closable]
        keyboard = [buttons[k:k + 3] for k in range(0, len(buttons), 3)]
        keyboard.append([_button("↻ Refresh", f"p|{inst.name}")] + self._back(inst)[0])
        return "\n".join(lines), keyboard

    def _target(self, name, symbol):
        key = (name, symbol)
        if key not in self._ids:
            self._ids[key] = str(len(self._ids) + 1)
            self.targets[self._ids[key]] = key
        return self._ids[key]

    def _signals(self, inst):
        df = reporting.signals(inst, ROWS, self.paper_db)
        if df.empty:
            return f"{inst.name}: no signals yet.", self._back(inst)
        if inst.mode == "paper":
            rows = [f"{_date(r.candle_time)} {r.symbol} {r.signal} @ {notify._num(r.close)}: {r.action}"
                    for r in df.itertuples()]
        else:
            rows = [f"{_date(r.time)} {r.level}{' ' + r.symbol if r.symbol else ''}: {r.message}"
                    for r in df.itertuples()]
        title = "last signals" if inst.mode == "paper" else "last events"
        return "\n".join([f"{inst.name}: {title} (newest first)"] + rows), self._back(inst)

    def _trades(self, inst):
        df = reporting.trades(inst, self.paper_db)
        if inst.mode == "paper" and not df.empty:
            df = df[df["exit_time"].notna()].sort_values("exit_time")
        if df.empty:
            return f"{inst.name}: no closed trades yet.", self._back(inst)
        df = df.tail(ROWS).iloc[::-1]
        if inst.mode == "paper":
            rows = [f"{_date(r.exit_time)} {r.symbol} {notify._pct(r.return_pct)} "
                    f"({r.reason or 'signal'}), PnL {r.pnl:+.2f}" for r in df.itertuples()]
        else:
            rows = [f"{_date(r.created_at)} {str(r.side).upper()} {r.symbol} {notify._num(r.filled)} "
                    f"@ {notify._num(r.average)} [{r.status}]" for r in df.itertuples()]
        title = "last closed trades" if inst.mode == "paper" else "last orders"
        return "\n".join([f"{inst.name}: {title} (newest first)"] + rows), self._back(inst)

    def _confirm(self, target_id, name, symbol):
        text = (f"Close {symbol} on {name} at the market now?\n"
                "The instance sells at the last price within about a minute; "
                "the trade is recorded with reason MANUAL.")
        return text, [[_button("Yes, close", f"y|{target_id}"), _button("Cancel", f"p|{name}")]]

    def _close(self, target_id, msg, name, symbol):
        back = [[_button("Positions", f"p|{name}"), _button("« Instances", "l")]]
        asked = (msg or {}).get("edit_date") or (msg or {}).get("date", 0)
        if self.clock() - asked > CONFIRM_TTL_S:
            return f"Too late: {symbol} was not closed. Press Close again.", back
        try:
            manual_close.request(instances.get(name, self.instances_path), symbol, self.paper_db)
        except (manual_close.CloseRefused, instances.InstanceError) as e:
            return f"{symbol} not closed: {e}", back
        return (f"✋ Close of {symbol} requested. {name} sells it within about a minute; "
                "the trade message follows."), back

    # ----- /status
    def status_text(self):
        lines = [f"📊 Status {pd.Timestamp(self.clock(), unit='s', tz='UTC'):%Y-%m-%d %H:%M} UTC"]
        for inst in instances.load_all(self.instances_path).values():
            state = reporting.service_status(inst.name)["state"]
            if not inst.enabled:
                lines.append(f"⚪️ {_name(inst)}: stopped")
                continue
            s = reporting.summary(inst, self.paper_db)
            perf = ("no data yet" if s["return_pct"] is None else
                    f"{notify._pct(s['return_pct'])} vs B&H {notify._pct(s['buy_hold_pct'])}")
            lines.append(f"🟢 {_name(inst)} ({notify._setup(inst)}): {state}\n"
                         f"  {perf} · {s['open_positions']} open")
        if len(lines) == 1:
            lines.append("No instances yet.")
        lines.append(f"Kill switch: {notify.kill_state()}")
        return "\n".join(lines)


def from_config(log=print):
    return CommandBot(config.TELEGRAM_BOT_TOKEN, config.TELEGRAM_CHAT_ID, log=log)
