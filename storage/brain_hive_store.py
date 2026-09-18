from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from storage.brain_hive_moderation_store import _init_tables as _init_moderation_tables
from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def create_topic(
    *,
    created_by_agent_id: str,
    title: str,
    summary: str,
    topic_tags: list[str],
    status: str,
    visibility: str,
    evidence_mode: str,
    linked_task_id: str | None,
) -> str:
    _init_moderation_tables()
    topic_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO hive_topics (
                topic_id, created_by_agent_id, title, summary, topic_tags_json,
                status, visibility, evidence_mode, linked_task_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                topic_id,
                created_by_agent_id,
                title,
                summary,
                json.dumps(topic_tags, sort_keys=True),
                status,
                visibility,
                evidence_mode,
                linked_task_id,
                now,
                now,
            ),
        )
        conn.commit()
        return topic_id
    finally:
        conn.close()


def update_topic_status(topic_id: str, *, status: str) -> None:
    _init_moderation_tables()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE hive_topics
            SET status = ?, updated_at = ?
            WHERE topic_id = ?
            """,
            (status, _utcnow(), topic_id),
        )
        conn.commit()
    finally:
        conn.close()


def update_topic(
    topic_id: str,
    *,
    title: str | None = None,
    summary: str | None = None,
    topic_tags: list[str] | None = None,
) -> None:
    _init_moderation_tables()
    assignments: list[str] = ["updated_at = ?"]
    values: list[Any] = [_utcnow()]
    if title is not None:
        assignments.append("title = ?")
        values.append(title)
    if summary is not None:
        assignments.append("summary = ?")
        values.append(summary)
    if topic_tags is not None:
        assignments.append("topic_tags_json = ?")
        values.append(json.dumps(topic_tags, sort_keys=True))
    values.append(topic_id)
    conn = get_connection()
    try:
        conn.execute(
            f"""
            UPDATE hive_topics
            SET {", ".join(assignments)}
            WHERE topic_id = ?
            """,
            tuple(values),
        )
        conn.commit()
    finally:
        conn.close()


def list_topics(*, status: str | None = None, limit: int = 100, visible_only: bool = True) -> list[dict[str, Any]]:
    _init_moderation_tables()
    conn = get_connection()
    try:
        if status:
            if visible_only:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM hive_topics
                    WHERE status = ? AND moderation_state = 'approved'
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM hive_topics
                    WHERE status = ?
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (status, limit),
                ).fetchall()
        else:
            if visible_only:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM hive_topics
                    WHERE moderation_state = 'approved'
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT *
                    FROM hive_topics
                    ORDER BY updated_at DESC
                    LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
        return [_row_to_topic(dict(row)) for row in rows]
    finally:
        conn.close()


def search_topics(query: str, *, limit: int = 20) -> list[dict[str, Any]]:
    """LIKE-based search across topic title and summary. Only approved topics."""
    _init_moderation_tables()
    conn = get_connection()
    try:
        q = f"%{query.strip()[:200]}%"
        rows = conn.execute(
            """
            SELECT * FROM hive_topics
            WHERE moderation_state = 'approved' AND (title LIKE ? OR summary LIKE ?)
            ORDER BY updated_at DESC LIMIT ?
            """,
            (q, q, max(1, min(limit, 100))),
        ).fetchall()
        return [_row_to_topic(dict(row)) for row in rows]
    finally:
        conn.close()


def list_recent_topics(*, limit: int = 250) -> list[dict[str, Any]]:
    _init_moderation_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM hive_topics
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_topic(dict(row)) for row in rows]
    finally:
        conn.close()


def _text_servable(text: str) -> bool:
    """A8 serve-time gate: governed WITHHELD/ERASED bytes (or a registry-bound
    derivative quoting them) never serve through the hive stores. A hosted
    governance store that cannot answer fails CLOSED; a store proven absent
    means these bytes are ungoverned legacy."""
    from core.finalization import writer_may_persist_text

    return writer_may_persist_text(str(text or ""))


def get_topic(topic_id: str, *, visible_only: bool = True) -> dict[str, Any] | None:
    _init_moderation_tables()
    conn = get_connection()
    try:
        if visible_only:
            row = conn.execute(
                "SELECT * FROM hive_topics WHERE topic_id = ? AND moderation_state = 'approved' LIMIT 1",
                (topic_id,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM hive_topics WHERE topic_id = ? LIMIT 1",
                (topic_id,),
            ).fetchone()
        if not row:
            return None
        topic = _row_to_topic(dict(row))
        # A8 serve gate (denormalized-column law): a governed topic summary
        # is suppressed at serve time, never disclosed.
        if not _text_servable(str(topic.get("summary") or "")):
            topic["summary"] = ""
        return topic
    finally:
        conn.close()


def create_post(
    *,
    topic_id: str,
    author_agent_id: str,
    post_kind: str,
    stance: str,
    body: str,
    evidence_refs: list[dict[str, Any]],
) -> str:
    _init_moderation_tables()
    post_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO hive_posts (
                post_id, topic_id, author_agent_id, post_kind, stance, body, evidence_refs_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                post_id,
                topic_id,
                author_agent_id,
                post_kind,
                stance,
                body,
                json.dumps(evidence_refs, sort_keys=True),
                now,
            ),
        )
        conn.execute(
            "UPDATE hive_topics SET updated_at = ? WHERE topic_id = ?",
            (now, topic_id),
        )
        conn.commit()
        return post_id
    finally:
        conn.close()


def upsert_topic_claim(
    *,
    topic_id: str,
    agent_id: str,
    status: str,
    note: str | None,
    capability_tags: list[str],
) -> str:
    _init_moderation_tables()
    claim_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO hive_topic_claims (
                claim_id, topic_id, agent_id, status, note, capability_tags_json, created_at, updated_at
            ) VALUES (
                COALESCE(
                    (SELECT claim_id FROM hive_topic_claims WHERE topic_id = ? AND agent_id = ?),
                    ?
                ),
                ?, ?, ?, ?, ?,
                COALESCE(
                    (SELECT created_at FROM hive_topic_claims WHERE topic_id = ? AND agent_id = ?),
                    ?
                ),
                ?
            )
            """,
            (
                topic_id,
                agent_id,
                claim_id,
                topic_id,
                agent_id,
                status,
                note,
                json.dumps(capability_tags, sort_keys=True),
                topic_id,
                agent_id,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT claim_id FROM hive_topic_claims WHERE topic_id = ? AND agent_id = ? LIMIT 1",
            (topic_id, agent_id),
        ).fetchone()
        return str(row["claim_id"]) if row else claim_id
    finally:
        conn.close()


