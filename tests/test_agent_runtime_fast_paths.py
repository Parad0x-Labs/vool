from __future__ import annotations

from types import SimpleNamespace
from unittest import mock

from apps.vool_agent import ResponseClass, VoolAgent
from core.agent_runtime import fast_paths


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_smalltalk_fast_path_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.fast_paths.smalltalk_fast_path",
        return_value="delegated reply",
    ) as smalltalk_fast_path:
        result = agent._smalltalk_fast_path(
            "gm",
            source_surface="openclaw",
            session_id="openclaw:test-smalltalk",
        )

    assert result == "delegated reply"
    smalltalk_fast_path.assert_called_once_with(
        agent,
        "gm",
        source_surface="openclaw",
        session_id="openclaw:test-smalltalk",
    )


def test_live_info_fast_path_facade_preserves_response_class_contract() -> None:
    agent = _build_agent()
    interpretation = SimpleNamespace(topic_hints=["news"])

    with mock.patch(
        "core.agent_runtime.fast_paths.maybe_handle_live_info_fast_path",
        return_value={"response": "delegated"},
    ) as maybe_handle_live_info_fast_path:
        result = agent._maybe_handle_live_info_fast_path(
            "latest news on OpenAI",
            session_id="session-live-info",
            source_context={"surface": "openclaw"},
            interpretation=interpretation,
        )

    assert result == {"response": "delegated"}
    maybe_handle_live_info_fast_path.assert_called_once_with(
        agent,
        "latest news on OpenAI",
        session_id="session-live-info",
        source_context={"surface": "openclaw"},
        interpretation=interpretation,
        response_class=ResponseClass.UTILITY_ANSWER,
    )


def test_builder_request_helpers_match_extracted_module_behavior() -> None:
    agent = _build_agent()
    query = "build a telegram bot from official docs and good github repos"

    assert agent._looks_like_builder_request(query) is fast_paths.looks_like_builder_request(query)


def test_builder_root_extraction_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    query = "create a folder named generated/telegram-bot and write the files"

    assert agent._extract_requested_builder_root(query) == fast_paths.extract_requested_builder_root(query)


def test_date_time_fast_path_does_not_treat_runtime_as_time_request() -> None:
    agent = _build_agent()

    result = agent._date_time_fast_path(
        "plan a safe patch for a runtime bug and explain what should be verified",
        source_surface="openclaw",
        session_id="openclaw:test-runtime-not-time",
        source_context={"surface": "openclaw"},
    )

    assert result is None


def test_builder_controller_does_not_capture_patch_plan_advice_only_prompt() -> None:
    agent = _build_agent()

    should_handle = agent._should_run_builder_controller(
        effective_input=(
            "Plan a safe patch for a hard local runtime bug: adaptive lane proof is missing from streamed events. "
            "Do not edit files; explain what should be verified."
        ),
        classification={"task_class": "system_design"},
        source_context={"workspace_root": "/tmp/workspace"},
    )

    assert should_handle is False


def test_live_info_rendering_facade_matches_extracted_module() -> None:
    agent = _build_agent()
    notes = [
        {
            "result_title": "Example result",
            "origin_domain": "example.com",
            "summary": "Fresh update from the example domain.",
            "result_url": "https://example.com/result",
        }
    ]

    assert agent._render_live_info_response(
        query="latest example update",
        notes=notes,
        mode="fresh_lookup",
    ) == fast_paths.render_live_info_response(
        query="latest example update",
        notes=notes,
        mode="fresh_lookup",
    )
