# kripto_bot/trading/order_sim.py
"""The v11 paper order simulator on closed 1m candles (docs/v11-ai-analyst.md, "Execution").

One Book per symbol holds its cash, the open position, a pending entry, a pending market exit
and the review levels. apply() turns an accepted plan into orders; step() runs one closed 1m
candle through them and returns what happened. The rules:

- A plan acts only on candles that open after it was stored (`after`), new stop and
  take-profit levels included: nothing can fill before the plan existed.
- Market entry or exit: the open of that first candle, with slippage.
- Limit buy: fills at the limit when a candle's low goes below it (touching is not enough);
  a candle that opens below it fills at the open, with slippage but never above the limit.
- Stop: when the low reaches it, at min(open, stop) with slippage.
- Take-profit: when the high goes above it, at the take-profit; a candle that opens above it
  fills at the open, with slippage but never below the take-profit.
- Stop and take-profit in one candle: the stop first. In the candle an entry fills, the stop
  is checked too (the low may have come after the fill) but the take-profit is not.
- A limit entry expires at its last daily close (00:00 UTC).
- A review level triggers once, when a candle that opened after its plan reaches it.
- Adjusting a pending entry re-places it: it acts from the next candle again.
Fees are charged on both legs and slippage always works against us, as in core/engine.py.
Candles already processed are skipped, so replaying missed candles after a restart is safe.
"""
from dataclasses import asdict, dataclass, field

import pandas as pd


@dataclass
class Candle:
    time: pd.Timestamp               # open time, UTC
    open: float
    high: float
    low: float
    close: float


@dataclass
class Entry:
    kind: str                        # "market" | "limit"
    price: float | None              # limit price
    stop: float
    take_profit: float | None
    after: pd.Timestamp              # acts on candles opening after this
    expires_at: pd.Timestamp | None  # limit only: the daily close it is cancelled at
    plan_id: int | None = None


@dataclass
class Position:
    qty: float
    entry_price: float
    entry_fee: float
    entry_time: pd.Timestamp
    equity_before: float
    stop: float
    take_profit: float | None
    plan_id: int | None = None


@dataclass
class Levels:
    """New stop and take-profit for the position, waiting for the next candle."""
    stop: float
    take_profit: float | None
    after: pd.Timestamp


@dataclass
class Book:
    symbol: str
    cash: float
    position: Position | None = None
    entry: Entry | None = None
    levels: Levels | None = None
    exit_after: pd.Timestamp | None = None
    review: list = field(default_factory=list)       # [{"side": "below"|"above", "price": x}]
    review_after: pd.Timestamp | None = None
    last_candle: pd.Timestamp | None = None

    def value(self, price):
        """Marked value: cash, or the position at `price` net of its entry fee."""
        p = self.position
        if p is None:
            return self.cash
        return p.equity_before - p.entry_fee + p.qty * (price - p.entry_price)


def expiry(after, candles):
    """The daily close `candles` closes after `after`: the next midnight is the first."""
    return pd.Timestamp(after).floor("D") + pd.Timedelta(days=candles)


def apply(book, plan, stored_at):
    """Turn an accepted plan into orders. Returns events (a cancelled entry)."""
    stored_at = pd.Timestamp(stored_at)
    events = []
    action = plan["action"]
    if action == "enter":
        e = plan["entry"]
        limit = e["type"] == "limit"
        book.entry = Entry(e["type"], e["price"] if limit else None, plan["stop"], plan.get("take_profit"),
                           stored_at, expiry(stored_at, e["expires_candles"]) if limit else None,
                           plan.get("plan_id"))
    elif action == "adjust":
        if book.position is not None:
            book.levels = Levels(plan["stop"], plan.get("take_profit"), stored_at)
        elif book.entry is not None:        # re-placed: the new levels must not meet an older candle
            book.entry.stop, book.entry.take_profit = plan["stop"], plan.get("take_profit")
            book.entry.after = stored_at
    elif action == "cancel" and book.entry is not None:
        book.entry = None
        events.append({"kind": "cancelled", "time": stored_at})
    elif action == "exit" and book.position is not None:
        book.exit_after = stored_at
    book.review = [dict(level) for level in plan.get("review_if") or []]
    book.review_after = stored_at
    return events


