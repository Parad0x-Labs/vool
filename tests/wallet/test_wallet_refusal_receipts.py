"""Every refusal the payment lifecycle makes leaves a durable receipt, with nothing charged.

A failed simulation, an endpoint that proves another chain, a ceiling (at prepare, or at the claim when the limits
changed in between), a claim refused because the payment's effect is already in flight or a session budget refused it,
each refused approval, a signing request that expired and the owner's own rejection are decisions money control made about one payment. Each records a receipt under its real
fault code, in the state the payment is left in, with a zero charged amount and fee, in the same transaction as that
state: a payment never ends without its receipt.
"""
from __future__ import annotations

import pytest

from tests.wallet._rig import DESTINATION

pytestmark = [pytest.mark.safety]
PIN = "246810"


def _pocket():
    from core.wallet import custody

    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _propose(profile, amount_minor=10_000):
    from core.wallet import proposals

    return proposals.propose_transaction(wallet_id=profile.wallet_id, destination=DESTINATION, amount_minor=amount_minor, asset="SOL", origin=proposals.ORIGIN_USER)


def _receipts(proposal_id):
    from core.wallet import receipts

    return list(reversed([r for r in receipts.list_receipts() if r["proposal_id"] == proposal_id]))


def _assert_refusal_receipt(proposal_id, *, state, fault_code, hold="", **detail):
    from core.wallet import limits, proposals

    assert proposals.get_proposal(proposal_id).state == state
    [receipt] = _receipts(proposal_id)
    assert (receipt["state"], receipt["fault_code"], receipt["tx_signature"]) == (state, fault_code, "")
    refusal = receipt["refusal"]
    assert (refusal["charged_amount_minor"], refusal["charged_fee_minor"]) == (0, 0)
    assert {k: refusal[k] for k in detail} == detail
    assert limits.reservation_state(proposal_id) == hold, "nothing was held, or the hold was released"


def test_prepare_limit_failure_has_truthful_zero_charge_receipt(wallet_env):
    from core.wallet import lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=5_000, daily_minor=1_000_000, per_destination_daily_minor=1_000_000))
    proposal = _propose(profile)
    with pytest.raises(WalletFault) as exc:
        lifecycle.default_lifecycle().prepare(proposal.proposal_id)
    assert exc.value.code == "wallet_limit_exceeded"
    _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_limit_exceeded", limit="per_transaction")
    assert wallet_env["rpc"].send_count() == 0


def test_limit_refusal_has_a_durable_wallet_receipt(wallet_env):
    """The owner lowers the daily ceiling while the payment waits for approval: the claim's reservation refuses it."""
    from core.wallet import approval, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    proposal = _propose(profile)
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=1_000_000, daily_minor=5_000, per_destination_daily_minor=1_000_000))
    with pytest.raises(WalletFault) as exc:
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    assert exc.value.code == "wallet_limit_exceeded"
    _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_limit_exceeded", limit="daily")
    assert wallet_env["rpc"].send_count() == 0


@pytest.mark.parametrize("failure", ["simulation_refused", "endpoint_on_another_chain"])
def test_prepare_simulation_failure_has_truthful_zero_charge_receipt(wallet_env, failure):
    from core.wallet import lifecycle, proposals
    from core.wallet.errors import WalletFault

    if failure == "simulation_refused":
        wallet_env["rpc"].simulate_ok = False
    else:
        wallet_env["rpc"].genesis_hash = "4uhcVJyU9pJkvQyS88uRDiswHXSCkY3zQawwpjk2NsNY"  # Solana Testnet's, not Devnet's
    proposal = _propose(_pocket())
    with pytest.raises(WalletFault) as exc:
        lifecycle.default_lifecycle().prepare(proposal.proposal_id)
    expected = "wallet_simulation_failed" if failure == "simulation_refused" else "wallet_chain_identity_mismatch"
    assert exc.value.code == expected
    _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_FAILED, fault_code=expected)
    assert wallet_env["rpc"].send_count() == 0


@pytest.mark.parametrize("refusals", [1, 5])
def test_refused_pin_approvals_have_durable_receipts(wallet_env, refusals):
    """A wrong PIN is a counted refusal, not the end of the payment: it stays pending, and the refusal leaves a receipt
    in that state. The refusal that reaches the lock rejects the payment, with its receipt in the same transaction."""
    from core.wallet import approval, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    proposal = _propose(_pocket())
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)
    for _ in range(refusals):
        with pytest.raises(WalletFault) as exc:
            engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover("999999"))
        assert exc.value.code == "wallet_approval_rejected"
    locked = refusals >= lifecycle.MAX_APPROVAL_ATTEMPTS
    seen = [(r["state"], r["fault_code"], r["refusal"]["reason"], r["refusal"]["attempts"], r["refusal"]["charged_amount_minor"], r["refusal"]["charged_fee_minor"])
            for r in _receipts(proposal.proposal_id)]
    expected = [(proposals.STATE_PENDING_APPROVAL, "wallet_approval_rejected", "challenge_mismatch_or_wrong_pin", attempt, 0, 0) for attempt in range(1, refusals + 1)]
    if locked:
        expected[-1] = (proposals.STATE_REJECTED, "wallet_approval_rejected", "approval_attempts_exhausted", refusals, 0, 0)
    assert seen == expected
    assert proposals.get_proposal(proposal.proposal_id).state == (proposals.STATE_REJECTED if locked else proposals.STATE_PENDING_APPROVAL)
    assert limits.reservation_state(proposal.proposal_id) == "" and wallet_env["rpc"].send_count() == 0


