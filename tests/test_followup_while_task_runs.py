"""A message sent while a task runs must not break, pause or duplicate that task.

Measured on main 9adff83 before this change: the page queued a mid-task message, the per-session
turn gate held it, and it ran as a NEW turn only after the task finished. A question waited for the
whole task; a correction never reached the work it was about and started a second task afterwards;
a typed "stop" was queued like any other text and stopped nothing.

These tests drive the served door (`dispatch_post`) and the real per-session gate in `run_agent`.
The only substitution is the turn body (`_run_agent_locked`): a scripted task that takes steps the
test releases one at a time and, at each step boundary, calls the same seam the real tool loop calls
(`drain_notes_into_context`). The tool-loop wiring itself is driven through the real
`_maybe_execute_model_tool_intent` with a scripted router at the end of this file.
"""

from __future__ import annotations

import pathlib
import tempfile
import threading
import time
import uuid
from types import SimpleNamespace
from typing import Any

import pytest

from core import followup_continuity as fc
from core import live_turns, runtime_continuity, runtime_paths
from storage.migrations import run_migrations

SESSION = "openclaw:ffffffffffffffffffff"


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    db = tmp_path / "continuity.db"
    run_migrations(db_path=db)
    runtime_continuity.configure_runtime_continuity_db_path(str(db))
    runtime_continuity.reset_runtime_continuity_state()
    fc.reset_followup_state()
    live_turns.reset_live_turns()
    yield
    fc.reset_followup_state()
    live_turns.reset_live_turns()
    runtime_continuity.reset_runtime_continuity_state()
    runtime_continuity.configure_runtime_continuity_db_path(None)
    runtime_paths.configure_runtime_home(None)


def _wait_until(predicate, timeout: float = 5.0, what: str = "condition") -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


class _ScriptedTask:
    """The turn body. A task takes steps the test releases; a side answer answers at once."""

    def __init__(self) -> None:
        self.started = threading.Event()
        self.step_release = threading.Semaphore(0)
        self.steps_done = 0
        self.notes_seen: list[list[str]] = []
        self.cancelled = threading.Event()
        self.finished = threading.Event()
        self.side_contexts: list[dict[str, Any]] = []
        self.task_contexts: list[dict[str, Any]] = []
        self.steps = 3

    def __call__(self, runtime, user_text, *, session_id=None, source_context=None, workspace_root_provider=None):
        context = source_context if isinstance(source_context, dict) else {}
        if context.get(fc.SIDE_ANSWER_CONTEXT_KEY):
            self.side_contexts.append(dict(context))
            return {"response": f"side answer to: {user_text}", "confidence": 0.9}
        self.task_contexts.append(context)
        self.started.set()
        try:
            cancel = context.get("cancel_event")
            for _step in range(self.steps):
                # A step boundary, exactly where the tool loop picks notes up.
                fc.drain_notes_into_context(context)
                self.notes_seen.append([n["text"] for n in context.get(fc.FOLLOWUP_NOTES_CONTEXT_KEY) or []])
                while not self.step_release.acquire(timeout=0.02):
                    if cancel is not None and cancel.is_set():
                        self.cancelled.set()
                        return {"response": "stopped", "confidence": 0.5}
                self.steps_done += 1
            return {"response": "task done", "confidence": 0.9}
        finally:
            self.finished.set()


def _post(path: str, body: dict[str, Any], *, host: str = "127.0.0.1"):
    import json

    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    res = dispatch_post(
        path=path,
        body=body,
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host=host,
    )
    return res.status, json.loads(res.body.decode("utf-8") or "{}")


def _chat_body(text: str, turn_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "session_id": SESSION,
        "stream": False,
        "turn_id": turn_id,
        "messages": [{"role": "user", "content": text}],
        **extra,
    }


def _followup(text: str, key: str = "") -> dict[str, Any]:
    status, payload = _post("/api/chat/followup", {"session_id": SESSION, "text": text, "idempotency_key": key})
    assert status == 200, payload
    return payload


