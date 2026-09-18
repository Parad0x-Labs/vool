from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from network.assist_models import CapabilityAd
from network.protocol import message_proof_hash
from storage.db import get_connection

_VERIFIED_ENDPOINT_SOURCES = {"self", "api", "observed", "bootstrap"}
_ENDPOINT_SOURCE_PRIORITIES = {
    "self": 500,
    "api": 450,
    "observed": 400,
    "bootstrap": 300,
    "advertised": 200,
    "dht": 100,
    "block_found": 90,
}


@dataclass
class HelperCandidate:
    peer_id: str
    status: str
    trust_score: float
    capacity: int
    capability_match_score: float
    freshness_score: float
    total_score: float
    host_group_hint_hash: str | None = None


@dataclass(frozen=True)
class PeerEndpointCandidate:
    peer_id: str
    host: str
    port: int
    source: str
    last_seen_at: str
    last_probe_attempt_at: str = ""
    last_probe_delivery_ok: bool = False
    consecutive_probe_failures: int = 0


@dataclass(frozen=True)
class VerifiedPeerEndpoint:
    peer_id: str
    host: str
    port: int
    source: str
    last_seen_at: str
    last_verified_at: str = ""
    verification_kind: str = ""
    proof_count: int = 0
    proof_message_id: str = ""
    proof_message_type: str = ""
    proof_hash: str = ""
    proof_timestamp: str = ""
    last_delivery_attempt_at: str = ""
    last_delivery_success_at: str = ""
    last_delivery_failure_at: str = ""
    consecutive_delivery_failures: int = 0


@dataclass(frozen=True)
class PeerDeliveryTarget:
    peer_id: str
    host: str
    port: int
    source: str
    verified: bool


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _is_recent(dt: datetime | None, *, window: timedelta) -> bool:
    if dt is None:
        return False
    return dt >= (datetime.now(timezone.utc) - window)


_RECENT_DELIVERY_SUCCESS_WINDOW = timedelta(hours=6)
_RECENT_SIGNED_OBSERVED_WINDOW = timedelta(hours=2)
_RECENT_SIGNED_DECLARATION_WINDOW = timedelta(hours=24)


def _endpoint_source_priority(value: str) -> int:
    normalized = str(value or "").strip().lower()
    return _ENDPOINT_SOURCE_PRIORITIES.get(normalized, 0)


def _is_verified_endpoint_source(value: str) -> bool:
    return str(value or "").strip().lower() in _VERIFIED_ENDPOINT_SOURCES


def _liveness_tier(endpoint: VerifiedPeerEndpoint) -> int:
    verification_kind = str(endpoint.verification_kind or "").strip().lower()
    source = str(endpoint.source or "").strip().lower()
    proof_dt = _parse_dt(endpoint.proof_timestamp) or _parse_dt(endpoint.last_verified_at) or _parse_dt(endpoint.last_seen_at)
    success_dt = _parse_dt(endpoint.last_delivery_success_at)
    if _is_recent(success_dt, window=_RECENT_DELIVERY_SUCCESS_WINDOW):
        return 4
    if verification_kind == "protocol_signature" and source == "observed" and _is_recent(
        proof_dt,
        window=_RECENT_SIGNED_OBSERVED_WINDOW,
    ):
        return 3
    if verification_kind in {"signed_api_write", "bootstrap_snapshot"} and _is_recent(
        proof_dt,
        window=_RECENT_SIGNED_DECLARATION_WINDOW,
    ):
        return 2
    if source == "self":
        return 1
    return 0


def _verified_endpoint_sort_key(endpoint: VerifiedPeerEndpoint) -> tuple[int, float, int, float, int, int, float]:
    verified_dt = _parse_dt(endpoint.last_verified_at) or _parse_dt(endpoint.last_seen_at)
    seen_dt = _parse_dt(endpoint.last_seen_at)
    success_dt = _parse_dt(endpoint.last_delivery_success_at)
    verified_ts = verified_dt.timestamp() if verified_dt is not None else 0.0
    seen_ts = seen_dt.timestamp() if seen_dt is not None else 0.0
    success_ts = success_dt.timestamp() if success_dt is not None else 0.0
    return (
        _liveness_tier(endpoint),
        success_ts,
        _endpoint_source_priority(endpoint.source),
        verified_ts,
        -int(endpoint.consecutive_delivery_failures or 0),
        int(endpoint.proof_count or 0),
        seen_ts,
    )


def _delivery_liveness_priority(endpoint: VerifiedPeerEndpoint) -> int:
    proof_dt = _parse_dt(endpoint.proof_timestamp) or _parse_dt(endpoint.last_verified_at) or _parse_dt(endpoint.last_seen_at)
    success_dt = _parse_dt(endpoint.last_delivery_success_at)
    if _is_recent(success_dt, window=_RECENT_DELIVERY_SUCCESS_WINDOW):
        return 3
    if (
        endpoint.source == "observed"
        and endpoint.verification_kind == "protocol_signature"
        and _is_recent(proof_dt, window=_RECENT_SIGNED_OBSERVED_WINDOW)
    ):
        return 2
    if endpoint.source == "self":
        return 1
    return 0