def test_a_payment_whose_effect_is_already_in_flight_leaves_a_receipt(wallet_env):
    from core.runtime_continuity import reserve_logical_effect
    from core.wallet import approval, lifecycle, limits, proposals, reconciliation
    from core.wallet.errors import WalletFault

    proposal = _propose(_pocket())
    engine = lifecycle.default_lifecycle()
    engine.prepare(proposal.proposal_id)
    # something else holds this payment's logical effect (a crashed earlier attempt, say): the claim refuses it
    reserve_logical_effect(intent=reconciliation.EFFECT_INTENT, arguments={"proposal_id": proposal.proposal_id}, resource_identity=proposal.proposal_id)
    with pytest.raises(WalletFault) as exc:
        engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
    assert exc.value.code == "wallet_duplicate_payment"
    _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_duplicate_payment", hold=limits.RESERVATION_RELEASED, reason="effect_in_flight")
    assert wallet_env["rpc"].send_count() == 0


def test_a_session_budget_that_refuses_the_payment_leaves_a_receipt(wallet_env):
    eb = pytest.importorskip("core.effect_budget")
    from core.effect_gateway import close_effect_receipt_scope, open_effect_receipt_scope
    from core.wallet import approval, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    eb.reset_effect_budget_process_state()
    token = eb.grant_operator_budget_authority("one transaction per session")  # outside any effect scope
    eb.apply_operator_adjustment(token, [eb.BudgetAdjustment(budget_class="wallet_transaction", scope=eb.SCOPE_SESSION, new_limit=1, window_seconds=0.0, note="one")])
    profile = _pocket()
    first, second = _propose(profile, 100), _propose(profile, 200)
    ctx = {"session_id": "s-receipt", "turn_id": "t-receipt", "request_id": "r-receipt", "workspace_root": "/wp"}
    engine = lifecycle.default_lifecycle(source_context=ctx)
    engine.prepare(first.proposal_id)
    engine.prepare(second.proposal_id)
    open_effect_receipt_scope(ctx)
    try:
        assert engine.approve_and_execute(first.proposal_id, approver=approval.PinApprover(PIN)).state == proposals.STATE_CONFIRMED
        with pytest.raises(WalletFault) as exc:
            engine.approve_and_execute(second.proposal_id, approver=approval.PinApprover(PIN))
    finally:
        close_effect_receipt_scope()
    assert exc.value.code == "wallet_limit_exceeded" and exc.value.context["reason"].startswith("effect_gateway_refused")
    _assert_refusal_receipt(second.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_limit_exceeded", hold=limits.RESERVATION_RELEASED, reason="effect_gateway_refused")
    assert wallet_env["rpc"].send_count() == 1


def test_a_refusal_whose_receipt_cannot_be_written_does_not_end_the_payment(wallet_env, monkeypatch):
    """The refusal and its receipt are one transaction: when the receipt cannot be written, the payment is not left
    rejected without one. It stays where it was, and the failure is raised."""
    from core.wallet import lifecycle, limits, proposals, receipts

    profile = _pocket()
    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=5_000, daily_minor=1_000_000, per_destination_daily_minor=1_000_000))
    proposal = _propose(profile)

    def disk_full(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(receipts, "_record", disk_full)
    with pytest.raises(OSError):
        lifecycle.default_lifecycle().prepare(proposal.proposal_id)
    monkeypatch.undo()
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_SIMULATED
    assert _receipts(proposal.proposal_id) == []


@pytest.mark.parametrize("moment", ["pending_approval", "after_claim"])
def test_the_owners_rejection_leaves_a_receipt(wallet_env, moment):
    import json

    from apps.vool_api_server import create_app
    from core.wallet import lifecycle, limits, proposals
    from core.web.api.runtime import RuntimeServices
    from tests.asgi_harness import asgi_request

    engine = lifecycle.default_lifecycle()
    if moment == "pending_approval":
        proposal = engine.prepare(_propose(_pocket()).proposal_id)
    else:
        # an external signer's payment is claimed (its spend held) while the signing request waits
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        from core.vool_wallet import b58encode
        from core.wallet import custody

        public_key = b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
        proposal = engine.prepare(_propose(custody.register_external_signer_wallet(public_key, label="ext")).proposal_id)
        engine.request_external_signature(proposal.proposal_id)
    app = create_app(RuntimeServices(display_name="VOOL"))
    _status, _headers, raw = asgi_request(app, method="POST", path="/api/wallet/reject", body=json.dumps({"proposal_id": proposal.proposal_id}).encode(),
                                         headers={"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"})
    assert json.loads(raw)["rejected"] is True
    if moment == "pending_approval":
        _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_approval_rejected", reason="owner_rejected")
    else:
        _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_approval_rejected", hold=limits.RESERVATION_RELEASED,
                                reason="owner_rejected_after_claim")
    assert wallet_env["rpc"].send_count() == 0


