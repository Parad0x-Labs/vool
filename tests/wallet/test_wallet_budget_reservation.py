"""Spend ceilings are enforced by an atomic reservation taken BEFORE signing, so concurrent
approvals of different proposals cannot escape the daily or per-destination ceiling; the
reservation settles after broadcast and releases on failure. The A6 logical-effect reservation
is taken at the same point and resolved from the chain. Conformance to the effect-budget
gateway contract on branch build/effect-budgets-p1-20260902 is pinned as strict xfails that
flip the moment that lane merges.
"""
from __future__ import annotations

import threading

import pytest

from tests.wallet._rig import DESTINATION, OTHER_DESTINATION

pytestmark = [pytest.mark.safety]
PIN = "246810"


def _pocket(custody):
    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _prepared(proposals, lifecycle, wallet_id, amount, dest=DESTINATION, memo=""):
    p = proposals.propose_transaction(wallet_id=wallet_id, destination=dest, amount_minor=amount, asset="SOL", origin="user", memo=memo)
    lifecycle.default_lifecycle().prepare(p.proposal_id)
    return p


def _approve_all(lifecycle, approval, ids):
    outcomes: list[str] = []
    lock = threading.Lock()

    def worker(pid):
        try:
            lifecycle.default_lifecycle().approve_and_execute(pid, approver=approval.PinApprover(PIN))
            result = "ok"
        except Exception as exc:
            result = getattr(exc, "code", type(exc).__name__)
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=worker, args=(pid,)) for pid in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    return outcomes


def test_reservation_is_taken_before_signing_and_settled_after_broadcast(wallet_env, monkeypatch):
    from core.wallet import approval, custody, lifecycle, limits, proposals, signers

    profile = _pocket(custody)
    p = _prepared(proposals, lifecycle, profile.wallet_id, 4_000)
    seen: dict[str, str] = {}
    real_signer_for = signers.signer_for

    def probing_signer_for(*args, **kwargs):
        signer = real_signer_for(*args, **kwargs)
        seen["state_at_sign"] = limits.reservation_state(p.proposal_id)
        seen["effect_open_at_sign"] = str(proposals.get_proposal(p.proposal_id).state)
        return signer

    monkeypatch.setattr(lifecycle.signers, "signer_for", probing_signer_for)
    lifecycle.default_lifecycle().approve_and_execute(p.proposal_id, approver=approval.PinApprover(PIN))
    assert seen["state_at_sign"] == limits.RESERVATION_RESERVED, "the amount must be held before the key is ever touched"
    assert limits.reservation_state(p.proposal_id) == limits.RESERVATION_SETTLED
    assert limits.spent_today(profile.wallet_id, "SOL") == 9_000  # 4_000 principal + the 5_000 reserved Solana fee


def test_reservation_is_released_when_signing_or_broadcast_fails(wallet_env):
    from core.wallet import approval, custody, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    wallet_env["rpc"].send_ok = False
    p = _prepared(proposals, lifecycle, profile.wallet_id, 2_500)
    with pytest.raises(WalletFault):
        lifecycle.default_lifecycle().approve_and_execute(p.proposal_id, approver=approval.PinApprover(PIN))
    assert limits.reservation_state(p.proposal_id) == limits.RESERVATION_RELEASED
    assert limits.spent_today(profile.wallet_id, "SOL") == 0
    watch = custody.create_watch_only_wallet(DESTINATION)
    q = _prepared(proposals, lifecycle, watch.wallet_id, 2_500)
    with pytest.raises(WalletFault) as exc:
        lifecycle.default_lifecycle().approve_and_execute(q.proposal_id, approver=approval.PinApprover(PIN))
    assert exc.value.code == "wallet_signing_unavailable"
    assert limits.reservation_state(q.proposal_id) == limits.RESERVATION_RELEASED


def test_concurrent_distinct_proposals_cannot_escape_the_daily_ceiling(wallet_env):
    from core.wallet import approval, custody, lifecycle, limits, proposals

    profile = _pocket(custody)
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=10_000, daily_minor=30_000, per_destination_daily_minor=30_000))
    ids = [_prepared(proposals, lifecycle, profile.wallet_id, 3_000, memo=f"n{i}").proposal_id for i in range(6)]
    outcomes = _approve_all(lifecycle, approval, ids)
    assert outcomes.count("ok") == 3, outcomes  # each costs 3000 principal + 5000 reserved fee = 8000; 3 x 8000 = 24000 <= 30000, a 4th would be 32000
    assert outcomes.count("wallet_limit_exceeded") == 3
    assert wallet_env["rpc"].send_count() == 3
    assert limits.spent_today(profile.wallet_id, "SOL") == 24_000


