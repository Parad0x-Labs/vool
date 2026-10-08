"""x402 end to end: a paid resource answers 402 -> the offer becomes a capped proposal ->
simulation -> limits -> operator approval -> testnet payment -> the request is retried with the
X-PAYMENT header -> the receipt binds request, payment and returned resource. Repeated fetches
and retries never pay twice.
"""
from __future__ import annotations

import json

import pytest

from tests.wallet._rig import DESTINATION, ScriptedX402Resource

pytestmark = [pytest.mark.safety]
PIN = "246810"


@pytest.fixture
def resource(wallet_env, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "2000")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    with ScriptedX402Resource(wallet_env["rpc"]) as server:
        yield server


def _pocket(custody):
    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def test_fetch_detects_402_and_parks_a_capped_proposal_without_paying(wallet_env, resource):
    from core.wallet import custody, proposals, x402

    profile = _pocket(custody)
    outcome = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert outcome.status == x402.OUTCOME_PAYMENT_REQUIRED and outcome.http_status == 402
    proposal = proposals.get_proposal(outcome.proposal_id)
    assert proposal.origin == proposals.ORIGIN_X402 and proposal.state == proposals.STATE_PENDING_APPROVAL and proposal.amount_minor == 1500
    again = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert again.proposal_id == outcome.proposal_id, "a second fetch reuses the parked proposal"
    assert wallet_env["rpc"].send_count() == 0 and resource.deliveries == []
    binding = x402.binding_for_proposal(outcome.proposal_id)
    assert binding["state"] == x402.BINDING_PAYMENT_REQUIRED and binding["url"] == resource.url


def test_approved_payment_retries_the_request_and_binds_the_receipt(wallet_env, resource):
    from core.wallet import approval, custody, lifecycle, receipts, x402

    profile = _pocket(custody)
    parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    receipt = lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=approval.PinApprover(PIN))
    delivered = x402.retry_paid_resource(parked.proposal_id)
    assert delivered.status == x402.OUTCOME_DELIVERED and delivered.http_status == 200 and b"PAID REPORT" in delivered.body
    assert delivered.tx_signature == receipt.tx_signature
    assert len(resource.deliveries) == 1 and resource.deliveries[0]["signature"] == receipt.tx_signature
    binding = x402.binding_for_proposal(parked.proposal_id)
    assert binding["state"] == x402.BINDING_DELIVERED and binding["resource_status"] == 200 and binding["resource_digest"]
    bound = [r for r in receipts.list_receipts() if r.get("x402")]
    assert bound and bound[0]["x402"]["request_digest"] == binding["request_digest"] and bound[0]["tx_signature"] == receipt.tx_signature
    assert bound[0]["x402"]["resource_digest"] == binding["resource_digest"]


def test_duplicate_retries_and_refetches_never_repay(wallet_env, resource):
    from core.wallet import approval, custody, lifecycle, x402

    profile = _pocket(custody)
    parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=approval.PinApprover(PIN))
    first = x402.retry_paid_resource(parked.proposal_id)
    second = x402.retry_paid_resource(parked.proposal_id)
    third = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert first.status == second.status == third.status == x402.OUTCOME_DELIVERED
    assert first.tx_signature == second.tx_signature == third.tx_signature
    assert wallet_env["rpc"].send_count() == 1, "one payment, however many retries"
    assert third.repaid is False and third.proposal_id == parked.proposal_id


@pytest.mark.parametrize("price", [1500, 1400], ids=["same-price", "other-price"])
def test_a_payment_whose_broadcast_outcome_is_unknown_is_never_paid_again(wallet_env, resource, price):
    """The node took the transaction and then answered 500: the payment may land, and no transaction id is known.
    Fetching the same request again refuses before anything is sent, so a resource asking on other terms cannot get
    a second payment parked for it."""
    from core.wallet import approval, custody, lifecycle, limits, proposals, x402
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    wallet_env["rpc"].send_mode = "accept_then_500"
    with pytest.raises(WalletFault) as broadcast:
        lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=approval.PinApprover(PIN))
    assert str(broadcast.value.context.get("reason") or "").startswith("broadcast_unknown:")
    wallet_env["rpc"].send_mode = "ok"
    proposal = proposals.get_proposal(parked.proposal_id)
    assert proposal.state == proposals.STATE_BROADCAST and proposal.tx_signature == ""
    assert limits.reservation_state(parked.proposal_id) == limits.RESERVATION_RESERVED
    resource.amount_minor = price
    with pytest.raises(WalletFault) as again:
        x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert again.value.code == "wallet_duplicate_payment" and again.value.context["reason"] == "payment_outcome_unknown"
    assert resource.challenges == 1 and resource.deliveries == []
    assert wallet_env["rpc"].send_count() == 1
    assert x402.binding_for_proposal(parked.proposal_id)["proposal_id"] == parked.proposal_id


