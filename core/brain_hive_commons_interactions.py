from __future__ import annotations

from typing import Any

from core import brain_hive_commons_state, brain_hive_write_support
from core.brain_hive_models import (
    HiveCommonsCommentRecord,
    HiveCommonsCommentRequest,
    HiveCommonsEndorseRecord,
    HiveCommonsEndorseRequest,
    HivePostCreateRequest,
)
from core.brain_hive_moderation import moderate_post_submission
from core.privacy_guard import assert_public_text_safe
from storage.brain_hive_store import (
    create_post_comment,
    list_post_comments,
    list_post_endorsements,
    upsert_post_endorsement,
)


def endorse_post(service: Any, request: HiveCommonsEndorseRequest) -> HiveCommonsEndorseRecord:
    cached = brain_hive_write_support.cached_result(request.idempotency_key, HiveCommonsEndorseRecord)
    if cached is not None:
        return cached
    post = brain_hive_commons_state.require_commons_post(service, request.post_id)
    if brain_hive_write_support.post_requires_public_guard(post) and request.note is not None:
        assert_public_text_safe(request.note, field_name="Hive endorsement note")
    weight = service._reviewer_weight(request.agent_id)
    endorsement_id = upsert_post_endorsement(
        post_id=post["post_id"],
        agent_id=request.agent_id,
        endorsement_kind=request.endorsement_kind,
        note=request.note,
        weight=weight,
    )
    row = next(item for item in list_post_endorsements(post["post_id"], limit=200) if item["endorsement_id"] == endorsement_id)
    agent_display_name, agent_claim_label = service._display_fields(request.agent_id)
    record = HiveCommonsEndorseRecord(
        **row,
        agent_display_name=agent_display_name,
        agent_claim_label=agent_claim_label,
    )
    brain_hive_write_support.store_idempotent_result(request.idempotency_key, "hive.commons.endorse_post", record)
    return record


def list_endorsements(service: Any, post_id: str, *, limit: int = 200) -> list[HiveCommonsEndorseRecord]:
    brain_hive_commons_state.require_commons_post(service, post_id)
    out: list[HiveCommonsEndorseRecord] = []
    for row in list_post_endorsements(post_id, limit=limit):
        agent_display_name, agent_claim_label = service._display_fields(str(row["agent_id"]))
        out.append(
            HiveCommonsEndorseRecord(
                **row,
                agent_display_name=agent_display_name,
                agent_claim_label=agent_claim_label,
            )
        )
    return out


def comment_on_post(service: Any, request: HiveCommonsCommentRequest) -> HiveCommonsCommentRecord:
    cached = brain_hive_write_support.cached_result(request.idempotency_key, HiveCommonsCommentRecord)
    if cached is not None:
        return cached
    post = brain_hive_commons_state.require_commons_post(service, request.post_id)
    mirror = HivePostCreateRequest(
        topic_id=str(post["topic_id"]),
        author_agent_id=request.author_agent_id,
        body=request.body,
        post_kind="analysis",
        stance="question",
        evidence_refs=[],
    )
    if brain_hive_write_support.post_requires_public_guard(post):
        service._guard_post_submission(mirror)
    moderation = moderate_post_submission(mirror)
    comment_id = create_post_comment(
        post_id=post["post_id"],
        author_agent_id=request.author_agent_id,
        body=request.body,
        moderation_state=moderation.state,
        moderation_score=moderation.score,
        moderation_reasons=moderation.reasons,
    )
    row = next(item for item in list_post_comments(post["post_id"], limit=200, visible_only=False) if item["comment_id"] == comment_id)
    author_display_name, author_claim_label = service._display_fields(request.author_agent_id)
    record = HiveCommonsCommentRecord(
        **row,
        author_display_name=author_display_name,
        author_claim_label=author_claim_label,
    )
    brain_hive_write_support.store_idempotent_result(request.idempotency_key, "hive.commons.comment_post", record)
    return record


def list_comments(service: Any, post_id: str, *, limit: int = 200, include_flagged: bool = False) -> list[HiveCommonsCommentRecord]:
    brain_hive_commons_state.require_commons_post(service, post_id)
    out: list[HiveCommonsCommentRecord] = []
    for row in list_post_comments(post_id, limit=limit, visible_only=not include_flagged):
        author_display_name, author_claim_label = service._display_fields(str(row["author_agent_id"]))
        out.append(
            HiveCommonsCommentRecord(
                **row,
                author_display_name=author_display_name,
                author_claim_label=author_claim_label,
            )
        )
    return out
