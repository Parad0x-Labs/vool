"""An offer only pay-kit can pay is refused by name at every door, never turned into a plain transfer.

A canonical Solana x402 offer names the resource's fee payer: the resource settles a transaction pay-kit builds. The
ordinary lanes would propose (and on approval broadcast) a plain transfer of this wallet's own instead, so the normal
fetch door, the model's ``x402.propose`` tool and the API's ``x402/propose`` door refuse it before any proposal
exists: ``wallet_paykit_unavailable`` while the optional ``pay`` extra is absent, naming the dependency, and
``x402_scheme_unavailable`` at a door that cannot pay it when the extra is present. An MPP Solana charge at the fetch
door is refused by name the same way. VOOL's own v1 Solana offers (no fee payer) and EVM entries keep their lanes.

The extra is made absent here the way a missing package is (its import fails), whether or not it is installed: the
real availability check runs, nothing in the wallet is patched.
"""
from __future__ import annotations

import json
import sys

import pytest

from tests.asgi_harness import asgi_request
from tests.wallet._rig import x402_body
from tests.wallet._rig_paykit import DEVNET_CAIP2, MAINNET_CAIP2, ScriptedMppResource, ScriptedPayKitResource

pytestmark = [pytest.mark.safety]
PIN = "246810"
API_HEADERS = {"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"}


@pytest.fixture
def env(wallet_env, monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "2000")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    return wallet_env


@pytest.fixture
def without_extra(env, monkeypatch):
    from core.wallet import paykit_x402

    # a None entry makes the import fail as a missing package does
    monkeypatch.setitem(sys.modules, "solana_pay_kit.protocols.x402.client.exact.payment", None)
    assert paykit_x402.availability() == (False, "paykit_not_installed")
    return env


def _pocket():
    from core.wallet import custody

    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _nothing_proposed_or_sent(resource, rpc):
    from core.wallet import proposals

    assert proposals.list_proposals() == [], "no proposal exists, so nothing can be approved into a plain transfer"
    assert resource.paid_requests == [] and rpc.send_count() == 0


class _BodyOnlyV1(ScriptedPayKitResource):
    """A v1 resource as v1 is served: the offer in the 402's JSON body, no PAYMENT-REQUIRED header."""

    def __init__(self, rpc, **kwargs):
        super().__init__(rpc, wire_version=1, **kwargs)
        handler = self._server.RequestHandlerClass
        sent = handler._send

        def _send(handler_self, status, body, headers):
            sent(handler_self, status, body, {k: v for k, v in headers.items() if k != "PAYMENT-REQUIRED"})

        handler._send = _send


class _FeePayerKeyV1(_BodyOnlyV1):
    """The same v1 offer naming its fee payer the other way the v1 parser reads it: a top-level ``feePayerKey``."""

    def challenge(self):
        doc = super().challenge()
        for offer in doc["accepts"]:
            offer["feePayerKey"] = offer["extra"].pop("feePayer")
        return doc


def _canonical_v1(network: str) -> dict:
    from solders.keypair import Keypair

    body = x402_body(amount_minor=1500, network=network)
    body["accepts"][0]["extra"] = {"feePayer": str(Keypair().pubkey())}
    return body


def _canonical_v2(network: str) -> dict:
    from solders.keypair import Keypair

    from tests.wallet._rig import OTHER_DESTINATION

    return {"x402Version": 2, "resource": {"url": "https://api.example.test/paid"}, "accepts": [{
        "scheme": "exact", "network": network, "amount": "1500", "asset": "SOL", "payTo": OTHER_DESTINATION,
        "maxTimeoutSeconds": 60, "extra": {"feePayer": str(Keypair().pubkey())}}]}


# --- the normal fetch door ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("wire", "network"), [
    ("v1-body-only", "solana-devnet"), ("v1-body-only", DEVNET_CAIP2), ("v1-body-only", "solana"),
    ("v2", DEVNET_CAIP2), ("v2", MAINNET_CAIP2), ("v1-fee-payer-key", "solana-devnet"),
])
def test_without_the_extra_a_canonical_offer_at_the_fetch_door_is_refused_by_name(without_extra, wire, network):
    from core.wallet import x402
    from core.wallet.errors import WalletFault

    profile = _pocket()
    rpc = without_extra["rpc"]
    resource_type = {"v1-body-only": _BodyOnlyV1, "v1-fee-payer-key": _FeePayerKeyV1}.get(wire, ScriptedPayKitResource)
    with resource_type(rpc, network=network) as resource:
        with pytest.raises(WalletFault) as exc:
            x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert exc.value.code == "wallet_paykit_unavailable" and exc.value.context["reason"] == "paykit_not_installed"
        assert len(resource.requests) == 1, "the 402 already received is the only request"
        _nothing_proposed_or_sent(resource, rpc)