def _delivery_target_sort_key(endpoint: VerifiedPeerEndpoint) -> tuple[int, float, int, int, float, int, float]:
    success_dt = _parse_dt(endpoint.last_delivery_success_at)
    verified_dt = _parse_dt(endpoint.last_verified_at) or _parse_dt(endpoint.last_seen_at)
    seen_dt = _parse_dt(endpoint.last_seen_at)
    success_ts = success_dt.timestamp() if success_dt is not None else 0.0
    verified_ts = verified_dt.timestamp() if verified_dt is not None else 0.0
    seen_ts = seen_dt.timestamp() if seen_dt is not None else 0.0
    return (
        _delivery_liveness_priority(endpoint),
        success_ts,
        -int(endpoint.consecutive_delivery_failures or 0),
        _endpoint_source_priority(endpoint.source),
        verified_ts,
        int(endpoint.proof_count or 0),
        seen_ts,
    )


def _row_value(row: Any, key: str, default: Any = "") -> Any:
    try:
        value = row[key]
    except Exception:
        return default
    return default if value is None else value


def _verified_endpoint_from_row(row: Any) -> VerifiedPeerEndpoint:
    return VerifiedPeerEndpoint(
        peer_id=str(_row_value(row, "peer_id")),
        host=str(_row_value(row, "host")),
        port=int(_row_value(row, "port", 0) or 0),
        source=str(_row_value(row, "source", "observed")),
        last_seen_at=str(_row_value(row, "last_seen_at", "")),
        last_verified_at=str(_row_value(row, "last_verified_at", "")),
        verification_kind=str(_row_value(row, "verification_kind", "")),
        proof_count=int(_row_value(row, "proof_count", 0) or 0),
        proof_message_id=str(_row_value(row, "proof_message_id", "")),
        proof_message_type=str(_row_value(row, "proof_message_type", "")),
        proof_hash=str(_row_value(row, "proof_hash", "")),
        proof_timestamp=str(_row_value(row, "proof_timestamp", "")),
        last_delivery_attempt_at=str(_row_value(row, "last_delivery_attempt_at", "")),
        last_delivery_success_at=str(_row_value(row, "last_delivery_success_at", "")),
        last_delivery_failure_at=str(_row_value(row, "last_delivery_failure_at", "")),
        consecutive_delivery_failures=int(_row_value(row, "consecutive_delivery_failures", 0) or 0),
    )


def _observation_endpoint_from_row(row: Any) -> VerifiedPeerEndpoint:
    verified_at = str(_row_value(row, "last_verified_at", ""))
    return VerifiedPeerEndpoint(
        peer_id=str(_row_value(row, "peer_id")),
        host=str(_row_value(row, "host")),
        port=int(_row_value(row, "port", 0) or 0),
        source=str(_row_value(row, "source", "observed")),
        last_seen_at=verified_at,
        last_verified_at=verified_at,
        verification_kind=str(_row_value(row, "verification_kind", "")),
        proof_count=int(_row_value(row, "proof_count", 0) or 0),
        proof_message_id=str(_row_value(row, "proof_message_id", "")),
        proof_message_type=str(_row_value(row, "proof_message_type", "")),
        proof_hash=str(_row_value(row, "proof_hash", "")),
        proof_timestamp=str(_row_value(row, "proof_timestamp", "")),
        last_delivery_attempt_at="",
        last_delivery_success_at="",
        last_delivery_failure_at="",
        consecutive_delivery_failures=0,
    )


def _load_verified_endpoint_row(conn: Any, *, peer_id: str, host: str, port: int) -> VerifiedPeerEndpoint | None:
    row = conn.execute(
        """
        SELECT peer_id, host, port, source, last_seen_at, last_verified_at,
               verification_kind, proof_count, proof_message_id, proof_message_type, proof_hash, proof_timestamp,
               last_delivery_attempt_at, last_delivery_success_at, last_delivery_failure_at,
               consecutive_delivery_failures
        FROM peer_endpoints
        WHERE peer_id = ? AND host = ? AND port = ?
        LIMIT 1
        """,
        (peer_id, str(host), int(port)),
    ).fetchone()
    return _verified_endpoint_from_row(row) if row else None


def _max_timestamp(left: str, right: str) -> str:
    left_dt = _parse_dt(left)
    right_dt = _parse_dt(right)
    if left_dt is None:
        return right
    if right_dt is None:
        return left
    return right if right_dt >= left_dt else left


