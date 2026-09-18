from __future__ import annotations

from typing import Any
from urllib.parse import quote

from core.public_hive.truth import (
    annotate_public_hive_packet_truth,
    annotate_public_hive_truth,
    research_packet_truth_complete,
    research_queue_truth_complete,
    route_missing,
)


def list_public_topics(
    bridge: Any,
    *,
    limit: int = 24,
    statuses: tuple[str, ...] = ("open", "researching", "disputed", "partial", "needs_improvement"),
) -> list[dict[str, Any]]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return []
    try:
        result = bridge._get_json(
            str(bridge.config.topic_target_url),
            f"/v1/hive/topics?limit={max(1, min(int(limit), 100))}",
        )
    except Exception:
        return []
    wanted_statuses = {str(item or "").strip().lower() for item in statuses if str(item or "").strip()}
    rows = list(result if isinstance(result, list) else [])
    out: list[dict[str, Any]] = []
    for row in rows:
        status = str((row or {}).get("status") or "").strip().lower()
        if wanted_statuses and status not in wanted_statuses:
            continue
        out.append(annotate_public_hive_truth(dict(row or {})))
    return out


def get_public_topic(
    bridge: Any,
    topic_id: str,
    *,
    include_flagged: bool = True,
) -> dict[str, Any] | None:
    clean_topic_id = str(topic_id or "").strip()
    if not clean_topic_id or not bridge.enabled() or not bridge.config.topic_target_url:
        return None
    route = f"/v1/hive/topics/{clean_topic_id}"
    if include_flagged:
        route = f"{route}?include_flagged=1"
    try:
        result = bridge._get_json(str(bridge.config.topic_target_url), route)
    except Exception:
        return None
    return annotate_public_hive_truth(dict(result or {}))


def list_public_research_queue(bridge: Any, *, limit: int = 24) -> list[dict[str, Any]]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return []
    bounded_limit = max(1, min(int(limit), 100))
    try:
        result = bridge._get_json(
            str(bridge.config.topic_target_url),
            f"/v1/hive/research-queue?limit={bounded_limit}",
        )
    except Exception as exc:
        if route_missing(exc):
            return build_research_queue_fallback(bridge, limit=bounded_limit)
        return []
    rows = [annotate_public_hive_truth(dict(item or {})) for item in list(result or [])]
    if rows and any(not research_queue_truth_complete(row) for row in rows):
        return overlay_research_queue_truth(bridge, rows, limit=bounded_limit)
    return rows


def list_public_review_queue(
    bridge: Any,
    *,
    object_type: str | None = None,
    limit: int = 24,
) -> list[dict[str, Any]]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return []
    route = f"/v1/hive/review-queue?limit={max(1, min(int(limit), 100))}"
    if str(object_type or "").strip():
        route += f"&object_type={quote(str(object_type or '').strip())}"
    try:
        result = bridge._get_json(str(bridge.config.topic_target_url), route)
    except Exception:
        return []
    return [dict(item or {}) for item in list(result or [])]


def get_public_research_packet(bridge: Any, topic_id: str) -> dict[str, Any]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return {}
    clean_topic_id = str(topic_id or "").strip()
    if not clean_topic_id:
        return {}
    try:
        result = bridge._get_json(
            str(bridge.config.topic_target_url),
            f"/v1/hive/topics/{clean_topic_id}/research-packet",
        )
    except Exception as exc:
        if route_missing(exc):
            return build_research_packet_fallback(bridge, clean_topic_id)
        return {}
    packet = annotate_public_hive_packet_truth(dict(result or {}))
    if not research_packet_truth_complete(packet):
        return overlay_research_packet_truth(bridge, clean_topic_id, packet)
    return packet


def build_research_queue_fallback(bridge: Any, *, limit: int) -> list[dict[str, Any]]:
    from core.brain_hive_research import build_research_queue_entry

    topics = list_public_topics(bridge, limit=max(32, int(limit) * 2))
    queue_rows: list[dict[str, Any]] = []
    for topic in topics:
        status = str(topic.get("status") or "").strip().lower()
        if status not in {"open", "researching", "disputed", "partial", "needs_improvement"}:
            continue
        topic_id = str(topic.get("topic_id") or "").strip()
        if not topic_id:
            continue
        posts = list_public_topic_posts(bridge, topic_id, limit=120)
        claims = list_public_topic_claims(bridge, topic_id, limit=48)
        row = build_research_queue_entry(topic=topic, posts=posts, claims=claims)
        row["claims"] = [dict(item or {}) for item in claims]
        row["updated_at"] = str(topic.get("updated_at") or "")
        row["created_at"] = str(topic.get("created_at") or "")
        row["compat_fallback"] = True
        queue_rows.append(annotate_public_hive_truth(row))
    queue_rows.sort(
        key=lambda row: (
            float(row.get("research_priority") or 0.0),
            -int(row.get("active_claim_count") or 0),
            str(row.get("updated_at") or ""),
        ),
        reverse=True,
    )
    return queue_rows[: max(1, min(int(limit), 100))]


def build_research_packet_fallback(bridge: Any, topic_id: str) -> dict[str, Any]:
    from core.brain_hive_research import build_topic_research_packet

    topic = get_public_topic_raw(bridge, topic_id)
    if not topic:
        return {}
    posts = list_public_topic_posts(bridge, topic_id, limit=400)
    claims = list_public_topic_claims(bridge, topic_id, limit=200)
    packet = build_topic_research_packet(topic=topic, posts=posts, claims=claims)
    packet["compat_fallback"] = True
    return annotate_public_hive_packet_truth(packet)


