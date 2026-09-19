"""Gauntlet — category 5 + 6: tool loop / agent loop hard gates.

Drives the REAL agent loop through the proven injection seam (scripting
``agent.memory_router.resolve_tool_intent`` with ModelExecutionDecisions, exactly
like tests/test_runtime_continuity.py) — no live model. Asserts:

  - the loop actually executes a tool and feeds the observation into synthesis
    (a full turn, tool -> observation -> grounded answer)
  - a real file lands on disk when the model asks for a write (end-to-end)
  - the loop never fabricates success: a missing or unknown intent yields an
    honest failure status and none of the FORBIDDEN_CHAT_LEAKS reach the user text
"""
from __future__ import annotations

import itertools
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from core.execution.models import WorkflowPlannerDecision
from core.execution.planner import should_attempt_tool_intent
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.memory_first_router import ModelExecutionDecision
from core.tool_intent_executor import execute_tool_intent
from tests.conftest import FORBIDDEN_CHAT_LEAKS

pytestmark = [pytest.mark.gauntlet]

_OPENCLAW = {"surface": "openclaw", "platform": "openclaw"}


def _force_loop_via_scripted_model(agent) -> None:
    """Neutralise the deterministic regex planner and the loop-entry gate so the
    scripted ``resolve_tool_intent`` decisions drive the loop. Without this, the
    real ``_plan_tool_workflow`` may pick and run its own tool before the mock
    ever fires (documented in the tool-loop map)."""
    agent._should_attempt_tool_intent = lambda *a, **k: True  # type: ignore[assignment]
    agent._plan_tool_workflow = lambda *a, **k: WorkflowPlannerDecision(  # type: ignore[assignment]
        handled=False, reason="gauntlet-bypass", next_payload=None
    )


def _tool_decision(intent: str, arguments: dict) -> ModelExecutionDecision:
    return ModelExecutionDecision(
        source="provider_execution",
        task_hash=f"tool-{intent}",
        provider_id="ollama-local:test",
        provider_name="ollama-local",
        model_name="test",
        structured_output={"intent": intent, "arguments": arguments},
        confidence=0.8,
        trust_score=0.84,
        used_model=True,
        validation_state="valid",
    )


def _final_decision(text: str) -> ModelExecutionDecision:
    return ModelExecutionDecision(
        source="provider_execution",
        task_hash="final-synthesis",
        provider_id="ollama-local:test",
        provider_name="ollama-local",
        model_name="test",
        output_text=text,
        confidence=0.82,
        trust_score=0.84,
        used_model=True,
        validation_state="valid",
    )


@pytest.fixture
def allow_workspace_writes():
    """Flip the workspace-write policy flag on (off by default under test) so the
    write lane in the loop is enabled, then restore."""
    from core import policy_engine

    previous = getattr(policy_engine, "_POLICY_CACHE", None)
    base = dict(policy_engine.load())
    filesystem = dict(base.get("filesystem") or {})
    filesystem["allow_write_workspace"] = True
    filesystem["allow_read_workspace"] = True
    base["filesystem"] = filesystem
    policy_engine._POLICY_CACHE = base
    try:
        yield
    finally:
        policy_engine._POLICY_CACHE = previous


# ---------------------------------------------------------------------------
# 1. Full loop: read-only tool executes, observation feeds synthesis
# ---------------------------------------------------------------------------

def test_full_loop_executes_tool_then_synthesizes(make_agent):
    agent = make_agent()
    _force_loop_via_scripted_model(agent)
    with tempfile.TemporaryDirectory() as tmpdir:
        (Path(tmpdir) / "notes.txt").write_text("the marker is xyzzy-42\n", encoding="utf-8")

        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            side_effect=[
                _tool_decision("workspace.search_text", {"query": "marker", "path": "."}),
                _tool_decision("respond.direct", {"message": "The marker is xyzzy-42."}),
            ]
        )
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=_final_decision("The marker is xyzzy-42.")
        )

        result = agent.run_once(
            "search the workspace for the marker",
            session_id_override="openclaw:loop-read",
            source_context={**_OPENCLAW, "workspace": tmpdir},
        )

    assert result["mode"] == "tool_executed"
    assert "xyzzy-42" in result["response"]
    # The tool was actually invoked (at least the search step).
    assert agent.memory_router.resolve_tool_intent.call_count >= 1


