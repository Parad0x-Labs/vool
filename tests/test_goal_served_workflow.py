"""The /goal workflow driven through the REAL served ingress — not a helper called directly.

Every journey here posts to ``/api/chat`` through ``dispatch_post`` exactly as the composer's
stream does, with the goal worker's step dispatches intercepted at the one seam production
itself uses (``core.web.api.runtime.run_agent``). The scripted provider is labeled synthetic;
the effects it performs are real file writes into a disposable workspace, and the step
verification reads the real runtime event ledger. The card's hydration door
(``/api/goal/state``) and the loopback control door (``/api/goal/*``) are driven over HTTP
shapes, including the foreign-chat refusal.
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

SESSION = "openclaw:goals-served-a"
OTHER = "openclaw:goals-served-b"

PLANNER_MARKER = "Reply with ONLY a JSON object"
STEP_MARKER = "You are executing one step of an explicit goal"


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    # Per-test home AND per-test store: the session conftest points the default DB at a
    # shared session path, which would leak goals between tests. The same pattern
    # tests/test_goal_workflow.py uses, applied to the served rig.
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    runtime_paths.configure_runtime_home(tmp_path)
    from storage.db import configure_default_db_path, reset_default_connection
    from storage.migrations import run_migrations

    configure_default_db_path(tmp_path / "data" / "vool_web0_v2.db")
    reset_default_connection()
    from core.runtime_continuity import configure_runtime_continuity_db_path

    configure_runtime_continuity_db_path(tmp_path / "data" / "vool_web0_v2.db")
    run_migrations()
    _drain_goal_workers()
    reset_goal_state_for_tests()
    global _WORKSPACE
    _WORKSPACE = tmp_path / "goal-workspace"
    _WORKSPACE.mkdir(parents=True, exist_ok=True)
    yield
    _drain_goal_workers()
    reset_goal_state_for_tests()
    configure_default_db_path(None)
    reset_default_connection()
    runtime_paths.configure_runtime_home(None)


def _drain_goal_workers(timeout: float = 20.0) -> None:
    """Let detached goal workers from a previous test settle BEFORE the store moves.

    A worker finishing after its test would otherwise write its old goal row into the NEXT
    test's fresh database — an order-dependence this rig refuses."""
    from core.goal_workflow import _WORKERS

    deadline = time.time() + timeout
    while time.time() < deadline:
        alive = [goal_id for goal_id, worker in list(_WORKERS.items()) if not worker.finished.is_set()]
        if not alive:
            return
        time.sleep(0.1)
    raise AssertionError(f"goal workers still running after {timeout}s: {alive}")


def _app():
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    return create_app(RuntimeServices(display_name="VOOL"))


def _asgi(app, method: str, path: str, payload: dict[str, Any] | None = None, client_host: str = "127.0.0.1"):
    """One request through the REAL app with a streaming-capable ASGI transport.

    The repo's raw asgi_harness cannot pull a StreamingResponse body (measured: even an
    ordinary streamed chat turn returns zero bytes through it), so this uses the same
    httpx.ASGITransport pattern tests/test_daemon_survives_concurrent_load.py uses.
    """
    import anyio
    import httpx

    headers = {"Content-Type": "application/json"}
    if payload is not None:
        headers["Origin"] = "http://127.0.0.1:8080"

    def _drive() -> tuple[int, bytes]:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 123))
        anyio_backend = "asyncio"

        async def _run():
            async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1", headers=headers) as client:
                if method == "GET":
                    response = await client.get(path, timeout=60.0)
                else:
                    response = await client.post(path, content=json.dumps(payload or {}).encode("utf-8"), timeout=60.0)
                return response.status_code, response.content

        return anyio.run(_run, backend=anyio_backend)

    return _drive()


class _Resp:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = int(status)
        self.body = body


def _dispatch_chat(body: dict[str, Any], *, client_host: str = "127.0.0.1"):
    status, resp_body = _asgi(_shared_app(), "POST", "/api/chat", {"session_id": SESSION, "stream": True, **body})
    return _Resp(status, resp_body)


