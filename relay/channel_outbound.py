from __future__ import annotations

import hashlib
import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.bootstrap_adapters import BootstrapMirrorAdapter, HttpJsonMirrorAdapter
from network.signer import get_local_peer_id as local_peer_id
from network.signer import sign

TOPIC_BY_PLATFORM = {
    "discord": "discord_outbound",
    "telegram": "telegram_outbound",
}
MAX_RECORDS_PER_TOPIC = 128


@dataclass(frozen=True)
class OutboundPostRecord:
    record_id: str
    platform: str
    target: str
    content: str
    task_id: str
    session_id: str
    source_context: dict[str, Any]
    created_at: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _canonical_bytes(obj: dict[str, Any]) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _build_snapshot(topic_name: str, records: list[dict[str, Any]], *, ttl_minutes: int = 24 * 60) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    body = {
        "topic_name": topic_name,
        "publisher_peer_id": local_peer_id(),
        "published_at": now.isoformat(),
        "expires_at": (now + timedelta(minutes=ttl_minutes)).isoformat(),
        "record_count": len(records),
        "records": records,
    }
    digest = hashlib.sha256(_canonical_bytes(body)).hexdigest()
    try:
        signature = sign(_canonical_bytes(body))
    except Exception:
        signature = "unsigned_local"
    signed = dict(body)
    signed["snapshot_hash"] = digest
    signed["signature"] = signature
    return signed


def _default_adapter() -> BootstrapMirrorAdapter:
    return HttpJsonMirrorAdapter(os.environ.get("VOOL_MIRROR_URL", "http://127.0.0.1:8787"))


