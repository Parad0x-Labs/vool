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
        self.requests: list[str] = []
        self.steps = 3
        # Per step, the action that changes something the task has "chosen" for it (None: a read).
        self.actions: dict[int, str] = {}
        self.actions_run: list[str] = []
        self.actions_withheld: list[str] = []
        # Released by the test to let an admitted action finish (only when `hold_actions`).
        self.hold_actions = False
        self.action_started = threading.Event()
        self.action_release = threading.Event()

    def __call__(self, runtime, user_text, *, session_id=None, source_context=None, workspace_root_provider=None):
        context = source_context if isinstance(source_context, dict) else {}
        if context.get(fc.SIDE_ANSWER_CONTEXT_KEY):
            self.side_contexts.append(dict(context))
            return {"response": f"side answer to: {user_text}", "confidence": 0.9}
        self.task_contexts.append(context)
        self.requests.append(str(user_text))
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
                action = self.actions.get(_step)
                # The scripted stand-in for the model's choice: with a note in hand that says not
                # to, it does not choose the action. (Whether a real model obeys the note is
                # proven on the running app, not here; this file proves the note is in hand in time.)
                if action and any("don't" in note.lower() for note in self.notes_seen[-1]):
                    action = None
                if action:
                    # The tool loop's last look before an action that changes something.
                    if fc.begin_action(context, action):
                        self.actions_withheld.append(action)
                    else:
                        self.action_started.set()
                        if self.hold_actions:
                            self.action_release.wait(5)
                        self.actions_run.append(action)
                        fc.end_action(context)
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
    assert _followup("which year does the report cover?")["route"] == "answer"
    # The page then runs the side answer. It must come back while the task is still mid-step.
    thread, box = _run_in_thread(
        lambda: _post("/api/chat", _chat_body("which year does the report cover?", "side-1", followup_mode="side_answer"))
    )
    thread.join(5)
    assert not thread.is_alive(), "the side answer waited behind the running task"
    status, payload = box["value"]
    assert status == 200 and "side answer to: which year does the report cover?" in str(payload)
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
    assert all("before my next step" in r["message"] for r in replies)
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


def test_a_plan_change_reaches_the_task_at_its_next_step_without_a_question(task) -> None:
    # Was: held for a yes/no while the task kept working on the old plan. A change the user typed
    # is the user's decision already; holding it let the task act on the wording it replaced.
    reply = _followup("actually use the 2025 data instead")
    assert reply["route"] == "note_added"
    assert "yes" not in reply["message"].lower().split(), "the change was answered with a question"
    task.step_release.release()
    _wait_until(lambda: len(task.notes_seen) >= 2, what="the next step")
    assert task.notes_seen[1] == ["actually use the 2025 data instead"]


def test_dont_email_john_sent_mid_task_means_no_email_is_sent(task) -> None:
    # The task's plan: read, then send the email, then finish. The user changes it before the send.
    task.actions = {1: "email.send"}
    reply = _followup("don't email John, just draft it")
    assert reply["route"] == "note_added" and not reply["action_in_flight"]
    for _ in range(task.steps):
        task.step_release.release()
    task.thread.join(5)
    assert task.actions_run == [], "the email was sent after the user said not to"
    assert task.notes_seen[-1] == ["don't email John, just draft it"]
    assert runtime_continuity.list_queued_messages(SESSION) == []


def test_a_change_that_arrives_between_choosing_an_action_and_running_it_still_wins(task) -> None:
    # Step 0's boundary has passed with no note: the task has chosen `email.send` for this step
    # and is about to run it. The note lands in that window. The last look must still catch it.
    task.actions = {0: "email.send"}
    assert _followup("don't email John, just draft it")["route"] == "note_added"
    task.step_release.release()
    _wait_until(lambda: task.steps_done >= 1, what="step 0")
    assert task.actions_withheld == ["email.send"] and task.actions_run == []


def test_a_change_that_arrives_while_the_send_is_under_way_is_told_so(task) -> None:
    task.actions = {0: "email.send"}
    task.hold_actions = True
    task.step_release.release()
    assert task.action_started.wait(5), "the send never started"
    reply = _followup("don't email John, just draft it")
    # Nothing can recall a send that is already running. The reply must not pretend otherwise.
    assert reply["route"] == "note_added" and reply["action_in_flight"] == "email.send"
    assert "already under way" in reply["message"] and "email send" in reply["message"]
    task.action_release.set()
    _wait_until(lambda: task.actions_run == ["email.send"], what="the send finishing")
    # The change still governs everything after it.
    task.step_release.release()
    _wait_until(lambda: len(task.notes_seen) >= 2, what="the next step")
    assert task.notes_seen[1] == ["don't email John, just draft it"]


