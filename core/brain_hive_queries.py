from __future__ import annotations

from collections import Counter
from typing import TYPE_CHECKING, Any

from core import brain_hive_commons_state
from core.brain_hive_models import BrainHiveStatsResponse, HiveAgentProfile, HiveRegionStat, HiveTaskStat
from core.scoreboard_engine import get_peer_scoreboard
from storage.brain_hive_store import (
    list_recent_posts,
    list_recent_topic_claims,
    topic_counts_by_status,
)
from storage.db import get_connection
from storage.knowledge_index import active_presence

if TYPE_CHECKING:
    from core.brain_hive_service import BrainHiveService


def list_recent_topic_claims_feed(
    service: BrainHiveService,
    *,
    limit: int = 50,
    topic_lookup: Any,
) -> list[dict[str, Any]]:
    rows = list_recent_topic_claims(limit=limit)
    out: list[dict[str, Any]] = []
    for row in rows:
        agent_display_name, agent_claim_label = service._display_fields(str(row["agent_id"]))
        topic = topic_lookup(str(row["topic_id"]), visible_only=True) or topic_lookup(
            str(row["topic_id"]),
            visible_only=False,
        ) or {}
        out.append(
            {
                **row,
                "agent_display_name": agent_display_name,
                "agent_claim_label": agent_claim_label,
                "topic_title": str(topic.get("title") or "Unknown topic"),
                "topic_status": str(topic.get("status") or "open"),
            }
        )
    return out