def _stale_first_check(monkeypatch, x402):
    """The second caller's first look at the request happened before the first caller's payment was bound: its gate
    answers 'nothing holds this request' once, then reads the store as it is."""
    real = x402._bound_outcome
    calls = []

    def stale_once(binding, **kwargs):
        calls.append(binding)
        return None if len(calls) == 1 else real(binding, **kwargs)

    monkeypatch.setattr(x402, "_bound_outcome", stale_once)


@pytest.mark.parametrize("outcome", ["unknown", "confirmed"])
def test_a_late_challenge_cannot_rebind_a_request_its_payment_holds(wallet_env, resource, monkeypatch, outcome):
    """Two callers fetch the same request. The first parks, is approved and pays (or its broadcast's outcome is
    unknown); the second passed its own check before that and its 402, on other terms, arrives late. The binding
    refuses the second proposal in the same statement that would rebind the request: no second payment is parked,
    and the proposal minted for the late challenge is rejected, never approvable."""
    from core.wallet import approval, custody, lifecycle, proposals, x402
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    if outcome == "unknown":
        wallet_env["rpc"].send_mode = "accept_then_500"
        with pytest.raises(WalletFault):
            lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=approval.PinApprover(PIN))
        wallet_env["rpc"].send_mode = "ok"
    else:
        assert lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=approval.PinApprover(PIN)).state == proposals.STATE_CONFIRMED
    resource.amount_minor = 1400
    _stale_first_check(monkeypatch, x402)
    if outcome == "unknown":
        with pytest.raises(WalletFault) as late:
            x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert late.value.code == "wallet_duplicate_payment" and late.value.context["reason"] == "payment_outcome_unknown"
    else:
        late = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert late.proposal_id == parked.proposal_id and late.repaid is False, "the late caller gets the one paid delivery"
    assert resource.challenges == 2, "the late caller's request did go out: its first check was stale"
    assert wallet_env["rpc"].send_count() == 1
    assert x402.binding_for_proposal(parked.proposal_id)["proposal_id"] == parked.proposal_id
    others = [p for p in proposals.list_proposals() if p.proposal_id != parked.proposal_id]
    assert [(p.state, p.amount_minor) for p in others] == [(proposals.STATE_REJECTED, 1400)]
    from core.wallet import receipts

    refused = [r for r in receipts.list_receipts() if r["proposal_id"] == others[0].proposal_id]
    assert [(r["state"], r["fault_code"], r["refusal"]["reason"], r["refusal"]["charged_amount_minor"]) for r in refused] == [
        (proposals.STATE_REJECTED, "wallet_duplicate_payment", "request_bound_to_another_payment", 0)]


def test_a_challenge_that_arrives_while_the_first_payment_is_prepared_cannot_take_its_request(wallet_env, resource, monkeypatch):
    """The first caller has bound the request and is still preparing its proposal (simulating it) when a second
    caller's 402 arrives on other terms. The request stays the first proposal's: the second proposal is rejected and
    never approvable, and one payment waits for the owner."""
    from core.wallet import custody, lifecycle, proposals, x402
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    asked = resource.amount_minor
    real_prepare = lifecycle.PaymentLifecycle.prepare
    late: list[WalletFault] = []

    def prepare_as_another_caller_arrives(self, proposal_id):
        if not late:
            late.append(None)
            resource.amount_minor = asked - 100
            with pytest.raises(WalletFault) as exc:
                x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
            late[0] = exc.value
        return real_prepare(self, proposal_id)

    monkeypatch.setattr(lifecycle.PaymentLifecycle, "prepare", prepare_as_another_caller_arrives)
    first = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert (late[0].code, late[0].context["reason"]) == ("wallet_duplicate_payment", "request_bound_to_another_payment")
    assert x402.binding_for_proposal(first.proposal_id)["proposal_id"] == first.proposal_id
    assert sorted((p.amount_minor, p.state) for p in proposals.list_proposals()) == [
        (asked - 100, proposals.STATE_REJECTED), (asked, proposals.STATE_PENDING_APPROVAL)]
    assert wallet_env["rpc"].send_count() == 0


