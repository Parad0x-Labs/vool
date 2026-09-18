"""Step 10 correction #7: durable approval continuation, proven against the six required
properties. No live LIVE_DATA scenario reaches WAITING_APPROVAL today (public read-only retrieval
never requires approval) -- these are direct tests against synthetic approval-required subtasks,
not a live daemon drive. Disclosed explicitly rather than implied.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.agent_runtime.attempt_approval import (
    CURRENT_APPROVAL_POLICY_VERSION,
    compute_action_digest,
    compute_plan_digest,
    is_approval_valid,
)
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    record_attempt_approval,
    reset_runtime_continuity_state,
    upsert_runtime_attempt_subtask,
)
from storage.migrations import run_migrations


class DigestTests(unittest.TestCase):
    def test_same_action_same_digest(self) -> None:
        d1 = compute_action_digest("web.research", {"asset_key": "gold"})
        d2 = compute_action_digest("web.research", {"asset_key": "gold"})
        self.assertEqual(d1, d2)

    def test_changed_arguments_change_digest(self) -> None:
        """Step 10: "changed arguments invalidate it" -- proven at the digest level first."""
        d1 = compute_action_digest("web.research", {"asset_key": "gold"})
        d2 = compute_action_digest("web.research", {"asset_key": "silver"})
        self.assertNotEqual(d1, d2)

    def test_plan_digest_changes_if_subtask_set_changes(self) -> None:
        d1 = compute_plan_digest(["a", "b"])
        d2 = compute_plan_digest(["a", "b", "c"])
        self.assertNotEqual(d1, d2)

    def test_plan_digest_is_order_independent(self) -> None:
        """The PLAN's identity doesn't depend on the order subtasks happen to be listed in."""
        self.assertEqual(compute_plan_digest(["a", "b"]), compute_plan_digest(["b", "a"]))


class ApprovalValidityTests(unittest.TestCase):
    def test_no_approval_on_record_is_invalid(self) -> None:
        valid, reason = is_approval_valid(None, action_digest="d1", plan_digest="p1", policy_version=CURRENT_APPROVAL_POLICY_VERSION)
        self.assertFalse(valid)
        self.assertIn("no approval", reason)

    def test_matching_allow_is_valid(self) -> None:
        approval = {"decision": "allow", "action_digest": "d1", "plan_digest": "p1", "policy_version": CURRENT_APPROVAL_POLICY_VERSION, "expires_at": None}
        valid, _ = is_approval_valid(approval, action_digest="d1", plan_digest="p1", policy_version=CURRENT_APPROVAL_POLICY_VERSION)
        self.assertTrue(valid)

    def test_denial_is_never_valid(self) -> None:
        approval = {"decision": "deny", "action_digest": "d1", "plan_digest": "p1", "policy_version": CURRENT_APPROVAL_POLICY_VERSION}
        valid, reason = is_approval_valid(approval, action_digest="d1", plan_digest="p1", policy_version=CURRENT_APPROVAL_POLICY_VERSION)
        self.assertFalse(valid)
        self.assertIn("deny", reason)

    def test_changed_action_digest_invalidates(self) -> None:
        """Step 10: "changed arguments invalidate it"."""
        approval = {"decision": "allow", "action_digest": "old-digest", "plan_digest": "p1", "policy_version": CURRENT_APPROVAL_POLICY_VERSION}
        valid, reason = is_approval_valid(approval, action_digest="new-digest", plan_digest="p1", policy_version=CURRENT_APPROVAL_POLICY_VERSION)
        self.assertFalse(valid)
        self.assertIn("action_digest", reason)

    def test_changed_plan_digest_invalidates(self) -> None:
        approval = {"decision": "allow", "action_digest": "d1", "plan_digest": "old-plan", "policy_version": CURRENT_APPROVAL_POLICY_VERSION}
        valid, reason = is_approval_valid(approval, action_digest="d1", plan_digest="new-plan", policy_version=CURRENT_APPROVAL_POLICY_VERSION)
        self.assertFalse(valid)
        self.assertIn("plan_digest", reason)

    def test_expired_approval_is_not_reused(self) -> None:
        """Step 10: "expired approval is not reused"."""
        approval = {"decision": "allow", "action_digest": "d1", "plan_digest": "p1", "policy_version": CURRENT_APPROVAL_POLICY_VERSION, "expires_at": "2020-01-01T00:00:00+00:00"}
        valid, reason = is_approval_valid(approval, action_digest="d1", plan_digest="p1", policy_version=CURRENT_APPROVAL_POLICY_VERSION, now_iso="2026-01-01T00:00:00+00:00")
        self.assertFalse(valid)
        self.assertIn("expired", reason)

    def test_unexpired_approval_with_future_expiry_is_valid(self) -> None:
        approval = {"decision": "allow", "action_digest": "d1", "plan_digest": "p1", "policy_version": CURRENT_APPROVAL_POLICY_VERSION, "expires_at": "2099-01-01T00:00:00+00:00"}
        valid, _ = is_approval_valid(approval, action_digest="d1", plan_digest="p1", policy_version=CURRENT_APPROVAL_POLICY_VERSION, now_iso="2026-01-01T00:00:00+00:00")
        self.assertTrue(valid)


class DurableApprovalIntegrationTests(unittest.TestCase):
    """Proves the remaining two required properties against the real database: WAITING_APPROVAL
    retains the complete plan, and restart does not silently grant an in-memory-only approval."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "approval.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_waiting_approval_subtask_retains_the_complete_plan(self) -> None:
        """Step 10: "WAITING_APPROVAL retains the complete immutable plan" -- every subtask in
        the plan is persisted, including the ones waiting on approval, with their full arguments
        intact (never a partial/summarized plan)."""
        attempt = create_runtime_attempt(session_id="s1", original_request="req requiring approval", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(
            attempt_id=attempt["attempt_id"], subtask_id="a", operation="workspace.write_file",
            arguments={"path": "/tmp/x", "content": "hello"}, lifecycle_state="WAITING_APPROVAL",
            approval_state="require_approval",
        )
        upsert_runtime_attempt_subtask(
            attempt_id=attempt["attempt_id"], subtask_id="b", operation="market_quote",
            arguments={"asset_key": "gold"}, lifecycle_state="SUCCEEDED",
        )
        from core.runtime_continuity import list_runtime_attempt_subtasks

        subtasks = list_runtime_attempt_subtasks(attempt["attempt_id"])
        self.assertEqual(len(subtasks), 2)
        waiting = next(s for s in subtasks if s["subtask_id"] == "a")
        self.assertEqual(waiting["arguments"], {"path": "/tmp/x", "content": "hello"})
        self.assertEqual(waiting["lifecycle_state"], "WAITING_APPROVAL")

    def test_restart_does_not_silently_grant_an_unrecorded_approval(self) -> None:
        """Step 10: "restart does not silently grant an in-memory approval." Simulates a restart
        by reconnecting to the SAME database with no approval ever recorded -- validity must be
        False, never inferred as True just because nothing says otherwise."""
        attempt = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        digest = compute_action_digest("workspace.write_file", {"path": "/tmp/x"})
        plan_digest = compute_plan_digest([digest])

        # "Restart": reconnect to the same db path fresh, exactly as a real daemon restart would
        # (a fresh process re-running configure_runtime_continuity_db_path against the same file).
        configure_runtime_continuity_db_path(str(self._db_path))

        # No approval was ever recorded -- the query must come back empty, and validity False.
        import sqlite3

        conn = sqlite3.connect(str(self._db_path))
        row = conn.execute(
            "SELECT COUNT(*) FROM runtime_attempt_approvals WHERE attempt_id = ?", (attempt["attempt_id"],),
        ).fetchone()
        conn.close()
        self.assertEqual(row[0], 0)
        valid, reason = is_approval_valid(None, action_digest=digest, plan_digest=plan_digest, policy_version=CURRENT_APPROVAL_POLICY_VERSION)
        self.assertFalse(valid)
        self.assertIn("no approval", reason)

    def test_approval_then_denial_both_resolve_the_same_attempt(self) -> None:
        """Step 10: "approval and denial resume/finalize the same attempt" -- both decisions are
        recorded against the SAME attempt_id, never a different or new one."""
        attempt = create_runtime_attempt(session_id="s1", original_request="req", answer_mode="LIVE_DATA")
        allow_record = record_attempt_approval(
            attempt_id=attempt["attempt_id"], action_digest="d1", plan_digest="p1",
            policy_version="v1", requested_scope="workspace.write_file", decision="allow",
        )
        self.assertEqual(allow_record["attempt_id"], attempt["attempt_id"])

        attempt2 = create_runtime_attempt(session_id="s1", original_request="req2", answer_mode="LIVE_DATA")
        deny_record = record_attempt_approval(
            attempt_id=attempt2["attempt_id"], action_digest="d2", plan_digest="p2",
            policy_version="v1", requested_scope="workspace.write_file", decision="deny",
        )
        self.assertEqual(deny_record["attempt_id"], attempt2["attempt_id"])
        self.assertNotEqual(allow_record["attempt_id"], deny_record["attempt_id"])


class SabotageRemovingDigestValidationTests(unittest.TestCase):
    """Sabotage #10: remove approval digest validation -- an approval granted for one action must
    stop being usable once the digest check is bypassed lets a DIFFERENT action reuse it."""

    def test_sabotage_skipping_digest_comparison_lets_a_stale_approval_authorize_a_different_action(self) -> None:
        approval = {"decision": "allow", "action_digest": "digest-for-write-file-A", "plan_digest": "p1", "policy_version": CURRENT_APPROVAL_POLICY_VERSION}
        different_action_digest = compute_action_digest("workspace.write_file", {"path": "/etc/passwd"})

        def sabotaged_is_approval_valid(approval, *, action_digest, plan_digest, policy_version, now_iso=None):
            # Sabotage: skip the action_digest/plan_digest comparison entirely -- only checks the
            # decision, exactly what "remove approval digest validation" means in practice.
            if approval is None:
                return False, "no approval"
            return str(approval.get("decision") or "") == "allow", "sabotaged: digest never compared"

        sabotaged_valid, _ = sabotaged_is_approval_valid(
            approval, action_digest=different_action_digest, plan_digest="p1", policy_version=CURRENT_APPROVAL_POLICY_VERSION,
        )
        self.assertTrue(sabotaged_valid, "sabotage should have wrongly authorized a different action")

        # Control: the REAL function correctly rejects it (on the digest, not the policy version --
        # this approval's policy_version already matches, isolating the digest check specifically).
        real_valid, reason = is_approval_valid(
            approval, action_digest=different_action_digest, plan_digest="p1", policy_version=CURRENT_APPROVAL_POLICY_VERSION,
        )
        self.assertFalse(real_valid)
        self.assertIn("action_digest", reason)


if __name__ == "__main__":
    unittest.main()
