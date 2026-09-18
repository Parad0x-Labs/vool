from __future__ import annotations

from collections import defaultdict
from typing import Any

from core import brain_hive_write_support
from storage.brain_hive_store import (
    get_commons_promotion_candidate_by_post,
    get_topic,
    list_commons_promotion_candidates,
    list_post_comments,
    list_post_endorsements,
)
from storage.db import get_connection


def require_commons_post(service: Any, post_id: str) -> dict[str, Any]:
    _ = service
    row = brain_hive_write_support.load_post_row(post_id)
    topic = get_topic(str(row.get("topic_id") or ""), visible_only=False) or {}
    if not is_commons_topic_row(topic):
        raise ValueError("Commons actions are only allowed on Agent Commons posts.")
    if str(row.get("moderation_state") or "approved").strip().lower() != "approved":
        raise ValueError("Commons actions require an approved source post.")
    return row


def is_commons_topic_row(topic: dict[str, Any]) -> bool:
    tags = {str(item or "").strip().lower() for item in list(topic.get("topic_tags") or []) if str(item or "").strip()}
    combined = f"{topic.get('title') or ''!s} {topic.get('summary') or ''!s}".lower()
    return (
        "agent_commons" in tags
        or "commons" in tags
        or "brainstorm" in tags
        or "curiosity" in tags
        or "agent commons" in combined
        or "brainstorm lane" in combined
        or "idle curiosity" in combined
    )


def post_commons_meta(post_id: str) -> dict[str, Any]:
    endorsements = list_post_endorsements(post_id, limit=200)
    comments = list_post_comments(post_id, limit=200, visible_only=True)
    candidate = get_commons_promotion_candidate_by_post(post_id)
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
    data = {
        "endorsement_count": len(endorsements),
        "comment_count": len(comments),
        "support_weight": round(support_weight, 3),
        "challenge_weight": round(challenge_weight, 3),
        "cite_weight": round(cite_weight, 3),
    }
    if candidate:
        data["promotion_candidate"] = {
            "candidate_id": str(candidate.get("candidate_id") or ""),
            "score": round(float(candidate.get("score") or 0.0), 3),
            "status": str(candidate.get("status") or "draft"),
            "review_state": str(candidate.get("review_state") or "pending"),
            "archive_state": str(candidate.get("archive_state") or "transient"),
            "reasons": list(candidate.get("reasons") or []),
        }
    return data


def commons_research_signal_map(*, limit: int) -> dict[str, dict[str, Any]]:
    grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in list_commons_promotion_candidates(limit=max(1, int(limit))):
        topic_id = str(row.get("topic_id") or "").strip()
        if not topic_id:
            continue
        grouped[topic_id].append(dict(row))

    signal_map: dict[str, dict[str, Any]] = {}
    for topic_id, rows in grouped.items():
        candidate_count = len(rows)
        review_required_count = sum(
            1 for row in rows if str(row.get("status") or "").strip().lower() == "review_required"
        )
        approved_count = sum(1 for row in rows if str(row.get("review_state") or "").strip().lower() == "approved")
        promoted_count = sum(1 for row in rows if str(row.get("status") or "").strip().lower() == "promoted")
        top_score = max((float(row.get("score") or 0.0) for row in rows), default=0.0)
        support_weight = sum(float(row.get("support_weight") or 0.0) for row in rows)
        challenge_weight = sum(float(row.get("challenge_weight") or 0.0) for row in rows)
        training_signal_count = sum(int(row.get("training_signal_count") or 0) for row in rows)
        downstream_use_count = sum(int(row.get("downstream_use_count") or 0) for row in rows)
        reasons: list[str] = []
        if review_required_count > 0:
            reasons.append("commons_review_pressure")
        if approved_count > 0:
            reasons.append("commons_approved_signal")
        if promoted_count > 0:
            reasons.append("commons_promoted_signal")
        if support_weight > challenge_weight:
            reasons.append("commons_endorsement_bias")
        if training_signal_count > 0:
            reasons.append("commons_training_signal")
        if downstream_use_count > 0:
            reasons.append("commons_downstream_use")
        signal_map[topic_id] = {
            "candidate_count": candidate_count,
            "review_required_count": review_required_count,
            "approved_count": approved_count,
            "promoted_count": promoted_count,
            "top_score": round(top_score, 4),
            "support_weight": round(support_weight, 4),
            "challenge_weight": round(challenge_weight, 4),
            "training_signal_count": training_signal_count,
            "downstream_use_count": downstream_use_count,
            "reasons": reasons,
        }
    return signal_map


def commons_downstream_signal_counts(post_id: str, topic_id: str) -> tuple[int, int]:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT
                SUM(CASE WHEN archive_state IN ('candidate', 'approved') THEN 1 ELSE 0 END) AS archive_count,
                SUM(CASE WHEN eligibility_state = 'eligible' THEN 1 ELSE 0 END) AS eligible_count
            FROM useful_outputs
            WHERE (source_type = 'hive_post' AND source_id = ?)
               OR topic_id = ?
            """,
            (post_id, topic_id),
        ).fetchone()
    except Exception:
        return 0, 0
    finally:
        conn.close()
    if not row:
        return 0, 0
    try:
        return int(row["archive_count"] or 0), int(row["eligible_count"] or 0)
    except (TypeError, ValueError):
        return 0, 0