def test_concurrent_callers_cannot_pay_in_proposal_creation_before_binding_window(wallet_env, resource, monkeypatch):
    """The first caller's proposal exists. Before its request was bound, another caller could find it, prepare it and
    have it approved, and a third caller whose 402 asks one lamport more found the request free, parked its own payment
    and had it approved too: two sends for one request. A fetched proposal now exists only together with its claim on
    the request, so whoever finds it finds the request already its own: the third caller gets the one payment's
    delivery."""
    from core.wallet import approval, custody, lifecycle, proposals, x402
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    engine = lifecycle.default_lifecycle()
    real_propose = proposals.propose_transaction
    later: list[x402.X402Outcome] = []

    def others_act_as_soon_as_it_exists(**kwargs):
        proposal = real_propose(**kwargs)
        if not later:
            later.append(None)
            engine.prepare(proposal.proposal_id)
            engine.approve_and_execute(proposal.proposal_id, approver=approval.PinApprover(PIN))
            resource.amount_minor = 1501
            later[0] = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
            if later[0].status == x402.OUTCOME_PAYMENT_REQUIRED:
                engine.approve_and_execute(later[0].proposal_id, approver=approval.PinApprover(PIN))
        return proposal

    monkeypatch.setattr(proposals, "propose_transaction", others_act_as_soon_as_it_exists)
    try:
        first = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    except WalletFault as exc:
        first = exc
    assert wallet_env["rpc"].send_count() == 1, "one request, one payment"
    # its own proposal was prepared and paid by someone else meanwhile: this caller's prepare is refused as a duplicate
    assert isinstance(first, WalletFault) and first.code == "wallet_duplicate_payment"
    [paid] = proposals.list_proposals()
    assert (paid.state, paid.amount_minor) == (proposals.STATE_CONFIRMED, 1500)
    assert (later[0].status, later[0].proposal_id, later[0].repaid) == (x402.OUTCOME_DELIVERED, paid.proposal_id, False)
    assert x402.binding_for_proposal(paid.proposal_id)["state"] == x402.BINDING_DELIVERED


@pytest.mark.parametrize("stage", ["prepare", "approve", "while_the_owner_approves"])
def test_a_fetched_payment_that_does_not_own_its_request_is_never_prepared_or_approved(wallet_env, resource, monkeypatch, stage):
    """A proposal the fetch door minted is paid only as its request's one payment. If the store says another payment
    holds that request (a store restored or edited behind the wallet's back), the lifecycle refuses it before
    anything is simulated, before the owner is asked for a PIN and before anything is held or sent: it is rejected,
    with a receipt that charges nothing. When the store changes while the owner is being asked, the claim refuses it
    before anything is held."""
    from core.wallet import approval, custody, lifecycle, limits, proposals, x402
    from core.wallet.errors import WalletFault
    from core.wallet.store import connection

    profile = _pocket(custody)
    engine = lifecycle.default_lifecycle()
    if stage == "prepare":
        real_prepare = lifecycle.PaymentLifecycle.prepare

        def stop(self, proposal_id):
            raise _ProcessStopped

        monkeypatch.setattr(lifecycle.PaymentLifecycle, "prepare", stop)
        with pytest.raises(_ProcessStopped):
            x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        monkeypatch.setattr(lifecycle.PaymentLifecycle, "prepare", real_prepare)
        [fetched] = proposals.list_proposals()
    else:
        fetched = proposals.get_proposal(x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id).proposal_id)
    left_in = fetched.state

    def move_the_binding():
        with connection() as conn:
            conn.execute("UPDATE wallet_x402_bindings SET proposal_id = ? WHERE proposal_id = ?", ("pay-another-payment", fetched.proposal_id))

    if stage != "while_the_owner_approves":
        move_the_binding()
    asked = []

    class Approver(approval.PinApprover):
        def approve(self, challenge):
            asked.append(challenge)
            if stage == "while_the_owner_approves":
                move_the_binding()
            return super().approve(challenge)

    with pytest.raises(WalletFault) as refused:
        if stage == "prepare":
            engine.prepare(fetched.proposal_id)
        else:
            engine.approve_and_execute(fetched.proposal_id, approver=Approver(PIN))
    assert (refused.value.code, refused.value.context["reason"]) == ("wallet_duplicate_payment", "request_bound_to_another_payment")
    assert len(asked) == (1 if stage == "while_the_owner_approves" else 0)
    assert wallet_env["rpc"].send_count() == 0 and limits.reservation_state(fetched.proposal_id) == ""
    assert proposals.get_proposal(fetched.proposal_id).state == proposals.STATE_REJECTED
    assert _refusal_reasons(fetched.proposal_id) == [(proposals.STATE_REJECTED, "wallet_duplicate_payment", "request_bound_to_another_payment", 0, 0)]
    assert left_in == (proposals.STATE_PROPOSED if stage == "prepare" else proposals.STATE_PENDING_APPROVAL)


