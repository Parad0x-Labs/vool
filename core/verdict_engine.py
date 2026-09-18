from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from core.conflict_classifier import classify_conflict
from core.evidence_scorer import EvidenceScore, score_result
from network.assist_models import TaskResult


@dataclass(frozen=True)
class ReviewVerdict:
    outcome: str
    helpfulness_score: float
    quality_score: float
    harmful: bool
    notes: list[str]


@dataclass(frozen=True)
class ConsensusVerdict:
    verdict: str
    best_index: int | None
    margin: float
    similarity: float
    notes: list[str]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def review_task_result(result: TaskResult | dict[str, Any], capsule: dict[str, Any] | None, assignment_exists: bool) -> ReviewVerdict:
    evidence: EvidenceScore = score_result(result, capsule, assignment_exists)
    quality = _clamp(
        (0.35 * evidence.support_score)
        + (0.30 * evidence.constraint_fit)
        + (0.20 * evidence.coverage_score)
        + (0.15 * evidence.confidence_weight)
    )
    helpfulness = _clamp(
        (0.45 * evidence.support_score)
        + (0.30 * evidence.coverage_score)
        + (0.15 * evidence.constraint_fit)
        + (0.10 * evidence.confidence_weight)
    )

    if evidence.harmful:
        return ReviewVerdict("harmful", min(0.10, helpfulness), min(0.10, quality), True, evidence.notes)
    if not assignment_exists:
        return ReviewVerdict("rejected", min(0.20, helpfulness), min(0.25, quality), False, evidence.notes)
    if quality >= 0.72 and helpfulness >= 0.62:
        return ReviewVerdict("accepted", helpfulness, quality, False, evidence.notes)
    if quality >= 0.48 and helpfulness >= 0.38:
        return ReviewVerdict("partial", helpfulness, quality, False, evidence.notes)
    return ReviewVerdict("rejected", helpfulness, quality, False, evidence.notes)


def evaluate_consensus(results: list[TaskResult | dict[str, Any]]) -> ConsensusVerdict:
    normalized: list[TaskResult] = []
    invalid_rows = 0
    for result in results:
        if isinstance(result, TaskResult):
            normalized.append(result)
            continue
        try:
            normalized.append(TaskResult.model_validate(result))
        except ValidationError:
            invalid_rows += 1
    if len(normalized) < 2:
        notes = ["not_enough_results"]
        if invalid_rows:
            notes.append("invalid_result_rows_skipped")
        return ConsensusVerdict("insufficient_evidence", None, 0.0, 0.0, notes)

    scored: list[tuple[float, int]] = []
    for idx, result in enumerate(normalized):
        score = _clamp(
            (0.45 * float(result.confidence))
            + (0.30 * min(1.0, len(result.evidence) / 4.0))
            + (0.25 * min(1.0, len(result.abstract_steps) / 4.0))
        )
        scored.append((score, idx))
    scored.sort(reverse=True)

    best_score, best_index = scored[0]
    second_score, _ = scored[1]
    margin = best_score - second_score
    conflict = classify_conflict(normalized[scored[0][1]].summary, normalized[scored[1][1]].summary)

    if best_score < 0.45:
        notes = ["low_candidate_quality"]
        if invalid_rows:
            notes.append("invalid_result_rows_skipped")
        return ConsensusVerdict("insufficient_evidence", None, margin, conflict.similarity, notes)
    if conflict.state == "hard_conflict" and margin < 0.18:
        notes = ["hard_conflict"]
        if invalid_rows:
            notes.append("invalid_result_rows_skipped")
        return ConsensusVerdict("disputed", best_index, margin, conflict.similarity, notes)
    if conflict.state == "soft_conflict":
        notes = ["soft_conflict"]
        if invalid_rows:
            notes.append("invalid_result_rows_skipped")
        return ConsensusVerdict("accepted_with_conflict", best_index, margin, conflict.similarity, notes)
    notes = ["aligned"]
    if invalid_rows:
        notes.append("invalid_result_rows_skipped")
    return ConsensusVerdict("accepted", best_index, margin, conflict.similarity, notes)
