from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from core.credit_ledger import get_credit_balance
from core.fraud_engine import slash_entry
from core.reward_engine import create_pending_assist_reward, finalize_confirmed_rewards, release_mature_pending_rewards
from storage.db import get_connection
from storage.migrations import run_migrations


class RewardEngineTests(unittest.TestCase):
    def setUp(self) -> None:
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
            ):
                try:
                    conn.execute(f"DELETE FROM {table}")
                except Exception:
                    continue
            conn.commit()
        finally:
            conn.close()

    def test_pending_reward_does_not_mint_compute_credits_before_release(self) -> None:
        helper_peer_id = f"peer-helper-{uuid.uuid4().hex}"
        parent_peer_id = f"peer-parent-{uuid.uuid4().hex}"
        task_id = f"task-{uuid.uuid4().hex}"

        reward = create_pending_assist_reward(
            task_id=task_id,
            parent_peer_id=parent_peer_id,
            helper_peer_id=helper_peer_id,
            helpfulness_score=0.95,
            quality_score=0.92,
            result_hash=f"hash-{uuid.uuid4().hex}",
        )

        self.assertEqual(reward.outcome, "pending")
        self.assertAlmostEqual(get_credit_balance(helper_peer_id), 0.0)

        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT outcome, finality_state, finality_depth, finality_target,
                       compute_credits_pending, compute_credits_released
                FROM contribution_ledger
                WHERE helper_peer_id = ?
                LIMIT 1
                """,
                (helper_peer_id,),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["outcome"], "pending")
        self.assertEqual(row["finality_state"], "pending")
        self.assertEqual(int(row["finality_depth"] or 0), 0)
        self.assertGreaterEqual(int(row["finality_target"] or 0), 1)
        self.assertGreater(float(row["compute_credits_pending"] or 0.0), 0.0)
        self.assertAlmostEqual(float(row["compute_credits_released"] or 0.0), 0.0)

        conn = get_connection()
        try:
            receipt = conn.execute(
                """
                SELECT stage, finality_state
                FROM contribution_proof_receipts
                WHERE entry_id = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (str(conn.execute("SELECT entry_id FROM contribution_ledger WHERE helper_peer_id = ? LIMIT 1", (helper_peer_id,)).fetchone()["entry_id"]),),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["stage"], "pending")
        self.assertEqual(receipt["finality_state"], "pending")

    def test_releasing_mature_reward_mints_compute_credits_once(self) -> None:
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
            row = conn.execute(
                """
                SELECT entry_id, compute_credits_pending
                FROM contribution_ledger
                WHERE helper_peer_id = ?
                LIMIT 1
                """,
                (helper_peer_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            entry_id = str(row["entry_id"])
            expected_credits = float(row["compute_credits_pending"] or 0.0)
            conn.execute(
                """
                UPDATE contribution_ledger
                SET fraud_window_end_ts = ?
                WHERE entry_id = ?
                """,
                ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), entry_id),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertGreater(expected_credits, 0.0)
        self.assertEqual(release_mature_pending_rewards(limit=10), 1)
        self.assertAlmostEqual(get_credit_balance(helper_peer_id), expected_credits)
        self.assertEqual(release_mature_pending_rewards(limit=10), 0)
        self.assertAlmostEqual(get_credit_balance(helper_peer_id), expected_credits)

        conn = get_connection()
        try:
            released = conn.execute(
                """
                SELECT outcome, finality_state, finality_depth, confirmed_at,
                       compute_credits_pending, compute_credits_released
                FROM contribution_ledger
                WHERE entry_id = ?
                LIMIT 1
                """,
                (entry_id,),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(released)
        self.assertEqual(released["outcome"], "released")
        self.assertEqual(released["finality_state"], "confirmed")
        self.assertEqual(int(released["finality_depth"] or 0), 1)
        self.assertTrue(str(released["confirmed_at"] or "").strip())
        self.assertAlmostEqual(float(released["compute_credits_pending"] or 0.0), 0.0)
        self.assertAlmostEqual(float(released["compute_credits_released"] or 0.0), expected_credits)

        conn = get_connection()
        try:
            stages = [
                str(row["stage"])
                for row in conn.execute(
                    """
                    SELECT stage
                    FROM contribution_proof_receipts
                    WHERE entry_id = ?
                    ORDER BY created_at ASC
                    """,
                    (entry_id,),
                ).fetchall()
            ]
        finally:
            conn.close()

        self.assertEqual(stages, ["pending", "confirmed"])

    def test_confirmed_reward_finalizes_after_quiet_window(self) -> None:
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
            row = conn.execute(
                """
                SELECT entry_id
                FROM contribution_ledger
                WHERE helper_peer_id = ?
                LIMIT 1
                """,
                (helper_peer_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            entry_id = str(row["entry_id"])
            conn.execute(
                """
                UPDATE contribution_ledger
                SET fraud_window_end_ts = ?
                WHERE entry_id = ?
                """,
                ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), entry_id),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(release_mature_pending_rewards(limit=10), 1)

        conn = get_connection()
        try:
            conn.execute(
                """
                UPDATE contribution_ledger
                SET confirmed_at = ?
                WHERE entry_id = ?
                """,
                ((datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(), entry_id),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(finalize_confirmed_rewards(limit=10), 1)

        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT outcome, finality_state, finality_depth, finalized_at
                FROM contribution_ledger
                WHERE entry_id = ?
                LIMIT 1
                """,
                (entry_id,),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["outcome"], "released")
        self.assertEqual(row["finality_state"], "finalized")
        self.assertGreaterEqual(int(row["finality_depth"] or 0), 2)
        self.assertTrue(str(row["finalized_at"] or "").strip())

        conn = get_connection()
        try:
            stages = [
                str(item["stage"])
                for item in conn.execute(
                    """
                    SELECT stage
                    FROM contribution_proof_receipts
                    WHERE entry_id = ?
                    ORDER BY created_at ASC
                    """,
                    (entry_id,),
                ).fetchall()
            ]
        finally:
            conn.close()

        self.assertEqual(stages, ["pending", "confirmed", "finalized"])

    def test_negative_review_after_confirmation_slashes_work(self) -> None:
        helper_peer_id = f"peer-helper-{uuid.uuid4().hex}"
        parent_peer_id = f"peer-parent-{uuid.uuid4().hex}"
        challenger_peer_id = f"peer-reviewer-{uuid.uuid4().hex}"
        task_id = f"task-{uuid.uuid4().hex}"

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
            conn.commit()
        finally:
            conn.close()

        create_pending_assist_reward(
            task_id=task_id,
            parent_peer_id=parent_peer_id,
            helper_peer_id=helper_peer_id,
            helpfulness_score=0.92,
            quality_score=0.91,
            result_hash=f"hash-{uuid.uuid4().hex}",
        )

        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT entry_id
                FROM contribution_ledger
                WHERE helper_peer_id = ?
                LIMIT 1
                """,
                (helper_peer_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            entry_id = str(row["entry_id"])
            conn.execute(
                """
                UPDATE contribution_ledger
                SET fraud_window_end_ts = ?
                WHERE entry_id = ?
                """,
                ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), entry_id),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(release_mature_pending_rewards(limit=10), 1)
        self.assertGreater(get_credit_balance(helper_peer_id), 0.0)

        conn = get_connection()
        try:
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
                    challenger_peer_id,
                    0.1,
                    0.1,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            conn.execute(
                """
                UPDATE contribution_ledger
                SET confirmed_at = ?
                WHERE entry_id = ?
                """,
                ((datetime.now(timezone.utc) - timedelta(hours=7)).isoformat(), entry_id),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(finalize_confirmed_rewards(limit=10), 0)
        self.assertAlmostEqual(get_credit_balance(helper_peer_id), 0.0)

        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT outcome, finality_state, slashed_flag
                FROM contribution_ledger
                WHERE entry_id = ?
                LIMIT 1
                """,
                (entry_id,),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["outcome"], "slashed")
        self.assertEqual(row["finality_state"], "slashed")
        self.assertEqual(int(row["slashed_flag"] or 0), 1)

        conn = get_connection()
        try:
            receipt = conn.execute(
                """
                SELECT stage, challenge_reason
                FROM contribution_proof_receipts
                WHERE entry_id = ?
                ORDER BY created_at DESC, rowid DESC
                LIMIT 1
                """,
                (entry_id,),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(receipt)
        self.assertEqual(receipt["stage"], "slashed")
        self.assertIn("finality_rejected", str(receipt["challenge_reason"] or ""))

    def test_slashed_released_reward_claws_back_compute_credits(self) -> None:
        helper_peer_id = f"peer-helper-{uuid.uuid4().hex}"
        parent_peer_id = f"peer-parent-{uuid.uuid4().hex}"
        task_id = f"task-{uuid.uuid4().hex}"

        create_pending_assist_reward(
            task_id=task_id,
            parent_peer_id=parent_peer_id,
            helper_peer_id=helper_peer_id,
            helpfulness_score=0.94,
            quality_score=0.93,
            result_hash=f"hash-{uuid.uuid4().hex}",
        )

        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT entry_id
                FROM contribution_ledger
                WHERE helper_peer_id = ?
                LIMIT 1
                """,
                (helper_peer_id,),
            ).fetchone()
            self.assertIsNotNone(row)
            entry_id = str(row["entry_id"])
            conn.execute(
                """
                UPDATE contribution_ledger
                SET fraud_window_end_ts = ?
                WHERE entry_id = ?
                """,
                ((datetime.now(timezone.utc) - timedelta(hours=1)).isoformat(), entry_id),
            )
            conn.commit()
        finally:
            conn.close()

        self.assertEqual(release_mature_pending_rewards(limit=10), 1)
        self.assertGreater(get_credit_balance(helper_peer_id), 0.0)

        slash_entry(entry_id, reason="fraud-confirmed", severity=1.0)

        self.assertAlmostEqual(get_credit_balance(helper_peer_id), 0.0)
        conn = get_connection()
        try:
            row = conn.execute(
                """
                SELECT outcome, finality_state, slashed_flag, compute_credits_pending
                FROM contribution_ledger
                WHERE entry_id = ?
                LIMIT 1
                """,
                (entry_id,),
            ).fetchone()
        finally:
            conn.close()

        self.assertIsNotNone(row)
        self.assertEqual(row["outcome"], "slashed")
        self.assertEqual(row["finality_state"], "slashed")
        self.assertEqual(int(row["slashed_flag"] or 0), 1)
        self.assertAlmostEqual(float(row["compute_credits_pending"] or 0.0), 0.0)


if __name__ == "__main__":
    unittest.main()
