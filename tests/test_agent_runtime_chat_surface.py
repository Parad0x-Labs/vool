from __future__ import annotations

from unittest import mock

from apps.vool_agent import VoolAgent, ResponseClass
from core.agent_runtime import chat_surface


def _build_agent() -> VoolAgent:
    return VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")


def test_chat_surface_smalltalk_model_input_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.chat_surface.smalltalk_model_input",
        return_value="delegated smalltalk prompt",
    ) as smalltalk_model_input:
        result = agent._chat_surface_smalltalk_model_input(user_input="help", phrase="help")

    assert result == "delegated smalltalk prompt"
    smalltalk_model_input.assert_called_once_with(
        agent,
        user_input="help",
        phrase="help",
    )


def test_chat_surface_model_wording_result_facade_delegates_to_extracted_module() -> None:
    agent = _build_agent()

    with mock.patch(
        "core.agent_runtime.chat_surface.chat_surface_model_wording_result",
        return_value={"response": "delegated wording"},
    ) as chat_surface_model_wording_result:
        result = agent._chat_surface_model_wording_result(
            session_id="session-123",
            user_input="hello",
            source_context={"surface": "openclaw"},
            persona=object(),
            interpretation=object(),
            task_class="unknown",
            response_class=ResponseClass.GENERIC_CONVERSATION,
            reason="test_reason",
            model_input="hello",
            fallback_response="fallback",
            tool_backing_sources=["web_lookup"],
        )

    assert result == {"response": "delegated wording"}
    chat_surface_model_wording_result.assert_called_once_with(
        agent,
        session_id="session-123",
        user_input="hello",
        source_context={"surface": "openclaw"},
        persona=mock.ANY,
        interpretation=mock.ANY,
        task_class="unknown",
        response_class=ResponseClass.GENERIC_CONVERSATION,
        reason="test_reason",
        model_input="hello",
        fallback_response="fallback",
        allow_provider_inference=True,
        tool_backing_sources=["web_lookup"],
        response_postprocessor=None,
    )


def test_live_info_observations_marks_browser_rendering_when_used() -> None:
    observations = chat_surface.live_info_observations(
        query="latest qwen release",
        mode="fresh_lookup",
        notes=[
            {
                "result_title": "Qwen release notes",
                "origin_domain": "qwen.ai",
                "summary": "Qwen shipped a new release.",
                "result_url": "https://qwen.ai/release",
                "used_browser": True,
            }
        ],
    )

    assert observations["channel"] == "live_info"
    assert observations["browser_rendering_used"] is True
    assert observations["source_count"] == 1
    assert observations["sources"][0]["domain"] == "qwen.ai"


def test_hive_task_list_without_real_topics_falls_back_to_truthful_response(make_agent) -> None:
    agent = make_agent()

    result = agent._postprocess_hive_chat_surface_text(
        "Here are some Hive thoughts without the real task names.",
        response_class=ResponseClass.TASK_LIST,
        payload={
            "truth_label": "public-bridge-derived",
            "topics": [
                {
                    "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                    "title": "OpenClaw integration audit",
                    "status": "open",
                }
            ],
        },
        fallback_response="Hive truth: public-bridge-derived. Hive tasks:\n- [open] OpenClaw integration audit (#7d33994f)",
    )

    assert "openclaw integration audit" in result.lower()
    assert "hive thoughts without the real task names" not in result.lower()


def test_hive_selection_clarification_without_real_topics_falls_back_to_truthful_response(make_agent) -> None:
    agent = make_agent()

    result = agent._postprocess_hive_chat_surface_text(
        "It seems like you want to review a problem, but I need more detail.",
        response_class=ResponseClass.TASK_SELECTION_CLARIFICATION,
        payload={
            "truth_label": "public-bridge-derived",
            "topics": [
                {
                    "topic_id": "a951bf9d-dd40-4a7e-b78a-f8e2d94fb701",
                    "title": "Agent Commons: better human-visible watcher and task-flow UX",
                    "status": "researching",
                },
                {
                    "topic_id": "7d33994f-dd40-4a7e-b78a-f8e2d94fb702",
                    "title": "VOOL Trading Learning Desk",
                    "status": "researching",
                },
            ],
        },
        fallback_response=(
            "Hive truth: public-bridge-derived. I still have multiple real Hive tasks open. "
            "Pick one by name or short `#id` and I’ll start there.\n"
            "- [researching] Agent Commons: better human-visible watcher and task-flow UX (#a951bf9d)\n"
            "- [researching] VOOL Trading Learning Desk (#7d33994f)"
        ),
    )

    lowered = result.lower()
    assert "watcher" in lowered or "ux" in lowered
    assert "trading" in lowered
    assert "need more detail" not in lowered


def test_hive_truth_prefix_formats_visible_presence_freshness(make_agent) -> None:
    agent = make_agent()

    result = agent._hive_truth_prefix(
        {
            "truth_label": "watcher-derived",
            "presence_claim_state": "visible",
            "presence_truth_label": "watcher-derived",
            "presence_freshness_label": "fresh",
            "presence_age_seconds": 90,
        }
    )

    assert result == "Hive truth: watcher-derived. Presence truth: watcher-derived, fresh (2m old)."