# ── what the task is doing ────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    ["what are you doing?", "wat r u doin", "status", "progres?", "hows it going", "u still there", "is it done yet", "eta?"],
)
def test_a_status_question_is_answered_at_once_from_the_tasks_own_record(task, text) -> None:
    reply = _followup(text)
    assert reply["route"] == "status"
    assert "build the quarterly report" in reply["message"]
    assert not task.finished.is_set() and not task.task_contexts[0]["cancel_event"].is_set()
    # No model was asked: no side answer ran, and the task took no note.
    assert task.side_contexts == [] and fc.active_task(SESSION)["notes"] == []


def test_the_status_says_what_is_under_way_and_never_guesses_time_left(task) -> None:
    task.actions = {0: "workspace.write_file"}
    task.hold_actions = True
    task.step_release.release()
    assert task.action_started.wait(5)
    message = _followup("how much longer")["message"]
    assert "running workspace write file" in message
    assert "can't tell how long is left" in message
    task.action_release.set()


@pytest.mark.parametrize("text", ["ok", "thanks", "cool", "got it"])
def test_an_acknowledgement_is_not_work_for_the_task(task, text) -> None:
    assert _followup(text)["route"] == "ack"
    assert fc.active_task(SESSION)["notes"] == []
    assert not task.task_contexts[0]["cancel_event"].is_set()


# ── set the task aside, do this first, come back ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "pause that and check tomorrow's weather in Vilnius first",
        "ok lets shift focus and do this your active task at the end and now focus on this",
        "hold that thought, list three dog names, then come back to this",
        "park this for now and draft the note to Anna",
    ],
)
def test_a_switch_sets_the_task_aside_runs_the_message_then_the_task_again(task, text) -> None:
    reply = _followup(text, key="switch-1")
    assert reply["route"] == "switched"
    assert task.cancelled.wait(5), "the task kept running after the user set it aside"
    task.thread.join(5)
    queued = runtime_continuity.list_queued_messages(SESSION)
    # The user's message first, in their own words; then the task's own request, once.
    # The new work first, in the user's own words without the "set that aside" part; then the
    # task's own request, once.
    assert [item["payload"]["text"] for item in queued] == [fc.new_work_text(text), "build the quarterly report"]
    assert queued[0]["payload"]["typed_text"] == text
    assert queued[1]["payload"]["followup_kind"] == "resume"
    # A resend of the same message queues nothing twice.
    assert _followup(text, key="switch-1").get("replayed") is True
    assert len(runtime_continuity.list_queued_messages(SESSION)) == 2


def test_a_replace_stops_the_task_for_good_and_runs_the_message(task) -> None:
    assert _followup("also add an appendix")["route"] == "note_added"
    reply = _followup("forget that, write me a haiku about rain instead")
    assert reply["route"] == "replaced"
    assert task.cancelled.wait(5)
    task.thread.join(5)
    queued = runtime_continuity.list_queued_messages(SESSION)
    # Only the new message: the thrown-away task is not run again, nor is the change to it.
    assert [item["payload"]["text"] for item in queued] == ["write me a haiku about rain"]


@pytest.mark.parametrize("text", ["pause", "hold on", "wait", "pasue it"])
def test_a_bare_pause_sets_the_task_aside_and_continue_starts_it_again(task, text) -> None:
    reply = _followup(text)
    assert reply["route"] == "paused" and "continue" in reply["message"]
    assert task.cancelled.wait(5)
    task.thread.join(5)
    assert runtime_continuity.list_queued_messages(SESSION) == []
    assert fc.take_parked_request(SESSION, "what is 2+2") == "", "an unrelated message resumed the paused task"
    assert fc.take_parked_request(SESSION, "continue") == "build the quarterly report"
    assert fc.take_parked_request(SESSION, "continue") == "", "the paused task was handed out twice"


