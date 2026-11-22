"""After a step that changed something, an answer that names an id none of the turn's steps returned is not published.

Installed fe98e849 (real-app check v3 and the outside tooling audit, 2026-11-22): the calendar tool made an event, and
the answer said "Reminder scheduled ... (reminder id 3bbb4fbf)", the id of an older recycling reminder from earlier in
the chat. An id is a token in every language, so the check reads no words: the step's own result speaks instead. Ids,
wordings and languages are built at random.
"""
from __future__ import annotations

import random
import secrets

import pytest

from core.agent_runtime.orchestrator import answer_with_receipt_ids, render_tool_loop_response

ANSWERS = (
    "Reminder scheduled: {task} (reminder id {rid}).",
    "Done! Your reminder is set, id {rid}.",
    "Priminimas nustatytas: {task} (id {rid}).",
    "Erinnerung gespeichert: {task}, ID {rid}.",
    "Listo, recordatorio creado ({rid}).",
    "C'est fait : rappel {rid} enregistré.",
    "Готово, напоминание {rid} создано.",
)
TASKS = ("book a haircut", "nupirkti duonos", "den Zahnarzt anrufen", "llamar al fontanero")


def _rid(rng: random.Random) -> str:
    while True:
        rid = "".join(rng.choice("0123456789abcdef") for _ in range(8))
        if any(ch.isdigit() for ch in rid):
            return rid


def _calendar_step(event_id: str, title: str) -> dict:
    return {
        "tool_name": "operator.schedule_calendar_event", "ok": True, "status": "executed", "mode": "tool_executed",
        "response_text": f"Calendar event created. ICS written to calendar_outbox/{title}-{event_id}.ics",
        "summary": "Calendar event created.", "details": {"action_id": event_id, "title": title},
        "arguments": {"title": title},
    }


def _reminder_step(reminder_id: str, task: str) -> dict:
    return {
        "tool_name": "operator.schedule_reminder", "ok": True, "status": "executed", "mode": "tool_executed",
        "response_text": f"Reminder scheduled: {task}. (reminder id {reminder_id[:8]})", "summary": "Reminder scheduled.",
        "details": {"reminder_id": reminder_id, "note": task}, "arguments": {"note": task},
    }


@pytest.mark.parametrize("seed", range(10))
def test_an_id_from_earlier_in_the_chat_is_replaced_by_the_steps_own_result(seed) -> None:
    rng = random.Random(f"receipt-ids-{seed}")
    task = rng.choice(TASKS)
    step = _calendar_step(_rid(rng), task.replace(" ", "-"))
    answer = rng.choice(ANSWERS).format(task=task, rid=_rid(rng))
    published = render_tool_loop_response(final_message=answer, executed_steps=[step], include_step_summary=False)
    assert published == step["response_text"], published


@pytest.mark.parametrize("seed", range(10))
def test_the_steps_own_id_stays(seed) -> None:
    rng = random.Random(f"receipt-ids-ok-{seed}")
    task = rng.choice(TASKS)
    reminder_id = secrets.token_hex(4) + "-" + secrets.token_hex(2) + "-4" + secrets.token_hex(2)[1:] + "-a" \
        + secrets.token_hex(2)[1:] + "-" + secrets.token_hex(6)
    step = _reminder_step(reminder_id, task)
    shown = rng.choice([reminder_id[:8], reminder_id[:8].upper(), reminder_id])
    answer = rng.choice(ANSWERS).format(task=task, rid=shown)
    assert answer_with_receipt_ids(answer, [step]) == answer


def test_a_read_or_no_id_leaves_the_answer_alone() -> None:
    rng = random.Random("receipt-ids-read")
    answer = rng.choice(ANSWERS).format(task="book a haircut", rid=_rid(rng))
    read = {**_reminder_step(secrets.token_hex(16), "x"), "tool_name": "operator.list_reminders"}
    assert answer_with_receipt_ids(answer, [read]) == answer
    plain = "Done, your event is in the calendar for 2026-12-01 at 15:00."
    assert answer_with_receipt_ids(plain, [_calendar_step(_rid(rng), "x")]) == plain
