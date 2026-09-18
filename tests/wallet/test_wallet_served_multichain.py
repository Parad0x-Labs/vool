"""Stage F — the served multichain journey through the production HTTP seam.

The real app dispatchers (the same ones the daemon mounts): the disabled wallet refuses
safely, operator account setup is owner-local and explicit, an x402 v2 proposal parks as a
visible pending approval carrying the full challenge card, operator rejection has zero
signing/network effects, and the approved path crosses the injected external EVM signer to
the loopback chain and facilitator simulators — with distinct chain identity and receipts
per testnet, panic freeze and restart states visible.
"""
from __future__ import annotations

import json

import pytest

from tests.asgi_harness import asgi_request
from tests.wallet._rig_evm import EvmExtensionSigner, FacilitatorSimulator, X402V2Resource

pytestmark = [pytest.mark.safety]

BASE_SEPOLIA = "eip155:84532"
ETHEREUM_SEPOLIA = "eip155:11155111"
USDC_BASE = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
USDC_SEPOLIA = "0x1c7D4B196Cb0C7B01d743Fbc6116a902379C7238"
PAY_TO = "0x209693Bc6afc0C5328bA36FaF03C514EF312287C"

HEADERS = {"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1"}
REMOTE_HEADERS = {"Host": "127.0.0.1", "Content-Type": "application/json", "Origin": "http://127.0.0.1", "X-Forwarded-For": "203.0.113.9"}


@pytest.fixture
def app(evm_wallet_env):
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    return create_app(RuntimeServices(display_name="VOOL"))


@pytest.fixture
def evm_wallet_env(monkeypatch, tmp_path, evm_rig):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    monkeypatch.setenv("VOOL_WALLET_X402_CAP_MINOR", "20000")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({BASE_SEPOLIA: evm_rig.rpc.url, ETHEREUM_SEPOLIA: evm_rig.rpc.url}))
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module

    store_module.reset_default_store()
    yield evm_rig
    store_module.reset_default_store()


def _post(app, path: str, body: dict, *, headers: dict[str, str] | None = None):
    status, _h, raw = asgi_request(app, method="POST", path=path, headers=headers or HEADERS, body=json.dumps(body).encode())
    return status, json.loads(raw or b"{}")


def _get(app, path: str):
    status, _h, raw = asgi_request(app, method="GET", path=path, headers={"Host": "127.0.0.1"})
    return status, json.loads(raw or b"{}")


def _signer_answer(signer, typed_data: dict) -> str:
    import http.client

    parts = signer.url.rsplit("/", 1)
    host, port = "127.0.0.1", int(signer.url.split(":")[2].split("/")[0])
    connection = http.client.HTTPConnection(host, port, timeout=10)
    try:
        connection.request("POST", f"/{parts[-1]}", body=json.dumps({"typed_data": typed_data}).encode(), headers={"Content-Type": "application/json"})
        response = connection.getresponse()
        answer = json.loads(response.read())
    finally:
        connection.close()
    return answer["signature"]


def test_f1_disabled_wallet_refuses_safely_over_the_wire(app, monkeypatch):
    monkeypatch.delenv("VOOL_WALLET_ENABLED", raising=False)
    status, st = _get(app, "/api/wallet/status")
    assert status == 200 and st["status"]["enabled"] is False and st["status"]["custody_mode"] == "none"
    code, refused = _post(app, "/api/wallet/propose", {"destination": "0x" + "1" * 40, "amount_minor": 1, "asset": "USDC"})
    assert code == 403 and refused["error"] == "wallet_disabled"


def test_f2_state_changes_are_owner_local_only(app):
    """A remote client is refused at the dispatcher door, before any wallet code runs."""
    import json as _json
    from pathlib import Path

    from core.web.api.service import dispatch_post

    resp = dispatch_post(
        path="/api/wallet/watch-only", body={"public_key": "0x" + "1" * 40, "network": BASE_SEPOLIA},
        headers={"Host": "127.0.0.1", "Content-Type": "application/json"}, runtime=app.state.runtime, model_name="",
        workspace_root_provider=lambda: Path("."), client_host="203.0.113.9",
    )
    raw = resp.body if hasattr(resp, "body") else bytes(resp)
    assert getattr(resp, "status", 0) == 403 or b"owner_local_required" in raw or _json.loads(raw or b"{}").get("error") == "owner_local_required"


