"""R5 (round 2): a matched skill's `allowed-tools` narrows the RANKED fill only.

The keep set of the narrowing survives (a) the seats the owner's own words asked for by name
and (b) every tool of a family the model expanded the same turn. Measured on the round-1 head
with only notes-digest matched: "show my agenda for today and summarize my notes" lost
show_agenda, and E01 after capability.expand_family {"family":"operator"} kept 5 of the 20
operator tools it had just been given.

Red on af2303f1 (round-1 head) and on b744af81 (no wording seats / no drain there either):
the assertions name exactly the seats those heads cut.
"""
from __future__ import annotations

import pytest

from core import capability_graph as cg
from core import tool_offer_assembly
from core.tool_offer_assembly import assemble_tool_offer
from core.tool_offer_state import (
    begin_turn_navigation,
    end_turn_navigation,
    record_family_expansion,
)

#: notes-digest's allowed-tools shape: the note-reading family, no agenda, no reminders.
NOTES_DIGEST_TOOLS = (
    "operator.apple_note_list",
    "operator.apple_note_read",
    "operator.save_note",
    "operator.show_note",
    "operator.find_notes",
)

OWNER_LOCAL = {"_owner_local": True, "runtime_session_id": "s-r5", "turn_id": "t-r5"}


@pytest.fixture(autouse=True)
def _real_registry_world():
    cg.reset()
    yield
    cg.reset()


@pytest.fixture()
def notes_digest_matched(monkeypatch):
    """One matched tool skill, through the same seam a real install takes."""
    monkeypatch.setattr(
        tool_offer_assembly,
        "matched_skill_tool_seats",
        lambda text, source_context=None: NOTES_DIGEST_TOOLS,
    )
    yield


def _offer(user_text: str, *, expand_operator: bool = False) -> set[str]:
    context = dict(OWNER_LOCAL)
    scope = begin_turn_navigation(context) if expand_operator else None
    try:
        if expand_operator:
            record_family_expansion(context, "operator")
        offer = assemble_tool_offer(user_text=user_text, source_context=context)
        return {str(spec.get("intent") or "") for spec in offer.specs}
    finally:
        if scope is not None:
            end_turn_navigation(context, scope)


def test_the_wording_seat_survives_the_skill_narrowing(notes_digest_matched):
    intents = _offer("show my agenda for today and summarize my notes")
    # The words asked for the agenda BY NAME: a matched note skill cannot evict it.
    assert "operator.show_agenda" in intents, sorted(intents)
    # The narrowing still applies to the ranked fill: reminders were not asked, not allowed.
    assert "operator.list_reminders" not in intents, sorted(intents)
    # The matched skill's own tools are all still there.
    for intent in NOTES_DIGEST_TOOLS:
        assert intent in intents, f"{intent} missing: {sorted(intents)}"


def test_every_expanded_family_tool_survives_the_skill_narrowing(notes_digest_matched):
    intents = _offer("summarize my notes from this week", expand_operator=True)
    # The model expanded operator THIS turn; the matched skill cannot cut the family it
    # just asked to see (the round-1 cut kept only the 5 allowed tools of the 20).
    for intent in ("operator.move_path", "operator.cleanup_temp_files",
                   "operator.schedule_calendar_event", "operator.show_agenda"):
        assert intent in intents, f"{intent} missing: {sorted(intents)}"
    for intent in NOTES_DIGEST_TOOLS:
        assert intent in intents, f"{intent} missing: {sorted(intents)}"
    assert len(intents) <= 24, sorted(intents)
