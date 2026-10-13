"""Plan mode holds the goal without applying work; planning cannot execute through authority.

The planning turn's no-tools rule is enforced through the EXISTING per-call deny authority
(``project_permissions``, the deny-only map every tool consult reads) — stamped by the goal
controller after ingress, never forgeable by a client, and unable to grant anything. The
Plan-mode goal journey proves the mode matrix holds: steps cannot mutate, nothing falsely
completes, and the goal ends honestly without the work having been applied.
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
from core.goal_workflow import reset_goal_state_for_tests

SESSION = "openclaw:goals-plan-mode"

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
    return _WORKSPACE


class _PlanProbeAgent:
    """Records what the planning turn's context carried; step turns claim prose success but
    perform NO effect — under Plan mode nothing may be applied and nothing may complete."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.lock = threading.Lock()

    def __call__(self, runtime, text, *, session_id=None, source_context=None, workspace_root_provider=None):
        with self.lock:
            self.calls.append({"text": text, "source_context": dict(source_context or {})})
        if PLANNER_MARKER in text:
            return {
                "response": (
                    '```json\n{"steps": ['
                    '{"id": "s1", "title": "Write the plan-mode page", "verify": {"tool": "workspace.write_file", "path_contains": "plan.html"}}'
                    "]}\n```"
                ),
                "confidence": 1.0,
            }
        if STEP_MARKER in text:
            # Pure prose success: the effect the verification demands never happens. Under
            # a truthful controller this can never complete the step.
            return {
                "response": "Done! I wrote plan.html with the full page content.",
                "fulfillment_outcome": {"fulfillment_status": "fulfilled"},
                "confidence": 0.9,
            }
        return {"response": "ok", "confidence": 0.9}


def test_planning_turn_carries_the_authority_deny_and_plan_mode_holds() -> None:
    from core.mode_permission_policy import PermissionAction

    agent = _PlanProbeAgent()
    with mock.patch("core.web.api.runtime.run_agent", agent):
        from tests.test_goal_served_workflow import _asgi, _Resp, _shared_app

        status, body = _asgi(
            _shared_app(), "POST", "/api/chat",
            {"session_id": SESSION, "stream": True, "mode": "plan", "turn_id": "plan-t1",
             "messages": [{"role": "user", "content": "/goal build the plan demo"}]},
        )
        assert status == 200
        lines = []
        for line in body.decode("utf-8", errors="replace").splitlines():
            line = line.strip()
            if line:
                lines.append(json.loads(line))
        views = [
            item["vool_event"]["goal"]
            for item in lines
            if isinstance(item.get("vool_event"), dict) and item["vool_event"].get("type") == "goal.updated"
        ]
        assert views, "no goal event on the plan-mode ingress"
        session = str(views[0]["session_id"])
        goal_id = views[0]["goal_id"]

        # The PLANNING turn carried the full deny through the existing authority: the
        # controller stamped it after ingress — no tool call may execute while planning,
        # whatever the prompt says.
        planner = [c for c in agent.calls if PLANNER_MARKER in c["text"]]
        assert planner, "the planner turn ran through the real path"
        deny = (planner[0]["source_context"].get("project_permissions") or {})
        assert deny and all(
            deny.get(action.value) is False for action in PermissionAction
        ), "planning must deny every tool action through the authority"

        # The step turn claims prose success but its verification evidence never exists:
        # bounded repair runs, then the goal FAILS honestly. Nothing is applied, nothing
        # completes.
        deadline = time.time() + 30
        goal = {}
        while time.time() < deadline:
            from core.goal_workflow import load_goal

            goal = load_goal(goal_id)
            if goal and goal["state"] in {"completed", "failed", "cancelled", "paused", "waiting_approval", "interrupted", "blocked"}:
                break
            time.sleep(0.2)
        assert goal["state"] == "failed", (goal.get("state"), goal.get("last_reason"))
        step = goal["plan"]["steps"][0]
        assert step["state"] == "failed"
        assert "verification not met" in (step.get("reason") or "")
        assert step["attempts"] == 2, "the bounded repair ran before the honest failure"
        assert not (Path(_workspace()) / "plan.html").exists(), "Plan mode applied nothing"
        from core.goal_workflow import goal_public_view

        view = goal_public_view(goal)
        assert (view["completed"], view["total"]) == (0, 1), "a prose success never counts"
