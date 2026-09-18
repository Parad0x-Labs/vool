"""RESTART CONTINUITY + RECONCILIATION — durable truth across deaths.

Consumed units survive a restart exactly as they ended. Reservations held by
a dead instance are the 'authorized but never executed' rollback case: they
release at reconciliation, with a durable receipt, and a released unit can
never be consumed after the fact.
"""
from __future__ import annotations

import pytest

from core import effect_budget as eb
from tests.effect_budget.conftest import *  # noqa: F401,F403 — fixtures


def test_consumed_units_survive_a_restart(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 3))
    receipt = eb.reserve_effect_units(
        "network_fetch", turn_id="t1", session_id="s-boot", project_key="p"
    )
    eb.consume_reservation(receipt.reservation_id)
    # the process dies and a NEW process boots against the same store
    eb.reset_effect_budget_process_state()
    statuses = eb.budget_status("network_fetch", session_id="s-boot")
    assert statuses[0].used == 1 and statuses[0].remaining == 2
    eb.reserve_effect_units(
        "network_fetch", turn_id="t2", session_id="s-boot", project_key="p"
    )
    eb.reserve_effect_units(
        "network_fetch", turn_id="t3", session_id="s-boot", project_key="p"
    )
    with pytest.raises(eb.EffectBudgetRefusedError):
        eb.reserve_effect_units(
            "network_fetch", turn_id="t4", session_id="s-boot", project_key="p"
        )
    assert eb.budget_status("network_fetch", session_id="s-boot")[0].used == 3


def test_reconcile_releases_reservations_of_a_dead_instance(set_budget):
    """A crash (no clean shutdown) leaves a reserved unit held. The next
    process's first reserve runs reconciliation and the unit returns — the
    rollback for an authorized effect that never executed."""
    set_budget(("command", eb.SCOPE_SESSION, 1))
    held = eb.reserve_effect_units(
        "command", turn_id="t1", session_id="s-dead", project_key="p"
    )
    assert held.reservation_id
    dead_instance = eb.current_budget_instance_id()
    # the process dies WITHOUT shutdown; a fresh process boots
    eb.reset_effect_budget_process_state()
    # simulate the dead instance never being seen again: age its heartbeat
    from storage.db import get_connection

    conn = get_connection()
    conn.execute(
        "UPDATE effect_budget_instances SET last_seen_epoch=? WHERE instance_id=?",
        (eb._utcnow_epoch() - (eb.INSTANCE_STALE_SECONDS + 60.0), dead_instance),
    )
    conn.commit()
    released = eb.reconcile_stale_reservations(force=True)
    assert len(released) == 1 and released[0]["reservation_id"] == held.reservation_id
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_RELEASED
    events = eb.budget_events("reconciled_release")
    assert len(events) == 1, "the rollback has a durable receipt"
    # and the session's unit is available again
    statuses = eb.budget_status("command", session_id="s-dead")
    assert statuses[0].used == 0 and statuses[0].remaining == 1


def test_clean_shutdown_releases_immediately_on_next_reconcile(set_budget):
    set_budget(("command", eb.SCOPE_SESSION, 1))
    eb.reserve_effect_units("command", turn_id="t", session_id="s-clean", project_key="p")
    eb.shutdown_effect_budget_instance()  # the clean path marks the instance closed
    eb.reset_effect_budget_process_state()
    released = eb.reconcile_stale_reservations(force=True)
    assert len(released) == 1
    assert eb.budget_status("command", session_id="s-clean")[0].remaining == 1


def test_reconcile_never_touches_live_instances(set_budget):
    set_budget(("command", eb.SCOPE_SESSION, 5))
    eb.reserve_effect_units("command", turn_id="t", session_id="s-live", project_key="p")
    # a still-live instance (fresh heartbeat) keeps its reservation
    released = eb.reconcile_stale_reservations(force=True)
    assert released == []
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_RESERVED
    assert eb.budget_status("command", session_id="s-live")[0].used == 1


def test_released_unit_cannot_be_consumed_after_the_rollback(set_budget):
    """The fail-closed consumption law: after a rollback (scope close or
    reconciliation), the reservation is RELEASED and consuming it is a typed
    STATE refusal — the effect does not run on a returned unit."""
    set_budget(("network_fetch", eb.SCOPE_SESSION, 2))
    receipt = eb.reserve_effect_units(
        "network_fetch", turn_id="t", session_id="s-roll", project_key="p"
    )
    assert eb.release_reservation(receipt.reservation_id, reason="never executed")
    with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
        eb.consume_reservation(receipt.reservation_id)
    assert refusal.value.code == eb.REFUSAL_STATE
    assert "released" in refusal.value.detail