_APP = None


def _shared_app():
    global _APP
    if _APP is None:
        _APP = _app()
    return _APP


_WORKSPACE = None


def _workspace() -> Path:
    assert _WORKSPACE is not None, "workspace not set up"
    return _WORKSPACE


def _post(app, path: str, payload: dict[str, Any]):
    status, body = _asgi(app or _shared_app(), "POST", path, payload)
    return status, json.loads(body or b"{}")


def _get(app, path: str):
    from tests.asgi_harness import asgi_request

    status, _headers, body = asgi_request(app or _shared_app(), method="GET", path=path, headers={"Host": "127.0.0.1"})
    return status, json.loads(body or b"{}")


def _dispatch_chat_direct(body: dict[str, Any], *, client_host: str, run_agent_provider=None):
    """A chat turn with a caller-controlled peer address (the ASGI transport pins loopback).

    ``run_agent_provider`` must be passed explicitly for buffered turns: dispatch_post's
    default binds the original ``run_agent`` at import time, so a module-level patch cannot
    reach it."""
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    response = dispatch_post(
        path="/api/chat",
        body={"session_id": SESSION, "stream": False, **body},
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: str(_workspace()),
        client_host=client_host,
        run_agent_provider=run_agent_provider or (lambda runtime, text, **kw: {"response": "[synthetic] ordinary chat reply", "confidence": 0.9}),
    )
    return _Resp(response.status, response.body or b"")


def _first_goal_view(body: bytes) -> dict[str, Any] | None:
    for item in _lines(body):
        event = item.get("vool_event")
        if isinstance(event, dict) and event.get("type") == "goal.updated":
            return event.get("goal")
    return None


def _wait_goal_terminal_or_holding(goal_id: str, timeout: float = 20.0) -> dict[str, Any]:
    """Wait for the detached goal worker to reach a terminal or holding state."""
    deadline = time.time() + timeout
    last: dict[str, Any] = {}
    while time.time() < deadline:
        goal = load_goal(goal_id)
        assert goal is not None, "goal row vanished"
        last = goal
        if goal["state"] in {"completed", "failed", "cancelled", "paused", "waiting_approval", "interrupted"}:
            time.sleep(0.2)
            return goal
        time.sleep(0.1)
    return last


def _lines(body: bytes) -> list[dict[str, Any]]:
    out = []
    for line in body.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


