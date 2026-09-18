from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from network import signer
from storage.identity_lifecycle_store import (
    delete_identity_revocation,
    get_identity_revocation,
    list_identity_key_history,
    list_identity_revocations,
    record_identity_key,
    upsert_identity_revocation,
)

IDENTITY_SCOPES = {"all", "mesh_message", "signed_write", "brain_hive", "meet_write"}


@dataclass(frozen=True)
class IdentityRevocationDecision:
    revoked: bool
    peer_id: str
    scope: str
    reason: str | None = None
    replacement_peer_id: str | None = None
    expires_at: str | None = None
    metadata: dict[str, Any] | None = None


def _normalize_scope(scope: str) -> str:
    value = str(scope or "all").strip().lower()
    if value not in IDENTITY_SCOPES:
        raise ValueError(f"Unsupported identity scope: {scope}")
    return value


def _revocation_for_scope(peer_id: str, scope: str) -> dict[str, Any] | None:
    record = get_identity_revocation(peer_id, scope=scope)
    if record:
        return record
    if scope != "all":
        return get_identity_revocation(peer_id, scope="all")
    return None


def is_identity_revoked(peer_id: str, *, scope: str = "all", now: datetime | None = None) -> IdentityRevocationDecision:
    normalized_scope = _normalize_scope(scope)
    record = _revocation_for_scope(peer_id, normalized_scope)
    if not record:
        return IdentityRevocationDecision(False, peer_id=peer_id, scope=normalized_scope)
    active_now = now or datetime.now(timezone.utc)
    expires_at = str(record.get("expires_at") or "").strip()
    if expires_at:
        expiry = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        expiry = expiry.replace(tzinfo=timezone.utc) if expiry.tzinfo is None else expiry.astimezone(timezone.utc)
        if active_now > expiry:
            delete_identity_revocation(peer_id, scope=str(record.get("scope") or normalized_scope))
            return IdentityRevocationDecision(False, peer_id=peer_id, scope=normalized_scope)
    return IdentityRevocationDecision(
        True,
        peer_id=peer_id,
        scope=str(record.get("scope") or normalized_scope),
        reason=str(record.get("reason") or "revoked"),
        replacement_peer_id=str(record.get("replacement_peer_id") or "") or None,
        expires_at=expires_at or None,
        metadata=dict(record.get("metadata") or {}),
    )


def enforce_active_identity(peer_id: str, *, scope: str = "all") -> None:
    decision = is_identity_revoked(peer_id, scope=scope)
    if decision.revoked:
        replacement = f" Replacement: {decision.replacement_peer_id}." if decision.replacement_peer_id else ""
        raise ValueError(
            f"Identity {peer_id[:24]} is revoked for scope '{decision.scope}': {decision.reason}.{replacement}"
        )


def revoke_identity(
    peer_id: str,
    *,
    scope: str = "all",
    reason: str,
    revoked_by_peer_id: str | None = None,
    replacement_peer_id: str | None = None,
    expires_at: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    normalized_scope = _normalize_scope(scope)
    upsert_identity_revocation(
        peer_id=peer_id,
        scope=normalized_scope,
        reason=reason,
        revoked_at=datetime.now(timezone.utc).isoformat(),
        expires_at=expires_at,
        revoked_by_peer_id=revoked_by_peer_id,
        replacement_peer_id=replacement_peer_id,
        metadata=metadata or {},
    )


def clear_identity_revocation(peer_id: str, *, scope: str = "all") -> int:
    return delete_identity_revocation(peer_id, scope=_normalize_scope(scope))


def record_local_identity_state(*, state: str, metadata: dict[str, Any] | None = None) -> str:
    peer_id = signer.get_local_peer_id()
    key_path = str(signer.local_key_path())
    return record_identity_key(peer_id=peer_id, key_path=key_path, state=state, metadata=metadata or {})


def rotate_local_identity(*, reason: str = "manual_rotation") -> dict[str, str]:
    old_peer_id = signer.get_local_peer_id()
    old_key_path = signer.local_key_path()
    record_identity_key(
        peer_id=old_peer_id,
        key_path=str(old_key_path),
        state="rotating_out",
        metadata={"reason": reason},
    )
    result = signer.rotate_local_keypair()
    new_peer_id = result["new_peer_id"]
    record_identity_key(
        peer_id=new_peer_id,
        key_path=str(signer.local_key_path()),
        state="active",
        metadata={"reason": reason, "replaced_peer_id": old_peer_id},
    )
    revoke_identity(
        old_peer_id,
        scope="all",
        reason=reason,
        replacement_peer_id=new_peer_id,
        metadata={"archived_key_path": str(result["archived_key_path"])},
    )
    return {
        "old_peer_id": old_peer_id,
        "new_peer_id": new_peer_id,
        "archived_key_path": str(result["archived_key_path"]),
    }


def identity_lifecycle_snapshot() -> dict[str, Any]:
    return {
        "active_local_peer_id": signer.get_local_peer_id(),
        "key_path": str(signer.local_key_path()),
        "revocations": list_identity_revocations(limit=200),
        "key_history": list_identity_key_history(limit=200),
    }


def current_key_dir() -> Path:
    return signer.local_key_path().parent
