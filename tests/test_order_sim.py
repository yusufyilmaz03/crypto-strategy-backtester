import json

import pandas as pd
import pytest

from kripto_bot.trading import order_sim as sim

FEE, BPS = 0.001, 5.0
SLIP = BPS / 10_000
DAY = pd.Timestamp("2026-11-10", tz="UTC")
STORED = DAY + pd.Timedelta(seconds=5)          # the plan is stored 5 s after the daily close


def _c(minute, o, h, l, c=None):
    return sim.Candle(DAY + pd.Timedelta(minutes=minute), o, h, l, o if c is None else c)


def _plan(action="enter", **kw):
    base = {"action": action, "entry": {"type": "limit", "price": 3340, "expires_candles": 1},
            "stop": 3200, "take_profit": 3495, "review_if": [], "plan_id": 7}
    base.update(kw)
    return base


def _book(plan=None, cash=1000.0):
    b = sim.Book("ETH/USDT", cash)
    if plan is not None:
        sim.apply(b, plan, STORED)
    return b


def _run(book, *candles):
    out = []
    for c in candles:
        out += sim.step(book, c, FEE, BPS)
    return out


def _kinds(events):
    return [e["kind"] for e in events]


# ---------- entries ----------

def test_nothing_fills_on_a_candle_that_opened_before_the_plan():
    b = _book(_plan())
    assert _run(b, _c(0, 3350, 3355, 3300)) == []          # opened 00:00:00, plan stored 00:00:05
    assert _kinds(_run(b, _c(1, 3350, 3355, 3300))) == ["entry"]


def test_a_limit_fills_only_when_the_low_goes_below_it():
    b = _book(_plan())
    assert _run(b, _c(1, 3360, 3365, 3340)) == []          # touched, not filled
    events = _run(b, _c(2, 3360, 3365, 3339.99))
    assert events[0]["price"] == 3340
    p = b.position
    assert p.qty == pytest.approx(1000 / (3340 * (1 + FEE)))
    assert p.entry_fee == pytest.approx(p.qty * 3340 * FEE)
    assert b.entry is None


def test_a_limit_gapped_through_fills_at_the_open_never_above_the_limit():
    b = _book(_plan())
    assert _run(b, _c(1, 3300, 3310, 3290))[0]["price"] == pytest.approx(3300 * (1 + SLIP))
    b = _book(_plan())
    assert _run(b, _c(1, 3339.5, 3345, 3330))[0]["price"] == 3340      # slippage capped at the limit


def test_a_market_entry_fills_at_the_next_open_with_slippage():
    b = _book(_plan(entry={"type": "market", "price": None, "expires_candles": 1}, stop=3300))
    assert _run(b, _c(1, 3412.5, 3415, 3410))[0]["price"] == pytest.approx(3412.5 * (1 + SLIP))


def test_a_limit_expires_at_its_last_daily_close():
    b = _book(_plan())
    assert b.entry.expires_at == DAY + pd.Timedelta(days=1)
    assert _run(b, _c(1439, 3400, 3401, 3399)) == []                   # 23:59, still live
    events = _run(b, _c(1440, 3300, 3310, 3290))                       # opens at the close
    assert _kinds(events) == ["expired"] and b.position is None


def test_three_closes_means_the_third_midnight():
    b = _book(_plan(entry={"type": "limit", "price": 3340, "expires_candles": 3}))
    assert b.entry.expires_at == DAY + pd.Timedelta(days=3)


def test_cancel_removes_the_pending_entry():
    b = _book(_plan())
    assert _kinds(sim.apply(b, _plan("cancel"), STORED + pd.Timedelta(hours=1))) == ["cancelled"]
    assert b.entry is None and _run(b, _c(90, 3300, 3310, 3290)) == []


def test_a_new_enter_replaces_the_pending_entry():
    b = _book(_plan())
    sim.apply(b, _plan(entry={"type": "limit", "price": 3300, "expires_candles": 2}, stop=3150),
              STORED + pd.Timedelta(hours=1))
    assert b.entry.price == 3300 and b.entry.stop == 3150


# ---------- exits ----------

def _open(cash=1000.0, **kw):
    """A position entered at the limit on the 00:01 candle."""
    b = _book(_plan(**kw), cash)
    _run(b, _c(1, 3360, 3365, 3339))
    return b


def test_stop_fills_at_the_stop_or_a_lower_open_with_slippage():
    b = _open()
    e = _run(b, _c(5, 3250, 3255, 3200))[0]
    assert e["kind"] == "stop" and e["price"] == pytest.approx(3200 * (1 - SLIP))
    b = _open()
    assert _run(b, _c(5, 3150, 3160, 3140))[0]["price"] == pytest.approx(3150 * (1 - SLIP))


def test_take_profit_needs_the_high_above_it():
    b = _open()
    assert _run(b, _c(5, 3480, 3495, 3470)) == []
    e = _run(b, _c(6, 3480, 3495.01, 3470))[0]
    assert e["kind"] == "take_profit" and e["price"] == 3495