def test_continue_after_a_pause_runs_the_paused_request_through_the_chat_door(task) -> None:
    assert _followup("pause")["route"] == "paused"
    task.thread.join(5)
    thread, box = _run_in_thread(lambda: _post("/api/chat", _chat_body("continue", "task-2")))
    _wait_until(lambda: len(task.requests) == 2, what="the continued turn")
    # The turn that runs is the paused task's own request, not the word "continue".
    assert task.requests[1] == "build the quarterly report"
    for _ in range(task.steps):
        task.step_release.release()
    thread.join(5)
    assert box["value"][0] == 200 and "task done" in str(box["value"][1])


def test_a_stop_after_a_pause_forgets_the_paused_task(monkeypatch, task) -> None:
    assert _followup("pause")["route"] == "paused"
    task.thread.join(5)
    # A new task starts and is stopped outright: "continue" must not bring the old one back.
    fc.begin_task(SESSION, "task-9", "something else entirely")
    assert fc.take_parked_request(SESSION, "continue") == ""
    fc.end_task(SESSION, "task-9")


@pytest.mark.parametrize(
    "text",
    [
        "wait, use the other file",
        "first sort them by date",
        "never mind the formatting, just finish",
        "stop using pandas",
        "paste this first",
        "quickly add a title",
        "forget about the second part",
        "no not like that, use bullet points",
    ],
)
def test_an_amendment_that_merely_sounds_like_a_stop_or_switch_does_not_end_the_task(task, text) -> None:
    assert _followup(text)["route"] == "note_added"
    assert not task.task_contexts[0]["cancel_event"].is_set(), f"{text!r} ended the task"


def test_when_the_task_just_finished_a_switch_runs_as_an_ordinary_message(task) -> None:
    for _ in range(task.steps):
        task.step_release.release()
    task.thread.join(5)
    # Nothing is live: nothing to set aside, nothing queued behind the user's back.
    assert _followup("pause that and tell me a joke first")["route"] == "run_now"
    assert runtime_continuity.list_queued_messages(SESSION) == []


def test_a_stop_that_cannot_queue_the_new_message_says_so(task, monkeypatch) -> None:
    def _broken(**_kwargs):
        raise RuntimeError("queue unavailable")

    monkeypatch.setattr(fc, "_default_enqueue", _broken)
    status, payload = _post(
        "/api/chat/followup", {"session_id": SESSION, "text": "forget that, write me a haiku instead", "idempotency_key": ""}
    )
    # The task is stopped and the new message is not queued: the user is told to send it again,
    # never told it is running.
    assert status == 400 and payload["task_stopped"] is True and "send it again" in payload["error"]


# ── the transcript keeps the exchange where it happened ──────────────────────────────────────────


def test_the_transcript_shows_a_mid_task_exchange_inside_its_turn() -> None:
    from core.web.api.runtime import interleave_followup_messages

    def _row(request_id: str, user: str, assistant: str, parent: str = ""):
        event = {"request_id": request_id, "followup_of_request_id": parent}
        return event, [{"role": "user", "content": user}, {"role": "assistant", "content": assistant}]

    spans = [
        _row("r0", "hello", "hi"),
        # Written first (the task was still working), though they happened inside r1's turn.
        _row("f1", "also add tests", "Got it.", parent="r1"),
        _row("f2", "status", "Still working on ...", parent="r1"),
        _row("r1", "build the report", "Here is the report."),
        # Its task's row is not in this read (still running): it stays where it was written.
        _row("f3", "progress?", "Still working on ...", parent="r2"),
    ]
    ordered = [message["content"] for message in interleave_followup_messages(spans)]
    assert ordered == [
        "hello", "hi",
        "build the report", "also add tests", "Got it.", "status", "Still working on ...", "Here is the report.",
        "progress?", "Still working on ...",
    ]


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


# ── the real tool loop does not run an action chosen before the user's change ─────────────────────


class _SendRan(Exception):
    """The loop reached the executor with the send: the stand-in executor stops the turn here."""


