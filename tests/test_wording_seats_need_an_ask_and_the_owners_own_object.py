"""R2 (round 2): a wording seat needs ask-shaped words AND the owner's own object.

The round-1 recognizer seated operator tools on ordinary prose ("where is the note C on a
piano?", "do we have any free will?", "make an event poster for Friday at 7pm") because the
seat regexes matched question shapes and imperative verbs with no possession anchor, and its
whole-text statement veto cancelled real asks whose objects contained a declarative clause
("list my reminders that are due today", "save this as a note: the calendar is full on
Monday").

Red on af2303f1 (round-1 head): every BAN row seated an operator tool there, every MISSED row
lost its seat. On b744af81 the recognizer itself does not exist, so the file is red there by
import; the offer/requirements rows that DO run there pin base behaviour (no operator seat,
no lane release) and are must-stay.

Job 2b (AMENDMENT 2, reviewer, 2026-10-28): the Job 2 own-object gate is now too strict, so
everyday asks seat nothing on 15c535fc (a bare "jot this down" save, "which/what ... do I
have" questions, a note the owner titled or called something, reading Apple Notes, "do I
have time free ...", bare "any ... today/tonight" questions, "what's my next meeting?", and
a combined look-over-my-notes-and-calendar report, which gets no tool list at all so the
model cannot expand the operator family). The JOB2B_MISSED and OWN2B_ASKS rows are red on
15c535fc until the loosening lands in core/tool_demand_signals.py; the JOB2B_BAN and
OWN2B_QUIET rows must stay quiet through it.
"""
from __future__ import annotations

import pytest

from core import capability_graph as cg
from core.capability_graph import model_visible_specs
from core.execution_requirements import requirements_for
from core.tool_demand_signals import owner_local_operator_intents

#: The eight prose sentences from the review (AMENDMENT 1, R2): none of them is an ask for the
#: owner's own note/agenda/calendar object, whatever question or verb shape they carry.
REVIEW_BAN = (
    "where is the note C on a piano?",
    "do we have any free will?",
    "am I free to quit my job without notice in Germany?",
    "plan my day trip to Rome",
    "make an event poster for Friday at 7pm",
    "put a meeting summary in a table",
    "where did the space go in my sentence?",
    "what's on the calendar for the Olympics next week?",
)
#: The three real asks the old statement veto cancelled (AMENDMENT 1, R2).
REVIEW_MISSED = {
    "list my reminders that are due today": "operator.list_reminders",
    "find my notes that have the venue address": "operator.find_notes",
    "save this as a note: the calendar is full on Monday": "operator.save_note",
}
#: Everyday wordings of the same asks, written for this file (not the visible sentences).
OWN_ASKS = {
    "what does my week look like": "operator.show_agenda",
    "read out my agenda for tomorrow": "operator.show_agenda",
    "remind me what's on my plate this week": "operator.show_agenda",
    "search my notes for the booking reference": "operator.find_notes",
    "list my notes about the insurance renewal": "operator.find_notes",
    "can you open my note with the gate code": "operator.show_note",
    "what's in my note about the wifi": "operator.show_note",
    "show my Apple Notes with the receipts": "operator.apple_note_list",
    "open the apple note with the gate code": "operator.apple_note_read",
    "when am I free this week": "operator.check_availability",
    "do I have anything on Friday": "operator.check_availability",
    "is tomorrow afternoon free": "operator.check_availability",
    "schedule a call with the notary for Friday": "operator.propose_calendar_event",
    "add the webinar to my calendar": "operator.propose_calendar_event",
    "block an hour on my calendar for deep work": "operator.propose_calendar_event",
}
#: Ordinary prose of my own that mentions the same nouns without asking this runtime to act
#: on the owner's own object. A false seat here is worse than a miss.
OWN_QUIET = (
    "the calendar on the wall is from 2019",
    "I put my keys on the calendar shelf yesterday",
    "my notes app review is due next month",
    "we plan our day trips months in advance",
    "the meeting summary went out this morning",
    "she makes event posters for the local fair",
    "am I free to change my mind about the venue?",
    "do we have any free parking near the stadium?",
    "where did the space go in the garage?",
    "what's on the schedule at the Olympics this weekend?",
    "my calendar looks beautiful in the new update",
    "I already wrote a note about it",
    "our agenda covers three topics",
    "the piano teacher explained where the note C sits",
)

