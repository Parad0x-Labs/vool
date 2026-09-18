"""The background receipt anchor is retired; the finalizer no longer dispatches one.

The old subject of this module — a daemon worker that broadcast a SOL-spending memo transaction
after `finalize_parent_response` returned and persisted the tx signature — was an unattended
signed broadcast, exactly the bypass the canonical wallet lifecycle (core.wallet) forbids. Every
test here now proves the opposite contract:

* with ``VOOL_ANCHOR_RECEIPTS`` unset the three anchor entry points are strict no-ops: they
  return None, spawn no thread and file no fault;
* with ``VOOL_ANCHOR_RECEIPTS=1`` they refuse typed (``wallet_legacy_surface_retired``) with a
  fault receipt, still spawning nothing and reaching no RPC;
* `finalize_parent_response` completes under both settings, never raises the refusal, never
  starts a worker, and leaves ``anchored_signature`` NULL.
"""
from __future__ import annotations

import os
import threading
import unittest
import uuid
from unittest import mock

import core.solana_anchor as anchor
from core.final_response_store import get_final_response, store_final_response
from core.solana_anchor import anchor_vault_proof, dispatch_anchor_in_background, submit_memo_anchor
from core.wallet.errors import WalletFault
from storage.migrations import run_migrations

LEGACY = "wallet_legacy_surface_retired"


def _new_task_id() -> str:
    return f"task-{uuid.uuid4().hex}"


def _env_without_anchor_flag() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k != "VOOL_ANCHOR_RECEIPTS"}


def _legacy_receipts() -> list:
    from core.faults.recorder import list_faults

    return [f for f in list_faults(limit=200) if f.code == LEGACY]


