from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from core import brain_hive_write_support
from core.brain_hive_models import (
    HiveModerationReviewRecord,
    HiveModerationReviewRequest,
    HiveModerationReviewSummary,
)
from core.privacy_guard import assert_public_text_safe
from storage.brain_hive_moderation_store import (
    apply_post_moderation,
    apply_topic_moderation,
    list_moderation_reviews,
    upsert_moderation_review,
)


def review_object(service: Any, request: HiveModerationReviewRequest) -> HiveModerationReviewSummary:
    public_surface = False
    if request.object_type == "topic":
        topic = service.get_topic(request.object_id, include_flagged=True)
        public_surface = brain_hive_write_support.visibility_requires_public_guard(topic.visibility)
    else:
        row = brain_hive_write_support.load_post_row(request.object_id)
        public_surface = brain_hive_write_support.post_requires_public_guard(row)
        author_display_name, author_claim_label = service._display_fields(str(row["author_agent_id"]))
        service._post_model_cls(
            **row,
            author_display_name=author_display_name,
            author_claim_label=author_claim_label,
        )
    if public_surface and request.note is not None:
        assert_public_text_safe(request.note, field_name="Hive moderation review note")
    weight = service._reviewer_weight(request.reviewer_agent_id)
    upsert_moderation_review(
        object_type=request.object_type,
        object_id=request.object_id,
        reviewer_agent_id=request.reviewer_agent_id,
        decision=request.decision,
        weight=weight,
        note=request.note,
        metadata={"weight_source": "scoreboard"},
    )
    summary = get_review_summary(service, request.object_type, request.object_id)
    if summary.quorum_reached and summary.applied_state and summary.applied_state != summary.current_state:
        _apply_review_state(
            service,
            object_type=request.object_type,
            object_id=request.object_id,
            actor_agent_id=request.reviewer_agent_id,
            current_state=summary.current_state,
            applied_state=summary.applied_state,
            decision_weights=summary.decision_weights,
        )
        summary = get_review_summary(service, request.object_type, request.object_id)
    return summary


def get_review_summary(service: Any, object_type: str, object_id: str) -> HiveModerationReviewSummary:
    reviews = list_reviews(service, object_type=object_type, object_id=object_id, limit=200)
    decision_weights: defaultdict[str, float] = defaultdict(float)
    for review in reviews:
        decision_weights[str(review.decision)] += float(review.weight or 0.0)
    current_state = _current_moderation_state(service, object_type=object_type, object_id=object_id)
    applied_state = _quorum_applied_state(decision_weights)
    quorum_reached = applied_state is not None
    return HiveModerationReviewSummary(
        object_type=object_type,
        object_id=object_id,
        current_state=current_state,
        quorum_reached=quorum_reached,
        total_reviews=len(reviews),
        decision_weights={key: round(value, 3) for key, value in sorted(decision_weights.items())},
        applied_state=applied_state,
        reviews=reviews,
    )


def list_reviews(service: Any, *, object_type: str, object_id: str, limit: int = 200) -> list[HiveModerationReviewRecord]:
    rows = list_moderation_reviews(object_type=object_type, object_id=object_id, limit=limit)
    out: list[HiveModerationReviewRecord] = []
    for row in rows:
        reviewer_display_name, reviewer_claim_label = service._display_fields(str(row["reviewer_agent_id"]))
        out.append(
            HiveModerationReviewRecord(
                **row,
                reviewer_display_name=reviewer_display_name,
                reviewer_claim_label=reviewer_claim_label,
            )
        )
    return out


def _current_moderation_state(service: Any, *, object_type: str, object_id: str) -> str:
    if object_type == "topic":
        return service.get_topic(object_id, include_flagged=True).moderation_state
    return str(brain_hive_write_support.load_post_row(object_id).get("moderation_state") or "review_required")


def _quorum_applied_state(decision_weights: dict[str, float]) -> str | None:
    if not decision_weights:
        return None
    ranked = sorted(
        ((str(key), float(value or 0.0)) for key, value in decision_weights.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    top_decision, top_weight = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if top_weight < 2.0 or top_weight < runner_up + 0.5:
        return None
    mapping = {
        "approve": "approved",
        "review_required": "review_required",
        "quarantine": "quarantined",
        "void": "voided",
    }
    return mapping.get(top_decision)


def _apply_review_state(
    service: Any,
    *,
    object_type: str,
    object_id: str,
    actor_agent_id: str,
    current_state: str,
    applied_state: str,
    decision_weights: dict[str, float],
) -> None:
    reasons = [f"weighted review quorum applied: {json.dumps(decision_weights, sort_keys=True)}"]
    metadata = {"quorum_decision_weights": dict(decision_weights), "previous_state": current_state}
    top_weight = max((float(value or 0.0) for value in decision_weights.values()), default=0.0)
    score = round(min(1.0, top_weight / max(1.0, sum(float(value or 0.0) for value in decision_weights.values()))), 3)
    if object_type == "topic":
        apply_topic_moderation(
            topic_id=object_id,
            agent_id=actor_agent_id,
            moderation_state=applied_state,
            moderation_score=score,
            reasons=reasons,
            metadata=metadata,
        )
        return
    apply_post_moderation(
        post_id=object_id,
        agent_id=actor_agent_id,
        moderation_state=applied_state,
        moderation_score=score,
        reasons=reasons,
        metadata=metadata,
    )
