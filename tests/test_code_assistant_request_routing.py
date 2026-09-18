"""Short repair instructions must reach the existing tool lane without turning advice into actions."""
import pytest
from core.agent_runtime.runtime_checkpoint_lane_policy import explicit_runtime_workflow_request

@pytest.mark.parametrize("prompt", [
    "Find why the test fails and repair the root cause.",
    "The unit tests are failing; please correct the underlying defect.",
    "Could you fix the failing compiler checks?",
])
def test_short_repair_instructions_require_execution(prompt):
    assert explicit_runtime_workflow_request(user_input=prompt, task_class="debugging")
    from core.tool_demand_signals import resolve_demand_signals
    assert "code.task.open" in resolve_demand_signals(prompt).explicit_intents

@pytest.mark.parametrize("prompt", [
    "Explain why a unit test might fail.",
    "How do I repair a failing test?",
    "Do not repair the test. Explain the approach only.",
    "Show me an example of how to fix a failing test.",
    "If the test fails, would fixing the assertion hide the bug?",
])
def test_advice_and_prohibitions_remain_conversation(prompt):
    assert not explicit_runtime_workflow_request(user_input=prompt, task_class="debugging")

@pytest.mark.parametrize("prompt", [
    "Fix math.js so the existing test.js passes. Run the existing tests and explain the change briefly. Keep the tests unchanged.",
    "Please repair parser.py to satisfy the tests in checks.py. Execute the existing checks and summarize the correction.",
])
def test_explicit_repair_is_not_owned_by_read_only_audit(prompt):
    from core.agent_runtime.workspace_audit import looks_like_code_audit_request
    from core.tool_demand_signals import is_explicit_code_repair

    assert is_explicit_code_repair(prompt)
    assert not looks_like_code_audit_request(prompt)


@pytest.mark.parametrize("prompt", [
    "Audit math.js for correctness without changing any files.",
    "Review parser.py for bugs and report the findings only.",
])
def test_read_only_review_still_belongs_to_audit(prompt):
    from core.agent_runtime.workspace_audit import looks_like_code_audit_request

    assert looks_like_code_audit_request(prompt)