@pytest.mark.parametrize("door", ["submit_after_its_ttl", "reaper"])
def test_a_signing_request_that_expires_leaves_a_receipt(wallet_env, monkeypatch, door):
    """An external signer's payment is claimed (its spend held) and its signing request outlives its TTL: the signature
    arrives too late at the submit door, or the reaper finds it first. Either door expires the payment, releases the
    hold and records the expiry's receipt with it, nothing charged."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import custody, external_signing, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    engine = lifecycle.default_lifecycle()
    public_key = b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    proposal = engine.prepare(_propose(custody.register_external_signer_wallet(public_key, label="ext")).proposal_id)
    request = engine.request_external_signature(proposal.proposal_id)
    real_now = external_signing._now
    monkeypatch.setattr(external_signing, "_now", lambda: real_now() + external_signing.REQUEST_TTL_SECONDS + 1)
    if door == "reaper":
        assert engine.reap_stale_signing_requests() == 1
    else:
        with pytest.raises(WalletFault) as late:
            engine.submit_external_signature(request["request_id"], signature_b58="1" * 88)
        assert (late.value.code, late.value.context["reason"]) == ("wallet_approval_rejected", "signing_request_expired")
    _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_EXPIRED, fault_code="wallet_approval_rejected", hold=limits.RESERVATION_RELEASED,
                            reason="signing_request_expired")
    assert wallet_env["rpc"].send_count() == 0


class _ProcessStopped(BaseException):
    """The process stops here: no handler runs and nothing after this point is written."""


def _claimed_external_payment():
    """An external signer's payment, claimed (its spend held) while its signing request waits for the signature."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import custody, lifecycle

    engine = lifecycle.default_lifecycle()
    public_key = b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())
    proposal = engine.prepare(_propose(custody.register_external_signer_wallet(public_key, label="ext")).proposal_id)
    return engine, proposal, engine.request_external_signature(proposal.proposal_id)


def _reject(proposal_id):
    from core.web.api.wallet_api import handle_wallet_post

    return handle_wallet_post("/api/wallet/reject", {"proposal_id": proposal_id}, client_host="127.0.0.1", headers={"Host": "127.0.0.1", "Origin": "http://127.0.0.1"})


@pytest.mark.parametrize("door", ["submit_after_its_ttl", "reaper", "owner_reject"])
def test_a_stop_while_an_unsent_payment_is_ended_leaves_it_whole_for_the_next_door(wallet_env, monkeypatch, door):
    """The process stops while it ends a claimed payment whose signature never came back: its signing request expired
    (at the submit door, or found by the reaper), or the owner rejected it. Ending it is one transaction (the signing
    request closed, the hold released, the state moved with its receipt), so the stop leaves the payment exactly as it
    was, waiting with its request open and its spend held. After the restart, the reaper or the owner's rejection ends
    it whole: one receipt, nothing held."""
    import json

    from core.wallet import external_signing, lifecycle, limits, proposals, receipts

    engine, proposal, request = _claimed_external_payment()
    real_now = external_signing._now
    if door != "owner_reject":
        monkeypatch.setattr(external_signing, "_now", lambda: real_now() + external_signing.REQUEST_TTL_SECONDS + 1)

    def stop(*_args, **_kwargs):
        raise _ProcessStopped

    with monkeypatch.context() as stopped:
        stopped.setattr(receipts, "_record_refusal", stop)
        with pytest.raises(_ProcessStopped):
            if door == "submit_after_its_ttl":
                engine.submit_external_signature(request["request_id"], signature_b58="1" * 88)
            elif door == "reaper":
                engine.reap_stale_signing_requests()
            else:
                _reject(proposal.proposal_id)
    as_it_was = (proposals.get_proposal(proposal.proposal_id).state, external_signing.get_signing_request(request["request_id"])["state"],
                 limits.reservation_state(proposal.proposal_id), _receipts(proposal.proposal_id))
    assert as_it_was == (proposals.STATE_AWAITING_SIGNATURE, external_signing.STATE_OPEN, limits.RESERVATION_RESERVED, [])
    restarted = lifecycle.default_lifecycle()
    if door == "owner_reject":
        assert json.loads(_reject(proposal.proposal_id).body)["rejected"] is True
        _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_approval_rejected", hold=limits.RESERVATION_RELEASED,
                                reason="owner_rejected_after_claim")
    else:
        assert restarted.reap_stale_signing_requests() == 1
        _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_EXPIRED, fault_code="wallet_approval_rejected", hold=limits.RESERVATION_RELEASED,
                                reason="signing_request_expired")
    assert wallet_env["rpc"].send_count() == 0


