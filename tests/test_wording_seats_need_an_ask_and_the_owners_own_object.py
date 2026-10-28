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

Job 2c (AMENDMENT 2, reviewer, 2026-10-28): Job 2b's own rules are too wide in three places
(the feasibility check vetoed real orders that merely carry a check, the pasted-material veto
dropped real store lookups with something else pasted beside them, and a segment opening with
"any meetings/..." seated the agenda for world questions) and too narrow in five (no "have I
got" question head, two event-noun lists that disagreed, no "when is my next ...", seat verbs
missing from the clause-lead list so their segments were never asks, and the app/settings
tail veto missing from the older check/show/open agenda branch). The JOB2C_MISSED rows are
red on f285dd92; the JOB2C_BAN rows seat there and must not; the verb-coverage test is the
guard that keeps every seat verb able to make its segment an ask.
"""
from __future__ import annotations

import re

import pytest

from core import capability_graph as cg
from core import tool_demand_signals as seat_signals
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

#: The reviewer's Job 2c list (AMENDMENT 2): real asks that seat nothing on f285dd92 because
#: Job 2b's new rules were too wide or too narrow. An order with a check attached and a polite
#: request are orders; a store lookup with something else pasted beside it is still a lookup;
#: "have I got", "any calls", "which appointments", part-of-day time words, "when is my next
#: ..." and the newly seated verbs are the same asks in other words.
JOB2C_MISSED = {
    # W1: an order followed by a check is still an order; a polite request is an order.
    "book a dentist appointment tuesday at 9, would that work?": "operator.propose_calendar_event",
    "create a calendar event for lunch with Mia tomorrow at 1, is that ok?":
        "operator.propose_calendar_event",
    "can you book a dentist appointment for tomorrow morning?": "operator.propose_calendar_event",
    # W2: the pasted words are an error code, not the notes — the lookup stays.
    "find my notes about the boiler, the error is below: E119": "operator.find_notes",
    # N1: "have I got" is the same ask as "do I have".
    "what reminders have I got set for tomorrow": "operator.list_reminders",
    "have I got time free tomorrow morning": "operator.check_availability",
    # N2: one event-noun list, part-of-day words are time words.
    "any calls today?": "operator.show_agenda",
    "which appointments do we have next week": "operator.show_agenda",
    "any meetings this afternoon?": "operator.show_agenda",
    # N3: "when is my next ..." seats like "what's my next meeting".
    "when is my next meeting": "operator.show_agenda",
    # N4: a seat verb must be able to make its segment an ask.
    "review my notes and my schedule and give me a plan for the week": "operator.find_notes",
    # Everyday wordings of the same shapes, written for this file.
    "book a dentist appointment friday at 4, does that work for you?":
        "operator.propose_calendar_event",
    "set up a call with the notary for monday, would that be ok?":
        "operator.propose_calendar_event",
    "please create a calendar event for the vaccine booster next monday, is that ok?":
        "operator.propose_calendar_event",
    "place the review session on my calendar for friday at 3":
        "operator.propose_calendar_event",
    "search my notes for the boiler error, the code is below: E119": "operator.find_notes",
    "find my notes about the lease, the reference number is below: 99-X": "operator.find_notes",
    "gather my notes about the move and sort them": "operator.find_notes",
    "collect my notes on the lease renewal": "operator.find_notes",
    "round up my notes from the standups": "operator.find_notes",
    "have we got any appointments today?": "operator.show_agenda",
    "have I got anything on friday": "operator.check_availability",
    "have we got time free on saturday": "operator.check_availability",
    "what calls have I got tomorrow?": "operator.show_agenda",
    "what meetings have we got this week?": "operator.show_agenda",
    "which reminders do I have coming up?": "operator.list_reminders",
    "any meetings in the morning?": "operator.show_agenda",
    "any appointments today or tomorrow?": "operator.show_agenda",
    "any calls for me tomorrow?": "operator.show_agenda",
    "when is my next appointment": "operator.show_agenda",
    "when's my next call?": "operator.show_agenda",
    "structure my day around the deadlines": "operator.show_agenda",
    "review my schedule and tell me where I'm overbooked": "operator.show_agenda",
}
#: The reviewer's Job 2c prose list (AMENDMENT 2): the feasibility check where the booking
#: itself is asked about, a world "any ..." question (a place, an organisation), and an
#: app/settings ask on the agenda branch. Job 2b's own banned rows (the pasted notes, the
#: Zoom frame, the Prime Minister bare question) stay in JOB2B_BAN above.
JOB2C_BAN = (
    # W1: the check asks about the booking itself.
    "is it possible to schedule a meeting with the team?",
    "is it free to schedule an event for friday?",
    "would that be ok to create a calendar event for the offsite?",
    "schedule a sync with the team? would that work?",
    # W3: a place or an organisation after the "any ..." noun makes it the world's.
    "any events in Paris tomorrow?",
    "any meetings of the city council today?",
    # N5: the app/settings tail on the check/show/open agenda branch.
    "check my calendar app, it crashes on launch",
    # Everyday wordings of the same shapes, written for this file.
    "any events in Berlin this weekend?",
    "any meetings of the residents association tonight?",
    "any appointments available at the clinic tomorrow?",
    "any events of the film festival today?",
    "any meetings booked at the town hall in the morning?",
    "any webinars about pensions tomorrow?",
    "check my calendar settings, they reset every launch",
    "open my agenda app and log the crash",
    "read these notes below: the gate code is 4-4-1-1",
    "recap my notes from the workshop I copied below: day one was scopes",
    "have I got anything to declare at customs?",
    "have we got any free parking at the venue?",
    "when is the next meeting of the book club?",
    "review the film tonight",
    "map the walking route for saturday",
    "have you seen my calendar notes from the course?",
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


def test_the_review_wording_of_the_combined_report_seats_both_sides_too():
    # N4: "review" sat in two seats but not in the clause-lead verb list, so this whole
    # report seated nothing on f285dd92.
    intents = owner_local_operator_intents(
        "review my notes and my schedule and give me a plan for the week")
    assert "operator.find_notes" in intents, intents
    assert "operator.show_agenda" in intents, intents


@pytest.mark.parametrize(("text", "intent"), sorted(JOB2C_MISSED.items()))
def test_job2c_missed_asks_seat_again(text, intent):
    assert intent in owner_local_operator_intents(text), owner_local_operator_intents(text)
    specs = model_visible_specs(user_text=text, source_context=dict(OWNER_LOCAL))
    intents = {str(spec.get("intent") or "") for spec in specs}
    assert intent in intents, f"{text}: offered {sorted(intents)}"
    requirements = requirements_for(
        text, task_class="chat_conversation", source_context=dict(OWNER_LOCAL))
    assert requirements.tools_required
    assert "operator" in requirements.allowed_toolsets


@pytest.mark.parametrize("text", JOB2C_BAN)
def test_job2c_banned_prose_seats_nothing(text):
    assert owner_local_operator_intents(text) == (), owner_local_operator_intents(text)


@pytest.mark.parametrize("text", JOB2C_BAN)
def test_job2c_banned_prose_never_widens_the_offer(text):
    specs = model_visible_specs(user_text=text, source_context=dict(OWNER_LOCAL))
    intents = {str(spec.get("intent") or "") for spec in specs}
    assert not intents & NEW_SEATS, f"{text}: offered {sorted(intents)}"


def _verb_group_at(pattern: str, pos: int) -> str | None:
    """The balanced "(?:...)" group starting at pos, or None if the pattern holds none."""
    if not pattern.startswith("(?:", pos):
        return None
    depth = 0
    for end in range(pos, len(pattern)):
        char = pattern[end]
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return pattern[pos : end + 1]
    return None


def _top_level_alternatives(group: str) -> list[str]:
    """The "|" alternatives of a group, ignoring pipes nested in inner groups."""
    body = group[3:-1]
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for char in body:
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        if char == "|" and depth == 0:
            parts.append("".join(current))
            current = []
        else:
            current.append(char)
    parts.append("".join(current))
    return parts


def test_every_seat_verb_can_lead_an_ask_clause():
    # N4: a seat verb missing from the clause-lead list is dead code — its segment never
    # counts as an ask, so the seat can never fire ("review my notes and my schedule ..."
    # seated nothing on f285dd92; Job 2b's `?` branch was the same kind of bug). Walk every
    # verb group the seat regexes accept behind an ask lead and hold each verb's first word
    # against the shared clause-lead list, so the two can never drift apart again.
    leads = (seat_signals._OWNER_ASK_LEAD, seat_signals._OWNER_ASK_LEAD_STRICT)
    seat_patterns = {
        name: getattr(seat_signals, name).pattern
        for name in dir(seat_signals)
        if name.endswith("_SEAT_RE") or name == "_OWNER_JOT_SAVE_RE"
    }
    assert len(seat_patterns) >= 8, sorted(seat_patterns)
    clause_lead_verbs = set(seat_signals._OWNER_CLAUSE_LEAD_VERBS)
    assert "review" in clause_lead_verbs  # the verb this job found dead
    groups_found = 0
    verbs_found: set[str] = set()
    for pattern in seat_patterns.values():
        for lead in leads:
            start = 0
            while True:
                index = pattern.find(lead, start)
                if index < 0:
                    break
                start = index + len(lead)
                group = _verb_group_at(pattern, start)
                if group is None:
                    continue
                groups_found += 1
                for alternative in _top_level_alternatives(group):
                    match = re.match(r"[a-z]+", alternative.strip().lower())
                    assert match, f"{group}: unparsable alternative {alternative!r}"
                    verbs_found.add(match.group(0))
    assert groups_found >= 10, f"only {groups_found} verb groups found — the walk is broken"
    missing = verbs_found - clause_lead_verbs
    assert not missing, (
        f"seat verbs missing from _OWNER_CLAUSE_LEAD_VERBS (dead seats): {sorted(missing)}"
    )
    # And the clause-lead regex itself really accepts each of those verbs at a lead.
    for verb in sorted(verbs_found):
        assert seat_signals._OWNER_IMPERATIVE_CLAUSE_RE.search(f"she left, {verb} it now"), verb