def _close(book, time, price, reason, fee_rate):
    p = book.position
    exit_fee = p.qty * price * fee_rate
    pnl = p.qty * (price - p.entry_price) - p.entry_fee - exit_fee
    book.cash = p.equity_before + pnl
    book.position, book.levels, book.exit_after = None, None, None
    return {"kind": reason, "time": time, "price": price, "qty": p.qty, "entry_time": p.entry_time,
            "entry_price": p.entry_price, "fees": p.entry_fee + exit_fee, "pnl": pnl,
            "return_pct": 100 * pnl / p.equity_before, "plan_id": p.plan_id}


def _stop_hit(book, c, slip, fee_rate):
    p = book.position
    if c.low <= p.stop:
        return _close(book, c.time, min(c.open, p.stop) * (1 - slip), "stop", fee_rate)
    return None


def step(book, c, fee_rate, slippage_bps):
    """Run one closed 1m candle through the book; returns events, oldest first."""
    if book.last_candle is not None and c.time <= book.last_candle:
        return []
    slip = slippage_bps / 10_000
    events = []

    if book.levels is not None and c.time > book.levels.after:
        book.position.stop, book.position.take_profit = book.levels.stop, book.levels.take_profit
        book.levels = None

    e = book.entry
    if e is not None and e.expires_at is not None and c.time >= e.expires_at:
        book.entry = None
        events.append({"kind": "expired", "time": e.expires_at, "price": e.price, "plan_id": e.plan_id})

    p = book.position
    if p is not None and book.exit_after is not None and c.time > book.exit_after:
        events.append(_close(book, c.time, c.open * (1 - slip), "exit", fee_rate))
    elif p is not None:
        hit = _stop_hit(book, c, slip, fee_rate)
        if hit is None and p.take_profit is not None and c.high > p.take_profit:
            price = max(c.open * (1 - slip), p.take_profit) if c.open > p.take_profit else p.take_profit
            hit = _close(book, c.time, price, "take_profit", fee_rate)
        if hit:
            events.append(hit)
    elif book.entry is not None and c.time > book.entry.after:
        e = book.entry
        fill = None
        if e.kind == "market":
            fill = c.open * (1 + slip)
        elif c.open < e.price:
            fill = min(c.open * (1 + slip), e.price)
        elif c.low < e.price:
            fill = e.price
        if fill is not None:
            qty = book.cash / (fill * (1 + fee_rate))
            book.position = Position(qty, fill, qty * fill * fee_rate, c.time, book.cash, e.stop,
                                     e.take_profit, e.plan_id)
            book.entry = None
            events.append({"kind": "entry", "time": c.time, "price": fill, "qty": qty, "plan_id": e.plan_id})
            hit = _stop_hit(book, c, slip, fee_rate)
            if hit:
                events.append(hit)

    armed = book.review_after is None or c.time > book.review_after
    for level in list(book.review) if armed else []:
        if level["side"] == "below" and c.low <= level["price"] or level["side"] == "above" and c.high >= level["price"]:
            book.review.remove(level)
            events.append({"kind": "review", "time": c.time, **level})

    book.last_candle = c.time
    return events


# ---------- persistence (the analyst stores books as JSON) ----------

def _times_to_text(d):
    return {k: (v.isoformat() if isinstance(v, pd.Timestamp) else _times_to_text(v) if isinstance(v, dict) else v)
            for k, v in d.items()}


def to_dict(book):
    return _times_to_text(asdict(book))


TIME_FIELDS = {"after", "expires_at", "entry_time", "exit_after", "review_after", "last_candle"}


def _text_to_times(d):
    return {k: (pd.Timestamp(v) if k in TIME_FIELDS and v is not None else v) for k, v in d.items()}


def from_dict(d):
    d = _text_to_times(d)
    for key, cls in (("position", Position), ("entry", Entry), ("levels", Levels)):
        if d.get(key) is not None:
            d[key] = cls(**_text_to_times(d[key]))
    return Book(**d)
