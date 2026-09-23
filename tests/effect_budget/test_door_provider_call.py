"""THE PROVIDER-CALL DOOR — budgets at the seal, consumed at the permit.

`seal_provider_invocation` is the one authorization point every production
transport call site converges on; `ProviderInvocationPermit.consume()` is the
one-use handoff immediately before transport. The budget rides exactly that
shape: RESERVED at the seal (before any I/O), CONSUMED at consume() (the
moment the model call is about to run), spend truth reconciled through the
permit's `settle_budget_cost`.
"""
from __future__ import annotations

import threading

import pytest

from core import effect_budget as eb
from tests.effect_budget.conftest import *  # noqa: F403 — fixtures


@pytest.fixture()
def provider_home(tmp_path):
    from core.runtime_paths import configure_runtime_home
    from tests.effect_budget import money_race_probe as probe

    # Restore exactly what was pinned: configure_runtime_home(None) would CLEAR the session
    # home the root conftest installed for the run, not undo this fixture's pin.
    runtime_state = probe.runtime_state_snapshot()
    configure_runtime_home(tmp_path)
    try:
        yield tmp_path
    finally:
        probe.restore_runtime_state(runtime_state)


def _seal(provider_home):
    from core.context_manifest import build_context_manifest
    from core.provenance_store import store_manifest
    from core.provider_invocation_gateway import seal_direct_provider_invocation

    manifest = build_context_manifest(
        task_id="task-budget-door",
        trace_id="request-budget-door",
        evidence_items=[],
        source_metadata=[],
        chat_id="chat-budget",
        project_id="project-budget",
        capsule_version="none",
    )
    store_manifest(manifest)
    return seal_direct_provider_invocation(
        provider_id="ollama:budget-door",
        model_id="local-test-model",
        operation="chat",
        payload={"model": "local-test-model", "messages": []},
        request_id="request-budget-door",
        context_manifest={
            "context_manifest_id": manifest.manifest_id,
            "chat_id": "chat-budget",
            "project_id": "project-budget",
            "capsule_version": "none",
            "items_included": [],
            "items_excluded": [],
        },
        max_output_tokens=16,
    )


def test_seal_reserves_and_consume_spends_the_unit(provider_home, set_budget):
    set_budget(("provider_call", eb.SCOPE_PROJECT, 2))
    permit = _seal(provider_home)
    assert permit.budget_reservation_id, "the seal reserved before any I/O"
    rows = eb.reservation_rows()
    assert rows[0]["state"] == eb.RESERVATION_RESERVED
    assert rows[0]["budget_class"] == "provider_call"
    permit.consume()  # the transport handoff
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_CONSUMED


def test_exhausted_budget_prevents_the_model_call_completely(provider_home, set_budget):
    """ZERO model calls on an exhausted provider budget: the seal itself
    refuses, so no permit, no consume, no transport — provable by there being
    no reservation at all beyond the limit."""
    set_budget(("provider_call", eb.SCOPE_PROJECT, 1))
    first = _seal(provider_home)
    first.consume()  # the one allowed call runs
    from core.effect_budget import EffectBudgetRefusedError

    with pytest.raises(EffectBudgetRefusedError) as refusal:
        _seal(provider_home)
    assert refusal.value.code == eb.REFUSAL_BUDGET_EXCEEDED
    assert len(eb.reservation_rows()) == 1, "the refused seal reserved nothing"
    assert eb.budget_events("refused")[0]["budget_class"] == "provider_call"


def test_released_reservation_refuses_at_consume(provider_home, set_budget):
    """A permit whose authorization was rolled back before it ran CANNOT
    spend its returned unit: consume() raises, the transport never gets the
    payload — the model call is prevented completely."""
    set_budget(("provider_call", eb.SCOPE_PROJECT, 1))
    permit = _seal(provider_home)
    eb.release_reservation(
        permit.budget_reservation_id, reason="turn rolled back before the call"
    )
    from core.effect_budget import EffectBudgetRefusedError

    with pytest.raises(EffectBudgetRefusedError) as refusal:
        permit.consume()
    assert refusal.value.code == eb.REFUSAL_STATE


def test_final_unit_race_through_the_seal(provider_home, set_budget):
    """Concurrent seals for the last configured permits: exactly the
    configured number of permits exist at the end — the rest are typed
    refusals, and no reservation row exceeds the limit."""
    limit = 3
    set_budget(("provider_call", eb.SCOPE_PROJECT, limit))
    permits: list = []
    refusals: list[eb.EffectBudgetRefusedError] = []
    barrier = threading.Barrier(8)
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            permit = _seal(provider_home)
            with lock:
                permits.append(permit)
        except eb.EffectBudgetRefusedError as refusal:
            with lock:
                refusals.append(refusal)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(permits) == limit, "exactly the configured number of calls"
    assert len(refusals) == 8 - limit
    assert all(r.code == eb.REFUSAL_BUDGET_EXCEEDED for r in refusals)
    assert len(eb.reservation_rows()) == limit