#: The reviewer's Job 2b list (AMENDMENT 2): real asks that seat nothing on 15c535fc because
#: the own-object gate is too strict. A "jot ... down" save, an "I have" question, a titled or
#: called note, Apple Notes, "time free" and bare "any ..." questions, and a combined
#: notes-and-calendar report all name the owner's own object.
JOB2B_MISSED = {
    "jot this down: buy milk": "operator.save_note",
    "jot it down as a note: pick up the dry cleaning": "operator.save_note",
    "which reminders do I have set?": "operator.list_reminders",
    "what appointments do I have this week?": "operator.show_agenda",
    "show the note titled Venue plan": "operator.show_note",
    "what does the note called Venue plan say?": "operator.show_note",
    "read my Apple Notes": "operator.apple_note_read",
    "what's in my Apple Notes?": "operator.apple_note_list",
    "do I have time free on Friday afternoon?": "operator.check_availability",
    "any appointments today?": "operator.show_agenda",
    "what's my next meeting?": "operator.show_agenda",
    "look over my notes and my calendar and tell me what's coming up": "operator.find_notes",
}
#: The reviewer's Job 2b prose list (AMENDMENT 2): each still seats on 15c535fc and must not
#: (free as price or permission, "anything to" of an abstract noun, a feasibility tail, a
#: call-to-action or meeting-tone compound, and material pasted into the message).
JOB2B_BAN = (
    "is it free to schedule a meeting on Zoom?",
    "is Sunday free parking in London?",
    "do I have anything to worry about if my cat sneezes?",
    "book a call with the Prime Minister? is that possible?",
    "add a call to action to my blog post for tomorrow",
    "set a meeting tone that's friendly, tips for tomorrow",
    "summarize my notes on the French revolution I pasted below: the revolution started in 1789 and ended in 1799",
)
#: Everyday wordings of the same Job 2b shapes, written for this file (not the visible
#: sentences).
OWN2B_ASKS = {
    "jot down the wifi password: gulf-22-bravo": "operator.save_note",
    "can you jot this down: call the garage": "operator.save_note",
    "what meetings do I have on Thursday?": "operator.show_agenda",
    "which reminders do I have coming up?": "operator.list_reminders",
    "open the note called Gate codes": "operator.show_note",
    "what's in the note titled Lockers?": "operator.show_note",
    "read my Apple Notes from last week": "operator.apple_note_read",
    "what's in apple notes about the lease?": "operator.apple_note_list",
    "do I have any time free tomorrow morning?": "operator.check_availability",
    "do I have time free after lunch?": "operator.check_availability",
    "any meetings on Monday?": "operator.show_agenda",
    "any events tonight?": "operator.show_agenda",
    "what's my next appointment?": "operator.show_agenda",
    "what is my next call today?": "operator.show_agenda",
    "go through my notes and my calendar and flag anything that clashes": "operator.find_notes",
}
#: Prose of my own probing the loosening's edges without ever being an ask on the owner's own
#: object: past-tense saves, free plus a thing-noun, "anything to" of an abstraction,
#: feasibility tails, call-to-action and meeting-tone compounds, pasted material, world
#: "any" and next-meeting questions, and titled notes or possessions inside reports.
OWN2B_QUIET = (
    "she jotted the recipe down years ago",
    "is Saturday free entry at the museum?",
    "is Monday free shipping on orders over 50?",
    "do I have anything to say about the proposal?",
    "do I have anything to declare?",
    "book a call with the CEO? is that even possible?",
    "schedule a demo? would that work?",
    "add a call to action button to my landing page",
    "set a meeting vibe that stays casual",
    "summarize my notes on Kant I pasted below: the categorical imperative is the star",
    "are there any meetings in London next week?",
    "is there any event parking at the arena?",
    "what's the next meeting of the astronomy society?",
    "the note titled Backlog on the door was for the cleaners",
    "my meeting with HR went fine",
    "which laptop do I have again?",
)

OWNER_LOCAL = {"_owner_local": True, "runtime_session_id": "s-r2", "turn_id": "t-r2"}

NEW_SEATS = frozenset({
    "operator.save_note", "operator.show_agenda", "operator.list_reminders",
    "operator.find_notes", "operator.show_note", "operator.apple_note_list",
    "operator.apple_note_read", "operator.propose_calendar_event",
    "operator.check_availability", "operator.inspect_disk_usage",
})


@pytest.fixture(autouse=True)
def _real_registry_world():
    cg.reset()
    yield
    cg.reset()


