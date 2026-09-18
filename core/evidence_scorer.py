from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from network.assist_models import TaskResult


@dataclass(frozen=True)
class EvidenceScore:
    support_score: float
    constraint_fit: float
    coverage_score: float
    confidence_weight: float
    harmful: bool
    notes: list[str]


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def _tokenize(text: str) -> set[str]:
    chars = []
    for ch in (text or "").lower():
        chars.append(ch if ch.isalnum() else " ")
    return {tok for tok in "".join(chars).split() if len(tok) > 2}


def _overlap(parts_a: list[str], parts_b: list[str]) -> float:
    tokens_a: set[str] = set()
    tokens_b: set[str] = set()
    for part in parts_a:
        tokens_a |= _tokenize(part)
    for part in parts_b:
        tokens_b |= _tokenize(part)
    if not tokens_a or not tokens_b:
        return 0.5
    return len(tokens_a & tokens_b) / max(1, len(tokens_a | tokens_b))


def score_result(result: TaskResult | dict[str, Any], capsule: dict[str, Any] | None, assignment_exists: bool) -> EvidenceScore:
    obj = result if isinstance(result, TaskResult) else TaskResult.model_validate(result)
    notes: list[str] = []
    harmful = "scope_violation" in set(obj.risk_flags or [])
    if harmful:
        notes.append("scope_violation")

    confidence_weight = _clamp(float(obj.confidence))
    coverage_score = _clamp((len(obj.evidence) * 0.55 + len(obj.abstract_steps) * 0.45) / 6.0)

    if not assignment_exists:
        notes.append("unsolicited_result")
        return EvidenceScore(
            support_score=0.15,
            constraint_fit=0.15,
            coverage_score=coverage_score,
            confidence_weight=confidence_weight,
            harmful=False,
            notes=notes,
        )

    if not capsule:
        return EvidenceScore(
            support_score=_clamp((coverage_score * 0.6) + (confidence_weight * 0.4)),
            constraint_fit=0.50,
            coverage_score=coverage_score,
            confidence_weight=confidence_weight,
            harmful=harmful,
            notes=notes,
        )

    ctx = capsule.get("sanitized_context") or {}
    summary = str(capsule.get("summary") or "")
    abstract_inputs = list(ctx.get("abstract_inputs") or [])
    constraints = list(ctx.get("known_constraints") or [])
    support_score = _clamp(_overlap([obj.summary, *list(obj.evidence)], [summary, *abstract_inputs]))
    constraint_fit = _clamp(_overlap(list(obj.abstract_steps) + list(obj.evidence), constraints))

    if constraint_fit < 0.35:
        notes.append("weak_constraint_fit")
    if support_score < 0.35:
        notes.append("weak_support")

    return EvidenceScore(
        support_score=support_score,
        constraint_fit=constraint_fit,
        coverage_score=coverage_score,
        confidence_weight=confidence_weight,
        harmful=harmful,
        notes=notes,
    )
