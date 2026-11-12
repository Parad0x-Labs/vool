"""A model build whose write waits for approval, or is refused, says so instead of blaming the model.

Found on a served run in Manual mode: the builder generated valid bytes, `workspace.write_file`
answered with a permission preview, and the build carried on as if the write had simply not
happened. The approval the operator needed to see was dropped and the turn closed as a failed
provider call. These tests drive `_run_model_build` with the real file builder and a stubbed tool
door, so what they check is the answer the operator gets, not the shape of the source.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

import pytest

from core.agent_runtime.builder import controller

REQUEST = "Create notes.txt containing a short shopping list."
CONTENT = "milk\nbread\napples\n"


@pytest.fixture(scope="module")
def agent():
    from apps.vool_agent import VoolAgent

    return VoolAgent(backend_name="test-backend", device="builder-approval-test", persona_id="default")


def _run(agent, tmp_path, *, write_result):
    tool_calls: list[str] = []

    def execute_tool_intent(payload, **kwargs):
        tool_calls.append(str(payload.get("intent")))
        if payload.get("intent") == "workspace.write_file":
            return write_result
        return SimpleNamespace(handled=True, ok=True, mode="tool_executed", status="executed",
                               response_text="", details={})

    def ollama_generate_fn(**kwargs):
        def generate(prompt: str) -> str:
            return CONTENT

        generate.budget = kwargs["budget"]  # type: ignore[attr-defined]
        return generate

    local = SimpleNamespace(provider_id="ollama-local:coder:7b", model_name="coder:7b",
                            runtime_config={"base_url": "http://127.0.0.1:11434"})
    workspace = str(tmp_path)
    with (
        mock.patch.object(agent, "_execute_tool_intent", side_effect=execute_tool_intent),
        mock.patch.object(controller, "build_ollama_generate_fn", side_effect=ollama_generate_fn),
        # The local model choice is not under test here; any eligible local lane will do.
        mock.patch.object(controller, "select_auto_builder_manifest", return_value=local, create=True),
    ):
        result = controller._run_model_build(
            agent,
            task=SimpleNamespace(task_id="task-build-approval"),
            effective_input=REQUEST,
            classification={"task_class": "system_design"},
            interpretation=None,
            session_id="build-approval-test",
            source_context={"workspace": workspace, "workspace_root": workspace,
                            "operating_mode": "manual", "surface": "api", "persist_memory": True},
            target={"root_dir": "", "platform": "generic", "language": "text"},
            load_active_persona_fn=lambda *a, **k: None,
        )
    return result, tool_calls


def test_a_write_waiting_for_approval_pauses_the_build_on_that_approval(agent, tmp_path) -> None:
    approval_text = "Approval required before writing notes.txt."
    pending = SimpleNamespace(
        handled=True, ok=False, mode="tool_preview", status="pending_approval",
        response_text=approval_text,
        details={"approval_request": {"tool_name": "workspace.write_file", "token": "approval-1"}},
    )
    with mock.patch.object(agent, "_action_fast_path_result", wraps=agent._action_fast_path_result) as action:
        result, tool_calls = _run(agent, tmp_path, write_result=pending)

    assert result["response"].startswith(approval_text), result["response"]
    assert result["mode"] == "tool_preview", result
    assert tool_calls.count("workspace.write_file") == 1, tool_calls
    call = action.call_args.kwargs
    assert call["reason"] == "builder_controller_pending_approval"
    assert call["task_outcome"] == "pending_approval"
    # A pause is not the turn's answer: the approval card carries it, and the approved resume
    # records the one final result (the same rule the scaffold lane follows).
    assert call["source_context"]["persist_memory"] is False
    # The approval request rides along for the activity record, never its token.
    assert call["details"]["approval_request"]["tool_name"] == "workspace.write_file"
    assert "approval-1" not in repr(call["details"])
    assert not (tmp_path / "notes.txt").exists()


def test_a_refused_write_reports_the_refusal_not_the_model(agent, tmp_path) -> None:
    refusal = "Writing notes.txt was declined."
    refused = SimpleNamespace(
        handled=True, ok=False, mode="tool_failed", status="denied",
        response_text=refusal, details={},
    )
    with mock.patch.object(agent, "_action_fast_path_result", wraps=agent._action_fast_path_result) as action:
        result, tool_calls = _run(agent, tmp_path, write_result=refused)

    assert refusal in result["response"], result["response"]
    assert "model did not return" not in result["response"]
    assert result["success"] is False
    assert tool_calls.count("workspace.write_file") == 1, tool_calls
    call = action.call_args.kwargs
    assert call["reason"] == "builder_model_build_tool_failed"
    assert call["task_outcome"] == "failed"
    assert not (tmp_path / "notes.txt").exists()
