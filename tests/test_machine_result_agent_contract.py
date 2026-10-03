"""Machine result projection crosses the real agent adapter and durable checkpoint owner."""
from __future__ import annotations

import pytest

from apps.vool_agent import VoolAgent
from core.agent_runtime.fast_paths_machine import _machine_tool_fast_path_result
from core.runtime_continuity import create_runtime_checkpoint, get_runtime_checkpoint, list_runtime_session_events
from core.runtime_execution_tools import RuntimeExecutionResult


@pytest.mark.parametrize(
    ("execution_status", "ok", "checkpoint_status", "task_event"),
    [
        ("executed", True, "completed", "task_completed"),
        ("failed", False, "failed", "task_failed"),
        ("cancelled", False, "cancelled", "task_cancelled"),
        ("pending_approval", False, "pending_approval", "task_pending_approval"),
    ],
)
def test_real_agent_preserves_machine_checkpoint_and_event_truth(execution_status, ok, checkpoint_status, task_event):
    # The execution envelope is a controlled boundary; the adapter, events and checkpoint writer are real.
    session = "machine-contract-" + execution_status
    context = {"session_id": session, "runtime_session_id": session, "surface": "api"}
    checkpoint = create_runtime_checkpoint(session_id=session, request_text="inspect the machine", source_context=context)
    context["runtime_checkpoint_id"] = checkpoint["checkpoint_id"]
    details = {"observation": {"sample": "measured"}} if ok else {}
    if execution_status == "pending_approval":
        details["approval_request"] = {"approval_id": "contract-approval", "intent": "machine.ensure_directory"}
    execution = RuntimeExecutionResult(
        handled=True, ok=ok, status=execution_status, response_text="Measured result" if ok else "Work did not execute",
        details=details,
    )
    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    result = _machine_tool_fast_path_result(
        agent, user_input="inspect the machine", session_id=session, source_context=context,
        intent="machine.disk_usage", execution=execution, reason="machine_read_fast_path",
    )
    stored = get_runtime_checkpoint(checkpoint["checkpoint_id"])
    assert stored["status"] == checkpoint_status
    assert bool(stored["failure_text"]) == (not ok and execution_status != "pending_approval")
    events = list_runtime_session_events(session, after_seq=0, limit=100)
    kinds = [event["event_type"] for event in events]
    assert task_event in kinds
    if not ok:
        assert "task_completed" not in kinds
        assert "tool_executed" not in kinds
    else:
        assert kinds.index("tool_executed") < kinds.index("task_completed")
    if execution_status == "pending_approval":
        assert result["approval_request"]["approval_id"] == "contract-approval"
        assert result["task_outcome"] == "pending_approval"
    if execution_status == "cancelled":
        assert result["status"] == "cancelled" and result["success"] is False


def test_real_agent_default_checkpoint_still_completes():
    session = "machine-contract-default"
    context = {"session_id": session, "runtime_session_id": session, "surface": "api"}
    checkpoint = create_runtime_checkpoint(session_id=session, request_text="answer", source_context=context)
    context["runtime_checkpoint_id"] = checkpoint["checkpoint_id"]
    agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
    agent._fast_path_result(session_id=session, user_input="answer", response="Ready", confidence=0.9,
                            source_context=context, reason="machine_read_fast_path")
    assert get_runtime_checkpoint(checkpoint["checkpoint_id"])["status"] == "completed"