@pytest.mark.parametrize("request_left", ["expired", "consumed"])
def test_a_payment_left_waiting_on_a_closed_signing_request_is_ended_by_the_reaper_only_when_nothing_was_dispatched(wallet_env, request_left):
    """A claimed payment can be left waiting on a signing request that is already closed: a wallet that stopped between
    closing the request and ending the payment (releases before this one did these in separate steps). An expired request
    was never consumed, so nothing was dispatched through it and no door takes the payment any more: the reaper ends
    it, the hold released, with its receipt. A consumed request is a signature that may be in flight: the reaper leaves
    that payment and its hold as they are."""
    from core.wallet import external_signing, limits, proposals

    engine, proposal, request = _claimed_external_payment()
    if request_left == "expired":
        assert external_signing.expire_signing_request(request["request_id"])
    else:
        external_signing.consume_signing_request(request["request_id"])
    if request_left == "consumed":
        assert engine.reap_stale_signing_requests() == 0
        assert (proposals.get_proposal(proposal.proposal_id).state, limits.reservation_state(proposal.proposal_id)) == (proposals.STATE_AWAITING_SIGNATURE, limits.RESERVATION_RESERVED)
        assert _receipts(proposal.proposal_id) == []
        return
    assert engine.reap_stale_signing_requests() == 1
    _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_EXPIRED, fault_code="wallet_approval_rejected", hold=limits.RESERVATION_RELEASED,
                            reason="signing_request_expired")
    assert engine.reap_stale_signing_requests() == 0


@pytest.mark.parametrize("door", ["submit_after_its_ttl", "reaper", "owner_reject"])
@pytest.mark.parametrize("stop_at", ["the_effect_resolution", "a_release_outside_the_transaction"])
def test_a_stop_after_an_unsent_payment_is_ended_leaves_no_hold_behind(wallet_env, monkeypatch, door, stop_at):
    """Once the transaction that ends a claimed payment whose signature never came back commits, the payment is ended
    whole: its state, its receipt and its hold released together. The process stops after that commit: at the effect
    resolution, the one step that follows it, or at a hold release made outside it (there is none). The payment is
    still ended with one receipt and nothing held, and the effect resolver, which reads the ended payment, resolves
    its effect as safe to retry: nothing was sent."""
    from core.effect_reconciliation import ResolutionOutcome, reconcile_unresolved_effect
    from core.runtime_continuity import find_active_unresolved_effect
    from core.wallet import external_signing, limits, proposals, reconciliation
    from core.wallet.errors import WalletFault

    engine, proposal, request = _claimed_external_payment()
    real_now = external_signing._now
    if door != "owner_reject":
        monkeypatch.setattr(external_signing, "_now", lambda: real_now() + external_signing.REQUEST_TTL_SECONDS + 1)

    def stop(*_args, **_kwargs):
        raise _ProcessStopped

    stopped = False
    with monkeypatch.context() as stopping:
        stopping.setattr(*((reconciliation, "resolve_payment_effect") if stop_at == "the_effect_resolution" else (limits, "release_spend")), stop)
        try:
            if door == "submit_after_its_ttl":
                with pytest.raises(WalletFault):
                    engine.submit_external_signature(request["request_id"], signature_b58="1" * 88)
            elif door == "reaper":
                assert engine.reap_stale_signing_requests() == 1
            else:
                _reject(proposal.proposal_id)
        except _ProcessStopped:
            stopped = True
    assert stopped is (stop_at == "the_effect_resolution")
    if door == "owner_reject":
        _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_approval_rejected", hold=limits.RESERVATION_RELEASED,
                                reason="owner_rejected_after_claim")
    else:
        _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_EXPIRED, fault_code="wallet_approval_rejected", hold=limits.RESERVATION_RELEASED,
                                reason="signing_request_expired")
    assert external_signing.get_signing_request(request["request_id"])["state"] == external_signing.STATE_EXPIRED
    active = find_active_unresolved_effect(reconciliation.logical_effect_id(proposal.proposal_id))
    if stopped:
        assert active is not None and reconcile_unresolved_effect(active).outcome == ResolutionOutcome.FAILED_SAFE_TO_RETRY
    else:
        assert active is None, "the effect was resolved as not applied"
    assert wallet_env["rpc"].send_count() == 0


def _interrupt_the_statement(monkeypatch, matches, interruption):
    """Every wallet store connection, wrapped: the first statement that ``matches`` (its SQL with whitespace collapsed,
    and its parameters) raises ``interruption()`` in place of running, whatever code issues it and on whatever
    connection. A stop (:class:`_ProcessStopped`) skips every handler, and its connection closes without a commit, as
    when the process dies there; a store error is what a lock timeout raises. Returns the statements it interrupted."""
    from core.wallet import store

    real = store._raw_conn
    interrupted: list[str] = []

    class _Interrupting:
        def __init__(self, conn):
            object.__setattr__(self, "_conn", conn)

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def __setattr__(self, name, value):
            setattr(self._conn, name, value)

        def execute(self, sql, *args):
            if not interrupted and matches(" ".join(str(sql).split()), tuple(args[0]) if args else ()):
                interrupted.append(str(sql))
                raise interruption()
            return self._conn.execute(sql, *args)

    monkeypatch.setattr(store, "_raw_conn", lambda: _Interrupting(real()))
    return interrupted