@pytest.fixture()
def task(monkeypatch):
    from core.web.api import runtime as api_runtime

    scripted = _ScriptedTask()
    monkeypatch.setattr(api_runtime, "_run_agent_locked", scripted)
    results: dict[str, Any] = {}

    def _run() -> None:
        results["task"] = _post("/api/chat", _chat_body("build the quarterly report", "task-1"))

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    assert scripted.started.wait(5), "the task never started"
    scripted.thread = thread
    scripted.results = results
    yield scripted
    for _ in range(10):
        scripted.step_release.release()
    thread.join(5)


def _run_in_thread(fn) -> tuple[threading.Thread, dict[str, Any]]:
    box: dict[str, Any] = {}
    thread = threading.Thread(target=lambda: box.setdefault("value", fn()), daemon=True)
    thread.start()
    return thread, box


# ── no task, no change ────────────────────────────────────────────────────────────────────────────


def test_with_no_task_running_a_message_is_an_ordinary_turn() -> None:
    assert _followup("what is 2+2?")["route"] == "run_now"
    assert _followup("also add tests")["route"] == "run_now"


def test_the_followup_door_is_owner_local_and_strict() -> None:
    status, payload = _post("/api/chat/followup", {"session_id": SESSION, "text": "hi"}, host="203.0.113.9")
    assert status == 403 and payload["error"] == "owner_local_required"
    status, payload = _post("/api/chat/followup", {"session_id": SESSION, "text": "hi", "turn_id": "x"})
    assert status == 400 and "unknown fields" in payload["error"]
    status, payload = _post("/api/chat/followup", {"session_id": SESSION, "text": "  "})
    assert status == 400


# ── a question is answered now, beside the task ───────────────────────────────────────────────────


def test_a_question_is_answered_while_the_task_keeps_running(task) -> None:
    assert _followup("how far along are you?")["route"] == "answer"
    # The page then runs the side answer. It must come back while the task is still mid-step.
    thread, box = _run_in_thread(
        lambda: _post("/api/chat", _chat_body("how far along are you?", "side-1", followup_mode="side_answer"))
    )
    thread.join(5)
    assert not thread.is_alive(), "the side answer waited behind the running task"
    status, payload = box["value"]
    assert status == 200 and "side answer to: how far along are you?" in str(payload)
    assert not task.finished.is_set() and task.steps_done == 0, "the task was disturbed by the side answer"
    side = task.side_contexts[-1]
    assert side[fc.SIDE_ANSWER_SNAPSHOT_KEY]["request_text"] == "build the quarterly report"
    # The side answer is not a task: a note sent now still goes to the real task.
    assert fc.active_task(SESSION)["turn_id"] == "task-1"
    assert _followup("also include the appendix")["route"] == "note_added"
    task.step_release.release()
    _wait_until(lambda: task.notes_seen and task.notes_seen[-1] == ["also include the appendix"], what="note delivery")


def test_an_ordinary_turn_still_waits_its_turn_behind_the_task(task) -> None:
    """The control: only the server-confirmed side answer bypasses the session gate."""
    thread, _box = _run_in_thread(lambda: _post("/api/chat", _chat_body("next request", "turn-2")))
    thread.join(0.5)
    assert thread.is_alive(), "an ordinary turn overtook the running task"
    assert len(task.task_contexts) == 1
    # It runs once the task ahead of it is done (the scripted body serves it too, so release its steps).
    for _ in range(task.steps * 2):
        task.step_release.release()
    thread.join(5)
    assert not thread.is_alive()
    assert len(task.task_contexts) == 2


def test_a_forged_side_answer_marker_is_dropped(monkeypatch) -> None:
    from core.web.api import runtime as api_runtime

    scripted = _ScriptedTask()
    scripted.steps = 0
    monkeypatch.setattr(api_runtime, "_run_agent_locked", scripted)
    forged = {"source_context": {fc.SIDE_ANSWER_CONTEXT_KEY: True, fc.FOLLOWUP_NOTES_CONTEXT_KEY: [{"text": "x"}]}}
    status, _ = _post("/api/chat", _chat_body("hello", "turn-f", followup_mode="side_answer", **forged))
    assert status == 200
    # No live task: the request for a side answer is not honoured, and the forged keys are gone.
    context = scripted.task_contexts[-1]
    assert not context.get(fc.SIDE_ANSWER_CONTEXT_KEY)
    assert not context.get(fc.FOLLOWUP_NOTES_CONTEXT_KEY)


