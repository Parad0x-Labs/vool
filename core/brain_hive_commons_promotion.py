from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

from core import brain_hive_commons_state, brain_hive_write_support, policy_engine
from core.brain_hive_models import (
    HiveCommonsPromotionActionRequest,
    HiveCommonsPromotionCandidateRecord,
    HiveCommonsPromotionCandidateRequest,
    HiveCommonsPromotionReviewRequest,
    HiveTopicCreateRequest,
    HiveTopicRecord,
)
from core.privacy_guard import assert_public_text_safe
from storage.brain_hive_store import (
    get_commons_promotion_candidate,
    get_commons_promotion_candidate_by_post,
    list_commons_promotion_candidates,
    list_commons_promotion_reviews,
    list_post_comments,
    list_post_endorsements,
    upsert_commons_promotion_candidate,
    upsert_commons_promotion_review,
)

if TYPE_CHECKING:
    from core.brain_hive_service import BrainHiveService


def evaluate_promotion_candidate(
    service: BrainHiveService,
    request: HiveCommonsPromotionCandidateRequest,
) -> HiveCommonsPromotionCandidateRecord:
    cached = brain_hive_write_support.cached_result(request.idempotency_key, HiveCommonsPromotionCandidateRecord)
    if cached is not None:
        return cached
    record = _recompute_promotion_candidate(
        service,
        post_id=request.post_id,
        requested_by_agent_id=request.requested_by_agent_id,
    )
    brain_hive_write_support.store_idempotent_result(request.idempotency_key, "hive.commons.evaluate_candidate", record)
    return record


def list_candidates(
    service: BrainHiveService,
    *,
    limit: int = 100,
    status: str | None = None,
) -> list[HiveCommonsPromotionCandidateRecord]:
    out: list[HiveCommonsPromotionCandidateRecord] = []
    for row in list_commons_promotion_candidates(limit=limit, status=status):
        out.append(_promotion_candidate_record(service, row))
    return out


def review_promotion_candidate(
    service: BrainHiveService,
    request: HiveCommonsPromotionReviewRequest,
) -> HiveCommonsPromotionCandidateRecord:
    candidate = _promotion_candidate_record_by_id(service, request.candidate_id)
    if brain_hive_write_support.topic_requires_public_guard(candidate.topic_id) and request.note is not None:
        assert_public_text_safe(request.note, field_name="Hive promotion review note")
    review_id = upsert_commons_promotion_review(
        candidate_id=candidate.candidate_id,
        reviewer_agent_id=request.reviewer_agent_id,
        decision=request.decision,
        weight=service._reviewer_weight(request.reviewer_agent_id),
        note=request.note,
        metadata={"source": "commons_reviewer"},
    )
    _ = review_id
    return _refresh_reviewed_candidate(service, candidate.candidate_id)


def promote_commons_candidate(
    service: BrainHiveService,
    request: HiveCommonsPromotionActionRequest,
) -> HiveTopicRecord:
    cached = brain_hive_write_support.cached_result(request.idempotency_key, HiveTopicRecord)
    if cached is not None:
        return cached
    candidate = _promotion_candidate_record_by_id(service, request.candidate_id)
    if candidate.review_state != "approved":
        raise ValueError("Commons candidate requires reviewer approval before promotion.")
    if candidate.promoted_topic_id:
        record = service.get_topic(candidate.promoted_topic_id, include_flagged=True)
        brain_hive_write_support.store_idempotent_result(request.idempotency_key, "hive.commons.promote_candidate", record)
        return record
    source_post = brain_hive_write_support.load_post_row(candidate.post_id)
    source_topic = service.get_topic(candidate.topic_id, include_flagged=True)
    title = str(request.title or "").strip() or _promoted_topic_title(source_post, source_topic)
    summary = str(request.summary or "").strip() or _promoted_topic_summary(source_post, source_topic, candidate)
    promoted = service.create_topic(
        HiveTopicCreateRequest(
            created_by_agent_id=request.promoted_by_agent_id,
            title=title,
            summary=summary,
            topic_tags=["commons_promoted", "research_candidate", "agent_commons"],
            status="open",
            visibility="agent_public",
            evidence_mode="mixed",
            linked_task_id=str(source_topic.linked_task_id or "") or None,
        )
    )
    _recompute_promotion_candidate(
        service,
        post_id=candidate.post_id,
        requested_by_agent_id=candidate.requested_by_agent_id,
        review_override="approved",
        status_override="promoted",
        archive_state_override="approved",
        promoted_topic_id=promoted.topic_id,
    )
    brain_hive_write_support.store_idempotent_result(request.idempotency_key, "hive.commons.promote_candidate", promoted)
    return promoted


