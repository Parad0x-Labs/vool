from __future__ import annotations

from unittest import mock

from core.curiosity_roamer import CuriosityResult
from core.memory_first_router import ModelExecutionDecision


def test_research_lane_uses_planned_search_for_fresh_updates(make_agent, context_result_factory, enable_web) -> None:
    agent = make_agent()
    agent.context_loader.load = mock.Mock(return_value=context_result_factory())  # type: ignore[assignment]
    agent.memory_router.resolve = mock.Mock(  # type: ignore[assignment]
        return_value=ModelExecutionDecision(
            source="provider",
            task_hash="fresh-web",
            provider_id="ollama:qwen",
            used_model=True,
            output_text="Telegram Bot API docs are the canonical source for these updates.",
            confidence=0.84,
            trust_score=0.84,
        )
    )
    agent.curiosity.maybe_roam = mock.Mock(  # type: ignore[assignment]
        return_value=CuriosityResult(enabled=False, mode="off", reason="test")
    )

    with mock.patch(
        "core.agent_runtime.agent.WebAdapter.planned_search_query",
        return_value=[
            {
                "summary": "Telegram Bot API docs are the canonical source for Bot API updates.",
                "confidence": 0.67,
                "source_profile_id": "messaging_platform_docs",
                "source_profile_label": "Messaging platform docs",
                "result_title": "Telegram Bot API",
                "result_url": "https://core.telegram.org/bots/api",
                "origin_domain": "core.telegram.org",
            }
        ],
    ):
        result = agent.run_once(
            "latest telegram bot api updates",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    assert result["response_class"] == "utility_answer"
    assert "telegram bot api" in result["response"].lower()
    assert "canonical source" in result["response"].lower()


def test_ultra_fresh_research_question_stays_honest(make_agent) -> None:
    agent = make_agent()

    with mock.patch.object(
        agent,
        "_live_info_search_notes",
        side_effect=AssertionError("ultra-fresh honesty path should not hit live search"),
    ):
        result = agent.run_once(
            "What happened five minutes ago in global markets?",
            source_context={"surface": "openclaw", "platform": "openclaw"},
        )

    lowered = result["response"].lower()
    assert any(phrase in lowered for phrase in ("can't verify", "insufficient evidence", "could not ground a current answer"))