class _ScriptedAgent:
    """The synthetic provider: deterministic plan + step answers, real file effects.

    Every answer it produces is labeled synthetic in the transcript, and its step answers
    record the same ``tool_executed`` events the real tool loop records, so verification
    reads the honest ledger shape.
    """

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.step_delay_event: threading.Event | None = None
        self.step_gate: threading.Event | None = None
        self.fail_step_writes: set[str] = set()
        self.fail_repair_too: bool = False

    def __call__(self, runtime, text, *, session_id=None, source_context=None, workspace_root_provider=None):
        with self.lock:
            self.calls.append({"text": text, "session_id": session_id, "source_context": dict(source_context or {})})
        from core.runtime_task_events import emit_runtime_event

        if PLANNER_MARKER in text:
            return {
                "response": (
                    '```json\n{"steps": ['
                    '{"id": "s1", "title": "Write the demo page", "verify": {"tool": "workspace.write_file", "path_contains": "index.html"}}, '
                    '{"id": "s2", "title": "Write the README", "depends_on": ["s1"], "verify": {"tool": "workspace.write_file", "path_contains": "README.md"}}'
                    "]}\n```"
                ),
                "confidence": 1.0,
            }
        if STEP_MARKER in text:
            step_line = next((l for l in text.splitlines() if l.startswith("Step ")), "")
            step_title = step_line.split(": ", 1)[-1].strip()
            name = "index.html" if "index.html" in text else ("README.md" if "README" in text else "goal-file.txt")
            target = _workspace() / name
            is_repair = "previous attempt" in text
            if name in self.fail_step_writes and (self.fail_repair_too or not is_repair):
                # The declared effect never materializes: no tool event, no file.
                return {
                    "response": f"[synthetic] I thought about writing {name} but did not.",
                    "fulfillment_outcome": {"fulfillment_status": "fulfilled"},
                    "confidence": 0.9,
                }
            target.write_text(f"<html>{step_title}</html>", encoding="utf-8")
            if self.step_gate is not None:
                # Hold the step mid-flight so a pause can race a real in-flight turn.
                assert self.step_gate.wait(timeout=15.0), "step gate never released"
            if self.step_delay_event is not None:
                self.step_delay_event.wait(timeout=1.0)
            cancelled = bool((source_context or {}).get("cancel_event") and source_context["cancel_event"].is_set())
            emit_runtime_event(
                source_context,
                event_type="tool_executed",
                message=f"[synthetic] wrote {name}",
                details={"tool_name": "workspace.write_file", "path": str(target), "summary": f"[synthetic] wrote {name}"},
            )
            if cancelled:
                return {
                    "response": f"[synthetic] step cancelled while writing {name}",
                    "fulfillment_outcome": {"fulfillment_status": "cancelled"},
                    "confidence": 0.9,
                }
            return {
                "response": f"[synthetic] wrote {name} with the demo content.",
                "fulfillment_outcome": {"fulfillment_status": "fulfilled"},
                "confidence": 0.9,
            }
        return {"response": "[synthetic] ordinary chat reply", "confidence": 0.9}

    def wait_until_calls(self, count: int, timeout: float = 15.0) -> None:
        deadline = time.time() + timeout
        while time.time() < deadline:
            with self.lock:
                if len(self.calls) >= count:
                    return
            time.sleep(0.05)
        raise AssertionError(f"expected {count} agent calls, saw {len(self.calls)}")


def _goal_ids() -> list[str]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        return [row[0] for row in conn.execute("SELECT goal_id FROM chat_goals ORDER BY created_at").fetchall()]
    finally:
        conn.close()


# ---------------------------------------------------------------- journey 1: start, plan, execute, complete


def test_goal_start_plans_executes_and_completes_with_real_effects() -> None:
    agent = _ScriptedAgent()
    with mock.patch("core.web.api.runtime.run_agent", agent):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal create the demo site files"}], "turn_id": "goal-turn-1"})
        assert response.status == 200
        lines = _lines(response.body)
        # The control reply is a real streamed turn with a final done chunk.
        assert any(item.get("done") is True for item in lines)
        goal_views = [item["vool_event"]["goal"] for item in lines if item.get("vool_event", {}).get("type") == "goal.updated"]
        assert goal_views and goal_views[0]["objective"] == "create the demo site files"
        created = goal_views[0]
        goal = _wait_goal_terminal_or_holding(created["goal_id"])
        assert goal["state"] == "completed", goal["last_reason"]
        # Both steps completed through the DEPENDENCY order (s2 declared s1).
        states = {s["id"]: s["state"] for s in goal["plan"]["steps"]}
        assert states == {"s1": "completed", "s2": "completed"}
        # The effects are real files in the disposable workspace.
        assert (_workspace() / "index.html").exists()
        assert (_workspace() / "README.md").exists()
        view = goal
        from core.goal_workflow import goal_public_view

        public = goal_public_view(goal)
        assert (public["completed"], public["total"]) == (2, 2)
        assert public["elapsed_seconds"] >= 0.0
        # The durable ledger carries the goal events AND the steps' real tool events.
        from core.runtime_continuity import list_runtime_session_events

        import core.runtime_continuity as _rc

        canonical = created["session_id"]
        rows = list_runtime_session_events(canonical, after_seq=0, limit=200)
        types = [r["event_type"] for r in rows]
        assert "goal_updated" in types
        tool_rows = [r for r in rows if r.get("tool_name") == "workspace.write_file"]
        assert len(tool_rows) >= 2


