from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from storage.db import get_connection


@dataclass(frozen=True)
class ChallengeRecord:
    challenge_id: str
    peer_id: str
    challenge_type: str
    expected_hash: str
    status: str


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _init_table() -> None:
    conn = get_connection()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS execution_challenges (
                challenge_id TEXT PRIMARY KEY,
                peer_id TEXT NOT NULL,
                challenge_type TEXT NOT NULL,
                expected_hash TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def issue_challenge(peer_id: str, challenge_type: str, payload: dict) -> ChallengeRecord:
    _init_table()
    challenge_id = str(uuid.uuid4())
    expected_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO execution_challenges (
                challenge_id, peer_id, challenge_type, expected_hash, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, 'issued', ?, ?)
            """,
            (challenge_id, peer_id, challenge_type, expected_hash, _utcnow(), _utcnow()),
        )
        conn.commit()
    finally:
        conn.close()
    return ChallengeRecord(challenge_id, peer_id, challenge_type, expected_hash, "issued")


def resolve_challenge(challenge_id: str, observed_payload: dict) -> bool:
    _init_table()
    observed_hash = hashlib.sha256(json.dumps(observed_payload, sort_keys=True).encode("utf-8")).hexdigest()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT expected_hash FROM execution_challenges WHERE challenge_id = ? LIMIT 1",
            (challenge_id,),
        ).fetchone()
        if not row:
            return False
        success = observed_hash == row["expected_hash"]
        conn.execute(
            "UPDATE execution_challenges SET status = ?, updated_at = ? WHERE challenge_id = ?",
            ("passed" if success else "failed", _utcnow(), challenge_id),
        )
        conn.commit()
        return success
    finally:
        conn.close()