def test_native_call_ids_do_not_hide_a_repeated_semantic_tool_call(make_agent):
    agent = make_agent()
    _force_loop_via_scripted_model(agent)
    first = _tool_decision("workspace.list_tree", {"path": ".", "limit": 200})
    first.structured_output["_native_tool_call_id"] = "native-call-1"
    repeated = _tool_decision("workspace.list_tree", {"path": ".", "limit": 200})
    repeated.structured_output["_native_tool_call_id"] = "native-call-2"
    agent.memory_router.resolve_tool_intent = mock.Mock(side_effect=[first, repeated])  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=_final_decision("Workspace tree under `.` contains `notes.txt`.")
    )

    execution = SimpleNamespace(
        handled=True,
        ok=True,
        status="executed",
        response_text="Workspace tree under `.`:\n- notes.txt",
        user_safe_response_text="",
        mode="tool_executed",
        tool_name="workspace.list_tree",
        details={
            "observation": {
                "schema": "tool_observation_v1",
                "intent": "workspace.list_tree",
                "ok": True,
                "status": "executed",
                "response_preview": "notes.txt",
            }
        },
        learned_plan=None,
    )
    with mock.patch.object(agent, "_execute_tool_intent", return_value=execution) as execute:
        result = agent.run_once(
            "inspect the current workspace",
            session_id_override="openclaw:loop-native-repeat",
            source_context={**_OPENCLAW, "workspace": ".", "requested_model": "test-pinned-model"},
        )

    assert execute.call_count == 1
    assert agent.memory_router.resolve_tool_intent.call_count == 2
    assert result["mode"] == "tool_executed"
    assert "notes.txt" in result["response"]


def test_exhausted_tool_budget_is_not_reported_as_completed(make_agent):
    # The model never stops asking for another area. Scripting a FIXED list here made the test
    # depend on the budget's exact value: it was a list of 5 because the unpinned budget was 5, so
    # when the pin-conditional `12 if explicit_model_pin else 5` was replaced by one number for
    # every model, the mock ran out and died on StopIteration rather than on the property under
    # test. An endless script asserts the property itself - whatever the budget is, exhausting it
    # is reported as incomplete and never as a finished answer.
    _areas = itertools.count()
    agent = make_agent()
    _force_loop_via_scripted_model(agent)
    agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
        side_effect=lambda **_kwargs: _tool_decision(
            "workspace.list_files", {"path": f"part-{next(_areas)}", "limit": 20}
        )
    )
    agent.memory_router.resolve = mock.Mock(return_value=_final_decision(""))  # type: ignore[assignment]

    def _executed(payload: dict, **_: object):
        path = str((payload.get("arguments") or {}).get("path") or "")
        return SimpleNamespace(
            handled=True,
            ok=True,
            status="executed",
            response_text=f"Files under {path}: example.py",
            user_safe_response_text="",
            mode="tool_executed",
            tool_name="workspace.list_files",
            details={
                "observation": {
                    "schema": "tool_observation_v1",
                    "intent": "workspace.list_files",
                    "ok": True,
                    "status": "executed",
                    "path": path,
                    "response_preview": "example.py",
                }
            },
            learned_plan=None,
        )

    with mock.patch.object(agent, "_execute_tool_intent", side_effect=_executed):
        result = agent.run_once(
            "use tools to list files in five workspace areas and then report",
            session_id_override="openclaw:loop-budget-exhausted",
            source_context={**_OPENCLAW, "workspace": "."},
        )

    assert result["mode"] == "tool_failed"
    assert result["details"]["loop_stop_reason"] == "step_budget_exhausted"
    assert "incomplete" in result["response"].lower()
    assert "not a completed audit" in result["response"].lower()


