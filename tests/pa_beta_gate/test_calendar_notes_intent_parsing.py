"""pa_beta_gate — intent routing for the calendar/notes vertical: original + novel
wording, and the preservation controls that prove the additions claim nothing the old
lanes owned and that context-free words still claim no effect.
"""
from __future__ import annotations

import pytest

pytestmark = [pytest.mark.pa_beta]

from core.local_operator_actions import parse_operator_action_intent as parse

# --- the vertical's own requests -------------------------------------------------------


@pytest.mark.parametrize("text,kind", [
    # original fixture wording (including its missing-space form)
    ("Check Tuesday afternoon for a free30-minute slot", "check_availability"),
    ("check Thursday morning for a free 15-minute slot", "check_availability"),
    # novel wording, different shape
    ("what's free on my calendar Wednesday evening for 45 minutes?", "check_availability"),
    ("is Friday afternoon open on the London calendar?", "check_availability"),
    ("show me my schedule for Monday", "check_availability"),
    ("list my calendars", "list_calendars"),
    ("which calendars do I have on the provider?", "list_calendars"),
    ('propose "Project review" Tuesday 15:00 for 30 minutes', "propose_calendar_event"),
    ("propose a project review Thursday at 10:00", "propose_calendar_event"),
    ('option 2, propose "Site prep call"', "propose_calendar_event"),
    ("schedule the action \"call the venue\" from my note", "propose_calendar_event"),
    ('show me the "Project review" event', "inspect_calendar_event"),
    ("inspect the event called Site prep call", "inspect_calendar_event"),
    ("what are the details of the Launch prep event?", "inspect_calendar_event"),
    ('rename the "Site prep call" event to "Launch prep"', "update_calendar_event"),
    ("retitle the project review event to Release review", "update_calendar_event"),
    ("save a note with agenda: packaging and release checks", "save_note"),
    ('save a note titled "Kickoff" with: call the venue', "save_note"),
    ("save meeting notes with: dial-in details", "save_note"),
    ("find my notes about packaging", "find_notes"),
    ("search notes for dial-in", "find_notes"),
    ("show my notes", "find_notes"),
    ('show the note "Agenda"', "show_note"),
    ("what's in the note titled Kickoff follow-ups?", "show_note"),
])
def test_vertical_requests_route_to_their_kinds(text, kind):
    intent = parse(text)
    assert intent is not None and intent.kind == kind, f"{text!r} -> {getattr(intent, 'kind', None)}"


# --- the existing lanes keep their own words -------------------------------------------


@pytest.mark.parametrize("text,kind", [
    ("remind me to check the locks tomorrow at 9:00", "schedule_reminder"),
    ("show my reminders", "list_reminders"),
    ("cancel the reminder", "cancel_reminder"),
    ("move my reminder to tomorrow at 9:00", "move_reminder"),
    ("cancel the meeting draft", "cancel_calendar_event"),
    ("reschedule a meeting to Friday at 10:00", "move_calendar_event"),
    ("schedule a meeting \"Ops\" on 2026-03-08 15:30 for 45m", "schedule_calendar_event"),
    ("what tools do you have", "list_tools"),
    ("clean temp files", "cleanup_temp_files"),
])
def test_existing_lanes_are_unchanged(text, kind):
    assert parse(text).kind == kind


# --- context-free words claim no effect -------------------------------------------------


@pytest.mark.parametrize("text", [
    "what is a calendar?",
    "I read an interesting note about calendars yesterday",
    "the concert was a memorable event",
    "option paralysis is a real thing",
    "can you propose an idea for dinner? (no calendar)",
])
def test_context_free_words_claim_no_effect(text):
    intent = parse(text)
    assert intent is None or intent.kind not in {
        "check_availability", "list_calendars", "inspect_calendar_event",
        "propose_calendar_event", "update_calendar_event", "save_note",
        "find_notes", "show_note",
    }, f"{text!r} wrongly claimed {intent.kind}"


def test_availability_has_a_calendar_name_label():
    intent = parse("check Tuesday afternoon on the London calendar for a free 30-minute slot")
    assert intent.kind == "check_availability"
    assert intent.target_label == "London"


def test_inspect_event_has_event_label_and_calendar():
    intent = parse('show the "Project review" event on the Vilnius calendar')
    assert intent.kind == "inspect_calendar_event"
    assert intent.target_label == "Project review"
    assert intent.destination_path == "Vilnius"
