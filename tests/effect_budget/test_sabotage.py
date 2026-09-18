"""SABOTAGE PROOFS — break the guard on purpose; the invariant must break
loudly exactly where the guard was, proving the green tests above test the
guard and not the accident.

Family S1: atomic reservation — sabotage the check-and-write atomicity
(usage reads lie / split transaction) and show the final-unit invariant
becomes violable, i.e. the passing race tests genuinely depend on the
atomic core.

Family S2: authority — sabotage the authority checks (drop the scope check,
drop the grant-record check) and show 'participants cannot raise budgets'
becomes violable.
"""
from __future__ import annotations

import threading

import pytest

from core import effect_budget as eb
from core.effect_gateway import (
    close_effect_receipt_scope,
    open_effect_receipt_scope,
)
from tests.effect_budget.conftest import *  # noqa: F401,F403 — fixtures


def _race(budget_class: str, identity: dict, count: int):
    successes: list[str] = []
    barrier = threading.Barrier(count)
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        try:
            receipt = eb.reserve_effect_units(budget_class, **identity)
            with lock:
                successes.append(receipt.reservation_id)
        except eb.EffectBudgetRefusedError:
            pass

    threads = [threading.Thread(target=worker) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return successes


# -- S1: atomic reservation sabotage -----------------------------------------


def test_s1_sabotaged_usage_check_lets_the_last_unit_double_spend(set_budget, monkeypatch):
    """RED-PROOF. With the usage check lying (always reporting zero used —
    the split-transaction hazard), the race overspends: successes exceed the
    limit. This is the failure mode the atomic core exists to prevent, and
    this test PROVES the unsabotaged race tests would catch it."""
    set_budget(("network_fetch", eb.SCOPE_SESSION, 1))
    identity = {"turn_id": "", "session_id": "s-sab", "project_key": "p"}

    # sanity: the real guard holds under the same race
    assert len(_race("network_fetch", identity, 4)) == 1

    monkeypatch.setattr(eb, "_rule_usage", lambda *args, **kwargs: 0)
    successes = _race("network_fetch", identity, 4)
    assert len(successes) > 1, (
        "sabotage failed to break anything — the atomicity proof would be vacuous"
    )
    # the status API shares the sabotaged check, so read the STORE itself:
    # more reserved rows than the limit is the overspend, plain and durable
    from storage.db import get_connection

    conn = get_connection()
    reserved_count = conn.execute(
        "SELECT COUNT(*) FROM effect_budget_reservations WHERE state='reserved'"
    ).fetchone()[0]
    assert reserved_count > 1, "the store itself shows the overspend"
    counter = conn.execute(
        "SELECT reserved_units FROM effect_budget_counters "
        "WHERE budget_class='network_fetch' AND scope='session'"
    ).fetchone()[0]
    assert counter > 1, "the durable counter shows the overspend"
    # and with the sabotage removed, the same race is safe again
    monkeypatch.undo()
    assert len(eb.reservation_rows(state=eb.RESERVATION_RESERVED)) == len(successes) + 1, (
        "the sanity race's one legitimate reservation + the sabotage's overspend"
    )
    with pytest.raises(eb.EffectBudgetRefusedError):
        eb.reserve_effect_units("network_fetch", **identity)


def test_s1_sabotaged_transaction_never_persists_a_partial_state(set_budget, monkeypatch):
    """Even sabotaged mid-write (the counter update raises after the
    reservation insert), the transaction rolls back WHOLE: no reservation
    row, no counter movement, a typed store refusal."""
    set_budget(("command", eb.SCOPE_SESSION, 5))
    identity = {"turn_id": "", "session_id": "s-txn", "project_key": "p"}
    real_reserve = eb.reserve_effect_units

    def _exploding_reserve(*args, **kwargs):
        real_conn = eb._budget_connection
        from storage.db import get_connection

        class _Boom:
            def __init__(self, inner):
                self._inner = inner

            def execute(self, sql, *a, **k):
                if "INSERT INTO effect_budget_reservations" in sql:
                    raise RuntimeError("sabotage: crash after check, before counter write")
                return self._inner.execute(sql, *a, **k)

            def __getattr__(self, name):
                return getattr(self._inner, name)

        monkeypatch.setattr(eb, "_budget_connection", lambda: _Boom(get_connection()))
        try:
            return real_reserve(*args, **kwargs)
        finally:
            monkeypatch.setattr(eb, "_budget_connection", real_conn)

    with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
        _exploding_reserve("command", **identity)
    assert refusal.value.code == eb.REFUSAL_STORE_UNAVAILABLE
    assert eb.reservation_rows() == [], "no partial reservation survived"
    assert eb.budget_status("command", session_id="s-txn")[0].used == 0


# -- S2: authority sabotage ----------------------------------------------------


def test_s2_sabotaged_scope_check_lets_a_participant_raise(operator_token, monkeypatch):
    """RED-PROOF. With the active-scope check removed from the mint, a
    participant inside a turn CAN mint — demonstrating exactly what the real
    check prevents (and that the green test depends on it)."""
    open_effect_receipt_scope()
    try:
        with pytest.raises(eb.EffectBudgetRefusedError):
            eb.grant_operator_budget_authority("honest path refuses")
        real_ledger_get = None
        import core.effect_gateway as gateway

        real_ledger_get = gateway.current_effect_ledger
        monkeypatch.setattr(gateway, "current_effect_ledger", lambda: None)
        try:
            token = eb.grant_operator_budget_authority("sabotaged mint inside turn")
            assert token.token_id, "sabotage failed — the red-proof would be vacuous"
        finally:
            monkeypatch.setattr(gateway, "current_effect_ledger", real_ledger_get)
        # the sabotage proven, the honest law restored and re-verified
        monkeypatch.undo()
        with pytest.raises(eb.EffectBudgetRefusedError):
            eb.grant_operator_budget_authority("honest path refuses again")
    finally:
        close_effect_receipt_scope()


def test_s2_forged_token_cannot_adjust_even_with_valid_shape(operator_token):
    """A token fabricated to look real (valid prefix, valid dataclass) has no
    durable grant — the grant-record check is the forgery wall."""
    forged = eb.OperatorBudgetToken(token_id="obt:" + "f" * 16, note="looks real")
    with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
        eb.apply_operator_adjustment(
            forged, [eb.BudgetAdjustment(eb.BUDGET_CLASS_COMMAND, eb.SCOPE_SESSION, 999)]
        )
    assert refusal.value.code == eb.REFUSAL_AUTHORITY
    assert eb.active_budgets() == []


def test_s2_sabotaged_grant_check_lets_the_forge_through(operator_token, monkeypatch):
    """RED-PROOF. With the grant-record check sabotaged away, the forged
    token adjusts — exactly the hole `_token_was_granted` exists to close."""
    forged = eb.OperatorBudgetToken(token_id="obt:" + "f" * 16, note="forged")
    with pytest.raises(eb.EffectBudgetRefusedError):
        eb.apply_operator_adjustment(
            forged, [eb.BudgetAdjustment(eb.BUDGET_CLASS_COMMAND, eb.SCOPE_SESSION, 999)]
        )
    monkeypatch.setattr(eb, "_token_was_granted", lambda token: True)
    try:
        applied = eb.apply_operator_adjustment(
            forged, [eb.BudgetAdjustment(eb.BUDGET_CLASS_COMMAND, eb.SCOPE_SESSION, 999)]
        )
        assert applied and applied[0].limit == 999, (
            "sabotage failed — the forgery-proof would be vacuous"
        )
    finally:
        monkeypatch.undo()
    # the check restored: a NEW forged adjustment is refused again. The row
    # the sabotage wrote is durable residue of the demonstrated breach — the
    # honest operator removes it with a REAL token.
    with pytest.raises(eb.EffectBudgetRefusedError):
        eb.apply_operator_adjustment(
            forged, [eb.BudgetAdjustment(eb.BUDGET_CLASS_COMMAND, eb.SCOPE_SESSION, 5)]
        )
    eb.apply_operator_adjustment(
        operator_token,
        [eb.BudgetAdjustment(eb.BUDGET_CLASS_COMMAND, eb.SCOPE_SESSION, None)],
    )
    assert eb.active_budgets() == []


# -- S3: the consumption gate sabotage ---------------------------------------


def test_s3_sabotaged_consumption_check_runs_unbudgeted(set_budget):
    """RED-PROOF at the gate: if consumption's released-reservation check is
    bypassed, a rolled-back effect would spend its returned unit — the
    unsabotaged test refuses exactly this."""
    from core.effect_gateway import DECISION_ALLOWED, EffectReceipt

    set_budget(("network_fetch", eb.SCOPE_SESSION, 1))
    ledger = open_effect_receipt_scope()
    try:
        lifecycle = ledger.open_effect(
            EffectReceipt(
                effect_class="network_fetch",
                decision=DECISION_ALLOWED,
                lifecycle="authorized",
                reason="test",
            )
        )
        row = eb.reservation_rows()[0]
        eb.release_reservation(row["reservation_id"], reason="rollback before execution")
        with pytest.raises(eb.EffectBudgetRefusedError) as honest:
            lifecycle.begin_attempt()
        assert honest.value.code == eb.REFUSAL_STATE
        # sabotage: the consumption gate ignores the released state
        real_consume_effect = eb.consume_effect_reservations

        def _loose_consume(effect_id, *, budget_class=""):
            return 0

        eb.consume_effect_reservations = _loose_consume
        try:
            lifecycle.begin_attempt()  # the sabotaged path lets the attempt run
            rows = eb.reservation_rows()
            assert rows[0]["state"] == eb.RESERVATION_RELEASED, "no unit was spent"
            assert lifecycle.outcome()["transport_ran"] is True, (
                "the attempt RAN on a returned unit — the exact hazard the "
                "released-reservation check prevents"
            )
        finally:
            eb.consume_effect_reservations = real_consume_effect
    finally:
        close_effect_receipt_scope()