def test_bare_goal_and_status_are_inline_usage_not_goals() -> None:
    agent = _ScriptedAgent()
    with mock.patch("core.web.api.runtime.run_agent", agent):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal"}], "turn_id": "goal-turn-usage"})
        assert response.status == 200
        text = "".join(
            item.get("message", {}).get("content", "") for item in _lines(response.body) if isinstance(item.get("message"), dict)
        )
        assert "/goal <" in text or "Set an explicit goal" in text
        assert _goal_ids() == []
        # status with no goal
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal status"}], "turn_id": "goal-turn-status"})
        assert response.status == 200
        assert _goal_ids() == []
        # an ordinary message stays an ordinary model turn (buffered lane; the streamed lane's
        # A7 seal needs a real finalize_answer the synthetic provider deliberately lacks)
        from core.web.api.service import dispatch_post

        captured_plain: list[str] = []
        def plain_agent(runtime, text, *, session_id=None, source_context=None, workspace_root_provider=None):
            captured_plain.append(text)
            return {"response": "[synthetic] ordinary chat reply", "confidence": 0.9}

        response = _dispatch_chat_direct(
            {"messages": [{"role": "user", "content": "hello there"}], "turn_id": "plain-1"},
            client_host="127.0.0.1",
            run_agent_provider=plain_agent,
        )
        assert response.status == 200
        assert captured_plain == ["hello there"]


def test_duplicate_start_asks_one_clarification_and_never_restarts() -> None:
    agent = _ScriptedAgent()
    agent.step_gate = threading.Event()
    with mock.patch("core.web.api.runtime.run_agent", agent):
        first = _dispatch_chat({"messages": [{"role": "user", "content": "/goal the first objective"}], "turn_id": "goal-turn-d1"})
        assert first.status == 200
        goal_id = _goal_ids()[0]
        # While step one holds mid-flight, a second /goal must clarify, not start or replace.
        second = _dispatch_chat({"messages": [{"role": "user", "content": "/goal a different objective"}], "turn_id": "goal-turn-d2"})
        assert second.status == 200
        text = "".join(item.get("message", {}).get("content", "") for item in _lines(second.body) if isinstance(item.get("message"), dict))
        assert "already active" in text
        assert _goal_ids() == [goal_id], "no second goal row may be created"
        # revise is the real correction path: same row, new objective, plan re-derived.
        agent.step_gate.set()
        revise = _dispatch_chat({"messages": [{"role": "user", "content": "/goal revise the sharpened objective"}], "turn_id": "goal-turn-d3"})
        assert revise.status == 200
        agent.step_gate = None
        goal = _wait_goal_terminal_or_holding(goal_id)
        assert goal["objective"] == "the sharpened objective"


# ---------------------------------------------------------------- journey 2: failing verification repaired, then honest failure


def test_failed_verification_repairs_then_fails_honestly_without_completed_badge() -> None:
    agent = _ScriptedAgent()
    agent.fail_step_writes = {"index.html"}
    with mock.patch("core.web.api.runtime.run_agent", agent):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal write the demo site"}], "turn_id": "goal-turn-f1"})
        assert response.status == 200
        goal_id = _goal_ids()[0]
        goal = _wait_goal_terminal_or_holding(goal_id)
        # Attempt 1 fails verification; the bounded repair (attempt 2) writes for real and
        # the goal completes — no premature completed badge existed between attempts.
        assert goal["state"] == "completed"
        states = {s["id"]: (s["state"], s["attempts"]) for s in goal["plan"]["steps"]}
        assert states["s1"][1] == 2, states
        assert (_workspace() / "index.html").exists()

    # Now a permanently failing step: both attempts miss the verification.
    agent2 = _ScriptedAgent()
    agent2.fail_step_writes = {"index.html", "README.md"}
    agent2.fail_repair_too = True
    (_workspace() / "index.html").unlink(missing_ok=True)
    with mock.patch("core.web.api.runtime.run_agent", agent2):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal write the demo site again"}], "turn_id": "goal-turn-f2"})
        assert response.status == 200
        goal_id = _goal_ids()[-1]
        goal = _wait_goal_terminal_or_holding(goal_id)
        assert goal["state"] == "failed"
        failed_steps = [s for s in goal["plan"]["steps"] if s["state"] == "failed"]
        assert failed_steps and failed_steps[0]["attempts"] == 2
        assert "verification not met" in (failed_steps[0]["reason"] or "")
        from core.goal_workflow import goal_public_view

        public = goal_public_view(goal)
        assert public["completed"] == 0, "a failed step must never count as completed"


