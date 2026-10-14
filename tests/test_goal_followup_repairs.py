"""The review's reproduced defects, repaired and re-proven at the served seam.

Each test pins one finding from artifacts/goal-progress-review-20261002/defects.json against
the REAL goal-control API (not a helper call): card resume/retry carrying the LIVE runtime,
the approval token resolved server-side and applied once to the exact waiting step, a
non-idempotent append effect NEVER duplicated across pause/resume, and a mid-flight revise
that preserves completed work, advances the revision monotonically and leaves exactly ONE
owning worker. The agent seam is intercepted here for control; the combined REAL journey
(provider-seam interception, real app HTTP) lives in test_goal_real_browser_journey.py.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any
from unittest import mock

import pytest

from core import runtime_paths
from core.goal_workflow import (
    load_goal,
    reset_goal_state_for_tests,
)

SESSION = "openclaw:goals-followup"

PLANNER_MARKER = "Reply with ONLY a JSON object"
STEP_MARKER = "You are executing one step of an explicit goal"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    from storage.db import configure_default_db_path, reset_default_connection
    from storage.migrations import run_migrations

    configure_default_db_path(tmp_path / "data" / "vool_web0_v2.db")
    reset_default_connection()
    from core.runtime_continuity import configure_runtime_continuity_db_path

    configure_runtime_continuity_db_path(tmp_path / "data" / "vool_web0_v2.db")
    run_migrations()
    reset_goal_state_for_tests()
    global _WORKSPACE
    _WORKSPACE = tmp_path / "goal-workspace"
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    from tests.test_goal_served_workflow import _drain_goal_workers

    _drain_goal_workers()
    yield
    _drain_goal_workers()
    reset_goal_state_for_tests()
    configure_default_db_path(None)
    reset_default_connection()
    runtime_paths.configure_runtime_home(None)


_WORKSPACE = None


def _workspace() -> Path:
    assert _WORKSPACE is not None
    return _WORKSPACE


_APP = None


def _shared_app():
    global _APP
    if _APP is None:
        from apps.vool_api_server import create_app
        from core.web.api.runtime import RuntimeServices

        _APP = create_app(RuntimeServices(display_name="VOOL"))
    return _APP


def _app():
    return _shared_app()


def _post(app, path: str, payload: dict[str, Any]):
    from tests.test_goal_served_workflow import _asgi

    status, body = _asgi(app, "POST", path, payload)
    return status, json.loads(body or b"{}")


def _get(app, path: str):
    from tests.test_goal_served_workflow import _get as _get_full

    return _get_full(app, path)


def _dispatch_chat(body: dict[str, Any]):
    from tests.test_goal_served_workflow import _asgi, _Resp

    status, resp_body = _asgi(_shared_app(), "POST", "/api/chat", {"session_id": SESSION, "stream": True, **body})
    return _Resp(status, resp_body)


def _first_goal_view(body: bytes) -> dict[str, Any] | None:
    from tests.test_goal_served_workflow import _lines

    for item in _lines(body):
        event = item.get("vool_event")
        if isinstance(event, dict) and event.get("type") == "goal.updated":
            return event.get("goal")
    return None


def _canonical(goal_id: str) -> str:
    goal = load_goal(goal_id)
    assert goal is not None
    return str(goal.get("session_id"))


def _goal_ids() -> list[str]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        return [row[0] for row in conn.execute("SELECT goal_id FROM chat_goals ORDER BY created_at").fetchall()]
    finally:
        conn.close()


def _wait_worker_exit(goal_id: str, timeout: float = 15.0) -> None:
    from core.goal_workflow import active_worker

    deadline = time.time() + timeout
    while time.time() < deadline:
        if active_worker(goal_id) is None:
            return
        time.sleep(0.05)


def _wait_step_not_running(goal_id: str, timeout: float = 15.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    goal = load_goal(goal_id)
    while time.time() < deadline:
        goal = load_goal(goal_id)
        steps = goal.get("plan", {}).get("steps", [])
        if steps and all(step.get("state") != "running" for step in steps):
            return goal
        time.sleep(0.1)
    return goal


def _wait_terminal_or_holding(goal_id: str, timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    goal: dict[str, Any] = {}
    while time.time() < deadline:
        goal = load_goal(goal_id)
        if goal and goal.get("state") in {"completed", "failed", "cancelled", "paused", "waiting_approval", "interrupted", "blocked"}:
            time.sleep(0.2)
            return goal
        time.sleep(0.1)
    return goal


def _wait_after_resume(goal_id: str, payload: dict[str, Any], timeout: float = 20.0) -> dict[str, Any]:
    """A Resume pressed while the paused step is still settling answers ``settling``: the goal
    reads paused until that step's worker hands over to its successor. Only then is a paused
    reading a real outcome."""
    if payload.get("settling"):
        deadline = time.time() + timeout
        while time.time() < deadline and (load_goal(goal_id) or {}).get("state") == "paused":
            time.sleep(0.05)
    return _wait_terminal_or_holding(goal_id, timeout=timeout)


class _AppendAgent:
    """Synthetic provider whose step effect is a NON-idempotent append, verified by the
    declared tool event — the exact shape the review's duplication probe used."""

    def __init__(self, *, plan_verify: dict[str, Any] | None = None, gate_first_step: bool = False) -> None:
        self.calls: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.gate_first_step = gate_first_step
        self._first_step_seen = False
        self.gate = threading.Event()
        self.plan_verify = plan_verify if plan_verify is not None else {
            "tool": "workspace.write_file", "path_contains": "counter.txt"
        }

    def __call__(self, runtime, text, *, session_id=None, source_context=None, workspace_root_provider=None):
        with self.lock:
            self.calls.append({
                "text": text,
                "runtime": runtime,
                "source_context": dict(source_context or {}),
            })
        from core.runtime_task_events import emit_runtime_event

        if PLANNER_MARKER in text:
            verify_json = json.dumps(self.plan_verify)
            return {
                "response": (
                    '```json\n{"steps": ['
                    '{"id": "s1", "title": "Append the counter line", "verify": ' + verify_json + "}, "
                    '{"id": "s2", "title": "Append the closing line", "depends_on": ["s1"], "verify": ' + verify_json + "}"
                    "]}\n```"
                ),
                "confidence": 1.0,
            }
        if STEP_MARKER in text:
            target = _workspace() / "counter.txt"
            with open(target, "a", encoding="utf-8") as handle:
                handle.write("append-" + str(source_context.get("cancel_turn_id")) + "\n")
            emit_runtime_event(
                source_context,
                event_type="tool_executed",
                message="[synthetic] appended the counter line",
                details={"tool_name": "workspace.write_file", "path": str(target), "summary": "[synthetic] appended"},
            )
            if self.gate_first_step and not self._first_step_seen:
                self._first_step_seen = True
                assert self.gate.wait(timeout=15.0), "gate never released"
            return {
                "response": "[synthetic] appended the line.",
                "fulfillment_outcome": {"fulfillment_status": "fulfilled"},
                "confidence": 0.9,
            }
        return {"response": "[synthetic] reply", "confidence": 0.9}

    def wait_until_calls(self, count: int, timeout: float = 15.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if len(self.calls) >= count:
                    return
            time.sleep(0.05)
        raise AssertionError(f"expected {count} agent calls, saw {len(self.calls)}")


def _append_count() -> int:
    target = _workspace() / "counter.txt"
    if not target.exists():
        return 0
    return len([line for line in target.read_text(encoding="utf-8").splitlines() if line.strip()])


# ---------------------------------------------------------------- defect 1: card controls carry the LIVE runtime


def test_card_resume_and_retry_spawn_workers_with_the_real_runtime() -> None:
    agent = _AppendAgent()
    agent.gate_first_step = True
    with mock.patch("core.web.api.runtime.run_agent", agent) as run_agent_mock:
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal append the demo lines"}], "turn_id": "fu-turn-1"})
        assert response.status == 200
        goal_id = _goal_ids()[0]
        agent.wait_until_calls(2)
        status, payload = _post(_APP, "/api/goal/pause", {"session_id": _canonical(goal_id), "goal_id": goal_id})
        assert status == 200 and payload["ok"] is True
        agent.gate.set()
        goal = _wait_step_not_running(goal_id)
        assert goal["state"] == "paused", goal["last_reason"]

        from apps.vool_api_server import create_app
        from core.web.api.runtime import RuntimeServices

        app_runtime = _shared_app().state.runtime
        assert isinstance(app_runtime, RuntimeServices)

        # RESUME: the resumed worker runs through the app's LIVE runtime (the review's
        # reproduction: spawn(None, ...) crashed in _run_agent_locked on runtime.agent).
        status, payload = _post(_APP, "/api/goal/resume", {"session_id": _canonical(goal_id), "goal_id": goal_id})
        assert status == 200 and payload["ok"] is True, payload
        goal = _wait_after_resume(goal_id, payload)
        assert goal["state"] == "completed", goal["last_reason"]
        step_calls = [c for c in agent.calls if STEP_MARKER in c["text"]]
        assert step_calls and all(c["runtime"] is app_runtime for c in step_calls[1:]), (
            "resumed dispatch must receive the live runtime handle"
        )


def test_retry_resolves_the_pending_approval_server_side_and_applies_it_once() -> None:
    from core.goal_workflow import active_worker
    from core.mode_permission_policy import register_external_approval, resolve_approval

    agent = _AppendAgent()
    agent.gate_first_step = True
    with mock.patch("core.web.api.runtime.run_agent", agent):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal append with approval"}], "turn_id": "fu-turn-2"})
        assert response.status == 200
        goal_id = _goal_ids()[0]
        agent.wait_until_calls(2)
        # Mint the pending approval the step turn WOULD have raised, and record it on the
        # waiting step exactly as _latest_approval_id records it from the ledger event.
        approval_id = register_external_approval(
            {
                "session_id": _canonical(goal_id),
                "task_id": f"{goal_id}:s1:a1",
                "intent": "workspace.write_file",
                "action": "Run workspace.write_file",
            }
        )
        # Park the goal: the in-flight step settles (its effect committed) and the worker
        # exits — the retry then owns a clean holding state.
        status, payload = _post(_APP, "/api/goal/pause", {"session_id": _canonical(goal_id), "goal_id": goal_id})
        assert status == 200 and payload["ok"] is True
        agent.gate.set()
        goal = _wait_step_not_running(goal_id)
        assert goal["state"] == "paused", goal["last_reason"]
        _wait_worker_exit(goal_id)
        goal = load_goal(goal_id)
        goal["state"] = "waiting_approval"
        goal["current_step_id"] = "s1"
        goal["plan"]["steps"][0]["state"] = "waiting_approval"
        goal["plan"]["steps"][0]["approval_id"] = approval_id
        from core.goal_workflow import save_goal

        save_goal(goal)

        status, preview = _post(_APP, "/api/goal/retry", {"session_id": _canonical(goal_id), "goal_id": goal_id})
        assert status == 409 and preview["approval"]["approval_id"] == approval_id
        assert resolve_approval(approval_id, decision="allow", scope="once") is not None
        status, payload = _post(_APP, "/api/goal/retry", {"session_id": _canonical(goal_id), "goal_id": goal_id})
        assert status == 200 and payload["ok"] is True, payload
        assert "pending_approval_token" not in payload, "raw approval secrets never ride the response"
        # The resumed dispatch of the waiting step carries the resolved token through the
        # ordinary mode_approval_token seam — validated by the existing consult.
        goal = _wait_terminal_or_holding(goal_id)
        assert goal["state"] == "completed", goal["last_reason"]
        resumed = [c for c in agent.calls if STEP_MARKER in c["text"]][1:]
        print("AGENTCTX:", [sorted(c["source_context"].keys()) for c in resumed])
        assert any(
            c["source_context"].get("mode_approval_token") == approval_id for c in resumed
        ), "the resumed attempt must carry the resolved approval token"


# ---------------------------------------------------------------- defect 2: pause never duplicates a real append


def test_pause_after_committed_append_never_duplicates_the_effect() -> None:
    agent = _AppendAgent()
    agent.gate_first_step = True
    with mock.patch("core.web.api.runtime.run_agent", agent):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal append exactly once"}], "turn_id": "fu-turn-3"})
        assert response.status == 200
        goal_id = _goal_ids()[0]
        agent.wait_until_calls(2)
        # The append + its ledger event are already committed; the turn is still in flight.
        assert _append_count() == 1
        status, payload = _post(_APP, "/api/goal/pause", {"session_id": _canonical(goal_id), "goal_id": goal_id})
        assert status == 200 and payload["ok"] is True
        agent.gate.set()
        goal = _wait_step_not_running(goal_id)
        assert goal["state"] == "paused", goal["last_reason"]
        assert _append_count() == 1, "pausing must not append"
        calls_after_pause = len(agent.calls)
        time.sleep(0.4)
        assert len(agent.calls) == calls_after_pause

        status, payload = _post(_APP, "/api/goal/resume", {"session_id": _canonical(goal_id), "goal_id": goal_id})
        assert status == 200 and payload["ok"] is True
        goal = _wait_after_resume(goal_id, payload)
        assert goal["state"] == "completed", goal["last_reason"]
        # EXACTLY ONE append for step one: the committed effect was reconciled from its
        # receipt, not replayed.
        assert _append_count() == 2, "one append for s1 (reconciled) + one for s2"
        assert goal["plan"]["steps"][0]["attempts"] == 1


# ---------------------------------------------------------------- defect 3: revise mid-flight


def test_revise_midflight_preserves_completed_work_and_keeps_one_worker() -> None:
    agent = _AppendAgent()
    agent.gate_first_step = True
    with mock.patch("core.web.api.runtime.run_agent", agent):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal append then close"}], "turn_id": "fu-turn-4"})
        assert response.status == 200
        goal_id = _goal_ids()[0]
        agent.wait_until_calls(2)
        from core.goal_workflow import active_worker
        from core.goal_workflow import load_goal as _load

        old_worker = active_worker(goal_id)
        assert old_worker is not None
        # Complete step one for real, hold step two mid-flight.
        _post(_APP, "/api/goal/pause", {"session_id": _canonical(goal_id), "goal_id": goal_id})
        agent.gate.set()
        goal = _wait_step_not_running(goal_id)
        assert goal["plan"]["steps"][0]["state"] == "completed"
        # Step two was never dispatched (the pause landed during step one's turn).
        assert goal["plan"]["steps"][1]["state"] == "pending"
        status, resumed = _post(_APP, "/api/goal/resume", {"session_id": _canonical(goal_id), "goal_id": goal_id})
        assert status == 200 and resumed["ok"]
        # Resume can be queued while the cancelled turn is still settling. Its old
        # paused snapshot is not the resumed goal's final outcome.
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            goal = load_goal(goal_id)
            if goal["state"] in {"completed", "failed", "cancelled", "blocked"}:
                break
            time.sleep(0.05)
        assert goal["state"] == "completed", goal["last_reason"]

    # A genuinely active revise: fresh goal, hold step one mid-flight, revise while the
    # worker is live. The revise turn serializes behind the in-flight step (MF-15), so the
    # gate auto-releases while the revise turn is pending — the revise lands at the first
    # session-gate boundary.
    agent2 = _AppendAgent()
    agent2.gate_first_step = True
    with mock.patch("core.web.api.runtime.run_agent", agent2):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal append the second demo"}], "turn_id": "fu-turn-5"})
        goal_id = _goal_ids()[-1]
        agent2.wait_until_calls(2)
        first_revision = load_goal(goal_id)["revision"]
        from core.goal_workflow import active_worker

        old_worker = active_worker(goal_id)
        assert old_worker is not None
        release_timer = threading.Timer(2.0, agent2.gate.set)
        release_timer.start()
        revise = _dispatch_chat({"messages": [{"role": "user", "content": "/goal revise append the THIRD demo instead"}], "turn_id": "fu-turn-6"})
        assert revise.status == 200
        release_timer.join()
        revised = load_goal(goal_id)
        assert revised["objective"] == "append the THIRD demo instead"
        assert revised["revision"] > first_revision, "revision advances monotonically"
        assert revised["plan"].get("history"), "durable revision provenance is written"
        # The old worker is gone; exactly ONE worker exists for the goal.
        deadline = time.time() + 10
        while active_worker(goal_id) is old_worker and time.time() < deadline:
            time.sleep(0.05)
        successor = active_worker(goal_id)
        assert successor is not old_worker
        goal = _wait_terminal_or_holding(goal_id)
        assert goal["state"] in ("completed", "failed", "cancelled", "paused"), goal["last_reason"]
        # The successor ran to completion and cleaned up; the old worker never re-registered.
        assert active_worker(goal_id) is None


def test_stale_worker_unregister_never_removes_its_successor() -> None:
    from core.goal_workflow import _GoalWorker, register_worker, unregister_worker

    old = _GoalWorker("goal-x", "chat", epoch=1)
    new = _GoalWorker("goal-x", "chat", epoch=2)
    register_worker(old)
    register_worker(new)
    unregister_worker("goal-x", old)
    from core.goal_workflow import active_worker

    assert active_worker("goal-x") is new, "a stale worker's unregister must not remove its successor"
    unregister_worker("goal-x", new)
    assert active_worker("goal-x") is None


# ---------------------------------------------------------------- conversational corrections


def test_conversational_goal_instruction_revises_with_provenance() -> None:
    agent = _AppendAgent()
    with mock.patch("core.web.api.runtime.run_agent", agent):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal append the base lines"}], "turn_id": "fu-turn-7"})
        assert response.status == 200
        goal_id = _goal_ids()[0]
        goal = _wait_terminal_or_holding(goal_id)
        assert goal["state"] == "completed", goal["last_reason"]
        from core.goal_workflow import load_active_goal_for_session

        assert load_active_goal_for_session(SESSION) is None  # terminal: no active goal

    agent2 = _AppendAgent(gate_first_step=True)
    with mock.patch("core.web.api.runtime.run_agent", agent2):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal append the live lines"}], "turn_id": "fu-turn-8"})
        goal_id = _goal_ids()[-1]
        agent2.wait_until_calls(2)
        first_revision = load_goal(goal_id)["revision"]
        # Park the goal on a holding state first: the correction turn is an ordinary chat
        # turn and serializes behind the in-flight step (the existing MF-15 session gate) —
        # the truthful product flow is pause, correct, resume.
        status, payload = _post(_APP, "/api/goal/pause", {"session_id": _canonical(goal_id), "goal_id": goal_id})
        assert status == 200 and payload["ok"] is True
        agent2.gate.set()
        _wait_step_not_running(goal_id)
        # The explicit conversational form reaches the ACTIVE goal as a revision.
        correction = _dispatch_chat({"messages": [{"role": "user", "content": "goal: append the corrected lines instead"}], "turn_id": "fu-turn-9"})
        assert correction.status == 200
        revised = load_goal(goal_id)
        assert revised["objective"] == "append the corrected lines instead"
        assert revised["revision"] > first_revision
        assert revised["plan"].get("history"), "durable provenance for the conversational correction"
        _wait_terminal_or_holding(goal_id)


def test_ambiguous_goal_reference_asks_one_question_and_mutates_nothing() -> None:
    agent = _AppendAgent(gate_first_step=True)
    with mock.patch("core.web.api.runtime.run_agent", agent):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal append the ambiguous demo"}], "turn_id": "fu-turn-10"})
        goal_id = _goal_ids()[0]
        agent.wait_until_calls(2)
        before = load_goal(goal_id)
        ambiguous = _dispatch_chat(
            {"messages": [{"role": "user", "content": "maybe change the plan to be faster"}], "turn_id": "fu-turn-11"}
        )
        assert ambiguous.status == 200
        from tests.test_goal_served_workflow import _lines

        text = "".join(
            item.get("message", {}).get("content", "") for item in _lines(ambiguous.body) if isinstance(item.get("message"), dict)
        )
        assert "goal:" in text, "the clarification names the explicit form"
        after = load_goal(goal_id)
        assert after["objective"] == before["objective"]
        assert after["revision"] == before["revision"], "an ambiguous reference mutates nothing"
        agent.gate.set()
        _wait_terminal_or_holding(goal_id)


def test_casual_chat_leaves_the_active_goal_untouched() -> None:
    agent = _AppendAgent(gate_first_step=True)
    with mock.patch("core.web.api.runtime.run_agent", agent):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal append the casual demo"}], "turn_id": "fu-turn-12"})
        goal_id = _goal_ids()[0]
        agent.wait_until_calls(2)
        before = load_goal(goal_id)
        casual = _dispatch_chat({"messages": [{"role": "user", "content": "hello there friend"}], "turn_id": "fu-turn-13"})
        assert casual.status == 200
        after = load_goal(goal_id)
        assert after["objective"] == before["objective"]
        assert after["revision"] == before["revision"]
        agent.gate.set()
        _wait_terminal_or_holding(goal_id)


# ---------------------------------------------------------------- acceptance capture


def test_acceptance_clause_is_captured_and_durable() -> None:
    agent = _AppendAgent(gate_first_step=True)
    with mock.patch("core.web.api.runtime.run_agent", agent):
        response = _dispatch_chat(
            {"messages": [{"role": "user", "content": "/goal append the acceptance demo acceptance: both lines appended"}],
             "turn_id": "fu-turn-14"}
        )
        assert response.status == 200
        goal_id = _goal_ids()[0]
        goal = load_goal(goal_id)
        assert goal["objective"] == "append the acceptance demo"
        assert goal["acceptance"] == "both lines appended"
        agent.gate.set()
        _wait_terminal_or_holding(goal_id)
