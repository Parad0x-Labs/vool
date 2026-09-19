"""THE FILE-WRITE DOOR — budgets at the one workspace-mutation choke point.

`_dispatch_with_mutation_activity` is where every workspace WRITE and DELETE
the runtime dispatches crosses; the reservation rides it: RESERVED before the
handler, CONSUMED immediately before the handler moves a byte, terminal
reconciled from the handler's real outcome. Refusal ⇒ the handler NEVER
runs — zero filesystem writes.
"""
from __future__ import annotations

import threading
from pathlib import Path

from core import effect_budget as eb
from tests.effect_budget.conftest import *  # noqa: F403 — fixtures


def _ok_handler(sink: list):
    from core.runtime_execution_tools import RuntimeExecutionResult

    def handler(*args, **kwargs):
        sink.append("ran")
        return RuntimeExecutionResult(handled=True, ok=True, status="ok")

    return handler


def _door(intent: str, handler, *, workspace_root: Path = Path("/tmp/eb-door-wt")):
    from core.runtime_execution_tools import _dispatch_with_mutation_activity

    return _dispatch_with_mutation_activity(
        intent,
        workspace_root=workspace_root,
        source_context={"session_id": "s-door", "workspace_root": str(workspace_root)},
        handler=handler,
        arguments={},
    )


def test_exhausted_budget_means_zero_filesystem_writes(set_budget):
    """The amendment's first proof, at the door: budget 1, one write spent,
    the second mutation returns the typed refusal and the handler NEVER
    ran."""
    set_budget(("file_write", eb.SCOPE_SESSION, 1))
    from core.effect_gateway import (
        close_effect_receipt_scope,
        open_effect_receipt_scope,
    )

    open_effect_receipt_scope({"session_id": "s-door", "workspace_root": "/wt"})
    try:
        sink_one: list = []
        first = _door("workspace.write_file", _ok_handler(sink_one))
        assert first.ok is True and sink_one == ["ran"]
        sink_two: list = []
        second = _door("workspace.write_file", _ok_handler(sink_two))
        assert second.ok is False
        assert second.status == "blocked_by_effect_budget"
        assert second.details["effect_budget"]["code"] == eb.REFUSAL_BUDGET_EXCEEDED
        assert sink_two == [], "ZERO filesystem writes: the handler never ran"
    finally:
        close_effect_receipt_scope()
    rows = eb.reservation_rows()
    assert len(rows) == 1 and rows[0]["state"] == eb.RESERVATION_CONSUMED


def test_successful_mutation_consumes_and_terminalizes(set_budget):
    set_budget(("file_write", eb.SCOPE_SESSION, 2))
    from core.effect_gateway import (
        close_effect_receipt_scope,
        effect_outcomes,
        open_effect_receipt_scope,
    )

    open_effect_receipt_scope({"session_id": "s-door", "workspace_root": "/wt"})
    try:
        result = _door("workspace.write_file", _ok_handler([]))
        assert result.ok is True
        outcome = next(o for o in effect_outcomes() if o["effect_class"] == "file_write")
        assert outcome["lifecycle"] == "succeeded" and outcome["transport_ran"] is True
    finally:
        close_effect_receipt_scope()
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_CONSUMED


def test_failed_mutation_terminalizes_failed_and_is_not_refunded(set_budget):
    """A handler that raises: the terminal is FAILED, the unit stays
    consumed — a write that attempted and failed is not a refund."""
    set_budget(("file_write", eb.SCOPE_SESSION, 2))
    from core.effect_gateway import (
        close_effect_receipt_scope,
        effect_outcomes,
        open_effect_receipt_scope,
    )

    def exploding_handler(*args, **kwargs):
        raise RuntimeError("disk full")

    open_effect_receipt_scope({"session_id": "s-door", "workspace_root": "/wt"})
    try:
        import pytest as _pytest

        with _pytest.raises(RuntimeError, match="disk full"):
            _door("workspace.write_file", exploding_handler)
        outcome = next(o for o in effect_outcomes() if o["effect_class"] == "file_write")
        assert outcome["lifecycle"] == "failed"
    finally:
        close_effect_receipt_scope()
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_CONSUMED
    assert eb.budget_status("file_write", session_id="s-door")[0].used == 1


