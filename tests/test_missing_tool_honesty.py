from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core.agent_runtime import turn_dispatch
from core.operator.parser import parse_operator_action_intent
from retrieval.web_adapter import WebAdapter


def _agent() -> SimpleNamespace:
    agent = SimpleNamespace()
    agent._resolve_runtime_task = mock.Mock(return_value=SimpleNamespace(task_id="task-missing-tool"))
    agent._update_runtime_checkpoint_context = mock.Mock()
    agent._update_task_class = mock.Mock()
    agent._emit_runtime_event = mock.Mock()
    agent._action_fast_path_result = mock.Mock(side_effect=lambda **kwargs: kwargs)
    agent._action_workflow_summary = mock.Mock(return_value={})
    return agent


def _prepare(
    text: str,
    *,
    parse_operator=mock.DEFAULT,
    dispatch_operator=mock.DEFAULT,
) -> tuple[dict, SimpleNamespace, mock.Mock, mock.Mock]:
    agent = _agent()
    parse_operator_fn = (
        mock.Mock(side_effect=parse_operator_action_intent)
        if parse_operator is mock.DEFAULT
        else parse_operator
    )
    dispatch_operator_fn = (
        mock.Mock()
        if dispatch_operator is mock.DEFAULT
        else dispatch_operator
    )
    result = turn_dispatch.prepare_turn_task_bundle(
        agent,
        effective_input=text,
        user_input=text,
        session_id="missing-tool-session",
        source_context={"surface": "api", "allow_remote_fetch": False},
        interpreted=SimpleNamespace(as_context=lambda: {"intent": "tool"}),
        classify_fn=mock.Mock(return_value={"task_class": "local_action"}),
        parse_channel_post_intent_fn=mock.Mock(return_value=(None, None)),
        dispatch_outbound_post_intent_fn=mock.Mock(),
        parse_operator_action_intent_fn=parse_operator_fn,
        dispatch_operator_action_fn=dispatch_operator_fn,
    )
    return result, agent, parse_operator_fn, dispatch_operator_fn


def test_nonexistent_named_tool_returns_honest_non_execution() -> None:
    prompt = (
        "Use a nonexistent local tool named delete_everything_now to delete temp.txt, "
        "then report what happened."
    )
    with mock.patch.object(WebAdapter, "research_query") as research, mock.patch.object(
        WebAdapter,
        "search_query",
    ) as search, mock.patch.object(WebAdapter, "planned_search_query") as planned:
        result, _agent_obj, parse_operator, dispatch_operator = _prepare(prompt)

    output = result["result"]
    response = str(output["response"]).lower()
    assert output["reason"] == "tool_honesty_missing_tool"
    assert output["route"] == "tool_honesty_missing_tool"
    assert output["mode_override"] == "tool_failed"
    assert output["success"] is False
    assert "no local tool named `delete_everything_now` is available" in response
    assert "nothing was executed" in response
    assert "no files were deleted or modified" in response
    assert output["details"]["executed"] is False
    parse_operator.assert_not_called()
    dispatch_operator.assert_not_called()
    research.assert_not_called()
    search.assert_not_called()
    planned.assert_not_called()


def test_known_named_cleanup_tool_is_not_rejected() -> None:
    prompt = "Use local tool named cleanup_temp_files to delete temp files."
    assert turn_dispatch._explicit_missing_local_tool_name(prompt) == ""
    intent = parse_operator_action_intent(prompt)
    assert intent is not None
    assert intent.kind == "cleanup_temp_files"


def test_destructive_cleanup_without_approval_remains_preview() -> None:
    prompt = "Use local tool named cleanup_temp_files to delete temp files."
    dispatch = mock.Mock(
        return_value=SimpleNamespace(
            status="approval_required",
            response_text="Approval is required. Nothing was executed.",
            learned_plan=None,
            ok=False,
            details={"executed": False},
        )
    )
    result, _agent_obj, parse_operator, dispatch_operator = _prepare(
        prompt,
        dispatch_operator=dispatch,
    )

    output = result["result"]
    assert output["mode_override"] == "tool_preview"
    assert output["task_outcome"] == "pending_approval"
    assert output["success"] is False
    parse_operator.assert_called()
    dispatch_operator.assert_called_once()


def test_direct_file_delete_without_supported_tool_does_not_scan_disk_or_execute() -> None:
    prompt = "Delete temp.txt and say it is done."
    result, _agent_obj, parse_operator, dispatch_operator = _prepare(prompt)

    output = result["result"]
    response = str(output["response"]).lower()
    assert output["reason"] == "action_honesty_no_execution"
    assert output["route"] == "action_honesty_no_execution"
    assert output["mode_override"] == "tool_failed"
    assert output["success"] is False
    assert "did not delete `temp.txt`" in response
    assert "nothing was executed" in response
    assert "no files were deleted or modified" in response
    parse_operator.assert_not_called()
    dispatch_operator.assert_not_called()
