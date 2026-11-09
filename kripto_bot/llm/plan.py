# kripto_bot/llm/plan.py
"""The v11 plan: its JSON schema and the validator (docs/v11-ai-analyst.md, "Guardrails").

The schema only fixes the shape; the model is held to it by Codex's `--output-schema`. The
validator checks the rules against the symbol's state at the call. A plan that breaks any rule
is rejected as a whole and never corrected: the previous plan stays in force. Every reason is
returned so the rejection can be stored.
"""
from dataclasses import dataclass

ACTIONS = ("hold", "enter", "adjust", "cancel", "exit")
GRADES = ("weak", "normal", "strong", "very strong")

STOP_MIN_ATR, STOP_MAX_ATR = 0.5, 4.0
LIMIT_MAX_ATR = 3.0
TAKE_PROFIT_MIN_ATR = 1.0
REVIEW_MIN_ATR = 0.5
MAX_EXPIRY_CANDLES = 3
MAX_REVIEW_LEVELS = 2
P_TP_MIN, P_TP_MAX = 0.01, 0.99
MEMO_MAX_CHARS = 300

_PRICE = {"type": ["number", "null"]}


def plan_schema():
    """One plan. Every key is required (strict structured output); unused fields are null."""
    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["symbol", "action", "entry", "stop", "take_profit", "p_tp", "grade",
                     "review_if", "reasons", "news_ids", "memo"],
        "properties": {
            "symbol": {"type": "string"},
            "action": {"type": "string", "enum": list(ACTIONS)},
            "entry": {"anyOf": [
                {"type": "null"},
                {"type": "object", "additionalProperties": False,
                 "required": ["type", "price", "expires_candles"],
                 "properties": {"type": {"type": "string", "enum": ["market", "limit"]},
                                "price": _PRICE,
                                "expires_candles": {"type": "integer"}}},
            ]},
            "stop": _PRICE,
            "take_profit": _PRICE,
            "p_tp": {"type": ["number", "null"]},
            "grade": {"type": ["string", "null"], "enum": [*GRADES, None]},
            "review_if": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": ["side", "price"],
                "properties": {"side": {"type": "string", "enum": ["below", "above"]},
                               "price": {"type": "number"}}}},
            "reasons": {"type": "array", "items": {"type": "string"}},
            "news_ids": {"type": "array", "items": {"type": "integer"}},
            "memo": {"type": "string"},
        },
    }


def group_schema():
    """Several symbols in one call: one plan per symbol."""
    return {"type": "object", "additionalProperties": False, "required": ["plans"],
            "properties": {"plans": {"type": "array", "items": plan_schema()}}}


@dataclass
class Position:
    entry_price: float
    stop: float
    take_profit: float | None = None


@dataclass
class PendingEntry:
    price: float                   # limit price
    stop: float
    take_profit: float | None = None


@dataclass
class Context:
    """What the validator knows about the symbol when the plan is made."""
    symbol: str
    last_price: float
    atr: float                     # daily ATR(14) in price units
    tick_size: float               # the symbol's price step
    position: Position | None = None
    pending: PendingEntry | None = None


@dataclass
class Verdict:
    ok: bool
    reasons: list                  # why the plan was rejected; empty when ok
    plan: dict | None = None       # the accepted plan, p_tp cleared where it does not apply


