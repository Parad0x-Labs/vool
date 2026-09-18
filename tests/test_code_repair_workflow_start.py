"""A typed repair request enters the existing task protocol despite a fallback class."""

import pytest

from core.execution.planner import plan_tool_workflow
from tests.test_code_assistant_task_runtime import _ctx, _door
from tests.test_code_assistant_task_runtime import fixture_repo as fixture_repo


@pytest.mark.parametrize(
    "prompt",
    [
        "Fix math.js so the existing test.js passes. Run the existing tests and explain the change briefly. Keep the tests unchanged.",
        "Please repair normalize.js to satisfy the tests in checks.js. Execute the existing checks and summarize the correction. Keep the tests unchanged.",
    ],
)
def test_repair_starts_the_real_task_protocol(fixture_repo, prompt):
    from core.code_assistant.task_runtime import active_task_control_intents

    context = _ctx(fixture_repo, operating_mode="manual")
    decision = plan_tool_workflow(
        user_text=prompt, task_class="shell_guidance", executed_steps=[], source_context=context
    )
    assert decision.next_payload == {"intent": "code.task.open", "arguments": {"objective": prompt}}
    before = (fixture_repo / "calc.py").read_bytes()
    result = _door(decision.next_payload["intent"], decision.next_payload["arguments"], context)
    assert result.ok
    assert result.details["stage"] == "reproduce"
    assert "code.task.step" in active_task_control_intents(context)
    assert (fixture_repo / "calc.py").read_bytes() == before, "Opening must not mutate source."


@pytest.mark.parametrize(
    "prompt",
    [
        "Explain why a unit test might fail.",
        "How do I repair a failing test?",
        "Do not repair the test. Explain the approach only.",
        "Show me an example of how to fix a failing test.",
        "If the test fails, would fixing the assertion hide the bug?",
    ],
)
def test_advice_does_not_start_work(prompt):
    result = plan_tool_workflow(user_text=prompt, task_class="debugging", executed_steps=[], source_context={})
    assert (result.next_payload or {}).get("intent") != "code.task.open"


def test_existing_work_does_not_open_a_replacement():
    result = plan_tool_workflow(
        user_text="Fix the failing tests.",
        task_class="debugging",
        executed_steps=[{"tool_name": "code.task.open", "status": "ok"}],
        source_context={},
    )
    assert (result.next_payload or {}).get("intent") != "code.task.open"
