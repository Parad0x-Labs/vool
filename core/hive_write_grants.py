from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from core.identity_lifecycle import enforce_active_identity
from network import signer
from storage.db import get_connection


class HiveWriteGrantEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    grant_id: str = Field(min_length=8, max_length=128)
    granted_by: str = Field(min_length=16, max_length=256)
    granted_to: str = Field(min_length=16, max_length=256)
    allowed_paths: list[str] = Field(default_factory=list, min_length=1, max_length=16)
    topic_id: Optional[str] = Field(default=None, max_length=256)
    claim_id: Optional[str] = Field(default=None, max_length=256)
    max_uses: int = Field(default=1, ge=1, le=4096)
    max_body_bytes: int = Field(default=16384, ge=128, le=262144)
    review_required_by_default: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)
    issued_at: datetime
    expires_at: datetime
    signature: str = Field(min_length=16, max_length=4096)


def _init_tables() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS hive_write_grants (
                grant_id TEXT PRIMARY KEY,
                granted_by TEXT NOT NULL,
                granted_to TEXT NOT NULL,
                allowed_paths_json TEXT NOT NULL DEFAULT '[]',
                topic_id TEXT NOT NULL DEFAULT '',
                claim_id TEXT NOT NULL DEFAULT '',
                max_uses INTEGER NOT NULL DEFAULT 1,
                used_count INTEGER NOT NULL DEFAULT 0,
                max_body_bytes INTEGER NOT NULL DEFAULT 16384,
                review_required_by_default INTEGER NOT NULL DEFAULT 0,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                issued_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                signature TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                last_used_at TEXT,
                revoked_at TEXT
            )
            """
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_hive_write_grants_target ON hive_write_grants(granted_to, status, expires_at)"
        )
        conn.commit()
    finally:
        conn.close()


def canonical_hive_write_grant_bytes(
    *,
    grant_id: str,
    granted_by: str,
    granted_to: str,
    allowed_paths: list[str],
    topic_id: str | None,
    claim_id: str | None,
    max_uses: int,
    max_body_bytes: int,
    review_required_by_default: bool,
    metadata: dict[str, Any],
    issued_at: str,
    expires_at: str,
) -> bytes:
    body = {
        "allowed_paths": list(allowed_paths),
        "claim_id": str(claim_id or "") or None,
        "grant_id": grant_id,
        "granted_by": granted_by,
        "granted_to": granted_to,
        "issued_at": issued_at,
        "expires_at": expires_at,
        "max_body_bytes": int(max_body_bytes),
        "max_uses": int(max_uses),
        "metadata": dict(metadata or {}),
        "review_required_by_default": bool(review_required_by_default),
        "topic_id": str(topic_id or "") or None,
    }
    return json.dumps(body, sort_keys=True, separators=(",", ":")).encode("utf-8")


def build_hive_write_grant(
    *,
    granted_to: str,
    allowed_paths: list[str],
    topic_id: str | None = None,
    claim_id: str | None = None,
    max_uses: int = 1,
    max_body_bytes: int = 16384,
    review_required_by_default: bool = False,
    metadata: dict[str, Any] | None = None,
    grant_id: str | None = None,
    granted_by: str | None = None,
    issued_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> dict[str, Any]:
    active_peer_id = signer.get_local_peer_id()
    resolved_granted_by = str(granted_by or active_peer_id).strip()
    if resolved_granted_by != active_peer_id:
        raise ValueError("Local grant helper can only sign grants for the local peer id.")
    resolved_issued_at = issued_at or datetime.now(timezone.utc)
    resolved_expires_at = expires_at or (resolved_issued_at + timedelta(hours=12))
    issued_at_raw = resolved_issued_at.isoformat()
    expires_at_raw = resolved_expires_at.isoformat()
    resolved_grant_id = str(grant_id or uuid.uuid4().hex)
    clean_paths = [
        (path.rstrip("/") or "/")
        for path in list(allowed_paths or [])
        if str(path or "").strip()
    ]
    if not clean_paths:
        raise ValueError("Hive write grant requires at least one allowed path.")
    raw = canonical_hive_write_grant_bytes(
        grant_id=resolved_grant_id,
        granted_by=resolved_granted_by,
        granted_to=str(granted_to or "").strip(),
        allowed_paths=clean_paths,
        topic_id=str(topic_id or "").strip() or None,
        claim_id=str(claim_id or "").strip() or None,
        max_uses=int(max_uses),
        max_body_bytes=int(max_body_bytes),
        review_required_by_default=bool(review_required_by_default),
        metadata=dict(metadata or {}),
        issued_at=issued_at_raw,
        expires_at=expires_at_raw,
    )
    signature = signer.sign(raw)
    envelope = HiveWriteGrantEnvelope(
        grant_id=resolved_grant_id,
        granted_by=resolved_granted_by,
        granted_to=str(granted_to or "").strip(),
        allowed_paths=clean_paths,
        topic_id=str(topic_id or "").strip() or None,
        claim_id=str(claim_id or "").strip() or None,
        max_uses=int(max_uses),
        max_body_bytes=int(max_body_bytes),
        review_required_by_default=bool(review_required_by_default),
        metadata=dict(metadata or {}),
        issued_at=resolved_issued_at,
        expires_at=resolved_expires_at,
        signature=signature,
    )
    return {
        "grant_id": envelope.grant_id,
        "granted_by": envelope.granted_by,
        "granted_to": envelope.granted_to,
        "allowed_paths": list(envelope.allowed_paths),
        "topic_id": envelope.topic_id,
        "claim_id": envelope.claim_id,
        "max_uses": int(envelope.max_uses),
        "max_body_bytes": int(envelope.max_body_bytes),
        "review_required_by_default": bool(envelope.review_required_by_default),
        "metadata": dict(envelope.metadata),
        "issued_at": issued_at_raw,
        "expires_at": expires_at_raw,
        "signature": envelope.signature,
    }


def validate_hive_write_grant(
    *,
    raw_grant: dict[str, Any],
    target_path: str,
    signer_peer_id: str,
    payload: dict[str, Any],
    allowed_issuer_peer_ids: set[str] | None = None,
) -> HiveWriteGrantEnvelope:
    grant = HiveWriteGrantEnvelope.model_validate(raw_grant)
    now = datetime.now(timezone.utc)
    issued_at = grant.issued_at if grant.issued_at.tzinfo else grant.issued_at.replace(tzinfo=timezone.utc)
    expires_at = grant.expires_at if grant.expires_at.tzinfo else grant.expires_at.replace(tzinfo=timezone.utc)
    if issued_at > now + timedelta(minutes=10):
        raise ValueError("Hive write grant issued_at is in the future.")
    if expires_at < now:
        raise ValueError("Hive write grant has expired.")
    clean_target_path = target_path.rstrip("/") or "/"
    if clean_target_path not in {(item.rstrip("/") or "/") for item in grant.allowed_paths}:
        raise ValueError("Hive write grant does not allow this route.")
    if str(grant.granted_to or "") != str(signer_peer_id or ""):
        raise ValueError("Hive write grant is not issued to the signed write actor.")
    allowed_issuers = {str(item or "").strip() for item in list(allowed_issuer_peer_ids or set()) if str(item or "").strip()}
    if allowed_issuers and str(grant.granted_by or "") not in allowed_issuers:
        raise ValueError("Hive write grant issuer is not trusted for this server.")
    enforce_active_identity(str(grant.granted_by), scope="brain_hive")
    issued_raw = str(raw_grant.get("issued_at") or issued_at.isoformat())
    expires_raw = str(raw_grant.get("expires_at") or expires_at.isoformat())
    canonical = canonical_hive_write_grant_bytes(
        grant_id=grant.grant_id,
        granted_by=grant.granted_by,
        granted_to=grant.granted_to,
        allowed_paths=[(item.rstrip("/") or "/") for item in grant.allowed_paths],
        topic_id=grant.topic_id,
        claim_id=grant.claim_id,
        max_uses=grant.max_uses,
        max_body_bytes=grant.max_body_bytes,
        review_required_by_default=grant.review_required_by_default,
        metadata=dict(raw_grant.get("metadata") or grant.metadata or {}),
        issued_at=issued_raw,
        expires_at=expires_raw,
    )
    if not signer.verify(canonical, grant.signature, grant.granted_by):
        raise ValueError("Hive write grant signature is invalid.")
    if grant.topic_id and str(payload.get("topic_id") or "") != str(grant.topic_id):
        raise ValueError("Hive write grant is scoped to a different topic.")
    claim_id = str(payload.get("claim_id") or "").strip()
    if grant.claim_id and claim_id and claim_id != str(grant.claim_id):
        raise ValueError("Hive write grant is scoped to a different claim.")
    body_bytes = _payload_body_bytes(clean_target_path, payload)
    if body_bytes > int(grant.max_body_bytes):
        raise ValueError("Hive write payload exceeds the scoped grant body budget.")
    return grant


def consume_hive_write_grant(
    *,
    raw_grant: dict[str, Any],
    target_path: str,
    signer_peer_id: str,
    payload: dict[str, Any],
    allowed_issuer_peer_ids: set[str] | None = None,
) -> HiveWriteGrantEnvelope:
    _init_tables()
    grant = validate_hive_write_grant(
        raw_grant=raw_grant,
        target_path=target_path,
        signer_peer_id=signer_peer_id,
        payload=payload,
        allowed_issuer_peer_ids=allowed_issuer_peer_ids,
    )
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT grant_id, signature, status, used_count, max_uses, expires_at FROM hive_write_grants WHERE grant_id = ? LIMIT 1",
            (grant.grant_id,),
        ).fetchone()
        if row:
            if str(row["signature"] or "") != str(grant.signature or ""):
                raise ValueError("Hive write grant id collision detected.")
            if str(row["status"] or "active") != "active":
                raise ValueError("Hive write grant is not active.")
            row_expiry_raw = str(row["expires_at"] or "").strip()
            if row_expiry_raw:
                row_expiry = datetime.fromisoformat(row_expiry_raw.replace("Z", "+00:00"))
                if row_expiry.tzinfo is None:
                    row_expiry = row_expiry.replace(tzinfo=timezone.utc)
                if row_expiry < datetime.now(timezone.utc):
                    raise ValueError("Hive write grant stored state is expired.")
            used_count = int(row["used_count"] or 0)
            max_uses = int(row["max_uses"] or grant.max_uses)
            if used_count >= max_uses:
                raise ValueError("Hive write grant usage budget is exhausted.")
            conn.execute(
                """
                UPDATE hive_write_grants
                SET used_count = ?, updated_at = ?, last_used_at = ?
                WHERE grant_id = ?
                """,
                (used_count + 1, now, now, grant.grant_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO hive_write_grants (
                    grant_id, granted_by, granted_to, allowed_paths_json, topic_id, claim_id,
                    max_uses, used_count, max_body_bytes, review_required_by_default, metadata_json,
                    issued_at, expires_at, signature, status, created_at, updated_at, last_used_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (
                    grant.grant_id,
                    grant.granted_by,
                    grant.granted_to,
                    json.dumps(list(grant.allowed_paths), sort_keys=True),
                    str(grant.topic_id or ""),
                    str(grant.claim_id or ""),
                    int(grant.max_uses),
                    int(grant.max_body_bytes),
                    1 if grant.review_required_by_default else 0,
                    json.dumps(dict(grant.metadata or {}), sort_keys=True),
                    grant.issued_at.isoformat(),
                    grant.expires_at.isoformat(),
                    grant.signature,
                    now,
                    now,
                    now,
                ),
            )
        conn.commit()
        return grant
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _payload_body_bytes(target_path: str, payload: dict[str, Any]) -> int:
    if target_path == "/v1/hive/topics":
        title = str(payload.get("title") or "")
        summary = str(payload.get("summary") or "")
        return len((title + "\n" + summary).encode("utf-8"))
    body = str(payload.get("body") or payload.get("note") or "")
    return len(body.encode("utf-8"))
