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

from kit_lib import GOLD_ANSWER, GOLD_QUESTION, context, gold_thread, weather_thread

from core.agent_runtime.answer_coverage import demand_units
from core.live_data_plan import build_live_data_plan

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
    """'and the weather there?' after a gold turn (CT-201 input): the recorded incident had the
    gold subtask bind the raw text's weather unit through a positional id collision and
    unclaimed read EMPTY -- a fully wrong answer certified as fully covered. The inheritance
    that carried the inherited-space ids is gone at TWO independent halves now (the content
    guard AND the own-request half), so this input can no longer mint a gold subtask at all --
    severing the content guard alone does not resurrect it. The binding law is pinned where it
    lives: no needle of the gold obligation can claim a unit of this text."""
    session = gold_thread("r302")
    text = "and the weather there?"
    source = context(session, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", text))
    canonical = demand_units(text)

    plan = build_live_data_plan(
        text, plan_id="p", attempt_id="a", source_context=source, canonical_units=canonical,
    )
    assert plan is None, "a weather followup must not mint an inherited gold obligation"

    import pytest

    import core.live_data_continuation as live_data_continuation

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(
            live_data_continuation,
            "_content_the_obligation_does_not_hold",
            lambda value, *, slots: [],
        )
        severed = build_live_data_plan(
            text, plan_id="p2", attempt_id="a2", source_context=source, canonical_units=canonical,
        )
    assert severed is None, (
        "even with the content guard severed, the own-request half must refuse: no gold receipt "
        "exists that could discharge a weather demand"
    )
    from core.agent_runtime.answer_coverage import units_matching_needle

    assert units_matching_needle(text, "gold") == ()


def test_CT302_unclaimed_counts_every_unit_the_plan_cannot_verifiably_serve():
    """Same input, conservation side: with no plan and no binding, the canonical weather unit
    is served by NOTHING from the live-data lane -- it can never be silently claimed by an
    inherited receipt, so the census counts it honestly wherever coverage is computed."""
    session = gold_thread("r302b")
    text = "and the weather there?"
    source = context(session, ("user", GOLD_QUESTION), ("assistant", GOLD_ANSWER), ("user", text))
    canonical = demand_units(text)

    plan = build_live_data_plan(
        text, plan_id="p", attempt_id="a", source_context=source, canonical_units=canonical,
    )
    assert plan is None
    # Nothing in this turn's own text binds a live-data needle: the unit stays wholly
    # unclaimed by this lane, which is the honest accounting the incident destroyed.
    from core.agent_runtime.answer_coverage import units_matching_needle

    assert units_matching_needle(text, "gold") == ()
    assert [u.unit_id for u in canonical if u.unit_id in units_matching_needle(text, "gold")] == []


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