class _RpcTrap:
    """Any RPC reached through the anchor module's old door is a test failure, not a network call."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def __call__(self, method: str, params: list, **_kw) -> object:
        self.calls.append(method)
        raise AssertionError(f"the retired anchor reached the RPC door: {method}")


class _ThreadWatch:
    def __enter__(self) -> _ThreadWatch:
        self.before = set(threading.enumerate())
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def spawned(self) -> list[threading.Thread]:
        return [t for t in threading.enumerate() if t not in self.before]


class AnchorEntryPointsTests(unittest.TestCase):
    def setUp(self) -> None:
        run_migrations()
        self.trap = _RpcTrap()
        patcher = mock.patch.object(anchor, "_rpc_call", self.trap, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_flag_off_every_entry_point_is_a_strict_noop(self) -> None:
        receipts_before = len(_legacy_receipts())
        with mock.patch.dict("os.environ", _env_without_anchor_flag(), clear=True), _ThreadWatch() as watch:
            self.assertIsNone(dispatch_anchor_in_background("task-x", "ab" * 32, 0.9))
            self.assertIsNone(anchor_vault_proof("task-x", "ab" * 32, 0.9))
            self.assertIsNone(anchor._anchor_and_persist("task-x", "ab" * 32, 0.9))
        self.assertEqual(watch.spawned(), [])
        self.assertEqual(self.trap.calls, [])
        self.assertEqual(len(_legacy_receipts()), receipts_before, "a no-op files nothing")
        self.assertFalse(anchor.anchor_enabled())

    def test_flag_on_dispatch_refuses_typed_and_spawns_nothing(self) -> None:
        with mock.patch.dict("os.environ", {"VOOL_ANCHOR_RECEIPTS": "1"}), _ThreadWatch() as watch:
            with self.assertRaises(WalletFault) as ctx:
                dispatch_anchor_in_background("task-y", "ab" * 32, 0.5)
        fault = ctx.exception
        self.assertEqual(fault.code, LEGACY)
        self.assertTrue(fault.fault_id.startswith("fault-"), fault.to_dict())
        self.assertEqual(fault.context["surface"], "solana_anchor.dispatch_anchor_in_background")
        self.assertEqual(watch.spawned(), [])
        self.assertEqual(self.trap.calls, [])
        from core.faults.recorder import fault_by_id

        self.assertIsNotNone(fault_by_id(fault.fault_id))

    def test_flag_on_anchor_vault_proof_refuses_before_any_rpc(self) -> None:
        with mock.patch.dict("os.environ", {"VOOL_ANCHOR_RECEIPTS": "1"}):
            with self.assertRaises(WalletFault) as ctx:
                anchor_vault_proof("task-z", "ab" * 32, 1.0)
        self.assertEqual(ctx.exception.code, LEGACY)
        self.assertEqual(ctx.exception.context["surface"], "solana_anchor.anchor_vault_proof")
        self.assertEqual(self.trap.calls, [])

    def test_submit_memo_anchor_refuses_regardless_of_the_flag(self) -> None:
        for env in ({"VOOL_ANCHOR_RECEIPTS": "1"}, {"VOOL_ANCHOR_RECEIPTS": "0"}):
            with mock.patch.dict("os.environ", env):
                with self.assertRaises(WalletFault) as ctx:
                    submit_memo_anchor("ab" * 32)
            self.assertEqual(ctx.exception.code, LEGACY)
            self.assertEqual(ctx.exception.context["surface"], "solana_anchor.submit_memo_anchor")
        with mock.patch.dict("os.environ", _env_without_anchor_flag(), clear=True):
            with self.assertRaises(WalletFault):
                submit_memo_anchor("ab" * 32)
        self.assertEqual(self.trap.calls, [])
        self.assertTrue(anchor._anchor_spend_blocked())

    def test_refusal_never_touches_a_finalized_row(self) -> None:
        task_id = _new_task_id()
        store_final_response(parent_task_id=task_id, raw="raw", rendered="rendered", status="finalized", confidence=0.9)
        with mock.patch.dict("os.environ", {"VOOL_ANCHOR_RECEIPTS": "1"}):
            with self.assertRaises(WalletFault):
                dispatch_anchor_in_background(task_id, "result-hash", 0.9)
        row = get_final_response(task_id)
        assert row is not None
        self.assertIsNone(row["anchored_signature"])


class FinalizerNoAnchorTests(unittest.TestCase):
    """End-to-end: finalize_parent_response no longer dispatches any anchor, on or off."""

    def setUp(self) -> None:
        run_migrations()

    def _plan(self, task_id: str):
        from core.task_reassembler import ReassembledPlan

        return ReassembledPlan(
            parent_task_id=task_id,
            is_complete=True,
            merged_summary="assembled answer",
            merged_evidence=["signal a"],
            merged_steps=["step a"],
            pending_subtasks=0,
            confidence=0.9,
            completeness_score=0.9,
            result_hash="result-hash-" + task_id,
        )

    def _finalize(self, task_id: str):
        from core import finalizer

        with mock.patch.object(finalizer, "reassemble_parent_task", return_value=self._plan(task_id)):
            with mock.patch.object(finalizer, "export_task_bundle"):
                return finalizer.finalize_parent_response(task_id)

    def test_finalizer_module_holds_no_anchor_dispatch(self) -> None:
        from core import finalizer

        self.assertFalse(hasattr(finalizer, "dispatch_anchor_in_background"))
        self.assertFalse(hasattr(finalizer, "anchor_vault_proof"))

    def test_finalize_with_anchoring_enabled_completes_without_the_refusal_or_a_worker(self) -> None:
        # If anyone re-wires the dispatch, this turn would raise the typed refusal (or, worse, spawn
        # the old worker). Neither may happen: finalization is not a money path.
        task_id = _new_task_id()
        receipts_before = len(_legacy_receipts())
        with mock.patch.dict("os.environ", {"VOOL_ANCHOR_RECEIPTS": "1"}), _ThreadWatch() as watch:
            with mock.patch.object(anchor, "submit_memo_anchor", side_effect=AssertionError("broadcast reached")):
                result = self._finalize(task_id)
        assert result is not None
        self.assertEqual(result.parent_task_id, task_id)
        self.assertEqual([t for t in watch.spawned() if getattr(t, "_target", None) is anchor._anchor_and_persist], [])
        self.assertEqual(len(_legacy_receipts()), receipts_before)
        row = get_final_response(task_id)
        assert row is not None
        self.assertIsNone(row["anchored_signature"])

    def test_finalize_with_anchoring_disabled_persists_no_signature(self) -> None:
        task_id = _new_task_id()
        with mock.patch.dict("os.environ", _env_without_anchor_flag(), clear=True):
            with mock.patch.object(anchor, "anchor_vault_proof", side_effect=AssertionError("anchor reached")):
                result = self._finalize(task_id)
        assert result is not None
        row = get_final_response(task_id)
        assert row is not None
        self.assertIsNone(row["anchored_signature"])


if __name__ == "__main__":
    unittest.main()
