"""CP3 — obligation completeness: attribution side (green at HEAD 091ed83b).

Pins the census mechanism of the recorded incident as executable facts:
- the incident's exact census (1 of 4 claimed, gold receipt on the car-comparison unit) is
  reproduced with the repair's guard severed — the unit-id collision does the discharging;
- legitimate rebinding satisfies the receipt-binding law (control for repro_ct3).
"""
from __future__ import annotations

from kit_lib import GOLD_ANSWER, GOLD_QUESTION, VW_QUESTION, context, gold_thread

import core.live_data_continuation as live_data_continuation
from core.agent_runtime.answer_coverage import demand_units, units_matching_needle
from core.live_data_plan import build_live_data_plan


def test_severed_guard_reproduces_the_incident_census_exactly():
    """The recorded '3 of 4 demand obligations unanswered' (seq 50), in-process.

    The gold subtask binds unit id 'u1' minted in the INHERITED text's unit space
    ('what is gold price now?' -> u1); the ledger's canonical set mints u1 from the RAW
    text ('give me comparision review Vw passat vs vw golf'). The positional id spaces
    collide, so the gold receipt discharges the car-comparison unit: 1 of 4 claimed,
    3 unanswered — the exact incident arithmetic.
    """
    session = gold_thread("census")
    source = context(session, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", VW_QUESTION))
    canonical = demand_units(VW_QUESTION)
    assert [u.unit_id for u in canonical] == ["u1", "u2", "u3", "u4"]

    with __import__("pytest").MonkeyPatch.context() as patch:
        patch.setattr(
            live_data_continuation,
            "_content_the_obligation_does_not_hold",
            lambda text, *, slots: [],
        )
        plan = build_live_data_plan(
            VW_QUESTION, plan_id="p", attempt_id="a",
            source_context=source, canonical_units=canonical,
        )
    assert plan is not None
    bound = {uid for task in plan.subtasks for uid in task.unit_ids}
    assert bound == {"u1"}, bound
    assert plan.unclaimed_unit_ids == ("u2", "u3", "u4")
    # The collision itself: two different texts, same positional id.
    assert units_matching_needle("what is gold price now?", "gold") == ("u1",)
    assert units_matching_needle(VW_QUESTION, "gold") == ()


def test_legitimate_rebinding_satisfies_the_receipt_binding_law():
    """Control: a legitimate rebind binds a canonical unit whose own text carries the entity."""
    session = gold_thread("lawful")
    text = "what about silver?"
    source = context(session, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", text))
    canonical = demand_units(text)
    plan = build_live_data_plan(
        text, plan_id="p", attempt_id="a", source_context=source, canonical_units=canonical,
    )
    assert plan is not None
    canonical_by_id = {u.unit_id: u for u in canonical}
    for task in plan.subtasks:
        for uid in task.unit_ids:
            assert uid in canonical_by_id, (uid, task.entity)
            assert task.entity.lower() in canonical_by_id[uid].text.lower(), (uid, task.entity)
