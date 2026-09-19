"""Correction 1: the UsePod doors through a real daemon (SIMULATED CHAIN, real daemon, no browser, no model).

A requirement becomes one operation through `POST /api/wallet/usepod/topup`; the quote and approval doors pay it once
with the owner's PIN; `GET /api/wallet/usepod/status` shows the operation, the transfer and its fee evidence label; a
replay returns the original operation; a requirement whose window is already closed is refused typed at the door and
writes nothing.
"""
from __future__ import annotations

import json
import time
import uuid
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

pytestmark = [pytest.mark.safety, pytest.mark.served]

BASE_SEPOLIA = "eip155:84532"
PIN = "482913"
PAY_TO = "0x" + "7" * 40


def _door(daemon, method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    data = json.dumps(body).encode() if body is not None else None
    request = Request(daemon.base_url + path, data=data, method=method, headers={"Origin": daemon.base_url, "Content-Type": "application/json"})
    try:
        with urlopen(request, timeout=120) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


@pytest.fixture(scope="module")
def served(tmp_path_factory):
    from tests._blackbox_served_rig import ServedDaemon

    home = tmp_path_factory.mktemp("usepod") / "home"
    with ScriptedEvmNativeChain(chain_id=84532, fee_model="op_stack", l1_fee=40_000_000_000_000, operator_fee=1_000) as node:
        daemon = ServedDaemon(home, env_extra={
            "VOOL_WALLET_ENABLED": "1", "VOOL_WALLET_RPC_URLS": json.dumps({BASE_SEPOLIA: node.url}), "VOOL_WALLET_X402_ALLOW_LOOPBACK": "1",
        })
        try:
            try:
                daemon.start(timeout=240)
            except Exception as exc:  # pragma: no cover - environment
                pytest.skip(f"served daemon could not boot here: {exc}")
            yield daemon, node
        finally:
            daemon.stop()


def _ready_pilot_wallet(daemon) -> dict:
    status, created = _door(daemon, "POST", "/api/wallet/setup/create", {"network": BASE_SEPOLIA, "method": "pin", "credential": PIN, "credential_confirmation": PIN, "creation_key": f"usepod-{uuid.uuid4().hex}", "label": "usepod served"})
    assert status == 200, created
    wallet_id = created["setup"]["wallet_id"]
    status, revealed = _door(daemon, "POST", "/api/wallet/setup/reveal", {"wallet_id": wallet_id, "credential": PIN})
    assert status == 200 and (revealed.get("backup") or {}).get("ack_token"), (status, sorted(revealed.get("backup") or {}))
    ack_token = revealed["backup"]["ack_token"]
    revealed = None
    status, ready = _door(daemon, "POST", "/api/wallet/setup/acknowledge", {"wallet_id": wallet_id, "ack_token": ack_token})
    assert status == 200 and ready["setup"]["setup_state"] == "ready", ready
    return ready["setup"]


def _requirement(**over) -> dict:
    body = {"correlation_id": f"served-{uuid.uuid4().hex[:12]}", "network": BASE_SEPOLIA, "asset": "ETH", "pay_to": PAY_TO, "amount": "0.001",
            "expires_at": time.time() + 600, "resource": "https://usepod.example/v1/credits"}
    body.update(over)
    return body


def test_the_usepod_doors_pay_one_operation_once_and_report_its_fee_evidence(served):
    daemon, node = served
    status, switched = _door(daemon, "POST", "/api/wallet/environment", {"environment": "testnet"})
    assert status == 200, switched
    wallet = _ready_pilot_wallet(daemon)
    node.fund(wallet["address"], 10**18)
    body = _requirement()
    status, topup = _door(daemon, "POST", "/api/wallet/usepod/topup", body)
    assert status == 200 and topup["topup"]["state"] == "pending_approval" and topup["topup"]["duplicate"] is False, topup
    proposal_id = topup["topup"]["proposal_id"]
    assert (topup["topup"]["operation"]["state"], topup["topup"]["operation"]["paid"], topup["topup"]["operation"]["proof_eligible"]) == ("proposed", False, False)
    status, quoted = _door(daemon, "POST", "/api/wallet/quote", {"proposal_id": proposal_id})
    assert status == 200, quoted
    quote = quoted["quote"]
    assert (quote["fields"]["to_address"], quote["fields"]["amount_human"]) == (PAY_TO, "0.001")
    status, wrong = _door(daemon, "POST", "/api/wallet/approve", {"proposal_id": proposal_id, "quote_id": quote["quote_id"], "quote_digest": quote["digest"], "pin": "000000"})
    assert status != 200 and wrong.get("error") == "wallet_pin_invalid" and len(node.sent) == 0
    status, answer = _door(daemon, "POST", "/api/wallet/approve", {"proposal_id": proposal_id, "quote_id": quote["quote_id"], "quote_digest": quote["digest"], "pin": PIN})
    assert status == 200 and answer["transfer"]["state"] == "confirmed", answer
    assert len(node.sent) == 1
    receipt = next(iter(node.receipts.values()))
    exact = int(receipt["gasUsed"], 16) * int(receipt["effectiveGasPrice"], 16) + node.l1_fee + node.operator_fee
    transfer = answer["transfer"]
    assert (transfer["fee_state"], transfer["charged_fee_minor"], transfer["fee_missing"]) == ("exact", str(exact), [])
    assert transfer["charged_fee_label"].startswith(transfer["charged_fee_human"]) and "at most" not in transfer["charged_fee_label"]
    status, record = _door(daemon, "GET", f"/api/wallet/usepod/status?correlation_id={body['correlation_id']}")
    assert status == 200 and record["topup"]["operation"]["state"] == "paid" and record["topup"]["transfer"]["tx_id"] == transfer["tx_id"], record
    assert (record["topup"]["operation"]["paid"], record["topup"]["operation"]["proof_eligible"], record["topup"]["operation"]["transfer_state"]) == (True, True, "confirmed")
    # the same requirement again: the original operation, nothing new
    status, again = _door(daemon, "POST", "/api/wallet/usepod/topup", body)
    assert status == 200 and again["topup"]["duplicate"] is True and again["topup"]["proposal_id"] == proposal_id and len(node.sent) == 1
    # a requirement whose window is already closed: refused typed at the door, nothing written
    status, closed = _door(daemon, "POST", "/api/wallet/usepod/topup", _requirement(expires_at=time.time() - 1))
    assert status != 200 and closed.get("error") == "wallet_quote_expired", closed
    status, listed = _door(daemon, "GET", "/api/wallet/transfers?limit=10")
    assert status == 200 and [t["proposal_id"] for t in listed["transfers"]] == [proposal_id]
    status, malformed = _door(daemon, "POST", "/api/wallet/usepod/topup", _requirement(expires_at="NaN"))
    assert (status, malformed.get("error"), malformed.get("reason")) == (400, "usepod_requirement_invalid", "expires_at_not_finite")
