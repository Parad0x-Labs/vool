"""A tool that ran backs what it is about, not every claim in the reply beside it.

Installed fe98e849 (real-app check v3 and the outside tooling audit, 2026-11-22): "hey make a remidner to book a
haircut tomorrow at 3pm ty" ran the calendar tool, made a 30-minute event, and the reply said "Reminder scheduled"
with the id of an older recycling reminder. No reminder existed. The claim check stood down because a tool ran.
Now a reminder claim needs a reminder tool, a calendar claim a calendar tool; a read backs a read, a change backs a
change. A run that names none of the live sources (a web lookup, a memory tool) stands the check down as before.
The wording is built from parts at random.
"""
from __future__ import annotations

import random
import secrets

import pytest

from tests.test_tool_cards_use import _gate, fresh_turns

CALENDAR_TOOLS = ("operator.schedule_calendar_event", "operator.propose_calendar_event")
REMINDER_CLAIMS = (
    "Reminder scheduled: {task} {when} (id {rid}).",
    "Done, I've set a reminder to {task} {when}.",
    "Your reminder to {task} is set for {when}.",
    "I'll remind you {when} to {task}.",
)
TASKS = ("book a haircut", "call the dentist", "water the ferns", "renew the parking permit")
WHENS = ("tomorrow at 3pm", "on Friday at 10", "tonight at 8")


@pytest.mark.parametrize("seed", range(8))
def test_a_reminder_claim_beside_a_calendar_run_is_corrected(seed, fresh_turns):
    rng = random.Random(f"tool-source-{seed}")
    task, when = rng.choice(TASKS), rng.choice(WHENS)
    claim = rng.choice(REMINDER_CLAIMS).format(task=task, when=when, rid=secrets.token_hex(4))
    event = f"I added a 30-minute event “{task.capitalize()}” to your calendar {when}."
    reply = rng.choice([f"{event} {claim}", f"{claim}\n\n{event}", claim])
    published, record = _gate(f"make a remidner to {task} {when}", reply, f"req:src:{seed}",
                              tool=rng.choice(CALENDAR_TOOLS))
    assert record["publication"] == "action_claims_corrected", (reply, record)
    assert claim not in published, published
    assert "No reminder" in published, published
    if event in reply:
        assert event in published, published   # the calendar tool ran: its own sentence stays


@pytest.mark.parametrize("seed", range(4))
def test_a_claim_about_the_source_whose_tool_ran_is_left_alone(seed, fresh_turns):
    rng = random.Random(f"tool-source-ok-{seed}")
    task, when = rng.choice(TASKS), rng.choice(WHENS)
    reply = rng.choice(REMINDER_CLAIMS).format(task=task, when=when, rid=secrets.token_hex(4))
    published, record = _gate(f"remind me {when} to {task}", reply, f"req:src-ok:{seed}",
                              tool="operator.schedule_reminder")
    assert published == reply and record["publication"] == "execution_evidence_present", record


def test_a_read_does_not_back_a_change_of_the_same_source(fresh_turns):
    reply = "Done, I've set a reminder to book a haircut tomorrow at 3pm."
    published, record = _gate("make a reminder to book a haircut tomorrow at 3pm", reply, "req:src-read",
                              tool="operator.list_reminders")
    assert record["publication"] == "action_claims_corrected", record
    assert reply not in published


def test_a_run_that_names_no_live_source_still_stands_the_check_down(fresh_turns):
    reply = "Done, I've set a reminder to book a haircut tomorrow at 3pm."
    published, record = _gate("make a reminder to book a haircut tomorrow at 3pm", reply, "req:src-web",
                              tool="web.search")
    assert published == reply and record["publication"] == "execution_evidence_present", record
