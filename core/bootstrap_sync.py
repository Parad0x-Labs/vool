from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from core import audit_logger
from core.bootstrap_adapters import BootstrapMirrorAdapter, FileTopicAdapter
from core.discovery_index import (
    delivery_targets_for_peer,
    record_bootstrap_presence,
    record_verified_peer_endpoint_proof,
    register_peer_endpoint,
)
from core.runtime_paths import data_path
from network.signer import get_local_peer_id as local_peer_id
from network.signer import sign, verify
from storage.db import get_connection

DEFAULT_TOPICS = ["topic_a", "topic_b", "topic_c"]


@dataclass
class BootstrapSyncResult:
    topics_written: int
    topics_read: int
    records_merged: int


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_dir() -> None:
    _bootstrap_dir().mkdir(parents=True, exist_ok=True)


def _bootstrap_dir() -> Path:
    return data_path("bootstrap")


def _topic_path(topic_name: str) -> Path:
    safe = "".join(ch for ch in topic_name if ch.isalnum() or ch in {"_", "-"}).strip() or "topic"
    return _bootstrap_dir() / f"{safe}.json"


def _canonical_bytes(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _snapshot_body(topic_name: str, publisher_peer_id: str, records: list[dict[str, Any]], ttl_minutes: int) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=ttl_minutes)

    return {
        "topic_name": topic_name,
        "publisher_peer_id": publisher_peer_id,
        "published_at": now.isoformat(),
        "expires_at": expires.isoformat(),
        "record_count": len(records),
        "records": records,
    }


def _sign_snapshot_body(body: dict[str, Any]) -> dict[str, Any]:
    digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    signed = dict(body)
    signed["snapshot_hash"] = digest
    signed["signature"] = sign(_canonical_bytes(body))
    return signed


def _verify_snapshot(snapshot: dict[str, Any]) -> bool:
    signature = snapshot.get("signature")
    publisher = snapshot.get("publisher_peer_id")
    snapshot_hash = snapshot.get("snapshot_hash")

    if not signature or not publisher or not snapshot_hash:
        return False

    body = dict(snapshot)
    body.pop("signature", None)
    body.pop("snapshot_hash", None)

    digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    if digest != snapshot_hash:
        return False

    if not verify(_canonical_bytes(body), signature, publisher):
        return False

    try:
        expires = datetime.fromisoformat(snapshot["expires_at"])
    except Exception:
        return False

    return not expires <= datetime.now(timezone.utc)


