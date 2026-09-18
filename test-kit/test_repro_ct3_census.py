"""CP3 reproductions — obligation-completeness defects LIVE at HEAD 091ed83b. RED by design.

CT-301  coordinated dimensions collapse into one demand unit — the census cannot see a
        dropped dimension inside a merged unit (core/agent_runtime/answer_coverage.py,
        demand_units clause/coordination splitting).
CT-302  a subtask's unit binding is minted in the EFFECTIVE text's unit space and matched
        by positional id against the RAW text's canonical set (core/live_data_plan.py
        _interim_unit_ids :612 -> answer_coverage.units_matching_needle :1529): a receipt
        for entity X can discharge a demand unit whose own text never mentions X, and
        unclaimed_unit_ids undercounts (the incident: gold receipt discharged the
        car-comparison unit; 'and the weather there?': unclaimed == () — a fully wrong
        answer certifies as fully covered).
"""
from __future__ import annotations

from core.agent_runtime.answer_coverage import demand_units
from core.live_data_plan import build_live_data_plan

from kit_lib import GOLD_ANSWER, GOLD_QUESTION, context, gold_thread, weather_thread


# ------------------------------------------------------------------ CT-301 dimension collapse

def test_CT301_coordinated_dimensions_are_tracked_separately():
    """A clean comparison ask: each coordinated dimension must be its own demand unit, or a
    partial answer closes the merged unit and the remaining dimensions silently vanish.
    The count is pinned too, so an over-splitting repair cannot pass by fragmentation."""
    text = "compare passat vs golf on production years, total sales, popular regions, engines and prices"
    units = demand_units(text)
    assert len(units) == 5, [(u.unit_id, u.text) for u in units]
    lowered = [u.text.lower() for u in units]
    for dimension in ("production years", "total sales", "popular regions", "engines", "prices"):
        own_unit = any(
            dimension in text_u and not any(
                other in text_u
                for other in ("production years", "total sales", "popular regions", "engines", "prices")
                if other != dimension
            )
            for text_u in lowered
        )
        assert own_unit, (dimension, units)


def test_CT301_the_incidents_runon_shape_tracks_engines_separately():
    """The recorded text's tail merged regions+engines+`and so on` into one unit."""
    units = demand_units("most popuplar regions where sold, engines and so on")
    engines_own = [u for u in units if "engines" in u.text.lower()]
    regions_own = [u for u in units if "regions" in u.text.lower() and "engines" not in u.text.lower()]
    assert engines_own and regions_own, units


# ------------------------------------------------------------------ CT-302 receipt binding

def test_CT302_a_receipt_never_binds_a_unit_whose_text_does_not_carry_the_entity():
    """'and the weather there?' after a gold turn (CT-201 input): the gold subtask binds
    unit id u1 minted from the INHERITED text; the RAW text's u1 is the weather question.
    The receipt then discharges a weather demand with a gold quote and unclaimed is EMPTY
    — the census certifies a fully wrong answer as fully covered. Correct: a binding that
    is not verifiable in the canonical (raw-request) unit set does not bind."""
    session = gold_thread("r302")
    text = "and the weather there?"
    source = context(session, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", text))
    canonical = demand_units(text)
    canonical_by_id = {u.unit_id: u for u in canonical}

    plan = build_live_data_plan(
        text, plan_id="p", attempt_id="a", source_context=source, canonical_units=canonical,
    )
    assert plan is not None
    for task in plan.subtasks:
        for uid in task.unit_ids:
            unit = canonical_by_id.get(uid)
            assert unit is not None, (uid, task.entity)
            assert task.entity.lower() in unit.text.lower(), (
                f"receipt for {task.entity!r} bound to unit {uid} whose text is {unit.text!r}"
            )


def test_CT302_unclaimed_counts_every_unit_the_plan_cannot_verifiably_serve():
    """Same input, conservation side: the canonical weather unit must appear in
    unclaimed_unit_ids (nothing verifiably served it), not be silently claimed."""
    session = gold_thread("r302b")
    text = "and the weather there?"
    source = context(session, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", text))
    canonical = demand_units(text)

    plan = build_live_data_plan(
        text, plan_id="p", attempt_id="a", source_context=source, canonical_units=canonical,
    )
    assert plan is not None
    assert [u.unit_id for u in canonical if u.unit_id in plan.unclaimed_unit_ids] == [
        u.unit_id for u in canonical
    ], (plan.unclaimed_unit_ids, canonical)


def test_CT302_typo_rebind_receipt_also_respects_the_law():
    """Cross-family check on the weather arm: a Title-case mis-bind (CT-204 input) must not
    bind the raw unit either — 'Write About Winter' never mentions a location the plan serves."""
    session = weather_thread("r302c", ["Kaunas"], "Get weather for Kaunas.")
    text = "Write About Winter"
    source = context(session, ("user", "Get weather for Kaunas."), ("assistant", "Kaunas: 18 C."), ("user", text))
    canonical = demand_units(text)
    canonical_by_id = {u.unit_id: u for u in canonical}

    plan = build_live_data_plan(
        text, plan_id="p", attempt_id="a", source_context=source, canonical_units=canonical,
    )
    if plan is None:
        return
    for task in plan.subtasks:
        for uid in task.unit_ids:
            unit = canonical_by_id.get(uid)
            assert unit is not None, (uid, task.entity)
            assert task.entity.lower() in unit.text.lower(), (
                f"receipt for {task.entity!r} bound to unit {uid} whose text is {unit.text!r}"
            )
