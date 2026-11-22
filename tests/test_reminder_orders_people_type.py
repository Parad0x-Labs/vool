"""Reminder orders the way people type them, and a cancel that reaches the reminder it names.

Real-app check v3 on fe98e849 (installed app, local qwen3:8b, fresh home, 2026-11-22):
- "hey make a remidner to book a haircut tomorrow at 3pm ty" was not read as a reminder. The model booked a calendar
  event and answered "Reminder scheduled" with another reminder's id; no reminder was saved.
- "Alright scrap the reminder to take out the recycling thakns", said in another chat than the one that set it, found
  nothing ("could not find a pending reminder"), while "which reminders are set" in that same chat listed it.
- "hey remind me what we were talking about :)" was scheduled as a reminder and answered "I could not read a date or
  time in that".
The wording is built from parts at random, so no fixed sentence is what passes.
"""
from __future__ import annotations

import random

import pytest

from core.operator.handlers import _reminder_note_from_text, handle_cancel_reminder
from core.operator.models import OperatorActionIntent
from core.operator.parser import parse_operator_action_intent

LEADS = ["", "hey ", "ok ", "hmm ", "Alright ", "so "]
ORDERS = ["make a {r}", "set a {r}", "make me a {r}", "add a {r}", "create a {r}", "set up a {r}", "put a {r} in"]
SLIPS = ["reminder", "remidner", "reminer", "remindr", "rmeinder", "reminderr"]
TASKS = ["book a haircut", "water the ferns", "call the plumber", "renew the parking permit", "take out the recycling"]
WHENS = ["tomorrow at 3pm", "at 6pm", "on friday at 10", "in 20 minutes"]
TAILS = ["", " ty", " thx", " pls", " :)"]


@pytest.mark.parametrize("seed", range(10))
def test_a_reminder_order_with_a_slip_or_a_plain_verb_is_a_reminder(seed):
    rng = random.Random(f"reminder-order-{seed}")
    for _ in range(5):
        task = rng.choice(TASKS)
        said = (rng.choice(LEADS) + rng.choice(ORDERS).format(r=rng.choice(SLIPS)) + " to " + task + " "
                + rng.choice(WHENS) + rng.choice(TAILS))
        intent = parse_operator_action_intent(said)
        assert intent is not None and intent.kind == "schedule_reminder", (said, intent)
        assert _reminder_note_from_text(said).startswith(task), (said, _reminder_note_from_text(said))


@pytest.mark.parametrize(
    "said",
    [
        "hey remind me what we were talking about :)",
        "ok remind me what my locker number is",
        "Alright, remind me which hotel I picked",
    ],
)
def test_a_lead_in_before_remind_me_keeps_a_recall_a_recall(said):
    intent = parse_operator_action_intent(said)
    assert intent is None or intent.kind != "schedule_reminder", (said, intent)


@pytest.mark.parametrize(
    "said",
    [
        "i put a reminder note on the fridge",     # a statement, not an order
        "we set a reminder last week already",
        "make a list of groceries",
    ],
)
def test_a_statement_about_a_reminder_is_not_an_order(said):
    intent = parse_operator_action_intent(said)
    assert intent is None or intent.kind != "schedule_reminder", (said, intent)


def _row(reminder_id, session_id, note):
    return {"reminder_id": reminder_id, "session_id": session_id, "note": note, "status": "scheduled",
            "due_at_utc": "2026-11-23T10:00:00+00:00", "tz_name": "UTC", "due_wall": "2026-11-23 10:00"}


RECYCLING = _row("3bbb4fbf-103a-41a4-b70f-02cfe04d362c", "chat-set", "take out the recycling please")
HAIRCUT = _row("5622b1af-0ce0-44e9-afdc-2672e67e0c79", "chat-set", "book a haircut")
SEALED = _row("f1e2d3c4-4444-4444-8444-f1e2d3c4e5e5", "chat-private", "take out the compost")
CANCELS = ["{lead}scrap the {r} to {task}{tail}", "{lead}cancel the {task} {r}{tail}",
           "{lead}delete my {r} about {task}{tail}", "{lead}get rid of the {r} to {task}{tail}"]


def _cancel(said, *, asking, visible):
    cancelled: list[str] = []
    result = handle_cancel_reminder(
        parse_operator_action_intent(said) or OperatorActionIntent(kind="cancel_reminder", raw_text=said),
        task_id="t", session_id=asking,
        load_reminder_fn=lambda rid: next((r for r in (RECYCLING, HAIRCUT, SEALED) if r["reminder_id"] == rid), None),
        list_reminders_fn=lambda *, session_id, include_delivered=False: [r for r in visible if r["session_id"] == session_id],
        cancel_reminder_fn=lambda rid: (cancelled.append(rid), {"ok": True, "status": "cancelled"})[1],
        audit_log_fn=lambda *a, **k: None,
        owner_rows_fn=lambda session_id: [r for r in visible if r is not SEALED or session_id == "chat-private"],
    )
    return result, cancelled


@pytest.mark.parametrize("seed", range(6))
def test_a_cancel_in_another_chat_reaches_the_listed_reminder_it_names(seed):
    rng = random.Random(f"reminder-cancel-{seed}")
    target, other = rng.sample([RECYCLING, HAIRCUT], 2)
    task = target["note"].replace(" please", "")
    said = rng.choice(CANCELS).format(lead=rng.choice(LEADS), r=rng.choice(SLIPS[:3]), task=task, tail=rng.choice(TAILS))
    intent = parse_operator_action_intent(said)
    assert intent is not None and intent.kind == "cancel_reminder", (said, intent)
    result, cancelled = _cancel(said, asking="chat-ask", visible=[RECYCLING, HAIRCUT, SEALED])
    assert cancelled == [target["reminder_id"]], (said, result.response_text)


def test_a_private_chats_reminder_is_never_reached_from_another_chat():
    result, cancelled = _cancel("scrap the reminder to take out the compost", asking="chat-ask",
                                visible=[RECYCLING, SEALED])
    assert SEALED["reminder_id"] not in cancelled, result.response_text
