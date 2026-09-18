"""Crypto Pilot, stage 5: the UsePod top-up boundary, crypto side (delivery/USEPOD-BOUNDARY.md).

SIMULATED CHAIN. One exact requirement from the UsePod owner becomes ONE pilot proposal under its correlation id; the
same requirement again is the same proposal (never a second liability); other content under the same id is refused;
the sheet's quote, the trusted approval, the single send and the settlement are the lane's own; the record for the
id carries the transfer's identity and explorer link. What the sibling tasks own is refused typed, writing nothing.
"""
from __future__ import annotations

import json
import time
import uuid

import pytest
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

from core.wallet.errors import WalletFault

pytestmark = [pytest.mark.safety]

BASE_SEPOLIA = "eip155:84532"
BASE_MAINNET = "eip155:8453"
SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
PIN = "482913"
PAY_TO = "0x" + "7" * 40
SAME_ORIGIN = {"Host": "127.0.0.1:11435", "Origin": "http://127.0.0.1:11435", "Content-Type": "application/json"}


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_RPC_URLS", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_UI_CAPABILITY_SHA256", "VOOL_ALLOWED_HOSTS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, environment, lifecycle
    from core.web.api import wallet_api

    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 1.0)
    wallet_api.reset_caller_binding_for_tests()
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    environment.set_active_environment("testnet")
    yield monkeypatch
    chains.invalidate_chain_identity()
    store_module.reset_default_store()
    wallet_api.reset_caller_binding_for_tests()


@pytest.fixture
def base(home):
    with ScriptedEvmNativeChain(chain_id=84532, fee_model="op_stack", l1_fee=40_000_000_000_000) as node:
        home.setenv("VOOL_WALLET_RPC_URLS", json.dumps({BASE_SEPOLIA: node.url}))
        yield node


@pytest.fixture
def app(home):
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    return create_app(RuntimeServices(display_name="VOOL"))


def _pilot(network: str, label: str = "pocket") -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=network, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"u-{uuid.uuid4().hex}", label=label)
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    return pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])


def _requirement(**over) -> dict:
    body = {"correlation_id": f"usepod-{uuid.uuid4().hex[:12]}", "network": BASE_SEPOLIA, "asset": "ETH", "pay_to": PAY_TO, "amount": "0.001",
            "expires_at": time.time() + 600, "resource": "https://usepod.example/v1/credits"}
    body.update(over)
    return body


def _count() -> int:
    from core.wallet.store import connection

    with connection() as conn:
        return int(conn.execute("SELECT COUNT(*) FROM wallet_proposals").fetchone()[0])


def _door(app, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    from tests.asgi_harness import asgi_request

    status, _headers, raw = asgi_request(app, method=method, path=path, headers=SAME_ORIGIN, body=json.dumps(body).encode() if body is not None else b"")
    return status, json.loads(raw or b"{}")


def test_one_requirement_is_one_proposal_under_its_correlation_id_and_the_lane_settles_it(base):
    from core.wallet import approval, lifecycle, proposals, quotes, usepod

    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    body = _requirement()
    first = usepod.validate_topup(usepod.parse_requirement(body))
    assert (first["state"], first["duplicate"], first["network"], first["amount_minor"], first["amount_human"], first["pay_to"]) == ("pending_approval", False, BASE_SEPOLIA, str(10**15), "0.001", PAY_TO)
    proposal = proposals.get_proposal(first["proposal_id"])
    key = usepod.operation_key(usepod.provider_of(body["resource"]), body["correlation_id"])
    assert (proposal.origin, proposal.idempotency_key, proposal.wallet_id) == (usepod.ORIGIN_USEPOD, usepod.proposal_idempotency_key(key), wallet["wallet_id"])
    assert "usepod.example" in proposal.memo
    # the same requirement again: the same proposal, no second liability
    again = usepod.validate_topup(usepod.parse_requirement(body))
    assert (again["proposal_id"], again["duplicate"]) == (first["proposal_id"], True) and _count() == 1
    # other content under the same id is refused, typed, by the operation record itself (before any account is resolved)
    with pytest.raises(WalletFault) as changed:
        usepod.validate_topup(usepod.parse_requirement({**body, "amount": "0.002"}))
    assert (changed.value.code, changed.value.context.get("reason")) == ("wallet_duplicate_payment", "same_operation_different_content")
    assert _count() == 1
    # the record before approval
    before = usepod.status_for(body["correlation_id"])
    assert (before["proposal_id"], before["proposal_state"], before["transfer"]) == (first["proposal_id"], "pending_approval", None)
    # the lane's own quote, approval, one send, settlement
    quote = quotes.mint_quote(first["proposal_id"])
    result = lifecycle.default_lifecycle().approve_pilot_transfer(first["proposal_id"], quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))
    assert result["transfer"]["state"] == "confirmed" and len(base.sent) == 1
    after = usepod.status_for(body["correlation_id"])
    assert after["transfer"]["state"] == "confirmed" and after["transfer"]["tx_id"] == result["transfer"]["tx_id"]
    assert after["transfer"]["explorer_url"].startswith("https://sepolia.basescan.org/tx/") and "raw_b64" not in json.dumps(after)
    assert usepod.status_for("never-proposed") is None


