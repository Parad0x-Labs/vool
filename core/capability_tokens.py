from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

from core.task_capsule import TaskCapsule
from network import signer
from storage.db import get_connection

ASSIGNMENT_CAPABILITY = "EXECUTE_TASK_CAPSULE"


@dataclass(frozen=True)
class CapabilityDecision:
    ok: bool
    reason: str


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _utcnow_iso() -> str:
    return _utcnow().isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:
        return None


def _normalized_scope(scope: dict[str, Any]) -> dict[str, Any]:
    clean = dict(scope or {})
    allowed = clean.get("allowed_operations") or []
    clean["allowed_operations"] = sorted({str(item) for item in allowed if str(item).strip()})
    clean["max_response_bytes"] = int(clean.get("max_response_bytes") or 0)
    clean["capsule_hash"] = str(clean.get("capsule_hash") or "")
    clean["assignment_mode"] = str(clean.get("assignment_mode") or "single")
    clean["task_id"] = str(clean.get("task_id") or "")
    return clean


def _canonical_token_bytes(token: dict[str, Any]) -> bytes:
    payload = dict(token)
    payload.pop("signature", None)
    payload["scope"] = _normalized_scope(dict(payload.get("scope") or {}))
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def issue_assignment_capability(
    *,
    task_id: str,
    parent_peer_id: str,
    helper_peer_id: str,
    capsule: TaskCapsule,
    assignment_mode: str,
    lease_seconds: int,
) -> dict[str, Any]:
    token_id = str(uuid.uuid4())
    created_at = _utcnow()
    expires_at = created_at + timedelta(seconds=max(60, int(lease_seconds)))
    scope = _normalized_scope(
        {
            "task_id": task_id,
            "capsule_hash": capsule.capsule_hash,
            "allowed_operations": list(capsule.allowed_operations),
            "max_response_bytes": int(capsule.max_response_bytes),
            "assignment_mode": assignment_mode,
        }
    )
    token = {
        "token_id": token_id,
        "capability_name": ASSIGNMENT_CAPABILITY,
        "task_id": task_id,
        "granted_by": parent_peer_id,
        "granted_to": helper_peer_id,
        "scope": scope,
        "created_at": created_at.isoformat(),
        "expires_at": expires_at.isoformat(),
    }
    token["signature"] = signer.sign(_canonical_token_bytes(token))
    remember_capability_token(token, status="active")
    return token


