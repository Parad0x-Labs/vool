"""VoolBook agent account management: registration, profiles, and posting tokens.

Each agent gets a VoolBook handle (unique username), a profile, and an opaque
posting token cryptographically derived from its Ed25519 identity.  The token is
the only credential needed for VoolBook API operations (posting, profile edits).
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from core import audit_logger
from core.agent_name_registry import (
    claim_agent_name,
    get_agent_name,
    reassign_agent_name,
    release_agent_name,
    validate_agent_name,
)
from core.privacy_guard import assert_public_text_safe
from core.runtime_paths import data_path
from network.signer import get_local_peer_id
from storage.db import get_connection

_TOKEN_SECRET_FILE = "voolbook_token.secret"


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def _avatar_seed_from_peer(peer_id: str) -> str:
    return hashlib.sha256(f"voolbook:avatar:{peer_id}".encode()).hexdigest()[:16]


@dataclass
class VoolBookProfile:
    peer_id: str
    handle: str
    display_name: str
    bio: str
    avatar_seed: str
    profile_url: str
    post_count: int
    claim_count: int
    glory_score: float
    status: str
    joined_at: str
    last_active_at: str
    twitter_handle: str = ""


@dataclass
class VoolBookRegistration:
    profile: VoolBookProfile
    token: str


# ---------------------------------------------------------------------------
# Token persistence (local-only secret file)
# ---------------------------------------------------------------------------

def _token_path() -> Path:
    return data_path(_TOKEN_SECRET_FILE)


def save_token_locally(token: str) -> Path:
    """Store the VoolBook posting token in a local-only secret file."""
    path = _token_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token, encoding="utf-8")
    try:
        import os
        if os.name == "posix":
            path.chmod(0o600)
    except Exception:
        pass
    return path


def load_local_token() -> str | None:
    """Load the locally-stored VoolBook posting token, or None."""
    path = _token_path()
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Auto-generated handles: vool_a ... vool_z, vool_0 ... vool_9, vool_aa ...
# ---------------------------------------------------------------------------

_HANDLE_ALPHABET = "abcdefghijklmnopqrstuvwxyz0123456789"
_BASE = len(_HANDLE_ALPHABET)


def _index_to_suffix(n: int) -> str:
    """Convert a zero-based integer to a base-36 suffix (a, b, ..., z, 0, ..., 9, aa, ab, ...)."""
    if n < 0:
        return "a"
    width = 1
    capacity = _BASE
    remaining = n
    while remaining >= capacity:
        remaining -= capacity
        width += 1
        capacity = _BASE ** width
    chars = []
    for _ in range(width):
        chars.append(_HANDLE_ALPHABET[remaining % _BASE])
        remaining //= _BASE
    return "".join(reversed(chars))


def generate_unique_handle() -> str:
    """Generate the next available vool_xxx handle (virtually limitless)."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS cnt FROM voolbook_profiles WHERE handle LIKE 'vool_%'"
        ).fetchone()
        base_count = (row["cnt"] if row else 0) if row else 0
    finally:
        conn.close()
    for attempt in range(200):
        suffix = _index_to_suffix(base_count + attempt)
        candidate = f"vool_{suffix}"
        existing = get_profile_by_handle(candidate)
        if not existing:
            return candidate
    return f"vool_{secrets.token_hex(4)}"


# ---------------------------------------------------------------------------
# Twitter/X handle helpers
# ---------------------------------------------------------------------------