def _local_capability_records(limit: int = 128) -> list[dict[str, Any]]:
    """
    Export only safe peer summaries.
    No raw tasks, no persona, no rewards, no private data.
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT peer_id, status, capabilities_json, capacity, trust_score,
                   host_group_hint_hash, last_seen_at
            FROM agent_capabilities
            ORDER BY last_seen_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    records: list[dict[str, Any]] = []
    for row in rows:
        try:
            capabilities = json.loads(row["capabilities_json"] or "[]")
        except Exception:
            capabilities = []

        delivery_targets = delivery_targets_for_peer(
            row["peer_id"],
            verified_limit=4,
            include_candidates=False,
        )
        record = {
            "peer_id": row["peer_id"],
            "status": row["status"],
            "capabilities": capabilities[:16],
            "capacity": int(row["capacity"]),
            "trust_score": float(row["trust_score"]),
            "host_group_hint_hash": row["host_group_hint_hash"],
            "last_seen_at": row["last_seen_at"],
        }
        if delivery_targets:
            record["endpoints"] = [
                {"host": item.host, "port": int(item.port), "source": item.source}
                for item in delivery_targets
            ]
            record["endpoint"] = {
                "host": delivery_targets[0].host,
                "port": int(delivery_targets[0].port),
            }
        records.append(record)
    return records


def _get_active_peer_records(ttl_minutes: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max(1, int(ttl_minutes)))
    records: list[dict[str, Any]] = []
    for record in _local_capability_records():
        last_seen_raw = str(record.get("last_seen_at") or "").strip()
        if not last_seen_raw:
            continue
        try:
            last_seen = datetime.fromisoformat(last_seen_raw.replace("Z", "+00:00"))
        except Exception:
            continue
        if last_seen >= cutoff:
            records.append(record)
    return records


def publish_local_presence_snapshots(
    *,
    topic_names: list[str] | None = None,
    ttl_minutes: int = 15,
    include_self_if_missing: bool = True,
    adapter: BootstrapMirrorAdapter | None = None,
) -> int:
    """
    Exports a local summary of active peers, signs it, and saves it to all bootstrap topics.
    Returns the number of files successfully written.
    """
    topics = topic_names or ["topic_a", "topic_b"]
    records = _get_active_peer_records(ttl_minutes)

    if include_self_if_missing:
        # local capability ad runs in independent job, so self might be missing if just booting
        if not any(r.get("peer_id") == local_peer_id() for r in records):
            # dummy fill just for schema validation out; we rely on real ads elsewhere
            pass

    adapter = adapter or FileTopicAdapter(_bootstrap_dir())
    written = 0
    for topic in topics:
        body = _snapshot_body(topic, local_peer_id(), records, ttl_minutes)
        signed = _sign_snapshot_body(body)
        if adapter.publish_snapshot(topic, signed):
            written += 1

    if written > 0:
        audit_logger.log(
            "bootstrap_sync_published",
            target_id=local_peer_id(),
            target_type="bootstrap",
            details={
                "topic_count": len(topics),
                "written": written,
                "peer_records": len(records),
            },
        )
    return written


def _merge_snapshot(snapshot: dict[str, Any]) -> int:
    merged = 0

    if not _verify_snapshot(snapshot):
        return 0

    records = snapshot.get("records") or []
    if not isinstance(records, list):
        return 0

    for rec in records:
        if not isinstance(rec, dict):
            continue

        peer_id = rec.get("peer_id")
        status = rec.get("status")
        capabilities = rec.get("capabilities") or []
        capacity = rec.get("capacity", 0)
        trust_score = rec.get("trust_score", 0.5)
        host_group_hint_hash = rec.get("host_group_hint_hash")

        if not peer_id or not isinstance(capabilities, list):
            continue

        record_bootstrap_presence(
            peer_id=str(peer_id),
            status=str(status or "idle"),
            capabilities=[str(x) for x in capabilities[:16]],
            capacity=max(0, int(capacity)),
            trust_score=max(0.0, min(1.0, float(trust_score))),
            host_group_hint_hash=str(host_group_hint_hash) if host_group_hint_hash else None,
        )

        endpoints = rec.get("endpoints")
        merged_any_endpoint = False
        if isinstance(endpoints, list):
            for endpoint in endpoints:
                if not isinstance(endpoint, dict):
                    continue
                host = endpoint.get("host")
                port = endpoint.get("port")
                if not host or not isinstance(port, int) or port <= 0:
                    continue
                register_peer_endpoint(str(peer_id), str(host), int(port), source="bootstrap")
                record_verified_peer_endpoint_proof(
                    str(peer_id),
                    str(host),
                    int(port),
                    source="bootstrap",
                    verification_kind="bootstrap_snapshot",
                    proof_message_id=str(snapshot.get("snapshot_hash") or ""),
                    proof_message_type=str(snapshot.get("topic_name") or "bootstrap_snapshot"),
                    proof_hash=str(snapshot.get("snapshot_hash") or ""),
                    proof_signature=str(snapshot.get("signature") or ""),
                    proof_timestamp=str(snapshot.get("published_at") or ""),
                )
                merged_any_endpoint = True
        if not merged_any_endpoint:
            endpoint = rec.get("endpoint")
            if isinstance(endpoint, dict):
                host = endpoint.get("host")
                port = endpoint.get("port")
                if host and isinstance(port, int) and port > 0:
                    register_peer_endpoint(str(peer_id), str(host), int(port), source="bootstrap")
                    record_verified_peer_endpoint_proof(
                        str(peer_id),
                        str(host),
                        int(port),
                        source="bootstrap",
                        verification_kind="bootstrap_snapshot",
                        proof_message_id=str(snapshot.get("snapshot_hash") or ""),
                        proof_message_type=str(snapshot.get("topic_name") or "bootstrap_snapshot"),
                        proof_hash=str(snapshot.get("snapshot_hash") or ""),
                        proof_signature=str(snapshot.get("signature") or ""),
                        proof_timestamp=str(snapshot.get("published_at") or ""),
                    )

        merged += 1

    return merged


def sync_from_bootstrap_topics(
    topic_names: list[str] | None = None,
    adapter: BootstrapMirrorAdapter | None = None,
) -> BootstrapSyncResult:
    """
    Reads available topic files, verifies signatures, and merges peer capabilities
    into the local database index.
    """
    topics = topic_names or ["topic_a", "topic_b"]
    topics_read = 0
    records_merged = 0

    adapter = adapter or FileTopicAdapter(_bootstrap_dir())

    for topic in topics:
        data = adapter.fetch_snapshot(topic)
        if not data:
            continue
        topics_read += 1
        records_merged += _merge_snapshot(data)

    if records_merged > 0:
        audit_logger.log(
            "bootstrap_sync_pulled",
            target_id=local_peer_id(),
            target_type="bootstrap",
            details={
                "topics_read": topics_read,
                "records_merged": records_merged,
            },
        )

    return BootstrapSyncResult(
        topics_written=0,
        topics_read=topics_read,
        records_merged=records_merged,
    )


def publish_and_sync(topic_names: list[str] | None = None) -> BootstrapSyncResult:
    topics = topic_names or DEFAULT_TOPICS
    written = publish_local_presence_snapshots(topic_names=topics)
    sync = sync_from_bootstrap_topics(topic_names=topics)
    return BootstrapSyncResult(
        topics_written=written,
        topics_read=sync.topics_read,
        records_merged=sync.records_merged,
    )


def prune_expired_topic_files(max_age_minutes: int = 60) -> int:
    _ensure_dir()
    removed = 0
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)

    for path in _bootstrap_dir().glob("*.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            published_at = datetime.fromisoformat(data["published_at"])
        except Exception:
            path.unlink(missing_ok=True)
            removed += 1
            continue

        if published_at < cutoff:
            path.unlink(missing_ok=True)
            removed += 1

    return removed
