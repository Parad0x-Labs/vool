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


def test_wallet_record_redaction_keeps_x402_digests_and_still_masks_secret_shaped_values():
    """The receipt-persistence contract at the redaction boundary, pinned without a socket: every
    digest the wallet's x402 producer mints must survive redaction byte-for-byte -- including the
    exact digest a CI run stored as '[redacted-key]' -- while secret-shaped values keep being
    masked, even when they ride under digest-named fields."""
    from core.wallet.redaction import redact_wallet_record

    digests = [
        "d2ff21956d1eb476a5d5e3b28d354c45a1bb473c2c447a68dd6e3b5c2fc1d3a1",  # the run-35923574353 offender
        "e3f487dda88a2238b64e4cfc7177ad52af85831b576ed549dc7ea4ac1c95b54d",  # a different valid digest without '0'
        "0abb7e26ee58bf2fdf62ec5905f11b86088955e77b74e1184e8533ded219e6cb",  # a valid digest containing '0'
    ]
    for digest in digests:
        for key in ("request_digest", "resource_digest"):
            clean = redact_wallet_record({"x402": {key: digest, "resource_status": 200}})
            assert clean["x402"][key] == digest
    secret = "4kWX9jmNvCnLzPQsThRbYfMdJgKUeHwZxVcBqNaSrTfDgLmEopWyuXABCDEFGHJKLMNPQRSTUV"
    clean = redact_wallet_record({
        "x402": {"request_digest": secret, "resource_digest": secret, "url": "http://127.0.0.1:20004/paid/report"},
        "nested": {"leak": f"key {secret} end"},
        "pin": "246810",
    })
    assert clean["x402"]["request_digest"] == "[redacted-key]" and clean["x402"]["resource_digest"] == "[redacted-key]"
    assert secret not in clean["nested"]["leak"] and "pin" not in clean
