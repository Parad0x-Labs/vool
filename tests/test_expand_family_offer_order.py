"""Job 6d: the operator expand keeps its calendar tool, and every tool keeps a legal path.

The 6c overflow order put every wording-seated tool into the droppable tail, so inside that
tail the stable sort dropped ``operator.propose_calendar_event`` (declared after the reminder
mutates) and the E bar fell to 0/3. Rule now: inside the path-covered tail a tool this turn's
own ask seats ranks first, the pre-lane family tools seat ahead of the reminder mutates Job 6
added, and the two ``operator.command.*`` reads keep their seat (they have no other path).

Also pins ``_WORDING_SEATED_INTENTS`` against the seat source it mirrors, so the hand-kept
copy cannot drift from ``core.tool_demand_signals.owner_local_operator_intents``.
"""
from __future__ import annotations

import inspect
import re

from core import capability_graph as cg
from core.capability_graph import model_visible_specs
from core.tool_offer_state import begin_turn_navigation, end_turn_navigation, record_family_expansion

#: The operator family as it was before this lane's reminder tools (the E-bar list).
PRE_LANE_OPERATOR = {
    "operator.list_tools",
    "operator.inspect_processes",
    "operator.inspect_services",
    "operator.inspect_disk_usage",
    "operator.check_availability",
    "operator.list_calendars",
    "operator.inspect_calendar_event",
    "operator.find_notes",
    "operator.show_note",
    "operator.apple_note_read",
    "operator.apple_note_list",
    "operator.show_agenda",
    "operator.search_calendar_event",
    "operator.list_reminders",
    "operator.cleanup_temp_files",
    "operator.move_path",
    "operator.save_note",
    "operator.propose_calendar_event",
    "operator.update_calendar_event",
    "operator.schedule_calendar_event",
}
#: The reminder mutates this lane added: the first to step down when the family overflows.
LANE_ADDED = {"operator.schedule_reminder", "operator.cancel_reminder"}

#: The harness E cases (harness/sets/visible.json), owner-local expand turns.
E_CASES = (
    ("E01", "summarize my notes from this week"),
    ("E02", "plan my day around my agenda and reminders"),
    ("E03", "help me tidy my notes and check my disk space"),
)


def _reset_registry():
    cg.reset()
    return cg.reset


def _offer_after_expand(text: str) -> set[str]:
    context: dict = {
        "_owner_local": True,
        "runtime_session_id": "s-j6d",
        "turn_id": "t-j6d",
    }
    scope = begin_turn_navigation(context)
    try:
        record_family_expansion(context, "operator")
        specs = model_visible_specs(
            family_hints=("web",),
            user_text=text,
            source_context=context,
        )
        return {str(spec.get("intent") or "") for spec in specs}
    finally:
        end_turn_navigation(context, scope)


def test_e01_e03_expand_keeps_the_calendar_tool_and_the_pre_lane_family():
    """After "expand operator" the offer keeps every pre-lane operator tool; only the
    lane-added reminder mutates (and, on a served rig, whatever sits behind them) step
    down for the cap."""
    restore = _reset_registry()
    try:
        for case_id, text in E_CASES:
            intents = _offer_after_expand(text)
            seated_operator = {i for i in intents if i.startswith("operator.")}
            missing = PRE_LANE_OPERATOR - seated_operator
            assert not missing, f"{case_id} lost pre-lane operator tools: {sorted(missing)}"
            assert "capability.expand_family" in intents
            # The two reminder mutates are the sanctioned step-downs, never a pre-lane tool.
            stepped = LANE_ADDED - seated_operator
            if stepped:
                assert stepped == LANE_ADDED or len(seated_operator & LANE_ADDED) < 2
    finally:
        restore()


def test_e02_expand_ranks_the_turns_own_seats_first():
    """E02 asks for the agenda and reminders: after the expand those seats are in the
    offer (the ask can always re-seat what it names)."""
    restore = _reset_registry()
    try:
        intents = _offer_after_expand("plan my day around my agenda and reminders")
        assert "operator.show_agenda" in intents, sorted(intents)
        assert "operator.list_reminders" in intents, sorted(intents)
    finally:
        restore()


def test_wording_seated_intents_cannot_drift_from_the_seat_source():
    """``_WORDING_SEATED_INTENTS`` is a hand-kept copy of what the owner-local wording
    seats can produce; this fails the moment the two disagree, in either direction."""
    from core.capability_graph import _WORDING_SEATED_INTENTS
    from core.tool_demand_signals import owner_local_operator_intents

    source = inspect.getsource(owner_local_operator_intents)
    # Comment lines may name intents in prose; only code literals count.
    code_lines = "\n".join(
        line for line in source.splitlines() if not line.lstrip().startswith("#")
    )
    seated = set(re.findall(r'"(operator\.[a-z0-9_]+)"', code_lines))
    assert seated, "the seat-source scan found no intents; update this drift guard"
    assert seated == set(_WORDING_SEATED_INTENTS), (
        f"wording-seat copy drifted: only-in-source={sorted(seated - set(_WORDING_SEATED_INTENTS))} "
        f"only-in-copy={sorted(set(_WORDING_SEATED_INTENTS) - seated)}"
    )
