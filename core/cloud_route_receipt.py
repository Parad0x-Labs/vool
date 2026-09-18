from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from core.secret_redaction import redact_secrets

SCHEMA = "vool.cloud_route_receipt.v1"
_WRITE_LOCK = threading.RLock()

# Keys hashed for receipts that carry the A11 additive provenance fields, and the
# LEGACY key set for anything issued before them — an old stored receipt must keep
# verifying against exactly the bytes it was signed over.
_PROVENANCE_KEYS = ("requested_model", "selection_mode", "lane", "fallback_from")


@dataclass(frozen=True)
class CloudRouteReceipt:
    receipt_id: str
    attempt_id: str
    phase: str
    session_id: str
    task_id: str
    turn_id: str
    subtask_id: str
    model_call_id: str
    provider_id: str
    model_id: str
    pricing_state: str
    estimated_max_usd: float
    actual_usd: float | None
    usage: dict[str, Any]
    privacy_class: str
    data_categories: tuple[str, ...]
    policy_decision: str
    route_reason: str
    success: bool | None
    retry_index: int
    fallback_chain: tuple[str, ...]
    error_kind: str
    safe_error: str
    issued_at: float
    prev_hash: str
    content_hash: str
    signer_peer_id: str
    signature: str
    schema: str = SCHEMA
    # A11 additive provenance. Empty means "not mechanically known at issue time" —
    # a missing/unknown fact never becomes a fabricated value. Legacy receipts
    # issued before these keys verify against their ORIGINAL key set (see _content).
    requested_model: str = ""
    selection_mode: str = ""
    lane: str = ""
    fallback_from: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["data_categories"] = list(self.data_categories)
        payload["fallback_chain"] = list(self.fallback_chain)
        return payload


def _canonical_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


_LEGACY_CONTENT_KEYS = (
    "schema",
    "receipt_id",
    "attempt_id",
    "phase",
    "session_id",
    "task_id",
    "turn_id",
    "subtask_id",
    "model_call_id",
    "provider_id",
    "model_id",
    "pricing_state",
    "estimated_max_usd",
    "actual_usd",
    "usage",
    "privacy_class",
    "data_categories",
    "policy_decision",
    "route_reason",
    "success",
    "retry_index",
    "fallback_chain",
    "error_kind",
    "safe_error",
    "issued_at",
    "prev_hash",
)


def _content(receipt: dict[str, Any]) -> dict[str, Any]:
    # A receipt carrying any additive provenance key hashes over the EXTENDED key
    # set; a legacy receipt (none present) keeps hashing over exactly its original
    # bytes so historical rows still verify.
    has_provenance = any(key in receipt for key in _PROVENANCE_KEYS)
    keys = (
        *_LEGACY_CONTENT_KEYS[: -2],  # everything up to safe_error
        *_PROVENANCE_KEYS,
        *_LEGACY_CONTENT_KEYS[-2:],  # issued_at, prev_hash
    ) if has_provenance else _LEGACY_CONTENT_KEYS
    return {key: receipt.get(key) for key in keys}