def _refusal_reasons(proposal_id):
    from core.wallet import receipts

    return [(r["state"], r["fault_code"], r["refusal"]["reason"], r["refusal"]["charged_amount_minor"], r["refusal"]["charged_fee_minor"])
            for r in receipts.list_receipts() if r["proposal_id"] == proposal_id]


def test_a_payment_whose_prepare_refuses_frees_its_request(wallet_env, resource, monkeypatch):
    """The owner switches the wallet's network environment between the 402 and its prepare: prepare refuses before the
    proposal could become approvable. That proposal is ended with its receipt (nothing charged), so it does not keep
    the request; once the environment is back, the same request parks a fresh payment."""
    from core.wallet import custody, lifecycle, proposals, x402
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    real_prepare = lifecycle.PaymentLifecycle.prepare

    def prepare_after_the_switch(self, proposal_id):
        monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "mainnet")
        return real_prepare(self, proposal_id)

    monkeypatch.setattr(lifecycle.PaymentLifecycle, "prepare", prepare_after_the_switch)
    with pytest.raises(WalletFault) as exc:
        x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert exc.value.code == "wallet_environment_inactive"
    [refused] = proposals.list_proposals()
    assert refused.state == proposals.STATE_REJECTED
    assert _refusal_reasons(refused.proposal_id) == [(proposals.STATE_REJECTED, "wallet_environment_inactive", "prepare_refused", 0, 0)]
    monkeypatch.setattr(lifecycle.PaymentLifecycle, "prepare", real_prepare)
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    fresh = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert fresh.status == x402.OUTCOME_PAYMENT_REQUIRED and fresh.proposal_id != refused.proposal_id
    assert proposals.get_proposal(fresh.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert x402.binding_for_proposal(fresh.proposal_id)["proposal_id"] == fresh.proposal_id
    assert wallet_env["rpc"].send_count() == 0


class _ProcessStopped(BaseException):
    """The process stopping mid-prepare: nothing after the raise runs, as after a kill."""


def test_a_payment_abandoned_mid_prepare_frees_its_request_only_after_the_prepare_window(wallet_env, resource, monkeypatch):
    """The process stopped while the first payment was being prepared: the proposal was never approvable and nothing
    was held. Within the prepare window its request stays its own (a prepare may still be running). Past it, the next
    fetch ends the abandoned proposal with its receipt (nothing charged) and parks a fresh payment."""
    from datetime import datetime, timedelta, timezone

    from core.wallet import custody, lifecycle, proposals, x402
    from core.wallet.errors import WalletFault
    from core.wallet.store import connection

    profile = _pocket(custody)
    real_prepare = lifecycle.PaymentLifecycle.prepare

    def stop(self, proposal_id):
        raise _ProcessStopped

    monkeypatch.setattr(lifecycle.PaymentLifecycle, "prepare", stop)
    with pytest.raises(_ProcessStopped):
        x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    monkeypatch.setattr(lifecycle.PaymentLifecycle, "prepare", real_prepare)
    [abandoned] = proposals.list_proposals()
    assert abandoned.state == proposals.STATE_PROPOSED
    with pytest.raises(WalletFault) as exc:
        x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert exc.value.code == "wallet_duplicate_payment"
    assert [(p.proposal_id, p.state) for p in proposals.list_proposals()] == [(abandoned.proposal_id, proposals.STATE_PROPOSED)]
    past = (datetime.now(timezone.utc) - timedelta(seconds=x402.ABANDONED_PREPARE_SECONDS + 1)).isoformat()
    with connection() as conn:
        conn.execute("UPDATE wallet_proposals SET updated_at = ? WHERE proposal_id = ?", (past, abandoned.proposal_id))
    fresh = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert fresh.status == x402.OUTCOME_PAYMENT_REQUIRED and fresh.proposal_id != abandoned.proposal_id
    assert proposals.get_proposal(fresh.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert proposals.get_proposal(abandoned.proposal_id).state == proposals.STATE_REJECTED
    assert _refusal_reasons(abandoned.proposal_id) == [(proposals.STATE_REJECTED, "wallet_quote_expired", "prepare_abandoned", 0, 0)]
    assert wallet_env["rpc"].send_count() == 0


@pytest.mark.parametrize("first_check", ["current", "stale"])
def test_a_payment_whose_state_says_failed_while_its_spend_is_held_keeps_its_request_closed(wallet_env, resource, monkeypatch, first_check):
    """The dispatch record decides, not the state column: a payment whose broadcast outcome is unknown still holds its
    spend, so its request stays closed even when its proposal row says failed (a rewound or corrupted row). That holds
    at the re-fetch gate and, for a caller whose first check was stale, in the binding's own compare-and-set."""
    from core.wallet import approval, custody, lifecycle, limits, proposals, x402
    from core.wallet.errors import WalletFault
    from core.wallet.store import connection

    profile = _pocket(custody)
    parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    wallet_env["rpc"].send_mode = "accept_then_500"
    with pytest.raises(WalletFault):
        lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=approval.PinApprover(PIN))
    wallet_env["rpc"].send_mode = "ok"
    with connection() as conn:
        conn.execute("UPDATE wallet_proposals SET state = 'failed' WHERE proposal_id = ?", (parked.proposal_id,))
    assert limits.reservation_state(parked.proposal_id) == limits.RESERVATION_RESERVED
    resource.amount_minor = 1400
    if first_check == "stale":
        _stale_first_check(monkeypatch, x402)
    with pytest.raises(WalletFault) as again:
        x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert again.value.code == "wallet_duplicate_payment" and again.value.context["reason"] == "payment_outcome_unknown"
    assert resource.challenges == (2 if first_check == "stale" else 1) and wallet_env["rpc"].send_count() == 1
    assert x402.binding_for_proposal(parked.proposal_id)["proposal_id"] == parked.proposal_id
    late = [p for p in proposals.list_proposals() if p.proposal_id != parked.proposal_id]
    assert [(p.state, p.amount_minor) for p in late] == ([(proposals.STATE_REJECTED, 1400)] if first_check == "stale" else [])


def test_retry_before_approval_or_over_cap_is_refused(wallet_env, resource, monkeypatch):
    from core.wallet import custody, x402
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    with pytest.raises(WalletFault) as exc:
        x402.retry_paid_resource(parked.proposal_id)
    assert exc.value.code == "wallet_approval_rejected" and exc.value.context["reason"] == "payment_not_confirmed"
    assert resource.deliveries == []
    resource.amount_minor = 5000
    with pytest.raises(WalletFault) as exc2:
        x402.fetch_paid_resource(resource.url + "?big=1", wallet_id=profile.wallet_id)
    assert exc2.value.code == "wallet_x402_cap_exceeded"


def test_x402_fetch_refuses_private_targets_unless_loopback_is_explicitly_allowed(wallet_env, resource, monkeypatch):
    from core.wallet import custody, x402
    from core.wallet.errors import WalletFault

    profile = _pocket(custody)
    monkeypatch.delenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", raising=False)
    with pytest.raises(WalletFault) as exc:
        x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert exc.value.code == "wallet_network_disabled" and exc.value.context["reason"] == "x402_target_not_public"
    with pytest.raises(WalletFault):
        x402.fetch_paid_resource("http://169.254.169.254/latest/meta-data", wallet_id=profile.wallet_id)


def test_x402_payment_header_shape_is_the_x402_v1_exact_scheme(wallet_env, resource):
    import base64

    from core.wallet import approval, custody, lifecycle, x402

    profile = _pocket(custody)
    parked = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    receipt = lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=approval.PinApprover(PIN))
    header = x402.payment_header_for(parked.proposal_id)
    payload = json.loads(base64.b64decode(header))
    assert payload["x402Version"] == 1 and payload["scheme"] == "exact" and payload["network"] == "solana-devnet"
    assert payload["payload"]["signature"] == receipt.tx_signature and payload["payload"]["payer"] == profile.public_key
    assert payload["payload"]["payTo"] == DESTINATION