def list_review_queue(
    service: BrainHiveService,
    *,
    object_type: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    conn = get_connection()
    try:
        rows: list[dict[str, Any]] = []
        if object_type in {None, "topic"}:
            topic_rows = conn.execute(
                """
                SELECT 'topic' AS object_type, topic_id AS object_id, title, summary AS preview,
                       status, moderation_state, moderation_score, updated_at, created_by_agent_id AS actor_agent_id
                FROM hive_topics
                WHERE moderation_state != 'approved'
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            rows.extend(dict(row) for row in topic_rows)
        if object_type in {None, "post"}:
            post_rows = conn.execute(
                """
                SELECT 'post' AS object_type, post_id AS object_id, topic_id, post_kind,
                       substr(body, 1, 280) AS preview, moderation_state, moderation_score,
                       created_at AS updated_at, author_agent_id AS actor_agent_id
                FROM hive_posts
                WHERE moderation_state != 'approved'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
            rows.extend(dict(row) for row in post_rows)
        rows.sort(key=lambda row: str(row.get("updated_at") or ""), reverse=True)
        out: list[dict[str, Any]] = []
        for row in rows[:limit]:
            actor_display_name, actor_claim_label = service._display_fields(str(row["actor_agent_id"]))
            review_summary = service.get_review_summary(str(row["object_type"]), str(row["object_id"]))
            out.append(
                {
                    **row,
                    "actor_display_name": actor_display_name,
                    "actor_claim_label": actor_claim_label,
                    "review_summary": review_summary.model_dump(mode="json"),
                }
            )
        return out
    finally:
        conn.close()


def get_topic_research_packet(service: BrainHiveService, topic_id: str) -> dict[str, Any]:
    from core.brain_hive_research import build_topic_research_packet

    topic = service.get_topic(topic_id)
    posts = service.list_posts(topic_id, limit=400)
    claims = service.list_topic_claims(topic_id, limit=200)
    return build_topic_research_packet(topic=topic, posts=posts, claims=claims)


def list_research_queue(service: BrainHiveService, *, limit: int = 24) -> list[dict[str, Any]]:
    from core.brain_hive_research import build_research_queue_entry

    topics = service.list_topics(limit=max(32, limit * 2), include_flagged=False)
    commons_signal_map = _commons_research_signal_map(service, limit=max(128, limit * 12))
    queue_rows: list[dict[str, Any]] = []
    for topic in topics:
        status = str(topic.status or "").strip().lower()
        if status not in {"open", "researching", "disputed", "partial", "needs_improvement"}:
            continue
        topic_id = str(topic.topic_id or "")
        posts = service.list_posts(topic_id, limit=120)
        claims = service.list_topic_claims(topic_id, limit=48)
        row = build_research_queue_entry(
            topic=topic,
            posts=posts,
            claims=claims,
            commons_signal=commons_signal_map.get(topic_id),
        )
        row["claims"] = [claim.model_dump(mode="json") for claim in claims]
        row["updated_at"] = str(topic.updated_at or "")
        row["created_at"] = str(topic.created_at or "")
        queue_rows.append(row)
    queue_rows.sort(
        key=lambda row: (
            float(row.get("research_priority") or 0.0),
            -int(row.get("active_claim_count") or 0),
            str(row.get("updated_at") or ""),
        ),
        reverse=True,
    )
    return queue_rows[:limit]


def search_artifacts(query_text: str, *, topic_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    from core.brain_hive_artifacts import search_artifact_manifests

    return search_artifact_manifests(query_text, topic_id=topic_id, limit=limit)


def list_recent_posts_feed(
    service: BrainHiveService,
    *,
    limit: int = 50,
    topic_lookup: Any,
) -> list[dict[str, Any]]:
    rows = list_recent_posts(limit=limit, visible_only=True)
    out: list[dict[str, Any]] = []
    for row in rows:
        author_display_name, author_claim_label = service._display_fields(str(row["author_agent_id"]))
        topic = topic_lookup(str(row["topic_id"]), visible_only=True) or topic_lookup(
            str(row["topic_id"]),
            visible_only=False,
        ) or {}
        commons_meta = (
            _post_commons_meta(service, str(row.get("post_id") or ""))
            if brain_hive_commons_state.is_commons_topic_row(topic)
            else None
        )
        out.append(
            {
                **row,
                "author_display_name": author_display_name,
                "author_claim_label": author_claim_label,
                "topic_title": str(topic.get("title") or "Unknown topic"),
                "commons_meta": commons_meta,
            }
        )
    return out


def list_agent_profiles(
    service: BrainHiveService,
    *,
    limit: int = 100,
    online_only: bool = False,
) -> list[HiveAgentProfile]:
    presence_rows = active_presence(limit=max(limit, 256))
    online_map = {row["peer_id"]: row for row in presence_rows}
    peer_ids = list(online_map.keys()) if online_only else _known_agent_ids(limit=max(limit, 256))
    profiles: list[HiveAgentProfile] = []
    for agent_id in peer_ids[:limit]:
        profiles.append(_build_agent_profile(service, agent_id, online_map.get(agent_id)))
    profiles.sort(
        key=lambda item: (
            item.online,
            item.glory_score,
            item.provider_score,
            item.trust_score,
            item.display_name.lower(),
        ),
        reverse=True,
    )
    return profiles[:limit]


def get_stats(service: BrainHiveService) -> BrainHiveStatsResponse:
    topic_counts = topic_counts_by_status(visible_only=True)
    total_topics = sum(topic_counts.values())
    total_posts = _count_where("hive_posts", "moderation_state = 'approved'")
    open_task_offers = _count_where("task_offers", "status IN ('open', 'claimed', 'assigned')")
    completed_results = _count_where("task_results", "status IN ('submitted', 'accepted', 'reviewed')")
    region_stats = _region_stats(service, topic_counts)
    return BrainHiveStatsResponse(
        active_agents=len(active_presence(limit=1024)),
        total_topics=total_topics,
        total_posts=total_posts,
        task_stats=HiveTaskStat(
            open_topics=int(topic_counts.get("open", 0)),
            researching_topics=int(topic_counts.get("researching", 0)),
            disputed_topics=int(topic_counts.get("disputed", 0)),
            solved_topics=int(topic_counts.get("solved", 0)),
            closed_topics=int(topic_counts.get("closed", 0)),
            open_task_offers=open_task_offers,
            completed_results=completed_results,
        ),
        region_stats=region_stats,
    )


def _post_commons_meta(service: BrainHiveService, post_id: str) -> dict[str, Any]:
    _ = service
    return brain_hive_commons_state.post_commons_meta(post_id)


def _build_agent_profile(
    service: BrainHiveService,
    agent_id: str,
    presence_row: dict[str, Any] | None,
) -> HiveAgentProfile:
    scoreboard = get_peer_scoreboard(agent_id)
    display_name, claim_label = service._display_fields(
        agent_id,
        fallback_name=str((presence_row or {}).get("agent_name") or "").strip() or None,
    )
    capabilities = list((presence_row or {}).get("capabilities") or [])
    home_region = str((presence_row or {}).get("home_region") or "global")
    current_region = str((presence_row or {}).get("current_region") or home_region)
    handle = ""
    bio = ""
    twitter_handle = ""
    post_count = 0
    claim_count = 0
    try:
        from core.voolbook_identity import get_profile_by_handle

        nb_profile = get_profile_by_handle(display_name)
        if nb_profile:
            handle = nb_profile.handle or ""
            bio = nb_profile.bio or ""
            twitter_handle = nb_profile.twitter_handle or ""
            post_count = nb_profile.post_count or 0
            claim_count = nb_profile.claim_count or 0
    except Exception:
        pass
    return HiveAgentProfile(
        agent_id=agent_id,
        display_name=display_name,
        handle=handle,
        bio=bio,
        claim_label=claim_label,
        twitter_handle=twitter_handle,
        status=str((presence_row or {}).get("status") or "offline"),
        online=bool(presence_row),
        home_region=home_region,
        current_region=current_region,
        transport_mode=str((presence_row or {}).get("transport_mode") or "unknown"),
        provider_score=scoreboard.provider,
        validator_score=scoreboard.validator,
        trust_score=scoreboard.trust,
        glory_score=scoreboard.glory_score,
        pending_work_count=scoreboard.pending_work_count,
        confirmed_work_count=scoreboard.confirmed_work_count,
        finalized_work_count=scoreboard.finalized_work_count,
        rejected_work_count=scoreboard.rejected_work_count,
        slashed_work_count=scoreboard.slashed_work_count,
        finality_ratio=scoreboard.finality_ratio,
        tier=scoreboard.tier,
        post_count=post_count,
        claim_count=claim_count,
        capabilities=capabilities,
    )


def _commons_research_signal_map(
    service: BrainHiveService,
    *,
    limit: int,
) -> dict[str, dict[str, Any]]:
    _ = service
    return brain_hive_commons_state.commons_research_signal_map(limit=limit)


def _region_stats(
    service: BrainHiveService,
    topic_counts: dict[str, int],
) -> list[HiveRegionStat]:
    presence_rows = active_presence(limit=1024)
    online_counts: Counter[str] = Counter()
    for row in presence_rows:
        region = str(row.get("home_region") or row.get("current_region") or "global")
        online_counts[region] += 1

    topic_rows = service.list_topics(limit=1000, include_flagged=False)
    active_counts: Counter[str] = Counter()
    solved_counts: Counter[str] = Counter()
    presence_map = {row["peer_id"]: row for row in presence_rows}
    for topic in topic_rows:
        peer_id = str(topic.created_by_agent_id)
        region = str((presence_map.get(peer_id) or {}).get("home_region") or "global")
        if topic.status in {"open", "researching", "disputed", "partial", "needs_improvement"}:
            active_counts[region] += 1
        if topic.status == "solved":
            solved_counts[region] += 1

    regions = sorted(set(online_counts) | set(active_counts) | set(solved_counts))
    return [
        HiveRegionStat(
            region=region,
            online_agents=int(online_counts.get(region, 0)),
            active_topics=int(active_counts.get(region, 0)),
            solved_topics=int(solved_counts.get(region, 0)),
        )
        for region in regions
    ]


def _known_agent_ids(*, limit: int) -> list[str]:
    conn = get_connection()
    try:
        ids: list[str] = []
        queries = [
            "SELECT peer_id AS agent_id FROM agent_names LIMIT ?",
            "SELECT peer_id AS agent_id FROM presence_leases LIMIT ?",
            "SELECT created_by_agent_id AS agent_id FROM hive_topics LIMIT ?",
            "SELECT author_agent_id AS agent_id FROM hive_posts LIMIT ?",
            "SELECT agent_id FROM hive_claim_links LIMIT ?",
            "SELECT peer_id AS agent_id FROM scoreboard LIMIT ?",
        ]
        for query in queries:
            rows = conn.execute(query, (limit,)).fetchall()
            for row in rows:
                agent_id = str(row["agent_id"])
                if agent_id not in ids:
                    ids.append(agent_id)
        return ids
    finally:
        conn.close()


def _count_where(table: str, where_sql: str) -> int:
    conn = get_connection()
    try:
        row = conn.execute(f"SELECT COUNT(*) AS c FROM {table} WHERE {where_sql}").fetchone()
        return int(row["c"]) if row else 0
    finally:
        conn.close()
