from __future__ import annotations

import unittest
import uuid
from types import SimpleNamespace
from unittest import mock

from core.credit_ledger import award_credits, get_credit_balance, get_free_tier_dispatch_usage
from core.parent_orchestrator import orchestrate_parent_task
from core.task_capsule import verify_task_capsule
from network.assist_models import TaskOffer
from storage.db import get_connection
from storage.migrations import run_migrations
from tests.task_offer_fixtures import build_offer_for_capsule, build_signed_capsule


def _real_subtask(points: int) -> SimpleNamespace:
    task_id = f"sub-{uuid.uuid4().hex}"
    capsule = build_signed_capsule(task_id=task_id, points=points)
    offer = build_offer_for_capsule(capsule, points=points)
    return SimpleNamespace(subtask_id=task_id, offer=offer, capsule=capsule, required_capabilities=["research"])


class ParentOrchestratorBudgetTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM compute_credit_ledger")
            conn.execute("DELETE FROM swarm_dispatch_budget_events")
            conn.commit()
        finally:
            conn.close()

    def test_paid_dispatch_preserves_offer_priority(self) -> None:
        peer_id = "peer-paid-orch"
        award_credits(peer_id, 20.0, "seed", receipt_id=f"seed-{uuid.uuid4().hex}")
        subtasks = [_real_subtask(5), _real_subtask(5)]
        with mock.patch("core.parent_orchestrator._subtask_ids_for_parent", return_value=[]), mock.patch(
            "core.parent_orchestrator.should_decompose", return_value=True
        ), mock.patch("core.parent_orchestrator.predict_local_override_necessity", return_value=False), mock.patch(
            "core.parent_orchestrator._resolved_subtask_width", return_value=2
        ), mock.patch("core.parent_orchestrator.decompose_task", return_value=subtasks), mock.patch(
            "core.parent_orchestrator.broadcast_decomposed_subtasks", return_value=2
        ) as broadcast, mock.patch("network.signer.get_local_peer_id", return_value=peer_id):
            result = orchestrate_parent_task(
                parent_task_id=f"parent-{uuid.uuid4().hex}",
                user_input="research this deeply",
                classification={"task_class": "research"},
            )
        self.assertEqual(result.action, "decomposed")
        self.assertEqual(broadcast.call_count, 1)
        self.assertEqual([sub.offer.priority for sub in subtasks], ["normal", "normal"])
        self.assertEqual([sub.offer.reward_hint.points for sub in subtasks], [5, 5])
        self.assertAlmostEqual(get_credit_balance(peer_id), 10.0)
        self.assertAlmostEqual(get_free_tier_dispatch_usage(peer_id), 0.0)

    def test_free_tier_dispatch_downgrades_rewards_and_priority(self) -> None:
        peer_id = "peer-free-orch"
        subtasks = [_real_subtask(4), _real_subtask(4)]
        with mock.patch("core.parent_orchestrator._subtask_ids_for_parent", return_value=[]), mock.patch(
            "core.parent_orchestrator.should_decompose", return_value=True
        ), mock.patch("core.parent_orchestrator.predict_local_override_necessity", return_value=False), mock.patch(
            "core.parent_orchestrator._resolved_subtask_width", return_value=2
        ), mock.patch("core.parent_orchestrator.decompose_task", return_value=subtasks), mock.patch(
            "core.parent_orchestrator.broadcast_decomposed_subtasks", return_value=2
        ), mock.patch("network.signer.get_local_peer_id", return_value=peer_id), mock.patch(
            "core.credit_ledger.policy_engine.get"
        ) as get_policy:
            get_policy.side_effect = lambda path, default=None: {
                "economics.free_tier_daily_swarm_points": 12.0,
                "economics.free_tier_max_dispatch_points": 12.0,
            }.get(path, default)
            result = orchestrate_parent_task(
                parent_task_id=f"parent-{uuid.uuid4().hex}",
                user_input="research this deeply",
                classification={"task_class": "research"},
            )
        self.assertEqual(result.action, "decomposed")
        self.assertEqual([sub.offer.reward_hint.points for sub in subtasks], [0, 0])
        self.assertEqual([sub.offer.priority for sub in subtasks], ["background", "background"])
        self.assertTrue(all(sub.capsule.learning_allowed for sub in subtasks))
        self.assertTrue(all(sub.offer.capsule["learning_allowed"] for sub in subtasks))
        self.assertAlmostEqual(get_free_tier_dispatch_usage(peer_id), 8.0)
        for sub in subtasks:
            # The downgrade mutates the capsule, so it must be re-hashed and
            # re-signed or honest helpers reject it with a hash mismatch.
            verified = verify_task_capsule(sub.offer.capsule)
            self.assertTrue(verified.learning_allowed)
            # The downgraded offer must still validate as a wire payload.
            revalidated = TaskOffer.model_validate(sub.offer.model_dump(mode="json"))
            self.assertEqual(revalidated.priority, "background")

    def test_dispatch_blocks_when_free_tier_budget_is_exhausted(self) -> None:
        peer_id = "peer-blocked-orch"
        subtasks = [_real_subtask(3), _real_subtask(3)]
        with mock.patch("core.credit_ledger.policy_engine.get") as get_policy:
            get_policy.side_effect = lambda path, default=None: {
                "economics.free_tier_daily_swarm_points": 6.0,
                "economics.free_tier_max_dispatch_points": 6.0,
            }.get(path, default)
            from core.credit_ledger import reserve_swarm_dispatch_budget

            reserve_swarm_dispatch_budget(peer_id, 6.0, receipt_id=f"prefill-{uuid.uuid4().hex}")
            with mock.patch("core.parent_orchestrator._subtask_ids_for_parent", return_value=[]), mock.patch(
                "core.parent_orchestrator.should_decompose", return_value=True
            ), mock.patch("core.parent_orchestrator.predict_local_override_necessity", return_value=False), mock.patch(
                "core.parent_orchestrator._resolved_subtask_width", return_value=2
            ), mock.patch("core.parent_orchestrator.decompose_task", return_value=subtasks), mock.patch(
                "core.parent_orchestrator.broadcast_decomposed_subtasks"
            ) as broadcast, mock.patch("network.signer.get_local_peer_id", return_value=peer_id):
                result = orchestrate_parent_task(
                    parent_task_id=f"parent-{uuid.uuid4().hex}",
                    user_input="research this deeply",
                    classification={"task_class": "research"},
                )
        self.assertEqual(result.action, "no_action")
        self.assertIn("free-tier budget", result.reason)
        broadcast.assert_not_called()


if __name__ == "__main__":
    unittest.main()
