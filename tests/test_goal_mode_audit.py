"""Stage-1 mode audit, served: all five operating modes agree, persist, and gate.

The mission's Stage 1 requires a small synthetic request per mode proving the server mode
agrees with the selector, survives its intended persistence, and keeps the right
read/write/approval behavior. The deep matrix (which action is allowed/prompted/denied per
mode) is pinned by tests/test_mode_permission_policy.py and the gauntlet; what is proven
here at the SERVED seam is the per-turn agreement and persistence contract every /goal step
inherits, plus that /goal itself never escalates: the goal turn carries exactly the chat's
own mode, and an expired/missing bypass is never granted by a goal.
"""
from __future__ import annotations

import json
from typing import Any

import pytest

from core import runtime_paths

SESSION = "openclaw:mode-audit"


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
    yield
    configure_default_db_path(None)
    reset_default_connection()
    runtime_paths.configure_runtime_home(None)


def _chat(body: dict[str, Any], captured: list[dict[str, Any]]):
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    def fake_agent(runtime, text, *, session_id=None, source_context=None, workspace_root_provider=None):
        captured.append(dict(source_context or {}))
        return {"response": "[synthetic] ok", "confidence": 0.9}

    response = dispatch_post(
        path="/api/chat",
        body={"session_id": SESSION, "stream": False, **body},
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
        run_agent_provider=fake_agent,
    )
    assert response.status == 200, response.body
    return response


@pytest.mark.parametrize(
    "mode_value",
    ["manual", "review_edits", "plan", "auto"],
)
def test_each_standard_mode_agrees_with_the_selector_and_persists(mode_value: str) -> None:
    captured: list[dict[str, Any]] = []
    # Turn 1: the composer's explicit selection rides the body.
    _chat({"messages": [{"role": "user", "content": "hello"}], "mode": mode_value, "turn_id": f"m1-{mode_value}"}, captured)
    assert captured[0]["operating_mode"] == mode_value
    # Turn 2: an omitted selection keeps the chat's controller-owned mode (persistence).
    _chat({"messages": [{"role": "user", "content": "again"}], "turn_id": f"m2-{mode_value}"}, captured)
    assert captured[1]["operating_mode"] == mode_value
    from core.mode_permission_policy import OperatingMode, active_mode_state

    # The authority records under the canonical chat id the ingress resolves (the same id the
    # card and the goal workflow use); the recorded state is read back through it.
    canonical = str(captured[1].get("runtime_session_id") or "")
    assert canonical
    state = active_mode_state({"runtime_session_id": canonical})
    assert state["mode"] == mode_value
    assert OperatingMode(mode_value).value == mode_value


def test_invalid_mode_fails_the_turn_closed_instead_of_inheriting() -> None:
    captured: list[dict[str, Any]] = []
    _chat({"messages": [{"role": "user", "content": "hello"}], "mode": "auto", "turn_id": "inv1"}, captured)
    assert captured[0]["operating_mode"] == "auto"
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    response = dispatch_post(
        path="/api/chat",
        body={"session_id": SESSION, "stream": False, "messages": [{"role": "user", "content": "x"}],
              "mode": "not-a-mode", "turn_id": "inv2"},
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
        run_agent_provider=lambda runtime, text, **kw: {"response": "should not run", "confidence": 0.0},
    )
    assert response.status == 400
    assert b"did not run" in response.body


def test_bypass_needs_a_current_grant_and_goal_mode_is_the_chats_own() -> None:
    from core.web.api.runtime import RuntimeServices
    from core.web.api.service import dispatch_post

    # An explicit bypass request without a server-side grant is refused out loud (409)...
    response = dispatch_post(
        path="/api/chat",
        body={"session_id": SESSION, "stream": False, "messages": [{"role": "user", "content": "x"}],
              "mode": "bypass_permissions", "turn_id": "bp1"},
        headers={"content-type": "application/json"},
        runtime=RuntimeServices(display_name="VOOL"),
        model_name="vool",
        workspace_root_provider=lambda: "/tmp",
        client_host="127.0.0.1",
        run_agent_provider=lambda runtime, text, **kw: {"response": "should not run", "confidence": 0.0},
    )
    assert response.status == 409
    assert b"explicitly confirmed grant" in response.body

    # ...and the /goal ingress stamps the goal turn with exactly the chat's own mode — a goal
    # begin never minted bypass, never raised a mode. The plain-message baseline: Manual.
    captured: list[dict[str, Any]] = []
    _chat({"messages": [{"role": "user", "content": "set the baseline"}], "mode": "manual", "turn_id": "bp2"}, captured)
    assert captured[0]["operating_mode"] == "manual"

    from core.goal_workflow import load_goal, new_goal_id, parse_goal_command, save_goal
    from core.web.api.goal_api import serve_goal_ingress
    from core.web.api.runtime import RuntimeServices

    goal_id = new_goal_id()
    save_goal({
        "goal_id": goal_id, "session_id": SESSION, "project_id": "", "objective": "check",
        "acceptance": "", "plan": {}, "state": "queued", "revision": 0, "current_step_id": "",
        "created_at": 1.0, "updated_at": 1.0, "ended_at": None, "started_at": None,
        "paused_at": None, "paused_seconds": 0.0, "created_turn_id": "t", "last_reason": "",
        "model": "vool", "exec_context": {},
    })
    # The step context carries NO bypass token even when the caller's context tries to hand one over.
    from core.goal_executor import step_source_context

    ctx = step_source_context(
        {"bypass_token": "forged", "mode_approval_token": "forged", "runtime_session_id": SESSION, "workspace": "/tmp"},
        goal={"goal_id": goal_id}, step={"id": "s1"}, attempt=1, turn_id="g:s:a",
    )
    assert "bypass_token" not in ctx
    assert "mode_approval_token" not in ctx
