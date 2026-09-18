from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from core.reward_engine import create_pending_assist_reward, release_mature_pending_rewards
from storage.db import get_connection
from storage.migrations import run_migrations


def _reset_reward_tables() -> None:
    run_migrations()
    conn = get_connection()
    try:
        for table in ("compute_credit_ledger", "contribution_proof_receipts", "contribution_ledger", "peers"):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()


def test_duplicate_reward_release_is_replay_safe() -> None:
    _reset_reward_tables()
    helper_peer_id = f"peer-helper-{uuid.uuid4().hex}"
    parent_peer_id = f"peer-parent-{uuid.uuid4().hex}"
    task_id = f"task-{uuid.uuid4().hex}"

    create_pending_assist_reward(
        task_id=task_id,
        parent_peer_id=parent_peer_id,
        helper_peer_id=helper_peer_id,
        helpfulness_score=0.91,
        quality_score=0.89,
        result_hash=f"hash-{uuid.uuid4().hex}",
    )

    conn = get_connection()
    try:
        entry_id = str(
            conn.execute(
                "SELECT entry_id FROM contribution_ledger WHERE helper_peer_id = ? LIMIT 1",
                (helper_peer_id,),
            ).fetchone()["entry_id"]
        )
        conn.execute(
            "UPDATE contribution_ledger SET fraud_window_end_ts = ? WHERE entry_id = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), entry_id),
        )
        conn.commit()
    finally:
        conn.close()

    assert release_mature_pending_rewards(limit=10) == 1
    assert release_mature_pending_rewards(limit=10) == 0