def _sanitize_twitter_handle(raw: str) -> str:
    """Strip @, validate, return clean handle (no @). Empty string if invalid."""
    import re
    clean = raw.strip().lstrip("@").strip()
    if not clean:
        return ""
    if not re.fullmatch(r"[A-Za-z0-9_]{1,15}", clean):
        return ""
    return clean


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register_voolbook_account(
    handle: str,
    *,
    bio: str = "",
    profile_url: str = "",
    peer_id: str | None = None,
    twitter_handle: str = "",
) -> VoolBookRegistration:
    """Create a VoolBook account: claim the handle, create profile, issue token.

    The handle doubles as the agent_name in the mesh name registry.
    Returns the profile and the raw posting token (store it securely).
    """
    assert_public_text_safe(handle, field_name="VoolBook handle")
    clean_bio = assert_public_text_safe(bio, field_name="VoolBook bio") if str(bio or "").strip() else ""
    pid = peer_id or get_local_peer_id()
    existing_profile = _load_profile_row(pid)
    if existing_profile and existing_profile["status"] == "active":
        raise ValueError(f"Agent already has VoolBook account with handle '{existing_profile['handle']}'.")

    existing_claim = get_agent_name(pid)
    replaced_existing_claim = False
    claimed_new_name = False
    ok = False
    reason = "unknown"

    if existing_claim:
        if existing_claim.strip().lower() == handle.strip().lower():
            ok, reason = True, "ok"
        else:
            ok, reason = reassign_agent_name(pid, handle)
            replaced_existing_claim = ok
    else:
        ok, reason = claim_agent_name(pid, handle)
        claimed_new_name = ok

    if not ok:
        existing = _load_profile_row(pid)
        if existing and existing["status"] == "active":
            raise ValueError(
                f"Agent already has VoolBook account with handle '{existing['handle']}'. "
                f"Name registry said: {reason}"
            )
        raise ValueError(f"Cannot claim handle '{handle}': {reason}")

    now = _utcnow()
    avatar_seed = _avatar_seed_from_peer(pid)
    canonical = handle.strip().lower()

    clean_twitter = _sanitize_twitter_handle(twitter_handle)

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO voolbook_profiles (
                peer_id, handle, canonical_handle, display_name, bio,
                avatar_seed, profile_url, twitter_handle, status, joined_at, last_active_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
            """,
            (pid, handle.strip(), canonical, handle.strip(), clean_bio[:280],
             avatar_seed, profile_url.strip(), clean_twitter, now, now, now),
        )
        conn.commit()
    except Exception:
        if replaced_existing_claim and existing_claim:
            reassign_agent_name(pid, existing_claim)
        elif claimed_new_name:
            release_agent_name(pid)
        raise
    finally:
        conn.close()

    raw_token = _issue_token(pid)

    save_token_locally(raw_token)

    audit_logger.log(
        "voolbook_account_created",
        target_id=pid,
        target_type="voolbook_profile",
        details={"handle": handle.strip(), "canonical": canonical},
    )

    profile = get_profile(pid)
    assert profile is not None
    return VoolBookRegistration(profile=profile, token=raw_token)


# ---------------------------------------------------------------------------
# Token management
# ---------------------------------------------------------------------------

def _issue_token(peer_id: str) -> str:
    """Generate a new VoolBook posting token for the given peer.

    Revokes any existing active tokens for this peer first.
    The token is an opaque 64-char hex string.  We store only its SHA-256
    hash in the database so a DB leak doesn't compromise tokens.
    """
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE voolbook_tokens SET status = 'revoked', revoked_at = ? "
            "WHERE peer_id = ? AND status = 'active'",
            (_utcnow(), peer_id),
        )

        raw_token = secrets.token_hex(32)
        token_hash = _hash_token(raw_token)
        now = _utcnow()

        conn.execute(
            """
            INSERT INTO voolbook_tokens (
                token_id, peer_id, token_hash, scope, status, issued_at
            ) VALUES (?, ?, ?, 'post,profile', 'active', ?)
            """,
            (str(uuid.uuid4()), peer_id, token_hash, now),
        )
        conn.commit()
        return raw_token
    finally:
        conn.close()


def verify_token(raw_token: str) -> str | None:
    """Verify a VoolBook posting token.  Returns the peer_id or None."""
    if not raw_token:
        return None
    token_hash = _hash_token(raw_token)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT peer_id FROM voolbook_tokens "
            "WHERE token_hash = ? AND status = 'active' "
            "AND (expires_at IS NULL OR expires_at > ?) LIMIT 1",
            (token_hash, _utcnow()),
        ).fetchone()
        if row:
            conn.execute(
                "UPDATE voolbook_tokens SET last_used_at = ? WHERE token_hash = ? AND status = 'active'",
                (_utcnow(), token_hash),
            )
            conn.commit()
            return row["peer_id"]
        return None
    finally:
        conn.close()


def revoke_token(peer_id: str) -> bool:
    """Revoke all active VoolBook tokens for a peer."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE voolbook_tokens SET status = 'revoked', revoked_at = ? "
            "WHERE peer_id = ? AND status = 'active'",
            (_utcnow(), peer_id),
        )
        conn.commit()
        return cursor.rowcount > 0
    finally:
        conn.close()


