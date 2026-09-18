from __future__ import annotations

from storage.knowledge_index import swarm_knowledge_index
from storage.knowledge_manifests import all_manifests
from storage.replica_table import holders_for_shard


def desired_replication_count() -> int:
    return 2


def replication_gap(shard_id: str) -> int:
    active_holders = len(holders_for_shard(shard_id, active_only=True))
    if active_holders:
        return max(0, desired_replication_count() - active_holders)
    for item in swarm_knowledge_index():
        if item["shard_id"] == shard_id:
            return max(0, desired_replication_count() - int(item["replication_count"]))
    return desired_replication_count()


def under_replicated_shards(limit: int = 100) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for manifest in all_manifests(limit=limit):
        gap = replication_gap(str(manifest["shard_id"]))
        if gap <= 0:
            continue
        out.append(
            {
                "shard_id": str(manifest["shard_id"]),
                "content_hash": str(manifest["content_hash"]),
                "version": int(manifest["version"]),
                "replication_gap": gap,
            }
        )
    out.sort(key=lambda item: int(item["replication_gap"]), reverse=True)
    return out
