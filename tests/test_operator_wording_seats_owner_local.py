"""The words that ask for a note, agenda, reminder, Apple Notes or calendar tool seat it on
round 0 — and only on the machine owner's own local turns.

Red on b744af81: `resolve_demand_signals` had no rule for any of this vocabulary, the turn was
classed plain chat (`strategy: tool_gap`), and the pinned model was handed no tool list at all
— it could not even ask for the tool (TASK §0 cause 1).
"""
from __future__ import annotations

import pytest

from core import capability_graph as cg
from core.capability_graph import model_visible_specs
from core.execution_requirements import requirements_for
from core.tool_demand_signals import owner_local_operator_intents

ASKS = {    "save this as a note: buy stamps on Friday": "operator.save_note",
    "show my agenda for today": "operator.show_agenda",
    "what's on my calendar tomorrow?": "operator.show_agenda",
    "what are my reminders": "operator.list_reminders",
    "list the reminders I set in this chat": "operator.list_reminders",
    "find my notes about the venue": "operator.find_notes",
    'open my note called "Venue plan"': "operator.show_note",
    "list my Apple Notes": "operator.apple_note_list",
    'read the Apple note "Weekly sync" from my Work folder': "operator.apple_note_read",
    "am I free on Thursday at 3pm?": "operator.check_availability",
    "put a dentist appointment on my calendar next Tuesday at 10am": "operator.propose_calendar_event",
    # Everyday wordings of the same asks (hidden-set-shaped, not the visible sentences).
    "make a note of the wifi password": "operator.save_note",
    "what have I got on tomorrow": "operator.show_agenda",
    "what am I doing on Thursday": "operator.show_agenda",
    "is Friday afternoon free": "operator.check_availability",
    "where are my notes on the loft plans": "operator.find_notes",
    "book a dentist appointment next Tuesday at 9": "operator.propose_calendar_event",
    "add a haircut to my calendar": "operator.propose_calendar_event",
    "set up a meeting with the accountant": "operator.propose_calendar_event",
    "summarize my notes from this week": "operator.find_notes",
    "help me tidy my notes and check my disk space": "operator.find_notes",
    "plan my day around my agenda and reminders": "operator.show_agenda",
}

QUIET = (
    "write a short thank-you note for my neighbour",
    "take note of how long the bread takes to rise and tell me if that is normal",
    "my agenda for the meeting is to keep it short, any tips?",
    "remind me why the sky is blue",
    "what are the notes of a C major chord?",
    "summarize the history of the Gregorian calendar",
    "how do reminders work on an iPhone?",
    "what does 'save as' mean in Word?",
    "explain the French revolution in a few notes for my essay",
    "is it worth keeping a diary?",
    # Ordinary prose beyond the visible set: reports, statements, how-tos, examples.
    "she opened and read the note out loud to us",
    "I find notes apps distracting in general",
    "the show notes were longer than the episode",
    "I read the note you sent me yesterday",
    "my agenda is packed this week, honestly",
    "my reminders app keeps crashing",
    "how do I show my agenda in Google Calendar",
    "my note to self is to slow down",
    "create a meeting agenda for the offsite",
    "book a meeting room for the standup",
    "the meeting notes say the launch slipped",
    "tell me about upcoming events in Lisbon this weekend",
    "the dentist appointment I had was fine",
    "I booked a table for two yesterday",
    "we keep a note of every visitor in the book",
)

NEW_SEATS = frozenset({
    "operator.save_note", "operator.show_agenda", "operator.list_reminders",
    "operator.find_notes", "operator.show_note", "operator.apple_note_list",
    "operator.apple_note_read", "operator.propose_calendar_event",
    "operator.check_availability",
})

OWNER_LOCAL = {"_owner_local": True, "runtime_session_id": "s-seat", "turn_id": "t-seat"}
REMOTE = {"_owner_local": False, "runtime_session_id": "s-remote", "turn_id": "t-remote"}


@pytest.fixture(autouse=True)
def _real_registry_world():
    cg.reset()
    yield
    cg.reset()


@pytest.mark.parametrize(("text", "intent"), sorted(ASKS.items()))
def test_every_ask_seats_its_intent(text, intent):
    assert intent in owner_local_operator_intents(text), owner_local_operator_intents(text)


@pytest.mark.parametrize("text", sorted(QUIET))
def test_ordinary_prose_seats_nothing(text):
    assert owner_local_operator_intents(text) == ()


@pytest.mark.parametrize(("text", "intent"), sorted(ASKS.items()))
def test_the_offer_seats_the_asked_intent_on_round_zero(text, intent):
    specs = model_visible_specs(user_text=text, source_context=dict(OWNER_LOCAL))
    intents = {str(spec.get("intent") or "") for spec in specs}
    assert intent in intents, f"{text}: offered {sorted(intents)}"
    assert "respond.direct" in intents and "capability.expand_family" in intents


@pytest.mark.parametrize("text", sorted(QUIET))
def test_the_offer_seats_nothing_new_for_ordinary_prose(text):
    owner_specs = model_visible_specs(user_text=text, source_context=dict(OWNER_LOCAL))
    intents = {str(spec.get("intent") or "") for spec in owner_specs}
    assert not intents & NEW_SEATS


@pytest.mark.parametrize(("text", "intent"), sorted(ASKS.items()))
def test_a_remote_turn_seats_none_of_it(text, intent):
    # The recognizer is pure text; the trust gate lives at the seams. Both seams must agree:
    # nothing NEW seats on a remote turn (whatever lanes the words already opened on their own
    # — research, evidence promises — stay exactly as they were).
    specs = model_visible_specs(user_text=text, source_context=dict(REMOTE))
    intents = {str(spec.get("intent") or "") for spec in specs}
    # Only the NEW seats are absent: the operator.inspect_* wording rules and the navigator
    # representatives predate this change and stay exactly as they were on a remote turn.
    assert intent not in intents
    assert not intents & NEW_SEATS, sorted(intents)
    requirements = requirements_for(text, task_class="chat_conversation", source_context=dict(REMOTE))
    assert "operator" not in requirements.allowed_toolsets
    assert "operator_action_request" not in requirements.reason_codes


@pytest.mark.parametrize(("text", "intent"), sorted(ASKS.items()))
def test_the_lane_releases_for_the_owner(text, intent):
    requirements = requirements_for(
        text, task_class="chat_conversation", source_context=dict(OWNER_LOCAL))
    assert requirements.tools_required
    assert "operator" in requirements.allowed_toolsets


def test_a_web_prohibition_does_not_block_the_local_operator_arm():
    requirements = requirements_for(
        "show my agenda for today and do not search the web",
        task_class="chat_conversation",
        source_context=dict(OWNER_LOCAL),
    )
    assert requirements.tools_required
    assert "operator" in requirements.allowed_toolsets


def test_an_all_tools_prohibition_still_wins():
    requirements = requirements_for(
        "show my agenda for today, but do not use any tools",
        task_class="chat_conversation",
        source_context=dict(OWNER_LOCAL),
    )
    assert not requirements.tools_required
