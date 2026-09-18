"""Pause-new-work coordination + destructive-work guard + active-state receipts."""
from __future__ import annotations

from core.updater.work import (
    ActiveStateReceipt,
    DestructiveWorkResolution,
    PauseReason,
    WorkCoordinator,
    WorkHandle,
    prepare_to_pause,
)


def _coordinator(*handles: WorkHandle) -> WorkCoordinator:
    coordinator = WorkCoordinator()
    for handle in handles:
        coordinator.register(handle)
    return coordinator


class TestPauseGate:
    def test_no_work_pauses_cleanly(self):
        decision = prepare_to_pause(WorkCoordinator())
        assert decision.ok
        assert decision.reason is PauseReason.OK

    def test_plain_work_is_paused_via_hook(self):
        paused: list[str] = []
        coordinator = _coordinator(WorkHandle("t1", "answering a question", pause=lambda: paused.append("t1")))
        decision = prepare_to_pause(coordinator)
        assert decision.ok
        assert paused == ["t1"]

    def test_destructive_work_blocks_restart(self):
        coordinator = _coordinator(
            WorkHandle("chat", "answering a question"),
            WorkHandle("cleanup", "deleting old files", destructive=True),
        )
        decision = prepare_to_pause(coordinator)
        assert not decision.ok
        assert decision.reason is PauseReason.DESTRUCTIVE_WORK_ACTIVE
        assert [h.id for h in decision.destructive] == ["cleanup"]
        assert "deleting old files" in decision.plain_message

    def test_explicit_resolution_named_for_the_work_allows_pause(self):
        coordinator = _coordinator(WorkHandle("cleanup", "deleting old files", destructive=True))
        resolution = DestructiveWorkResolution(operator_ack="operator saw receipt", work_ids=("cleanup",))
        decision = prepare_to_pause(coordinator, resolution=resolution)
        assert decision.ok

    def test_resolution_for_different_work_does_not_apply(self):
        coordinator = _coordinator(WorkHandle("cleanup", "deleting old files", destructive=True))
        resolution = DestructiveWorkResolution(operator_ack="wrong target", work_ids=("something-else",))
        decision = prepare_to_pause(coordinator, resolution=resolution)
        assert not decision.ok
        assert decision.reason is PauseReason.DESTRUCTIVE_WORK_ACTIVE

    def test_completed_work_no_longer_blocks(self):
        coordinator = _coordinator(WorkHandle("cleanup", "deleting old files", destructive=True))
        coordinator.complete("cleanup")
        assert prepare_to_pause(coordinator).ok

    def test_pause_hook_failure_is_not_hidden(self):
        def boom() -> None:
            raise RuntimeError("pause refused")

        coordinator = _coordinator(WorkHandle("t1", "working", pause=boom))
        decision = prepare_to_pause(coordinator)
        assert decision.ok  # non-destructive work cannot veto the update
        # the handle stays registered, so the receipt still tells the truth
        assert coordinator.active()[0].id == "t1"


class TestActiveStateReceipt:
    def test_capture_records_active_and_destructive(self):
        coordinator = _coordinator(
            WorkHandle("chat", "answering a question"),
            WorkHandle("wipe", "clearing the cache folder", destructive=True),
        )
        receipt = ActiveStateReceipt.capture("tx-1", coordinator, now=123.0)
        assert receipt.txid == "tx-1"
        assert receipt.created_at == 123.0
        assert len(receipt.active_work) == 2
        assert [w["id"] for w in receipt.destructive_work] == ["wipe"]
        payload = receipt.to_dict()
        assert payload["destructive_work"][0]["description"] == "clearing the cache folder"
