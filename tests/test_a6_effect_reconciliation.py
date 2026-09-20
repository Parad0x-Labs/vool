"""A6 effect reconciliation — acceptance contracts R1-R18 (assembly W001).

Harness follows the proven recon-ninja U1 oracle: the real served funnel
(``execute_tool_intent`` -> ``execute_runtime_tool`` -> ``workspace.write_file``)
drives an instrumented handler whose physical effect is a marker-file append;
the marker line count IS the cumulative physical effect count. No network, no
email, no payment. All state isolated per test by the conftest storage reset.

Core law under test: UNKNOWN != FAILED. An authorized mutating effect whose
physical outcome cannot be proven becomes a durable unresolved record that
blocks identical redispatch until mechanically / provider / user resolved.
"""
from __future__ import annotations

import json
import tempfile
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from core import runtime_execution_tools as ret
from core.effect_reconciliation import (
    UNKNOWN_OUTCOME_MODE,
    UNKNOWN_OUTCOME_STATUS,
    ResolutionOutcome,
    reconcile_unresolved_effect,
)
from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig
from core.runtime_continuity import (
    compute_logical_effect_id,
    find_active_unresolved_effect,
    get_unresolved_effect,
    list_unresolved_effect_resolutions,
    mark_effect_dispatched,
    reserve_logical_effect,
    resolve_unresolved_effect,
    sweep_stale_unresolved_effects,
)
from core.tool_intent_executor import execute_tool_intent

# These cases are about A6 effect reconciliation on a turn that was ALREADY authorized -- the
# class names say so ("R1AuthorizedSuccessClassifiesApplied"). They used to express that
# authorization by sending no mode at all, back when a modeless call skipped the permission
# decision entirely. That bypass is gone: every dispatch is decided now, so the premise has to be
# stated out loud instead of inherited from a hole.
AUTHORIZED_MODE = "auto"

INTENT = "workspace.write_file"
ARGUMENTS = {"path": "a6-target.txt", "content": "send $10 to Bob (logical-effect-a6)"}


def _tracker() -> HiveActivityTracker:
    return HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))


class A6Harness(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="a6-")
        self.workspace = Path(self._tmp.name) / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.marker = self.workspace / "_a6_physical_marker.jsonl"
        self._original_write = ret._write_file
        ret._write_file = self._instrumented_write(raise_after_write=False)
        self.addCleanup(self._teardown)

    def _teardown(self) -> None:
        ret._write_file = self._original_write
        self._tmp.cleanup()

    # ── instrumentation ──────────────────────────────────────────────────────
    def _instrumented_write(self, *, raise_after_write: bool, exc_class: type[Exception] = RuntimeError):
        harness = self

        # `reviewed_destination` (approved-destination identity, R6 on 2026-09-20) is now part of
        # the `_write_file` call contract; the stub must accept what the dispatch seam passes or
        # every instrumented turn dies as an unknown effect instead of exercising reconciliation.
        def handler(arguments, *, workspace_root, session_id, reviewed_destination=None):
            harness.marker.parent.mkdir(parents=True, exist_ok=True)
            with harness.marker.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps({"event": "PHYSICAL_MUTATION", "arguments": arguments}) + "\n")
            if raise_after_write:
                raise exc_class("simulated dispatched-but-unacknowledged")
            return ret.RuntimeExecutionResult(
                handled=True,
                ok=True,
                status="executed",
                response_text="marker written",
                details={},
            )

        return handler

    def arm_ambiguous(self) -> None:
        """Physical mutation lands, then the acknowledgement is lost."""
        ret._write_file = self._instrumented_write(raise_after_write=True)

    def arm_clean(self) -> None:
        ret._write_file = self._instrumented_write(raise_after_write=False)

    def mutations(self) -> int:
        if not self.marker.exists():
            return 0
        return sum(1 for line in self.marker.read_text().splitlines() if line.strip())

    # ── funnel helpers ───────────────────────────────────────────────────────
    def run_turn(self, *, label: str | None = None) -> object:
        checkpoint_id = f"runtime-{uuid.uuid4().hex}"  # fresh per turn, like the planner
        return execute_tool_intent(
            {"intent": INTENT, "arguments": dict(ARGUMENTS)},
            task_id=f"a6-{label or uuid.uuid4().hex}",
            session_id=f"a6-session-{uuid.uuid4().hex}",
            checkpoint_id=checkpoint_id,
            step_index=0,
            source_context={"workspace": str(self.workspace), "operating_mode": AUTHORIZED_MODE},
            hive_activity_tracker=_tracker(),
        )

    def logical_id(self) -> str:
        return compute_logical_effect_id(intent=INTENT, arguments=ARGUMENTS)