def test_without_the_extra_an_mpp_solana_charge_at_the_fetch_door_is_refused_by_name(without_extra):
    from core.wallet import x402
    from core.wallet.errors import WalletFault

    profile = _pocket()
    rpc = without_extra["rpc"]
    with ScriptedMppResource(rpc) as resource:
        # pay-kit writes the challenge in the rig; without it, the same header by hand
        resource.www_authenticate = lambda: 'Payment id="ch-1", realm="scripted", method="solana", intent="charge", request="e30"'
        with pytest.raises(WalletFault) as exc:
            x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
        assert exc.value.code == "wallet_paykit_unavailable"
        _nothing_proposed_or_sent(resource, rpc)


def test_a_challenge_for_another_payment_method_is_still_an_ordinary_refusal(without_extra):
    from core.wallet import x402

    profile = _pocket()
    with ScriptedMppResource(without_extra["rpc"]) as resource:
        resource.www_authenticate = lambda: 'Payment id="ch-1", realm="scripted", method="stripe", intent="charge", request="e30"'
        outcome = x402.fetch_paid_resource(resource.url, wallet_id=profile.wallet_id)
    assert outcome.status == x402.OUTCOME_REFUSED and outcome.proposal_id == ""


# --- the model's x402.propose tool and the API door ------------------------------------------------------------------

@pytest.mark.parametrize("offer", ["v1", "v2"])
def test_without_the_extra_the_proposal_doors_refuse_a_canonical_offer_by_name(without_extra, offer):
    from apps.vool_api_server import create_app
    from core.runtime_execution_tools import _dispatch_runtime_tool
    from core.wallet import proposals
    from core.web.api.runtime import RuntimeServices

    _pocket()
    body = _canonical_v1("solana-devnet") if offer == "v1" else _canonical_v2(DEVNET_CAIP2)
    result = _dispatch_runtime_tool("x402.propose", {"status": 402, "body": body}, source_context={"session_id": "s1"})
    assert result.handled and not result.ok and result.status == "wallet_paykit_unavailable"
    app = create_app(RuntimeServices(display_name="VOOL"))
    _status, _headers, raw = asgi_request(app, method="POST", path="/api/wallet/x402/propose", headers=API_HEADERS,
                                         body=json.dumps({"status": 402, "headers": {}, "body": body}).encode())
    assert json.loads(raw)["error"] == "wallet_paykit_unavailable"
    assert proposals.list_proposals() == []


def test_with_the_extra_a_door_that_cannot_pay_a_canonical_offer_still_refuses_it(env):
    pytest.importorskip("solana_pay_kit", reason="the pay-kit lane is the optional `pay` extra")
    from core.runtime_execution_tools import _dispatch_runtime_tool
    from core.wallet import proposals

    _pocket()
    for body in (_canonical_v1("solana-devnet"), _canonical_v2(DEVNET_CAIP2)):
        result = _dispatch_runtime_tool("x402.propose", {"status": 402, "body": body}, source_context={"session_id": "s1"})
        assert result.handled and not result.ok and result.status == "x402_scheme_unavailable"
    assert proposals.list_proposals() == []


# --- the lanes that stay ---------------------------------------------------------------------------------------------

def test_vools_own_v1_solana_offer_without_a_fee_payer_keeps_its_lane(without_extra):
    from core.runtime_execution_tools import _dispatch_runtime_tool
    from core.wallet import proposals

    _pocket()
    result = _dispatch_runtime_tool("x402.propose", {"status": 402, "body": x402_body(amount_minor=1500)}, source_context={"session_id": "s1"})
    assert result.handled and result.ok
    assert [p.origin for p in proposals.list_proposals()] == [proposals.ORIGIN_X402]


def test_an_evm_entry_beside_a_solana_entry_is_still_selected(env):
    from core.wallet import x402_v2

    evm = {"scheme": "exact", "network": "eip155:84532", "amount": "1500", "asset": "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
           "payTo": "0x" + "2" * 40, "maxTimeoutSeconds": 60, "extra": {"name": "USDC", "version": "2", "assetTransferMethod": "eip3009"}}
    both = _canonical_v2(DEVNET_CAIP2)
    both["accepts"].append(evm)
    offer = x402_v2.parse_payment_required({}, json.dumps(both).encode(), status=402)
    picked, reason = x402_v2.select_offer(offer, wallet_id="wallet-any")
    assert picked is not None and picked.network == "eip155:84532" and reason.startswith("selected:eip155:84532")
    only_solana = x402_v2.parse_payment_required({}, json.dumps(_canonical_v2(DEVNET_CAIP2)).encode(), status=402)
    assert x402_v2.select_offer(only_solana, wallet_id="wallet-any") == (None, x402_v2.REFUSED_SOLANA_NEEDS_PAYKIT)