def test_unavailable_tool_model_does_not_execute_empty_payload(make_agent):
    agent = make_agent()
    _force_loop_via_scripted_model(agent)
    agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="no_provider_available",
            task_hash="tool-provider-unavailable",
            structured_output=None,
            used_model=False,
        )
    )
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=_final_decision("A calm workspace supports focused thinking.")
    )

    with mock.patch.object(
        agent,
        "_execute_tool_intent",
        side_effect=AssertionError("an unavailable model must not execute an empty tool payload"),
    ) as execute:
        result = agent.run_once(
            "Describe a calm workspace.",
            session_id_override="openclaw:tool-model-unavailable",
            source_context={**_OPENCLAW, "workspace": "."},
        )

    execute.assert_not_called()
    assert result["response"] == "A calm workspace supports focused thinking."
    assert result["mode"] != "tool_failed"


# ---------------------------------------------------------------------------
# 2. Full loop: a real file is written to disk
# ---------------------------------------------------------------------------

def test_full_loop_writes_a_real_file_on_disk(make_agent, allow_workspace_writes):
    agent = make_agent()
    _force_loop_via_scripted_model(agent)
    with tempfile.TemporaryDirectory() as tmpdir:
        # Filename deliberately avoids the substring "create": a "create ..." phrasing
        # (even inside a filename) is intercepted by the builder controller front door
        # before the research tool loop; a plain workspace-file action reaches the loop.
        target = Path(tmpdir) / "loop_output.txt"

        agent.memory_router.resolve_tool_intent = mock.Mock(  # type: ignore[assignment]
            side_effect=[
                _tool_decision(
                    "workspace.write_file",
                    {"path": "loop_output.txt", "content": "written through the loop\n"},
                ),
                _tool_decision("respond.direct", {"message": "Done — file saved."}),
            ]
        )
        agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
            return_value=_final_decision("Done — file saved.")
        )

        result = agent.run_once(
            "save the notes to workspace file loop_output.txt",
            session_id_override="openclaw:loop-write",
            # A turn with no mode resolves to MANUAL, and a workspace write in MANUAL is
            # approval-gated by design -- the loop would correctly stop at a preview. The
            # loop's write path is exercised under AUTO, the mode the served drives use.
            source_context={**_OPENCLAW, "workspace": tmpdir, "operating_mode": "auto"},
        )

        assert result["mode"] == "tool_executed"
        assert target.exists(), "the loop did not write the file to disk"
        assert "written through the loop" in target.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# 3. Fake-success prevention at the executor
# ---------------------------------------------------------------------------

def _tracker() -> HiveActivityTracker:
    return HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))


def test_missing_intent_is_honest_failure_not_fake_success():
    execution = execute_tool_intent(
        {"arguments": {"foo": "bar"}},  # no intent
        task_id="t-1",
        session_id="s-1",
        source_context=_OPENCLAW,
        hive_activity_tracker=_tracker(),
    )
    assert execution.ok is False
    assert execution.status == "missing_intent"
    # The user-safe text is honest and carries none of the internal leak strings.
    safe = (execution.user_safe_response_text or "").lower()
    assert "couldn't map" in safe
    for leak in FORBIDDEN_CHAT_LEAKS:
        assert leak not in safe


def test_unknown_intent_reports_unsupported_not_fake_success():
    execution = execute_tool_intent(
        {"intent": "foo.bar", "arguments": {}},
        task_id="t-2",
        session_id="s-2",
        source_context=_OPENCLAW,
        hive_activity_tracker=_tracker(),
    )
    assert execution.ok is False
    assert execution.status == "unsupported"
    safe = (execution.user_safe_response_text or "").lower()
    assert "not wired" in safe
    for leak in FORBIDDEN_CHAT_LEAKS:
        assert leak not in safe


def test_respond_direct_is_not_treated_as_a_tool():
    execution = execute_tool_intent(
        {"intent": "respond.direct", "arguments": {"message": "hi"}},
        task_id="t-3",
        session_id="s-3",
        source_context=_OPENCLAW,
        hive_activity_tracker=_tracker(),
    )
    # respond.direct is a non-tool sentinel: handled=False so the loop synthesizes normally.
    assert execution.handled is False
    assert execution.status == "direct_response"


