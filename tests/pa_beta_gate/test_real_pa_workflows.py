"""pa_beta_gate — the 8 canonical local-assistant workflows (deterministic).

Each is a real thing a person asks their PA to do, driven end-to-end through the
runtime seams (frontdoor handlers, the checkpoint/resume layer, the scripted tool
loop) with no live model. The natural-language *wording* of a summary/explanation
is the pa_beta_live tier; the plumbing that makes the workflow correct is here.

Workflows: remember a preference, continue yesterday's task, recover after an
interruption, edit a local file, run tests, create a Web0 copy, explain state
honestly, and summarize notes.
"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from unittest import mock

import pytest

from core.execution.models import WorkflowPlannerDecision
from core.memory_first_router import ModelExecutionDecision

pytestmark = [pytest.mark.pa_beta]

_OPENCLAW = {"surface": "openclaw", "platform": "openclaw"}
_PRIVATE_LOCAL = {
    "surface": "desktop",
    "platform": "local",
    "_owner_local": True,
}


def _sid(label):
    return f"openclaw:{label}:{uuid.uuid4().hex}"


def _policy(session_id):
    from core.context_scope import ContextAccessPolicy

    return ContextAccessPolicy.for_request(
        session_id=session_id,
        source_context=_PRIVATE_LOCAL,
    )


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    from core import runtime_paths
    monkeypatch.setattr(runtime_paths, "_VOOL_HOME_OVERRIDE", None, raising=False)
    return tmp_path


def _tool(intent, arguments):
    return ModelExecutionDecision(
        source="provider_execution", task_hash=f"t-{intent}", provider_id="ollama-local:test",
        provider_name="ollama-local", model_name="test",
        structured_output={"intent": intent, "arguments": arguments},
        confidence=0.8, trust_score=0.84, used_model=True, validation_state="valid",
    )


def _final(text):
    return ModelExecutionDecision(
        source="provider_execution", task_hash="final", provider_id="ollama-local:test",
        provider_name="ollama-local", model_name="test", output_text=text,
        confidence=0.82, trust_score=0.84, used_model=True, validation_state="valid",
    )


def _force_loop(agent):
    agent._should_attempt_tool_intent = lambda *a, **k: True  # type: ignore[assignment]
    agent._plan_tool_workflow = lambda *a, **k: WorkflowPlannerDecision(  # type: ignore[assignment]
        handled=False, reason="pa-bypass", next_payload=None)


# ---------------------------------------------------------------------------
# 1. Remember a user preference (frontdoor, no model)
# ---------------------------------------------------------------------------

def test_remember_a_fact_persists_it(isolated_home):
    from core.memory.entries import search_relevant_memory
    from core.persistent_memory import maybe_handle_memory_command

    sid = _sid("rem")
    policy = _policy(sid)
    handled, response = maybe_handle_memory_command(
        "remember that my launch wallet index is 8829145",
        session_id=sid,
        access_policy=policy,
    )
    assert handled is True and response
    rows = search_relevant_memory(
        "launch wallet index",
        access_policy=policy,
        topic_hints=["wallet"],
        limit=5,
    )
    assert any("8829145" in str(r.get("text") or "") for r in rows)


def test_remember_a_style_preference_persists_it(isolated_home):
    # Style preferences belong to the typed Operator Profile authority now: an explicit
    # command persists there (the legacy style_notes seam deliberately declines).
    from core import operator_profile as profile
    from core.operator_profile_interpretation import interpret_profile_turn
    from core.user_preferences import maybe_handle_preference_command

    handled, _ = maybe_handle_preference_command("always answer me in short telegram style")
    assert handled is False
    proposals = [
        p for p in interpret_profile_turn("always answer me in short telegram style")
        if p.category == "response_style"
    ]
    assert proposals and proposals[0].explicit
    change = profile.remember(
        "pa-workflow-user", proposals[0].category, proposals[0].value,
        session_id="pa:style", origin="explicit", actor="chat",
        reason="explicit: always answer me in short telegram style",
    )
    assert change.kind in {"saved", "updated"}
    saved = [
        i for i in profile.list_items("pa-workflow-user")
        if i.category == "response_style" and "telegram" in str(i.value_text)
    ]
    assert saved, "explicit style preference not persisted to the Operator Profile"


def test_forget_removes_a_remembered_fact(isolated_home):
    from core.memory.entries import search_relevant_memory
    from core.persistent_memory import maybe_handle_memory_command

    sid = _sid("forget")
    policy = _policy(sid)
    maybe_handle_memory_command(
        "remember the old treasury prefix is Z9QX7",
        session_id=sid,
        access_policy=policy,
    )
    handled, _ = maybe_handle_memory_command(
        "forget the old treasury prefix",
        session_id=sid,
        access_policy=policy,
    )
    assert handled is True
    rows = search_relevant_memory(
        "old treasury prefix",
        access_policy=policy,
        topic_hints=[],
        limit=5,
    )
    assert not any("Z9QX7" in str(r.get("text") or "") for r in rows)


# ---------------------------------------------------------------------------
# 2 + 3. Continue yesterday's task / recover after an interruption
# ---------------------------------------------------------------------------

def test_interrupted_task_is_resumable():
    from core.runtime_continuity import (
        create_runtime_checkpoint,
        latest_resumable_checkpoint,
        mark_stale_runtime_checkpoints_interrupted,
    )

    sid = _sid("resume")
    checkpoint = create_runtime_checkpoint(
        session_id=sid, request_text="refactor the auth module and run tests",
        source_context={"runtime_session_id": sid},
    )
    changed = mark_stale_runtime_checkpoints_interrupted()
    assert changed >= 1

    resumable = latest_resumable_checkpoint(sid)
    assert resumable is not None
    assert resumable["checkpoint_id"] == checkpoint["checkpoint_id"]
    assert resumable["status"] == "interrupted"


def test_a_completed_session_has_nothing_to_resume():
    from core.runtime_continuity import latest_resumable_checkpoint
    assert latest_resumable_checkpoint(_sid("fresh")) is None


def test_interruption_emits_a_task_interrupted_event():
    from core.runtime_continuity import (
        create_runtime_checkpoint,
        list_runtime_session_events,
        mark_stale_runtime_checkpoints_interrupted,
    )

    sid = _sid("resume-evt")
    create_runtime_checkpoint(session_id=sid, request_text="keep working on the deploy pipeline",
                              source_context={"runtime_session_id": sid})
    mark_stale_runtime_checkpoints_interrupted()
    events = list_runtime_session_events(sid, after_seq=0, limit=10)
    assert any(e["event_type"] == "task_interrupted" for e in events)


# ---------------------------------------------------------------------------
# 4. Edit a local file
# ---------------------------------------------------------------------------

@pytest.fixture
def allow_workspace_writes():
    from core import policy_engine
    previous = getattr(policy_engine, "_POLICY_CACHE", None)
    base = dict(policy_engine.load())
    fs = dict(base.get("filesystem") or {})
    fs["allow_write_workspace"] = True
    fs["allow_read_workspace"] = True
    base["filesystem"] = fs
    policy_engine._POLICY_CACHE = base
    try:
        yield
    finally:
        policy_engine._POLICY_CACHE = previous


def test_edit_a_local_file_end_to_end(make_agent, allow_workspace_writes):
    agent = make_agent()
    _force_loop(agent)
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "todo.txt"
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[
            _tool("workspace.write_file", {"path": "todo.txt", "content": "ship the beta\n"}),
            _tool("respond.direct", {"message": "Saved todo.txt."}),
        ])
        agent.memory_router.resolve = mock.Mock(return_value=_final("Saved todo.txt."))
        result = agent.run_once("save the todo to workspace file todo.txt",
                                session_id_override="pa:edit2",
                                # Auto is the mode the permission law defines as "may apply
                                # ordinary writes" (Manual prompts per file).
                                source_context={**_OPENCLAW, "workspace": tmp, "operating_mode": "auto"})
        assert result["mode"] == "tool_executed"
        assert target.read_text(encoding="utf-8") == "ship the beta\n"


# ---------------------------------------------------------------------------
# 5. Run tests and explain the result
# ---------------------------------------------------------------------------

def test_run_tests_workflow_maps_to_pytest():
    from core.execution.validation_tools import validation_command
    assert "pytest -q" in validation_command("workspace.run_tests", {})


# ---------------------------------------------------------------------------
# 6. Create a Web0 copy (local draft; publish stays gated)
# ---------------------------------------------------------------------------

def test_create_web0_copy_is_a_local_draft():
    from core.runtime_execution_tools import execute_runtime_tool
    result = execute_runtime_tool(
        "web0.open_builder_draft", {"title": "Launch Page", "code": "<main>welcome</main>", "domain": "launch"},
        source_context={},
    )
    assert result is not None and result.ok is True and "/templates/editor/?" in result.response_text


def test_web0_publish_needs_a_trusted_opt_in():
    from core.runtime_execution_tools import execute_runtime_tool
    result = execute_runtime_tool("web0.publish", {"project_id": "p1", "allow_network_publish": True},
                                  source_context={})
    assert result is not None and result.ok is False and result.status == "requires_opt_in"


# ---------------------------------------------------------------------------
# 7. Explain project state honestly (no fabricated success)
# ---------------------------------------------------------------------------

def test_unknown_action_is_reported_honestly():
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
    from core.tool_intent_executor import execute_tool_intent

    ex = execute_tool_intent(
        {"intent": "deploy.to_mainnet", "arguments": {}}, task_id="t", session_id="s",
        source_context=_OPENCLAW,
        hive_activity_tracker=HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None)),
    )
    assert ex.ok is False and ex.status == "unsupported"
    assert "not wired" in (ex.user_safe_response_text or "").lower()


# ---------------------------------------------------------------------------
# 8. Summarize notes (read -> ground the summary)
# ---------------------------------------------------------------------------

def test_summarize_notes_reads_the_file(make_agent):
    agent = make_agent()
    _force_loop(agent)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "notes.txt").write_text("cap 0.037 SOL; deadline 2026-07-15; domain alice.null\n", encoding="utf-8")
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[
            _tool("workspace.read_file", {"path": "notes.txt"}),
            _tool("respond.direct", {"message": "Cap 0.037 SOL, deadline 2026-07-15, domain alice.null."}),
        ])
        agent.memory_router.resolve = mock.Mock(
            return_value=_final("Cap 0.037 SOL, deadline 2026-07-15, domain alice.null."))
        result = agent.run_once("summarize my notes in the workspace",
                                session_id_override="pa:sum", source_context={**_OPENCLAW, "workspace": tmp})
        assert result["mode"] == "tool_executed"
        assert "0.037 SOL" in result["response"] and "alice.null" in result["response"]