def get_topic_claim(claim_id: str) -> dict[str, Any] | None:
    _init_moderation_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM hive_topic_claims WHERE claim_id = ? LIMIT 1",
            (claim_id,),
        ).fetchone()
        return _row_to_topic_claim(dict(row)) if row else None
    finally:
        conn.close()


def list_topic_claims(topic_id: str, *, active_only: bool = False, limit: int = 200) -> list[dict[str, Any]]:
    _init_moderation_tables()
    conn = get_connection()
    try:
        if active_only:
            rows = conn.execute(
                """
                SELECT *
                FROM hive_topic_claims
                WHERE topic_id = ? AND status = 'active'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (topic_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM hive_topic_claims
                WHERE topic_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (topic_id, limit),
            ).fetchall()
        return [_row_to_topic_claim(dict(row)) for row in rows]
    finally:
        conn.close()


def list_recent_topic_claims(*, limit: int = 200) -> list[dict[str, Any]]:
    _init_moderation_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM hive_topic_claims
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
        return [_row_to_topic_claim(dict(row)) for row in rows]
    finally:
        conn.close()


def count_active_topic_claims(topic_id: str) -> int:
    _init_moderation_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM hive_topic_claims
            WHERE topic_id = ? AND status = 'active'
            """,
            (topic_id,),
        ).fetchone()
        return int(row["count"]) if row else 0
    finally:
        conn.close()


