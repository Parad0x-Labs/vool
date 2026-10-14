"""Goal workflow guards proven through the served doors: /api/chat, /api/goal/*, /api/commands.

Each test drives the real app over HTTP. Only the step-turn seam (``core.web.api.runtime.run_agent``)
is scripted, the same seam the other served goal tests script, so the goal controller, its store,
the private-chat switch, the provider registry and the command registry all run for real.

Covered here: a goal never runs a turn on a paid model by itself; a goal that predates a private
chat stops dispatching while the chat is private and keeps none of its words; two tabs starting a
goal at once make one goal; a Resume pressed while the paused step settles is not lost; card
controls hand step turns the same owner-local and local-only binding the chat door did; a control
through the command registry without the live daemon refuses instead of failing the goal.
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
from core.goal_workflow import load_goal, reset_goal_state_for_tests

PLANNER_MARKER = "Reply with ONLY a JSON object"
STEP_MARKER = "You are executing one step of an explicit goal"
PAID_MODEL = "review-paid-model"


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
    from tests.test_goal_served_workflow import _drain_goal_workers

    _drain_goal_workers()
    reset_goal_state_for_tests()
    yield tmp_path
    _drain_goal_workers()
    reset_goal_state_for_tests()
    configure_default_db_path(None)
    reset_default_connection()
    runtime_paths.configure_runtime_home(None)


_APP = None


def _app():
    global _APP
    if _APP is None:
        from apps.vool_api_server import create_app
        from core.web.api.runtime import RuntimeServices

        _APP = create_app(RuntimeServices(display_name="VOOL"))
    return _APP


def _http(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, bytes]:
    from tests.test_goal_served_workflow import _asgi

    return _asgi(_app(), method, path, payload)


def _post_json(path: str, payload: dict[str, Any]) -> tuple[int, dict[str, Any]]:
    status, body = _http("POST", path, payload)
    return status, json.loads(body or b"{}")


def _chat(session: str, text: str, **extra: Any) -> list[dict[str, Any]]:
    from tests.test_goal_served_workflow import _lines

    status, body = _http(
        "POST",
        "/api/chat",
        {"session_id": session, "stream": True, "messages": [{"role": "user", "content": text}], **extra},
    )
    assert status == 200, body[:300]
    return _lines(body)


def _reply_text(lines: list[dict[str, Any]]) -> str:
    return "".join(str((line.get("message") or {}).get("content") or "") for line in lines)


def _goal_rows() -> list[dict[str, Any]]:
    from storage.db import get_connection

    conn = get_connection()
    try:
        rows = conn.execute("SELECT goal_id, session_id, state FROM chat_goals ORDER BY created_at").fetchall()
    finally:
        conn.close()
    return [{"goal_id": r[0], "session_id": r[1], "state": r[2]} for r in rows]


def _goal_view(lines: list[dict[str, Any]]) -> dict[str, Any] | None:
    for line in lines:
        event = line.get("vool_event")
        if isinstance(event, dict) and event.get("type") == "goal.updated" and isinstance(event.get("goal"), dict):
            return event["goal"]
    return None


def _start(session: str, text: str, agent: _ScriptedSteps, **extra: Any) -> tuple[str, str]:
    """Send a /goal through the chat door; return (goal_id, the chat's canonical id)."""
    view = _goal_view(_chat(session, text, **extra))
    assert view is not None, "the /goal turn carried no goal event"
    if agent.hold_first_step:
        assert agent.step_entered.wait(15), "the first step never started"
    return str(view["goal_id"]), str(view["session_id"])


def _wait(goal_id: str, states: set[str], timeout: float = 20.0) -> dict[str, Any]:
    deadline = time.time() + timeout
    goal: dict[str, Any] = {}
    while time.time() < deadline:
        goal = load_goal(goal_id) or {}
        if goal.get("state") in states and all(s.get("state") != "running" for s in goal.get("plan", {}).get("steps", [])):
            return goal
        time.sleep(0.05)
    return goal


def _wait_worker_gone(goal_id: str, timeout: float = 20.0) -> None:
    from core.goal_workflow import active_worker

    deadline = time.time() + timeout
    while time.time() < deadline and active_worker(goal_id) is not None:
        time.sleep(0.05)


def _bytes_on_disk(root: Path, needle: str) -> list[str]:
    hits = []
    raw = needle.encode("utf-8")
    for path in root.rglob("*"):
        if path.is_file():
            try:
                if raw in path.read_bytes():
                    hits.append(str(path.relative_to(root)))
            except OSError:
                continue
    return hits


class _ScriptedSteps:
    """Plans two steps; holds the first step on a gate when asked; records every context."""

    def __init__(self, *, hold_first_step: bool = False, step_answer: str = "[synthetic] step done.") -> None:
        self.calls: list[dict[str, Any]] = []
        self.lock = threading.Lock()
        self.hold_first_step = hold_first_step
        self.step_entered = threading.Event()
        self.release = threading.Event()
        self.step_answer = step_answer
        self._held = False

    def __call__(self, runtime, text, *, session_id=None, source_context=None, workspace_root_provider=None):
        with self.lock:
            self.calls.append({"text": text, "source_context": dict(source_context or {}), "runtime": runtime})
        if PLANNER_MARKER in text:
            return {
                "response": '{"steps": [{"id": "s1", "title": "First part"}, {"id": "s2", "title": "Second part", "depends_on": ["s1"]}]}',
                "confidence": 1.0,
            }
        if STEP_MARKER in text:
            if self.hold_first_step and not self._held:
                self._held = True
                self.step_entered.set()
                assert self.release.wait(20), "the held step was never released"
            return {"response": self.step_answer, "fulfillment_outcome": {"fulfillment_status": "fulfilled"}, "confidence": 0.9}
        return {"response": "[synthetic] ordinary reply", "confidence": 0.9}

    def step_calls(self) -> list[dict[str, Any]]:
        with self.lock:
            return [call for call in self.calls if STEP_MARKER in call["text"]]

    def planner_calls(self) -> list[dict[str, Any]]:
        with self.lock:
            return [call for call in self.calls if PLANNER_MARKER in call["text"]]


def _seed_paid_model() -> None:
    from core.model_registry import ModelRegistry
    from storage.model_provider_manifest import ModelProviderManifest

    ModelRegistry().register_manifest(
        ModelProviderManifest(
            provider_name="review-cloud",
            model_name=PAID_MODEL,
            source_type="http",
            adapter_type="openai_compatible",
            license_name="Apache-2.0",
            license_reference="https://example.invalid/license",
            weight_location="external",
            runtime_dependency="stub",
            capabilities=["summarize", "classify", "format", "extract", "structured_json"],
            runtime_config={"base_url": "https://api.example.invalid/v1", "timeout_seconds": 30},
            metadata={"cost_class": "paid_cloud"},
            enabled=True,
        )
    )


# ---------------------------------------------------------------- money: no paid turn by itself


def test_a_goal_on_a_paid_model_is_refused_before_anything_runs() -> None:
    _seed_paid_model()
    agent = _ScriptedSteps()
    with mock.patch("core.web.api.runtime.run_agent", agent):
        lines = _chat("goals-review-paid-start", "/goal tidy the notes folder", model=PAID_MODEL, mode="auto")
        time.sleep(0.5)
        for row in _goal_rows():
            _wait_worker_gone(row["goal_id"])
    reply = _reply_text(lines)
    assert "paid" in reply.lower(), reply
    assert _goal_rows() == [], "a goal row was written for a paid-model goal"
    assert agent.calls == [], "a turn ran on the paid model"


def test_a_resumed_goal_never_dispatches_a_step_on_a_paid_model() -> None:
    _seed_paid_model()
    agent = _ScriptedSteps(hold_first_step=True)
    with mock.patch("core.web.api.runtime.run_agent", agent):
        try:
            goal_id, chat = _start("goals-review-paid-resume", "/goal sort the reading list", agent, model="vool", mode="auto")
            status, payload = _post_json("/api/goal/pause", {"session_id": chat, "goal_id": goal_id})
            assert status == 200 and payload["ok"], payload
        finally:
            agent.release.set()
        assert _wait(goal_id, {"paused"})["state"] == "paused"
        _wait_worker_gone(goal_id)
        steps_before = len(agent.step_calls())
        # The owner resumes from a composer that now has a paid model selected.
        _chat(chat, "/goal resume", model=PAID_MODEL, mode="auto")
        goal = _wait(goal_id, {"blocked", "completed", "failed"})
        _wait_worker_gone(goal_id)
    assert len(agent.step_calls()) == steps_before, "a goal step was dispatched on the paid model"
    assert goal["state"] == "blocked", goal.get("last_reason")
    assert "paid" in str(goal.get("last_reason")).lower()


# ---------------------------------------------------------------- private chat


def test_a_goal_stops_while_its_chat_is_private_and_keeps_no_step_words(_isolated_home) -> None:
    from core.private_chat import is_private, set_private

    secret = "QUOKKA-ORBIT-7731"
    agent = _ScriptedSteps(hold_first_step=True, step_answer=f"The private answer is {secret}.")
    with mock.patch("core.web.api.runtime.run_agent", agent):
        try:
            goal_id, chat = _start("goals-review-private", "/goal check the garden plan", agent, model="vool", mode="auto")
            # The owner turns private chat on while the first step runs (the real chat door).
            _chat(chat, "private chat", model="vool", mode="auto")
            assert is_private(chat)
        finally:
            agent.release.set()
        goal = _wait(goal_id, {"paused", "completed", "failed", "blocked"})
        _wait_worker_gone(goal_id)
        time.sleep(0.3)
        assert len(agent.step_calls()) == 1, "a goal step was dispatched while the chat was private"
        assert goal["state"] == "paused", (goal["state"], goal.get("last_reason"))
        assert "private" in str(goal.get("last_reason")).lower()
        hits = _bytes_on_disk(_isolated_home, secret)
        assert hits == [], f"private step words reached disk: {hits}"
        # Resume refuses while the chat is private, without changing the goal.
        status, payload = _post_json("/api/goal/resume", {"session_id": chat, "goal_id": goal_id})
        assert status == 409 and not payload["ok"], payload
        assert load_goal(goal_id)["state"] == "paused"
        assert len(agent.step_calls()) == 1
        # Ending private chat lets the goal resume and finish. Turning it off is answered by the
        # agent turn itself (scripted out here), so the switch is flipped through its owner.
        set_private(chat, False)
        assert not is_private(chat)
        status, payload = _post_json("/api/goal/resume", {"session_id": chat, "goal_id": goal_id})
        assert status == 200 and payload["ok"], payload
        goal = _wait(goal_id, {"completed", "failed", "blocked"})
        _wait_worker_gone(goal_id)
    assert goal["state"] == "completed", goal.get("last_reason")


def test_a_goal_sent_in_a_private_chat_is_refused_and_leaves_no_words_on_disk(_isolated_home) -> None:
    secret = "PELICAN-LANTERN-4410"
    agent = _ScriptedSteps()
    with mock.patch("core.web.api.runtime.run_agent", agent):
        _chat("goals-review-private-start", "private chat", model="vool", mode="auto")
        rows_before = _goal_rows()
        lines = _chat("goals-review-private-start", f"/goal plan the {secret} surprise", model="vool", mode="auto")
        time.sleep(0.3)
    reply = _reply_text(lines).lower()
    assert "private" in reply and "cannot" in reply, reply
    assert _goal_rows() == rows_before == []
    assert not any(STEP_MARKER in call["text"] or PLANNER_MARKER in call["text"] for call in agent.calls)
    hits = _bytes_on_disk(_isolated_home, secret)
    assert hits == [], f"private goal words reached disk: {hits}"


# ---------------------------------------------------------------- two tabs, one goal


def test_two_tabs_sending_a_goal_at_once_make_one_goal() -> None:
    agent = _ScriptedSteps(hold_first_step=True)
    barrier = threading.Barrier(4)
    results: list[Any] = []

    def _send(index: int) -> None:
        barrier.wait()
        results.append(_chat("goals-review-two-tabs", f"/goal plan the trip variant {index}", model="vool", mode="auto"))

    with mock.patch("core.web.api.runtime.run_agent", agent):
        try:
            threads = [threading.Thread(target=_send, args=(i,)) for i in range(4)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(60)
            rows = _goal_rows()
        finally:
            agent.release.set()
        for row in rows:
            _wait(row["goal_id"], {"completed", "failed", "blocked", "paused"})
            _wait_worker_gone(row["goal_id"])
    assert len(results) == 4
    assert len(rows) == 1, f"two tabs made {len(rows)} goals in one chat"
    assert len(agent.planner_calls()) == 1
    replies = [_reply_text(lines) for lines in results]
    assert sum("already active" in reply for reply in replies) == 3, replies


# ---------------------------------------------------------------- resume while the paused step settles


def test_resume_pressed_while_the_paused_step_settles_is_not_lost() -> None:
    """Pause, then Resume at once, many times: every Resume must end in a finished goal."""
    for round_index in range(12):
        agent = _ScriptedSteps(hold_first_step=True)
        with mock.patch("core.web.api.runtime.run_agent", agent):
            try:
                goal_id, chat = _start(f"goals-review-race-{round_index}", "/goal water the plants", agent, model="vool", mode="auto")
                status, payload = _post_json("/api/goal/pause", {"session_id": chat, "goal_id": goal_id})
                assert status == 200 and payload["ok"], payload
            finally:
                agent.release.set()
            deadline = time.time() + 15
            while time.time() < deadline:
                steps = (load_goal(goal_id) or {}).get("plan", {}).get("steps", [])
                if steps and all(s.get("state") != "running" for s in steps):
                    break
                time.sleep(0.005)
            status, payload = _post_json("/api/goal/resume", {"session_id": chat, "goal_id": goal_id})
            assert status == 200 and payload["ok"], payload
            goal = _wait(goal_id, {"completed", "failed", "blocked"}, timeout=10)
            _wait_worker_gone(goal_id)
        assert goal.get("state") == "completed", (round_index, goal.get("state"), goal.get("last_reason"))


# ---------------------------------------------------------------- card controls keep the binding


def test_card_resume_hands_step_turns_the_chat_doors_binding() -> None:
    from core.auto_local_only_mode import turn_is_local_only
    from core.request_trust import request_is_owner_local

    agent = _ScriptedSteps(hold_first_step=True)
    with mock.patch("core.web.api.runtime.run_agent", agent):
        try:
            goal_id, chat = _start("goals-review-binding", "/goal label the photos", agent, model="vool-local-only", mode="auto")
            status, payload = _post_json("/api/goal/pause", {"session_id": chat, "goal_id": goal_id})
            assert status == 200 and payload["ok"]
        finally:
            agent.release.set()
        assert _wait(goal_id, {"paused"})["state"] == "paused"
        _wait_worker_gone(goal_id)
        status, payload = _post_json("/api/goal/resume", {"session_id": chat, "goal_id": goal_id})
        assert status == 200 and payload["ok"], payload
        goal = _wait(goal_id, {"completed", "failed", "blocked"})
        _wait_worker_gone(goal_id)
    assert goal["state"] == "completed", goal.get("last_reason")
    steps = agent.step_calls()
    assert len(steps) >= 2
    door, resumed = steps[0]["source_context"], steps[-1]["source_context"]
    assert turn_is_local_only(door) and turn_is_local_only(resumed)
    assert request_is_owner_local(door) is True
    assert request_is_owner_local(resumed) is True, "a card-resumed step lost the owner's local trust"
    assert resumed.get("chat_id") == door.get("chat_id")


# ---------------------------------------------------------------- registry door without the daemon


def test_a_registry_resume_without_the_live_daemon_refuses_and_keeps_the_goal() -> None:
    agent = _ScriptedSteps(hold_first_step=True)
    with mock.patch("core.web.api.runtime.run_agent", agent):
        try:
            goal_id, chat = _start("goals-review-registry", "/goal draft the shopping list", agent, model="vool", mode="auto")
            status, payload = _post_json("/api/goal/pause", {"session_id": chat, "goal_id": goal_id})
            assert status == 200 and payload["ok"]
        finally:
            agent.release.set()
        assert _wait(goal_id, {"paused"})["state"] == "paused"
        _wait_worker_gone(goal_id)
        steps_before = len(agent.step_calls())
        status, payload = _post_json(
            "/api/commands/dispatch",
            {"command_id": "goal.resume", "input": {"body": {"session_id": chat, "goal_id": goal_id}}},
        )
        time.sleep(0.5)
        _wait_worker_gone(goal_id)
        goal = load_goal(goal_id)
    assert status != 200 or not payload.get("ok"), payload
    assert goal["state"] == "paused", (goal["state"], goal.get("last_reason"))
    assert len(agent.step_calls()) == steps_before