def _merge_verified_endpoint(
    existing: VerifiedPeerEndpoint | None,
    incoming: VerifiedPeerEndpoint,
) -> VerifiedPeerEndpoint:
    if existing is None:
        return incoming

    incoming_reference_dt = _parse_dt(incoming.proof_timestamp or incoming.last_verified_at or incoming.last_seen_at or "")
    existing_reference_dt = _parse_dt(existing.proof_timestamp or existing.last_verified_at or existing.last_seen_at or "")
    incoming_more_proven = (
        int(incoming.proof_count or 0) > int(existing.proof_count or 0)
        or (incoming_reference_dt is not None and (existing_reference_dt is None or incoming_reference_dt >= existing_reference_dt))
    )
    selected_source = existing.source
    if incoming_more_proven and incoming.verification_kind == "protocol_signature" and incoming.source == "observed":
        selected_source = incoming.source
    elif (not incoming_more_proven) and existing.verification_kind == "protocol_signature" and existing.source == "observed":
        selected_source = existing.source
    elif _endpoint_source_priority(incoming.source) >= _endpoint_source_priority(existing.source):
        selected_source = incoming.source

    selected_last_verified_at = _max_timestamp(existing.last_verified_at, incoming.last_verified_at)
    selected_proof_timestamp = _max_timestamp(existing.proof_timestamp, incoming.proof_timestamp)
    selected_proof_count = max(int(existing.proof_count or 0), int(incoming.proof_count or 0))

    selected_verification_kind = existing.verification_kind
    selected_proof_message_id = existing.proof_message_id
    selected_proof_message_type = existing.proof_message_type
    selected_proof_hash = existing.proof_hash
    if incoming_more_proven:
        if incoming.verification_kind:
            selected_verification_kind = incoming.verification_kind
        if incoming.proof_message_id:
            selected_proof_message_id = incoming.proof_message_id
        if incoming.proof_message_type:
            selected_proof_message_type = incoming.proof_message_type
        if incoming.proof_hash:
            selected_proof_hash = incoming.proof_hash

    return VerifiedPeerEndpoint(
        peer_id=existing.peer_id,
        host=existing.host,
        port=int(existing.port),
        source=selected_source,
        last_seen_at=_max_timestamp(existing.last_seen_at, incoming.last_seen_at),
        last_verified_at=selected_last_verified_at,
        verification_kind=selected_verification_kind,
        proof_count=selected_proof_count,
        proof_message_id=selected_proof_message_id,
        proof_message_type=selected_proof_message_type,
        proof_hash=selected_proof_hash,
        proof_timestamp=selected_proof_timestamp,
        last_delivery_attempt_at=_max_timestamp(existing.last_delivery_attempt_at, incoming.last_delivery_attempt_at),
        last_delivery_success_at=_max_timestamp(existing.last_delivery_success_at, incoming.last_delivery_success_at),
        last_delivery_failure_at=_max_timestamp(existing.last_delivery_failure_at, incoming.last_delivery_failure_at),
        consecutive_delivery_failures=min(
            int(existing.consecutive_delivery_failures or 0),
            int(incoming.consecutive_delivery_failures or 0),
        )
        if incoming.last_delivery_success_at
        else max(
            int(existing.consecutive_delivery_failures or 0),
            int(incoming.consecutive_delivery_failures or 0),
        ),
    )