def _drive_loop_that_chooses_a_send(monkeypatch, *, note: str | None):
    """Round one of the REAL tool loop chooses `email.send`. With `note`, the user's change lands
    while that choice is being made -- after the round's step boundary, before the action runs."""
    from apps.vool_agent import VoolAgent
    from core.memory_first_router import ModelExecutionDecision

    workspace = pathlib.Path(tempfile.mkdtemp())
    session = f"openclaw:{uuid.uuid4().hex[:20]}"
    fc.begin_task(session, "loop-turn", "email John the Q3 summary")

    def _decision(intent: str, arguments: dict[str, Any], call_id: str):
        call = SimpleNamespace(intent=intent, arguments=arguments, call_id=call_id, name=intent)
        return ModelExecutionDecision(
            source="scripted", task_hash="h", used_model=True, confidence=0.8,
            structured_output={"intent": intent, "arguments": dict(arguments)},
            tool_calls=(call,), validation_state="validated", provider_id="p",
        )

    class _Router:
        def __init__(self) -> None:
            self.contexts: list[dict[str, Any]] = []

        def resolve_tool_intent(self, **kwargs):
            self.contexts.append(dict(kwargs.get("source_context") or {}))
            if len(self.contexts) == 1:
                if note:
                    assert fc.handle_followup(session, note)["route"] == "note_added"
                return _decision("email.send", {"to": "john@example.com", "subject": "Q3", "body": "Summary."}, "c1")
            return _decision("respond.direct", {"message": "Drafted it. Nothing was sent."}, "c2")

    router = _Router()
    agent = VoolAgent.__new__(VoolAgent)
    agent.memory_router = router
    agent._should_keep_ai_first_chat_lane = lambda **_kw: False
    agent._should_run_builder_controller = lambda **_kw: False
    agent._plan_tool_workflow = lambda **_kw: SimpleNamespace(handled=False, stop_after=False, next_payload=None, reason="")
    agent.hive_activity_tracker = None
    agent.public_hive_bridge = None
    executed: list[str] = []

    def _executor(payload, **_kwargs):
        executed.append(str(payload.get("intent")))
        raise _SendRan()

    agent._execute_tool_intent = _executor
    events: list[dict[str, Any]] = []
    original_emit = VoolAgent._emit_runtime_event

    def _recording_emit(self, source_context, *, event_type, message, **details):
        events.append({"event_type": event_type, "message": message, **details})
        return original_emit(self, source_context, event_type=event_type, message=message, **details)

    monkeypatch.setattr(VoolAgent, "_emit_runtime_event", _recording_emit)

    def _run():
        return agent._maybe_execute_model_tool_intent(
            task=SimpleNamespace(task_id="t1"),
            effective_input="email John the Q3 summary",
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

    return session, router, executed, events, _run


def test_the_real_loop_sends_when_nothing_changed(monkeypatch) -> None:
    # The control: without a change from the user the chosen send does reach the executor, so the
    # test below is not passing because the loop never sends at all.
    session, _router, executed, _events, run = _drive_loop_that_chooses_a_send(monkeypatch, note=None)
    with pytest.raises(_SendRan):
        run()
    assert executed == ["email.send"]
    assert fc.active_task(session)["action_in_flight"] == "email.send"
    fc.end_task(session, "loop-turn")


def test_the_real_loop_does_not_send_after_dont_email_john_arrived(monkeypatch) -> None:
    session, router, executed, events, run = _drive_loop_that_chooses_a_send(
        monkeypatch, note="don't email John, just draft it"
    )
    run()
    assert executed == [], "the email was sent on the wording the user had already changed"
    # The model was asked again, this time with the change in hand.
    assert len(router.contexts) >= 2
    assert [n["text"] for n in router.contexts[1][fc.FOLLOWUP_NOTES_CONTEXT_KEY]] == ["don't email John, just draft it"]
    withheld = [e for e in events if e.get("status") == "action_withheld"]
    assert len(withheld) == 1 and withheld[0]["tool_name"] == "email.send"
    fc.end_task(session, "loop-turn")


@pytest.mark.parametrize(
    ("typed", "work"),
    [
        ("pause that and check tomorrow's weather in Vilnius first", "check tomorrow's weather in Vilnius"),
        ("hold that thought, list three dog names, then come back to this", "list three dog names"),
        ("Check my mail first then go back to it", "Check my mail"),
        ("scrap that. New plan: list 3 dog names", "list 3 dog names"),
        ("forget that, write me a haiku about rain instead", "write me a haiku about rain"),
        ("finish that later, first find me a recipe for pancakes", "find me a recipe for pancakes"),
    ],
)
def test_the_new_work_is_the_users_own_words_without_the_part_about_the_old_task(typed, work) -> None:
    assert fc.new_work_text(typed) == work


def test_new_work_that_cannot_be_cut_out_cleanly_is_the_whole_message() -> None:
    assert fc.new_work_text("also add tests") == "also add tests"
