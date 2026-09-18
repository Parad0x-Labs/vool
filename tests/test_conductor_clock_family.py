"""Clock family in the conductor — multi-city time asks resolve and serve.

AUD-20260829-001 (watch session 2026-08-29T1150Z): "what time is in rome now
and in paris?" decomposed by the conductor into clauses that resolved to
`machine_observation` (refused: "found nothing to act on") and to weather —
the conductor had no clock family, so two clock clauses died and one city was
answered by the wrong domain.

The repair registers `time_clock` with `outranks_planner_naming=True` and an
`accepts_clause` scope check, so a clause the clock recognizers own binds the
clock family EVEN WHEN the planner names it wrong — scripted here with the
exact wrong name production produced.

Route honesty: single-city and plain-shaped time asks belong to the clock
fast path (covered in tests/test_v2_pack_root_cause_repairs.py); the
conductor route here is for conductor-shaped multi-part turns whose plan
carries clock clauses.

Sabotage contract (CLAUDE.md §6b.4): unregistering the clock family (or
demoting its outranking claim) must turn these tests red, naming the cause.
"""

from __future__ import annotations

import json

from core.conductor.planner import plan_conductor_turn
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan
from tests.conductor_product import compose_product


def _plan_reply(*clauses: dict) -> str:
    return json.dumps(list(clauses))


def _run(prompt: str, reply: str) -> tuple:
    plan = plan_conductor_turn(prompt, ask_model=lambda _s, _p: reply, plan_id="t")
    assert plan is not None, "the conductor declined a turn it must claim"
    outcomes = run_conductor_plan(plan, context=NodeContext(timeout_s=5.0))
    return plan, outcomes, compose_product(plan, outcomes)


def test_time_clauses_misnamed_by_the_planner_bind_the_clock_family():
    # THE reported defect, scripted exactly: the planner proposed
    # machine_observation for both time clauses (the production reply's own
    # family). The clock claim outranks that naming.
    reply = _plan_reply(
        {"request": "what time is in rome now", "operation": "machine_observation", "depends_on": []},
        {"request": "what time is in paris now", "operation": "machine_observation", "depends_on": []},
    )
    plan, outcomes, product = _run(
        "what time is in rome now and in paris?", reply
    )
    clock_ops = [n.operation for n in plan.nodes if n.operation == "time_clock"]
    assert len(clock_ops) == 2, f"expected two time_clock nodes, got {clock_ops}"
    answer = product.text
    assert "Could not be answered" not in answer, answer
    assert "Rome" in answer and "Paris" in answer, answer
    assert "Current time in" in answer, answer


def test_conductor_shaped_city_pair_wording_serves_both_cities():
    # Same shape as the original report, different cities: the wording family
    # must hold beyond the exact reported string (§6b.2).
    reply = _plan_reply(
        {"request": "what time is in berlin now", "operation": "machine_observation", "depends_on": []},
        {"request": "what time is in madrid now", "operation": "machine_observation", "depends_on": []},
    )
    plan, outcomes, product = _run(
        "what time is in berlin now and in madrid now?", reply
    )
    answer = product.text
    assert "Berlin" in answer and "Madrid" in answer, answer
    assert "Could not be answered" not in answer, answer


def test_clock_claim_does_not_steal_sibling_clauses():
    # A clock clause and an arithmetic clause in one turn: each keeps its own
    # family — the clock claim binds clock clauses only.
    reply = _plan_reply(
        {"request": "what time is in rome now", "operation": "machine_observation", "depends_on": []},
        {"request": "12*12", "operation": "calculation", "depends_on": []},
    )
    plan, outcomes, product = _run(
        "what time is in rome now and also 12*12", reply
    )
    answer = product.text
    assert "144" in answer, f"arithmetic clause lost: {answer}"
    assert "Rome" in answer, f"clock clause lost: {answer}"


def test_clock_capability_scope_rejects_non_clock_clauses():
    # The scope check is what keeps the claim from stealing: machine and
    # arithmetic clauses are outside the clock family's diet even when a place
    # name or the word "machine" rides along.
    from core.conductor.operations import _clock_capability_accepts

    class _Clause:
        request_text = ""

    machine_clause = _Clause()
    machine_clause.request_text = "how much free disk space is left on this machine?"
    assert _clock_capability_accepts(machine_clause) is False

    arithmetic_clause = _Clause()
    arithmetic_clause.request_text = "what is 144 divided by 12"
    assert _clock_capability_accepts(arithmetic_clause) is False

    no_place_clock = _Clause()
    no_place_clock.request_text = "what time is it now?"
    assert _clock_capability_accepts(no_place_clock) is False, (
        "no place named: the clock FAMILY does not own it (the no-place fast "
        "path does), so a conductor clause without a resolvable place must not "
        "bind here"
    )
