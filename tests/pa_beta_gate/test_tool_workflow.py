"""pa_beta_gate — tool / workflow honesty (deterministic).

Proves VOOL runs real local-assistant tool work truthfully: it picks a tool,
passes the right args, actually touches the workspace, and — crucially — never
claims success it didn't achieve. Drives the REAL tool loop through the scripted
resolve_tool_intent seam (no live model), and the executor directly for the
honesty matrix.

Live wording (does the model emit a valid tool_intent for a real prompt) is the
pa_beta_live tier, not here.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from unittest import mock

import pytest

from core.execution.models import WorkflowPlannerDecision
from core.execution.planner import should_attempt_tool_intent
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.memory_first_router import ModelExecutionDecision
from core.tool_intent_executor import execute_tool_intent
from tests.conftest import FORBIDDEN_CHAT_LEAKS

pytestmark = [pytest.mark.pa_beta]

_OPENCLAW = {"surface": "openclaw", "platform": "openclaw"}


def _tracker():
    return HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))


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


# ---------------------------------------------------------------------------
# Correct tool + correct args: a real file edit lands on disk
# ---------------------------------------------------------------------------

def test_edit_a_local_file_writes_the_exact_content(make_agent, allow_workspace_writes):
    agent = make_agent()
    _force_loop(agent)
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "notes.txt"
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[
            _tool("workspace.write_file", {"path": "notes.txt", "content": "launch cap is 0.037 SOL\n"}),
            _tool("respond.direct", {"message": "Saved notes.txt."}),
        ])
        agent.memory_router.resolve = mock.Mock(return_value=_final("Saved notes.txt."))
        result = agent.run_once("save the notes to workspace file notes.txt",
                                session_id_override="pa:edit",
                                # Auto is the mode the permission law defines as "may apply
                                # ordinary writes" (Manual prompts per file). The write still
                                # crosses decide_tool_call, the effect ledger and confinement.
                                source_context={**_OPENCLAW, "workspace": tmp, "operating_mode": "auto"})
        assert result["mode"] == "tool_executed"
        assert target.exists()
        assert target.read_text(encoding="utf-8") == "launch cap is 0.037 SOL\n"  # exact args, not paraphrased


def test_read_a_local_file_grounds_the_answer(make_agent):
    agent = make_agent()
    _force_loop(agent)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "notes.txt").write_text("the deadline is 2026-07-15\n", encoding="utf-8")
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[
            _tool("workspace.read_file", {"path": "notes.txt"}),
            _tool("respond.direct", {"message": "Your deadline is 2026-07-15."}),
        ])
        agent.memory_router.resolve = mock.Mock(return_value=_final("Your deadline is 2026-07-15."))
        result = agent.run_once("read the notes file in the workspace",
                                session_id_override="pa:read", source_context={**_OPENCLAW, "workspace": tmp})
        assert result["mode"] == "tool_executed"
        assert "2026-07-15" in result["response"]


# ---------------------------------------------------------------------------
# No fake success: honest failure statuses, no leaked internals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload,status", [
    ({"arguments": {}}, "missing_intent"),
    ({"intent": "", "arguments": {}}, "missing_intent"),
    ({"intent": "foo.bar", "arguments": {}}, "unsupported"),
    ({"intent": "make.coffee"}, "unsupported"),
])
def test_bad_intent_is_honest_not_fake_success(payload, status):
    ex = execute_tool_intent(payload, task_id="t", session_id="s", source_context=_OPENCLAW,
                             hive_activity_tracker=_tracker())
    assert ex.ok is False
    assert ex.status == status
    safe = (ex.user_safe_response_text or "").lower()
    for leak in FORBIDDEN_CHAT_LEAKS:
        assert leak not in safe


def test_respond_direct_is_not_a_fake_tool_run():
    ex = execute_tool_intent({"intent": "respond.direct", "arguments": {"message": "hi"}},
                             task_id="t", session_id="s", source_context=_OPENCLAW, hive_activity_tracker=_tracker())
    assert ex.handled is False
    assert ex.status == "direct_response"


def test_failed_tool_result_is_reported_honestly_not_as_success(make_agent):
    from core.execution.models import ToolIntentExecution

    agent = make_agent()
    _force_loop(agent)
    failed = ToolIntentExecution(
        handled=True, ok=False, status="disabled", response_text="Workspace writes are disabled.",
        user_safe_response_text="I couldn't complete that action.", mode="tool_failed", tool_name="workspace.write_file",
        details={},
    )
    with tempfile.TemporaryDirectory() as tmp:
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[
            _tool("workspace.write_file", {"path": "out.txt", "content": "x"}),
            _tool("respond.direct", {"message": "ok"}),
        ])
        agent.memory_router.resolve = mock.Mock(return_value=_final("I couldn't complete that action."))
        with mock.patch("core.agent_runtime.agent.execute_tool_intent", return_value=failed):
            result = agent.run_once("save the notes to workspace file out.txt",
                                    session_id_override="pa:fail", source_context={**_OPENCLAW, "workspace": tmp})
        # never reports a fake success, never leaks internals
        assert result["mode"] != "tool_executed"
        for leak in FORBIDDEN_CHAT_LEAKS:
            assert leak not in (result["response"] or "").lower()


# ---------------------------------------------------------------------------
# should_attempt_tool_intent truth table for PA phrasings
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("check my disk space and clean temp files", True),
    ("what are my machine specs?", True),
    ("proceed", True),
    ("yes do it", True),
    ("run it", True),
    ("explain how python decorators work", False),
    ("what is a good architecture for a chat app?", False),
    ("", False),
])
def test_should_attempt_tool_intent_truth_table(text, expected):
    assert should_attempt_tool_intent(text, task_class="chat", source_context=_OPENCLAW) is expected


def test_non_tool_surface_never_attempts_tools():
    assert should_attempt_tool_intent("delete the temp folder", task_class="chat",
                                      source_context={"surface": "email", "platform": "email"}) is False


# ---------------------------------------------------------------------------
# Run tests and explain the result
# ---------------------------------------------------------------------------

def test_run_tests_maps_to_the_pytest_command():
    from core.execution.validation_tools import validation_command
    assert "pytest -q" in validation_command("workspace.run_tests", {})


def test_run_lint_maps_to_a_lint_command():
    from core.execution.validation_tools import validation_command
    cmd = validation_command("workspace.run_lint", {})
    assert "ruff" in cmd or "lint" in cmd


# ---------------------------------------------------------------------------
# Create a Web0 copy locally; publishing is gated off by default
# ---------------------------------------------------------------------------

def test_web0_builder_draft_is_a_local_action():
    from core.runtime_execution_tools import execute_runtime_tool
    result = execute_runtime_tool(
        "web0.open_builder_draft",
        {"title": "My Page", "code": "<main>owner content</main>", "domain": "mypage"},
        source_context={},
    )
    assert result is not None and result.ok is True
    assert "/templates/editor/?" in result.response_text  # a local draft URL, nothing published


def test_web0_publish_is_gated_off_without_trusted_opt_in():
    from core.runtime_execution_tools import execute_runtime_tool
    # even if the model puts the opt-in in its args, only trusted source_context can flip it
    result = execute_runtime_tool(
        "web0.publish",
        {"project_id": "proj-1", "allow_network_publish": True, "vool_wallet": "attacker"},
        source_context={},
    )
    assert result is not None and result.ok is False
    assert result.status == "requires_opt_in"


def test_tool_loop_flagged_answer_shows_the_verifier_draft_caveat(make_agent):
    # Codex F5: a verifier-flagged tool-loop answer must show the visible caveat.
    from core.memory_first_router import VERIFIER_DRAFT_CAVEAT

    agent = make_agent()
    _force_loop(agent)
    flagged_direct = ModelExecutionDecision(
        source="provider_execution", task_hash="direct", provider_id="ollama-local:test",
        provider_name="ollama-local", model_name="test",
        structured_output={"intent": "respond.direct", "arguments": {"message": "The staging port is 9090."}},
        confidence=0.7, trust_score=0.4, used_model=True, validation_state="valid",
        details={"needs_review": True, "verifier_gate": "flagged"},
    )
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "notes.txt").write_text("the staging port is 9090\n", encoding="utf-8")
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[
            _tool("workspace.read_file", {"path": "notes.txt"}),
            flagged_direct,
        ])
        agent.memory_router.resolve = mock.Mock(return_value=_final("The staging port is 9090."))
        result = agent.run_once("read the notes file in the workspace",
                                session_id_override="pa:flagdirect", source_context={**_OPENCLAW, "workspace": tmp})
        assert result["mode"] == "tool_executed"
        assert VERIFIER_DRAFT_CAVEAT in result["response"]


def test_search_the_workspace_returns_real_matches(make_agent):
    agent = make_agent()
    _force_loop(agent)
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "cfg.txt").write_text("staging port is 8096\n", encoding="utf-8")
        agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[
            _tool("workspace.search_text", {"query": "8096", "path": "."}),
            _tool("respond.direct", {"message": "The staging port is 8096."}),
        ])
        agent.memory_router.resolve = mock.Mock(return_value=_final("The staging port is 8096."))
        result = agent.run_once("search the workspace for the staging port",
                                session_id_override="pa:search", source_context={**_OPENCLAW, "workspace": tmp})
        assert result["mode"] == "tool_executed"
        assert "8096" in result["response"]