# ---------------------------------------------------------------- journey 3: pause, zero dispatch, resume, stop


def test_pause_stops_dispatch_then_resume_completes_and_stop_terminates() -> None:
    agent = _ScriptedAgent()
    agent.step_gate = threading.Event()
    with mock.patch("core.web.api.runtime.run_agent", agent):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal write the pause demo"}], "turn_id": "goal-turn-p1"})
        assert response.status == 200
        created = _first_goal_view(response.body)
        assert created is not None
        goal_id = _goal_ids()[0]
        # Wait until step one is genuinely in flight, then pause.
        agent.wait_until_calls(2)  # planner + step 1
        status, payload = _post(_APP, "/api/goal/pause", {"session_id": created["session_id"], "goal_id": goal_id})
        assert status == 200 and payload["ok"] is True, payload
        agent.step_gate.set()  # let the in-flight turn settle
        deadline = time.time() + 15
        while time.time() < deadline:
            goal = load_goal(goal_id)
            if goal["plan"]["steps"][0]["state"] != "running":
                break
            time.sleep(0.1)
        goal = _wait_goal_terminal_or_holding(goal_id)
        assert goal["state"] == "paused", goal["last_reason"]
        # The settled in-flight turn is truthfully classified: its declared effect already
        # verifiably committed, so the step reads completed (never replayed on resume).
        assert goal["plan"]["steps"][0]["state"] == "completed"
        calls_after_pause = len(agent.calls)
        time.sleep(0.6)
        assert len(agent.calls) == calls_after_pause, "pause must stop all new dispatch"

        # Resume reconciles the interrupted step against its recorded receipts: the
        # in-flight attempt had already committed its declared effect (real file +
        # ledger event), so it is marked completed from evidence — never re-dispatched.
        calls_before_resume = len(agent.calls)
        status, payload = _post(_APP, "/api/goal/resume", {"session_id": created["session_id"], "goal_id": goal_id})
        assert status == 200 and payload["ok"] is True, payload
        goal = _wait_goal_terminal_or_holding(goal_id)
        assert goal["state"] == "completed", goal["last_reason"]
        assert goal["plan"]["steps"][0]["state"] == "completed"
        assert goal["plan"]["steps"][0]["attempts"] == 1, "a committed effect is reconciled, never replayed"
        # Only step TWO was newly dispatched after the resume (planner + settled s1 + s2).
        assert len(agent.calls) == calls_before_resume + 1

        # A terminal goal refuses further control and further dispatch.
        calls_before = len(agent.calls)
        status, payload = _post(_APP, "/api/goal/pause", {"session_id": created["session_id"], "goal_id": goal_id})
        assert status == 409
        assert len(agent.calls) == calls_before

    # Stop on a fresh goal, mid-flight, prevents any new work after acknowledgement.
    agent2 = _ScriptedAgent()
    agent2.step_gate = threading.Event()
    with mock.patch("core.web.api.runtime.run_agent", agent2):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal write the stop demo"}], "turn_id": "goal-turn-p2"})
        created = _first_goal_view(response.body)
        assert created is not None
        goal_id = _goal_ids()[-1]
        agent2.wait_until_calls(2)
        status, payload = _post(_APP, "/api/goal/stop", {"session_id": created["session_id"], "goal_id": goal_id})
        assert status == 200 and payload["ok"] is True
        agent2.step_gate.set()
        goal = _wait_goal_terminal_or_holding(goal_id)
        assert goal["state"] == "cancelled"
        calls = len(agent2.calls)
        time.sleep(0.5)
        assert len(agent2.calls) == calls, "stop prevents all new dispatch after acknowledgement"