def test_retry_does_not_double_reserve_or_double_consume(provider_home, set_budget):
    """The retry law through the real door: the SAME logical effect re-sealed
    as retry_of continues its reservation; consume twice on one permit is the
    permit's own one-use law; the unit is counted exactly once."""
    set_budget(("provider_call", eb.SCOPE_SESSION, 2))
    from core.effect_gateway import (
        DECISION_ALLOWED,
        LIFECYCLE_AUTHORIZED,
        EffectReceipt,
        close_effect_receipt_scope,
        open_effect_receipt_scope,
    )

    ledger = open_effect_receipt_scope({"session_id": "s-retry", "workspace_root": "/p"})
    try:
        first = ledger.open_effect(
            EffectReceipt(
                effect_class="provider_call",
                decision=DECISION_ALLOWED,
                lifecycle=LIFECYCLE_AUTHORIZED,
                reason="first attempt",
            )
        )
        first.begin_attempt()
        first.fail(reason="timeout")
        retry = ledger.open_effect(
            EffectReceipt(
                effect_class="provider_call",
                decision=DECISION_ALLOWED,
                lifecycle=LIFECYCLE_AUTHORIZED,
                reason="retry",
            ),
            retry_of=first.effect_id,
        )
        retry.begin_attempt()
        retry.succeed()
        assert retry.effect_id == first.effect_id
        assert len(eb.reservation_rows()) == 1, "one logical effect, one unit"
        assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_CONSUMED
        status = eb.budget_status("provider_call", session_id="s-retry")[0]
        assert status.used == 1, "two attempts, ONE consumed unit"
    finally:
        close_effect_receipt_scope()


def test_unknown_outcome_keeps_the_unit_and_stays_unknown(provider_home, set_budget):
    """An ambiguous external outcome (transport crashed mid-call, no terminal
    anyone can observe): the reservation stays CONSUMED — never refunded —
    and the ledger's outcome for the effect is honestly UNKNOWN/unresolved."""
    set_budget(("provider_call", eb.SCOPE_SESSION, 2))
    from core.effect_gateway import (
        DECISION_ALLOWED,
        LIFECYCLE_AUTHORIZED,
        EffectReceipt,
        close_effect_receipt_scope,
        effect_outcomes,
        open_effect_receipt_scope,
    )

    ledger = open_effect_receipt_scope({"session_id": "s-unknown", "workspace_root": "/p"})
    try:
        effect = ledger.open_effect(
            EffectReceipt(
                effect_class="provider_call",
                decision=DECISION_ALLOWED,
                lifecycle=LIFECYCLE_AUTHORIZED,
                reason="call that never reported back",
            )
        )
        effect.begin_attempt()  # transport began; nothing after this is known
        outcome = next(
            o for o in effect_outcomes() if o["effect_id"] == effect.effect_id
        )
        assert outcome["unresolved"] is True, "the honest state is started/unresolved"
        assert outcome["lifecycle"] == "started"
    finally:
        close_effect_receipt_scope()
    rows = eb.reservation_rows()
    assert rows[0]["state"] == eb.RESERVATION_CONSUMED, "unknown is not refunded"
    assert eb.budget_status("provider_call", session_id="s-unknown")[0].used == 1


def test_actual_cost_overage_cannot_exceed_the_ceiling_silently(
    provider_home, set_budget,
):
    """The spend-truth reconciliation: the call estimated 1 unit; the
    measured cost was 3. The delta is charged, the overage crosses the
    operator ceiling and is flagged `over_limit` DURABLY, and the next
    reserve is refused — the ceiling was exceeded by a call that already
    happened (truth), but never silently."""
    set_budget(("provider_call", eb.SCOPE_PROJECT, 2))
    permit = _seal(provider_home)
    permit.consume()
    record = permit.settle_budget_cost(actual_units=3)
    assert record["delta"] == 2 and record["over_limit"] is True
    overage_events = eb.budget_events("cost_reconciled")
    assert len(overage_events) == 1
    assert overage_events[0]["code"] == eb.REFUSAL_BUDGET_EXCEEDED
    status = eb.budget_status("provider_call", project_key="default")[0]
    assert status.used == 3 and status.remaining == 0, "true spend is on the books"
    from core.effect_budget import EffectBudgetRefusedError

    with pytest.raises(EffectBudgetRefusedError):
        _seal(provider_home)  # the ceiling now binds future calls
    # a downward reconciliation records truth and never auto-refunds
    permit2 = None
    eb.apply_operator_adjustment(
        __import__("core.effect_budget", fromlist=["x"]).grant_operator_budget_authority(
            "widen for the downward leg"
        ),
        [eb.BudgetAdjustment("provider_call", eb.SCOPE_PROJECT, 10)],
    )
    permit2 = _seal(provider_home)
    permit2.consume()
    down = permit2.settle_budget_cost(actual_units=0)  # a call that cost nothing
    assert down["delta"] == -1 and down["over_limit"] is False
    status = eb.budget_status("provider_call", project_key="default")[0]
    assert status.used == 4, "measured-but-unspent units are NOT auto-returned"


def test_sabotage_dropped_door_guard_lets_the_call_through(provider_home, set_budget, monkeypatch):
    """RED-PROOF: with the seal's budget reserve sabotaged away, an
    exhausted budget still mints permits — exactly the hole the guard
    prevents; unsabotaged, the same seal refuses."""
    import core.provider_invocation_gateway as gateway

    set_budget(("provider_call", eb.SCOPE_PROJECT, 1))
    first = _seal(provider_home)
    first.consume()
    from core.effect_budget import EffectBudgetRefusedError

    with pytest.raises(EffectBudgetRefusedError):
        _seal(provider_home)  # the honest guard refuses
    with monkeypatch.context() as sabotage:
        sabotage.setattr(gateway, "_reserve_provider_call_budget", lambda **k: ("", ""))
        smuggled = _seal(provider_home)
        assert smuggled.budget_reservation_id == "", (
            "sabotage failed — the red-proof would be vacuous"
        )
        smuggled.consume()  # the model call would run unbudgeted
        assert len(eb.reservation_rows()) == 1, "only the honest call is on the books"
    with pytest.raises(EffectBudgetRefusedError):
        _seal(provider_home)  # restored
