from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from core.execution import web_tools as extracted_web_tools
from core.execution.models import _tool_observation
from core.tool_intent_executor import _execute_web_tool


def test_web_tool_facade_matches_extracted_module_for_search(enable_web) -> None:
    planned_rows = [
        {
            "result_title": "Qwen release notes",
            "result_url": "https://example.test/qwen",
            "summary": "Fresh update summary",
            "source_profile_label": "Official docs",
        }
    ]
    with mock.patch("core.tool_intent_executor.load_builtin_tools", return_value=None), mock.patch(
        "core.tool_intent_executor.WebAdapter.planned_search_query",
        return_value=planned_rows,
    ):
        result = _execute_web_tool(
            "web.search",
            {"query": "latest qwen release notes", "limit": 2},
            task_id="task-123",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    expected = extracted_web_tools.execute_web_tool(
        "web.search",
        {"query": "latest qwen release notes", "limit": 2},
        task_id="task-123",
        source_context={"surface": "openclaw", "platform": "openclaw"},
        allow_web_fallback_fn=lambda: True,
        load_builtin_tools_fn=lambda: None,
        planned_search_query_fn=lambda *_args, **_kwargs: planned_rows,
        call_tool_fn=lambda *_args, **_kwargs: [],
        adaptive_research_fn=lambda **_kwargs: None,
        unsupported_execution_for_intent_fn=lambda intent, *, status, **_kwargs: None,
        tool_observation_fn=_tool_observation,
        audit_log_fn=lambda *_args, **_kwargs: None,
    )
    assert result == expected


def test_web_tool_facade_matches_extracted_module_for_research_uncertainty(enable_web) -> None:
    research_result = SimpleNamespace(
        strategy="verify",
        actions_taken=["initial_search", "verify_claim"],
        queries_run=["verify claim"],
        notes=[],
        evidence_strength="none",
        admitted_uncertainty=True,
        uncertainty_reason="No grounded live evidence came back for this question.",
        stop_reason="weak_evidence",
    )
    with mock.patch("core.tool_intent_executor.CuriosityRoamer.adaptive_research", return_value=research_result):
        result = _execute_web_tool(
            "web.research",
            {"query": "verify a shaky claim"},
            task_id="task-123",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    expected = extracted_web_tools.execute_web_tool(
        "web.research",
        {"query": "verify a shaky claim"},
        task_id="task-123",
        source_context={"surface": "openclaw", "platform": "openclaw"},
        allow_web_fallback_fn=lambda: True,
        load_builtin_tools_fn=lambda: None,
        planned_search_query_fn=lambda *_args, **_kwargs: [],
        call_tool_fn=lambda *_args, **_kwargs: {},
        adaptive_research_fn=lambda **_kwargs: research_result,
        unsupported_execution_for_intent_fn=lambda intent, *, status, **_kwargs: None,
        tool_observation_fn=_tool_observation,
        audit_log_fn=lambda *_args, **_kwargs: None,
    )
    assert result == expected