def test_a_side_answer_cannot_act() -> None:
    from core.agent_runtime.agent import _forbid_side_answer_actions
    from core.agent_runtime.intent_claims import ActionPolicy, commands_forbidden_in_context

    context: dict[str, Any] = {fc.SIDE_ANSWER_CONTEXT_KEY: True, "action_policy": ActionPolicy.ALLOWED.value}
    _forbid_side_answer_actions(context)
    assert context["action_policy"] == ActionPolicy.FORBIDDEN.value
    assert commands_forbidden_in_context(context)
    ordinary: dict[str, Any] = {"action_policy": ActionPolicy.ALLOWED.value}
    _forbid_side_answer_actions(ordinary)
    assert ordinary["action_policy"] == ActionPolicy.ALLOWED.value


# ── a note joins the running task ─────────────────────────────────────────────────────────────────


def test_several_notes_in_a_row_all_land_in_order_at_the_next_step(task) -> None:
    replies = [_followup(text) for text in ("include Q3 numbers", "make sure to cite sources", "keep it under 2 pages")]
    assert [r["route"] for r in replies] == ["note_added"] * 3
    assert all("Added to the running task" in r["message"] for r in replies)
    task.step_release.release()
    _wait_until(lambda: len(task.notes_seen) >= 2, what="the next step")
    assert task.notes_seen[1] == ["include Q3 numbers", "make sure to cite sources", "keep it under 2 pages"]
    for _ in range(task.steps):
        task.step_release.release()
    task.thread.join(5)
    status, payload = task.results["task"]
    assert status == 200 and "task done" in str(payload)
    # Exactly one task ran: no duplicate was started for the notes.
    assert len(task.task_contexts) == 1
    assert runtime_continuity.list_queued_messages(SESSION) == []


def test_a_replayed_followup_attaches_once(task) -> None:
    first = _followup("add a summary table", key="idem-1")
    second = _followup("add a summary table", key="idem-1")
    assert second.get("replayed") is True and second["note"]["note_id"] == first["note"]["note_id"]
    assert [n["text"] for n in fc.active_task(SESSION)["notes"]] == ["add a summary table"]


def test_a_plan_change_is_held_until_the_user_says_yes(task) -> None:
    held = _followup("actually use the 2025 data instead")
    assert held["route"] == "needs_confirmation" and "yes" in held["message"]
    task.step_release.release()
    _wait_until(lambda: len(task.notes_seen) >= 2, what="the next step")
    assert task.notes_seen[1] == [], "a plan change reached the task before the user confirmed it"
    assert _followup("yes")["route"] == "note_confirmed"
    task.step_release.release()
    _wait_until(lambda: len(task.notes_seen) >= 3, what="the step after confirmation")
    assert task.notes_seen[2] == ["actually use the 2025 data instead"]


def test_a_declined_plan_change_never_reaches_the_task(task) -> None:
    assert _followup("skip the charts")["route"] == "needs_confirmation"
    assert _followup("no")["route"] == "note_dropped"
    for _ in range(task.steps):
        task.step_release.release()
    task.thread.join(5)
    assert all(seen == [] for seen in task.notes_seen)
    assert runtime_continuity.list_queued_messages(SESSION) == []


def test_a_note_the_task_never_reached_runs_next_instead_of_vanishing(task) -> None:
    for _ in range(task.steps - 1):
        task.step_release.release()
    _wait_until(lambda: len(task.notes_seen) == task.steps, what="the last step")
    # The task is on its last step: it will not take another, so this note cannot reach it.
    assert _followup("also email it to the team")["route"] == "note_added"
    task.step_release.release()
    task.thread.join(5)
    queued = runtime_continuity.list_queued_messages(SESSION)
    assert [item["payload"]["text"] for item in queued] == ["also email it to the team"]
    assert fc.active_task(SESSION) is None


# ── only an explicit stop stops it ────────────────────────────────────────────────────────────────


def test_an_explicit_stop_stops_the_task(task) -> None:
    reply = _followup("stop")
    assert reply["route"] == "stopped" and reply["cancel_state"] == "cancelled"
    assert task.cancelled.wait(5), "the task never observed the stop"
    task.thread.join(5)
    assert live_turns.request_cancel(SESSION, "task-1") == "not_found"