def _num(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def _on_tick(price, tick):
    steps = price / tick
    return abs(steps - round(steps)) < 1e-6


def _check_price(name, value, ctx, errors):
    if not _num(value) or value <= 0:
        errors.append(f"{name} must be a positive number")
        return False
    if not _on_tick(value, ctx.tick_size):
        errors.append(f"{name} {value} is finer than the price step {ctx.tick_size}")
        return False
    return True


def _check_stop(stop, ref, ref_name, ctx, current_stop, errors):
    if stop is None:
        errors.append("stop is required")
        return
    if current_stop is not None and stop == current_stop:
        return      # an unchanged stop is fine even if the price has come closer to it
    if not _check_price("stop", stop, ctx, errors):
        return
    if stop >= ref:
        errors.append(f"stop {stop} must be below the {ref_name} {ref}")
        return
    dist = (ref - stop) / ctx.atr
    if not STOP_MIN_ATR <= dist <= STOP_MAX_ATR:
        errors.append(f"stop is {dist:.2f} ATR below the {ref_name}, allowed {STOP_MIN_ATR}-{STOP_MAX_ATR}")
    if current_stop is not None and stop < current_stop:
        errors.append(f"stop {stop} is below the current stop {current_stop}; a stop can only be raised")


def _check_take_profit(tp, ref, ref_name, ctx, errors):
    if tp is None:
        return
    if not _check_price("take_profit", tp, ctx, errors):
        return
    if (tp - ref) / ctx.atr < TAKE_PROFIT_MIN_ATR:
        errors.append(f"take_profit must be at least {TAKE_PROFIT_MIN_ATR} ATR above the {ref_name} {ref}")
    if ctx.position is not None and tp <= ctx.last_price:
        errors.append(f"take_profit {tp} must be above the last price {ctx.last_price}")


def _check_p_tp(p_tp, errors):
    if not _num(p_tp) or not P_TP_MIN <= p_tp <= P_TP_MAX:
        errors.append(f"p_tp must be between {P_TP_MIN} and {P_TP_MAX}")


def _check_entry(plan, ctx, errors):
    """The entry's reference price for the stop and take-profit, or None if invalid."""
    entry = plan.get("entry")
    if not isinstance(entry, dict):
        errors.append("enter needs an entry")
        return None
    kind = entry.get("type")
    if kind == "market":
        return ctx.last_price
    if kind != "limit":
        errors.append("entry type must be market or limit")
        return None
    price = entry.get("price")
    expires = entry.get("expires_candles")
    if not isinstance(expires, int) or isinstance(expires, bool) or not 1 <= expires <= MAX_EXPIRY_CANDLES:
        errors.append(f"expires_candles must be 1-{MAX_EXPIRY_CANDLES}")
    if not _check_price("entry price", price, ctx, errors):
        return None
    if price > ctx.last_price:
        errors.append(f"limit {price} is above the last price {ctx.last_price}")
    elif (ctx.last_price - price) / ctx.atr > LIMIT_MAX_ATR:
        errors.append(f"limit is more than {LIMIT_MAX_ATR} ATR below the last price")
    return price


def _check_common(plan, ctx, errors):
    if plan.get("symbol") != ctx.symbol:
        errors.append(f"symbol {plan.get('symbol')!r} is not {ctx.symbol}")
    levels = plan.get("review_if")
    if not isinstance(levels, list) or len(levels) > MAX_REVIEW_LEVELS:
        errors.append(f"review_if must hold at most {MAX_REVIEW_LEVELS} levels")
        levels = []
    for level in levels:
        side, price = (level or {}).get("side"), (level or {}).get("price")
        if side not in ("below", "above"):
            errors.append(f"review level side must be below or above, not {side!r}")
            continue
        if not _check_price("review level", price, ctx, errors):
            continue
        if side == "below" and price >= ctx.last_price or side == "above" and price <= ctx.last_price:
            errors.append(f"review level {side} {price} is on the wrong side of the last price")
        elif abs(price - ctx.last_price) / ctx.atr < REVIEW_MIN_ATR:
            errors.append(f"review level {price} is less than {REVIEW_MIN_ATR} ATR from the last price")
    memo = plan.get("memo")
    if not isinstance(memo, str) or len(memo) > MEMO_MAX_CHARS:
        errors.append(f"memo must be text of at most {MEMO_MAX_CHARS} characters")


def validate(plan, ctx):
    """Check one plan against the rules; see the module docstring."""
    if not isinstance(plan, dict):
        return Verdict(False, ["the plan is not an object"])
    errors = []
    action = plan.get("action")
    if action not in ACTIONS:
        return Verdict(False, [f"unknown action {action!r}"])
    _check_common(plan, ctx, errors)
    keep_p_tp = False

    if action == "enter":
        if ctx.position is not None:
            errors.append("enter while a position is open (no adding to a position)")
        ref = _check_entry(plan, ctx, errors)
        if ref is not None:
            _check_stop(plan.get("stop"), ref, "entry", ctx, None, errors)
            _check_take_profit(plan.get("take_profit"), ref, "entry", ctx, errors)
        if plan.get("grade") not in GRADES:
            errors.append("enter needs a grade")
        if plan.get("take_profit") is not None:
            _check_p_tp(plan.get("p_tp"), errors)
            keep_p_tp = True

    elif action == "adjust":
        if ctx.position is not None:
            _check_stop(plan.get("stop"), ctx.last_price, "last price", ctx, ctx.position.stop, errors)
            _check_take_profit(plan.get("take_profit"), ctx.position.entry_price, "entry", ctx, errors)
        elif ctx.pending is not None:
            pend = ctx.pending
            _check_stop(plan.get("stop"), pend.price, "entry", ctx, pend.stop, errors)
            _check_take_profit(plan.get("take_profit"), pend.price, "entry", ctx, errors)
            changed = plan.get("stop") != pend.stop or plan.get("take_profit") != pend.take_profit
            if changed and plan.get("take_profit") is not None:
                _check_p_tp(plan.get("p_tp"), errors)
                keep_p_tp = True
        else:
            errors.append("adjust needs a position or a pending entry")

    elif action == "cancel" and ctx.pending is None:
        errors.append("cancel needs a pending entry")
    elif action == "exit" and ctx.position is None:
        errors.append("exit needs a position")

    if errors:
        return Verdict(False, errors)
    accepted = dict(plan)
    if not keep_p_tp:
        accepted["p_tp"] = None
    return Verdict(True, [], accepted)