def test_concurrent_distinct_proposals_cannot_escape_the_destination_ceiling(wallet_env):
    from core.wallet import approval, custody, lifecycle, limits, proposals

    profile = _pocket(custody)
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=10_000, daily_minor=100_000, per_destination_daily_minor=10_000))  # each payment holds 5000 principal + 5000 fee
    a = [_prepared(proposals, lifecycle, profile.wallet_id, 5_000, DESTINATION, memo=f"a{i}").proposal_id for i in range(4)]
    b = [_prepared(proposals, lifecycle, profile.wallet_id, 5_000, OTHER_DESTINATION, memo=f"b{i}").proposal_id for i in range(4)]
    outcomes = _approve_all(lifecycle, approval, a + b)
    assert outcomes.count("ok") == 2, outcomes  # one per destination
    assert wallet_env["rpc"].send_count() == 2


def test_a6_logical_effect_is_reserved_before_signing_and_resolved_from_the_chain(wallet_env):
    from core.effect_reconciliation import ResolutionOutcome, reconcile_unresolved_effect
    from core.runtime_continuity import compute_logical_effect_id, find_active_unresolved_effect
    from core.wallet import approval, custody, lifecycle, proposals, reconciliation

    profile = _pocket(custody)
    p = _prepared(proposals, lifecycle, profile.wallet_id, 700)
    leid = compute_logical_effect_id(intent=reconciliation.EFFECT_INTENT, arguments={"proposal_id": p.proposal_id})
    assert find_active_unresolved_effect(leid) is None
    receipt = lifecycle.default_lifecycle().approve_and_execute(p.proposal_id, approver=approval.PinApprover(PIN))
    active = find_active_unresolved_effect(leid)
    assert active is None, "a confirmed broadcast resolves its reservation"
    # the registered resolver answers from the proposal row + chain, never from the model
    resolution = reconcile_unresolved_effect({"logical_effect_id": leid, "tool_name": reconciliation.EFFECT_INTENT, "expected_evidence": {"proposal_id": p.proposal_id}})
    assert resolution.outcome is ResolutionOutcome.APPLIED and resolution.evidence == receipt.tx_signature and resolution.source == "provider"


def test_a6_reservation_blocks_a_second_in_flight_instance(wallet_env):
    from core.runtime_continuity import reserve_logical_effect
    from core.wallet import approval, custody, lifecycle, proposals, reconciliation
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    p = _prepared(proposals, lifecycle, profile.wallet_id, 700)
    # something else is holding this logical effect (a crashed earlier attempt, say)
    reserve_logical_effect(intent=reconciliation.EFFECT_INTENT, arguments={"proposal_id": p.proposal_id}, resource_identity=p.proposal_id)
    with pytest.raises(WalletFault) as exc:
        lifecycle.default_lifecycle().approve_and_execute(p.proposal_id, approver=approval.PinApprover(PIN))
    assert exc.value.code == "wallet_duplicate_payment" and exc.value.context["reason"] == "effect_in_flight"
    assert wallet_env["rpc"].send_count() == 0


# --- effect-budget gateway contract: the sibling lane's typed contract, bound through the gateway ------------
# The authority (core/effect_budget.py) lives on build/effect-budgets-p1-20260902; its contract names the two
# wallet operations as effect classes `wallet_sign` and `wallet_transaction` and maps them through the gateway's
# open_effect seam. The wallet opens exactly those effects, in the contract's order, on THIS tree already; the
# budget-bound proofs below execute only once the module is present, and pass then without further wiring.

EFFECT_BUDGET_DEPENDENCY = "convergence dependency: build/effect-budgets-p1-20260902 tip a94a4a48 (implementation 4190b717), base a7b78e2a — core/effect_budget.py is not in this tree"
CONTRACT_SIGN = "wallet_sign"
CONTRACT_TRANSACTION = "wallet_transaction"


def _scoped(ctx):
    from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope

    open_effect_receipt_scope(ctx)
    return close_effect_receipt_scope