def _upsert_verified_endpoint_row(conn: Any, endpoint: VerifiedPeerEndpoint) -> None:
    current = _load_verified_endpoint_row(conn, peer_id=endpoint.peer_id, host=endpoint.host, port=int(endpoint.port))
    merged = _merge_verified_endpoint(current, endpoint)
    conn.execute(
        """
        INSERT OR REPLACE INTO peer_endpoints (
            peer_id, host, port, source, last_seen_at, last_verified_at,
            verification_kind, proof_count, proof_message_id, proof_message_type, proof_hash, proof_timestamp,
            last_delivery_attempt_at, last_delivery_success_at, last_delivery_failure_at,
            consecutive_delivery_failures, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            merged.peer_id,
            merged.host,
            int(merged.port),
            merged.source,
            merged.last_seen_at or _utcnow(),
            merged.last_verified_at or "",
            merged.verification_kind or "",
            int(merged.proof_count or 0),
            merged.proof_message_id or "",
            merged.proof_message_type or "",
            merged.proof_hash or "",
            merged.proof_timestamp or "",
            merged.last_delivery_attempt_at or "",
            merged.last_delivery_success_at or "",
            merged.last_delivery_failure_at or "",
            int(merged.consecutive_delivery_failures or 0),
            _utcnow(),
        ),
    )


def _upsert_verified_endpoint_proof(
    conn: Any,
    *,
    peer_id: str,
    host: str,
    port: int,
    source: str,
    verification_kind: str,
    proof_message_id: str,
    proof_message_type: str,
    proof_hash: str,
    proof_signature: str,
    proof_timestamp: str,
) -> None:
    now = _utcnow()
    conn.execute(
        """
        INSERT INTO peer_endpoint_observations (
            peer_id, host, port, source, verification_kind,
            proof_message_id, proof_message_type, proof_hash, proof_signature, proof_timestamp,
            first_verified_at, last_verified_at, proof_count, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        ON CONFLICT(peer_id, host, port, source) DO UPDATE SET
            verification_kind = excluded.verification_kind,
            proof_message_id = excluded.proof_message_id,
            proof_message_type = excluded.proof_message_type,
            proof_hash = excluded.proof_hash,
            proof_signature = excluded.proof_signature,
            proof_timestamp = excluded.proof_timestamp,
            last_verified_at = excluded.last_verified_at,
            proof_count = peer_endpoint_observations.proof_count + 1,
            updated_at = excluded.updated_at
        """,
        (
            peer_id,
            str(host),
            int(port),
            source,
            verification_kind,
            proof_message_id,
            proof_message_type,
            proof_hash,
            proof_signature,
            proof_timestamp,
            now,
            now,
            now,
        ),
    )
    conn.execute(
        """
        DELETE FROM peer_endpoint_candidates
        WHERE peer_id = ? AND host = ? AND port = ?
        """,
        (peer_id, str(host), int(port)),
    )
    row = conn.execute(
        """
        SELECT peer_id, host, port, source, verification_kind,
               proof_message_id, proof_message_type, proof_hash, proof_timestamp,
               last_verified_at, proof_count
        FROM peer_endpoint_observations
        WHERE peer_id = ? AND host = ? AND port = ? AND source = ?
        LIMIT 1
        """,
        (peer_id, str(host), int(port), source),
    ).fetchone()
    if row is not None:
        _upsert_verified_endpoint_row(conn, _observation_endpoint_from_row(row))


def upsert_peer_minimal(peer_id: str) -> None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT 1 FROM peers WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()

        if not row:
            now = _utcnow()
            conn.execute(
                """
                INSERT INTO peers (
                    peer_id, display_alias, trust_score, successful_shards, failed_shards,
                    strike_count, status, last_seen_at, created_at, updated_at
                ) VALUES (?, ?, 0.5, 0, 0, 0, 'active', ?, ?, ?)
                """,
                (peer_id, None, now, now, now),
            )
        else:
            conn.execute(
                "UPDATE peers SET last_seen_at = ?, updated_at = ? WHERE peer_id = ?",
                (_utcnow(), _utcnow(), peer_id),
            )

        conn.commit()
    finally:
        conn.close()


def register_capability_ad(ad: CapabilityAd) -> None:
    upsert_peer_minimal(ad.agent_id)

    # Trust is owned by the local reputation engine (peers.trust_score), NOT by
    # the peer advertising itself. The routing-read trust_score column is seeded
    # from our locally-held trust for this peer; the peer's self-declared value
    # is retained only in self_reported_trust, which no routing/abuse gate reads.
    # This stops a peer from self-promoting (trust=1.0 for preferential routing)
    # or defaming a victim (trust=0.0) via the advertised value.
    routing_trust = peer_trust(ad.agent_id)

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO agent_capabilities (
                peer_id, status, capabilities_json, compute_class, supported_models_json, capacity, trust_score,
                self_reported_trust, assist_filters_json, host_group_hint_hash,
                last_seen_at, created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                COALESCE((SELECT created_at FROM agent_capabilities WHERE peer_id = ?), ?),
                ?
            )
            """,
            (
                ad.agent_id,
                ad.status,
                json.dumps(ad.capabilities, sort_keys=True),
                ad.compute_class,
                json.dumps(ad.supported_models, sort_keys=True),
                ad.capacity,
                routing_trust,
                ad.trust_score,
                json.dumps(ad.assist_filters.model_dump(), sort_keys=True),
                ad.assist_filters.host_group_hint_hash,
                ad.timestamp.isoformat(),
                ad.agent_id,
                _utcnow(),
                _utcnow(),
            ),
        )

        # Touch liveness only. The peer does NOT get to set its own trust_score
        # here — that is recomputed solely by the reputation engine from
        # observed behaviour (successful/failed shards, strikes).
        conn.execute(
            """
            UPDATE peers
            SET last_seen_at = ?,
                updated_at = ?
            WHERE peer_id = ?
            """,
            (ad.timestamp.isoformat(), _utcnow(), ad.agent_id),
        )

        conn.commit()
    finally:
        conn.close()


def record_bootstrap_presence(
    *,
    peer_id: str,
    status: str,
    capabilities: list[str],
    capacity: int,
    trust_score: float,
    host_group_hint_hash: str | None = None,
) -> None:
    upsert_peer_minimal(peer_id)

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO agent_capabilities (
                peer_id, status, capabilities_json, compute_class, supported_models_json, capacity, trust_score,
                self_reported_trust, assist_filters_json, host_group_hint_hash,
                last_seen_at, created_at, updated_at
            ) VALUES (
                ?, ?, ?, 'cpu_basic', '[]', ?, ?,
                COALESCE((SELECT self_reported_trust FROM agent_capabilities WHERE peer_id = ?), 0.5),
                '{}', ?, ?,
                COALESCE((SELECT created_at FROM agent_capabilities WHERE peer_id = ?), ?),
                ?
            )
            """,
            (
                peer_id,
                status,
                json.dumps(capabilities, sort_keys=True),
                capacity,
                trust_score,
                peer_id,
                host_group_hint_hash,
                _utcnow(),
                peer_id,
                _utcnow(),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def peer_trust(peer_id: str) -> float:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT trust_score FROM peers WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()
        return float(row["trust_score"]) if row else 0.50
    finally:
        conn.close()


def host_group_hint_for_peer(peer_id: str) -> str | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT host_group_hint_hash FROM agent_capabilities WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()
        if not row or not row["host_group_hint_hash"]:
            return None
        return str(row["host_group_hint_hash"])
    finally:
        conn.close()


def same_host_group_suspect(local_host_group_hint_hash: str | None, remote_peer_id: str) -> bool:
    if not local_host_group_hint_hash:
        return False
    remote = host_group_hint_for_peer(remote_peer_id)
    return bool(remote and remote == local_host_group_hint_hash)


def _freshness_score(last_seen_at: str | None) -> float:
    dt = _parse_dt(last_seen_at)
    if not dt:
        return 0.2

    age = datetime.now(timezone.utc) - dt
    if age <= timedelta(minutes=5):
        return 1.0
    if age <= timedelta(minutes=30):
        return 0.8
    if age <= timedelta(hours=2):
        return 0.6
    if age <= timedelta(hours=12):
        return 0.4
    return 0.2


def _capability_match(required_capabilities: list[str], peer_capabilities: list[str]) -> float:
    if not required_capabilities:
        return 1.0
    req = set(required_capabilities)
    got = set(peer_capabilities)
    overlap = len(req & got)
    return overlap / max(1, len(req))


def _status_weight(status: str) -> float:
    return {
        "idle": 1.0,
        "limited": 0.6,
        "busy": 0.3,
        "offline": 0.0,
    }.get(status, 0.0)


def get_best_helpers(
    *,
    required_capabilities: list[str],
    exclude_peer_id: str | None = None,
    min_trust: float = 0.30,
    limit: int = 10,
    trusted_only: bool = False,
    exclude_host_group_hint_hash: str | None = None,
) -> list[HelperCandidate]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT a.peer_id, a.status, a.capabilities_json, a.capacity, a.trust_score,
                   a.host_group_hint_hash, a.last_seen_at
            FROM agent_capabilities a
            WHERE a.capacity > 0
            ORDER BY a.last_seen_at DESC
            LIMIT 500
            """
        ).fetchall()
    finally:
        conn.close()

    out: list[HelperCandidate] = []

    for row in rows:
        peer_id = row["peer_id"]
        if exclude_peer_id and peer_id == exclude_peer_id:
            continue

        trust = float(row["trust_score"])
        if trust < min_trust:
            continue
        if trusted_only and trust < 0.65:
            continue

        if exclude_host_group_hint_hash and row["host_group_hint_hash"] and row["host_group_hint_hash"] == exclude_host_group_hint_hash:
            continue

        status = str(row["status"])
        status_w = _status_weight(status)
        if status_w <= 0.0:
            continue

        try:
            caps = json.loads(row["capabilities_json"] or "[]")
        except Exception:
            caps = []

        cap_match = _capability_match(required_capabilities, caps)
        if cap_match <= 0.0:
            continue

        freshness = _freshness_score(row["last_seen_at"])
        capacity_score = min(1.0, float(row["capacity"]) / 4.0)

        total = (
            (0.35 * cap_match)
            + (0.25 * trust)
            + (0.20 * freshness)
            + (0.10 * status_w)
            + (0.10 * capacity_score)
        )

        out.append(
            HelperCandidate(
                peer_id=peer_id,
                status=status,
                trust_score=trust,
                capacity=int(row["capacity"]),
                capability_match_score=cap_match,
                freshness_score=freshness,
                total_score=max(0.0, min(1.0, total)),
                host_group_hint_hash=row["host_group_hint_hash"],
            )
        )

    out.sort(key=lambda x: x.total_score, reverse=True)
    return out[:limit]


