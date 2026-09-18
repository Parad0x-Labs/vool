from __future__ import annotations

from datetime import datetime, timedelta, timezone

from core import policy_engine
from storage.db import get_connection


def predict_local_override_necessity(complexity_score: float = 1.0) -> bool:
    """
    If the swarm has NO peers seen in the last 15 minutes, we MUST override
    decomposition and run the task locally, otherwise it stalls infinitely.
    """
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=15)).isoformat()
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT COUNT(DISTINCT peer_id) as cnt FROM peers WHERE last_seen_at >= ?",
            (cutoff,)
        ).fetchone()
        count = int(row["cnt"]) if row else 0
        if count < 1 and bool(policy_engine.get("orchestration.enable_local_worker_pool_when_swarm_empty", True)):
            # Allow decomposition to proceed when local worker-pool mode is enabled.
            return False
        return count < 1
    finally:
        conn.close()
