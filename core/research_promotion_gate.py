from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class PromotionGateDecision:
    status: str
    can_promote: bool
    score: float
    reason: str
    missing_requirements: list[str] = field(default_factory=list)
    metrics: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "can_promote": self.can_promote,
            "score": self.score,
            "reason": self.reason,
            "missing_requirements": list(self.missing_requirements),
            "metrics": dict(self.metrics),
        }


def evaluate_research_promotion_candidate(
    candidate: dict[str, Any],
    *,
    research_packet: dict[str, Any],
) -> PromotionGateDecision:
    counts = dict(research_packet.get("counts") or {})
    trading = dict(research_packet.get("trading_feature_export") or {})
    candidate_kind = str(candidate.get("candidate_kind") or "heuristic").strip().lower() or "heuristic"
    support = int(candidate.get("support") or candidate.get("count") or 0)
    score_hint = float(candidate.get("score") or candidate.get("confidence") or 0.0)
    source_domain_count = int(counts.get("source_domain_count") or 0)
    evidence_count = int(counts.get("evidence_count") or 0)
    pattern_total = int(dict(trading.get("pattern_health") or {}).get("total_patterns") or 0)
    backtest = dict(candidate.get("evaluation") or {}).get("backtest")
    evaluation = dict(candidate.get("evaluation") or {})

    if candidate_kind == "finding":
        topic_overlap = int(evaluation.get("topic_overlap") or 0)
        specificity_score = float(evaluation.get("specificity_score") or 0.0)
        domain_count = int(evaluation.get("domain_count") or 0)
        snippet_count = int(evaluation.get("snippet_count") or support or 0)
        missing: list[str] = []
        gate_score = 0.20
        if snippet_count >= 1:
            gate_score += 0.18
        else:
            missing.append("need_grounded_snippets")
        if domain_count >= 1:
            gate_score += 0.16
        else:
            missing.append("need_source_domain")
        if topic_overlap >= 2:
            gate_score += 0.22
        else:
            missing.append("need_topic_overlap")
        if specificity_score >= 0.45:
            gate_score += 0.18
        else:
            missing.append("need_specificity")
        if score_hint >= 0.55:
            gate_score += 0.12
        else:
            missing.append("weak_signal_score")
        can_promote = not missing
        status = "approved" if can_promote else "candidate_only"
        reason = "finding_grounded" if can_promote else "finding_not_specific_enough"
        return PromotionGateDecision(
            status=status,
            can_promote=can_promote,
            score=round(max(0.0, min(1.0, gate_score)), 4),
            reason=reason,
            missing_requirements=missing,
            metrics={
                "candidate_kind": candidate_kind,
                "support": support,
                "score_hint": score_hint,
                "topic_overlap": topic_overlap,
                "specificity_score": specificity_score,
                "domain_count": domain_count,
                "snippet_count": snippet_count,
            },
        )

    missing: list[str] = []
    gate_score = 0.15
    if evidence_count >= 4:
        gate_score += 0.18
    else:
        missing.append("need_more_evidence_refs")
    if source_domain_count >= 2:
        gate_score += 0.18
    else:
        missing.append("need_more_source_diversity")
    if support >= 5:
        gate_score += 0.20
    else:
        missing.append("need_more_support")
    if score_hint >= 0.60:
        gate_score += 0.17
    else:
        missing.append("weak_signal_score")
    if pattern_total >= 100:
        gate_score += 0.10

    if candidate_kind == "script":
        if not backtest:
            missing.append("missing_backtest")
        else:
            gate_score += 0.12

    can_promote = not missing
    status = "approved" if can_promote else "candidate_only"
    reason = "promotion_gate_passed" if can_promote else "promotion_blocked_pending_evaluation"
    return PromotionGateDecision(
        status=status,
        can_promote=can_promote,
        score=round(max(0.0, min(1.0, gate_score)), 4),
        reason=reason,
        missing_requirements=missing,
        metrics={
            "candidate_kind": candidate_kind,
            "support": support,
            "score_hint": score_hint,
            "evidence_count": evidence_count,
            "source_domain_count": source_domain_count,
            "pattern_total": pattern_total,
            "has_backtest": bool(backtest),
        },
    )