def best_helpers_for_task_offer(offer: dict[str, Any], exclude_host_group_hint_hash: str | None = None) -> list[HelperCandidate]:
    try:
        required = json.loads(offer.get("required_capabilities_json", "[]"))
    except Exception:
        required = []

    return get_best_helpers(
        required_capabilities=required,
        exclude_peer_id=offer.get("parent_peer_id"),
        limit=min(int(offer.get("max_helpers", 1)) * 3, 12),
        exclude_host_group_hint_hash=exclude_host_group_hint_hash,
    )


def prune_stale_capabilities(max_age_hours: int = 24) -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=max_age_hours)).isoformat()

    conn = get_connection()
    try:
        cur = conn.execute(
            """
            DELETE FROM agent_capabilities
            WHERE last_seen_at < ?
            """,
            (cutoff,),
        )
        conn.commit()
        return int(cur.rowcount or 0)
    finally:
        conn.close()


def register_peer_endpoint(peer_id: str, host: str, port: int, source: str = "observed") -> None:
    if not host or port <= 0:
        return
    incoming_source = str(source or "observed").strip().lower() or "observed"
    if not _is_verified_endpoint_source(incoming_source):
        register_peer_endpoint_candidate(peer_id, host, port, source=incoming_source)
        return

    conn = get_connection()
    try:
        incoming_host = str(host)
        incoming_port = int(port)
        _upsert_verified_endpoint_row(
            conn,
            VerifiedPeerEndpoint(
                peer_id=peer_id,
                host=incoming_host,
                port=incoming_port,
                source=incoming_source,
                last_seen_at=_utcnow(),
            ),
        )
        conn.execute(
            """
            DELETE FROM peer_endpoint_candidates
            WHERE peer_id = ? AND host = ? AND port = ?
            """,
            (peer_id, incoming_host, incoming_port),
        )
        conn.commit()
    finally:
        conn.close()


