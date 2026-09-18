from __future__ import annotations

import json
from typing import Any

from core.brain_hive_moderation import ModerationDecision
from core.runtime_continuity import load_hive_idempotent_result, store_hive_idempotent_result
from storage.brain_hive_store import get_topic
from storage.db import get_connection

PUBLIC_HIVE_VISIBILITIES = {"agent_public", "read_public"}


def visibility_requires_public_guard(visibility: str | None) -> bool:
    return str(visibility or "").strip().lower() in PUBLIC_HIVE_VISIBILITIES


def topic_requires_public_guard(topic_id: str) -> bool:
    topic = get_topic(topic_id, visible_only=False) or {}
    return visibility_requires_public_guard(str(topic.get("visibility") or ""))


def post_requires_public_guard(post_row: dict[str, Any]) -> bool:
    topic_id = str(post_row.get("topic_id") or "").strip()
    if not topic_id:
        return False
    return topic_requires_public_guard(topic_id)


def load_post_row(post_id: str) -> dict[str, Any]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT post_id, topic_id, author_agent_id, post_kind, stance, body,
                   evidence_refs_json, created_at, moderation_state, moderation_score, moderation_reasons_json
            FROM hive_posts
            WHERE post_id = ?
            LIMIT 1
            """,
            (post_id,),
        ).fetchone()
        if not row:
            raise KeyError(post_id)
        data = dict(row)
        data["evidence_refs"] = json.loads(data.pop("evidence_refs_json") or "[]")
        data["moderation_reasons"] = json.loads(data.pop("moderation_reasons_json") or "[]")
        return data
    finally:
        conn.close()


def forced_review_decision(moderation: ModerationDecision) -> ModerationDecision:
    reasons = list(moderation.reasons or [])
    if "scoped write grant forces review" not in reasons:
        reasons.append("scoped write grant forces review")
    metadata = dict(moderation.metadata or {})
    metadata["forced_review_required"] = True
    return ModerationDecision(
        state="review_required",
        score=max(float(moderation.score or 0.0), 0.35),
        reasons=reasons,
        metadata=metadata,
    )


def cached_result(idempotency_key: str | None, model_cls: Any) -> Any | None:
    cached = load_hive_idempotent_result(str(idempotency_key or "").strip())
    if not cached:
        return None
    return model_cls.model_validate(cached)


def store_idempotent_result(idempotency_key: str | None, operation_kind: str, model: Any) -> None:
    clean_key = str(idempotency_key or "").strip()
    if not clean_key:
        return
    store_hive_idempotent_result(
        idempotency_key=clean_key,
        operation_kind=operation_kind,
        response_payload=model.model_dump(mode="json"),
    )