class R1AuthorizedSuccessClassifiesApplied(A6Harness):
    def test_success_is_terminal_and_repeat_allowed(self) -> None:  # R1 + R13
        first = self.run_turn()
        assert first.ok is True
        row = get_unresolved_effect(self.logical_id())
        assert row is not None and row["state"] == "applied"
        # No active unresolved record remains.
        assert find_active_unresolved_effect(self.logical_id()) is None
        # An intentional identical repeat is a NEW instance and is allowed.
        second = self.run_turn()
        assert second.ok is True
        assert self.mutations() == 2


class R2ProvableAbsenceReleasesOneRetry(A6Harness):
    def test_unknown_resolved_failed_by_absence_allows_exactly_one_retry(self) -> None:  # R2/R12-family
        self.arm_ambiguous()
        first = self.run_turn()
        assert first.status == UNKNOWN_OUTCOME_STATUS
        assert self.mutations() == 1
        # The handler mutated only the MARKER; the intended target was never written.
        resolution = reconcile_unresolved_effect(get_unresolved_effect(self.logical_id()))
        assert resolution.outcome is ResolutionOutcome.FAILED_SAFE_TO_RETRY
        applied = resolve_unresolved_effect(
            logical_effect_id=self.logical_id(),
            resolution="CONFIRMED_FAILED_SAFE_TO_RETRY",
            source="mechanical",
            evidence=resolution.evidence,
            resolved_by="effect_reconciler:mechanical",
        )
        assert applied["outcome"] == "resolved"
        # Exactly one controlled retry now permitted.
        self.arm_clean()
        retry = self.run_turn()
        assert retry.ok is True
        assert self.mutations() == 2


class R3R4AmbiguousBecomesUnknownAndBlocksRetry(A6Harness):
    def test_mutate_then_raise_records_durable_unknown_and_blocks_fresh_retry(self) -> None:  # R3/R4/R14/R15
        self.arm_ambiguous()
        first = self.run_turn()
        # Served truth law: NOT ok=false/status=error/tool_failed prose.
        assert first.status == UNKNOWN_OUTCOME_STATUS
        assert first.mode == UNKNOWN_OUTCOME_MODE
        assert "unknown" in (first.response_text or "").lower()
        assert self.mutations() == 1
        row = find_active_unresolved_effect(self.logical_id())
        assert row is not None and row["state"] == "unknown"
        # Fresh user turn retrying identical typed args is BLOCKED.
        second = self.run_turn()
        assert second.status == UNKNOWN_OUTCOME_STATUS
        assert second.details.get("blocked_pending_reconciliation") is True
        assert self.mutations() == 1  # NO second physical dispatch


class R5UnknownSurvivesRestart(A6Harness):
    def test_durable_unknown_still_blocks_after_restart(self) -> None:  # R5/R9/R10-restart
        from core.runtime_continuity import configure_runtime_continuity_db_path
        from storage.db import active_default_db_path, reset_default_connection

        self.arm_ambiguous()
        self.run_turn()
        assert self.mutations() == 1
        # Simulate an OS-process restart: drop pooled connections; the SQLite
        # file on disk is all the next process would see.
        reset_default_connection()
        configure_runtime_continuity_db_path(active_default_db_path())
        row = find_active_unresolved_effect(self.logical_id())
        assert row is not None and row["state"] == "unknown"
        retry = self.run_turn()
        assert retry.status == UNKNOWN_OUTCOME_STATUS
        assert retry.details.get("blocked_pending_reconciliation") is True
        assert self.mutations() == 1