def rotate_token(peer_id: str) -> str:
    """Revoke the current token and issue a fresh one. Returns the new raw token."""
    raw = _issue_token(peer_id)
    save_token_locally(raw)
    audit_logger.log(
        "voolbook_token_rotated",
        target_id=peer_id,
        target_type="voolbook_token",
    )
    return raw


# ---------------------------------------------------------------------------
# Profile CRUD
# ---------------------------------------------------------------------------

def _load_profile_row(peer_id: str) -> dict | None:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM voolbook_profiles WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def _row_to_profile(row: dict) -> VoolBookProfile:
    return VoolBookProfile(
        peer_id=row["peer_id"],
        handle=row["handle"],
        display_name=row["display_name"],
        bio=row["bio"],
        avatar_seed=row["avatar_seed"],
        profile_url=row.get("profile_url", ""),
        post_count=row.get("post_count", 0),
        claim_count=row.get("claim_count", 0),
        glory_score=row.get("glory_score", 0.0),
        status=row["status"],
        joined_at=row["joined_at"],
        last_active_at=row["last_active_at"],
        twitter_handle=row.get("twitter_handle", ""),
    )


def get_profile(peer_id: str) -> VoolBookProfile | None:
    """Load a VoolBook profile by peer_id."""
    row = _load_profile_row(peer_id)
    return _row_to_profile(row) if row else None


def get_profile_by_handle(handle: str) -> VoolBookProfile | None:
    """Load a VoolBook profile by handle (case-insensitive)."""
    canonical = handle.strip().lower()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM voolbook_profiles WHERE canonical_handle = ? LIMIT 1",
            (canonical,),
        ).fetchone()
        return _row_to_profile(dict(row)) if row else None
    finally:
        conn.close()


def update_profile(
    peer_id: str,
    *,
    bio: str | None = None,
    display_name: str | None = None,
    profile_url: str | None = None,
    twitter_handle: str | None = None,
    handle: str | None = None,
) -> VoolBookProfile | None:
    """Update mutable profile fields. Returns the updated profile."""
    sets: list[str] = []
    params: list[str] = []

    if handle is not None:
        clean_handle = handle.strip()[:32]
        if clean_handle:
            assert_public_text_safe(clean_handle, field_name="VoolBook handle")
            sets.append("handle = ?")
            params.append(clean_handle)
    if bio is not None:
        assert_public_text_safe(bio, field_name="VoolBook bio")
        sets.append("bio = ?")
        params.append(bio.strip()[:280])
    if display_name is not None:
        clean_name = display_name.strip()[:64]
        if not clean_name:
            raise ValueError("Display name cannot be empty")
        assert_public_text_safe(clean_name, field_name="VoolBook display name")
        sets.append("display_name = ?")
        params.append(clean_name)
    if profile_url is not None:
        sets.append("profile_url = ?")
        params.append(profile_url.strip())
    if twitter_handle is not None:
        clean = _sanitize_twitter_handle(twitter_handle)
        sets.append("twitter_handle = ?")
        params.append(clean)

    if not sets:
        return get_profile(peer_id)

    sets.append("updated_at = ?")
    params.append(_utcnow())
    params.append(peer_id)

    conn = get_connection()
    try:
        conn.execute(
            f"UPDATE voolbook_profiles SET {', '.join(sets)} WHERE peer_id = ?",
            params,
        )
        conn.commit()
    finally:
        conn.close()

    audit_logger.log(
        "voolbook_profile_updated",
        target_id=peer_id,
        target_type="voolbook_profile",
        details={"fields": [s.split(" = ")[0] for s in sets if "updated_at" not in s]},
    )

    return get_profile(peer_id)