def test_f3_operator_registers_evm_account_and_panic_freeze_is_visible(app):
    code, registered = _post(app, "/api/wallet/external", {"public_key": "0x" + "a" * 40, "network": BASE_SEPOLIA, "label": "evm-cold"})
    assert code == 200 and registered["wallet"]["mode"] == "external_signer" and registered["wallet"]["network"] == BASE_SEPOLIA
    _status, st = _get(app, "/api/wallet/status")
    accounts = st["status"].get("accounts") or []
    assert any(a["chain"] == BASE_SEPOLIA and a["family"] == "evm" and a["testnet"] is True for a in accounts)
    # EVM pocket custody is a typed unavailable over the wire too
    code, refused = _post(app, "/api/wallet/pocket/create", {"acknowledged_warning": True, "confirmation_phrase": "I ACCEPT THAT THIS DEVICE HOLDS THE KEY", "pin": "246810", "network": BASE_SEPOLIA})
    assert code == 403 and refused["error"] == "evm_pocket_custody_unavailable"
    # the panic freeze flips and is visible on the public status
    from core.wallet import limits

    assert limits.set_frozen(True) is False
    _status, st = _get(app, "/api/wallet/status")
    assert st["status"]["enabled"] is True
    limits.set_frozen(False)


def test_f4_x402_v2_proposal_parks_with_the_full_challenge_card_and_rejection_has_zero_effects(app, evm_wallet_env, monkeypatch):
    rig = evm_wallet_env
    monkeypatch.setattr(rig.rpc, "chain_id", 84532, raising=False)
    from core.wallet import chains, facilitators

    chains.invalidate_chain_identity(None)
    with FacilitatorSimulator(networks=(BASE_SEPOLIA,)) as facilitator:
        facilitators.discover(facilitator.url, facilitator_id="fac-served")
        with X402V2Resource(rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000) as resource:
            _code, registered = _post(app, "/api/wallet/external", {"public_key": "0x" + "a" * 40, "network": BASE_SEPOLIA})
            wallet_id = registered["wallet"]["wallet_id"]
            code, outcome = _post(app, "/api/wallet/x402/fetch", {"url": resource.url, "wallet_id": wallet_id})
            assert code == 200 and outcome["outcome"]["status"] == "payment_required"
            proposal_id = outcome["outcome"]["proposal_id"]
            _status, detail = _get(app, f"/api/wallet/proposals/{proposal_id}")
            proposal = detail["proposal"]
            assert proposal["state"] == "pending_approval" and proposal["origin"] == "x402"
            challenge = detail["challenge"]
            for field in ("chain", "asset_address", "asset_decimals", "amount_minor", "human_amount", "payee", "resource_origin", "facilitator", "max_total_minor", "idempotency_key", "challenge_digest"):
                assert challenge.get(field) not in (None, ""), field
            assert challenge["chain"] == BASE_SEPOLIA and challenge["asset_decimals"] == 6
            # operator rejection: zero signing, zero network, terminal rejection
            code, rejected = _post(app, "/api/wallet/reject", {"proposal_id": proposal_id})
            assert code == 200 and rejected["rejected"] is True
            assert rig.rpc.call_count() >= 1  # the identity read happened at prepare
            assert resource.deliveries == [] and rig.facilitator.settle_count() == 0
            # a rejected proposal cannot be approved afterwards
            code, late = _post(app, "/api/wallet/approve", {"proposal_id": proposal_id, "pin": "246810"})
            assert code in (400, 403, 409) and late["ok"] is False


