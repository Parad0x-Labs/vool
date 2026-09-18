from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass


@dataclass
class DHTNode:
    peer_id: str
    ip: str
    port: int
    last_seen: float
    endpoint_source: str = "observed"


@dataclass(frozen=True)
class DHTRefreshTarget:
    bucket_index: int
    target_id: str
    node_count: int
    last_touched: float
    age_seconds: float


class RoutingTable:
    def __init__(self, local_peer_id: str, k_bucket_size: int = 20, bucket_count: int = 256):
        self.local_peer_id = local_peer_id
        self.local_peer_int = self._peer_int(local_peer_id)
        self._peer_id_width = max(64, len(str(local_peer_id or "").strip()))
        self.k_bucket_size = max(1, int(k_bucket_size))
        self.bucket_count = max(16, int(bucket_count))
        self._buckets: list[OrderedDict[str, DHTNode]] = [OrderedDict() for _ in range(self.bucket_count)]
        self._replacement_caches: list[OrderedDict[str, DHTNode]] = [OrderedDict() for _ in range(self.bucket_count)]
        self._bucket_touched_at: list[float] = [0.0 for _ in range(self.bucket_count)]
        self._stale_node_age_seconds = 3600.0
        # compatibility surface for existing callers
        self.nodes: dict[str, DHTNode] = {}

    def _distance(self, id1: str, id2: str) -> int:
        return self._peer_int(id1) ^ self._peer_int(id2)

    def _peer_int(self, peer_id: str) -> int:
        try:
            return int(peer_id, 16)
        except Exception:
            digest = hashlib.sha256(peer_id.encode("utf-8")).hexdigest()
            return int(digest, 16)

    def _bucket_index(self, peer_id: str) -> int | None:
        if peer_id == self.local_peer_id:
            return None
        distance = self.local_peer_int ^ self._peer_int(peer_id)
        if distance <= 0:
            return None
        idx = distance.bit_length() - 1
        if idx < 0:
            return None
        return min(idx, self.bucket_count - 1)

    def _endpoint_source_priority(self, source: str) -> int:
        normalized = str(source or "").strip().lower()
        priorities = {
            "self": 500,
            "api": 450,
            "observed": 400,
            "bootstrap": 300,
            "advertised": 200,
            "dht": 100,
            "block_found": 90,
        }
        return priorities.get(normalized, 0)

    def _is_verified_endpoint_source(self, source: str) -> bool:
        return str(source or "").strip().lower() in {"self", "api", "observed", "bootstrap"}

    def add_node(self, peer_id: str, ip: str, port: int, *, source: str = "observed") -> None:
        bucket_index = self._bucket_index(peer_id)
        if bucket_index is None:
            return
        now = time.time()
        source_priority = self._endpoint_source_priority(source)
        source_is_verified = self._is_verified_endpoint_source(source)
        source_last_seen = now if source_is_verified else 0.0
        bucket = self._buckets[bucket_index]
        replacements = self._replacement_caches[bucket_index]

        existing = self.nodes.get(peer_id)
        if existing is not None:
            same_endpoint = existing.ip == ip and int(existing.port) == int(port)
            existing_priority = self._endpoint_source_priority(existing.endpoint_source)
            refresh_liveness = False
            reorder_bucket = False
            if same_endpoint:
                if source_priority > existing_priority:
                    existing.endpoint_source = source
                    reorder_bucket = True
                if source_is_verified:
                    existing.last_seen = now
                    refresh_liveness = True
                    reorder_bucket = True
            elif source_priority >= existing_priority:
                existing.ip = ip
                existing.port = int(port)
                existing.endpoint_source = source
                existing.last_seen = source_last_seen
                refresh_liveness = source_is_verified
                reorder_bucket = True
            if peer_id in bucket:
                if reorder_bucket:
                    bucket.move_to_end(peer_id, last=True)
            else:
                bucket[peer_id] = existing
            replacements.pop(peer_id, None)
            if refresh_liveness:
                self._bucket_touched_at[bucket_index] = now
            return

        node = DHTNode(peer_id=peer_id, ip=ip, port=int(port), last_seen=source_last_seen, endpoint_source=source)
        if peer_id in replacements:
            cached = replacements[peer_id]
            same_endpoint = cached.ip == ip and int(cached.port) == int(port)
            cached_priority = self._endpoint_source_priority(cached.endpoint_source)
            if same_endpoint:
                if source_priority > cached_priority:
                    cached.endpoint_source = source
            elif source_priority >= cached_priority:
                cached.ip = ip
                cached.port = int(port)
                cached.endpoint_source = source
            if source_is_verified:
                cached.last_seen = now
            replacements.move_to_end(peer_id, last=True)
        elif len(bucket) >= self.k_bucket_size:
            oldest_peer_id, oldest_node = next(iter(bucket.items()))
            if self._node_is_stale(oldest_node, now=now):
                bucket.pop(oldest_peer_id, None)
                self.nodes.pop(oldest_peer_id, None)
                bucket[peer_id] = node
                self.nodes[peer_id] = node
            else:
                replacements[peer_id] = node
                while len(replacements) > self.k_bucket_size:
                    replacements.popitem(last=False)
        else:
            bucket[peer_id] = node
            self.nodes[peer_id] = node
        if peer_id in bucket:
            replacements.pop(peer_id, None)
        if source_is_verified:
            self._bucket_touched_at[bucket_index] = now

    def remove_node(self, peer_id: str) -> None:
        node = self.nodes.pop(peer_id, None)
        if node is None:
            return
        bucket_index = self._bucket_index(peer_id)
        if bucket_index is None:
            return
        self._buckets[bucket_index].pop(peer_id, None)
        self._promote_replacement(bucket_index, now=time.time())
        self._bucket_touched_at[bucket_index] = time.time()

    def find_closest_peers(self, target_id: str, count: int = 20, *, verified_only: bool = False) -> list[DHTNode]:
        """
        Returns up to 'count' closest peers to 'target_id' according to XOR metric.
        """
        if not self.nodes:
            return []

        distances = []
        for node in self.nodes.values():
            if verified_only and not self._is_verified_endpoint_source(node.endpoint_source):
                continue
            dist = self._distance(target_id, node.peer_id)
            distances.append((dist, node))

        distances.sort(key=lambda x: x[0])

        # Return top N nodes
        return [item[1] for item in distances[: max(1, int(count))]]

    def find_lookup_candidates(
        self,
        target_id: str,
        *,
        count: int = 20,
        exclude_peer_ids: set[str] | None = None,
        now: float | None = None,
        max_age_seconds: float | None = None,
    ) -> list[DHTNode]:
        excluded = {str(item).strip() for item in set(exclude_peer_ids or set()) if str(item).strip()}
        current_time = float(time.time() if now is None else now)
        stale_limit = float(self._stale_node_age_seconds if max_age_seconds is None else max_age_seconds)
        ranked: list[tuple[int, int, DHTNode]] = []
        for node in self.nodes.values():
            if node.peer_id in excluded:
                continue
            is_stale = self._node_is_stale(node, now=current_time, max_age_seconds=stale_limit)
            ranked.append((1 if is_stale else 0, self._distance(target_id, node.peer_id), node))
        ranked.sort(key=lambda item: (item[0], item[1], item[2].peer_id))
        return [item[2] for item in ranked[: max(1, int(count))]]

    def refresh_targets(
        self,
        *,
        max_age_seconds: float = 900.0,
        limit: int | None = None,
        now: float | None = None,
    ) -> list[DHTRefreshTarget]:
        current_time = float(time.time() if now is None else now)
        stale_targets: list[DHTRefreshTarget] = []
        for bucket_index, bucket in enumerate(self._buckets):
            if not bucket:
                continue
            last_touched = float(self._bucket_touched_at[bucket_index] or 0.0)
            age_seconds = current_time - last_touched if last_touched > 0.0 else current_time
            if age_seconds < float(max_age_seconds):
                continue
            stale_targets.append(
                DHTRefreshTarget(
                    bucket_index=bucket_index,
                    target_id=self._refresh_target_id(bucket_index),
                    node_count=len(bucket),
                    last_touched=last_touched,
                    age_seconds=age_seconds,
                )
            )
        stale_targets.sort(key=lambda item: (-item.age_seconds, item.bucket_index))
        if limit is not None:
            stale_targets = stale_targets[: max(0, int(limit))]
        return stale_targets

    def get_all_nodes(self) -> list[DHTNode]:
        return list(self.nodes.values())

    def prune_stale_nodes(self, *, max_age_seconds: float = 3600.0) -> int:
        now = time.time()
        self._stale_node_age_seconds = max(1.0, float(max_age_seconds))
        stale_peer_ids = [
            peer_id
            for peer_id, node in self.nodes.items()
            if (now - float(node.last_seen)) > float(max_age_seconds)
        ]
        for peer_id in stale_peer_ids:
            self.remove_node(peer_id)
        return len(stale_peer_ids)

    def _refresh_target_id(self, bucket_index: int) -> str:
        lower = 1 << max(0, int(bucket_index))
        upper = (1 << (max(0, int(bucket_index)) + 1)) - 1
        midpoint_distance = lower + ((upper - lower) // 2)
        mask = (1 << (self._peer_id_width * 4)) - 1
        target_int = (self.local_peer_int ^ midpoint_distance) & mask
        return f"{target_int:0{self._peer_id_width}x}"

    def _node_is_stale(self, node: DHTNode, *, now: float, max_age_seconds: float | None = None) -> bool:
        stale_limit = float(self._stale_node_age_seconds if max_age_seconds is None else max_age_seconds)
        return (now - float(node.last_seen)) > stale_limit

    def _promote_replacement(self, bucket_index: int, *, now: float) -> None:
        bucket = self._buckets[bucket_index]
        replacements = self._replacement_caches[bucket_index]
        while replacements and len(bucket) < self.k_bucket_size:
            peer_id, node = replacements.popitem(last=False)
            node.last_seen = now
            bucket[peer_id] = node
            self.nodes[peer_id] = node

_table: RoutingTable | None = None

def get_routing_table() -> RoutingTable:
    global _table
    if _table is None:
        from network.signer import get_local_peer_id
        _table = RoutingTable(get_local_peer_id())
    return _table