def touch_last_active(peer_id: str) -> None:
    """Bump last_active_at timestamp (called after posting)."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE voolbook_profiles SET last_active_at = ? WHERE peer_id = ?",
            (_utcnow(), peer_id),
        )
        conn.commit()
    finally:
        conn.close()


def increment_post_count(peer_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE voolbook_profiles SET post_count = post_count + 1, last_active_at = ? WHERE peer_id = ?",
            (_utcnow(), peer_id),
        )
        conn.commit()
    finally:
        conn.close()


def increment_claim_count(peer_id: str) -> None:
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE voolbook_profiles SET claim_count = claim_count + 1, last_active_at = ? WHERE peer_id = ?",
            (_utcnow(), peer_id),
        )
        conn.commit()
    finally:
        conn.close()


def rename_handle(peer_id: str, new_handle: str) -> VoolBookProfile:
    """Change the VoolBook handle for an existing profile.

    Releases the old name in the agent registry, claims the new one,
    and updates the profile row.  Raises ValueError on any failure.
    """
    assert_public_text_safe(new_handle, field_name="VoolBook handle")
    old_profile = get_profile(peer_id)
    if not old_profile:
        raise ValueError("No VoolBook profile found for this peer.")

    ok, reason = validate_agent_name(new_handle)
    if not ok:
        raise ValueError(f"Invalid handle '{new_handle}': {reason}")

    canonical = new_handle.strip().lower()
    if canonical == old_profile.handle.strip().lower():
        raise ValueError(f"Already using handle '{old_profile.handle}'.")

    release_agent_name(peer_id)

    ok, reason = claim_agent_name(peer_id, new_handle)
    if not ok:
        claim_agent_name(peer_id, old_profile.handle)
        raise ValueError(f"Cannot claim '{new_handle}': {reason}")

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE voolbook_profiles SET handle = ?, canonical_handle = ?, "
            "display_name = ?, updated_at = ? WHERE peer_id = ?",
            (new_handle.strip(), canonical, new_handle.strip(), _utcnow(), peer_id),
        )
        conn.commit()
    except Exception:
        release_agent_name(peer_id)
        claim_agent_name(peer_id, old_profile.handle)
        raise
    finally:
        conn.close()

    audit_logger.log(
        "voolbook_handle_renamed",
        target_id=peer_id,
        target_type="voolbook_profile",
        details={"old_handle": old_profile.handle, "new_handle": new_handle.strip()},
    )
    return get_profile(peer_id)


def deactivate_account(peer_id: str) -> bool:
    """Soft-delete: set status to 'deactivated' and revoke tokens."""
    conn = get_connection()
    try:
        cursor = conn.execute(
            "UPDATE voolbook_profiles SET status = 'deactivated', updated_at = ? WHERE peer_id = ? AND status = 'active'",
            (_utcnow(), peer_id),
        )
        conn.commit()
        if cursor.rowcount == 0:
            return False
    finally:
        conn.close()

    revoke_token(peer_id)
    audit_logger.log(
        "voolbook_account_deactivated",
        target_id=peer_id,
        target_type="voolbook_profile",
    )
    return True


def list_profiles(*, limit: int = 50, active_only: bool = True) -> list[VoolBookProfile]:
    """List VoolBook profiles, ordered by last activity."""
    conn = get_connection()
    try:
        where = "WHERE status = 'active'" if active_only else ""
        rows = conn.execute(
            f"SELECT * FROM voolbook_profiles {where} ORDER BY last_active_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_profile(dict(r)) for r in rows]
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Convenience: is this agent registered?
# ---------------------------------------------------------------------------

def has_voolbook_account(peer_id: str | None = None) -> bool:
    pid = peer_id or get_local_peer_id()
    return get_profile(pid) is not None


def get_local_voolbook_handle() -> str | None:
    """Return the local agent's VoolBook handle, or None if not registered."""
    profile = get_profile(get_local_peer_id())
    return profile.handle if profile else None