def test_consume_is_idempotent_per_reservation(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 5))
    receipt = eb.reserve_effect_units(
        "network_fetch", turn_id="t", session_id="s-idem", project_key="p"
    )
    assert eb.consume_reservation(receipt.reservation_id) is True
    assert eb.consume_reservation(receipt.reservation_id) is False  # already consumed
    assert eb.consume_reservation(receipt.reservation_id) is False
    status = eb.budget_status("network_fetch", session_id="s-idem")[0]
    assert status.used == 1, "one reservation is counted exactly once"


def test_reservation_release_and_consumption_each_have_durable_receipts(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 3))
    identity = {"turn_id": "t", "session_id": "s-ev", "project_key": "p"}
    consumed = eb.reserve_effect_units("network_fetch", **identity)
    released = eb.reserve_effect_units("network_fetch", **identity)
    eb.reserve_effect_units("network_fetch", **identity)  # third unit, stays reserved
    refused = None
    try:
        eb.reserve_effect_units("network_fetch", **identity)  # fourth: over limit
    except eb.EffectBudgetRefusedError as exc:
        refused = exc
    assert refused is not None
    eb.consume_reservation(consumed.reservation_id)
    eb.release_reservation(released.reservation_id, reason="test rollback")
    kinds = [event["event_kind"] for event in eb.budget_events()]
    assert kinds.count("reserved") == 3
    assert kinds.count("consumed") == 1
    assert kinds.count("released") == 1
    assert kinds.count("refused") == 1, "the refusal itself is a durable receipt"
    for event in eb.budget_events():
        assert event["reservation_id"] or event["event_kind"] in (
            "adjustment",
            "authority_grant",
            "authority_refused",
            "refused",  # a refusal names an effect that never got a reservation
            "instance_closed",
        )


def test_owner_close_rolls_back_authorized_never_executed(set_budget):
    set_budget(("network_fetch", eb.SCOPE_SESSION, 2))
    identity = {"turn_id": "t", "session_id": "s-owner", "project_key": "p"}
    executed = eb.reserve_effect_units("network_fetch", owner_ref="ledger-1", **identity)
    never_ran = eb.reserve_effect_units("network_fetch", owner_ref="ledger-1", **identity)
    eb.consume_reservation(executed.reservation_id)
    # the owner's scope closes: every reservation it still holds rolls back
    released = eb.release_unconsumed_for_owner(
        "ledger-1", reason="owner scope closed: authorized effect never executed"
    )
    assert released == 1
    rows = {row["reservation_id"]: row["state"] for row in eb.reservation_rows()}
    assert rows[executed.reservation_id] == eb.RESERVATION_CONSUMED
    assert rows[never_ran.reservation_id] == eb.RESERVATION_RELEASED
    status = eb.budget_status("network_fetch", session_id="s-owner")[0]
    assert status.used == 1, "only the executed effect's unit stays spent"


def test_owner_close_is_quiet_on_store_failure(set_budget, monkeypatch):
    """The scope-close path must not break a turn when the store is down: a
    held unit can never widen anything, so the failure is counted, not raised."""
    set_budget(("network_fetch", eb.SCOPE_SESSION, 1))
    eb.reserve_effect_units(
        "network_fetch",
        owner_ref="ledger-q",
        turn_id="t",
        session_id="s-q",
        project_key="p",
    )

    def _broken_connection():
        raise RuntimeError("store down")

    monkeypatch.setattr(eb, "_budget_connection", _broken_connection)
    assert eb.release_unconsumed_for_owner("ledger-q", reason="x", quiet=True) == 0
    assert eb.budget_store_status()["count"] >= 1
    monkeypatch.undo()
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_RESERVED


def test_unavailable_store_fails_closed_when_rules_may_exist(set_budget, monkeypatch):
    """A configured budget whose store cannot be consulted REFUSES — the
    effect is never run unbudgeted on a store error."""
    set_budget(("network_fetch", eb.SCOPE_SESSION, 5))

    def _broken_connection():
        raise RuntimeError("store down")

    monkeypatch.setattr(eb, "_budget_connection", _broken_connection)
    with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
        eb.reserve_effect_units("network_fetch", turn_id="t", session_id="s", project_key="p")
    assert refusal.value.code == eb.REFUSAL_STORE_UNAVAILABLE
    assert "refused rather than run unbudgeted" in refusal.value.detail
