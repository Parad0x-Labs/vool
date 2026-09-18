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