def record_signed_peer_endpoint_observation(
    peer_id: str,
    host: str,
    port: int,
    *,
    envelope: dict[str, Any],
    source: str = "observed",
) -> None:
    if not host or port <= 0:
        return
    normalized_source = str(source or "observed").strip().lower() or "observed"
    if not _is_verified_endpoint_source(normalized_source):
        normalized_source = "observed"
    proof_timestamp = str((envelope or {}).get("timestamp") or "").strip() or _utcnow()
    proof_message_id = str((envelope or {}).get("msg_id") or "").strip()
    proof_message_type = str((envelope or {}).get("msg_type") or "").strip()
    proof_signature = str((envelope or {}).get("signature") or "").strip()
    proof_hash = message_proof_hash(envelope or {})

    conn = get_connection()
    try:
        _upsert_verified_endpoint_proof(
            conn,
            peer_id=peer_id,
            host=str(host),
            port=int(port),
            source=normalized_source,
            verification_kind="protocol_signature",
            proof_message_id=proof_message_id,
            proof_message_type=proof_message_type,
            proof_hash=proof_hash,
            proof_signature=proof_signature,
            proof_timestamp=proof_timestamp,
        )
        conn.commit()
    finally:
        conn.close()


def record_verified_peer_endpoint_proof(
    peer_id: str,
    host: str,
    port: int,
    *,
    source: str,
    verification_kind: str,
    proof_message_id: str = "",
    proof_message_type: str = "",
    proof_hash: str = "",
    proof_signature: str = "",
    proof_timestamp: str = "",
) -> None:
    if not host or port <= 0:
        return
    normalized_source = str(source or "observed").strip().lower() or "observed"
    if not _is_verified_endpoint_source(normalized_source):
        normalized_source = "observed"
    conn = get_connection()
    try:
        _upsert_verified_endpoint_proof(
            conn,
            peer_id=peer_id,
            host=str(host),
            port=int(port),
            source=normalized_source,
            verification_kind=str(verification_kind or "").strip() or "verified_proof",
            proof_message_id=str(proof_message_id or "").strip(),
            proof_message_type=str(proof_message_type or "").strip(),
            proof_hash=str(proof_hash or "").strip(),
            proof_signature=str(proof_signature or "").strip(),
            proof_timestamp=str(proof_timestamp or "").strip() or _utcnow(),
        )
        conn.commit()
    finally:
        conn.close()


def register_peer_endpoint_candidate(peer_id: str, host: str, port: int, source: str = "dht") -> None:
    if not host or port <= 0:
        return
    normalized_source = str(source or "dht").strip().lower() or "dht"
    if _is_verified_endpoint_source(normalized_source):
        register_peer_endpoint(peer_id, host, port, source=normalized_source)
        return

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO peer_endpoint_candidates (
                peer_id, host, port, source, first_seen_at, last_seen_at,
                last_probe_attempt_at, last_probe_delivery_ok, consecutive_probe_failures, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, '', 0, 0, ?)
            ON CONFLICT(peer_id, host, port, source) DO UPDATE SET
                last_seen_at = excluded.last_seen_at,
                updated_at = excluded.updated_at
            """,
            (
                peer_id,
                str(host),
                int(port),
                normalized_source,
                _utcnow(),
                _utcnow(),
                _utcnow(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def endpoint_for_peer(peer_id: str) -> tuple[str, int] | None:
    endpoint = selected_verified_endpoint_for_peer(peer_id)
    if endpoint is None:
        return None
    return str(endpoint.host), int(endpoint.port)


def selected_verified_endpoint_for_peer(peer_id: str) -> VerifiedPeerEndpoint | None:
    endpoints = verified_endpoints_for_peer(peer_id, limit=1)
    return endpoints[0] if endpoints else None


def signed_observed_endpoints_for_peer(peer_id: str, *, limit: int = 8) -> list[VerifiedPeerEndpoint]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT peer_id, host, port, source, verification_kind,
                   proof_message_id, proof_message_type, proof_hash, proof_timestamp,
                   last_verified_at, proof_count
            FROM peer_endpoint_observations
            WHERE peer_id = ?
            ORDER BY last_verified_at DESC, proof_count DESC
            LIMIT ?
            """,
            (peer_id, max(1, int(limit))),
        ).fetchall()
    finally:
        conn.close()

    return [_observation_endpoint_from_row(row) for row in rows]


