from __future__ import annotations

from datetime import datetime, timezone

from core import audit_logger, policy_engine
from storage.db import get_connection


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_peer_row(peer_id: str) -> None:
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
                    peer_id, display_alias, trust_score,
                    successful_shards, failed_shards, strike_count,
                    status, last_seen_at, created_at, updated_at
                ) VALUES (?, ?, ?, 0, 0, 0, 'active', ?, ?, ?)
                """,
                (
                    peer_id,
                    None,
                    float(policy_engine.get("trust.initial_peer_trust", 0.50)),
                    now,
                    now,
                    now,
                ),
            )
            conn.commit()
    finally:
        conn.close()


def is_peer_quarantined(peer_id: str) -> bool:
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT status FROM peers WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()
        if not row:
            return False
        return row["status"] in {"quarantined", "blocked"}
    finally:
        conn.close()


def quarantine_peer(peer_id: str, reason: str) -> None:
    _ensure_peer_row(peer_id)

    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE peers
            SET status = 'quarantined',
                strike_count = strike_count + 1,
                updated_at = ?
            WHERE peer_id = ?
            """,
            (_utcnow(), peer_id),
        )
        conn.commit()
    finally:
        conn.close()

    audit_logger.log(
        "peer_quarantined",
        target_id=peer_id,
        target_type="peer",
        details={"reason": reason},
    )


def note_peer_violation(peer_id: str, reason: str) -> None:
    _ensure_peer_row(peer_id)

    limit = int(policy_engine.get("network.max_failed_messages_before_quarantine", 3))
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE peers
            SET strike_count = strike_count + 1,
                updated_at = ?
            WHERE peer_id = ?
            """,
            (_utcnow(), peer_id),
        )

        row = conn.execute(
            "SELECT strike_count FROM peers WHERE peer_id = ? LIMIT 1",
            (peer_id,),
        ).fetchone()

        if row and int(row["strike_count"]) >= limit:
            conn.execute(
                """
                UPDATE peers
                SET status = 'quarantined',
                    updated_at = ?
                WHERE peer_id = ?
                """,
                (_utcnow(), peer_id),
            )

        conn.commit()
    finally:
        conn.close()

    audit_logger.log(
        "peer_violation",
        target_id=peer_id,
        target_type="peer",
        details={"reason": reason},
    )


def filter_inbound_candidates(candidates: list[dict]) -> list[dict]:
    """
    Filters remote candidates before ranking.
    Drops:
    - non-dicts
    - quarantined shards
    - low-trust shards
    - shards with blocked risk flags
    - shards from quarantined peers
    """
    if not isinstance(candidates, list):
        return []

    min_trust = float(policy_engine.get("trust.min_trust_to_consider_shard", 0.30))
    blocked_flags = set(policy_engine.get("shards.quarantine_if_risk_flags_include", []))

    safe: list[dict] = []

    for candidate in candidates:
        if not isinstance(candidate, dict):
            continue

        if candidate.get("quarantine_status") in {"quarantined", "expired"}:
            continue

        trust_score = float(candidate.get("trust_score", 0.0))
        if trust_score < min_trust:
            continue

        risk_flags = candidate.get("risk_flags") or []
        if any(flag in blocked_flags for flag in risk_flags):
            continue

        source_node_id = candidate.get("source_node_id")
        if source_node_id and is_peer_quarantined(source_node_id):
            continue

        safe.append(candidate)

    return safe
