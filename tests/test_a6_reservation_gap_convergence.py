"""A10 P0 convergence — A6 reservation/claim gap closed for non-runtime lanes.

Pre-fix truth (mechanically reproduced at SHA 380879c9): eight served mutating
intents satisfied ``a6_reserved_intent`` (the reservation POLICY) yet failed
``_a6_runtime_lane_intent`` (which demanded ``handler == "runtime"``), skipped
the reserve/claim path entirely, and dispatched BARE through the hive/operator
lanes with no journal row at all:

    reserved_policy=True  claim_eligible=False  dispatched_ok=True  journal_row=absent

Five of those intents are ``hive.*`` mutations with no runtime contract; three
are ``operator.*`` mutations whose contracts declare handler ``external_lane``.

Post-fix law under test: IF a served intent requires A6 reservation THEN it
either enters the canonical reserve -> mark-dispatched -> terminal-classify
path across its ACTUAL dispatching lane, or it deterministically does not
dispatch. UNKNOWN stays UNKNOWN; an unavailable claim never becomes permissive
dispatch; retry/replay cannot produce a second external effect under the same
logical effect identity without a canonical A6 state transition permitting it.
"""
from __future__ import annotations

import tempfile
import unittest
import uuid
from unittest import mock

import core.tool_intent_executor as tie
from core.effect_reconciliation import (
    UNKNOWN_OUTCOME_MODE,
    UNKNOWN_OUTCOME_STATUS,
    a6_reserved_intent,
)
from core.execution.constants import _HIVE_TOOL_INTENTS, _MUTATING_OPERATOR_INTENTS
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.mode_permission_policy import (
    PermissionAction,
    grant_internal_authority,
    revoke_internal_authority,
)
from core.runtime_continuity import _conn, is_mutating_tool_intent

# These cases exercise A6 reservation convergence on an ALREADY-AUTHORIZED turn. They used to
# say so by sending no mode, back when a modeless dispatch skipped the permission decision
# entirely. Every dispatch is decided now, so the authorization is stated instead of assumed.
# These cases exercise A6 reservation mechanics across eight effect shapes on an ALREADY-AUTHORIZED
# turn. They used to express that authorization by sending no mode at all, back when a modeless
# dispatch skipped the permission decision entirely. Every dispatch is decided now, so the harness
# holds an explicit typed scope naming exactly the five actions these eight shapes need -- anything
# outside that list still falls through to the ordinary matrix.
GAP_SHAPE_ACTIONS = {
    PermissionAction.USE_NETWORK,
    PermissionAction.ACCESS_EXTERNAL_PROVIDERS,
    PermissionAction.DELETE_FILES,
    PermissionAction.RUN_SIDE_EFFECTING_COMMANDS,
    PermissionAction.SEND_EXTERNAL_MESSAGES,
}


def _tracker() -> HiveActivityTracker:
    return HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))


HIVE_GAP = ["hive.research_topic", "hive.create_topic", "hive.claim_task", "hive.post_progress", "hive.submit_result"]
OPERATOR_GAP = ["operator.cleanup_temp_files", "operator.move_path", "operator.schedule_calendar_event"]
ALL_EIGHT = HIVE_GAP + OPERATOR_GAP


def _row_for(tool_name: str, checkpoint_id: str) -> dict | None:
    """Latest journal row for this intent on this checkpoint (rows stamp both)."""
    conn = _conn()
    try:
        rows = conn.execute(
            "SELECT * FROM runtime_unresolved_effects WHERE tool_name = ? AND checkpoint_id = ? "
            "ORDER BY rowid DESC LIMIT 1",
            (tool_name, checkpoint_id),
        ).fetchall()
    finally:
        conn.close()
    return dict(rows[0]) if rows else None