def test_lifecycle_opens_the_contract_effect_classes_in_the_contract_order(wallet_env):
    """reserve (open) BEFORE authorization of the operation; consume (begin_attempt) immediately BEFORE
    execution; terminal exactly once — for both wallet_sign and wallet_transaction, on this tree."""
    from core.effect_gateway import effect_receipts
    from core.wallet import approval, custody, lifecycle, proposals

    profile = _pocket(custody)
    p = _prepared(proposals, lifecycle, profile.wallet_id, 300)
    ctx = {"session_id": "s-contract", "turn_id": "t-contract", "request_id": "r-contract"}
    close = _scoped(ctx)
    try:
        lifecycle.default_lifecycle(source_context=ctx).approve_and_execute(p.proposal_id, approver=approval.PinApprover(PIN))
        rows = [r for r in effect_receipts() if r.get("effect_class") in {CONTRACT_SIGN, CONTRACT_TRANSACTION}]
    finally:
        close()
    order = [(r["effect_class"], r["lifecycle"]) for r in rows]
    assert (CONTRACT_SIGN, "authorized") in order and (CONTRACT_TRANSACTION, "authorized") in order
    assert order.index((CONTRACT_SIGN, "authorized")) < order.index((CONTRACT_SIGN, "started")) < order.index((CONTRACT_SIGN, "succeeded"))
    assert order.index((CONTRACT_TRANSACTION, "authorized")) < order.index((CONTRACT_SIGN, "started")), "both reserved before the first execution"
    assert order.index((CONTRACT_SIGN, "succeeded")) < order.index((CONTRACT_TRANSACTION, "started")) < order.index((CONTRACT_TRANSACTION, "succeeded"))
    assert sum(1 for c, l in order if c == CONTRACT_TRANSACTION and l in {"succeeded", "failed", "cancelled"}) == 1
    assert "payment" not in {r.get("effect_class") for r in effect_receipts()}, "the contract classes replace the P1 'payment' class"


def test_a_failed_broadcast_terminates_the_transaction_effect_once_and_releases_the_hold(wallet_env):
    from core.effect_gateway import effect_receipts
    from core.wallet import approval, custody, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    wallet_env["rpc"].send_ok = False
    p = _prepared(proposals, lifecycle, profile.wallet_id, 300)
    ctx = {"session_id": "s-fail", "turn_id": "t-fail", "request_id": "r-fail"}
    close = _scoped(ctx)
    try:
        with pytest.raises(WalletFault):
            lifecycle.default_lifecycle(source_context=ctx).approve_and_execute(p.proposal_id, approver=approval.PinApprover(PIN))
        rows = [(r["effect_class"], r["lifecycle"]) for r in effect_receipts()]
    finally:
        close()
    assert (CONTRACT_SIGN, "succeeded") in rows and (CONTRACT_TRANSACTION, "failed") in rows
    assert limits.reservation_state(p.proposal_id) == limits.RESERVATION_RELEASED


def test_effect_budget_binds_the_wallet_transaction_class_through_the_gateway(wallet_env):
    eb = pytest.importorskip("core.effect_budget", reason=EFFECT_BUDGET_DEPENDENCY)
    from core.wallet import approval, custody, lifecycle, proposals
    from core.wallet.errors import WalletFault

    assert eb.gateway_budget_class(CONTRACT_TRANSACTION) == CONTRACT_TRANSACTION and eb.gateway_budget_class(CONTRACT_SIGN) == CONTRACT_SIGN
    contract = eb.wallet_effect_budget_contract()
    assert set(contract.budget_classes) == {CONTRACT_TRANSACTION, CONTRACT_SIGN}
    eb.reset_effect_budget_process_state()
    token = eb.grant_operator_budget_authority("wallet budget proof")  # outside any effect scope, as the contract demands
    eb.apply_operator_adjustment(token, [eb.BudgetAdjustment(budget_class=CONTRACT_TRANSACTION, scope=eb.SCOPE_SESSION, new_limit=1, window_seconds=0.0, note="one transaction per session")])
    profile = _pocket(custody)
    first = _prepared(proposals, lifecycle, profile.wallet_id, 100, memo="one")
    second = _prepared(proposals, lifecycle, profile.wallet_id, 100, memo="two")
    ctx = {"session_id": "s-budget", "turn_id": "t-budget", "request_id": "r-budget", "workspace_root": "/wp"}
    close = _scoped(ctx)
    try:
        receipt = lifecycle.default_lifecycle(source_context=ctx).approve_and_execute(first.proposal_id, approver=approval.PinApprover(PIN))
        assert receipt.state == proposals.STATE_CONFIRMED
        with pytest.raises(WalletFault) as exc:
            lifecycle.default_lifecycle(source_context=ctx).approve_and_execute(second.proposal_id, approver=approval.PinApprover(PIN))
    finally:
        close()
    assert exc.value.code == "wallet_limit_exceeded" and "EffectBudgetRefusedError" in exc.value.context["reason"]
    assert wallet_env["rpc"].send_count() == 1
    assert proposals.get_proposal(second.proposal_id).state == proposals.STATE_REJECTED
    kinds = [event["event_kind"] for event in eb.budget_events()]
    assert kinds.count("refused") >= 1 and kinds.count("consumed") >= 1