def _verified_peer_endpoint_rows(
    peer_id: str,
    *,
    limit: int,
) -> list[VerifiedPeerEndpoint]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT peer_id, host, port, source, last_seen_at, last_verified_at,
                   verification_kind, proof_count, proof_message_id, proof_message_type, proof_hash, proof_timestamp,
                   last_delivery_attempt_at, last_delivery_success_at, last_delivery_failure_at,
                   consecutive_delivery_failures
            FROM peer_endpoints
            WHERE peer_id = ?
            ORDER BY updated_at DESC, rowid DESC
            LIMIT ?
            """,
            (peer_id, max(1, int(limit))),
        ).fetchall()
    finally:
        conn.close()
    endpoints = [_verified_endpoint_from_row(row) for row in rows]
    endpoints.sort(key=_verified_endpoint_sort_key, reverse=True)
    return endpoints


def verified_endpoints_for_peer(peer_id: str, *, limit: int = 8) -> list[VerifiedPeerEndpoint]:
    return _verified_peer_endpoint_rows(peer_id, limit=limit)


def note_verified_peer_endpoint_delivery_result(
    peer_id: str,
    host: str,
    port: int,
    *,
    delivered: bool,
) -> None:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT last_seen_at, last_delivery_success_at, consecutive_delivery_failures
                 , last_delivery_failure_at
            FROM peer_endpoints
            WHERE peer_id = ? AND host = ? AND port = ?
            LIMIT 1
            """,
            (peer_id, str(host), int(port)),
        ).fetchone()
        if not row:
            return
        now = _utcnow()
        delivery_failures = 0 if delivered else (int(row["consecutive_delivery_failures"] or 0) + 1)
        conn.execute(
            """
            UPDATE peer_endpoints
            SET last_seen_at = ?,
                last_delivery_attempt_at = ?,
                last_delivery_success_at = ?,
                last_delivery_failure_at = ?,
                consecutive_delivery_failures = ?,
                updated_at = ?
            WHERE peer_id = ? AND host = ? AND port = ?
            """,
            (
                _max_timestamp(str(row["last_seen_at"] or ""), now) if delivered else str(row["last_seen_at"] or ""),
                now,
                now if delivered else str(row["last_delivery_success_at"] or ""),
                str(row["last_delivery_failure_at"] or "") if delivered else now,
                delivery_failures,
                now,
                peer_id,
                str(host),
                int(port),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def candidate_endpoints_for_peer(peer_id: str, *, limit: int = 8) -> list[PeerEndpointCandidate]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT peer_id, host, port, source, last_seen_at,
                   last_probe_attempt_at, last_probe_delivery_ok, consecutive_probe_failures
            FROM peer_endpoint_candidates
            WHERE peer_id = ?
            ORDER BY consecutive_probe_failures ASC, updated_at DESC
            LIMIT ?
            """,
            (peer_id, max(1, int(limit))),
        ).fetchall()
    finally:
        conn.close()

    return [
        PeerEndpointCandidate(
            peer_id=str(row["peer_id"]),
            host=str(row["host"]),
            port=int(row["port"]),
            source=str(row["source"]),
            last_seen_at=str(row["last_seen_at"]),
            last_probe_attempt_at=str(row["last_probe_attempt_at"] or ""),
            last_probe_delivery_ok=bool(row["last_probe_delivery_ok"]),
            consecutive_probe_failures=int(row["consecutive_probe_failures"] or 0),
        )
        for row in rows
    ]


def recent_peer_endpoint_candidates(
    *,
    exclude_peer_id: str | None = None,
    limit: int = 32,
    cooldown_seconds: int = 0,
    max_consecutive_failures: int | None = None,
) -> list[PeerEndpointCandidate]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT peer_id, host, port, source, last_seen_at,
                   last_probe_attempt_at, last_probe_delivery_ok, consecutive_probe_failures
            FROM peer_endpoint_candidates
            ORDER BY consecutive_probe_failures ASC, updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)) * 4,),
        ).fetchall()
    finally:
        conn.close()

    out: list[PeerEndpointCandidate] = []
    now = datetime.now(timezone.utc)
    for row in rows:
        peer_id = str(row["peer_id"])
        if exclude_peer_id and peer_id == exclude_peer_id:
            continue
        failures = int(row["consecutive_probe_failures"] or 0)
        if max_consecutive_failures is not None and failures >= max(0, int(max_consecutive_failures)):
            continue
        last_probe_attempt_at = str(row["last_probe_attempt_at"] or "")
        if cooldown_seconds > 0 and last_probe_attempt_at:
            probe_dt = _parse_dt(last_probe_attempt_at)
            if probe_dt is not None and (now - probe_dt).total_seconds() < max(0, int(cooldown_seconds)):
                continue
        out.append(
            PeerEndpointCandidate(
                peer_id=peer_id,
                host=str(row["host"]),
                port=int(row["port"]),
                source=str(row["source"]),
                last_seen_at=str(row["last_seen_at"]),
                last_probe_attempt_at=last_probe_attempt_at,
                last_probe_delivery_ok=bool(row["last_probe_delivery_ok"]),
                consecutive_probe_failures=failures,
            )
        )
        if len(out) >= max(1, int(limit)):
            break
    return out


def note_peer_endpoint_candidate_probe_result(
    peer_id: str,
    host: str,
    port: int,
    *,
    source: str,
    delivered: bool,
) -> None:
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT consecutive_probe_failures
            FROM peer_endpoint_candidates
            WHERE peer_id = ? AND host = ? AND port = ? AND source = ?
            LIMIT 1
            """,
            (peer_id, str(host), int(port), str(source)),
        ).fetchone()
        if not row:
            return
        failures = 0 if delivered else (int(row["consecutive_probe_failures"] or 0) + 1)
        conn.execute(
            """
            UPDATE peer_endpoint_candidates
            SET last_probe_attempt_at = ?,
                last_probe_delivery_ok = ?,
                consecutive_probe_failures = ?,
                updated_at = ?
            WHERE peer_id = ? AND host = ? AND port = ? AND source = ?
            """,
            (
                _utcnow(),
                1 if delivered else 0,
                failures,
                _utcnow(),
                peer_id,
                str(host),
                int(port),
                str(source),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def recent_peer_endpoints(*, exclude_peer_id: str | None = None, limit: int = 32) -> list[tuple[str, str, int]]:
    rows = recent_peer_verified_endpoints(exclude_peer_id=exclude_peer_id, limit=limit, per_peer_limit=1)
    out: list[tuple[str, str, int]] = []
    for endpoint in rows:
        out.append((endpoint.peer_id, endpoint.host, int(endpoint.port)))
    return out


def recent_peer_verified_endpoints(
    *,
    exclude_peer_id: str | None = None,
    limit: int = 32,
    per_peer_limit: int = 1,
) -> list[VerifiedPeerEndpoint]:
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT peer_id, host, port, source, last_seen_at, last_verified_at,
                   verification_kind, proof_count, proof_message_id, proof_message_type, proof_hash, proof_timestamp,
                   last_delivery_attempt_at, last_delivery_success_at, last_delivery_failure_at,
                   consecutive_delivery_failures
            FROM peer_endpoints
            ORDER BY updated_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)) * max(1, int(per_peer_limit)) * 8,),
        ).fetchall()
    finally:
        conn.close()

    max_per_peer = max(1, int(per_peer_limit))
    seen_endpoints: set[tuple[str, str, int]] = set()
    per_peer_counts: dict[str, int] = {}
    out: list[VerifiedPeerEndpoint] = []
    sorted_rows = sorted((_verified_endpoint_from_row(row) for row in rows), key=_verified_endpoint_sort_key, reverse=True)

    def _try_add(endpoint: VerifiedPeerEndpoint) -> None:
        if exclude_peer_id and endpoint.peer_id == exclude_peer_id:
            return
        if per_peer_counts.get(endpoint.peer_id, 0) >= max_per_peer:
            return
        key = (endpoint.peer_id, endpoint.host, int(endpoint.port))
        if key in seen_endpoints:
            return
        seen_endpoints.add(key)
        per_peer_counts[endpoint.peer_id] = per_peer_counts.get(endpoint.peer_id, 0) + 1
        out.append(endpoint)

    for endpoint in sorted_rows:
        _try_add(endpoint)
        if len(out) >= max(1, int(limit)):
            return out
    return out