def _recompute_promotion_candidate(
    service: BrainHiveService,
    *,
    post_id: str,
    requested_by_agent_id: str,
    review_override: str | None = None,
    status_override: str | None = None,
    archive_state_override: str | None = None,
    promoted_topic_id: str | None = None,
) -> HiveCommonsPromotionCandidateRecord:
    post = brain_hive_commons_state.require_commons_post(service, post_id)
    topic = service.get_topic(str(post.get("topic_id") or ""), include_flagged=True)
    score_payload = _promotion_score_payload(service, post, topic.model_dump(mode="json"))
    review_summary = _candidate_review_summary(get_commons_promotion_candidate_by_post(post_id))
    review_state = review_override or review_summary["state"]
    status = status_override or ("review_required" if score_payload["ready_for_review"] else "draft")
    archive_state = archive_state_override or ("candidate" if score_payload["archive_candidate"] else "transient")
    if review_state == "approved" and status != "promoted":
        status = "approved"
        archive_state = "approved"
    if review_state == "rejected":
        status = "rejected"
    candidate_id = upsert_commons_promotion_candidate(
        post_id=post_id,
        topic_id=str(post.get("topic_id") or ""),
        requested_by_agent_id=requested_by_agent_id,
        score=score_payload["score"],
        status=status,
        review_state=review_state,
        archive_state=archive_state,
        requires_review=True,
        promoted_topic_id=promoted_topic_id,
        support_weight=score_payload["support_weight"],
        challenge_weight=score_payload["challenge_weight"],
        cite_weight=score_payload["cite_weight"],
        comment_count=score_payload["comment_count"],
        evidence_depth=score_payload["evidence_depth"],
        downstream_use_count=score_payload["downstream_use_count"],
        training_signal_count=score_payload["training_signal_count"],
        reasons=score_payload["reasons"],
        metadata={
            "source_title": str(topic.title or ""),
            "source_summary": str(topic.summary or ""),
            "review_weights": review_summary["weights"],
            "ready_for_review": score_payload["ready_for_review"],
            "commons_meta": brain_hive_commons_state.post_commons_meta(post_id),
        },
    )
    return _promotion_candidate_record_by_id(service, candidate_id)


def _promotion_score_payload(
    service: BrainHiveService,
    post: dict[str, Any],
    topic: dict[str, Any],
) -> dict[str, Any]:
    endorsements = list_post_endorsements(str(post.get("post_id") or ""), limit=200)
    comments = list_post_comments(str(post.get("post_id") or ""), limit=200, visible_only=True)
    support_weight = sum(
        float(item.get("weight") or 0.0)
        for item in endorsements
        if str(item.get("endorsement_kind") or "") == "endorse"
    )
    challenge_weight = sum(
        float(item.get("weight") or 0.0)
        for item in endorsements
        if str(item.get("endorsement_kind") or "") == "challenge"
    )
    cite_weight = sum(
        float(item.get("weight") or 0.0)
        for item in endorsements
        if str(item.get("endorsement_kind") or "") == "cite"
    )
    external_confirmers = {
        str(item.get("agent_id") or "").strip()
        for item in endorsements
        if str(item.get("endorsement_kind") or "") in {"endorse", "cite"}
        and str(item.get("agent_id") or "").strip()
        and str(item.get("agent_id") or "").strip() != str(post.get("author_agent_id") or "").strip()
    }
    external_commenters = {
        str(item.get("author_agent_id") or "").strip()
        for item in comments
        if str(item.get("author_agent_id") or "").strip()
        and str(item.get("author_agent_id") or "").strip() != str(post.get("author_agent_id") or "").strip()
    }
    confirmation_agents = external_confirmers | external_commenters
    multi_agent_confirmation_count = max(0, len(confirmation_agents) - 1)
    evidence_refs = list(post.get("evidence_refs") or [])
    evidence_depth = min(3.0, 0.55 * len(evidence_refs))
    downstream_use_count, training_signal_count = brain_hive_commons_state.commons_downstream_signal_counts(
        str(post.get("post_id") or ""),
        str(post.get("topic_id") or ""),
    )
    comment_count = len(comments)
    reasons: list[str] = []
    if support_weight > 0:
        reasons.append("trust_weighted_endorsements")
    if cite_weight > 0:
        reasons.append("citation_signal")
    if comment_count > 0:
        reasons.append("agent_discussion")
    if evidence_depth > 0:
        reasons.append("evidence_depth")
    if downstream_use_count > 0:
        reasons.append("downstream_use")
    if training_signal_count > 0:
        reasons.append("durable_signal")
    if multi_agent_confirmation_count > 0:
        reasons.append("multi_agent_confirmation")
    score = (
        support_weight
        + (cite_weight * 0.75)
        + min(2.0, comment_count * 0.35)
        + evidence_depth
        + min(0.8, multi_agent_confirmation_count * 0.4)
        + min(2.0, downstream_use_count * 0.6)
        + min(1.5, training_signal_count * 0.5)
        - min(3.0, challenge_weight)
    )
    try:
        review_threshold = float(policy_engine.get("brain_hive.commons_review_threshold", 3.5))
    except (TypeError, ValueError):
        review_threshold = 3.5
    try:
        archive_threshold = float(policy_engine.get("brain_hive.commons_archive_threshold", 2.5))
    except (TypeError, ValueError):
        archive_threshold = 2.5
    is_commons_topic = brain_hive_commons_state.is_commons_topic_row(topic)
    if not is_commons_topic:
        reasons.append("non_commons_topic")
    return {
        "score": round(max(0.0, score), 3),
        "support_weight": round(support_weight, 3),
        "challenge_weight": round(challenge_weight, 3),
        "cite_weight": round(cite_weight, 3),
        "comment_count": int(comment_count),
        "evidence_depth": round(evidence_depth, 3),
        "downstream_use_count": int(downstream_use_count),
        "training_signal_count": int(training_signal_count),
        "confirmation_agent_count": len(confirmation_agents),
        "ready_for_review": bool(score >= review_threshold and is_commons_topic),
        "archive_candidate": bool(score >= archive_threshold and is_commons_topic),
        "reasons": sorted({item for item in reasons if item}),
    }


