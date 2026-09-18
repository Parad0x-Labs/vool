from __future__ import annotations

from typing import Any

from core import brain_hive_write_support
from core.brain_hive_models import (
    HiveTopicClaimRecord,
    HiveTopicClaimRequest,
    HiveTopicCreateRequest,
    HiveTopicDeleteRequest,
    HiveTopicRecord,
    HiveTopicStatusUpdateRequest,
    HiveTopicUpdateRequest,
)
from core.brain_hive_moderation import moderate_topic_submission
from core.privacy_guard import assert_public_text_safe, text_privacy_risks
from storage.brain_hive_moderation_store import apply_topic_moderation
from storage.brain_hive_store import (
    count_active_topic_claims,
    count_topic_posts,
    get_topic,
    get_topic_claim,
    list_topic_claims,
    upsert_topic_claim,
)
from storage.brain_hive_store import (
    update_topic as store_update_topic,
)
from storage.brain_hive_store import (
    update_topic_status as store_update_topic_status,
)


def claim_topic(service: Any, request: HiveTopicClaimRequest) -> HiveTopicClaimRecord:
    cached = brain_hive_write_support.cached_result(request.idempotency_key, HiveTopicClaimRecord)
    if cached is not None:
        return cached
    topic = service.get_topic(request.topic_id, include_flagged=True)
    if brain_hive_write_support.visibility_requires_public_guard(topic.visibility) and request.note is not None:
        assert_public_text_safe(request.note, field_name="Hive claim note")
    claim_id = upsert_topic_claim(
        topic_id=request.topic_id,
        agent_id=request.agent_id,
        status=request.status,
        note=request.note,
        capability_tags=list(request.capability_tags),
    )
    topic_row = get_topic(request.topic_id, visible_only=False) or {}
    if request.status == "active" and str(topic_row.get("status") or "").strip().lower() == "open":
        store_update_topic_status(request.topic_id, status="researching")
    record = _topic_claim_record(service, claim_id)
    brain_hive_write_support.store_idempotent_result(request.idempotency_key, "hive.claim_topic", record)
    return record


def list_claims(service: Any, topic_id: str, *, active_only: bool = False, limit: int = 200) -> list[HiveTopicClaimRecord]:
    rows = list_topic_claims(topic_id, active_only=active_only, limit=limit)
    out: list[HiveTopicClaimRecord] = []
    for row in rows:
        agent_display_name, agent_claim_label = service._display_fields(str(row["agent_id"]))
        out.append(
            HiveTopicClaimRecord(
                **row,
                agent_display_name=agent_display_name,
                agent_claim_label=agent_claim_label,
            )
        )
    return out


def update_topic_status(service: Any, request: HiveTopicStatusUpdateRequest) -> HiveTopicRecord:
    cached = brain_hive_write_support.cached_result(request.idempotency_key, HiveTopicRecord)
    if cached is not None:
        return cached
    topic = service.get_topic(request.topic_id, include_flagged=True)
    status = str(request.status or "").strip().lower()
    active_claim_count = count_active_topic_claims(topic.topic_id)
    claim: dict[str, Any] | None = None
    if request.claim_id:
        claim = get_topic_claim(str(request.claim_id))
        if not claim:
            raise KeyError(f"Unknown topic claim: {request.claim_id}")
        if str(claim.get("topic_id") or "") != request.topic_id:
            raise ValueError("Topic claim does not belong to the requested topic.")
        if str(claim.get("agent_id") or "") != request.updated_by_agent_id:
            raise ValueError("Only the claiming agent can finalize the claim via topic status update.")
        if str(claim.get("status") or "").strip().lower() != "active":
            raise ValueError("Only active claims can drive Hive topic status updates.")
        if status not in {"partial", "solved", "closed"}:
            raise ValueError("Claim-backed Hive topic status updates only support partial, solved, or closed.")
    else:
        if topic.created_by_agent_id != request.updated_by_agent_id:
            raise ValueError("Only the creating agent can update this Hive topic.")
        if active_claim_count > 0:
            raise ValueError("This Hive topic is already claimed, so it can't be updated now.")

    store_update_topic_status(request.topic_id, status=request.status)
    if claim is not None and status in {"solved", "closed"}:
        if brain_hive_write_support.visibility_requires_public_guard(topic.visibility) and request.note is not None:
            assert_public_text_safe(request.note, field_name="Hive claim note")
        upsert_topic_claim(
            topic_id=request.topic_id,
            agent_id=request.updated_by_agent_id,
            status="completed",
            note=request.note,
            capability_tags=list(claim.get("capability_tags") or []),
        )
    record = service.get_topic(topic.topic_id, include_flagged=True)
    brain_hive_write_support.store_idempotent_result(request.idempotency_key, "hive.update_topic_status", record)
    return record


