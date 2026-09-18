from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from core import audit_logger
from storage.db import get_connection

# ---------------------------------------------------------------------------
# Validation rules
# ---------------------------------------------------------------------------

MIN_NAME_LENGTH = 3
MAX_NAME_LENGTH = 32
NAME_PATTERN = re.compile(r"^[a-zA-Z0-9_\-]+$")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize(name: str) -> str:
    """
    Canonical form = just lowercase.
    'baby-vool' and 'baby_vool' are DIFFERENT names.
    'baby-vool' and 'BaBY-nullA' are the SAME name.
    """
    return name.strip().lower()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate_agent_name(name: str) -> tuple[bool, str]:
    """
    Validates a proposed agent name for the MESH leaderboard.
    Locally, users can name their agent anything they want.
    Returns (is_valid, reason).
    """
    stripped = name.strip()

    if len(stripped) < MIN_NAME_LENGTH:
        return False, f"Name must be at least {MIN_NAME_LENGTH} characters."

    if len(stripped) > MAX_NAME_LENGTH:
        return False, f"Name must be at most {MAX_NAME_LENGTH} characters."

    if not NAME_PATTERN.match(stripped):
        return False, "Name may only contain letters, numbers, hyphens, and underscores. No spaces."

    return True, "ok"


def claim_agent_name(peer_id: str, name: str) -> tuple[bool, str]:
    """
    Attempts to claim a unique agent name for the given peer_id.
    First-come-first-served. Once claimed, the name is bound to the key.

    Returns (success, message).
    """
    valid, reason = validate_agent_name(name)
    if not valid:
        return False, reason

    canonical = _normalize(name)
    display = name.strip()

    conn = get_connection()
    try:
        # Check if this peer already has a name
        existing = conn.execute(
            "SELECT display_name FROM agent_names WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()

        if existing:
            return False, f"You already claimed the name '{existing['display_name']}'. Release it first."

        # Check if the canonical name is already taken by anyone
        taken = conn.execute(
            "SELECT peer_id, display_name FROM agent_names WHERE canonical_name = ? LIMIT 1",
            (canonical,),
        ).fetchone()

        if taken:
            return False, f"Name '{taken['display_name']}' is already claimed by another agent."

        # Claim it
        conn.execute(
            """
            INSERT INTO agent_names (
                entry_id, peer_id, display_name, canonical_name, claimed_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (str(uuid.uuid4()), peer_id, display, canonical, _utcnow()),
        )
        conn.commit()

        audit_logger.log(
            "agent_name_claimed",
            target_id=peer_id,
            target_type="peer",
            details={"display_name": display, "canonical": canonical},
        )

        return True, f"Name '{display}' claimed successfully."

    finally:
        conn.close()


def reassign_agent_name(peer_id: str, name: str) -> tuple[bool, str]:
    """
    Atomically replace the currently claimed name for this peer.

    If the peer has no existing name, this behaves like claim_agent_name().
    """
    valid, reason = validate_agent_name(name)
    if not valid:
        return False, reason

    canonical = _normalize(name)
    display = name.strip()

    conn = get_connection()
    try:
        existing = conn.execute(
            "SELECT display_name, canonical_name FROM agent_names WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()
        if not existing:
            return claim_agent_name(peer_id, display)

        taken = conn.execute(
            "SELECT peer_id, display_name FROM agent_names WHERE canonical_name = ? LIMIT 1",
            (canonical,),
        ).fetchone()
        if taken and taken["peer_id"] != peer_id:
            return False, f"Name '{taken['display_name']}' is already claimed by another agent."

        conn.execute(
            """
            UPDATE agent_names
            SET display_name = ?, canonical_name = ?, claimed_at = ?
            WHERE peer_id = ?
            """,
            (display, canonical, _utcnow(), peer_id),
        )
        conn.commit()

        audit_logger.log(
            "agent_name_reassigned",
            target_id=peer_id,
            target_type="peer",
            details={
                "old_display_name": existing["display_name"],
                "new_display_name": display,
                "canonical": canonical,
            },
        )
        return True, f"Name '{display}' claimed successfully."
    finally:
        conn.close()


def release_agent_name(peer_id: str) -> bool:
    """
    Releases the name claimed by this peer, freeing it for others.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT display_name FROM agent_names WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()

        if not row:
            return False

        conn.execute("DELETE FROM agent_names WHERE peer_id = ?", (peer_id,))
        conn.commit()

        audit_logger.log(
            "agent_name_released",
            target_id=peer_id,
            target_type="peer",
            details={"display_name": row["display_name"]},
        )
        return True
    finally:
        conn.close()


def get_agent_name(peer_id: str) -> str | None:
    """
    Returns the display name for a peer, or None if unclaimed.
    """
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT display_name FROM agent_names WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()
        return row["display_name"] if row else None
    finally:
        conn.close()


def get_peer_by_name(name: str) -> str | None:
    """
    Reverse lookup: returns the peer_id that owns the given name, or None.
    """
    canonical = _normalize(name)
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT peer_id FROM agent_names WHERE canonical_name = ? LIMIT 1",
            (canonical,),
        ).fetchone()
        return row["peer_id"] if row else None
    finally:
        conn.close()


def list_agent_names(*, limit: int = 200) -> list[dict[str, str]]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT peer_id, display_name FROM agent_names ORDER BY display_name LIMIT ?",
            (max(1, min(limit, 1000)),),
        ).fetchall()
        return [{"peer_id": row["peer_id"], "display_name": row["display_name"]} for row in rows]
    finally:
        conn.close()