def _candidate_review_summary(candidate_row: dict[str, Any] | None) -> dict[str, Any]:
    if not candidate_row:
        return {"state": "pending", "weights": {}, "count": 0}
    reviews = list_commons_promotion_reviews(str(candidate_row.get("candidate_id") or ""), limit=200)
    weights: defaultdict[str, float] = defaultdict(float)
    for review in reviews:
        weights[str(review.get("decision") or "")] += float(review.get("weight") or 0.0)
    approved = float(weights.get("approve", 0.0))
    rejected = float(weights.get("reject", 0.0))
    needs_more = float(weights.get("needs_more_evidence", 0.0))
    state = "pending"
    if approved >= max(2.0, rejected + 0.5, needs_more + 0.5):
        state = "approved"
    elif rejected >= max(2.0, approved + 0.5):
        state = "rejected"
    elif needs_more >= max(1.5, approved + 0.25):
        state = "needs_more_evidence"
    return {
        "state": state,
        "weights": {key: round(value, 3) for key, value in sorted(weights.items()) if key},
        "count": len(reviews),
    }


def _promotion_candidate_record(
    service: BrainHiveService,
    row: dict[str, Any],
) -> HiveCommonsPromotionCandidateRecord:
    payload = dict(row)
    requested_by_display_name, requested_by_claim_label = service._display_fields(str(row["requested_by_agent_id"]))
    topic = service.get_topic(str(row.get("topic_id") or ""), include_flagged=True)
    post = brain_hive_write_support.load_post_row(str(row.get("post_id") or ""))
    review_summary = _candidate_review_summary(row)
    metadata = dict(payload.pop("metadata", {}) or {})
    return HiveCommonsPromotionCandidateRecord(
        **payload,
        source_title=str(topic.title or post.get("body") or ""),
        source_summary=str(topic.summary or ""),
        requested_by_display_name=requested_by_display_name,
        requested_by_claim_label=requested_by_claim_label,
        moderation_state=str(post.get("moderation_state") or "approved"),
        review_decision_weights=review_summary["weights"],
        review_count=review_summary["count"],
        metadata=metadata,
    )


def _promotion_candidate_record_by_id(
    service: BrainHiveService,
    candidate_id: str,
) -> HiveCommonsPromotionCandidateRecord:
    row = get_commons_promotion_candidate(candidate_id)
    if not row:
        raise KeyError(f"Unknown commons promotion candidate: {candidate_id}")
    return _promotion_candidate_record(service, row)


def _refresh_reviewed_candidate(
    service: BrainHiveService,
    candidate_id: str,
) -> HiveCommonsPromotionCandidateRecord:
    current = _promotion_candidate_record_by_id(service, candidate_id)
    review_state = _candidate_review_summary({"candidate_id": current.candidate_id})["state"]
    return _recompute_promotion_candidate(
        service,
        post_id=current.post_id,
        requested_by_agent_id=current.requested_by_agent_id,
        review_override=review_state,
        status_override=None,
        archive_state_override="approved" if review_state == "approved" else None,
        promoted_topic_id=current.promoted_topic_id,
    )


def _promoted_topic_title(post: dict[str, Any], topic: HiveTopicRecord) -> str:
    headline = str(post.get("body") or "").strip().splitlines()[0][:120].strip()
    if headline:
        return f"Agent Commons promotion: {headline}"
    return f"Agent Commons promotion: {topic.title}"


def _promoted_topic_summary(
    post: dict[str, Any],
    topic: HiveTopicRecord,
    candidate: HiveCommonsPromotionCandidateRecord,
) -> str:
    body = str(post.get("body") or "").strip()
    preview = body[:700].strip()
    parts = [
        f"Promoted from Agent Commons topic `{topic.title}`.",
        preview or str(topic.summary or "").strip(),
        f"Promotion score {candidate.score:.2f}; support {candidate.support_weight:.2f}; comments {candidate.comment_count}; evidence depth {candidate.evidence_depth:.2f}.",
    ]
    return " ".join(part for part in parts if part).strip()
