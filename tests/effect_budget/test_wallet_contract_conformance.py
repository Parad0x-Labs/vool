"""THE WALLET INTEGRATION CONTRACT — typed, executable conformance.

The wallet adapter is being built CONCURRENTLY in another lane and is NOT
duplicated here. What this file is: the one typed contract
(`core.effect_budget.wallet_effect_budget_contract`) plus the conformance
suite the wallet lane MUST keep green as it wires its doors. End-to-end
wallet budget enforcement is DEPENDENCY-BLOCKED until that convergence —
these tests execute against the authority itself, so the lane that consumes
the contract inherits exactly these laws:

    reserve BEFORE authorization; consume immediately BEFORE execution;
    reconcile the terminal exactly once; UNKNOWN outcomes keep their units;
    every step is a durable receipt.
"""
from __future__ import annotations

import pytest

from core import effect_budget as eb
from core.effect_budget import wallet_effect_budget_contract
from tests.effect_budget.conftest import *  # noqa: F401,F403 — fixtures


def test_the_contract_is_typed_and_names_both_operations():
    contract = wallet_effect_budget_contract()
    assert contract.contract_id == "effect-budget-contract/v1"
    assert contract.budget_classes == ("wallet_transaction", "wallet_sign")
    assert set(contract.sequence) == {
        "reserve_before_authorization",
        "consume_immediately_before_execution",
        "reconcile_terminal_exactly_once",
        "unknown_outcomes_keep_their_units",
        "every_step_is_a_durable_receipt",
    }
    assert eb.REFUSAL_BUDGET_EXCEEDED in contract.refusal_codes
    assert eb.REFUSAL_STORE_UNAVAILABLE in contract.refusal_codes


def test_the_contract_api_exists_and_is_the_one_authority():
    contract = wallet_effect_budget_contract()
    for name in contract.api:
        assert callable(getattr(eb, name, None)), f"contract api {name} is missing"
    # no lane-specific counters: the contract's api IS this module's public
    # surface — there is no second wallet budget authority to call
    assert not any("wallet" in name for name in contract.api)


@pytest.mark.parametrize("budget_class", ["wallet_transaction", "wallet_sign"])
def test_conformance_full_law_sequence_per_operation(set_budget, budget_class):
    contract = wallet_effect_budget_contract()
    assert budget_class in contract.budget_classes
    set_budget((budget_class, eb.SCOPE_SESSION, 2))
    identity = {"turn_id": "wt-1", "session_id": "ws-1", "project_key": "wp"}

    # 1 — reserve BEFORE authorization
    first = eb.reserve_effect_units(budget_class, owner_ref="wallet.lane", **identity)
    assert first.rules and first.unbudgeted is False
    # 2 — consume immediately BEFORE execution
    assert eb.consume_reservation(first.reservation_id) is True
    # 3 — exhaustion refuses with the typed code and prevents the operation
    second = eb.reserve_effect_units(budget_class, owner_ref="wallet.lane", **identity)
    with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
        eb.reserve_effect_units(budget_class, owner_ref="wallet.lane", **identity)
    assert refusal.value.code == eb.REFUSAL_BUDGET_EXCEEDED
    assert refusal.value.rule == f"{budget_class}/session"
    # 5 — every step left its durable receipt
    kinds = [event["event_kind"] for event in eb.budget_events()]
    assert kinds.count("reserved") == 2
    assert kinds.count("consumed") == 1
    assert kinds.count("refused") == 1


@pytest.mark.parametrize("budget_class", ["wallet_transaction", "wallet_sign"])
def test_conformance_unknown_outcome_keeps_the_unit(set_budget, budget_class):
    """A wallet operation whose external outcome is ambiguous (signed but
    never broadcast, broadcast but never confirmed): the unit stays consumed
    — an ambiguous outcome is never blindly refunded."""
    set_budget((budget_class, eb.SCOPE_SESSION, 1))
    receipt = eb.reserve_effect_units(
        budget_class, owner_ref="wallet.lane", turn_id="t", session_id="s", project_key="p"
    )
    eb.consume_reservation(receipt.reservation_id)
    # nothing further is known; the reservation stays consumed
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_CONSUMED
    assert eb.budget_status(budget_class, session_id="s")[0].remaining == 0