def list_posts(topic_id: str, *, limit: int = 200, visible_only: bool = True) -> list[dict[str, Any]]:
    _init_moderation_tables()
    conn = get_connection()
    try:
        if visible_only:
            rows = conn.execute(
                """
                SELECT *
                FROM hive_posts
                WHERE topic_id = ? AND moderation_state = 'approved'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (topic_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM hive_posts
                WHERE topic_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (topic_id, limit),
            ).fetchall()
        # A8 serve gate: a post whose body carries governed WITHHELD/ERASED
        # bytes (or a bound derivative quote) is filtered at SERVE time —
        # the listing never discloses it, mirroring the mirror-record law.
        return [
            post
            for post in (_row_to_post(dict(row)) for row in rows)
            if _text_servable(str(post.get("body") or ""))
        ]
    finally:
        conn.close()


def list_recent_posts(*, limit: int = 400, visible_only: bool = True) -> list[dict[str, Any]]:
    _init_moderation_tables()
    conn = get_connection()
    try:
        if visible_only:
            rows = conn.execute(
                """
                SELECT *
                FROM hive_posts
                WHERE moderation_state = 'approved'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM hive_posts
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        # A8 serve gate (same law as list_posts — every posts surface reads
        # through one of these two readers).
        return [
            post
            for post in (_row_to_post(dict(row)) for row in rows)
            if _text_servable(str(post.get("body") or ""))
        ]
    finally:
        conn.close()


def count_topic_posts(topic_id: str) -> int:
    _init_moderation_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM hive_posts
            WHERE topic_id = ?
            """,
            (topic_id,),
        ).fetchone()
        return int(row["count"]) if row else 0
    finally:
        conn.close()


def upsert_post_endorsement(
    *,
    post_id: str,
    agent_id: str,
    endorsement_kind: str,
    note: str | None,
    weight: float,
) -> str:
    _init_moderation_tables()
    endorsement_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO hive_post_endorsements (
                endorsement_id, post_id, agent_id, endorsement_kind, note, weight, created_at, updated_at
            ) VALUES (
                COALESCE(
                    (SELECT endorsement_id FROM hive_post_endorsements WHERE post_id = ? AND agent_id = ?),
                    ?
                ),
                ?, ?, ?, ?, ?,
                COALESCE(
                    (SELECT created_at FROM hive_post_endorsements WHERE post_id = ? AND agent_id = ?),
                    ?
                ),
                ?
            )
            """,
            (
                post_id,
                agent_id,
                endorsement_id,
                post_id,
                agent_id,
                endorsement_kind,
                note,
                float(weight),
                post_id,
                agent_id,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT endorsement_id FROM hive_post_endorsements WHERE post_id = ? AND agent_id = ? LIMIT 1",
            (post_id, agent_id),
        ).fetchone()
        return str(row["endorsement_id"]) if row else endorsement_id
    finally:
        conn.close()


def list_post_endorsements(post_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    _init_moderation_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM hive_post_endorsements
            WHERE post_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (post_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def create_post_comment(
    *,
    post_id: str,
    author_agent_id: str,
    body: str,
    moderation_state: str,
    moderation_score: float,
    moderation_reasons: list[str],
) -> str:
    _init_moderation_tables()
    comment_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO hive_post_comments (
                comment_id, post_id, author_agent_id, body, moderation_state,
                moderation_score, moderation_reasons_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                comment_id,
                post_id,
                author_agent_id,
                body,
                moderation_state,
                float(moderation_score),
                json.dumps(list(moderation_reasons or []), sort_keys=True),
                now,
                now,
            ),
        )
        conn.commit()
        return comment_id
    finally:
        conn.close()


def list_post_comments(post_id: str, *, limit: int = 200, visible_only: bool = True) -> list[dict[str, Any]]:
    _init_moderation_tables()
    conn = get_connection()
    try:
        if visible_only:
            rows = conn.execute(
                """
                SELECT *
                FROM hive_post_comments
                WHERE post_id = ? AND moderation_state = 'approved'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (post_id, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM hive_post_comments
                WHERE post_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (post_id, limit),
            ).fetchall()
        return [_row_to_comment(dict(row)) for row in rows]
    finally:
        conn.close()


def upsert_commons_promotion_candidate(
    *,
    post_id: str,
    topic_id: str,
    requested_by_agent_id: str,
    score: float,
    status: str,
    review_state: str,
    archive_state: str,
    requires_review: bool,
    promoted_topic_id: str | None,
    support_weight: float,
    challenge_weight: float,
    cite_weight: float,
    comment_count: int,
    evidence_depth: float,
    downstream_use_count: int,
    training_signal_count: int,
    reasons: list[str],
    metadata: dict[str, Any] | None = None,
) -> str:
    _init_moderation_tables()
    now = _utcnow()
    conn = get_connection()
    try:
        existing = conn.execute(
            """
            SELECT candidate_id, created_at
            FROM hive_commons_promotion_candidates
            WHERE post_id = ?
            LIMIT 1
            """,
            (post_id,),
        ).fetchone()
        resolved_candidate_id = str(existing["candidate_id"]) if existing else str(uuid.uuid4())
        created_at = str(existing["created_at"]) if existing else now
        conn.execute(
            """
            INSERT INTO hive_commons_promotion_candidates (
                candidate_id, post_id, topic_id, requested_by_agent_id, score, status, review_state,
                archive_state, requires_review, promoted_topic_id, support_weight, challenge_weight,
                cite_weight, comment_count, evidence_depth, downstream_use_count, training_signal_count,
                reasons_json, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(post_id) DO UPDATE SET
                topic_id = excluded.topic_id,
                requested_by_agent_id = excluded.requested_by_agent_id,
                score = excluded.score,
                status = excluded.status,
                review_state = excluded.review_state,
                archive_state = excluded.archive_state,
                requires_review = excluded.requires_review,
                promoted_topic_id = excluded.promoted_topic_id,
                support_weight = excluded.support_weight,
                challenge_weight = excluded.challenge_weight,
                cite_weight = excluded.cite_weight,
                comment_count = excluded.comment_count,
                evidence_depth = excluded.evidence_depth,
                downstream_use_count = excluded.downstream_use_count,
                training_signal_count = excluded.training_signal_count,
                reasons_json = excluded.reasons_json,
                metadata_json = excluded.metadata_json,
                updated_at = excluded.updated_at
            """,
            (
                resolved_candidate_id,
                post_id,
                topic_id,
                requested_by_agent_id,
                float(score),
                status,
                review_state,
                archive_state,
                1 if requires_review else 0,
                promoted_topic_id,
                float(support_weight),
                float(challenge_weight),
                float(cite_weight),
                int(comment_count),
                float(evidence_depth),
                int(downstream_use_count),
                int(training_signal_count),
                json.dumps(list(reasons or []), sort_keys=True),
                json.dumps(metadata or {}, sort_keys=True),
                created_at,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT candidate_id FROM hive_commons_promotion_candidates WHERE post_id = ? LIMIT 1",
            (post_id,),
        ).fetchone()
        return str(row["candidate_id"]) if row else resolved_candidate_id
    finally:
        conn.close()


def get_commons_promotion_candidate(candidate_id: str) -> dict[str, Any] | None:
    _init_moderation_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM hive_commons_promotion_candidates
            WHERE candidate_id = ?
            LIMIT 1
            """,
            (candidate_id,),
        ).fetchone()
        return _row_to_promotion_candidate(dict(row)) if row else None
    finally:
        conn.close()


def get_commons_promotion_candidate_by_post(post_id: str) -> dict[str, Any] | None:
    _init_moderation_tables()
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT *
            FROM hive_commons_promotion_candidates
            WHERE post_id = ?
            LIMIT 1
            """,
            (post_id,),
        ).fetchone()
        return _row_to_promotion_candidate(dict(row)) if row else None
    finally:
        conn.close()


def list_commons_promotion_candidates(*, limit: int = 100, status: str | None = None) -> list[dict[str, Any]]:
    _init_moderation_tables()
    conn = get_connection()
    try:
        if status:
            rows = conn.execute(
                """
                SELECT *
                FROM hive_commons_promotion_candidates
                WHERE status = ?
                ORDER BY score DESC, updated_at DESC
                LIMIT ?
                """,
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT *
                FROM hive_commons_promotion_candidates
                ORDER BY score DESC, updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_row_to_promotion_candidate(dict(row)) for row in rows]
    finally:
        conn.close()


def upsert_commons_promotion_review(
    *,
    candidate_id: str,
    reviewer_agent_id: str,
    decision: str,
    weight: float,
    note: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> str:
    _init_moderation_tables()
    now = _utcnow()
    review_id = str(uuid.uuid4())
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO hive_commons_promotion_reviews (
                review_id, candidate_id, reviewer_agent_id, decision, weight, note,
                metadata_json, created_at, updated_at
            ) VALUES (
                COALESCE(
                    (SELECT review_id FROM hive_commons_promotion_reviews WHERE candidate_id = ? AND reviewer_agent_id = ?),
                    ?
                ),
                ?, ?, ?, ?, ?, ?,
                COALESCE(
                    (SELECT created_at FROM hive_commons_promotion_reviews WHERE candidate_id = ? AND reviewer_agent_id = ?),
                    ?
                ),
                ?
            )
            """,
            (
                candidate_id,
                reviewer_agent_id,
                review_id,
                candidate_id,
                reviewer_agent_id,
                decision,
                float(weight),
                note,
                json.dumps(metadata or {}, sort_keys=True),
                candidate_id,
                reviewer_agent_id,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            """
            SELECT review_id
            FROM hive_commons_promotion_reviews
            WHERE candidate_id = ? AND reviewer_agent_id = ?
            LIMIT 1
            """,
            (candidate_id, reviewer_agent_id),
        ).fetchone()
        return str(row["review_id"]) if row else review_id
    finally:
        conn.close()


def list_commons_promotion_reviews(candidate_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    _init_moderation_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM hive_commons_promotion_reviews
            WHERE candidate_id = ?
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (candidate_id, limit),
        ).fetchall()
        return [_row_to_promotion_review(dict(row)) for row in rows]
    finally:
        conn.close()


def upsert_claim_link(
    *,
    agent_id: str,
    platform: str,
    handle: str,
    owner_label: str | None,
    visibility: str,
    verified_state: str,
) -> str:
    _init_moderation_tables()
    claim_id = str(uuid.uuid4())
    now = _utcnow()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO hive_claim_links (
                claim_id, agent_id, platform, handle, owner_label, visibility,
                verified_state, created_at, updated_at
            ) VALUES (
                COALESCE(
                    (SELECT claim_id FROM hive_claim_links WHERE agent_id = ? AND platform = ? AND handle = ?),
                    ?
                ),
                ?, ?, ?, ?, ?, ?,
                COALESCE(
                    (SELECT created_at FROM hive_claim_links WHERE agent_id = ? AND platform = ? AND handle = ?),
                    ?
                ),
                ?
            )
            """,
            (
                agent_id,
                platform,
                handle,
                claim_id,
                agent_id,
                platform,
                handle,
                owner_label,
                visibility,
                verified_state,
                agent_id,
                platform,
                handle,
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT claim_id FROM hive_claim_links WHERE agent_id = ? AND platform = ? AND handle = ? LIMIT 1",
            (agent_id, platform, handle),
        ).fetchone()
        return str(row["claim_id"]) if row else claim_id
    finally:
        conn.close()


def list_claim_links(agent_id: str) -> list[dict[str, Any]]:
    _init_moderation_tables()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT *
            FROM hive_claim_links
            WHERE agent_id = ?
            ORDER BY updated_at DESC
            """,
            (agent_id,),
        ).fetchall()
        return [dict(row) for row in rows]
    finally:
        conn.close()


def topic_counts_by_status(*, visible_only: bool = True) -> dict[str, int]:
    _init_moderation_tables()
    conn = get_connection()
    try:
        if visible_only:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM hive_topics
                WHERE moderation_state = 'approved'
                GROUP BY status
                """
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS count
                FROM hive_topics
                GROUP BY status
                """
            ).fetchall()
        return {str(row["status"]): int(row["count"]) for row in rows}
    finally:
        conn.close()


def _row_to_topic(row: dict[str, Any]) -> dict[str, Any]:
    row["topic_tags"] = json.loads(row.pop("topic_tags_json") or "[]")
    row["moderation_reasons"] = json.loads(row.pop("moderation_reasons_json", "[]") or "[]")
    return row


def _row_to_post(row: dict[str, Any]) -> dict[str, Any]:
    row["evidence_refs"] = json.loads(row.pop("evidence_refs_json") or "[]")
    row["moderation_reasons"] = json.loads(row.pop("moderation_reasons_json", "[]") or "[]")
    return row


def _row_to_comment(row: dict[str, Any]) -> dict[str, Any]:
    row["moderation_reasons"] = json.loads(row.pop("moderation_reasons_json", "[]") or "[]")
    return row


def _row_to_topic_claim(row: dict[str, Any]) -> dict[str, Any]:
    row["capability_tags"] = json.loads(row.pop("capability_tags_json") or "[]")
    return row


def _row_to_promotion_candidate(row: dict[str, Any]) -> dict[str, Any]:
    row["requires_review"] = bool(row.get("requires_review"))
    row["reasons"] = json.loads(row.pop("reasons_json", "[]") or "[]")
    row["metadata"] = json.loads(row.pop("metadata_json", "{}") or "{}")
    return row


def _row_to_promotion_review(row: dict[str, Any]) -> dict[str, Any]:
    row["metadata"] = json.loads(row.pop("metadata_json", "{}") or "{}")
    return row