@pytest.mark.parametrize("text", REVIEW_BAN)
def test_review_prose_seats_nothing(text):
    assert owner_local_operator_intents(text) == (), owner_local_operator_intents(text)


@pytest.mark.parametrize("text", OWN_QUIET)
def test_own_prose_seats_nothing(text):
    assert owner_local_operator_intents(text) == (), owner_local_operator_intents(text)


@pytest.mark.parametrize("text", REVIEW_BAN)
def test_review_prose_never_widens_the_offer_or_the_lane(text):
    specs = model_visible_specs(user_text=text, source_context=dict(OWNER_LOCAL))
    intents = {str(spec.get("intent") or "") for spec in specs}
    assert not intents & NEW_SEATS, f"{text}: offered {sorted(intents)}"
    requirements = requirements_for(text, task_class="chat_conversation", source_context=dict(OWNER_LOCAL))
    assert "operator" not in requirements.allowed_toolsets
    assert "operator_action_request" not in requirements.reason_codes


@pytest.mark.parametrize("text", OWN_QUIET)
def test_own_prose_never_widens_the_offer(text):
    specs = model_visible_specs(user_text=text, source_context=dict(OWNER_LOCAL))
    intents = {str(spec.get("intent") or "") for spec in specs}
    assert not intents & NEW_SEATS, f"{text}: offered {sorted(intents)}"


@pytest.mark.parametrize(("text", "intent"), sorted(REVIEW_MISSED.items()))
def test_the_missed_asks_seat_again(text, intent):
    assert intent in owner_local_operator_intents(text), owner_local_operator_intents(text)


@pytest.mark.parametrize(("text", "intent"), sorted({**REVIEW_MISSED, **OWN_ASKS}.items()))
def test_an_ask_with_the_owners_own_object_seats_end_to_end(text, intent):
    assert intent in owner_local_operator_intents(text), owner_local_operator_intents(text)
    specs = model_visible_specs(user_text=text, source_context=dict(OWNER_LOCAL))
    intents = {str(spec.get("intent") or "") for spec in specs}
    assert intent in intents, f"{text}: offered {sorted(intents)}"
    requirements = requirements_for(
        text, task_class="chat_conversation", source_context=dict(OWNER_LOCAL))
    assert requirements.tools_required
    assert "operator" in requirements.allowed_toolsets


def test_an_ask_whose_object_holds_a_statement_keeps_its_seat():
    # The old whole-text veto: "my reminders that are" and "my notes that have" read as
    # declarative verbs and cancelled the ask. The ask shape of the SEGMENT is what counts.
    for text in (
        "list my reminders that are due today and show my agenda",
        "find my notes that have the venue address, the ones from March",
    ):
        assert "operator.list_reminders" in owner_local_operator_intents(text) or \
            "operator.find_notes" in owner_local_operator_intents(text), text


@pytest.mark.parametrize(("text", "intent"), sorted({**JOB2B_MISSED, **OWN2B_ASKS}.items()))
def test_job2b_missed_asks_seat_again(text, intent):
    assert intent in owner_local_operator_intents(text), owner_local_operator_intents(text)
    specs = model_visible_specs(user_text=text, source_context=dict(OWNER_LOCAL))
    intents = {str(spec.get("intent") or "") for spec in specs}
    assert intent in intents, f"{text}: offered {sorted(intents)}"
    requirements = requirements_for(
        text, task_class="chat_conversation", source_context=dict(OWNER_LOCAL))
    assert requirements.tools_required
    assert "operator" in requirements.allowed_toolsets


@pytest.mark.parametrize("text", JOB2B_BAN + OWN2B_QUIET)
def test_job2b_banned_prose_seats_nothing(text):
    assert owner_local_operator_intents(text) == (), owner_local_operator_intents(text)


@pytest.mark.parametrize("text", JOB2B_BAN + OWN2B_QUIET)
def test_job2b_banned_prose_never_widens_the_offer(text):
    specs = model_visible_specs(user_text=text, source_context=dict(OWNER_LOCAL))
    intents = {str(spec.get("intent") or "") for spec in specs}
    assert not intents & NEW_SEATS, f"{text}: offered {sorted(intents)}"


def test_the_combined_report_seats_notes_and_agenda():
    # A look-over-my-notes-and-calendar report must seat BOTH sides, so the model starts from
    # a real tool list and can expand the operator family.
    intents = owner_local_operator_intents(
        "look over my notes and my calendar and tell me what's coming up")
    assert "operator.find_notes" in intents, intents
    assert "operator.show_agenda" in intents, intents