def _deterministic_digest_resource(rpc, *, digest_contains_zero: bool):
    """Bind the scripted resource on a port whose request digest deterministically lands in one
    redaction class. A sha256-hex digest with no '0' also matches the canonical base58 secret
    pattern -- the exact mechanism that masked a stored receipt digest in CI (run 35923574353,
    digest d2ff21956d1eb476a5d5e3b28d354c45a1bb473c2c447a68dd6e3b5c2fc1d3a1). The port->digest
    mapping is pure, so the class is chosen before any socket exists; candidates that are already
    held just move to the next port of the same class."""
    from core.wallet.x402 import _request_digest

    candidates = []
    for port in range(20000, 45000):
        if ("0" in _request_digest("GET", f"http://127.0.0.1:{port}/paid/report")) == digest_contains_zero:
            candidates.append(port)
        if len(candidates) == 8:
            break
    for port in candidates:
        try:
            return ScriptedX402Resource(rpc, port=port)
        except OSError:
            continue
    raise AssertionError("no free port in the requested digest class")


@pytest.fixture(params=[False, True], ids=["digest-without-zero", "digest-with-zero"])
def class_pinned_resource(wallet_env, monkeypatch, request):
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "2000")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    from core.wallet.x402 import _request_digest

    with _deterministic_digest_resource(wallet_env["rpc"], digest_contains_zero=request.param) as server:
        assert ("0" in _request_digest("GET", server.url)) == request.param
        yield server


