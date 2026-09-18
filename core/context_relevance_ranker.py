from __future__ import annotations

from datetime import datetime, timezone

from core.prompt_assembly_report import ContextItem, normalize_context_score


def _tokenize(text: str) -> set[str]:
    return {
        token
        for token in "".join(ch if ch.isalnum() else " " for ch in (text or "").lower()).split()
        if len(token) >= 3
    }


def _semantic_score(query_text: str, item: ContextItem, topic_hints: list[str]) -> float:
    query_tokens = _tokenize(query_text) | {token for hint in topic_hints for token in _tokenize(hint)}
    if not query_tokens:
        return 0.0
    item_tokens = _tokenize(item.title) | _tokenize(item.content)
    if not item_tokens:
        return 0.0
    overlap = len(query_tokens & item_tokens)
    return overlap / max(1, len(query_tokens))


def _recency_score(item: ContextItem) -> float:
    timestamp = (
        item.metadata.get("created_at")
        or item.metadata.get("updated_at")
        or item.metadata.get("freshness_ts")
        or item.metadata.get("last_heartbeat_at")
    )
    if not timestamp:
        return 0.4
    try:
        age_days = max(0.0, (datetime.now(timezone.utc) - datetime.fromisoformat(str(timestamp))).total_seconds() / 86400.0)
    except Exception:
        return 0.4
    return max(0.2, 1.0 - min(age_days / 90.0, 0.8))


def _source_weight(task_class: str, source_type: str) -> float:
    weights: dict[str, dict[str, float]] = {
        "system_design": {"local_shard": 1.0, "swarm_metadata": 0.95, "final_response": 0.75, "dialogue_turn": 0.55, "runtime_memory": 0.82, "session_summary": 0.8, "swarm_context": 0.84},
        "security_hardening": {"local_shard": 1.0, "final_response": 0.85, "dialogue_turn": 0.55, "payment_status": 0.35},
        "research": {"local_shard": 0.85, "swarm_metadata": 0.8, "final_response": 0.75, "dialogue_turn": 0.5, "runtime_memory": 0.74, "session_summary": 0.68, "swarm_context": 0.76},
        "dependency_resolution": {"local_shard": 0.95, "final_response": 0.7, "dialogue_turn": 0.45},
        "config": {"local_shard": 0.9, "final_response": 0.75, "dialogue_turn": 0.55},
    }
    source_defaults = {
        "local_shard": 0.85,
        "dialogue_turn": 0.6,
        "final_response": 0.7,
        "swarm_metadata": 0.65,
        "payment_status": 0.4,
        "shorthand": 0.5,
        "cold_archive": 0.35,
        "runtime_memory": 0.8,
        "session_summary": 0.72,
        "swarm_context": 0.74,
    }
    return weights.get(task_class, {}).get(source_type, source_defaults.get(source_type, 0.45))


def rank_context_items(
    items: list[ContextItem],
    *,
    query_text: str,
    topic_hints: list[str],
    task_class: str,
) -> list[ContextItem]:
    ranked: list[ContextItem] = []
    for item in items:
        semantic = _semantic_score(query_text, item, topic_hints)
        recency = _recency_score(item)
        source_weight = _source_weight(task_class, item.source_type)
        confidence = normalize_context_score(item.confidence)
        priority = normalize_context_score(
            0.45 * semantic
            + 0.20 * recency
            + 0.20 * source_weight
            + 0.15 * confidence
        )
        ranked.append(
            ContextItem(
                item_id=item.item_id,
                layer=item.layer,
                source_type=item.source_type,
                title=item.title,
                content=item.content,
                priority=priority,
                confidence=confidence,
                must_keep=item.must_keep,
                include_reason=item.include_reason,
                metadata={**item.metadata, "semantic_score": round(semantic, 4), "recency_score": round(recency, 4)},
                provenance=dict(item.provenance),
            )
        )
    ranked.sort(key=lambda item: (item.priority, item.confidence), reverse=True)
    return ranked


def retrieval_confidence(items: list[ContextItem]) -> tuple[str, float]:
    if not items:
        return "low", 0.0
    top = normalize_context_score(items[0].priority)
    if top >= 0.72:
        return "high", top
    if top >= 0.48:
        return "medium", top
    return "low", top
