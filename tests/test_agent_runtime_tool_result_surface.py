from __future__ import annotations

from types import SimpleNamespace

from apps.vool_agent import ResponseClass, VoolAgent
from core.agent_runtime import (
    tool_result_history_surface,
    tool_result_text_surface,
    tool_result_truth_metrics,
    tool_result_workflow_surface,
)


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_chat_truth_claim_metrics_facade_matches_extracted_module() -> None:
    agent = _build_agent()

    assert agent._chat_truth_claim_metrics(
        "We checked the workspace receipts and updated the task log.",
        tool_backing_sources=["workspace"],
    ) == tool_result_truth_metrics.ToolResultTruthMetricsMixin._chat_truth_claim_metrics(
        agent,
        "We checked the workspace receipts and updated the task log.",
        tool_backing_sources=["workspace"],
    )


def test_sanitize_user_chat_text_facade_matches_extracted_module() -> None:
    agent = _build_agent()

    assert agent._sanitize_user_chat_text(
        "First, I'll inspect the workspace and then report back.",
        response_class=ResponseClass.GENERIC_CONVERSATION,
        allow_planner_style=False,
    ) == tool_result_text_surface.ToolResultTextSurfaceMixin._sanitize_user_chat_text(
        agent,
        "First, I'll inspect the workspace and then report back.",
        response_class=ResponseClass.GENERIC_CONVERSATION,
        allow_planner_style=False,
    )


def test_tool_history_observation_message_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    execution = SimpleNamespace(
        details={"observation": {"schema": "tool_observation_v1", "intent": "workspace.search_text", "tool_surface": "workspace"}},
        response_text="Search matches for runtime_checkpoint",
        ok=True,
        status="executed",
        mode="tool_executed",
        tool_name="workspace.search_text",
    )

    assert agent._tool_history_observation_message(
        execution=execution,
        tool_name="workspace.search_text",
    ) == tool_result_history_surface.ToolResultHistorySurfaceMixin._tool_history_observation_message(
        agent,
        execution=execution,
        tool_name="workspace.search_text",
    )


def test_tool_intent_workflow_summary_facade_matches_extracted_module() -> None:
    agent = _build_agent()

    assert agent._tool_intent_workflow_summary(
        tool_name="workspace.search_text",
        dispatch_status="executed",
        provider_id="local-qwen",
        validation_state="passed",
    ) == tool_result_workflow_surface.ToolResultWorkflowSurfaceMixin._tool_intent_workflow_summary(
        agent,
        tool_name="workspace.search_text",
        dispatch_status="executed",
        provider_id="local-qwen",
        validation_state="passed",
    )