def _the_hold_write(sql, _params):
    return sql.startswith("INSERT INTO wallet_spend_ledger")


def _the_hold_release(sql, params):
    from core.wallet import limits

    return sql.startswith("UPDATE wallet_spend_ledger SET state = ?") and params[:1] == (limits.RESERVATION_RELEASED,)


def _a_store_error():
    import sqlite3

    return sqlite3.OperationalError("database is locked")


def _external_wallet():
    """An external signer's wallet whose key this test holds, so it can answer the signing request."""
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode
    from core.wallet import custody

    key = Ed25519PrivateKey.generate()
    return custody.register_external_signer_wallet(b58encode(key.public_key().public_bytes_raw()), label="ext"), key


def _approve_and_pay(engine, lane, proposal_id, key):
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import approval

    if lane == "pocket":
        return engine.approve_and_execute(proposal_id, approver=approval.PinApprover(PIN))
    view = engine.request_external_signature(proposal_id)
    return engine.submit_external_signature(view["request_id"], signature_b58=b58encode(key.sign(b58decode(view["message_b58"]))))


def _one_payment_a_day(profile):
    """A daily limit of one 10_000 payment and its 5_000 fee."""
    from core.wallet import limits

    limits.set_limits(profile.wallet_id, "SOL", limits.SpendLimits(per_tx_minor=1_000_000, daily_minor=15_000, per_destination_daily_minor=1_000_000))


@pytest.mark.parametrize("lane", ["external_signer", "pocket"])
@pytest.mark.parametrize("interruption", ["a_stop", "a_store_error"])
def test_an_approval_and_its_hold_commit_together(wallet_env, monkeypatch, lane, interruption):
    """The approval moves the payment to approved and holds its amount and its fee against the spend limits in one
    transaction. The hold is what the limits count, so an approved payment always holds. A stop or a store error (a
    lock timeout) at the hold's write leaves the payment awaiting approval, nothing held, no signing request and no
    receipt. The next approval claims it whole and pays it, and the limits count it: under a daily limit of one payment
    and its fee, a second payment is refused."""
    import sqlite3

    from core.wallet import external_signing, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile, key = (_pocket(), None) if lane == "pocket" else _external_wallet()
    _one_payment_a_day(profile)
    engine = lifecycle.default_lifecycle()
    proposal = engine.prepare(_propose(profile).proposal_id)
    with monkeypatch.context() as interrupting:
        interrupted = _interrupt_the_statement(interrupting, _the_hold_write, _ProcessStopped if interruption == "a_stop" else _a_store_error)
        with pytest.raises(_ProcessStopped if interruption == "a_stop" else sqlite3.OperationalError):
            _approve_and_pay(engine, lane, proposal.proposal_id, key)
    assert interrupted, "the approval reached the hold's write"
    assert (proposals.get_proposal(proposal.proposal_id).state, limits.reservation_state(proposal.proposal_id)) == (proposals.STATE_PENDING_APPROVAL, "")
    assert external_signing.open_request_for_proposal(proposal.proposal_id) is None and _receipts(proposal.proposal_id) == []
    restarted = lifecycle.default_lifecycle()
    assert _approve_and_pay(restarted, lane, proposal.proposal_id, key).state == proposals.STATE_CONFIRMED
    assert limits.reservation_state(proposal.proposal_id) == limits.RESERVATION_SETTLED
    second = _propose(profile)
    with pytest.raises(WalletFault) as refused:
        restarted.prepare(second.proposal_id)
        _approve_and_pay(restarted, lane, second.proposal_id, key)
    assert refused.value.code == "wallet_limit_exceeded"
    assert wallet_env["rpc"].send_count() == 1


@pytest.mark.parametrize("left", ["nothing_held", "its_hold_released"])
def test_an_approved_payment_that_holds_nothing_is_ended_not_handed_to_the_signer(wallet_env, left):
    """A payment can be left approved with nothing held: a release before this one stopped between the approval and the
    hold (two transactions then), or an ending released the hold and stopped before it ended the payment. The limits
    never counted it. Resuming that approval would hand the payment to the signer and it would leave uncounted, past the
    limits. The wallet ends it instead, with its receipt: no signing request, nothing sent. The limits are as they
    were, so a payment of the same size is approved and paid."""
    from core.wallet import external_signing, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile, key = _external_wallet()
    _one_payment_a_day(profile)
    engine = lifecycle.default_lifecycle()
    proposal = engine.prepare(_propose(profile).proposal_id)
    assert proposals.transition(proposal.proposal_id, proposals.STATE_APPROVED, detail={"method": "external_signer"}, expected_state=proposals.STATE_PENDING_APPROVAL)
    if left == "its_hold_released":
        assert limits.reserve_spend(wallet_id=profile.wallet_id, asset="SOL", amount_minor=10_000, destination=DESTINATION, proposal_id=proposal.proposal_id, fee_minor=5_000,
                                    chain=proposal.network).ok
        limits.release_spend(proposal.proposal_id)
    with pytest.raises(WalletFault) as refused:
        engine.request_external_signature(proposal.proposal_id)
    assert (refused.value.code, refused.value.context["reason"]) == ("wallet_approval_rejected", "spend_not_held")
    _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_approval_rejected",
                            hold="" if left == "nothing_held" else limits.RESERVATION_RELEASED, reason="spend_not_held")
    assert external_signing.open_request_for_proposal(proposal.proposal_id) is None
    next_one = engine.prepare(_propose(profile).proposal_id)
    assert _approve_and_pay(engine, "external_signer", next_one.proposal_id, key).state == proposals.STATE_CONFIRMED
    assert wallet_env["rpc"].send_count() == 1


