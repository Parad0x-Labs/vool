from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core.execution import operator_tools as extracted_operator_tools
from core.tool_intent_executor import _build_operator_action_intent, _execute_operator_tool


def test_operator_tool_facade_builds_same_action_intent_as_extracted_module() -> None:
    arguments = {
        "source_path": "/tmp/source.txt",
        "destination_path": "/tmp/dest",
    }
    assert _build_operator_action_intent("move_path", arguments) == extracted_operator_tools.build_operator_action_intent(
        "move_path",
        arguments,
    )


def test_operator_tool_facade_matches_extracted_module_execution_shape() -> None:
    dispatch = SimpleNamespace(
        ok=True,
        status="reported",
        response_text="Visible services or startup agents:\n- launchd.test: running",
        details={"services": [{"name": "launchd.test", "state": "running"}]},
        learned_plan=None,
    )
    with mock.patch("core.tool_intent_executor.dispatch_operator_action", return_value=dispatch):
        result = _execute_operator_tool(
            "operator.inspect_services",
            {},
            task_id="task-123",
            session_id="session-123",
        )

    expected = extracted_operator_tools.execute_operator_tool(
        "operator.inspect_services",
        {},
        task_id="task-123",
        session_id="session-123",
        dispatch_operator_action_fn=lambda *_args, **_kwargs: dispatch,
    )
    assert result == expected