def delivery_endpoints_for_peer(
    peer_id: str,
    *,
    verified_limit: int = 4,
    include_candidates: bool = False,
    candidate_limit: int = 2,
) -> list[tuple[str, int]]:
    return [
        (item.host, int(item.port))
        for item in delivery_targets_for_peer(
            peer_id,
            verified_limit=verified_limit,
            include_candidates=include_candidates,
            candidate_limit=candidate_limit,
        )
    ]


def delivery_targets_for_peer(
    peer_id: str,
    *,
    verified_limit: int = 4,
    include_candidates: bool = False,
    candidate_limit: int = 2,
) -> list[PeerDeliveryTarget]:
    targets: list[PeerDeliveryTarget] = []
    seen: set[tuple[str, int]] = set()

    verified_rows = verified_endpoints_for_peer(peer_id, limit=max(1, int(verified_limit)) * 3)
    verified_rows = sorted(verified_rows, key=_delivery_target_sort_key, reverse=True)
    for endpoint in verified_rows[: max(1, int(verified_limit))]:
        key = (endpoint.host, int(endpoint.port))
        if key in seen:
            continue
        seen.add(key)
        targets.append(
            PeerDeliveryTarget(
                peer_id=peer_id,
                host=endpoint.host,
                port=int(endpoint.port),
                source=endpoint.source,
                verified=True,
            )
        )

    if include_candidates:
        for candidate in candidate_endpoints_for_peer(peer_id, limit=max(1, int(candidate_limit))):
            key = (candidate.host, int(candidate.port))
            if key in seen:
                continue
            seen.add(key)
            targets.append(
                PeerDeliveryTarget(
                    peer_id=peer_id,
                    host=candidate.host,
                    port=int(candidate.port),
                    source=candidate.source,
                    verified=False,
                )
            )
    return targets

# Phase 28: Progressive Trust Spot-Check Probability
def get_spot_check_probability(peer_id: str) -> float:
    """
    Brand new nodes: 100% spot check
    Trusted (>100): 20%
    Provider (>500): 5%
    Elite (>1000): 1%
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT successful_shards FROM peers WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()
        count = int(row["successful_shards"]) if row else 0
    finally:
        conn.close()

    if count < 100:
        return 1.0
    if count < 500:
        return 0.20
    if count < 1000:
        return 0.05
    return 0.01
