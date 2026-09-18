"""R2 -- ARGUS-confirmed defect: core.agent_runtime.memory_runtime._single_candidate_failure_hint
claimed "`{name}` was the only model this turn was allowed to use" whenever exactly one model was
ATTEMPTED, regardless of how many were actually RANKED/eligible. False whenever the ranked pool
held more candidates than were ever tried -- e.g. the fallback time budget ran out before trying
the rest, or a policy block (explicit-heavy-lane failure, autopilot block) refused to try the rest.

Required cases (operator's exact matrix):
  A. attempted=[A], ranked=[A]                                    -> single-model wording permitted
  B. attempted=[A], ranked=[A,B,C], budget exhausted               -> must NOT claim "only model ... allowed"
  C. attempted=[A], ranked=[A,B], explicit-heavy policy block       -> must NOT falsely claim singularity
  D. explicit pin truly narrows ranked set to [A]                  -> preserve no-silent-substitution truth
"""
from __future__ import annotations

from types import SimpleNamespace

from core.agent_runtime.memory_runtime import _single_candidate_failure_hint


def _execution(*, source: str, attempted: list[str], ranked_candidates: list[str]) -> SimpleNamespace:
    return SimpleNamespace(
        source=source,
        details={"attempted": attempted, "ranked_candidates": ranked_candidates, "reason": source},
    )


# --- case A: genuinely singular pool -- current wording stays true --------------------------------


def test_case_a_genuinely_singular_pool_permits_only_model_wording() -> None:
    execution = _execution(source="all_ranked_providers_failed", attempted=["fixture:model-a"], ranked_candidates=["fixture:model-a"])
    message = _single_candidate_failure_hint(execution)
    assert "was the only model this turn was allowed to use" in message
    assert "model-a" in message


# --- case B: budget exhaustion -- must not claim singularity, must name the real cause -----------


def test_case_b_budget_exhaustion_does_not_claim_only_model_allowed() -> None:
    execution = _execution(
        source="provider_fallback_budget_exceeded",
        attempted=["fixture:model-a"],
        ranked_candidates=["fixture:model-a", "fixture:model-b", "fixture:model-c"],
    )
    message = _single_candidate_failure_hint(execution)
    assert "only model this turn was allowed to use" not in message
    assert "only model" not in message
    assert "budget" in message.lower()
    assert "model-a" in message


# --- case C: explicit-heavy policy block -- must not falsely claim singularity -------------------


def test_case_c_policy_block_does_not_falsely_claim_singularity() -> None:
    execution = _execution(
        source="explicit_heavy_lane_failed",
        attempted=["fixture:model-a"],
        ranked_candidates=["fixture:model-a", "fixture:model-b"],
    )
    message = _single_candidate_failure_hint(execution)
    assert "only model this turn was allowed to use" not in message
    assert "only model" not in message
    assert "model-a" in message
    # Must not expose the raw internal reason string verbatim.
    assert "explicit_heavy_lane_failed" not in message


def test_case_c_autopilot_block_does_not_falsely_claim_singularity() -> None:
    """Same policy-block family, different source value."""
    execution = _execution(
        source="autopilot_blocked",
        attempted=["fixture:model-a"],
        ranked_candidates=["fixture:model-a", "fixture:model-b"],
    )
    message = _single_candidate_failure_hint(execution)
    assert "only model this turn was allowed to use" not in message
    assert "model-a" in message


# --- case D: explicit pin genuinely narrows the ranked pool to one -- singularity is TRUE here ---


def test_case_d_explicit_pin_narrows_ranked_set_preserves_no_substitution_truth() -> None:
    """An explicit pin (ranking narrowed to exactly the pinned provider BEFORE any attempt) is a
    genuinely singular pool -- mechanically identical to case A, but exercised with pin-shaped
    data to prove the pin scenario specifically still gets the true, permitted wording, not
    accidentally caught by the new budget/policy branches."""
    execution = _execution(
        source="all_ranked_providers_failed",
        attempted=["ollama-local:qwen2.5:32b"],
        ranked_candidates=["ollama-local:qwen2.5:32b"],
    )
    message = _single_candidate_failure_hint(execution)
    assert "was the only model this turn was allowed to use" in message
    assert "won't answer as a different model" in message
    assert "qwen2.5:32b" in message


# --- controls: several attempted -> silent (unchanged), no details -> silent ----------------------


def test_several_attempted_still_returns_nothing() -> None:
    execution = _execution(
        source="all_ranked_providers_failed",
        attempted=["fixture:model-a", "fixture:model-b"],
        ranked_candidates=["fixture:model-a", "fixture:model-b"],
    )
    assert _single_candidate_failure_hint(execution) == ""


def test_missing_details_returns_nothing() -> None:
    execution = SimpleNamespace(source="all_ranked_providers_failed", details=None)
    assert _single_candidate_failure_hint(execution) == ""