@pytest.mark.parametrize(("over", "code", "reason"), [
    ({"asset": "USDC"}, "wallet_network_disabled", "usepod_asset_not_native"),
    ({"network": BASE_MAINNET}, "wallet_environment_inactive", "row_outside_active_environment"),
    ({"pay_to": "2fgqQMyTitjLceky7ZTFwQDKCFjitKAc5A4EpVpk54FQ"}, "wallet_recipient_refused", "recipient_shape_mismatch"),
    ({"expires_at": time.time() - 1}, "wallet_quote_expired", "usepod_requirement_expired"),
    ({"amount": "0.0000000000000000001"}, "wallet_amount_invalid", "amount_precision_exceeds_asset"),
    ({"amount": "9300000000"}, "wallet_amount_invalid", "amount_exceeds_pilot_storage_ceiling"),
    ({"network": SOLANA_DEVNET, "asset": "SOL", "pay_to": "2fgqQMyTitjLceky7ZTFwQDKCFjitKAc5A4EpVpk54FQ", "amount": "0.5"}, "wallet_not_found", "no_signing_account_for_request"),
])
def test_what_the_lane_does_not_carry_is_refused_typed_and_writes_nothing(base, over, code, reason):
    from core.wallet import usepod

    _pilot(BASE_SEPOLIA)
    before = _count()
    with pytest.raises(WalletFault) as refused:
        usepod.validate_topup(usepod.parse_requirement(_requirement(**over)))
    assert (refused.value.code, refused.value.context.get("reason")) == (code, reason), refused.value.context
    assert _count() == before


def test_a_row_that_is_not_ready_is_refused_before_any_write(base):
    from core.wallet import capabilities, usepod

    _pilot(BASE_SEPOLIA)
    # the chain is up; readiness is the product's constant, narrowed here to another row
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(capabilities, "PILOT_TRANSFER_READY_ROWS", frozenset({SOLANA_DEVNET}))
        with pytest.raises(WalletFault) as refused:
            usepod.validate_topup(usepod.parse_requirement(_requirement()))
    assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_network_disabled", "usepod_row_not_ready")
    assert _count() == 0


@pytest.mark.parametrize(("over", "reason"), [
    ({"correlation_id": ""}, "correlation_id_required"),
    ({"correlation_id": "has space"}, "correlation_id_required"),
    ({"network": ""}, "network_required"),
    ({"asset": ""}, "asset_required"),
    ({"pay_to": ""}, "pay_to_required"),
    ({"amount": None}, "amount_required"),
    ({"amount": 0.001}, "amount_must_not_be_a_float"),
    ({"expires_at": "soon"}, "expires_at_not_a_number"),
])
def test_a_malformed_requirement_is_a_shape_error_not_a_money_decision(home, over, reason):
    from core.wallet import usepod

    with pytest.raises(usepod.RequirementError) as bad:
        usepod.parse_requirement(_requirement(**over))
    assert bad.value.reason == reason


def test_the_doors_carry_the_requirement_and_its_record(base, app):
    wallet = _pilot(BASE_SEPOLIA)
    base.fund(wallet["address"], 10**18)
    body = _requirement()
    status, answer = _door(app, "POST", "/api/wallet/usepod/topup", body)
    assert status == 200 and answer["topup"]["state"] == "pending_approval", answer
    status, again = _door(app, "POST", "/api/wallet/usepod/topup", body)
    assert status == 200 and again["topup"]["duplicate"] is True and again["topup"]["proposal_id"] == answer["topup"]["proposal_id"]
    status, record = _door(app, "GET", f"/api/wallet/usepod/status?correlation_id={body['correlation_id']}")
    assert status == 200 and record["topup"]["proposal_id"] == answer["topup"]["proposal_id"]
    status, malformed = _door(app, "POST", "/api/wallet/usepod/topup", {**body, "correlation_id": ""})
    assert (status, malformed.get("error"), malformed.get("reason")) == (400, "usepod_requirement_invalid", "correlation_id_required")
    status, token = _door(app, "POST", "/api/wallet/usepod/topup", _requirement(asset="USDC"))
    assert status != 200 and token.get("error") == "wallet_network_disabled"
    status, _missing = _door(app, "GET", "/api/wallet/usepod/status?correlation_id=nope")
    assert status == 404
    assert _count() == 1