@pytest.mark.parametrize("text", ["should I stop it?", "stop using pandas", "don't stop", "how do I cancel my plan?"])
def test_mentioning_stop_is_not_a_stop(task, text) -> None:
    assert _followup(text)["route"] != "stopped"
    cancel = task.task_contexts[0].get("cancel_event")
    assert cancel is not None and not cancel.is_set(), f"{text!r} stopped the task"


def test_the_shutdown_brake_is_left_to_its_own_authority(task) -> None:
    assert _followup("stop everything")["route"] == "passthrough"
    assert not task.task_contexts[0]["cancel_event"].is_set()


# ── the real tool loop picks a note up between model rounds ───────────────────────────────────────


def test_the_tool_loop_hands_a_mid_task_note_to_the_next_model_round(monkeypatch) -> None:
    from apps.vool_agent import VoolAgent
    from core.memory_first_router import ModelExecutionDecision
    from core.prompt_normalizer import _followup_context_message
    from core.task_event_model import build_task_event

    workspace = pathlib.Path(tempfile.mkdtemp())
    (workspace / "README.md").write_text("MARKER_README\n", encoding="utf-8")
    session = f"openclaw:{uuid.uuid4().hex[:20]}"
    fc.begin_task(session, "loop-turn", "check the project")

    class _Router:
        def __init__(self) -> None:
            self.contexts: list[dict[str, Any]] = []

        def resolve_tool_intent(self, **kwargs):
            self.contexts.append(dict(kwargs.get("source_context") or {}))
            if len(self.contexts) == 1:
                # The user adds a note while round one's tool is being chosen and run.
                assert fc.handle_followup(session, "also check the license file")["route"] == "note_added"
                call = SimpleNamespace(intent="workspace.read_file", arguments={"path": "README.md"}, call_id="c1", name="workspace.read_file")
                return ModelExecutionDecision(
                    source="scripted", task_hash="h", used_model=True, confidence=0.8,
                    structured_output={"intent": call.intent, "arguments": dict(call.arguments)},
                    tool_calls=(call,), validation_state="validated", provider_id="p",
                )
            done = SimpleNamespace(intent="respond.direct", arguments={"message": "Done."}, call_id="c2", name="respond.direct")
            return ModelExecutionDecision(
                source="scripted", task_hash="h", used_model=True, confidence=0.8,
                structured_output={"intent": "respond.direct", "arguments": {"message": "Done."}},
                tool_calls=(done,), validation_state="validated", provider_id="p",
            )

    router = _Router()
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None, reason="")
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    events: list[dict[str, Any]] = []
    original_emit = VoolAgent._emit_runtime_event

    def _recording_emit(self, source_context, *, event_type, message, **details):
        events.append({"event_type": event_type, "message": message, **details})
        return original_emit(self, source_context, event_type=event_type, message=message, **details)

    monkeypatch.setattr(VoolAgent, "_emit_runtime_event", _recording_emit)
    agent._maybe_execute_model_tool_intent(
        task=SimpleNamespace(task_id="t1"),
        effective_input="check the project",
        classification={"task_class": "debugging"},
        interpretation=SimpleNamespace(),
        context_result=SimpleNamespace(),
        persona=SimpleNamespace(),
        session_id=session,
        source_context={
            "session_id": session, "runtime_session_id": session, "cancel_turn_id": "loop-turn",
            "surface": "api", "workspace": str(workspace), "workspace_root": str(workspace),
            "workspace_binding": "project", "project_id": workspace.name,
        },
        surface="api",
    )
    assert len(router.contexts) >= 2
    assert not router.contexts[0].get(fc.FOLLOWUP_NOTES_CONTEXT_KEY)
    second = router.contexts[1]
    assert [n["text"] for n in second[fc.FOLLOWUP_NOTES_CONTEXT_KEY]] == ["also check the license file"]
    rendered = _followup_context_message(second)
    assert rendered is not None and "also check the license file" in rendered.content
    picked = [e for e in events if e["event_type"] == "followup_note_applied"]
    assert len(picked) == 1
    typed = build_task_event(picked[0])
    assert typed is not None and typed["summary"].startswith("Picked up your note: also check the license file")
    fc.end_task(session, "loop-turn")