def test_pre_execution_release_returns_the_unit(set_budget):
    """Cancelled/pre-execution: an effect RESERVED (authorized) whose
    dispatch never began releases at scope close — the rollback law at this
    door's own ledger."""
    set_budget(("file_write", eb.SCOPE_SESSION, 1))
    from core.effect_gateway import (
        DECISION_ALLOWED,
        LIFECYCLE_AUTHORIZED,
        EffectReceipt,
        close_effect_receipt_scope,
        open_effect_receipt_scope,
    )

    ledger = open_effect_receipt_scope({"session_id": "s-door", "workspace_root": "/wt"})
    reserved = ledger.open_effect(
        EffectReceipt(
            effect_class="file_write",
            decision=DECISION_ALLOWED,
            lifecycle=LIFECYCLE_AUTHORIZED,
            reason="authorized but the turn was cancelled before dispatch",
        )
    )
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_RESERVED
    close_effect_receipt_scope()
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_RELEASED
    assert eb.budget_status("file_write", session_id="s-door")[0].remaining == 1


def test_final_unit_race_through_the_door(set_budget):
    """Concurrent mutations racing the last configured write through the
    door's DIRECT leg (no ambient ledger — the leg a background caller
    takes): exactly the configured number run, the rest are typed refusals,
    and no handler runs beyond the limit."""
    limit = 3
    set_budget(("file_write", eb.SCOPE_PROJECT, limit))
    handler_runs: list = []
    refusals: list = []
    barrier = threading.Barrier(8)
    lock = threading.Lock()

    def worker(index: int) -> None:
        barrier.wait()
        local_sink: list = []
        result = _door(
            "workspace.write_file",
            _ok_handler(local_sink),
            workspace_root=Path("/tmp/eb-race-project"),
        )
        with lock:
            if result.ok:
                handler_runs.extend(local_sink)
            else:
                refusals.append(result.details["effect_budget"]["code"])

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(handler_runs) == limit, (
        f"exactly {limit} handlers may run (got {len(handler_runs)})"
    )
    assert len(refusals) == 8 - limit
    assert all(code == eb.REFUSAL_BUDGET_EXCEEDED for code in refusals)
    assert len(eb.reservation_rows()) == limit


def test_unbudgeted_mutations_are_unchanged():
    """No operator rule: the door adds nothing — base behavior, no rows, no
    events."""
    sink: list = []
    result = _door("workspace.write_file", _ok_handler(sink))
    assert result.ok is True and sink == ["ran"]
    assert eb.reservation_rows() == []
    assert [e for e in eb.budget_events() if e["event_kind"] == "reserved"] == []


def test_sabotage_dropped_door_guard_lets_the_write_through(set_budget, monkeypatch):
    """RED-PROOF: with the door's budget gate sabotaged away, an exhausted
    budget still runs the handler — exactly the hole the guard prevents."""
    import core.runtime_execution_tools as door_module

    set_budget(("file_write", eb.SCOPE_SESSION, 1))
    from core.effect_gateway import (
        close_effect_receipt_scope,
        open_effect_receipt_scope,
    )

    open_effect_receipt_scope({"session_id": "s-door", "workspace_root": "/wt"})
    try:
        first_sink: list = []
        assert _door("workspace.write_file", _ok_handler(first_sink)).ok is True
        sink: list = []
        refused = _door("workspace.write_file", _ok_handler(sink))
        assert refused.ok is False and sink == []  # the honest guard blocks
        monkeypatch.setattr(door_module, "_open_file_write_budget_effect", lambda *a, **k: None)
        try:
            smuggled_sink: list = []
            smuggled = _door("workspace.write_file", _ok_handler(smuggled_sink))
            assert smuggled.ok is True and smuggled_sink == ["ran"], (
                "sabotage failed — the red-proof would be vacuous"
            )
        finally:
            monkeypatch.undo()
        blocked_sink: list = []
        assert _door("workspace.write_file", _ok_handler(blocked_sink)).ok is False
        assert blocked_sink == []  # restored
    finally:
        close_effect_receipt_scope()
