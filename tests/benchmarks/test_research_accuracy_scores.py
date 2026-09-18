from __future__ import annotations

from core.llm_eval.metrics import score_research_response


def test_research_score_rewards_supported_answer() -> None:
    result = score_research_response(
        scenario_id="supported",
        response_text="Source: https://core.telegram.org/bots/api Telegram Bot API docs remain the canonical reference.",
        expected_sources=["core.telegram.org", "telegram bot api"],
    )

    assert result["status"] in {"correct", "partial"}
    assert result["citation_validity"] == 1.0


def test_research_score_requires_honest_refusal_when_requested() -> None:
    result = score_research_response(
        scenario_id="uncertain",
        response_text="I can't verify that from current evidence.",
        expected_sources=[],
        must_refuse=True,
    )

    assert result["status"] == "correct"
    assert result["uncertainty_honesty"] == 1.0
