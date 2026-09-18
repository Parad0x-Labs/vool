from __future__ import annotations

from .models import ToolIntentExecution, WorkflowPlannerDecision

__all__ = [
    "ToolIntentExecution",
    "WorkflowPlannerDecision",
    "_looks_like_workspace_bootstrap_request",
    "plan_tool_workflow",
    "should_attempt_tool_intent",
]


def __getattr__(name: str):
    if name in {"_looks_like_workspace_bootstrap_request", "plan_tool_workflow", "should_attempt_tool_intent"}:
        from . import planner

        return getattr(planner, name)
    raise AttributeError(name)
