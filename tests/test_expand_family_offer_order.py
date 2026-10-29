"""Job 6d/6e: the operator expand keeps its calendar tool, and every tool keeps a legal path.

The 6c overflow order put every wording-seated tool into the droppable tail, so inside that
tail the stable sort dropped ``operator.propose_calendar_event`` (declared after the reminder
mutates) and the E bar fell to 0/3. That overflow only happens with the two
``operator.command.*`` reads seated (the served rig projects them; a bare in-process world
does not, and there nothing overflows at the cap), so this file seats them first, exactly as
the command registry does on boot. Rule now: the pre-lane family tools seat ahead of the
reminder mutates Job 6 added, the two reminder tools are exactly what steps down at today's
cap, and one seat below the cap the calendar's entry mutate still holds (Job 6e).

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
#: The model-offerable command reads the command registry projects on boot. They have no
#: path but the expansion, so they always seat, and they are what makes the family overflow.
COMMAND_READS = {"operator.command.status.show", "operator.command.capabilities.list"}

#: The harness E cases (harness/sets/visible.json), owner-local expand turns.
E_CASES = (
    ("E01", "summarize my notes from this week"),
    ("E02", "plan my day around my agenda and reminders"),
    ("E03", "help me tidy my notes and check my disk space"),
)


def _reset_registry():
    """A fresh graph with the operator.command.* reads projected, as the served daemon has."""
    from core.command_registry.model_tools import project_model_tools
    from core.command_registry.registry import registry

    cg.reset()
    registry()
    project_model_tools()
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
    """After "expand operator" the offer keeps every pre-lane operator tool and both command
    reads; the tools that step down for the cap are exactly the two reminder tools."""
    restore = _reset_registry()
    try:
        for case_id, text in E_CASES:
            intents = _offer_after_expand(text)
            seated_operator = {i for i in intents if i.startswith("operator.")}
            assert seated_operator >= COMMAND_READS, (
                f"{case_id}: the command reads are not seated, so the overflow this test pins "
                f"is not reproduced: {sorted(seated_operator)}"
            )
            missing = PRE_LANE_OPERATOR - seated_operator
            assert not missing, f"{case_id} lost pre-lane operator tools: {sorted(missing)}"
            assert "capability.expand_family" in intents
            assert len(intents) == cg._HARD_MAX_CANDIDATES, (case_id, len(intents))
            stepped = (PRE_LANE_OPERATOR | LANE_ADDED) - seated_operator
            assert stepped == LANE_ADDED, f"{case_id}: stepped down {sorted(stepped)}"
    finally:
        restore()


def test_one_seat_below_the_cap_the_calendar_entry_tool_still_seats(monkeypatch):
    """One more seat of overflow (the cap one below today's): the third tool to step down is
    operator.save_note, never propose_calendar_event, and the calendar tools stay together."""
    restore = _reset_registry()
    monkeypatch.setattr(cg, "_HARD_MAX_CANDIDATES", cg._HARD_MAX_CANDIDATES - 1)
    try:
        for case_id, text in E_CASES:
            intents = _offer_after_expand(text)
            seated_operator = {i for i in intents if i.startswith("operator.")}
            assert seated_operator >= COMMAND_READS, (case_id, sorted(seated_operator))
            assert len(intents) == cg._HARD_MAX_CANDIDATES, (case_id, len(intents))
            assert {
                "operator.propose_calendar_event",
                "operator.update_calendar_event",
                "operator.schedule_calendar_event",
            } <= seated_operator, f"{case_id}: {sorted(seated_operator)}"
            stepped = (PRE_LANE_OPERATOR | LANE_ADDED) - seated_operator
            assert stepped == LANE_ADDED | {"operator.save_note"}, f"{case_id}: stepped down {sorted(stepped)}"
    finally:
        restore()


def test_the_hand_kept_step_down_sets_name_path_covered_operator_mutates():
    """The two hand-kept rank sets may only name operator mutates a wording seat can seat
    again; a read, a renamed tool or an expansion-only tool there would be a silent drift."""
    from core.tool_registry import registry_map

    cg.ensure_registry_bootstrap()
    contracts = registry_map()
    for intent in cg._LANE_ADDED_FAMILY_MUTATES | cg._CALENDAR_ENTRY_MUTATES:
        assert intent.startswith("operator."), intent
        assert intent in contracts, f"{intent} is no longer a registered tool"
        assert str(getattr(contracts[intent], "side_effect_class", "")) != "read_only", intent
        assert intent in cg._WORDING_SEATED_INTENTS, f"{intent} has no wording seat to re-seat it"
    assert frozenset(LANE_ADDED) == cg._LANE_ADDED_FAMILY_MUTATES


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
