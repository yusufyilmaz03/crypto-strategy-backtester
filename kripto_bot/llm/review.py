# kripto_bot/llm/review.py
"""One v11 call: build the prompt, call the model (with the retry rule), validate every plan
in the answer, and store all of it. Shared by the analyst and the dry run.

A symbol whose plan is missing from the answer, or whose call failed, gets a rejected
verdict: for the analyst that means `hold`, its previous plan stays in force.
"""
import time as _time
from dataclasses import dataclass

from kripto_bot.llm import plan as pl
from kripto_bot.llm import prompt as pr
from kripto_bot.llm import provider as p


@dataclass
class Outcome:
    call_kind: str           # the last attempt's result kind (provider.OK, USAGE_LIMIT, ...)
    verdicts: dict           # symbol -> plan.Verdict
    call_id: int | None      # the last attempt's id in llm_calls
    plan_ids: dict = None    # symbol -> id of its row in llm_plans (symbols with a plan in the answer)


def run(provider, store, instance, mode, snap, symbols, contexts, kind="scheduled", news=True,
        sleep=_time.sleep):
    """Ask for plans for `symbols` (SymbolInputs, in order) and validate each against
    `contexts` (symbol -> plan.Context)."""
    prompt = pr.build(snap.time, symbols, snap.context, snap.news if news else None, kind)
    attempts = p.call(provider, prompt, pl.group_schema(), sleep=sleep)
    call_id = None
    for a in attempts:
        call_id = store.record_call(instance, snap.time, kind, mode, [s.symbol for s in symbols], prompt, a)
    last = attempts[-1]
    answers = {}
    if last.ok:
        for item in last.output.get("plans") or []:
            if isinstance(item, dict) and item.get("symbol") not in answers:
                answers[item.get("symbol")] = item
    verdicts, plan_ids = {}, {}
    for s in symbols:
        ctx = contexts[s.symbol]
        context = {"last": ctx.last_price, "atr": ctx.atr, "tick": ctx.tick_size}
        if not last.ok:
            verdicts[s.symbol] = pl.Verdict(False, [f"call failed: {last.kind}"])
            continue
        item = answers.get(s.symbol)
        verdict = pl.validate(item, ctx) if item is not None else pl.Verdict(False, ["no plan for this symbol"])
        plan_ids[s.symbol] = store.record_plan(instance, call_id, snap.time, mode, s.symbol, item or {}, verdict,
                                               context)
        verdicts[s.symbol] = verdict
    return Outcome(last.kind, verdicts, call_id, plan_ids)