class R6MechanicalApplied(A6Harness):
    def test_matching_target_proves_applied_and_prevents_redispatch(self) -> None:  # R6
        self.arm_ambiguous()
        self.run_turn()
        # Simulate that the physical write DID land (target exists as intended).
        target = self.workspace / ARGUMENTS["path"]
        target.write_text(ARGUMENTS["content"], encoding="utf-8")
        resolution = reconcile_unresolved_effect(get_unresolved_effect(self.logical_id()))
        assert resolution.outcome is ResolutionOutcome.APPLIED
        resolve_unresolved_effect(
            logical_effect_id=self.logical_id(),
            resolution="CONFIRMED_APPLIED",
            source="mechanical",
            evidence=resolution.evidence,
        )
        assert find_active_unresolved_effect(self.logical_id()) is None
        assert self.mutations() == 1  # reconciliation did NOT redispatch


class R8UserResolutionTypedPersisted(A6Harness):
    def test_user_confirmed_applied_persists_event_with_user_source(self) -> None:  # R8
        self.arm_ambiguous()
        self.run_turn()
        resolve_unresolved_effect(
            logical_effect_id=self.logical_id(),
            resolution="CONFIRMED_APPLIED",
            source="user",
            evidence="user confirmed via typed followup channel",
            resolved_by="user:chat",
        )
        events = list_unresolved_effect_resolutions(self.logical_id())
        assert len(events) == 1
        assert events[0]["source"] == "user"
        assert events[0]["resolution"] == "CONFIRMED_APPLIED"
        assert get_unresolved_effect(self.logical_id())["state"] == "applied"
        assert self.mutations() == 1  # nothing auto-redispatches behind the user's back

    def test_user_confirmed_failed_releases_one_new_attempt(self) -> None:  # R9
        self.arm_ambiguous()
        self.run_turn()
        resolve_unresolved_effect(
            logical_effect_id=self.logical_id(),
            resolution="CONFIRMED_FAILED_SAFE_TO_RETRY",
            source="user",
            resolved_by="user:chat",
        )
        self.arm_clean()
        retry = self.run_turn()
        assert retry.ok is True
        assert self.mutations() == 2

    def test_superseded_releases_block_without_asserting_history(self) -> None:  # R9
        self.arm_ambiguous()
        self.run_turn()
        resolve_unresolved_effect(
            logical_effect_id=self.logical_id(),
            resolution="SUPERSEDED_NEW_INSTANCE",
            source="user",
            resolved_by="user:chat",
        )
        self.arm_clean()
        fresh = self.run_turn()
        assert fresh.ok is True
        assert self.mutations() == 2