def issue_cloud_route_receipt(
    *,
    attempt_id: str,
    phase: str,
    session_id: str,
    task_id: str,
    turn_id: str,
    subtask_id: str,
    model_call_id: str,
    provider_id: str,
    model_id: str,
    pricing_state: str,
    estimated_max_usd: float,
    actual_usd: float | None = None,
    usage: dict[str, Any] | None = None,
    privacy_class: str,
    data_categories: tuple[str, ...] = (),
    policy_decision: str,
    route_reason: str,
    success: bool | None = None,
    retry_index: int = 0,
    fallback_chain: tuple[str, ...] = (),
    error_kind: str = "",
    safe_error: str = "",
    prev_hash: str = "",
    issued_at: float | None = None,
    requested_model: str = "",
    selection_mode: str = "",
    lane: str = "",
    fallback_from: str = "",
) -> CloudRouteReceipt:
    clean_error = redact_secrets(str(safe_error or ""))[:240]
    safe_usage = {
        str(key): value
        for key, value in dict(usage or {}).items()
        if str(key) in {"prompt_tokens", "completion_tokens", "input_tokens", "output_tokens", "total_tokens", "cost"}
        and isinstance(value, (int, float))
    }
    payload = {
        "schema": SCHEMA,
        "receipt_id": f"crr-{uuid.uuid4().hex}",
        "attempt_id": str(attempt_id),
        "phase": str(phase),
        "session_id": str(session_id or "local"),
        "task_id": str(task_id),
        "turn_id": str(turn_id),
        "subtask_id": str(subtask_id),
        "model_call_id": str(model_call_id),
        "provider_id": str(provider_id),
        "model_id": str(model_id),
        "pricing_state": str(pricing_state),
        "estimated_max_usd": round(max(0.0, float(estimated_max_usd)), 8),
        "actual_usd": None if actual_usd is None else round(max(0.0, float(actual_usd)), 8),
        "usage": safe_usage,
        "privacy_class": str(privacy_class),
        "data_categories": sorted(set(str(item) for item in data_categories)),
        "policy_decision": str(policy_decision),
        "route_reason": str(route_reason),
        "success": success,
        "retry_index": max(0, int(retry_index)),
        "fallback_chain": list(fallback_chain),
        "error_kind": str(error_kind),
        "safe_error": clean_error,
        "issued_at": round(float(time.time() if issued_at is None else issued_at), 6),
        "prev_hash": str(prev_hash),
        # A11 additive provenance — empty stays empty when not mechanically known.
        "requested_model": str(requested_model or ""),
        "selection_mode": str(selection_mode or ""),
        "lane": str(lane or ""),
        "fallback_from": str(fallback_from or ""),
    }
    content_bytes = _canonical_bytes(payload)
    content_hash = hashlib.sha256(content_bytes).hexdigest()
    signer_peer_id, signature = "", ""
    try:
        from network import signer

        signature = signer.sign(content_bytes)
        signer_peer_id = signer.get_local_peer_id()
    except Exception:
        pass
    return CloudRouteReceipt(
        **payload,
        content_hash=content_hash,
        signer_peer_id=signer_peer_id,
        signature=signature,
    )


def verify_cloud_route_receipt(receipt: CloudRouteReceipt | dict[str, Any]) -> tuple[bool, str]:
    payload = receipt.to_dict() if isinstance(receipt, CloudRouteReceipt) else dict(receipt)
    content_bytes = _canonical_bytes(_content(payload))
    if hashlib.sha256(content_bytes).hexdigest() != str(payload.get("content_hash") or ""):
        return False, "content_hash_mismatch"
    if not payload.get("signature") or not payload.get("signer_peer_id"):
        return False, "unsigned"
    try:
        from network import signer

        if not signer.verify(content_bytes, str(payload["signature"]), str(payload["signer_peer_id"])):
            return False, "bad_signature"
    except Exception:
        return False, "verification_unavailable"
    return True, "ok"


def _receipt_root() -> Path:
    from core.runtime_paths import active_data_dir

    return (active_data_dir() / "cloud_route_receipts").resolve()


def _receipt_path(session_id: str) -> Path:
    safe = "".join(char for char in str(session_id or "local") if char.isalnum() or char in {"-", "_"})[:96]
    return _receipt_root() / f"{safe or 'local'}.jsonl"


def list_cloud_route_receipts(session_id: str) -> list[dict[str, Any]]:
    path = _receipt_path(session_id)
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def last_cloud_route_receipt_hash(session_id: str) -> str:
    receipts = list_cloud_route_receipts(session_id)
    return str(receipts[-1].get("content_hash") or "") if receipts else ""


def record_cloud_route_receipt(receipt: CloudRouteReceipt) -> None:
    ok, reason = verify_cloud_route_receipt(receipt)
    if not ok:
        raise ValueError(f"cloud route receipt rejected: {reason}")
    with _WRITE_LOCK:
        expected = last_cloud_route_receipt_hash(receipt.session_id)
        if receipt.prev_hash != expected:
            raise ValueError("cloud route receipt chain changed concurrently")
        path = _receipt_path(receipt.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(receipt.to_dict(), sort_keys=True, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())


__all__ = [
    "CloudRouteReceipt",
    "issue_cloud_route_receipt",
    "last_cloud_route_receipt_hash",
    "list_cloud_route_receipts",
    "record_cloud_route_receipt",
    "verify_cloud_route_receipt",
]