@pytest.mark.parametrize("door", ["the_request_asked_again", "the_signature_submitted"])
def test_a_signing_request_for_a_payment_that_holds_nothing_is_ended_not_signed_or_sent(wallet_env, monkeypatch, door):
    """A release before this one resumed such an approval and opened its signing request: the payment waits for its
    signature with nothing held. Neither door hands it on. Asked for the request again, or given the signature, the
    wallet ends the payment with its receipt and closes its request in the same transaction. Nothing is sent."""
    from core.vool_wallet import b58decode, b58encode
    from core.wallet import external_signing, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile, key = _external_wallet()
    engine = lifecycle.default_lifecycle()
    proposal = engine.prepare(_propose(profile).proposal_id)
    approved = proposals.transition(proposal.proposal_id, proposals.STATE_APPROVED, detail={"method": "external_signer"}, expected_state=proposals.STATE_PENDING_APPROVAL)
    with monkeypatch.context() as older_release:
        older_release.setattr(engine, "_require_held", lambda *_args, **_kwargs: None, raising=False)  # that release's resume had no hold check
        view = engine._open_external_request_after_claim(approved)
    assert (proposals.get_proposal(proposal.proposal_id).state, limits.reservation_state(proposal.proposal_id)) == (proposals.STATE_AWAITING_SIGNATURE, "")
    with pytest.raises(WalletFault) as refused:
        if door == "the_request_asked_again":
            engine.request_external_signature(proposal.proposal_id)
        else:
            engine.submit_external_signature(view["request_id"], signature_b58=b58encode(key.sign(b58decode(view["message_b58"]))))
    assert (refused.value.code, refused.value.context["reason"]) == ("wallet_approval_rejected", "spend_not_held")
    _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_approval_rejected", reason="spend_not_held")
    assert external_signing.get_signing_request(view["request_id"])["state"] == external_signing.STATE_EXPIRED
    assert wallet_env["rpc"].send_count() == 0


@pytest.mark.parametrize("door", ["submit_after_its_ttl", "reaper", "owner_reject"])
def test_a_stop_at_the_hold_release_while_an_unsent_payment_is_ended_leaves_it_whole_for_the_next_door(wallet_env, monkeypatch, door):
    """The stop lands on the statement that releases the hold, whichever code issues it and on whichever connection.
    The release belongs to the transaction that ends the payment, so the stop leaves the payment exactly as it was:
    waiting, its request open, its spend held, no receipt. The next door ends it whole: one receipt, nothing held."""
    import json

    from core.wallet import external_signing, lifecycle, limits, proposals

    engine, proposal, request = _claimed_external_payment()
    real_now = external_signing._now
    if door != "owner_reject":
        monkeypatch.setattr(external_signing, "_now", lambda: real_now() + external_signing.REQUEST_TTL_SECONDS + 1)
    with monkeypatch.context() as interrupting:
        interrupted = _interrupt_the_statement(interrupting, _the_hold_release, _ProcessStopped)
        with pytest.raises(_ProcessStopped):
            if door == "submit_after_its_ttl":
                engine.submit_external_signature(request["request_id"], signature_b58="1" * 88)
            elif door == "reaper":
                engine.reap_stale_signing_requests()
            else:
                _reject(proposal.proposal_id)
    assert interrupted, "the ending reached the hold's release"
    as_it_was = (proposals.get_proposal(proposal.proposal_id).state, external_signing.get_signing_request(request["request_id"])["state"],
                 limits.reservation_state(proposal.proposal_id), _receipts(proposal.proposal_id))
    assert as_it_was == (proposals.STATE_AWAITING_SIGNATURE, external_signing.STATE_OPEN, limits.RESERVATION_RESERVED, [])
    restarted = lifecycle.default_lifecycle()
    if door == "owner_reject":
        assert json.loads(_reject(proposal.proposal_id).body)["rejected"] is True
        _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_REJECTED, fault_code="wallet_approval_rejected", hold=limits.RESERVATION_RELEASED,
                                reason="owner_rejected_after_claim")
    else:
        assert restarted.reap_stale_signing_requests() == 1
        _assert_refusal_receipt(proposal.proposal_id, state=proposals.STATE_EXPIRED, fault_code="wallet_approval_rejected", hold=limits.RESERVATION_RELEASED,
                                reason="signing_request_expired")
    assert wallet_env["rpc"].send_count() == 0


