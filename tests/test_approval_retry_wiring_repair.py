"""Repair 8 (Mnemosyne review, 2026-08-06): the retry path called
`run_live_data_plan(..., approval_decisions=None)` -- no approval evaluation at all, live or
persisted. Fixed: the retry path now calls the SAME real live gate a first execution uses
(`evaluate_approval_policy`), and for any subtask that gate does not allow, consults a durable
persisted approval keyed by digest.

No LIVE_DATA scenario in the current product actually requires approval (public read-only
market/weather retrieval never does), so every test here uses a SYNTHETIC approval-gated subtask:
`evaluate_approval_policy` is mocked at its real source
(`core.agent_runtime.live_data_plan.evaluate_approval_policy`) to deny one specific subtask, while
everything downstream of that decision -- digest computation, the persisted-approval lookup,
`is_approval_valid`, the upsert with `approval_state` -- runs through the REAL, unmocked
production code path (`execute_attempt_retry`, real database).
"""

from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from core.agent_runtime.attempt_approval import compute_action_digest, compute_plan_digest
from core.runtime_continuity import (
    configure_runtime_continuity_db_path,
    create_runtime_attempt,
    finalize_runtime_attempt,
    list_runtime_attempt_subtasks,
    record_attempt_approval,
    reset_runtime_continuity_state,
    upsert_runtime_attempt_subtask,
)
from storage.migrations import run_migrations


class _DenyDecision:
    def __init__(self, reason: str = "requires approval") -> None:
        self.allowed = False
        self.reason = reason
        self.effect = "require_approval"


class ApprovalRetryWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "approval_retry.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def _make_parent(self) -> tuple[dict, list[dict]]:
        parent = create_runtime_attempt(session_id="s1", original_request="gold price", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(
            attempt_id=parent["attempt_id"], subtask_id="p:market:gold", plan_id="plan-orig",
            operation="market_quote", entity_type="commodity", entity_key="gold",
            arguments={"asset_key": "gold", "kind": "commodity"}, lifecycle_state="FAILED",
            failure_class="transient", failure_reason="HTTPError: HTTP Error 500: Internal Server Error",
            retryable=True, retry_reason="transient tool failure",
        )
        finalized = finalize_runtime_attempt(parent["attempt_id"])
        subtasks = list_runtime_attempt_subtasks(parent["attempt_id"])
        return finalized, subtasks

    def _deny_gate(self, deny_reason: str = "requires approval"):
        def fake_evaluate(plan, *, source_context):
            return {task.subtask_id: _DenyDecision(deny_reason) for task in plan.subtasks}
        return fake_evaluate

    def test_synthetic_approval_required_subtask_stays_waiting_with_no_recorded_approval(self) -> None:
        from core.agent_runtime.attempt_retry import execute_attempt_retry

        parent, subtasks = self._make_parent()
        with mock.patch("core.agent_runtime.live_data_plan.evaluate_approval_policy", side_effect=self._deny_gate()):
            result = execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-1",
            )
        new_subtasks = list_runtime_attempt_subtasks(result["attempt"]["attempt_id"])
        gold = next(s for s in new_subtasks if s.get("entity_key") == "gold")
        self.assertEqual(gold["lifecycle_state"], "WAITING_APPROVAL")
        self.assertFalse(gold["retryable"])  # matches the established WAITING_APPROVAL retry policy

    def test_a_valid_persisted_approval_lets_the_denied_subtask_proceed(self) -> None:
        from core.agent_runtime.attempt_retry import execute_attempt_retry

        parent, subtasks = self._make_parent()
        action_digest = compute_action_digest("web.research", {"asset_key": "gold", "kind": "commodity"})
        plan_digest = compute_plan_digest([action_digest])
        record_attempt_approval(
            attempt_id=parent["attempt_id"], subtask_id="p:market:gold",
            action_digest=action_digest, plan_digest=plan_digest, policy_version="v1",
            requested_scope="web.research", decision="allow",
        )

        from core.live_quote_contract import LiveQuoteResult

        def fake_commodity(_query, targets, **_kwargs):
            return [LiveQuoteResult(
                asset_key="gold", asset_name="Gold", symbol="GC=F", value=4400.0, currency="USD",
                as_of="2026-08-06 12:00 UTC", source_label="Yahoo Finance", source_url="https://x",
                kind="commodity", unit_label="per troy ounce", change_percent=0.2,
            )]

        with mock.patch("core.agent_runtime.live_data_plan.evaluate_approval_policy", side_effect=self._deny_gate()), \
             mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=fake_commodity):
            result = execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-2",
            )
        new_subtasks = list_runtime_attempt_subtasks(result["attempt"]["attempt_id"])
        gold = next(s for s in new_subtasks if s.get("entity_key") == "gold")
        self.assertEqual(gold["lifecycle_state"], "SUCCEEDED")
        self.assertEqual(gold["result_summary"].get("price"), 4400.0)

    def test_changed_arguments_invalidate_the_persisted_approval(self) -> None:
        """Digest is computed from the ACTUAL retry arguments -- an approval recorded for a
        different action digest (as if the arguments had changed) must not authorize this one."""
        from core.agent_runtime.attempt_retry import execute_attempt_retry

        parent, subtasks = self._make_parent()
        wrong_digest = compute_action_digest("web.research", {"asset_key": "silver", "kind": "commodity"})
        plan_digest = compute_plan_digest([wrong_digest])
        record_attempt_approval(
            attempt_id=parent["attempt_id"], subtask_id="p:market:gold",
            action_digest=wrong_digest, plan_digest=plan_digest, policy_version="v1",
            requested_scope="web.research", decision="allow",
        )

        with mock.patch("core.agent_runtime.live_data_plan.evaluate_approval_policy", side_effect=self._deny_gate()):
            result = execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-3",
            )
        new_subtasks = list_runtime_attempt_subtasks(result["attempt"]["attempt_id"])
        gold = next(s for s in new_subtasks if s.get("entity_key") == "gold")
        self.assertEqual(gold["lifecycle_state"], "WAITING_APPROVAL")

    def test_expired_approval_returns_to_waiting_approval(self) -> None:
        from core.agent_runtime.attempt_retry import execute_attempt_retry

        parent, subtasks = self._make_parent()
        action_digest = compute_action_digest("web.research", {"asset_key": "gold", "kind": "commodity"})
        plan_digest = compute_plan_digest([action_digest])
        expired_at = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        record_attempt_approval(
            attempt_id=parent["attempt_id"], subtask_id="p:market:gold",
            action_digest=action_digest, plan_digest=plan_digest, policy_version="v1",
            requested_scope="web.research", decision="allow", expires_at=expired_at,
        )

        with mock.patch("core.agent_runtime.live_data_plan.evaluate_approval_policy", side_effect=self._deny_gate()):
            result = execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-4",
            )
        new_subtasks = list_runtime_attempt_subtasks(result["attempt"]["attempt_id"])
        gold = next(s for s in new_subtasks if s.get("entity_key") == "gold")
        self.assertEqual(gold["lifecycle_state"], "WAITING_APPROVAL")

    def test_denial_is_never_treated_as_an_approval(self) -> None:
        from core.agent_runtime.attempt_retry import execute_attempt_retry

        parent, subtasks = self._make_parent()
        action_digest = compute_action_digest("web.research", {"asset_key": "gold", "kind": "commodity"})
        plan_digest = compute_plan_digest([action_digest])
        record_attempt_approval(
            attempt_id=parent["attempt_id"], subtask_id="p:market:gold",
            action_digest=action_digest, plan_digest=plan_digest, policy_version="v1",
            requested_scope="web.research", decision="deny",
        )

        with mock.patch("core.agent_runtime.live_data_plan.evaluate_approval_policy", side_effect=self._deny_gate()):
            result = execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-5",
            )
        new_subtasks = list_runtime_attempt_subtasks(result["attempt"]["attempt_id"])
        gold = next(s for s in new_subtasks if s.get("entity_key") == "gold")
        self.assertEqual(gold["lifecycle_state"], "WAITING_APPROVAL")

    def test_restart_does_not_silently_grant_an_unrecorded_approval_through_retry(self) -> None:
        """A "restart" is simulated by reconnecting to the SAME on-disk database with no approval
        ever recorded -- the retry path must resolve to WAITING_APPROVAL, never inferring a grant
        from the mere absence of a denial."""
        from core.agent_runtime.attempt_retry import execute_attempt_retry

        parent, subtasks = self._make_parent()
        # "Restart": reconnect fresh to the same db path, exactly as a new daemon process would.
        configure_runtime_continuity_db_path(str(self._db_path))

        with mock.patch("core.agent_runtime.live_data_plan.evaluate_approval_policy", side_effect=self._deny_gate()):
            result = execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-6",
            )
        new_subtasks = list_runtime_attempt_subtasks(result["attempt"]["attempt_id"])
        gold = next(s for s in new_subtasks if s.get("entity_key") == "gold")
        self.assertEqual(gold["lifecycle_state"], "WAITING_APPROVAL")

    def test_approve_survives_a_restart_and_still_authorizes_the_retry(self) -> None:
        """The durable counterpart: approve, "restart" (fresh reconnect to the same db file), then
        retry -- the SAME digest still authorizes, because it was actually persisted, not merely
        held in memory."""
        from core.agent_runtime.attempt_retry import execute_attempt_retry
        from core.live_quote_contract import LiveQuoteResult

        parent, subtasks = self._make_parent()
        action_digest = compute_action_digest("web.research", {"asset_key": "gold", "kind": "commodity"})
        plan_digest = compute_plan_digest([action_digest])
        record_attempt_approval(
            attempt_id=parent["attempt_id"], subtask_id="p:market:gold",
            action_digest=action_digest, plan_digest=plan_digest, policy_version="v1",
            requested_scope="web.research", decision="allow",
        )
        # "Restart": reconnect fresh to the same db path.
        configure_runtime_continuity_db_path(str(self._db_path))

        def fake_commodity(_query, targets, **_kwargs):
            return [LiveQuoteResult(
                asset_key="gold", asset_name="Gold", symbol="GC=F", value=4410.0, currency="USD",
                as_of="2026-08-06 12:05 UTC", source_label="Yahoo Finance", source_url="https://x",
                kind="commodity", unit_label="per troy ounce", change_percent=0.3,
            )]

        with mock.patch("core.agent_runtime.live_data_plan.evaluate_approval_policy", side_effect=self._deny_gate()), \
             mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=fake_commodity):
            result = execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-7",
            )
        new_subtasks = list_runtime_attempt_subtasks(result["attempt"]["attempt_id"])
        gold = next(s for s in new_subtasks if s.get("entity_key") == "gold")
        self.assertEqual(gold["lifecycle_state"], "SUCCEEDED")