def load_capability_token(token_id: str) -> dict[str, Any] | None:
    token_key = str(token_id or "").strip()
    if not token_key:
        return None
    conn = get_connection()
    try:
        row = conn.execute(
            """
            SELECT token_id, capability_name, scope_json, granted_by, granted_to, task_id,
                   signature, status, expires_at, created_at, updated_at, used_at, revoked_at
            FROM capability_tokens
            WHERE token_id = ?
            LIMIT 1
            """,
            (token_key,),
        ).fetchone()
        if not row:
            return None
        return {
            "token_id": row["token_id"],
            "capability_name": row["capability_name"],
            "scope": json.loads(str(row["scope_json"] or "{}")),
            "granted_by": row["granted_by"],
            "granted_to": row["granted_to"],
            "task_id": row["task_id"],
            "signature": row["signature"],
            "status": row["status"],
            "expires_at": row["expires_at"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "used_at": row["used_at"],
            "revoked_at": row["revoked_at"],
        }
    finally:
        conn.close()


def remember_capability_token(token: dict[str, Any], *, status: str = "active") -> None:
    token_id = str(token.get("token_id") or "").strip()
    if not token_id:
        return
    scope = _normalized_scope(dict(token.get("scope") or {}))
    created_at = str(token.get("created_at") or _utcnow_iso())
    expires_at = str(token.get("expires_at") or "")
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT OR REPLACE INTO capability_tokens (
                token_id, capability_name, scope_json, granted_by, granted_to, task_id,
                signature, status, expires_at, created_at, updated_at, used_at, revoked_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, COALESCE(
                (SELECT status FROM capability_tokens
                 WHERE token_id = ? AND status IN ('used', 'revoked', 'expired')), ?
            ), ?, COALESCE(
                (SELECT created_at FROM capability_tokens WHERE token_id = ?), ?
            ), ?, COALESCE((SELECT used_at FROM capability_tokens WHERE token_id = ?), NULL),
               COALESCE((SELECT revoked_at FROM capability_tokens WHERE token_id = ?), NULL))
            """,
            (
                token_id,
                str(token.get("capability_name") or ASSIGNMENT_CAPABILITY),
                json.dumps(scope, sort_keys=True),
                str(token.get("granted_by") or ""),
                str(token.get("granted_to") or ""),
                str(token.get("task_id") or scope.get("task_id") or ""),
                str(token.get("signature") or ""),
                token_id,                      # status: keep a terminal status sticky…
                str(status or "active"),        # …otherwise apply the requested status
                expires_at or None,
                token_id,
                created_at,
                _utcnow_iso(),
                token_id,
                token_id,
            ),
        )
        conn.commit()
    finally:
        conn.close()


def verify_assignment_capability(
    token: dict[str, Any] | None,
    *,
    task_id: str,
    parent_peer_id: str,
    helper_peer_id: str,
    capsule: TaskCapsule,
) -> CapabilityDecision:
    if not isinstance(token, dict):
        return CapabilityDecision(False, "Missing assignment capability token.")

    token_id = str(token.get("token_id") or "").strip()
    capability_name = str(token.get("capability_name") or "").strip()
    granted_by = str(token.get("granted_by") or "").strip()
    granted_to = str(token.get("granted_to") or "").strip()
    signature = str(token.get("signature") or "").strip()
    token_task_id = str(token.get("task_id") or "").strip()
    expires_at = _parse_iso(str(token.get("expires_at") or ""))
    scope = _normalized_scope(dict(token.get("scope") or {}))

    if not token_id or not signature:
        return CapabilityDecision(False, "Capability token is missing identity fields.")
    if capability_name != ASSIGNMENT_CAPABILITY:
        return CapabilityDecision(False, f"Unsupported capability token: {capability_name or 'unknown'}.")
    if granted_by != parent_peer_id:
        return CapabilityDecision(False, "Capability grant parent mismatch.")
    if granted_to != helper_peer_id:
        return CapabilityDecision(False, "Capability grant helper mismatch.")
    if token_task_id != task_id or scope.get("task_id") != task_id:
        return CapabilityDecision(False, "Capability grant task mismatch.")
    if expires_at is None or expires_at <= _utcnow():
        return CapabilityDecision(False, "Capability token expired.")
    if scope.get("capsule_hash") != capsule.capsule_hash:
        return CapabilityDecision(False, "Capability token capsule hash mismatch.")

    allowed_scope = set(scope.get("allowed_operations") or [])
    allowed_capsule = set(capsule.allowed_operations or [])
    if not allowed_scope or not allowed_scope.issubset(allowed_capsule):
        return CapabilityDecision(False, "Capability token scope exceeds capsule permissions.")

    max_response_bytes = int(scope.get("max_response_bytes") or 0)
    if max_response_bytes <= 0 or max_response_bytes > int(capsule.max_response_bytes):
        return CapabilityDecision(False, "Capability token response budget is invalid.")

    if not signer.verify(_canonical_token_bytes(token), signature, granted_by):
        return CapabilityDecision(False, "Capability token signature invalid.")

    remembered = load_capability_token(token_id)
    if remembered is not None:
        status = str(remembered.get("status") or "active")
        if status != "active":
            return CapabilityDecision(False, f"Capability token is {status}.")
        local_signature = str(remembered.get("signature") or "")
        if local_signature and local_signature != signature:
            return CapabilityDecision(False, "Capability token signature mismatch against local record.")

    remember_capability_token(token, status="active")
    return CapabilityDecision(True, "Capability token accepted.")


def mark_capability_token_used(token_id: str) -> bool:
    """Mark a token used, routed through the atomic :func:`consume_capability_token` CAS.

    This delegates so there is a single race-safe path that spends a token — the prior
    implementation was a non-rowcount-gated ``CASE`` update that could not be used as a
    race-safe gate. Prefer calling :func:`consume_capability_token` directly at a consumer
    and gating on its boolean. Returns True only when this call performed the
    active -> used transition; an already used/revoked/expired token, or a concurrent
    winner, returns False.
    """
    return consume_capability_token(token_id)


def consume_capability_token(token_id: str) -> bool:
    """Atomically flip active -> used, returning True only if THIS call performed the
    transition (rowcount == 1). A concurrent caller, or an already used/revoked/expired
    token, gets False. Gating the consumer on this makes verify+consume race-safe: two
    concurrent assignment executions cannot both win the same single-use token.
    """
    token_key = str(token_id or "").strip()
    if not token_key:
        return False
    conn = get_connection()
    try:
        cursor = conn.execute(
            """
            UPDATE capability_tokens
            SET status = 'used',
                used_at = COALESCE(used_at, ?),
                updated_at = ?
            WHERE token_id = ? AND status = 'active'
            """,
            (_utcnow_iso(), _utcnow_iso(), token_key),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        conn.close()


def revoke_capability_tokens_for_task(
    task_id: str,
    *,
    helper_peer_id: str | None = None,
    reason: str = "",
) -> int:
    task_key = str(task_id or "").strip()
    if not task_key:
        return 0
    conn = get_connection()
    try:
        if helper_peer_id:
            result = conn.execute(
                """
                UPDATE capability_tokens
                SET status = 'revoked',
                    revoked_at = COALESCE(revoked_at, ?),
                    updated_at = ?
                WHERE task_id = ?
                  AND granted_to = ?
                  AND status IN ('active', 'used')
                """,
                (_utcnow_iso(), _utcnow_iso(), task_key, str(helper_peer_id)),
            )
        else:
            result = conn.execute(
                """
                UPDATE capability_tokens
                SET status = 'revoked',
                    revoked_at = COALESCE(revoked_at, ?),
                    updated_at = ?
                WHERE task_id = ?
                  AND status IN ('active', 'used')
                """,
                (_utcnow_iso(), _utcnow_iso(), task_key),
            )
        conn.commit()
        del reason
        return int(result.rowcount or 0)
    finally:
        conn.close()


def expire_stale_capability_tokens(limit: int = 200) -> int:
    safe_limit = max(1, int(limit))
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT token_id
            FROM capability_tokens
            WHERE status IN ('active', 'used')
              AND expires_at IS NOT NULL
              AND expires_at != ''
              AND expires_at <= ?
            ORDER BY expires_at ASC
            LIMIT ?
            """,
            (_utcnow_iso(), safe_limit),
        ).fetchall()
        if not rows:
            return 0
        token_ids = [str(row["token_id"]) for row in rows]
        placeholders = ",".join("?" for _ in token_ids)
        conn.execute(
            f"""
            UPDATE capability_tokens
            SET status = 'expired',
                revoked_at = COALESCE(revoked_at, ?),
                updated_at = ?
            WHERE token_id IN ({placeholders})
            """,
            (_utcnow_iso(), _utcnow_iso(), *token_ids),
        )
        conn.commit()
        return len(token_ids)
    finally:
        conn.close()