@pytest.mark.parametrize("budget_class", ["wallet_transaction", "wallet_sign"])
def test_conformance_cancelled_before_execution_releases(set_budget, budget_class):
    """An operation authorized then cancelled BEFORE execution: the unit
    returns (scope-close rollback, the same law every door runs)."""
    set_budget((budget_class, eb.SCOPE_SESSION, 1))
    receipt = eb.reserve_effect_units(
        budget_class, owner_ref="wallet.lane", turn_id="t", session_id="s", project_key="p"
    )
    # the operation was cancelled before any execution: the lane releases
    assert eb.release_reservation(receipt.reservation_id, reason="cancelled before execution")
    assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_RELEASED
    assert eb.budget_status(budget_class, session_id="s")[0].remaining == 1
    # and the returned unit cannot be consumed after the rollback
    with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
        eb.consume_reservation(receipt.reservation_id)
    assert refusal.value.code == eb.REFUSAL_STATE


@pytest.mark.parametrize("budget_class", ["wallet_transaction", "wallet_sign"])
def test_conformance_retries_never_double_reserve_or_consume(set_budget, budget_class):
    """A retried wallet operation continues the SAME logical effect: one
    unit, consumed once, however many attempts."""
    set_budget((budget_class, eb.SCOPE_SESSION, 1))
    from core.effect_gateway import (
        DECISION_ALLOWED,
        LIFECYCLE_AUTHORIZED,
        EffectReceipt,
        close_effect_receipt_scope,
        open_effect_receipt_scope,
    )

    ledger = open_effect_receipt_scope({"session_id": "ws-retry", "workspace_root": "/wp"})
    try:
        first = ledger.open_effect(
            EffectReceipt(
                effect_class=budget_class,
                decision=DECISION_ALLOWED,
                lifecycle=LIFECYCLE_AUTHORIZED,
                reason="attempt 1",
            )
        )
        first.begin_attempt()
        first.fail(reason="provider timeout")
        retry = ledger.open_effect(
            EffectReceipt(
                effect_class=budget_class,
                decision=DECISION_ALLOWED,
                lifecycle=LIFECYCLE_AUTHORIZED,
                reason="retry",
            ),
            retry_of=first.effect_id,
        )
        retry.begin_attempt()
        retry.succeed()
        assert retry.effect_id == first.effect_id
        assert len(eb.reservation_rows()) == 1
        assert eb.reservation_rows()[0]["state"] == eb.RESERVATION_CONSUMED
        assert eb.budget_status(budget_class, session_id="ws-retry")[0].used == 1
    finally:
        close_effect_receipt_scope()


@pytest.mark.parametrize("budget_class", ["wallet_transaction", "wallet_sign"])
def test_conformance_classes_bind_the_moment_the_lane_uses_the_gate(budget_class):
    """The gateway effect-class mapping is wired NOW: the moment the wallet
    lane opens its effects through `EffectLedger.open_effect`, budgets bind
    with zero further vocabulary on either side."""
    assert eb.gateway_budget_class(budget_class) == budget_class


def test_wallet_authority_is_not_mintable_by_participants(set_budget):
    """The contract inherits the authority law: no token inside an effect
    scope, whatever class the lane budgets."""
    from core.effect_gateway import (
        close_effect_receipt_scope,
        open_effect_receipt_scope,
    )

    open_effect_receipt_scope({"session_id": "ws-auth", "workspace_root": "/wp"})
    try:
        with pytest.raises(eb.EffectBudgetRefusedError) as refusal:
            eb.grant_operator_budget_authority("wallet lane self-raise")
        assert refusal.value.code == eb.REFUSAL_AUTHORITY
    finally:
        close_effect_receipt_scope()