def _clean_existing_records(snapshot: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(snapshot, dict):
        return []
    records = snapshot.get("records")
    if not isinstance(records, list):
        return []
    out: list[dict[str, Any]] = []
    for item in records:
        if not isinstance(item, dict):
            continue
        record_id = str(item.get("record_id") or "").strip()
        content = str(item.get("content") or "").strip()
        platform = str(item.get("platform") or "").strip().lower()
        if not record_id or not content or platform not in TOPIC_BY_PLATFORM:
            continue
        out.append(dict(item))
    return out[-MAX_RECORDS_PER_TOPIC:]


def propose_bridge_delivery(
    finalization_id: str,
    *,
    evidence_class: str = "PLATFORM_ACK",
) -> bool:
    """R-7/H-5 (K-10): a bridge/relay PROPOSES typed recipient-class evidence;
    it never authors DELIVERED directly. The canonical delivery reconciliation
    (`core.finalization.set_delivery_status`) decides — evidence-gated."""
    if not str(finalization_id or "").strip():
        return False
    from core.finalization import DELIVERY_DELIVERED
    from core.finalization import set_delivery_status as _canonical_sds

    return _canonical_sds(
        str(finalization_id), DELIVERY_DELIVERED, evidence_class=str(evidence_class)
    )


def build_outbound_post_record(
    *,
    platform: str,
    content: str,
    task_id: str,
    session_id: str,
    source_context: dict[str, Any] | None,
    target: str = "default",
    finalization_id: str = "",
) -> dict[str, Any]:
    platform_name = str(platform or "").strip().lower()
    if platform_name not in TOPIC_BY_PLATFORM:
        raise ValueError(f"unsupported_platform:{platform_name}")

    now = _utcnow()
    safe_target = str(target or "default").strip() or "default"
    # R-7/H-5 typed finalization state (CE-3): dispatch runs mid-turn, BEFORE
    # any answer seal, so a queued post normally has NO A7 identity yet. That
    # state is declared EXPLICITLY here — a bridge PLATFORM_ACK against a
    # pre-finalization record is refused by `propose_bridge_delivery` (empty
    # fid), never silently ignored — instead of shipping an ambiguous missing
    # field. Only an explicitly CARRIED fid (minted solely by
    # core.finalization) marks the record reconcilable.
    _fid = str(finalization_id or "").strip()
    compact_source_context = {
        "surface": str((source_context or {}).get("surface", "")),
        "platform": str((source_context or {}).get("platform", "")),
        "channel_id": str((source_context or {}).get("channel_id", "")),
        "source_user_id": str((source_context or {}).get("source_user_id", "")),
    }
    record = {
        "record_id": str(uuid.uuid4()),
        "kind": "outbound_post",
        "platform": platform_name,
        "target": safe_target,
        "content": str(content or "").strip(),
        "task_id": str(task_id),
        "session_id": str(session_id),
        "created_at": now,
        "delivery_status": "pending",
        "delivery_attempts": 0,
        "finalization_state": "carried" if _fid else "pre_finalization",
        "canonical_memory_eligible": False,
        "candidate_memory_eligible": False,
        "provenance": {
            "origin": "vool_agent",
            "source_surface": compact_source_context["surface"],
            "source_platform": compact_source_context["platform"],
            "channel_id": compact_source_context["channel_id"],
            "source_user_id": compact_source_context["source_user_id"],
            "task_id": str(task_id),
            "session_id": str(session_id),
            "memory_policy": "do_not_promote_canonical",
        },
        "source_context": compact_source_context,
    }
    if _fid:
        record["finalization_id"] = _fid
    return record


def append_outbound_post(
    *,
    platform: str,
    content: str,
    task_id: str,
    session_id: str,
    source_context: dict[str, Any] | None,
    target: str = "default",
    adapter: BootstrapMirrorAdapter | None = None,
    finalization_id: str = "",
) -> tuple[bool, dict[str, Any]]:
    platform_name = str(platform or "").strip().lower()
    topic_name = TOPIC_BY_PLATFORM.get(platform_name)
    if not topic_name:
        raise ValueError(f"unsupported_platform:{platform_name}")

    mirror = adapter or _default_adapter()
    # A8 disclosure gate at snapshot write: a governed payload that is no
    # longer AVAILABLE is never (re)published into a mirror snapshot. Lineage
    # (finalization_id) is authoritative; the content-hash verdict covers
    # unlineaged content (erasure digest tombstones make it deterministic).
    from core.finalization import (
        AVAILABILITY_AVAILABLE,
        payload_availability_for_finalization_id,
        payload_availability_for_text,
    )

    fid = str(finalization_id or "").strip()
    verdict = (
        payload_availability_for_finalization_id(fid)
        if fid
        else payload_availability_for_text(content)
    )
    if verdict is not None and verdict != AVAILABILITY_AVAILABLE:
        return False, {
            "kind": "outbound_post_refused",
            "platform": platform_name,
            "reason": "payload_unavailable_by_policy",
            "availability": verdict,
        }
    current = mirror.fetch_snapshot(topic_name)
    records = _clean_existing_records(current)
    record = build_outbound_post_record(
        platform=platform_name,
        content=content,
        task_id=task_id,
        session_id=session_id,
        source_context=source_context,
        target=target,
        finalization_id=str(finalization_id or ""),
    )
    # (fid attachment now happens inside build_outbound_post_record, which also
    # stamps the typed `finalization_state`; nothing further needed here.)
    # Overflow drops are NEVER silent: evicted pending records whose committed
    # truth exists get a typed FAILED mark (never a vanishing message).
    dropped = records[: max(0, len(records) + 1 - MAX_RECORDS_PER_TOPIC)]
    for stale in dropped:
        _fid = str(stale.get("finalization_id") or "")
        if _fid:
            from core.finalization import (
                DELIVERY_FAILED_TRANSPORT,
            )
            from core.finalization import (
                set_delivery_status as _sds_drop,
            )

            _sds_drop(_fid, DELIVERY_FAILED_TRANSPORT, evidence_class="TRANSPORT_HANDOFF")
    records.append(record)
    records = records[-MAX_RECORDS_PER_TOPIC:]
    snapshot = _build_snapshot(topic_name, records)
    ok = mirror.publish_snapshot(topic_name, snapshot)
    return ok, record


def replace_outbound_records(
    platform: str,
    records: list[dict[str, Any]],
    *,
    adapter: BootstrapMirrorAdapter | None = None,
) -> bool:
    platform_name = str(platform or "").strip().lower()
    topic_name = TOPIC_BY_PLATFORM.get(platform_name)
    if not topic_name:
        raise ValueError(f"unsupported_platform:{platform_name}")
    cleaned = _clean_existing_records({"records": records})
    snapshot = _build_snapshot(topic_name, cleaned)
    mirror = adapter or _default_adapter()
    return mirror.publish_snapshot(topic_name, snapshot)

