"""A passing unrelated step must never clear a failed command."""
from types import SimpleNamespace

import pytest

from core.agent_runtime.builder import controller
from core.execution.models import ToolIntentExecution, WorkflowPlannerDecision
from tests.test_agent_runtime_builder import _build_agent


@pytest.mark.parametrize("case", [
    "same_command", "different_command", "different_cwd", "missing_identity",
    "retry_failed", "no_retry", "approval", "refusal", "budget", "unhandled",
])
def test_command_failure_requires_matching_successful_retry(case):
    command = "python3 app.py"
    details = {"command": command, "cwd": ".", "returncode": 1}
    first = ToolIntentExecution(True, False, "command_failed", mode="tool_failed",
                                tool_name="sandbox.run_command", details=details)
    results = [first]
    if case == "approval":
        first.mode, first.status = "tool_preview", "pending_approval"
    elif case == "refusal":
        first.status = "policy_denied"
    elif case != "no_retry":
        retry_details = {"command": command, "cwd": ".", "returncode": 0}
        if case == "different_command":
            retry_details["command"] = "python3 other.py"
        elif case == "different_cwd":
            retry_details["cwd"] = "other"
        elif case == "missing_identity":
            first.details = {"returncode": 1}
            retry_details = {"returncode": 0}
        elif case == "retry_failed":
            retry_details["returncode"] = 2
        if case == "budget":
            results.extend(ToolIntentExecution(True, True, "executed", mode="tool_executed",
                                              tool_name="workspace.read_file") for _ in range(5))
        results.append(ToolIntentExecution(
            case != "unhandled", case != "retry_failed",
            "command_failed" if case == "retry_failed" else "executed",
            mode="tool_failed" if case == "retry_failed" else "tool_executed",
            tool_name="sandbox.run_command", details=retry_details,
        ))

    executed = []
    plans = []

    def plan(**kwargs):
        plans.append(len(kwargs["executed_steps"]))
        index = len(kwargs["executed_steps"])
        if index < len(results):
            return WorkflowPlannerDecision(True, "concrete_next_step", next_payload={
                "intent": results[index].tool_name, "arguments": {},
                "trusted_local_only": True,
            })
        return WorkflowPlannerDecision(True, "done", stop_after=True)

    def execute(_payload, **kwargs):
        # A planner follow-up must never turn itself into trusted-local execution.
        assert not kwargs["trusted_local_only"]
        value = results[len(executed)]
        executed.append(value)
        return value

    steps, _context, stop, failed = controller.run_bounded_builder_loop(
        _build_agent(), task=SimpleNamespace(task_id="recovery-test"), session_id="recovery-test",
        effective_input="run the check and retry after repair", task_class="build",
        source_context={}, initial_payloads=[{"intent": first.tool_name, "arguments": {}}],
        plan_tool_workflow_fn=plan, execute_tool_intent_fn=execute,
    )
    if case == "same_command":
        assert failed is None and len(steps) == 2
        assert steps[0]["mode"] == "tool_failed"
    else:
        assert failed is not None
    if case in {"approval", "refusal"}:
        assert len(executed) == 1 and not plans
    if case == "budget":
        assert len(executed) == 6 and stop == "step_budget_exhausted"