class R10ConcurrentReserve(A6Harness):
    def test_concurrent_identical_effects_cannot_double_dispatch(self) -> None:  # R10/R16-concurrency
        barrier_reserve_count = {"reserved": 0}

        def attempt(_index: int) -> str:
            result = reserve_logical_effect(intent=INTENT, arguments=dict(ARGUMENTS))
            if result["outcome"] == "reserved":
                barrier_reserve_count["reserved"] += 1
                return "reserved"
            return "blocked"

        with ThreadPoolExecutor(max_workers=8) as pool:
            outcomes = list(pool.map(attempt, range(16)))
        assert outcomes.count("reserved") == 1
        assert barrier_reserve_count["reserved"] == 1
        # The loser sees an active row for the SAME logical effect.
        assert find_active_unresolved_effect(self.logical_id()) is not None

    def test_cross_process_concurrency_primitive_is_pinned(self) -> None:
        # In-process threads cannot expose the check-then-insert window (CPython
        # + SQLite serialize them), so pin the actual cross-process primitives:
        # the partial unique index and the BEGIN IMMEDIATE reservation.
        import inspect

        from core import runtime_continuity as rc
        from core.runtime_continuity import _conn

        conn = _conn()
        try:
            row = conn.execute(
                "SELECT sql FROM sqlite_master WHERE type='index' AND name='ux_unresolved_active_effect'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert "prepared" in str(row["sql"]) and "dispatched" in str(row["sql"])
        source = inspect.getsource(rc.reserve_logical_effect)
        assert "BEGIN IMMEDIATE" in source


class R11DistinctArgsDistinctEffects(A6Harness):
    def test_different_typed_args_are_distinct_logical_effects(self) -> None:  # R11
        self.arm_clean()
        first = execute_tool_intent(
            {"intent": INTENT, "arguments": {"path": "one.txt", "content": "alpha"}},
            task_id="r11-a",
            session_id="r11-s",
            checkpoint_id=f"runtime-{uuid.uuid4().hex}",
            step_index=0,
            source_context={"workspace": str(self.workspace), "operating_mode": AUTHORIZED_MODE},
            hive_activity_tracker=_tracker(),
        )
        second = execute_tool_intent(
            {"intent": INTENT, "arguments": {"path": "two.txt", "content": "alpha"}},
            task_id="r11-b",
            session_id="r11-s",
            checkpoint_id=f"runtime-{uuid.uuid4().hex}",
            step_index=0,
            source_context={"workspace": str(self.workspace), "operating_mode": AUTHORIZED_MODE},
            hive_activity_tracker=_tracker(),
        )
        assert first.ok and second.ok
        assert compute_logical_effect_id(
            intent=INTENT, arguments={"path": "one.txt", "content": "alpha"}
        ) != compute_logical_effect_id(intent=INTENT, arguments={"path": "two.txt", "content": "alpha"})


class R12IdentityIsCheckpointFree(A6Harness):
    def test_same_args_different_turn_labels_are_the_same_logical_effect(self) -> None:  # R12/R3-identity
        base = compute_logical_effect_id(intent=INTENT, arguments=dict(ARGUMENTS))
        again = compute_logical_effect_id(intent=INTENT, arguments=dict(ARGUMENTS))
        assert base == again
        self.arm_ambiguous()
        self.run_turn()
        # A different session/task/checkpoint spelling of the same intent+args
        # resolves to the same blocked logical effect.
        blocked = execute_tool_intent(
            {"intent": INTENT, "arguments": dict(ARGUMENTS)},
            task_id="different-task",
            session_id="different-session",
            checkpoint_id=f"runtime-{uuid.uuid4().hex}",
            step_index=3,
            source_context={"workspace": str(self.workspace), "operating_mode": AUTHORIZED_MODE},
            hive_activity_tracker=_tracker(),
        )
        assert blocked.status == UNKNOWN_OUTCOME_STATUS
        assert self.mutations() == 1


class R17ResultFailureIsFailedSafeToRetry(A6Harness):
    def test_invalid_arguments_result_classifies_failed_safe_to_retry(self) -> None:  # R17
        execution = execute_tool_intent(
            {"intent": INTENT, "arguments": {**ARGUMENTS, "bogus_argument": 1}},
            task_id="r17",
            session_id="r17-s",
            checkpoint_id=f"runtime-{uuid.uuid4().hex}",
            step_index=0,
            source_context={"workspace": str(self.workspace), "operating_mode": AUTHORIZED_MODE},
            hive_activity_tracker=_tracker(),
        )
        assert execution.status == "invalid_arguments"
        leid = compute_logical_effect_id(intent=INTENT, arguments={**ARGUMENTS, "bogus_argument": 1})
        row = get_unresolved_effect(leid)
        assert row is not None and row["state"] == "failed_safe_to_retry"
        assert find_active_unresolved_effect(leid) is None


class R18TimeoutClassIsUnknown(A6Harness):
    def test_timeout_after_dispatch_boundary_is_unknown_never_failed(self) -> None:  # R18/AB-10/C5-timeout
        ret._write_file = self._instrumented_write(raise_after_write=True, exc_class=TimeoutError)
        first = self.run_turn()
        assert first.status == UNKNOWN_OUTCOME_STATUS
        row = find_active_unresolved_effect(self.logical_id())
        assert row is not None and row["state"] == "unknown"
        retry = self.run_turn()
        assert retry.details.get("blocked_pending_reconciliation") is True
        assert self.mutations() == 1

    def test_unknown_shaped_ack_result_stays_unknown_not_failed(self) -> None:  # M8 guard
        original = ret._write_file

        # Accepts `reviewed_destination` for the same contract reason as `_instrumented_write`.
        def unknown_result(arguments, *, workspace_root, session_id, reviewed_destination=None):
            return ret.RuntimeExecutionResult(
                handled=True, ok=False, status=UNKNOWN_OUTCOME_STATUS,
                response_text="outcome unprovable", details={},
            )

        ret._write_file = unknown_result
        try:
            first = self.run_turn()
        finally:
            ret._write_file = original
        assert find_active_unresolved_effect(self.logical_id())["state"] == "unknown"
        self.arm_clean()
        retry = self.run_turn()
        assert retry.details.get("blocked_pending_reconciliation") is True
        assert self.mutations() == 0


class R16RefusedEffectCreatesNoRow(A6Harness):
    def test_action_policy_forbidden_creates_no_reservation(self) -> None:  # R16/I5
        execution = execute_tool_intent(
            {"intent": INTENT, "arguments": dict(ARGUMENTS)},
            task_id="r16",
            session_id="r16-s",
            checkpoint_id=f"runtime-{uuid.uuid4().hex}",
            step_index=0,
            source_context={"workspace": str(self.workspace), "operating_mode": AUTHORIZED_MODE, "action_policy": "forbidden"},
            hive_activity_tracker=_tracker(),
        )
        assert execution.status == "blocked_by_action_policy"
        assert find_active_unresolved_effect(self.logical_id()) is None
        assert get_unresolved_effect(self.logical_id()) is None
        assert self.mutations() == 0


class R14UnknownReceiptSerialization(A6Harness):
    def test_ambiguous_turn_receipt_never_serializes_as_plain_failure(self) -> None:  # R14/R15
        from core.runtime_continuity import build_tool_receipt_key, load_tool_receipt

        self.arm_ambiguous()
        checkpoint_id = f"runtime-{uuid.uuid4().hex}"
        first = execute_tool_intent(
            {"intent": INTENT, "arguments": dict(ARGUMENTS)},
            task_id="r14",
            session_id="r14-s",
            checkpoint_id=checkpoint_id,
            step_index=0,
            source_context={"workspace": str(self.workspace), "operating_mode": AUTHORIZED_MODE},
            hive_activity_tracker=_tracker(),
        )
        receipt = load_tool_receipt(
            build_tool_receipt_key(
                checkpoint_id=checkpoint_id, step_index=0, intent=INTENT, arguments=dict(ARGUMENTS)
            )
        )
        assert receipt is not None
        stored = receipt.get("execution") or {}
        assert str(stored.get("status")) == UNKNOWN_OUTCOME_STATUS
        assert str(stored.get("mode")) == UNKNOWN_OUTCOME_MODE
        assert "unknown" in str(stored.get("response_text") or "").lower()
        assert first.status == UNKNOWN_OUTCOME_STATUS


class ModelCannotResolve(A6Harness):
    def test_model_source_resolution_is_refused(self) -> None:  # R14/I6/M7-guard
        self.arm_ambiguous()
        self.run_turn()
        try:
            resolve_unresolved_effect(
                logical_effect_id=self.logical_id(),
                resolution="CONFIRMED_APPLIED",
                source="model",
                evidence="model says probably worked",
            )
        except ValueError:
            pass
        else:
            self.fail("model-origin resolution must be refused")
        # The unknown still blocks.
        retry = self.run_turn()
        assert retry.details.get("blocked_pending_reconciliation") is True
        assert self.mutations() == 1


class NonReconcilableStaysUnknown(A6Harness):
    def test_undeclared_intent_has_no_mechanical_resolver(self) -> None:  # R13-truthfulness
        row = {
            "tool_name": "sandbox.run_command",
            "expected_evidence": {},
            "resource_identity": "",
        }
        resolution = reconcile_unresolved_effect(row)
        assert resolution.outcome is ResolutionOutcome.STILL_UNKNOWN


class CrashWindows(A6Harness):
    def _set_state_directly(self, leid: str, updates: dict) -> None:
        from core.runtime_continuity import _conn

        conn = _conn()
        try:
            assignments = ", ".join(f"{key} = ?" for key in updates)
            conn.execute(
                f"UPDATE runtime_unresolved_effects SET {assignments} WHERE logical_effect_id = ?",
                (*updates.values(), leid),
            )
            conn.commit()
        finally:
            conn.close()

    def test_c2_crash_before_dispatch_expires_and_allows_fresh_dispatch(self) -> None:  # C2
        reservation = reserve_logical_effect(intent=INTENT, arguments=dict(ARGUMENTS))
        assert reservation["outcome"] == "reserved"
        leid = reservation["logical_effect_id"]
        # Age the PREPARED lease past expiry without ever claiming dispatch.
        self._set_state_directly(leid, {"created_at": "2020-01-01T00:00:00+00:00"})
        sweep_stale_unresolved_effects()
        assert get_unresolved_effect(leid)["state"] == "expired_pre_dispatch"
        self.arm_clean()
        fresh = self.run_turn()
        assert fresh.ok is True
        assert self.mutations() == 1

    def test_c3_c4_lost_dispatch_becomes_unknown_and_blocks(self) -> None:  # C3/C4/C6
        reservation = reserve_logical_effect(intent=INTENT, arguments=dict(ARGUMENTS))
        leid = reservation["logical_effect_id"]
        instance = reservation["effect_instance_id"]
        assert mark_effect_dispatched(logical_effect_id=leid, effect_instance_id=instance)
        # Process dies right here (before any outcome persistence).
        self._set_state_directly(leid, {"dispatched_at": "2020-01-01T00:00:00+00:00"})
        sweep_stale_unresolved_effects()
        row = get_unresolved_effect(leid)
        assert row["state"] == "unknown"
        assert row["reason"] == "crash_recovery_dispatch_lease_expired"
        self.arm_clean()
        retry = self.run_turn()
        assert retry.details.get("blocked_pending_reconciliation") is True
        assert self.mutations() == 0  # blind redispatch after restart forbidden


class ServedP0Repro(A6Harness):
    def test_double_effect_p0_is_eliminated(self) -> None:
        # BASE behavior was cumulative physical effects == 2 across ambiguous
        # dispatch + fresh-turn retry. Candidate law: <=1, blocked pending
        # reconciliation, no redispatch once APPLIED is proven.
        self.arm_ambiguous()
        first = self.run_turn()
        assert self.mutations() == 1
        assert first.status == UNKNOWN_OUTCOME_STATUS
        blocked = self.run_turn()
        assert blocked.details.get("blocked_pending_reconciliation") is True
        assert self.mutations() == 1
        target = self.workspace / ARGUMENTS["path"]
        target.write_text(ARGUMENTS["content"], encoding="utf-8")
        resolve_unresolved_effect(
            logical_effect_id=self.logical_id(),
            resolution="CONFIRMED_APPLIED",
            source="mechanical",
            evidence=reconcile_unresolved_effect(get_unresolved_effect(self.logical_id())).evidence,
        )
        assert self.mutations() == 1  # APPLIED reconciliation never redispatches


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
