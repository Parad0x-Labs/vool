from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

from core.brain_hive_service import BrainHiveService

TRADING_SCANNER_AGENT_ID = "vool:trading-scanner"
TRADING_SCANNER_LIVE_SEC = 300.0
TRADING_SCANNER_VISIBLE_SEC = 1800.0


def _agent_is_online(agent: dict[str, Any]) -> bool:
    status = str((agent or {}).get("status") or "").strip().lower()
    if bool((agent or {}).get("online")):
        return True
    return status in {"online", "idle", "busy", "limited"}


def _agent_profile_key(agent: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    label = str(agent.get("display_name") or agent.get("claim_label") or agent.get("agent_id") or "agent").strip().lower()
    region = str(agent.get("home_region") or agent.get("current_region") or "global").strip().lower()
    capabilities = tuple(
        sorted(
            str(item).strip().lower()
            for item in list(agent.get("capabilities") or [])
            if str(item).strip()
        )
    )
    return (label, region, capabilities)


def _agent_profile_rank(agent: dict[str, Any]) -> tuple[int, int, int, str]:
    status = str(agent.get("status") or "").strip().lower()
    transport = str(agent.get("transport_mode") or "").strip().lower()
    status_rank = {
        "busy": 4,
        "online": 3,
        "idle": 2,
        "limited": 1,
    }.get(status, 0)
    transport_rank = {
        "channel_openclaw": 4,
        "vool_agent": 3,
        "direct": 2,
        "hole_punch": 1,
        "relay": 1,
        "lan_only": 0,
        "background_openclaw": 0,
    }.get(transport, 0)
    capability_count = len([item for item in list(agent.get("capabilities") or []) if str(item).strip()])
    return (status_rank, transport_rank, capability_count, str(agent.get("agent_id") or ""))


def _distinct_visible_agents(agents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chosen: dict[tuple[str, str, tuple[str, ...]], dict[str, Any]] = {}
    for agent in list(agents or []):
        key = _agent_profile_key(agent)
        current = chosen.get(key)
        if current is None or _agent_profile_rank(agent) > _agent_profile_rank(current):
            chosen[key] = dict(agent)
    return list(chosen.values())


def _display_agent_stats(stats: dict[str, Any], agents: list[dict[str, Any]]) -> dict[str, Any]:
    merged = dict(stats or {})
    distinct_agents = _distinct_visible_agents(agents)
    raw_presence = merged.get("presence_agents", merged.get("active_agents", 0))
    merged["presence_agents"] = int(raw_presence or 0)
    merged["raw_online_agents"] = sum(1 for agent in agents if _agent_is_online(agent))
    merged["raw_visible_agents"] = len(agents)
    merged["duplicate_visible_agents"] = max(0, len(agents) - len(distinct_agents))
    merged["active_agents"] = sum(1 for agent in distinct_agents if _agent_is_online(agent))
    merged["visible_agents"] = len(distinct_agents)
    return merged


def build_dashboard_snapshot(
    hive: BrainHiveService | None = None,
    *,
    topic_limit: int = 12,
    post_limit: int = 24,
    agent_limit: int = 24,
    hooks: Any,
) -> dict[str, Any]:
    service = hive or BrainHiveService()
    stats = service.get_stats().model_dump(mode="json")
    topics: list[dict[str, Any]] = []
    for item in service.list_topics(limit=max(32, topic_limit * 4)):
        payload = item.model_dump(mode="json")
        if str(payload.get("status") or "").strip().lower() not in {"open", "researching", "disputed", "partial", "needs_improvement"}:
            continue
        topics.append(payload)
        if len(topics) >= max(1, topic_limit):
            break
    agents = [item.model_dump(mode="json") for item in service.list_agent_profiles(limit=max(1, agent_limit))]
    posts = list(service.list_recent_posts_feed(limit=max(1, post_limit)))
    all_topics = [item.model_dump(mode="json") for item in service.list_topics(limit=max(32, topic_limit * 4))]
    feed_posts = list(service.list_recent_posts_feed(limit=max(48, post_limit * 4)))
    active_topic_ids = [
        str(topic.get("topic_id") or "")
        for topic in all_topics
        if str(topic.get("status") or "").strip().lower() in {"open", "researching", "disputed", "partial", "needs_improvement"}
    ][:16]
    trading_topic_ids = [
        str(topic.get("topic_id") or "")
        for topic in all_topics
        if _is_trading_learning_topic(topic)
    ][:8]
    scoped_topic_ids: list[str] = []
    for topic_id in trading_topic_ids + active_topic_ids:
        if topic_id and topic_id not in scoped_topic_ids:
            scoped_topic_ids.append(topic_id)
    scoped_posts: list[dict[str, Any]] = []
    for topic_id in scoped_topic_ids:
        scoped_posts.extend(_safe_list_posts(service, topic_id, limit=120 if topic_id in trading_topic_ids else 48))
    all_posts = _merge_posts(feed_posts, scoped_posts)
    topic_claims = list(_safe_list_recent_topic_claims_feed(service, limit=max(48, post_limit * 4)))
    summary = hooks.build_user_summary(limit_recent=8)
    control_plane = hooks.collect_control_plane_status()
    proof_of_useful_work = dict(control_plane.get("proof_of_useful_work") or {})
    adaptation_proof = dict(control_plane.get("adaptation_proof") or {})
    generated_at = datetime.now(timezone.utc).isoformat()
    trading_learning = _build_trading_learning_payload(
        topics=all_topics,
        posts=all_posts,
    )
    stats, agents = _augment_dashboard_with_trading_scanner(
        stats=stats,
        agents=agents,
        trading_learning=trading_learning,
        generated_at=generated_at,
    )
    stats = _display_agent_stats(stats, agents)
    agents = _distinct_visible_agents(agents)
    recent_evals = list((control_plane.get("adaptation") or {}).get("recent_evals") or [])
    latest_eval = dict(recent_evals[0] or {}) if recent_evals else {}
    return {
        "generated_at": generated_at,
        "branding": hooks._branding_payload(),
        "stats": stats,
        "proof_of_useful_work": proof_of_useful_work,
        "adaptation_proof": adaptation_proof,
        "mesh_overview": summary["mesh_index"],
        "learning_overview": summary["learning"],
        "knowledge_overview": dict(summary.get("knowledge_lanes") or {}),
        "memory_overview": {
            "local_task_count": int(summary["memory"]["local_task_count"]),
            "finalized_response_count": int(summary["memory"]["finalized_response_count"]),
            "mesh_learning_rows": int(summary["memory"]["mesh_learning_rows"]),
            "useful_output_count": int(summary["memory"].get("useful_output_count") or 0),
            "training_eligible_output_count": int(summary["memory"].get("training_eligible_output_count") or 0),
            "archive_candidate_count": int(summary["memory"].get("archive_candidate_count") or 0),
        },
        "adaptation_overview": {
            "status": str(((control_plane.get("adaptation") or {}).get("loop_state") or {}).get("status") or "idle"),
            "decision": str(((control_plane.get("adaptation") or {}).get("loop_state") or {}).get("last_decision") or "none"),
            "blocker": str(((control_plane.get("adaptation") or {}).get("loop_state") or {}).get("last_reason") or "none"),
            "quality_score": float(((control_plane.get("adaptation") or {}).get("loop_state") or {}).get("last_quality_score") or 0.0),
            "training_ready": int((control_plane.get("useful_outputs") or {}).get("training_eligible_count") or 0),
            "high_signal": int((control_plane.get("useful_outputs") or {}).get("high_signal_count") or 0),
            "proof_state": str(adaptation_proof.get("proof_state") or "no_recent_eval"),
            "latest_eval": latest_eval,
            "recent_evals": recent_evals[:4],
        },
        "commons_overview": {
            "promotion_candidates": [
                item.model_dump(mode="json")
                for item in (
                    list(service.list_commons_promotion_candidates(limit=8))
                    if callable(getattr(service, "list_commons_promotion_candidates", None))
                    else []
                )
            ],
        },
        "recent_activity": {
            "tasks": list(summary["memory"]["recent_tasks"]),
            "responses": list(summary["memory"]["recent_final_responses"]),
            "learning": list(summary["learning"]["recent_learning"]),
        },
        "topics": topics,
        "research_queue": _safe_list_research_queue(service, limit=8),
        "recent_posts": posts,
        "recent_topic_claims": topic_claims[:24],
        "task_event_stream": _build_task_event_stream(
            topics=all_topics,
            posts=all_posts,
            topic_claims=topic_claims,
        ),
        "agents": agents,
        "trading_learning": trading_learning,
        "learning_lab": _build_learning_lab_payload(
            hive=service,
            topics=all_topics,
            posts=all_posts,
            hooks=hooks,
        ),
    }


def _safe_list_recent_topic_claims_feed(service: BrainHiveService, *, limit: int) -> list[dict[str, Any]]:
    method = getattr(service, "list_recent_topic_claims_feed", None)
    if callable(method):
        try:
            return list(method(limit=limit))
        except Exception:
            return []
    return []


def _safe_list_research_queue(service: BrainHiveService, *, limit: int) -> list[dict[str, Any]]:
    method = getattr(service, "list_research_queue", None)
    if callable(method):
        try:
            return list(method(limit=limit))
        except Exception:
            return []
    return []


def _safe_list_topic_claims(service: BrainHiveService, topic_id: str, *, limit: int) -> list[dict[str, Any]]:
    method = getattr(service, "list_topic_claims", None)
    if callable(method):
        try:
            return [item.model_dump(mode="json") for item in method(topic_id, limit=limit)]
        except Exception:
            return []
    return []


def _safe_list_posts(service: BrainHiveService, topic_id: str, *, limit: int) -> list[dict[str, Any]]:
    method = getattr(service, "list_posts", None)
    if callable(method):
        try:
            return [item.model_dump(mode="json") for item in method(topic_id, limit=limit)]
        except Exception:
            return []
    return []


def _merge_posts(*post_groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    synthetic_index = 0
    for group in post_groups:
        for post in list(group or []):
            if not isinstance(post, dict):
                continue
            post_id = str(post.get("post_id") or "").strip()
            if not post_id:
                synthetic_index += 1
                post_id = f"synthetic-{synthetic_index}"
            existing = merged.get(post_id)
            if existing is None or str(post.get("created_at") or "") >= str(existing.get("created_at") or ""):
                merged[post_id] = dict(post)
    return sorted(merged.values(), key=lambda row: str(row.get("created_at") or ""), reverse=True)


def _is_trading_learning_topic(topic: dict[str, Any]) -> bool:
    tags = {str(item or "").strip().lower() for item in list(topic.get("topic_tags") or []) if str(item or "").strip()}
    combined = f"{topic.get('title') or ''!s} {topic.get('summary') or ''!s}".lower()
    return (
        "trading_learning" in tags
        or "manual_trader" in tags
        or "calls" in tags
        or "trading learning" in combined
        or "manual trader" in combined
    )


def _parse_dashboard_timestamp(value: Any) -> float:
    if value in (None, "", 0):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _latest_trading_presence_ts(trading_learning: dict[str, Any]) -> float:
    latest_ts = 0.0
    heartbeat = dict(trading_learning.get("latest_heartbeat") or {})
    latest_ts = max(
        latest_ts,
        _parse_dashboard_timestamp(heartbeat.get("last_tick_ts")),
        _parse_dashboard_timestamp(heartbeat.get("post_created_at")),
    )
    for topic in list(trading_learning.get("topics") or []):
        if not isinstance(topic, dict):
            continue
        latest_ts = max(
            latest_ts,
            _parse_dashboard_timestamp(topic.get("updated_at")),
            _parse_dashboard_timestamp(topic.get("created_at")),
        )
    return latest_ts


def _augment_dashboard_with_trading_scanner(
    *,
    stats: dict[str, Any],
    agents: list[dict[str, Any]],
    trading_learning: dict[str, Any],
    generated_at: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    heartbeat = dict(trading_learning.get("latest_heartbeat") or {})
    generated_ts = _parse_dashboard_timestamp(generated_at)
    presence_ts = _latest_trading_presence_ts(trading_learning)
    if not presence_ts or not generated_ts:
        return stats, agents

    age_sec = max(0.0, generated_ts - presence_ts)
    if age_sec > TRADING_SCANNER_VISIBLE_SEC:
        return stats, agents

    online = age_sec <= TRADING_SCANNER_LIVE_SEC
    summary = dict(trading_learning.get("latest_summary") or {})
    synthetic_agent = {
        "agent_id": TRADING_SCANNER_AGENT_ID,
        "claim_label": "Vool Trading Scanner",
        "display_name": "Vool Trading Scanner",
        "home_region": "local-trading",
        "current_region": "brain-hive",
        "online": online,
        "status": "online" if online else "stale",
        "trust_score": 1.0,
        "glory_score": 0.0,
        "provider_score": float(heartbeat.get("tracked_tokens", 0) or 0),
        "validator_score": float(summary.get("total_calls", 0) or 0),
        "pending_work_count": 0,
        "confirmed_work_count": 0,
        "finalized_work_count": 0,
        "rejected_work_count": 0,
        "slashed_work_count": 0,
        "finality_ratio": 0.0,
        "capabilities": [
            "trading_learning",
            "manual_signals",
            "paper_shadow",
            "stealth_accumulation",
        ],
    }

    merged_agents: list[dict[str, Any]] = [dict(agent) for agent in agents]
    existing_index = next(
        (
            idx
            for idx, agent in enumerate(merged_agents)
            if str(agent.get("agent_id") or "") == TRADING_SCANNER_AGENT_ID
            or str(agent.get("claim_label") or agent.get("display_name") or "").strip().lower() == "vool trading scanner"
        ),
        -1,
    )
    if existing_index >= 0:
        current = dict(merged_agents[existing_index])
        current.update(synthetic_agent)
        merged_agents[existing_index] = current
    else:
        merged_agents.insert(0, synthetic_agent)

    return _display_agent_stats(stats, merged_agents), merged_agents


def _build_trading_learning_payload(*, topics: list[dict[str, Any]], posts: list[dict[str, Any]]) -> dict[str, Any]:
    trading_topics = [topic for topic in topics if _is_trading_learning_topic(topic)]
    topic_ids = {str(topic.get("topic_id") or "") for topic in trading_topics}
    trading_posts = [
        post for post in posts
        if str(post.get("topic_id") or "") in topic_ids
        or "trading learning" in str(post.get("topic_title") or "").lower()
    ]

    latest_summary: dict[str, Any] = {}
    latest_heartbeat: dict[str, Any] = {}
    lab_summary: dict[str, Any] = {}
    decision_funnel: dict[str, Any] = {}
    pattern_health: dict[str, Any] = {}
    calls_by_mint: dict[str, dict[str, Any]] = {}
    missed_by_key: dict[str, dict[str, Any]] = {}
    edges_by_key: dict[str, dict[str, Any]] = {}
    discoveries_by_key: dict[str, dict[str, Any]] = {}
    lessons: list[dict[str, Any]] = []
    updates: list[dict[str, Any]] = []
    flow: list[dict[str, Any]] = []

    def _merge_rows(target: dict[str, dict[str, Any]], rows: list[Any], key_fields: tuple[str, ...]) -> None:
        for item in rows:
            if not isinstance(item, dict):
                continue
            key = ""
            for field in key_fields:
                value = str(item.get(field) or "").strip()
                if value:
                    key = value
                    break
            if not key:
                continue
            merged = dict(target.get(key) or {})
            for name, value in item.items():
                if value not in (None, "", [], {}):
                    merged[name] = value
            target[key] = merged

    def _extend_flow(items: list[Any]) -> None:
        for item in items:
            if isinstance(item, dict):
                flow.append(dict(item))

    for post in reversed(trading_posts):
        refs = list(post.get("evidence_refs") or [])
        for ref in refs:
            if not isinstance(ref, dict):
                continue
            kind = str(ref.get("kind") or "").strip().lower()
            if kind == "trading_learning_summary" and isinstance(ref.get("summary"), dict):
                latest_summary = {
                    **dict(ref.get("summary") or {}),
                    "topic_id": str(post.get("topic_id") or ""),
                    "topic_title": str(post.get("topic_title") or ""),
                    "post_created_at": str(post.get("created_at") or ""),
                }
            elif kind == "trading_runtime_heartbeat" and isinstance(ref.get("heartbeat"), dict):
                latest_heartbeat = {
                    **dict(ref.get("heartbeat") or {}),
                    "topic_id": str(post.get("topic_id") or ""),
                    "topic_title": str(post.get("topic_title") or ""),
                    "post_created_at": str(post.get("created_at") or ""),
                }
            elif kind == "trading_learning_lab_summary" and isinstance(ref.get("summary"), dict):
                summary_payload = dict(ref.get("summary") or {})
                lab_summary = {
                    **summary_payload,
                    "topic_id": str(post.get("topic_id") or ""),
                    "topic_title": str(post.get("topic_title") or ""),
                    "post_created_at": str(post.get("created_at") or ""),
                }
                if not decision_funnel and isinstance(summary_payload.get("decision_funnel"), dict):
                    decision_funnel = {
                        **dict(summary_payload.get("decision_funnel") or {}),
                        "topic_id": str(post.get("topic_id") or ""),
                        "topic_title": str(post.get("topic_title") or ""),
                        "post_created_at": str(post.get("created_at") or ""),
                    }
                if not pattern_health and isinstance(summary_payload.get("pattern_health"), dict):
                    pattern_health = {
                        **dict(summary_payload.get("pattern_health") or {}),
                        "topic_id": str(post.get("topic_id") or ""),
                        "topic_title": str(post.get("topic_title") or ""),
                        "post_created_at": str(post.get("created_at") or ""),
                    }
                missed_items = summary_payload.get("missed_mooner_items")
                if not isinstance(missed_items, list):
                    missed_items = summary_payload.get("missed_mooners") if isinstance(summary_payload.get("missed_mooners"), list) else []
                hidden_edge_items = summary_payload.get("hidden_edge_items")
                if not isinstance(hidden_edge_items, list):
                    hidden_edge_items = summary_payload.get("hidden_edges") if isinstance(summary_payload.get("hidden_edges"), list) else []
                discovery_items = summary_payload.get("discovery_items")
                if not isinstance(discovery_items, list):
                    discovery_items = summary_payload.get("discoveries") if isinstance(summary_payload.get("discoveries"), list) else []
                flow_items = summary_payload.get("flow_items")
                if not isinstance(flow_items, list):
                    flow_items = summary_payload.get("flow") if isinstance(summary_payload.get("flow"), list) else []
                _merge_rows(missed_by_key, list(missed_items), ("id", "token_mint"))
                _merge_rows(edges_by_key, list(hidden_edge_items), ("id", "metric"))
                _merge_rows(discoveries_by_key, list(discovery_items), ("id", "discovery"))
                _extend_flow(list(flow_items))
            elif kind == "trading_decision_funnel" and isinstance(ref.get("summary"), dict):
                decision_funnel = {
                    **dict(ref.get("summary") or {}),
                    "topic_id": str(post.get("topic_id") or ""),
                    "topic_title": str(post.get("topic_title") or ""),
                    "post_created_at": str(post.get("created_at") or ""),
                }
            elif kind == "trading_calls":
                for item in list(ref.get("items") or []):
                    if not isinstance(item, dict):
                        continue
                    mint = str(item.get("token_mint") or item.get("call_id") or "").strip()
                    if not mint:
                        continue
                    merged = dict(calls_by_mint.get(mint) or {})
                    for key, value in item.items():
                        if value not in (None, "", [], {}):
                            merged[key] = value
                    merged["topic_id"] = str(post.get("topic_id") or "")
                    merged["topic_title"] = str(post.get("topic_title") or "")
                    calls_by_mint[mint] = merged
            elif kind == "trading_missed_mooners":
                _merge_rows(missed_by_key, list(ref.get("items") or []), ("id", "token_mint"))
            elif kind == "trading_hidden_edges":
                _merge_rows(edges_by_key, list(ref.get("items") or []), ("id", "metric"))
            elif kind == "trading_discoveries":
                _merge_rows(discoveries_by_key, list(ref.get("items") or []), ("id", "discovery"))
            elif kind == "trading_pattern_health" and isinstance(ref.get("summary"), dict):
                pattern_health = {
                    **dict(ref.get("summary") or {}),
                    "topic_id": str(post.get("topic_id") or ""),
                    "topic_title": str(post.get("topic_title") or ""),
                    "post_created_at": str(post.get("created_at") or ""),
                }
            elif kind == "trading_live_flow":
                _extend_flow(list(ref.get("items") or []))
            elif kind == "trading_lessons":
                for item in list(ref.get("items") or []):
                    if isinstance(item, dict):
                        lessons.append(dict(item))
            elif kind == "trading_ath_updates":
                for item in list(ref.get("items") or []):
                    if isinstance(item, dict):
                        updates.append(dict(item))

    calls = sorted(
        calls_by_mint.values(),
        key=lambda row: float(row.get("call_ts", 0.0) or 0.0),
        reverse=True,
    )[:40]
    missed_mooners = sorted(
        missed_by_key.values(),
        key=lambda row: float(row.get("ts", 0.0) or 0.0),
        reverse=True,
    )[:20]
    hidden_edges = sorted(
        edges_by_key.values(),
        key=lambda row: float(row.get("score", 0.0) or 0.0),
        reverse=True,
    )[:20]
    discoveries = sorted(
        discoveries_by_key.values(),
        key=lambda row: float(row.get("ts", 0.0) or 0.0),
        reverse=True,
    )[:20]
    flow = sorted(
        flow,
        key=lambda row: float(row.get("ts", 0.0) or 0.0),
        reverse=True,
    )[:30]
    lessons = lessons[:12]
    updates = updates[:12]
    recent_call_items = list(lab_summary.get("recent_call_items") or [])[:16]
    return {
        "topic_count": len(trading_topics),
        "topics": trading_topics[:8],
        "latest_summary": latest_summary,
        "latest_heartbeat": latest_heartbeat,
        "lab_summary": lab_summary,
        "decision_funnel": decision_funnel,
        "pattern_health": pattern_health,
        "calls": calls,
        "missed_mooners": missed_mooners,
        "hidden_edges": hidden_edges,
        "discoveries": discoveries,
        "flow": flow,
        "recent_calls": recent_call_items,
        "lessons": lessons,
        "updates": updates,
        "recent_posts": trading_posts[:12],
    }


def _build_task_event_stream(
    *,
    topics: list[dict[str, Any]],
    posts: list[dict[str, Any]],
    topic_claims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for topic in topics:
        events.append(
            {
                "event_type": "topic_created",
                "topic_id": str(topic.get("topic_id") or ""),
                "topic_title": str(topic.get("title") or "Untitled topic"),
                "status": str(topic.get("status") or "open"),
                "agent_label": str(topic.get("creator_claim_label") or topic.get("creator_display_name") or topic.get("created_by_agent_id") or ""),
                "timestamp": str(topic.get("created_at") or ""),
                "detail": str(topic.get("summary") or ""),
                "tags": list(topic.get("topic_tags") or [])[:6],
            }
        )
    for claim in topic_claims:
        claim_status = str(claim.get("status") or "active")
        event_type = {
            "active": "task_claimed",
            "released": "task_released",
            "completed": "task_completed",
            "blocked": "task_blocked",
        }.get(claim_status, "task_claimed")
        events.append(
            {
                "event_type": event_type,
                "topic_id": str(claim.get("topic_id") or ""),
                "topic_title": str(claim.get("topic_title") or "Unknown topic"),
                "status": str(claim.get("topic_status") or ""),
                "agent_label": str(claim.get("agent_claim_label") or claim.get("agent_display_name") or claim.get("agent_id") or ""),
                "timestamp": str(claim.get("updated_at") or claim.get("created_at") or ""),
                "detail": str(claim.get("note") or ""),
                "claim_id": str(claim.get("claim_id") or ""),
                "capability_tags": list(claim.get("capability_tags") or [])[:6],
            }
        )
    for post in posts:
        event_meta = _task_event_meta(post)
        post_kind = str(post.get("post_kind") or "analysis")
        event_type = str(event_meta.get("event_type") or _post_kind_event_type(post_kind))
        events.append(
            {
                "event_type": event_type,
                "topic_id": str(post.get("topic_id") or ""),
                "topic_title": str(post.get("topic_title") or "Unknown topic"),
                "status": str(event_meta.get("result_status") or post_kind),
                "agent_label": str(post.get("author_claim_label") or post.get("author_display_name") or post.get("author_agent_id") or ""),
                "timestamp": str(post.get("created_at") or ""),
                "detail": str(post.get("body") or ""),
                "claim_id": str(event_meta.get("claim_id") or ""),
                "progress_state": str(event_meta.get("progress_state") or ""),
                "post_kind": post_kind,
            }
        )
    return sorted(events, key=lambda row: str(row.get("timestamp") or ""), reverse=True)[:40]


def _build_learning_lab_payload(
    *,
    hive: BrainHiveService,
    topics: list[dict[str, Any]],
    posts: list[dict[str, Any]],
    hooks: Any,
) -> dict[str, Any]:
    active_topics = [
        topic for topic in topics
        if str(topic.get("status") or "").strip().lower() in {"open", "researching", "disputed", "partial", "needs_improvement"}
    ]
    posts_by_topic: dict[str, list[dict[str, Any]]] = {}
    for post in posts:
        posts_by_topic.setdefault(str(post.get("topic_id") or ""), []).append(post)

    payload_topics: list[dict[str, Any]] = []
    for topic in active_topics[:16]:
        topic_id = str(topic.get("topic_id") or "")
        topic_posts = list(posts_by_topic.get(topic_id) or [])
        claims = _safe_list_topic_claims(hive, topic_id, limit=24)
        evidence_counts: Counter[str] = Counter()
        post_kind_counts: Counter[str] = Counter()
        for post in topic_posts:
            post_kind_counts[str(post.get("post_kind") or "analysis")] += 1
            for ref in list(post.get("evidence_refs") or []):
                if not isinstance(ref, dict):
                    continue
                kind = str(ref.get("kind") or ref.get("type") or "reference").strip() or "reference"
                evidence_counts[kind] += 1
        recent_events = [
            event for event in _build_task_event_stream(topics=[topic], posts=topic_posts[:16], topic_claims=claims)
            if str(event.get("topic_id") or "") == topic_id
        ][:8]
        artifact_count = hooks.count_artifact_manifests(topic_id=topic_id)
        payload_topics.append(
            {
                "topic_id": topic_id,
                "title": str(topic.get("title") or "Untitled topic"),
                "summary": str(topic.get("summary") or ""),
                "status": str(topic.get("status") or "open"),
                "updated_at": str(topic.get("updated_at") or ""),
                "created_at": str(topic.get("created_at") or ""),
                "creator_label": str(
                    topic.get("creator_claim_label") or topic.get("creator_display_name") or topic.get("created_by_agent_id") or ""
                ),
                "linked_task_id": str(topic.get("linked_task_id") or ""),
                "topic_tags": list(topic.get("topic_tags") or [])[:12],
                "post_count": len(topic_posts),
                "claim_count": len(claims),
                "active_claim_count": sum(1 for claim in claims if str(claim.get("status") or "") == "active"),
                "artifact_count": artifact_count,
                "packet_endpoint": f"/v1/hive/topics/{topic_id}/research-packet",
                "evidence_kind_counts": [
                    {"kind": kind, "count": int(count)}
                    for kind, count in evidence_counts.most_common(8)
                ],
                "post_kind_counts": [
                    {"kind": kind, "count": int(count)}
                    for kind, count in post_kind_counts.most_common(6)
                ],
                "claims": [
                    {
                        "claim_id": str(claim.get("claim_id") or ""),
                        "agent_label": str(
                            claim.get("agent_claim_label") or claim.get("agent_display_name") or claim.get("agent_id") or ""
                        ),
                        "status": str(claim.get("status") or ""),
                        "note": str(claim.get("note") or ""),
                        "updated_at": str(claim.get("updated_at") or ""),
                        "capability_tags": list(claim.get("capability_tags") or [])[:8],
                    }
                    for claim in claims[:6]
                ],
                "recent_posts": [
                    {
                        "created_at": str(post.get("created_at") or ""),
                        "post_kind": str(post.get("post_kind") or "analysis"),
                        "stance": str(post.get("stance") or ""),
                        "author_label": str(
                            post.get("author_claim_label") or post.get("author_display_name") or post.get("author_agent_id") or ""
                        ),
                        "body": str(post.get("body") or ""),
                        "evidence_kinds": [
                            str(ref.get("kind") or ref.get("type") or "reference")
                            for ref in list(post.get("evidence_refs") or [])
                            if isinstance(ref, dict)
                        ][:6],
                    }
                    for post in topic_posts[:6]
                ],
                "recent_events": recent_events,
            }
        )

    return {
        "topic_count": len(payload_topics),
        "active_topics": payload_topics,
    }


def _task_event_meta(post: dict[str, Any]) -> dict[str, Any]:
    for ref in list(post.get("evidence_refs") or []):
        if isinstance(ref, dict) and str(ref.get("kind") or "").strip().lower() == "task_event":
            return dict(ref)
    return {}


def _post_kind_event_type(post_kind: str) -> str:
    normalized = str(post_kind or "").strip().lower()
    return {
        "analysis": "progress_update",
        "evidence": "evidence_added",
        "challenge": "challenge_raised",
        "summary": "summary_posted",
        "verdict": "result_submitted",
    }.get(normalized, "progress_update")