def test_approved_payment_round_trips_the_original_digest_through_persistence(class_pinned_resource):
    from core.wallet import approval, custody, lifecycle, receipts, x402
    from core.wallet.x402 import _request_digest

    expected = _request_digest("GET", class_pinned_resource.url)
    profile = _pocket(custody)
    parked = x402.fetch_paid_resource(class_pinned_resource.url, wallet_id=profile.wallet_id)
    receipt = lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=approval.PinApprover(PIN))
    delivered = x402.retry_paid_resource(parked.proposal_id)
    assert delivered.status == x402.OUTCOME_DELIVERED and b"PAID REPORT" in delivered.body
    assert len(class_pinned_resource.deliveries) == 1 and class_pinned_resource.deliveries[0]["signature"] == receipt.tx_signature
    binding = x402.binding_for_proposal(parked.proposal_id)
    assert binding["request_digest"] == expected, "the binding carries the digest of the request that was actually retried"
    bound = [r for r in receipts.list_receipts() if r.get("x402")]
    assert bound, "the delivered payment wrote its receipt"
    assert bound[0]["x402"]["request_digest"] == expected and bound[0]["tx_signature"] == receipt.tx_signature
    assert bound[0]["x402"]["resource_digest"] == binding["resource_digest"]


def test_approved_payment_round_trips_the_digest_across_a_registry_restart(class_pinned_resource):
    """The public-identifier registry is process-local, but receipts are durable: after a
    restart (registries emptied between binding and delivery) the binding store re-vouches its
    stored request digest on load, delivery re-vouches the fresh resource digest, and the
    receipt store re-vouches on readback -- so persistence and later re-redaction of the
    stored payload never corrupt a digest the wallet minted."""
    from core.secret_redaction import clear_exact_secrets_for_tests
    from core.wallet import approval, custody, lifecycle, receipts, x402
    from core.wallet.redaction import redact_wallet_record
    from core.wallet.x402 import _request_digest

    expected = _request_digest("GET", class_pinned_resource.url)
    profile = _pocket(custody)
    parked = x402.fetch_paid_resource(class_pinned_resource.url, wallet_id=profile.wallet_id)
    clear_exact_secrets_for_tests()  # the restart: nothing minted survives in RAM
    receipt = lifecycle.default_lifecycle().approve_and_execute(parked.proposal_id, approver=approval.PinApprover(PIN))
    delivered = x402.retry_paid_resource(parked.proposal_id)
    assert delivered.status == x402.OUTCOME_DELIVERED and b"PAID REPORT" in delivered.body
    binding = x402.binding_for_proposal(parked.proposal_id)
    assert binding["request_digest"] == expected
    stored = next(r for r in receipts.list_receipts() if r.get("x402"))
    assert stored["x402"]["request_digest"] == expected and stored["x402"]["resource_digest"] == binding["resource_digest"]
    clear_exact_secrets_for_tests()  # readback alone must re-vouch: a status surface re-redacts stored payloads
    payload = receipts.last_receipt()
    reredacted = redact_wallet_record(payload)
    assert reredacted["x402"]["request_digest"] == expected and reredacted["x402"]["resource_digest"] == binding["resource_digest"]


