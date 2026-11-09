import pytest

from kripto_bot.llm import plan as pl

LAST, ATR = 3412.50, 118.40      # ETH in the Codex trial (docs/research/013)


def _ctx(**kw):
    return pl.Context("ETH/USDT", LAST, ATR, 0.01, **kw)


def _plan(**kw):
    base = {"symbol": "ETH/USDT", "action": "hold", "entry": None, "stop": None, "take_profit": None,
            "p_tp": None, "grade": None, "review_if": [], "reasons": ["r"], "news_ids": [], "memo": ""}
    base.update(kw)
    return base


def _enter(**kw):
    """The enter plan gpt-6-astra wrote in the trial: limit 3340, stop 1.18 ATR, TP 1.31 ATR."""
    base = dict(action="enter", entry={"type": "limit", "price": 3340, "expires_candles": 2},
                stop=3200, take_profit=3495, p_tp=0.54, grade="normal",
                review_if=[{"side": "below", "price": 3280}, {"side": "above", "price": 3505}])
    base.update(kw)
    return _plan(**base)


def _rejected(plan, ctx=None, contains=""):
    v = pl.validate(plan, ctx or _ctx())
    assert not v.ok and v.plan is None
    assert any(contains in r for r in v.reasons), v.reasons
    return v


def test_the_schema_requires_every_field_and_allows_no_others():
    s = pl.plan_schema()
    assert set(s["required"]) == set(s["properties"])
    assert s["additionalProperties"] is False
    g = pl.group_schema()
    assert g["required"] == ["plans"] and g["properties"]["plans"]["items"] == s


def test_the_trial_plans_are_valid():
    assert pl.validate(_enter(), _ctx()).ok
    hold = _plan(review_if=[{"side": "below", "price": 3322}, {"side": "above", "price": 3502}])
    assert pl.validate(hold, _ctx()).ok


def test_an_accepted_plan_is_returned_unchanged_where_it_applies():
    v = pl.validate(_enter(), _ctx())
    assert v.plan == _enter() and v.reasons == []


# ---------- enter ----------

def test_market_entry_uses_the_last_price_for_the_stop_and_take_profit():
    plan = _enter(entry={"type": "market", "price": None, "expires_candles": 1}, stop=3300, take_profit=3600)
    assert pl.validate(plan, _ctx()).ok
    _rejected(_enter(entry={"type": "market", "price": None, "expires_candles": 1}, stop=3380),
              contains="ATR below the entry")


def test_limit_must_be_at_or_below_the_last_price_and_within_3_atr():
    _rejected(_enter(entry={"type": "limit", "price": 3420, "expires_candles": 1}), contains="above the last price")
    deepest = round(LAST - 3 * ATR, 2)                        # 3057.30
    ok = _enter(entry={"type": "limit", "price": deepest, "expires_candles": 1}, stop=2950,
                take_profit=3200, review_if=[])
    assert pl.validate(ok, _ctx()).ok
    _rejected(_enter(entry={"type": "limit", "price": deepest - 1, "expires_candles": 1}, stop=2950,
                     take_profit=3200, review_if=[]), contains="more than 3.0 ATR")


@pytest.mark.parametrize("expires", [0, 4, None, True])
def test_limit_expiry_is_one_to_three_closes(expires):
    _rejected(_enter(entry={"type": "limit", "price": 3340, "expires_candles": expires}), contains="expires_candles")


def test_enter_needs_an_entry():
    _rejected(_enter(entry=None), contains="needs an entry")


@pytest.mark.parametrize("stop,why", [
    (None, "stop is required"),
    (3340, "must be below the entry"),
    (3300, "0.34 ATR below the entry"),     # 40 below the 3340 entry, under half an ATR
    (2860, "4.05 ATR below the entry"),     # 480 below, over 4 ATR
])
def test_enter_stop_rules(stop, why):
    _rejected(_enter(stop=stop), contains=why)


def test_take_profit_at_least_one_atr_above_the_entry():
    _rejected(_enter(take_profit=3440), contains="at least 1.0 ATR")


def test_without_take_profit_p_tp_is_not_needed_and_is_cleared():
    v = pl.validate(_enter(take_profit=None, p_tp=0.7), _ctx())
    assert v.ok and v.plan["p_tp"] is None


@pytest.mark.parametrize("p_tp", [None, 0, 1.0, 0.995, "0.5", True])
def test_p_tp_is_required_with_a_take_profit_and_between_bounds(p_tp):
    _rejected(_enter(p_tp=p_tp), contains="p_tp")


def test_enter_needs_a_grade():
    _rejected(_enter(grade=None), contains="grade")
    _rejected(_enter(grade="huge"), contains="grade")