# ---------------------------------------------------------------------------
# 4. Loop-entry gate: should_attempt_tool_intent truth table (deterministic)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "text,task_class,expected",
    [
        # Action / execution phrasing -> attempt tools.
        ("check my disk space and clean temp", "chat", True),
        ("what are my machine specs?", "chat", True),
        ("proceed", "chat", True),
        ("yes do it", "chat", True),
        # Two ambiguous markers co-occurring is a real machine question -> attempt tools.
        ("check my process list", "chat", True),
        # Advice-only / pure chat -> do NOT attempt tools.
        ("explain how oauth works", "chat", False),
        ("", "chat", False),
    ],
)
def test_should_attempt_tool_intent_truth_table(text, task_class, expected):
    got = should_attempt_tool_intent(text, task_class=task_class, source_context=_OPENCLAW)
    assert got is expected, f"should_attempt_tool_intent({text!r}) -> {got}, expected {expected}"


def test_tool_intent_gate_over_triggers_on_benign_advice_regression_pin():
    """Conceptual project advice remains plain chat rather than opening the tool loop."""
    got = should_attempt_tool_intent(
        "what is a good way to structure a python project?",
        task_class="chat",
        source_context=_OPENCLAW,
    )
    assert got is False


# ---------------------------------------------------------------------------
# 4b. A single AMBIGUOUS marker (an ordinary English word that also has a
# common everyday, tool-free sense) must not open the tool loop by itself.
# Each phrasing below was measured live to return True before the ambiguous
# markers required a second, corroborating marker to co-occur -- one bare
# hit on a common word was enough to route ordinary chat into the tool lane.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # _LIVE_LOOKUP_AMBIGUOUS_MARKERS
        "check this couch for comfort before purchasing",
        "find comfort in knowing you tried your best",
        "keep an open mind about this",
        "what is your current mood right now",
        "I feel great today",
        "my recent memories of that trip are fond",
        "that is just a different version of the same story",
        "the dog loves to fetch the ball",
        "pull yourself together and relax",
        "the artist will render a beautiful portrait",
        # _LOCAL_TOOL_AMBIGUOUS_MARKERS
        "examine my sleep schedule and suggest improvements",
        "this process took forever to finish",
        "the customer service at that restaurant was excellent",
        "give me some space to think",
        "move on from this relationship",
        "the tool of choice for this job is a hammer",
        "his memory of that day is vivid",
        "the vending machine ate my dollar",
    ],
)
def test_single_ambiguous_marker_does_not_open_tool_loop(text):
    got = should_attempt_tool_intent(text, task_class="chat", source_context=_OPENCLAW)
    assert got is False, f"should_attempt_tool_intent({text!r}) -> True, a lone ambiguous marker must not attempt tools"


@pytest.mark.parametrize(
    "text",
    [
        # "version" embedded mid-word inside an unrelated word.
        "I have an aversion to cilantro",
        # "render" embedded mid-word inside "surrender".
        "the artist will surrender to the moment",
        # "current" embedded mid-word inside "concurrent".
        "we need concurrent access to this resource",
        # "on x" as a bare substring of "on xbox" (a brand name, not the marker).
        "playing on xbox tonight",
    ],
)
def test_marker_word_boundary_ignores_mid_word_substrings(text):
    """A marker embedded inside an unrelated word must not match as a bare substring."""
    got = should_attempt_tool_intent(text, task_class="chat", source_context=_OPENCLAW)
    assert got is False, f"should_attempt_tool_intent({text!r}) -> True, matched a marker mid-word"


def test_non_tool_capable_surface_never_attempts_tools():
    # A surface that is neither tool-capable nor a tool-capable platform must bail,
    # regardless of how action-like the text is.
    got = should_attempt_tool_intent(
        "delete the temp folder now",
        task_class="chat",
        source_context={"surface": "email", "platform": "email"},
    )
    assert got is False