# ---------------------------------------------------------------- journey 4: hydration, foreign chat, restart


def test_state_door_hydrates_and_refuses_foreign_chat_controls() -> None:
    status, payload = _get(_APP, f"/api/goal/state?session={OTHER}")
    assert status == 200 and payload["goal"] is None

    agent = _ScriptedAgent()
    agent.step_gate = threading.Event()
    with mock.patch("core.web.api.runtime.run_agent", agent):
        response = _dispatch_chat({"messages": [{"role": "user", "content": "/goal the hydration demo"}], "turn_id": "goal-turn-h1"})
        assert response.status == 200
        created = _first_goal_view(response.body)
        assert created is not None
        goal_id = _goal_ids()[0]
        agent.wait_until_calls(2)
        status, payload = _get(_APP, f"/api/goal/state?session={created['session_id']}")
        assert status == 200
        view = payload["goal"]
        assert view["goal_id"] == goal_id
        assert view["state"] in {"running", "planning"}
        assert view["session_id"] == created["session_id"]
        # foreign chat sees nothing and cannot control it
        status, payload = _get(_APP, f"/api/goal/state?session={OTHER}")
        assert payload["goal"] is None
        status, payload = _post(_APP, "/api/goal/stop", {"session_id": OTHER, "goal_id": goal_id})
        assert status == 403
        # controls without any goal row refuse honestly
        status, payload = _post(_APP, "/api/goal/stop", {"session_id": OTHER, "goal_id": "goal-nope"})
        assert status == 404
        agent.step_gate.set()
        _wait_goal_terminal_or_holding(goal_id)


def test_restart_reconciliation_reads_running_goals_as_interrupted() -> None:
    from core.goal_workflow import (
        build_plan,
        new_goal_id,
        reconcile_on_startup,
        save_goal,
    )

    plan = build_plan([{"id": "s1", "title": "only"}], provenance="derived")
    goal = {
        "goal_id": new_goal_id(),
        "session_id": SESSION,
        "project_id": "",
        "objective": "survive a restart",
        "acceptance": "",
        "plan": plan,
        "state": "running",
        "revision": 1,
        "current_step_id": "s1",
        "created_at": 1.0,
        "updated_at": 1.0,
        "ended_at": None,
        "started_at": 1.0,
        "paused_at": None,
        "paused_seconds": 0.0,
        "created_turn_id": "t",
        "last_reason": "",
        "model": "m",
        "exec_context": {},
    }
    plan["steps"][0]["state"] = "running"
    save_goal(goal)
    # The daemon died: no worker is registered. Startup reconciliation runs the honest sweep.
    assert reconcile_on_startup() >= 1
    status, payload = _get(_APP, f"/api/goal/state?session={SESSION}")
    view = payload["goal"]
    assert view["state"] == "interrupted", "hydration must not resurrect running"
    assert view["steps"][0]["state"] == "interrupted"
    # ...and an explicit resume is the only way back to running.
    status, payload = _post(_APP, "/api/goal/resume", {"session_id": SESSION, "goal_id": goal["goal_id"]})
    assert status == 200 and payload["ok"] is True
    from core.goal_workflow import load_goal

    assert load_goal(goal["goal_id"])["state"] == "running"


def test_remote_peer_goal_text_is_an_ordinary_message() -> None:
    agent = _ScriptedAgent()
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    response = dispatch_post(
        path="/api/chat",
        body={"session_id": SESSION, "stream": False,
              "messages": [{"role": "user", "content": "/goal should not start from outside"}], "turn_id": "goal-turn-remote"},
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: str(_workspace()),
        client_host="10.9.8.7",
        run_agent_provider=agent,
    )
    assert response.status == 200
    assert _goal_ids() == []
    agent.wait_until_calls(1)
    assert agent.calls[0]["text"] == "/goal should not start from outside"