def _the_receipt_write(sql, _params):
    return sql.startswith("INSERT INTO wallet_receipts")


def _sign_for(key, view):
    from core.vool_wallet import b58decode, b58encode

    return b58encode(key.sign(b58decode(view["message_b58"])))


@pytest.mark.parametrize(("failure", "ending_lands"), [
    ("the_request_fails_to_open", "at_the_submit_door"), ("the_effect_is_in_flight", "at_the_submit_door"), ("the_effect_gateway_refuses", "at_the_submit_door"),
    ("the_request_fails_to_open", "after_it_was_sent"),
])
def test_an_approval_that_fails_after_its_claim_never_ends_a_payment_another_door_moved_on(wallet_env, monkeypatch, failure, ending_lands):
    """The owner approves one payment twice at once. The first approval claims it, approved and held, then fails before
    it hands it on: its signing request does not open, its effect is already in flight, or the effect gateway refuses.
    Meanwhile the second approval resumes the approved payment and hands it to the wallet, whose answer reaches the
    submit door. The first approval's ending lands while that answer is at the submit door, past its hold check, or
    after the payment was sent. The payment is no longer the one it claimed, so the ending writes nothing: no receipt,
    the hold stays and the payment's effect stays open. The payment is sent once and the limits count it: under a daily
    limit of one payment and its fee, a second payment is refused."""
    import sqlite3
    import threading

    from core.runtime_continuity import find_active_unresolved_effect
    from core.wallet import external_signing, lifecycle, limits, proposals, reconciliation
    from core.wallet.errors import WalletFault

    profile, key = _external_wallet()
    _one_payment_a_day(profile)
    first_engine = lifecycle.default_lifecycle()
    payment = first_engine.prepare(_propose(profile).proposal_id)
    second = first_engine.prepare(_propose(profile).proposal_id)
    claimed, ending = threading.Event(), threading.Event()
    first: dict[str, object] = {}

    def approve_first():
        try:
            first_engine.request_external_signature(payment.proposal_id)
            first["outcome"] = "handed on"
        except WalletFault as exc:
            first["outcome"] = (exc.code, str(exc.context.get("reason") or "").split(":")[0])

    first_approval = threading.Thread(target=approve_first)

    def held_up_then(failing):
        """In the first approval, after its claim committed: wait while the second approval runs, then fail."""
        def hook(*args, **kwargs):
            if threading.current_thread() is not first_approval:
                return real(*args, **kwargs)
            claimed.set()
            assert ending.wait(30), "the first approval was let go"
            return failing()
        return hook

    def fail_to_open():
        raise sqlite3.OperationalError("database is locked")

    def refuse():
        raise RuntimeError("effect gateway refused")

    if failure == "the_request_fails_to_open":
        real = external_signing.open_signing_request
        monkeypatch.setattr(external_signing, "open_signing_request", held_up_then(fail_to_open))
    elif failure == "the_effect_is_in_flight":
        real = reconciliation.reserve_payment_effect
        monkeypatch.setattr(reconciliation, "reserve_payment_effect", held_up_then(lambda: {"outcome": "in_flight"}))
    else:
        real = first_engine._open_effect
        monkeypatch.setattr(first_engine, "_open_effect", held_up_then(refuse))

    def let_the_first_end():
        ending.set()
        first_approval.join(30)
        assert not first_approval.is_alive()

    def as_the_second_left_it():
        return (proposals.get_proposal(payment.proposal_id).state, limits.reservation_state(payment.proposal_id), _receipts(payment.proposal_id))

    first_approval.start()
    try:
        assert claimed.wait(30), "the first approval claimed the payment"
        assert (proposals.get_proposal(payment.proposal_id).state, limits.reservation_state(payment.proposal_id)) == (proposals.STATE_APPROVED, limits.RESERVATION_RESERVED)
        view = lifecycle.default_lifecycle().request_external_signature(payment.proposal_id)
        assert view.get("resume") is True, "the second approval resumed the approved payment"
        if ending_lands == "at_the_submit_door":
            real_verify = external_signing.verify_submission

            def the_first_ends_meanwhile(record, **kwargs):
                let_the_first_end()
                assert as_the_second_left_it() == (proposals.STATE_AWAITING_SIGNATURE, limits.RESERVATION_RESERVED, []), "the first approval's ending wrote nothing"
                if failure != "the_effect_is_in_flight":  # the first approval reserved the payment's effect
                    assert find_active_unresolved_effect(reconciliation.logical_effect_id(payment.proposal_id)) is not None, "nor resolved the effect"
                return real_verify(record, **kwargs)

            monkeypatch.setattr(external_signing, "verify_submission", the_first_ends_meanwhile)
        sent = lifecycle.default_lifecycle().submit_external_signature(view["request_id"], signature_b58=_sign_for(key, view))
        if ending_lands == "after_it_was_sent":
            let_the_first_end()
    finally:
        ending.set()
        first_approval.join(30)
    assert first["outcome"] == {"the_request_fails_to_open": ("wallet_signing_unavailable", "signing_request_failed"),
                                "the_effect_is_in_flight": ("wallet_duplicate_payment", "effect_in_flight"),
                                "the_effect_gateway_refuses": ("wallet_limit_exceeded", "effect_gateway_refused")}[failure]
    assert sent.state == proposals.STATE_CONFIRMED
    state, hold, receipts_left = as_the_second_left_it()
    assert (state, hold) == (proposals.STATE_CONFIRMED, limits.RESERVATION_SETTLED), "the payment that left is counted"
    assert [(r["state"], r["fault_code"]) for r in receipts_left] == [(proposals.STATE_CONFIRMED, "")], "one payment, one receipt"
    with pytest.raises(WalletFault) as refused:
        lifecycle.default_lifecycle().request_external_signature(second.proposal_id)
    assert refused.value.code == "wallet_limit_exceeded"
    assert wallet_env["rpc"].send_count() == 1