class SabotageApprovalDigestValidationInRetryTests(unittest.TestCase):
    """Sabotage #10: remove approval digest validation in the RETRY path specifically -- with the
    digest check bypassed, a stale/wrong-action approval would wrongly authorize a different one."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmp.name) / "sabotage_approval_retry.db"
        run_migrations(db_path=self._db_path)
        configure_runtime_continuity_db_path(str(self._db_path))
        reset_runtime_continuity_state()

    def tearDown(self) -> None:
        reset_runtime_continuity_state()
        configure_runtime_continuity_db_path(None)
        self._tmp.cleanup()

    def test_sabotage_bypassing_is_approval_valid_authorizes_a_different_action(self) -> None:
        """Sabotage: patch `is_approval_valid` at its real source (`core.agent_runtime.
        attempt_approval` -- `_resolve_retry_approval_decisions` imports it lazily, so patching the
        attempt_retry module's namespace would not intercept the call) to only check `decision`,
        never comparing digests. Reproduces the exact defect Repair 8's digest check exists to
        prevent: an approval granted for a DIFFERENT action wrongly authorizes this one."""
        parent = create_runtime_attempt(session_id="s1", original_request="gold price", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(
            attempt_id=parent["attempt_id"], subtask_id="p:market:gold", plan_id="plan-orig",
            operation="market_quote", entity_type="commodity", entity_key="gold",
            arguments={"asset_key": "gold", "kind": "commodity"}, lifecycle_state="FAILED",
            failure_class="transient", failure_reason="HTTPError: HTTP Error 500",
            retryable=True, retry_reason="transient tool failure",
        )
        finalize_runtime_attempt(parent["attempt_id"])
        from core.runtime_continuity import get_runtime_attempt

        parent = get_runtime_attempt(parent["attempt_id"])
        subtasks = list_runtime_attempt_subtasks(parent["attempt_id"])

        wrong_digest = compute_action_digest("web.research", {"asset_key": "silver", "kind": "commodity"})
        plan_digest = compute_plan_digest([wrong_digest])
        record_attempt_approval(
            attempt_id=parent["attempt_id"], subtask_id="p:market:gold",
            action_digest=wrong_digest, plan_digest=plan_digest, policy_version="v1",
            requested_scope="web.research", decision="allow",
        )

        def sabotaged_is_valid(approval, *, action_digest, plan_digest, policy_version, now_iso=None):
            if approval is None:
                return False, "no approval"
            return str(approval.get("decision") or "") == "allow", "sabotaged: digest never compared"

        def deny_gate(plan, *, source_context):
            return {task.subtask_id: _DenyDecision() for task in plan.subtasks}

        from core.agent_runtime.attempt_retry import execute_attempt_retry
        from core.live_quote_contract import LiveQuoteResult

        def fake_commodity(_query, targets, **_kwargs):
            return [LiveQuoteResult(
                asset_key="gold", asset_name="Gold", symbol="GC=F", value=4420.0, currency="USD",
                as_of="2026-08-06 12:10 UTC", source_label="Yahoo Finance", source_url="https://x",
                kind="commodity", unit_label="per troy ounce", change_percent=0.1,
            )]

        with mock.patch("core.agent_runtime.attempt_approval.is_approval_valid", sabotaged_is_valid), \
             mock.patch("core.agent_runtime.live_data_plan.evaluate_approval_policy", side_effect=deny_gate), \
             mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=fake_commodity):
            result = execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-sabotage-bypass",
            )
        new_subtasks = list_runtime_attempt_subtasks(result["attempt"]["attempt_id"])
        gold = next(s for s in new_subtasks if s.get("entity_key") == "gold")
        self.assertEqual(
            gold["lifecycle_state"], "SUCCEEDED",
            "sabotage should have wrongly authorized a mismatched-digest approval",
        )

    def test_control_the_real_code_rejects_the_mismatched_digest(self) -> None:
        parent = create_runtime_attempt(session_id="s1", original_request="gold price", answer_mode="LIVE_DATA")
        upsert_runtime_attempt_subtask(
            attempt_id=parent["attempt_id"], subtask_id="p:market:gold", plan_id="plan-orig",
            operation="market_quote", entity_type="commodity", entity_key="gold",
            arguments={"asset_key": "gold", "kind": "commodity"}, lifecycle_state="FAILED",
            failure_class="transient", failure_reason="HTTPError: HTTP Error 500",
            retryable=True, retry_reason="transient tool failure",
        )
        finalize_runtime_attempt(parent["attempt_id"])
        from core.runtime_continuity import get_runtime_attempt

        parent = get_runtime_attempt(parent["attempt_id"])
        subtasks = list_runtime_attempt_subtasks(parent["attempt_id"])

        wrong_digest = compute_action_digest("web.research", {"asset_key": "silver", "kind": "commodity"})
        plan_digest = compute_plan_digest([wrong_digest])
        record_attempt_approval(
            attempt_id=parent["attempt_id"], subtask_id="p:market:gold",
            action_digest=wrong_digest, plan_digest=plan_digest, policy_version="v1",
            requested_scope="web.research", decision="allow",
        )

        def deny_gate(plan, *, source_context):
            return {task.subtask_id: _DenyDecision() for task in plan.subtasks}

        from core.agent_runtime.attempt_retry import execute_attempt_retry

        with mock.patch("core.agent_runtime.live_data_plan.evaluate_approval_policy", side_effect=deny_gate):
            result = execute_attempt_retry(
                parent, subtasks, session_id="s1", checkpoint_id="cp-1", trigger_user_turn_id="turn-sabotage",
            )
        new_subtasks = list_runtime_attempt_subtasks(result["attempt"]["attempt_id"])
        gold = next(s for s in new_subtasks if s.get("entity_key") == "gold")
        self.assertEqual(gold["lifecycle_state"], "WAITING_APPROVAL", "the real code must reject the mismatched digest")


if __name__ == "__main__":
    unittest.main()
