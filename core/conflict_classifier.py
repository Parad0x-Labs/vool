from __future__ import annotations

from dataclasses import dataclass

from core.semantic_judge import evaluate_semantic_agreement


@dataclass(frozen=True)
class ConflictAssessment:
    similarity: float
    state: str


def classify_conflict(summary_a: str, summary_b: str) -> ConflictAssessment:
    similarity = float(evaluate_semantic_agreement(summary_a, summary_b))
    if similarity >= 0.82:
        state = "aligned"
    elif similarity >= 0.55:
        state = "soft_conflict"
    else:
        state = "hard_conflict"
    return ConflictAssessment(similarity=similarity, state=state)