@pytest.mark.parametrize("at", ["the_hold_release", "the_receipt_write"])
def test_a_stop_inside_the_ending_of_an_approval_that_failed_after_its_claim_leaves_it_for_the_resume(wallet_env, monkeypatch, at):
    """An approval claims a payment, approved and held, then cannot open its signing request and ends it: the payment
    fails with its receipt and its hold is released, in one transaction. A stop inside it, at the release or at the
    receipt, leaves the payment approved and held with no receipt, as the claim left it. The owner's next approval
    resumes it and it is paid once, counted against the limits."""
    import sqlite3

    from core.wallet import external_signing, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile, key = _external_wallet()
    _one_payment_a_day(profile)
    engine = lifecycle.default_lifecycle()
    payment = engine.prepare(_propose(profile).proposal_id)
    second = engine.prepare(_propose(profile).proposal_id)

    def fail_to_open(*_args, **_kwargs):
        raise sqlite3.OperationalError("database is locked")

    with monkeypatch.context() as failing:
        failing.setattr(external_signing, "open_signing_request", fail_to_open)
        interrupted = _interrupt_the_statement(failing, _the_hold_release if at == "the_hold_release" else _the_receipt_write, _ProcessStopped)
        with pytest.raises(_ProcessStopped):
            engine.request_external_signature(payment.proposal_id)
    assert interrupted, "the ending reached the statement"
    as_claimed = (proposals.get_proposal(payment.proposal_id).state, limits.reservation_state(payment.proposal_id), _receipts(payment.proposal_id))
    assert as_claimed == (proposals.STATE_APPROVED, limits.RESERVATION_RESERVED, [])
    assert external_signing.open_request_for_proposal(payment.proposal_id) is None
    restarted = lifecycle.default_lifecycle()
    view = restarted.request_external_signature(payment.proposal_id)
    assert view.get("resume") is True
    assert restarted.submit_external_signature(view["request_id"], signature_b58=_sign_for(key, view)).state == proposals.STATE_CONFIRMED
    assert limits.reservation_state(payment.proposal_id) == limits.RESERVATION_SETTLED
    with pytest.raises(WalletFault) as refused:
        restarted.request_external_signature(second.proposal_id)
    assert refused.value.code == "wallet_limit_exceeded"
    assert wallet_env["rpc"].send_count() == 1


@pytest.mark.parametrize("decision", ["the_owner_rejects_it", "it_expires"])
def test_an_approval_whose_payment_was_decided_meanwhile_holds_nothing(wallet_env, monkeypatch, decision):
    """The owner's rejection, or the payment's expiry, lands between an approval passing its door and its claim. The
    claim's compare-and-set loses and it holds nothing: the payment stays as that decision left it and the limits do not
    count it, so a payment of the full daily limit is then paid."""
    from core.wallet import approval, lifecycle, limits, proposals
    from core.wallet.errors import WalletFault

    profile = _pocket()
    _one_payment_a_day(profile)
    engine = lifecycle.default_lifecycle()
    payment = engine.prepare(_propose(profile).proposal_id)
    decided = proposals.STATE_REJECTED if decision == "the_owner_rejects_it" else proposals.STATE_EXPIRED
    real_claim = engine._claim
    landed: list[str] = []

    def decided_then_claim(claiming, *args, **kwargs):
        if not landed:
            landed.append(claiming.proposal_id)
            assert proposals.transition(claiming.proposal_id, decided, expected_state=proposals.STATE_PENDING_APPROVAL) is not None
        return real_claim(claiming, *args, **kwargs)

    monkeypatch.setattr(engine, "_claim", decided_then_claim)
    with pytest.raises(WalletFault):
        engine.approve_and_execute(payment.proposal_id, approver=approval.PinApprover(PIN))
    assert landed, "the decision landed before the claim"
    assert (proposals.get_proposal(payment.proposal_id).state, limits.reservation_state(payment.proposal_id)) == (decided, "")
    monkeypatch.setattr(engine, "_claim", real_claim)
    full = engine.prepare(_propose(profile).proposal_id)
    assert engine.approve_and_execute(full.proposal_id, approver=approval.PinApprover(PIN)).state == proposals.STATE_CONFIRMED
    assert wallet_env["rpc"].send_count() == 1
