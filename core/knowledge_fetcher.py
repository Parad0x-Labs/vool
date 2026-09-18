from __future__ import annotations

from typing import Any

from core.knowledge_registry import find_relevant_remote_shards, holders_for_fetch, search_swarm_memory_metadata
from retrieval.swarm_query import request_specific_shard


def request_best_holder(shard_id: str, *, query_id: str) -> dict[str, Any] | None:
    holders = holders_for_fetch(shard_id)
    if not holders:
        return None
    best = holders[0]
    ok = request_specific_shard(
        peer_id=best["holder_peer_id"],
        query_id=query_id,
        shard_id=shard_id,
    )
    return {"ok": ok, "holder_peer_id": best["holder_peer_id"], "fetch_route": best["fetch_route"]}


def request_relevant_holders(problem_class: str, summary: str, *, query_id: str, limit: int = 3) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in find_relevant_remote_shards(problem_class, summary, limit=limit):
        ok = request_specific_shard(
            peer_id=item["holder_peer_id"],
            query_id=query_id,
            shard_id=item["shard_id"],
        )
        out.append({**item, "ok": ok})
    return out


def consult_relevant_swarm_metadata(
    problem_class: str,
    summary: str,
    *,
    query_id: str | None = None,
    limit: int = 3,
    allow_fetch: bool = False,
    fetch_threshold: float = 2.0,
) -> dict[str, Any]:
    """
    Consult metadata-first canonical swarm memory.

    This path is intentionally separate from model candidate caching and will
    not fetch full remote payloads unless explicitly allowed.
    """
    items = search_swarm_memory_metadata(problem_class, summary, limit=limit)
    fetched = 0
    if allow_fetch:
        for item in items:
            if float(item.get("relevance_score") or 0.0) < fetch_threshold:
                continue
            ok = request_specific_shard(
                peer_id=item["holder_peer_id"],
                query_id=query_id or str(item["shard_id"]),
                shard_id=item["shard_id"],
            )
            if ok:
                fetched += 1
            item["fetched"] = bool(ok)
    return {
        "consulted": True,
        "items": items,
        "fetched": fetched,
        "metadata_only": not allow_fetch,
    }