def test_wallet_record_redaction_keeps_x402_digests_and_still_masks_secret_shaped_values():
    """The receipt-persistence contract at the redaction boundary, pinned without a socket. A
    digest-named field only preserves what the wallet's x402 producer actually minted and
    vouched for; the canonical redactor stays the authority -- registered exact secrets win
    over every exemption (review finding on the first PR45 head: a hex-shaped registered
    secret passed the shape check and was copied untouched), and unvouched values keep
    canonical treatment."""
    from core.secret_redaction import clear_exact_secrets_for_tests, register_exact_secret, register_public_identifier
    from core.wallet.redaction import redact_wallet_record
    from core.wallet.x402 import _request_digest

    clear_exact_secrets_for_tests()
    minted = _request_digest("GET", "http://127.0.0.1:20004/paid/report")  # mint+vouch through the real producer
    other_minted = _request_digest("GET", "http://127.0.0.1:20005/paid/report")
    for digest in (minted, other_minted):
        for key in ("request_digest", "resource_digest"):
            clean = redact_wallet_record({"x402": {key: digest, "resource_status": 200}})
            assert clean["x402"][key] == digest
    zeroed = "0abb7e26ee58bf2fdf62ec5905f11b86088955e77b74e1184e8533ded219e6cb"  # valid digest shape containing '0'
    clean = redact_wallet_record({"x402": {"request_digest": zeroed, "resource_digest": zeroed}})
    assert clean["x402"]["request_digest"] == zeroed and clean["x402"]["resource_digest"] == zeroed

    # a REGISTERED EXACT SECRET in the producer's hex spelling is masked under digest names,
    # at any nesting depth -- and exact-secret precedence holds even if the value is also
    # registered public (hex shape establishes spelling, not trusted origin).
    synthetic = "a1b2c3d4" * 8
    register_exact_secret(synthetic)
    register_public_identifier(synthetic)
    payload = {
        "x402": {"request_digest": synthetic, "resource_digest": synthetic, "url": "http://127.0.0.1:20004/paid/report"},
        "nested": {"journal": {"request_digest": synthetic, "note": f"key {synthetic} end"}},
        "pin": "246810",
    }
    clean = redact_wallet_record(payload)
    assert clean["x402"]["request_digest"] == "[redacted-credential]"
    assert clean["x402"]["resource_digest"] == "[redacted-credential]"
    assert clean["nested"]["journal"]["request_digest"] == "[redacted-credential]"
    assert synthetic not in clean["nested"]["journal"]["note"] and "pin" not in clean

    # an unvouched hex-shaped string (its preimage never passed through the producer, so nothing
    # registered it) keeps the generic masker's verdict: the no-'0' spelling is masked
    import hashlib

    unvouched = hashlib.sha256(b"GET|http://never-minted.invalid/report/58").hexdigest()
    assert "0" not in unvouched
    assert redact_wallet_record({"request_digest": unvouched})["request_digest"] == "[redacted-key]"
    secret = "4kWX9jmNvCnLzPQsThRbYfMdJgKUeHwZxVcBqNaSrTfDgLmEopWyuXABCDEFGHJKLMNPQRSTUV"
    clean = redact_wallet_record({"x402": {"request_digest": secret, "resource_digest": secret}})
    assert clean["x402"]["request_digest"] == "[redacted-key]" and clean["x402"]["resource_digest"] == "[redacted-key]"
    clear_exact_secrets_for_tests()
