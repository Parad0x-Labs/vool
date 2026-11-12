"""R7 (round 2): the expansion drain (the full-family seating after capability.expand_family)
is owner-local only.

Red on af2303f1 (round-1 head): a remote turn that expanded operator drained all 20 operator
tools into its offer, move_path, cleanup_temp_files and schedule_calendar_event among them.
On b744af81 the drain did not exist at all, so the remote rows pin base behaviour (must-stay)
and the owner-local contrast row is red there.
"""
from __future__ import annotations

import pytest

from core import capability_graph as cg
from core.capability_graph import model_visible_specs
from core.tool_offer_state import (
    begin_turn_navigation,
    end_turn_navigation,
    record_family_expansion,
)

#: The write-side tools only a full drain seats: the round-robin ranked them last on base.
DRAIN_ONLY = {"operator.move_path", "operator.cleanup_temp_files", "operator.schedule_calendar_event"}
#: Every operator intent the real registry contracts on this runtime (20).
OPERATOR_EXPECTED = {
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


@pytest.fixture(autouse=True)
def _real_registry_world():
    cg.reset()
    yield
    cg.reset()


def _offer_after_expand(owner_local: bool) -> set[str]:
    context: dict = {
        "_owner_local": owner_local,
        "runtime_session_id": "s-r7" if owner_local else "s-r7-remote",
        "turn_id": "t-r7" if owner_local else "t-r7-remote",
    }
    scope = begin_turn_navigation(context)
    try:
        record_family_expansion(context, "operator")
        specs = model_visible_specs(
            family_hints=("web",),
            user_text="summarize my notes from this week and research it online",
            source_context=context,
        )
        return {str(spec.get("intent") or "") for spec in specs}
    finally:
        end_turn_navigation(context, scope)


def test_a_remote_expand_does_not_drain_the_family():
    intents = _offer_after_expand(owner_local=False)
    seated_operator = {i for i in intents if i.startswith("operator.")}
    assert not DRAIN_ONLY & seated_operator, f"remote expand seated {sorted(DRAIN_ONLY & seated_operator)}"
    # The drain, not a family hint, is what reaches the full catalogue.
    assert len(seated_operator) < len(OPERATOR_EXPECTED), sorted(seated_operator)
    # The words' own family still fills: the turn loses nothing it had before the expand.
    assert any(i.startswith("web.") for i in intents)


def test_an_owner_local_expand_still_drains_the_family_in_full():
    intents = _offer_after_expand(owner_local=True)
    assert intents >= OPERATOR_EXPECTED, sorted(OPERATOR_EXPECTED - intents)


def test_the_remote_expand_keeps_the_turn_bounded():
    intents = _offer_after_expand(owner_local=False)
    assert len(intents) <= 24, sorted(intents)
