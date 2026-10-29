"""A same-turn capability.expand_family seats the whole family, whatever else the words resolved.

Red on b744af81: the expanded family shared its bonus seats round-robin with the families the
words resolved, and under read-only-first ranking with declaration-order tie-breaks the long
tail of the operator family (agenda, note writes, reminders) never seated — measured on the
served rig, E01 after `capability.expand_family {"family":"operator"}` offered only 11 of the
20 operator tools beside the web and token seats.
"""
from __future__ import annotations

import pytest

from core import capability_graph as cg
from core.capability_graph import _ALWAYS_VISIBLE, model_visible_specs
from core.tool_offer_state import (
    begin_turn_navigation,
    end_turn_navigation,
    record_family_expansion,
)

#: Every operator intent the real registry contracts on this runtime (the 20 the E-cases name).
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
    """The live registry bootstrap, not the legacy-only mapping: the operator family's
    notes/agenda/calendar tools reach the graph only through registry projection."""
    cg.reset()
    yield
    cg.reset()


def _expanded_offer(user_text: str, *families: str) -> list[str]:
    context: dict = {"turn_id": "expansion-test", "runtime_session_id": "s-expand"}
    scope = begin_turn_navigation(context)
    try:
        record_family_expansion(context, "operator")
        specs = model_visible_specs(
            family_hints=tuple(families),
            user_text=user_text,
            source_context=context,
        )
        return [str(spec.get("intent") or "") for spec in specs]
    finally:
        end_turn_navigation(context, scope)


def test_the_expanded_family_seats_in_full_beside_other_families():
    intents = _expanded_offer("summarize my notes from this week and research it online", "web")
    seated = set(intents)
    missing = OPERATOR_EXPECTED - seated
    assert not missing, f"after expand, round lacks {sorted(missing)}; offered {sorted(seated)}"
    # The words' own family still fills what is left, and reserved seats stay.
    assert "respond.direct" in seated and "capability.expand_family" in seated
    assert any(intent.startswith("web.") for intent in seated)


def test_the_expanded_offer_stays_under_the_hard_cap():
    intents = _expanded_offer("summarize my notes from this week and research it online", "web", "token")
    assert len(intents) <= 24
    assert len(set(intents)) == len(intents)


def test_without_expansion_round_zero_is_unchanged_and_bounded():
    specs = model_visible_specs(
        family_hints=("web",),
        user_text="summarize my notes from this week and research it online",
        source_context={"turn_id": "plain", "runtime_session_id": "s-plain"},
    )
    intents = [str(spec.get("intent") or "") for spec in specs]
    assert len(intents) <= 8
    assert not set(intents) >= OPERATOR_EXPECTED, "round 0 must not seat the whole operator family"


def test_a_second_expanded_family_still_leaves_the_cap_intact():
    context: dict = {"turn_id": "two-expansions", "runtime_session_id": "s-two"}
    scope = begin_turn_navigation(context)
    try:
        record_family_expansion(context, "operator")
        record_family_expansion(context, "web")
        specs = model_visible_specs(
            family_hints=("token",), user_text="what launched on solana today", source_context=context)
        intents = [str(spec.get("intent") or "") for spec in specs]
        assert len(intents) <= 24
        assert set(intents) >= OPERATOR_EXPECTED, "the first expanded family still seats in full"
    finally:
        end_turn_navigation(context, scope)