class FallThroughReservationGap(unittest.TestCase):
    """The verifier's exact counterexample class: policy YES / claim NO / dispatch."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="a10-a6-gap-")
        self.calls: list[str] = []
        self.result_factory = self._ok_result
        self._authority = grant_internal_authority(
            label="a6-gap-shape-harness",
            actions=GAP_SHAPE_ACTIONS,
            duration_seconds=300,
            workspace_root=str(self._tmp.name),
        )
        self.addCleanup(revoke_internal_authority, self._authority)
        self.addCleanup(self._teardown)

    def _teardown(self) -> None:
        self._tmp.cleanup()

    # ── controllable lane fakes ──────────────────────────────────────────────
    def _ok_result(self, intent: str):
        return tie.ToolIntentExecution(
            handled=True, ok=True, status="executed",
            response_text="lane-ok", mode="tool_executed",
            tool_name=intent, details={},
        )

    def patch_lanes(self) -> None:
        harness = self

        def fake_hive(intent, arguments, **kw):
            harness.calls.append(intent)
            return harness.result_factory(intent)

        def fake_op(intent, arguments, **kw):
            harness.calls.append(intent)
            return harness.result_factory(intent)

        p1 = mock.patch.object(tie, "_execute_hive_tool_impl", fake_hive)
        p2 = mock.patch.object(tie, "_execute_operator_tool_impl", fake_op)
        p1.start()
        p2.start()
        self.addCleanup(p1.stop)
        self.addCleanup(p2.stop)

    def dispatch(self, intent: str, *, step_index: int = 0) -> object:
        suffix = uuid.uuid4().hex[:10]
        args = {"topic": f"a10-{suffix}"} if intent.startswith("hive.") else {"target": f"a10-{suffix}"}
        self._last_args = args
        self._checkpoint = f"runtime-a10gap-{uuid.uuid4().hex}"
        execution = tie.execute_tool_intent(
            {"intent": intent, "arguments": dict(args)},
            task_id=f"a10-gap-{uuid.uuid4().hex[:8]}",
            session_id=f"a10-gap-s-{uuid.uuid4().hex[:8]}",
            checkpoint_id=self._checkpoint,
            step_index=step_index,
            source_context={"workspace": str(self._tmp.name), "internal_authority_token": self._authority},
            hive_activity_tracker=_tracker(),
        )
        self.execution = execution
        return execution

    def rerun_same_logical_effect(self) -> object:
        """Same intent AND same typed arguments — the SAME logical effect identity."""
        execution = tie.execute_tool_intent(
            {"intent": self._intent, "arguments": dict(self._last_args)},
            task_id=f"a10-gap-retry-{uuid.uuid4().hex[:8]}",
            session_id=f"a10-gap-s-{uuid.uuid4().hex[:8]}",
            checkpoint_id=f"runtime-a10gap-{uuid.uuid4().hex}",  # fresh turn, fresh receipt key
            step_index=0,
            source_context={"workspace": str(self._tmp.name), "internal_authority_token": self._authority},
            hive_activity_tracker=_tracker(),
        )
        self.execution = execution
        return execution

    def setUp_with_intent(self, intent: str) -> None:
        self._intent = intent
        self.patch_lanes()

    # ── 1: bare dispatch is impossible for every reported shape ─────────────
    def test_all_eight_gap_shapes_reserve_claim_and_reconcile(self) -> None:
        for intent in ALL_EIGHT:
            with self.subTest(intent=intent):
                self.setUp_with_intent(intent)
                # The policy predicate and the old lane predicate must agree NOW:
                # reserved == required. No parallel partially-overlapping predicates.
                self.assertTrue(a6_reserved_intent(intent))
                if intent.startswith("hive."):
                    self.assertIn(intent, _HIVE_TOOL_INTENTS)
                else:
                    self.assertIn(intent, _MUTATING_OPERATOR_INTENTS)
                execution = self.dispatch(intent)
                self.assertTrue(execution.ok, msg=f"{intent}: {execution.status}")
                self.assertIn(intent, self.calls, "effect must actually dispatch")
                row = _row_for(intent, self._checkpoint)
                self.assertIsNotNone(row, f"{intent}: dispatched with NO canonical A6 journal row")
                self.assertEqual(row["state"], "applied")

    def test_policy_and_gate_are_one_predicate(self) -> None:
        # Every mutating-policy intent must be reservation-required; the removed
        # handler=='runtime' clause can no longer carve out exceptions.
        for intent in ALL_EIGHT:
            self.assertTrue(tie._a6_reservation_required(intent))
            self.assertTrue(is_mutating_tool_intent(intent))
        self.assertFalse(tie._a6_reservation_required("respond.direct"))
        self.assertFalse(tie._a6_reservation_required(""))

    # ── 2: ambiguous outcome blocks, never downcasts, survives as UNKNOWN ───
    def test_unknown_outcome_stays_unknown_and_blocks_retry(self) -> None:
        self.setUp_with_intent("hive.create_topic")
        self.result_factory = lambda intent: tie.ToolIntentExecution(
            handled=True, ok=False, status="unknown",
            response_text="outcome unprovable", mode=UNKNOWN_OUTCOME_MODE,
            tool_name=intent, details={},
        )
        first = self.dispatch(self._intent)
        self.assertEqual(first.status, "unknown")  # UNKNOWN-STAYS-UNKNOWN: returned unchanged
        self.assertEqual(len(self.calls), 1)
        row = _row_for(self._intent, self._checkpoint)
        self.assertIsNotNone(row)
        # Fresh-turn retry of the IDENTICAL logical effect is blocked pre-dispatch.
        self.result_factory = self._ok_result
        retry = self.rerun_same_logical_effect()
        self.assertEqual(retry.status, UNKNOWN_OUTCOME_STATUS)
        self.assertTrue(retry.details.get("blocked_pending_reconciliation"))
        self.assertEqual(len(self.calls), 1, "retry must NOT dispatch a second external effect")

    # ── 3: provable failure releases exactly one safe retry ────────────────
    def test_failed_lane_result_resolves_failed_safe_to_retry_and_allows_retry(self) -> None:
        self.setUp_with_intent("operator.move_path")
        self.result_factory = lambda intent: tie.ToolIntentExecution(
            handled=True, ok=False, status="error",
            response_text="refused before any mutation", mode="tool_failed",
            tool_name=intent, details={},
        )
        first = self.dispatch(self._intent)
        self.assertFalse(first.ok)
        row = _row_for(self._intent, self._checkpoint)
        self.assertIsNotNone(row)
        self.assertEqual(row["state"], "failed_safe_to_retry")
        self.result_factory = self._ok_result
        retry = self.rerun_same_logical_effect()
        self.assertTrue(retry.ok, msg=f"safe retry refused: {retry.status}")
        self.assertEqual(len(self.calls), 2)

    # ── 4: cancelled turns create no reservation and never dispatch ─────────
    def test_cancelled_turn_neither_reserves_nor_dispatches(self) -> None:
        self.setUp_with_intent("hive.claim_task")
        execution = tie.execute_tool_intent(
            {"intent": "hive.claim_task", "arguments": {"topic": "cancelled"}},
            task_id="a10-cancel",
            session_id="a10-cancel-s",
            checkpoint_id=f"runtime-a10gap-{uuid.uuid4().hex}",
            step_index=0,
            source_context={"workspace": str(self._tmp.name), "internal_authority_token": self._authority, "cancel_event": True},
            hive_activity_tracker=_tracker(),
        )
        self.assertEqual(execution.status, "cancelled")
        self.assertEqual(self.calls, [])
        conn = _conn()
        try:
            rows = conn.execute(
                "SELECT COUNT(*) AS n FROM runtime_unresolved_effects WHERE tool_name='hive.claim_task' "
                "AND checkpoint_id LIKE 'runtime-a10gap-%' AND attempt_id='a10-cancel'"
            ).fetchone()
        finally:
            conn.close()
        self.assertEqual(rows["n"], 0)

    # ── 5: success permits an intentional identical repeat (new instance) ───
    def test_applied_resolution_allows_intentional_identical_repeat(self) -> None:
        self.setUp_with_intent("hive.post_progress")
        first = self.dispatch(self._intent)
        self.assertTrue(first.ok)
        args = dict(self._last_args)
        repeat = tie.execute_tool_intent(
            {"intent": "hive.post_progress", "arguments": args},
            task_id="a10-repeat",
            session_id="a10-repeat-s",
            checkpoint_id=f"runtime-a10gap-{uuid.uuid4().hex}",
            step_index=0,
            source_context={"workspace": str(self._tmp.name), "internal_authority_token": self._authority},
            hive_activity_tracker=_tracker(),
        )
        self.assertTrue(repeat.ok, msg=f"applied state must not block new instances: {repeat.status}")
        self.assertEqual(len(self.calls), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