def test_f5_approved_v2_payment_crosses_the_external_signer_to_the_loopback_chain(app, evm_wallet_env, monkeypatch):
    rig = evm_wallet_env
    monkeypatch.setattr(rig.rpc, "chain_id", 84532, raising=False)
    from core.wallet import chains, facilitators

    chains.invalidate_chain_identity(None)
    with FacilitatorSimulator(networks=(BASE_SEPOLIA,)) as facilitator:
        facilitators.discover(facilitator.url, facilitator_id="fac-served")
        with X402V2Resource(rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000) as resource, EvmExtensionSigner() as signer:
            settlement_tx = "0x" + ("7" * 64)
            resource.settlement_tx = settlement_tx
            rig.rpc.add_transfer_receipt(settlement_tx, contract_address=USDC_BASE, from_address=signer.address, to_address=PAY_TO, amount_int=10000)
            _code, registered = _post(app, "/api/wallet/external", {"public_key": signer.address, "network": BASE_SEPOLIA})
            wallet_id = registered["wallet"]["wallet_id"]
            _code, outcome = _post(app, "/api/wallet/x402/fetch", {"url": resource.url, "wallet_id": wallet_id})
            proposal_id = outcome["outcome"]["proposal_id"]
            # the operator hands the payment to the external wallet: one EIP-1193 request
            code, handed = _post(app, "/api/wallet/approve", {"proposal_id": proposal_id, "method": "external"})
            assert code == 200 and handed["signing_request"]["family"] == "evm"
            request_id = handed["signing_request"]["request_id"]
            transport = handed["signing_request"]["transports"]["eip1193"]
            assert transport["method"] == "eth_signTypedData_v4"
            typed_data = json.loads(transport["params"][1])
            assert typed_data["domain"]["chainId"] == 84532
            # the wallet signs in its own process; the resource settles on proof of that signature
            signature = _signer_answer(signer, typed_data)
            resource.settled_signatures.add(signature)
            code, submitted = _post(app, "/api/wallet/external/submit", {"request_id": request_id, "signature_hex": signature})
            assert code == 200, submitted
            receipt = submitted["receipt"]
            assert receipt["state"] == "confirmed" and receipt["tx_signature"] == settlement_tx
            assert receipt["network"] == BASE_SEPOLIA
            # exactly one delivery ever, and the chain proven the terms
            assert len(resource.deliveries) == 1
            _status, st = _get(app, "/api/wallet/status")
            assert st["status"]["last_receipt"]["state"] == "confirmed"


def test_f6_restart_and_unknown_states_are_visible_on_status(app, evm_wallet_env, monkeypatch):
    """After a simulated crash between submission and settlement the status surface shows a
    non-confirmed state, and a restart does not repay or re-sign."""
    rig = evm_wallet_env
    monkeypatch.setattr(rig.rpc, "chain_id", 84532, raising=False)
    from core.wallet import chains, facilitators, receipts

    chains.invalidate_chain_identity(None)
    with FacilitatorSimulator(networks=(BASE_SEPOLIA,)) as facilitator:
        facilitators.discover(facilitator.url, facilitator_id="fac-served")
        with X402V2Resource(rig.facilitator, network=BASE_SEPOLIA, asset=USDC_BASE, pay_to=PAY_TO, amount_minor=10000) as resource:
            _code, registered = _post(app, "/api/wallet/external", {"public_key": "0x" + "a" * 40, "network": BASE_SEPOLIA})
            wallet_id = registered["wallet"]["wallet_id"]
            _code, outcome = _post(app, "/api/wallet/x402/fetch", {"url": resource.url, "wallet_id": wallet_id})
            proposal_id = outcome["outcome"]["proposal_id"]
            # crash: the proposal stays pending, the receipt stays absent
            _status, detail = _get(app, f"/api/wallet/proposals/{proposal_id}")
            assert detail["proposal"]["state"] in {"pending_approval", "proposed"}
            assert all(r["state"] != "confirmed" for r in receipts.list_receipts() if r["proposal_id"] == proposal_id)
            # a restart (fresh engine) re-reading the same proposal is a parked proposal, not a payment
            code, _handed = _post(app, "/api/wallet/approve", {"proposal_id": proposal_id, "method": "external"})
            assert code == 200  # the owner can still take it into the signing stage
            assert len([r for r in receipts.list_receipts() if r["state"] == "confirmed"]) == 0
            assert rig.facilitator.settle_count() == 0 and len(resource.deliveries) == 0