def test_no_adding_to_a_position():
    ctx = _ctx(position=pl.Position(3300, 3150, 3600))
    _rejected(_enter(), ctx, contains="no adding")


def test_a_new_enter_replaces_a_pending_entry():
    ctx = _ctx(pending=pl.PendingEntry(3350, 3220, 3500))
    assert pl.validate(_enter(), ctx).ok


# ---------- prices and common fields ----------

def test_prices_finer_than_the_price_step_are_rejected():
    _rejected(_enter(stop=3200.005), contains="price step")


@pytest.mark.parametrize("price", [0, -5, "3300", None])
def test_prices_must_be_positive_numbers(price):
    _rejected(_enter(entry={"type": "limit", "price": price, "expires_candles": 1}), contains="positive number")


def test_review_levels():
    _rejected(_plan(review_if=[{"side": "below", "price": 3300}] * 3), contains="at most 2")
    _rejected(_plan(review_if=[{"side": "below", "price": 3500}]), contains="wrong side")
    _rejected(_plan(review_if=[{"side": "above", "price": 3450}]), contains="less than 0.5 ATR")
    _rejected(_plan(review_if=[{"side": "sideways", "price": 3600}]), contains="below or above")


def test_memo_is_text_of_at_most_300_characters():
    assert pl.validate(_plan(memo="x" * 300), _ctx()).ok
    _rejected(_plan(memo="x" * 301), contains="memo")
    _rejected(_plan(memo=None), contains="memo")


def test_the_plan_must_be_for_the_symbol_asked():
    _rejected(_plan(symbol="BTC/USDT"), contains="is not ETH/USDT")


def test_every_broken_rule_is_reported():
    v = _rejected(_enter(stop=3400, grade=None, memo="x" * 400), contains="grade")
    assert len(v.reasons) == 3


@pytest.mark.parametrize("plan,why", [("hold", "not an object"), (_plan(action="buy"), "unknown action")])
def test_not_a_plan(plan, why):
    _rejected(plan, contains=why)


# ---------- adjust, cancel, exit ----------

POSITION = pl.Position(entry_price=3300, stop=3150, take_profit=3600)


def test_adjust_needs_something_to_adjust():
    _rejected(_plan(action="adjust", stop=3200), contains="needs a position or a pending entry")


def test_a_position_stop_can_only_be_raised():
    ctx = _ctx(position=POSITION)
    assert pl.validate(_plan(action="adjust", stop=3250, take_profit=3600), ctx).ok
    _rejected(_plan(action="adjust", stop=3100, take_profit=3600), ctx, contains="can only be raised")


def test_a_raised_stop_keeps_half_an_atr_from_the_last_price():
    ctx = _ctx(position=POSITION)
    _rejected(_plan(action="adjust", stop=3380, take_profit=3600), ctx, contains="ATR below the last price")


def test_an_unchanged_stop_is_fine_when_the_price_came_close():
    ctx = pl.Context("ETH/USDT", 3170.0, ATR, 0.01, position=POSITION)     # 20 above the stop
    assert pl.validate(_plan(action="adjust", stop=3150, take_profit=3500), ctx).ok


def test_a_position_take_profit_must_stay_above_the_last_price():
    ctx = _ctx(position=pl.Position(3000, 2900, None))
    _rejected(_plan(action="adjust", stop=2900, take_profit=3400), ctx, contains="above the last price")


def test_adjusting_a_position_needs_no_p_tp():
    v = pl.validate(_plan(action="adjust", stop=3250, take_profit=3700, p_tp=0.4), _ctx(position=POSITION))
    assert v.ok and v.plan["p_tp"] is None


def test_adjusting_a_pending_entrys_levels_needs_a_new_p_tp():
    ctx = _ctx(pending=pl.PendingEntry(3340, 3200, 3495))
    _rejected(_plan(action="adjust", stop=3210, take_profit=3495), ctx, contains="p_tp")
    v = pl.validate(_plan(action="adjust", stop=3210, take_profit=3495, p_tp=0.5), ctx)
    assert v.ok and v.plan["p_tp"] == 0.5
    v = pl.validate(_plan(action="adjust", stop=3200, take_profit=3495, p_tp=0.5,
                          review_if=[{"side": "above", "price": 3600}]), ctx)
    assert v.ok and v.plan["p_tp"] is None       # only the review level changed


def test_cancel_and_exit_need_something_to_cancel_or_exit():
    _rejected(_plan(action="cancel"), contains="pending entry")
    _rejected(_plan(action="exit"), contains="needs a position")
    assert pl.validate(_plan(action="exit"), _ctx(position=POSITION)).ok
    assert pl.validate(_plan(action="cancel"), _ctx(pending=pl.PendingEntry(3340, 3200))).ok