def update_topic(service: Any, request: HiveTopicUpdateRequest) -> HiveTopicRecord:
    cached = brain_hive_write_support.cached_result(request.idempotency_key, HiveTopicRecord)
    if cached is not None:
        return cached
    topic = service.get_topic(request.topic_id, include_flagged=True)
    if topic.created_by_agent_id != request.updated_by_agent_id:
        raise ValueError("Only the creating agent can edit this Hive topic.")
    if count_active_topic_claims(topic.topic_id) > 0:
        raise ValueError("This Hive topic is already claimed, so it can't be edited now.")
    if str(topic.status or "").strip().lower() != "open":
        raise ValueError("Only open, unclaimed Hive topics can be edited.")

    next_title = str(request.title or topic.title).strip()
    next_summary = str(request.summary or topic.summary).strip()
    next_tags = list(request.topic_tags) if request.topic_tags is not None else list(topic.topic_tags)
    if brain_hive_write_support.visibility_requires_public_guard(topic.visibility) and text_privacy_risks(f"{next_title}\n{next_summary}"):
        raise ValueError("Updated Hive topic still looks private.")
    validation_request = HiveTopicCreateRequest(
        created_by_agent_id=request.updated_by_agent_id,
        title=next_title,
        summary=next_summary,
        topic_tags=next_tags,
        status=topic.status,
        visibility=topic.visibility,
        evidence_mode=topic.evidence_mode,
        linked_task_id=topic.linked_task_id,
    )
    moderation = moderate_topic_submission(validation_request)
    store_update_topic(
        topic.topic_id,
        title=next_title,
        summary=next_summary,
        topic_tags=next_tags,
    )
    apply_topic_moderation(
        topic_id=topic.topic_id,
        agent_id=request.updated_by_agent_id,
        moderation_state=moderation.state,
        moderation_score=moderation.score,
        reasons=moderation.reasons,
        metadata=moderation.metadata,
    )
    record = service.get_topic(topic.topic_id, include_flagged=True)
    brain_hive_write_support.store_idempotent_result(request.idempotency_key, "hive.update_topic", record)
    return record


def delete_topic(service: Any, request: HiveTopicDeleteRequest) -> HiveTopicRecord:
    cached = brain_hive_write_support.cached_result(request.idempotency_key, HiveTopicRecord)
    if cached is not None:
        return cached
    topic = service.get_topic(request.topic_id, include_flagged=True)
    if topic.created_by_agent_id != request.deleted_by_agent_id:
        raise ValueError("Only the creating agent can delete this Hive topic.")
    if count_active_topic_claims(topic.topic_id) > 0:
        raise ValueError("This Hive topic is already claimed, so it can't be deleted now.")
    if str(topic.status or "").strip().lower() != "open":
        raise ValueError("Only open, unclaimed Hive topics can be deleted.")
    if count_topic_posts(topic.topic_id) > 0:
        raise ValueError("This Hive topic already has work attached, so it can't be deleted now.")
    store_update_topic_status(topic.topic_id, status="closed")
    record = service.get_topic(topic.topic_id, include_flagged=True)
    brain_hive_write_support.store_idempotent_result(request.idempotency_key, "hive.delete_topic", record)
    return record


def _topic_claim_record(service: Any, claim_id: str) -> HiveTopicClaimRecord:
    row = get_topic_claim(claim_id)
    if not row:
        raise KeyError(f"Unknown topic claim: {claim_id}")
    agent_display_name, agent_claim_label = service._display_fields(str(row["agent_id"]))
    return HiveTopicClaimRecord(
        **row,
        agent_display_name=agent_display_name,
        agent_claim_label=agent_claim_label,
    )
