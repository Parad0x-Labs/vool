"""A chat "forget" reaches the chat's goals, and a goal writer cannot put the words back.

Measured on the goals branch merged with the 0.7.2 train: the forget sweep knew every chat-keyed runtime
table except ``chat_goals`` (added by the /goal workflow), so "Forget X" in a chat whose goal named X always
answered "Forget could not be verified ... A retry will not clear chat goals" -- that chat could never forget
X. And ``save_goal`` writes the whole goal object a worker holds in memory, so a worker that read the goal
before the forget could write the words straight back.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from tests.test_forget_scrubs_run_history import _remember, _runtime_db, isolated_pages
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command as command
from tests.test_question_date_time_leg_20261002 import _hash_backend

pytestmark = pytest.mark.usefixtures("_hash_backend")

CANARY = "Flourstone Bakehouse"
FACT = "my sister's bakery is called Flourstone Bakehouse."


def _goal(chat: str, objective: str, steps: list[dict[str, Any]], *, state: str = "running",
          answer: str = "") -> dict[str, Any]:
    """A goal written by the goal store's own writer, as the served ingress and the worker write it."""
    import time

    from core.goal_workflow import build_plan, new_goal_id, save_goal

    plan = build_plan(steps, provenance="model")
    plan["steps"][0]["state"] = "completed" if answer else "running"
    plan["steps"][0]["answer_excerpt"] = answer
    plan["steps"][0]["turn_id"] = "step-turn-1"
    now = time.time()
    goal = {
        "goal_id": new_goal_id(), "session_id": chat, "project_id": "", "objective": objective, "acceptance": "",
        "plan": plan, "state": state, "revision": 1, "current_step_id": plan["steps"][0]["id"],
        "created_at": now, "updated_at": now, "ended_at": now if state == "completed" else None,
        "started_at": now, "paused_at": None, "paused_seconds": 0.0, "created_turn_id": "goal-turn-1",
        "last_reason": "", "model": "qwen2.5:0.5b",
        "exec_context": {"workspace": "", "chat_id": chat, "owner_local": True, "local_only": False},
        "worker_epoch": 0,
    }
    save_goal(goal)
    return goal


def _row(goal_id: str) -> dict[str, Any]:
    conn = sqlite3.connect(_runtime_db())
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT * FROM chat_goals WHERE goal_id = ?", (goal_id,)).fetchone()
    finally:
        conn.close()
    assert row is not None, goal_id
    return dict(row)


def _carries(row: dict[str, Any]) -> bool:
    return "flourstone" in json.dumps(row).casefold()


def _live_worker(goal: dict[str, Any], turn_id: str):
    """The registration the served adapter makes before a worker thread starts, with its step turn live."""
    from core.goal_workflow import _GoalWorker, claim_worker_epoch, register_worker
    from core.live_turns import register_turn

    worker = _GoalWorker(goal["goal_id"], goal["session_id"], epoch=claim_worker_epoch(goal["goal_id"]))
    register_worker(worker)
    cancel = register_turn(goal["session_id"], turn_id)
    worker.set_current_turn(turn_id)
    return worker, cancel


def _release(worker, turn_id: str) -> None:
    from core.goal_workflow import unregister_worker
    from core.live_turns import unregister_turn

    unregister_turn(worker.session_id, turn_id)
    unregister_worker(worker.goal_id, worker)


def test_forget_completes_and_stops_the_goal_that_runs_on_the_words(isolated_pages):
    from core.goal_workflow import WorkerFencedError, load_goal, save_goal

    chat, other = "goals-forget-own", "goals-forget-other"
    _remember(isolated_pages, chat, FACT)
    running = _goal(chat, "Plan a visit to the Flourstone Bakehouse", [
        {"id": "step-1", "title": "Find the Flourstone Bakehouse opening hours"},
        {"id": "step-2", "title": "Book a table", "depends_on": ["step-1"]}])
    finished = _goal(chat, "Paint the harbor lantern signal green", [{"id": "step-1", "title": "Buy paint"}],
                     state="completed", answer="Bought a tin of signal green.")
    foreign = _goal(other, "Plan a visit to the Flourstone Bakehouse", [{"id": "step-1", "title": "Call them"}])
    worker, cancel = _live_worker(running, "step-turn-1")
    stale = load_goal(running["goal_id"])  # what the worker holds in memory
    finished_before, foreign_before = _row(finished["goal_id"]), _row(foreign["goal_id"])
    try:
        handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

        assert handled and reply.startswith("Forget applied"), reply
        row = _row(running["goal_id"])
        assert not _carries(row), row
        # The goal's own objective was the forgotten words: it does not keep running on a blanked objective.
        assert row["state"] == "cancelled" and row["ended_at"], row
        assert [step["state"] for step in json.loads(row["plan_json"])["steps"]] == ["cancelled", "pending"], row
        assert row["worker_epoch"] > worker.epoch, row
        # The live worker is told to stop and its in-flight step turn is cancelled.
        assert worker.stop_requested.is_set() and cancel.is_set()
        # Its stale copy cannot be written back over the erasure.
        with pytest.raises(WorkerFencedError):
            save_goal(stale, expected_epoch=worker.epoch)
        assert _row(running["goal_id"]) == row
    finally:
        _release(worker, "step-turn-1")
    # A goal of this chat that never held the words, and another chat's goal, are byte-for-byte untouched.
    assert _row(finished["goal_id"]) == finished_before
    assert _row(foreign["goal_id"]) == foreign_before


