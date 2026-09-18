from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from core.reward_engine import create_pending_assist_reward, finalize_confirmed_rewards, release_mature_pending_rewards
from storage.db import get_connection
from storage.migrations import run_migrations


def _reset_reward_tables() -> None:
    run_migrations()
    conn = get_connection()
    try:
        for table in (
            "compute_credit_ledger",
            "contribution_proof_receipts",
            "contribution_ledger",
            "anti_abuse_signals",
            "scoreboard",
            "scoreboard_events",
            "task_results",
            "task_offers",
            "peers",
            "task_reviews",
        ):
            try:
                conn.execute(f"DELETE FROM {table}")
            except Exception:
                continue
        conn.commit()
    finally:
        conn.close()


def test_reward_release_and_finalization_require_ordered_stages() -> None:
    _reset_reward_tables()
    helper_peer_id = f"peer-helper-{uuid.uuid4().hex}"
    parent_peer_id = f"peer-parent-{uuid.uuid4().hex}"
    task_id = f"task-{uuid.uuid4().hex}"

    create_pending_assist_reward(
        task_id=task_id,
        parent_peer_id=parent_peer_id,
        helper_peer_id=helper_peer_id,
        helpfulness_score=0.93,
        quality_score=0.94,
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

    conn = get_connection()
    try:
        conn.execute(
            "UPDATE contribution_ledger SET confirmed_at = ? WHERE entry_id = ?",
            ((datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(), entry_id),
        )
        conn.commit()
    finally:
        conn.close()

    assert finalize_confirmed_rewards(limit=10) == 1


def test_negative_review_blocks_reward_finalization() -> None:
    _reset_reward_tables()
    helper_peer_id = f"peer-helper-{uuid.uuid4().hex}"
    parent_peer_id = f"peer-parent-{uuid.uuid4().hex}"
    reviewer_peer_id = f"peer-reviewer-{uuid.uuid4().hex}"
    task_id = f"task-{uuid.uuid4().hex}"

    create_pending_assist_reward(
        task_id=task_id,
        parent_peer_id=parent_peer_id,
        helper_peer_id=helper_peer_id,
        helpfulness_score=0.95,
        quality_score=0.93,
        result_hash=f"hash-{uuid.uuid4().hex}",
    )

    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO task_offers (
                task_id, parent_peer_id, capsule_id, task_type, subtask_type, summary,
                input_capsule_hash, required_capabilities_json, reward_hint_json, max_helpers,
                priority, deadline_ts, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '[]', '{}', 1, 'high', ?, 'open', ?, ?)
            """,
            (
                task_id,
                parent_peer_id,
                f"capsule-{uuid.uuid4().hex}",
                "research",
                "research",
                "Late review challenge for confirmed work.",
                f"hash-{uuid.uuid4().hex}",
                "2026-03-10T12:00:00+00:00",
                "2026-03-10T10:00:00+00:00",
                "2026-03-10T10:00:00+00:00",
            ),
        )
        row = conn.execute(
            "SELECT entry_id FROM contribution_ledger WHERE helper_peer_id = ? LIMIT 1",
            (helper_peer_id,),
        ).fetchone()
        entry_id = str(row["entry_id"])
        conn.execute(
            "UPDATE contribution_ledger SET fraud_window_end_ts = ?, confirmed_at = ? WHERE entry_id = ?",
            (
                (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(),
                (datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(),
                entry_id,
            ),
        )
        conn.execute(
            """
            INSERT INTO task_reviews (
                review_id, task_id, helper_peer_id, reviewer_peer_id, outcome,
                helpfulness_score, quality_score, harmful_flag, created_at
            ) VALUES (?, ?, ?, ?, 'rejected', ?, ?, 1, ?)
            """,
            (
                f"review-{uuid.uuid4().hex}",
                task_id,
                helper_peer_id,
                reviewer_peer_id,
                0.1,
                0.1,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        conn.commit()
    finally:
        conn.close()

    assert release_mature_pending_rewards(limit=10) == 1
    assert finalize_confirmed_rewards(limit=10) == 0