def overlay_research_queue_truth(
    bridge: Any,
    rows: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    fallback_rows = {
        str(item.get("topic_id") or "").strip(): dict(item)
        for item in build_research_queue_fallback(bridge, limit=limit)
        if str(item.get("topic_id") or "").strip()
    }
    merged_rows: list[dict[str, Any]] = []
    for row in rows:
        topic_id = str(row.get("topic_id") or "").strip()
        fallback = dict(fallback_rows.get(topic_id) or {})
        if fallback:
            merged = dict(fallback)
            merged.update({key: value for key, value in row.items() if value not in (None, "", [], {})})
            merged["truth_overlay"] = True
            merged_rows.append(annotate_public_hive_truth(merged))
        else:
            merged_rows.append(row)
    return merged_rows[: max(1, min(int(limit), 100))]


def overlay_research_packet_truth(
    bridge: Any,
    topic_id: str,
    direct_packet: dict[str, Any],
) -> dict[str, Any]:
    fallback = build_research_packet_fallback(bridge, topic_id)
    if not fallback:
        return direct_packet
    merged = dict(fallback)
    merged.update({key: value for key, value in direct_packet.items() if value not in (None, "", [], {})})
    if dict(direct_packet.get("topic") or {}):
        topic = dict(fallback.get("topic") or {})
        topic.update({key: value for key, value in dict(direct_packet.get("topic") or {}).items() if value not in (None, "", [], {})})
        merged["topic"] = topic
    merged["truth_overlay"] = True
    return annotate_public_hive_packet_truth(merged)


def get_public_topic_raw(bridge: Any, topic_id: str) -> dict[str, Any]:
    clean_topic_id = str(topic_id or "").strip()
    if not clean_topic_id or not bridge.config.topic_target_url:
        return {}
    try:
        result = bridge._get_json(
            str(bridge.config.topic_target_url),
            f"/v1/hive/topics/{clean_topic_id}",
        )
    except Exception:
        return {}
    return annotate_public_hive_truth(dict(result or {}))


def list_public_topic_posts(bridge: Any, topic_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    clean_topic_id = str(topic_id or "").strip()
    if not clean_topic_id or not bridge.config.topic_target_url:
        return []
    try:
        result = bridge._get_json(
            str(bridge.config.topic_target_url),
            f"/v1/hive/topics/{clean_topic_id}/posts?limit={max(1, min(int(limit), 400))}",
        )
    except Exception:
        return []
    return [dict(item or {}) for item in list(result or [])]


def list_public_topic_claims(bridge: Any, topic_id: str, *, limit: int = 200) -> list[dict[str, Any]]:
    clean_topic_id = str(topic_id or "").strip()
    if not clean_topic_id or not bridge.config.topic_target_url:
        return []
    try:
        result = bridge._get_json(
            str(bridge.config.topic_target_url),
            f"/v1/hive/topics/{clean_topic_id}/claims?limit={max(1, min(int(limit), 400))}",
        )
    except Exception:
        return []
    return [dict(item or {}) for item in list(result or [])]


def topic_result_settlement_helpers(
    bridge: Any,
    *,
    topic_id: str,
    claim_id: str,
) -> list[str]:
    claim_rows = list_public_topic_claims(bridge, topic_id, limit=200)
    clean_claim_id = str(claim_id or "").strip()
    if clean_claim_id:
        for row in claim_rows:
            if str(row.get("claim_id") or "").strip() != clean_claim_id:
                continue
            agent_id = str(row.get("agent_id") or "").strip()
            if agent_id:
                return [agent_id]
    helper_peer_ids: list[str] = []
    seen_helpers: set[str] = set()
    for row in claim_rows:
        claim_status = str(row.get("status") or "").strip().lower()
        if claim_status not in {"active", "completed"}:
            continue
        agent_id = str(row.get("agent_id") or "").strip()
        if not agent_id or agent_id in seen_helpers:
            continue
        seen_helpers.add(agent_id)
        helper_peer_ids.append(agent_id)
    return helper_peer_ids


def search_public_artifacts(
    bridge: Any,
    *,
    query_text: str,
    topic_id: str | None = None,
    limit: int = 24,
) -> list[dict[str, Any]]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return []
    clean_query = " ".join(str(query_text or "").split()).strip()
    if not clean_query:
        return []
    route = f"/v1/hive/artifacts/search?q={quote(clean_query)}&limit={max(1, min(int(limit), 100))}"
    if str(topic_id or "").strip():
        route += f"&topic_id={quote(str(topic_id or '').strip())}"
    try:
        result = bridge._get_json(str(bridge.config.topic_target_url), route)
    except Exception:
        return []
    return [dict(item or {}) for item in list(result or [])]


def get_public_review_summary(
    bridge: Any,
    *,
    object_type: str,
    object_id: str,
) -> dict[str, Any]:
    if not bridge.enabled() or not bridge.config.topic_target_url:
        return {}
    clean_type = str(object_type or "").strip()
    clean_id = str(object_id or "").strip()
    if not clean_type or not clean_id:
        return {}
    route = (
        "/v1/hive/moderation/reviews"
        f"?object_type={quote(clean_type)}"
        f"&object_id={quote(clean_id)}"
    )
    try:
        result = bridge._get_json(str(bridge.config.topic_target_url), route)
    except Exception:
        return {}
    return dict(result or {})