def test_a_goal_whose_own_text_is_clear_loses_only_the_words_and_keeps_running(isolated_pages):
    chat = "goals-forget-excerpt"
    _remember(isolated_pages, chat, FACT)
    goal = _goal(chat, "Write a note about harbor lanterns", [
        {"id": "step-1", "title": "Collect lantern colours"},
        {"id": "step-2", "title": "Write the note", "depends_on": ["step-1"]}],
        answer="Colours collected; the Flourstone Bakehouse sells lantern oil.")
    before = _row(goal["goal_id"])

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Forget applied"), reply
    row = _row(goal["goal_id"])
    assert not _carries(row), row
    steps = json.loads(row["plan_json"])["steps"]
    assert steps[0]["answer_excerpt"] == "" and steps[0]["title"] == "Collect lantern colours", steps
    # Nothing the goal runs on was forgotten: it keeps its state, its worker epoch and its other words.
    for column in ("state", "objective", "worker_epoch", "current_step_id", "ended_at", "exec_context_json"):
        assert row[column] == before[column], column


def test_a_goal_write_after_the_forget_cannot_put_the_words_back(isolated_pages):
    from core.goal_workflow import WorkerFencedError, create_goal_unless_active, load_goal, save_goal

    chat = "goals-forget-late-writer"
    _remember(isolated_pages, chat, FACT)
    neutral = _goal(chat, "Write a note about harbor lanterns", [{"id": "step-1", "title": "Write the note"}])
    worker, _cancel = _live_worker(neutral, "step-turn-1")
    try:
        held = load_goal(neutral["goal_id"])
        handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")
        assert handled and reply.startswith("Forget applied"), reply

        # The worker settles its step after the forget: its answer and reason carry the words.
        step = held["plan"]["steps"][0]
        step["state"] = "completed"
        step["answer_excerpt"] = "Done. The Flourstone Bakehouse sells lantern oil."
        held["state"] = "completed"
        held["last_reason"] = "finished after asking the Flourstone Bakehouse"
        save_goal(held, expected_epoch=worker.epoch)

        row = _row(neutral["goal_id"])
        assert not _carries(row), row
        # Everything else the worker wrote is kept.
        assert row["state"] == "completed" and json.loads(row["plan_json"])["steps"][0]["state"] == "completed", row
        assert json.loads(row["plan_json"])["steps"][0]["answer_excerpt"] == "", row
    finally:
        _release(worker, "step-turn-1")

    # A copy whose objective is the forgotten words (read before the forget, written after) is not
    # written as it is, and the goal it describes is not left to run on them.
    stale = dict(load_goal(neutral["goal_id"]), state="running", ended_at=None,
                 objective="Plan a visit to the Flourstone Bakehouse")
    with pytest.raises(WorkerFencedError):
        save_goal(stale)
    row = _row(neutral["goal_id"])
    assert not _carries(row) and row["state"] == "cancelled", row

    # A new goal naming the forgotten words is not recorded in this chat.
    fresh = dict(stale, goal_id="goal-forgotten-words-new", state="queued", plan={}, ended_at=None,
                 objective="Plan a visit to the Flourstone Bakehouse")
    with pytest.raises(WorkerFencedError):
        create_goal_unless_active(fresh)
    conn = sqlite3.connect(_runtime_db())
    try:
        assert conn.execute("SELECT COUNT(*) FROM chat_goals WHERE goal_id = ?", (fresh["goal_id"],)).fetchone() == (0,)
    finally:
        conn.close()


def test_an_unreadable_forget_ledger_refuses_every_goal_write(isolated_pages):
    from core.goal_workflow import WorkerFencedError, create_goal_unless_active, load_goal, save_goal
    from core.memory import revocation

    chat = "goals-forget-ledger"
    _remember(isolated_pages, chat, FACT)
    goal = _goal(chat, "Write a note about harbor lanterns", [{"id": "step-1", "title": "Write the note"}])
    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")
    assert handled and reply.startswith("Forget applied"), reply
    ledger = revocation._directory() / "ledger.json"
    assert ledger.exists(), "the forget wrote no ledger; the unreadable case would be vacuous"
    before = _row(goal["goal_id"])
    good = ledger.read_bytes()
    ledger.write_text("{not json", encoding="utf-8")
    try:
        held = load_goal(goal["goal_id"])
        held["plan"]["steps"][0]["answer_excerpt"] = "Lanterns burn oil."  # no forgotten words at all
        held["state"] = "completed"
        # Unknown is not "nothing was forgotten": the write is refused, not made.
        with pytest.raises(WorkerFencedError):
            save_goal(held)
        assert _row(goal["goal_id"]) == before
        with pytest.raises(WorkerFencedError):
            create_goal_unless_active(dict(held, goal_id="goal-ledger-unreadable", session_id="goals-forget-ledger-new",
                                           state="queued", plan={}))
        conn = sqlite3.connect(_runtime_db())
        try:
            assert conn.execute("SELECT COUNT(*) FROM chat_goals WHERE goal_id = 'goal-ledger-unreadable'").fetchone() == (0,)
        finally:
            conn.close()
    finally:
        ledger.write_bytes(good)


def test_a_forget_that_finds_nothing_leaves_the_chats_goal_alone(isolated_pages):
    """Regression guard (passes before and after the change): an empty forget still says nothing was
    forgotten and does not touch a goal that never held the words."""
    from core.context_namespace import ensure_chat_namespace

    chat = "goals-forget-empty"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    goal = _goal(chat, "Write a note about harbor lanterns", [{"id": "step-1", "title": "Write the note"}])
    before = _row(goal["goal_id"])

    handled, reply = command(isolated_pages, chat, f"Forget {CANARY}.")

    assert handled and reply.startswith("Nothing was removed"), reply
    assert _row(goal["goal_id"]) == before
