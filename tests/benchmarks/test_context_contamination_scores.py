from __future__ import annotations

from core.llm_eval.metrics import score_context_scenario


def test_context_score_marks_clean_followup_correct() -> None:
    result = score_context_scenario(
        scenario_id="clean",
        response_text="Current time in Vilnius is 12:32 EET.",
        expected_terms=["vilnius", "time"],
        forbidden_terms=["toly", "solana"],
    )

    assert result["status"] == "correct"
    assert result["stale_hits"] == []


def test_context_score_marks_stale_reuse_as_contaminated() -> None:
    result = score_context_scenario(
        scenario_id="dirty",
        response_text="Toly is still the Solana founder.",
        expected_terms=["323"],
        forbidden_terms=["toly", "solana"],
    )

    assert result["status"] == "contaminated"
    assert result["stale_hits"] == ["toly", "solana"]