def test_take_profit_gapped_through_fills_at_the_open_never_below_it():
    b = _open()
    assert _run(b, _c(5, 3520, 3530, 3510))[0]["price"] == pytest.approx(3520 * (1 - SLIP))
    b = _open()
    assert _run(b, _c(5, 3495.5, 3500, 3490))[0]["price"] == 3495


def test_stop_comes_first_when_both_are_hit():
    b = _open()
    assert _kinds(_run(b, _c(5, 3300, 3500, 3190))) == ["stop"]


def test_in_the_fill_candle_the_stop_counts_but_not_the_take_profit():
    b = _book(_plan())
    assert _kinds(_run(b, _c(1, 3350, 3355, 3190))) == ["entry", "stop"]
    b = _book(_plan())
    assert _kinds(_run(b, _c(1, 3350, 3500, 3330))) == ["entry"]
    assert b.position is not None


def test_pnl_and_cash_after_a_round_trip():
    b = _open()
    p = b.position
    e = _run(b, _c(5, 3480, 3496, 3470))[0]
    exit_fee = p.qty * 3495 * FEE
    assert e["pnl"] == pytest.approx(p.qty * (3495 - 3340) - p.entry_fee - exit_fee)
    assert b.cash == pytest.approx(1000 + e["pnl"]) and b.position is None
    assert e["return_pct"] == pytest.approx(100 * e["pnl"] / 1000)


def test_exit_sells_at_the_next_open():
    b = _open()
    sim.apply(b, _plan("exit"), DAY + pd.Timedelta(minutes=10, seconds=30))
    assert _run(b, _c(10, 3400, 3450, 3300)) == []              # opened before the exit plan
    e = _run(b, _c(11, 3390, 3395, 3385))[0]
    assert e["kind"] == "exit" and e["price"] == pytest.approx(3390 * (1 - SLIP))


def test_new_levels_act_from_the_next_candle():
    b = _open()
    sim.apply(b, _plan("adjust", stop=3300, take_profit=3600), DAY + pd.Timedelta(minutes=10, seconds=30))
    assert _run(b, _c(10, 3320, 3325, 3290)) == []              # the old stop 3200 still holds here
    assert b.position.stop == 3200
    e = _run(b, _c(11, 3320, 3325, 3290))[0]
    assert e["kind"] == "stop" and e["price"] == pytest.approx(3300 * (1 - SLIP))


def test_adjusting_a_pending_entry_re_places_it():
    b = _book(_plan())
    sim.apply(b, _plan("adjust", stop=3250, take_profit=3500), DAY + pd.Timedelta(minutes=10, seconds=30))
    assert _run(b, _c(10, 3350, 3355, 3300)) == []
    assert _run(b, _c(11, 3350, 3355, 3300))[0]["kind"] == "entry"
    assert b.position.stop == 3250


def test_marked_value():
    b = _open()
    p = b.position
    assert b.value(3400) == pytest.approx(1000 - p.entry_fee + p.qty * (3400 - 3340))
    assert _book().value(3400) == 1000


# ---------- review levels, replay, storage ----------

def test_a_review_level_triggers_once_and_not_before_its_plan():
    b = _book(_plan("hold", review_if=[{"side": "below", "price": 3300}, {"side": "above", "price": 3505}]))
    assert _run(b, _c(0, 3350, 3360, 3290)) == []               # opened before the plan
    events = _run(b, _c(1, 3350, 3360, 3299))
    assert events == [{"kind": "review", "time": DAY + pd.Timedelta(minutes=1), "side": "below", "price": 3300}]
    assert _run(b, _c(2, 3290, 3295, 3280)) == []
    assert _kinds(_run(b, _c(3, 3500, 3505, 3495))) == ["review"]


def test_a_new_plan_replaces_the_review_levels():
    b = _book(_plan("hold", review_if=[{"side": "below", "price": 3300}]))
    sim.apply(b, _plan("hold", review_if=[]), STORED + pd.Timedelta(hours=1))
    assert _run(b, _c(120, 3290, 3295, 3280)) == []


def test_replayed_candles_are_not_processed_twice():
    b = _open()
    candle = _c(5, 3250, 3255, 3200)
    assert len(_run(b, candle)) == 1
    assert _run(b, candle) == [] and _run(b, _c(4, 3250, 3255, 3100)) == []


def test_a_book_survives_a_round_trip_through_json():
    b = _open()
    sim.apply(b, _plan("adjust", stop=3250, take_profit=3600,
                       review_if=[{"side": "above", "price": 3600}]), STORED + pd.Timedelta(minutes=30))
    copy = sim.from_dict(json.loads(json.dumps(sim.to_dict(b))))
    assert copy == b
    pending = _book(_plan())
    assert sim.from_dict(json.loads(json.dumps(sim.to_dict(pending)))) == pending
