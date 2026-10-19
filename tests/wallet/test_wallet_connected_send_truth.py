"""A connected or watch-only account on a mainnet Crypto Pilot row is told the truth, before any credential.

Only a Crypto Pilot wallet sends on these rows. A connected (external signer) or watch-only account can
receive and show its balance; it cannot send from VOOL. The refusal must say exactly that, not "check the
balance and destination" (a simulation failure it never had) nor "connect a signer" (connecting changes
nothing here), and it must come before any approval prompt, hold or chain write.

Public-shaped fixture addresses, local RPC doubles, no key material, no signature, no submission.
"""
from __future__ import annotations

import json

import pytest

from core.vool_wallet import b58encode
from core.wallet import approval, caller_binding, chains, custody, limits, proposals, quotes
from core.wallet.errors import WalletFault
from core.web.api.wallet_api import handle_wallet_post
from tests.wallet._rig import MAINNET_GENESIS, ScriptedRpc
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

EVM_MAINNET = "eip155:1"
EVM_SENDER = "0x" + "c" * 40
EVM_DESTINATION = "0x" + "e" * 40
HEADERS = {"host": "127.0.0.1:8731", "origin": "http://127.0.0.1:8731", "content-type": "application/json"}
MISLEADING = ("balance and destination", "connect a signer", "Connect a signer")


class _CountingApprover:
    def __init__(self, inner) -> None:
        self.inner, self.asked = inner, 0

    def approve(self, challenge):
        self.asked += 1
        return self.inner.approve(challenge)


def _post(path: str, body: dict) -> tuple[int, dict]:
    response = handle_wallet_post(path, body, client_host="127.0.0.1", headers=HEADERS)
    return response.status, json.loads(response.body)


@pytest.fixture
def mainnet(monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")  # the loopback RPC doubles are approved endpoints
    monkeypatch.delenv("VOOL_WALLET_NETWORK_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VOOL_WALLET_UI_CAPABILITY_SHA256", raising=False)
    caller_binding.reset_for_tests()
    chains.invalidate_chain_identity()
    with ScriptedEvmNativeChain(chain_id=1) as evm, ScriptedRpc(genesis_hash=MAINNET_GENESIS) as sol:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({EVM_MAINNET: evm.url, chains.SOLANA_MAINNET: sol.url}))
        yield evm, sol
        assert evm.sent == [] and sol.send_count() == 0, "nothing may ever be submitted, even to the local RPC"
    chains.invalidate_chain_identity()


@pytest.mark.parametrize("register", [custody.register_external_signer_wallet, custody.create_watch_only_wallet], ids=["connected", "watch_only"])
def test_a_native_evm_send_from_a_non_pilot_account_is_refused_truthfully_before_any_chain_read(mainnet, register):
    evm, _sol = mainnet
    account = register(EVM_SENDER, network=EVM_MAINNET)
    evm.balances[EVM_SENDER] = 10**18
    evm.calls.clear()
    status, refused = _post("/api/wallet/propose", {"wallet_id": account.wallet_id, "network": EVM_MAINNET, "destination": EVM_DESTINATION,
                                                    "amount_minor": 10**12, "asset": "ETH", "origin": "user"})
    assert status >= 400 and refused["error"] == "wallet_send_needs_vool_wallet", refused
    assert refused["fault"]["context"]["reason"] == "pilot_send_needs_vool_wallet", refused
    assert "only a VOOL Wallet created on this device sends here" in refused["message"]
    assert not any(text in refused["message"] for text in MISLEADING), refused["message"]
    assert evm.calls == [], "the refusal needs no chain read"
    proposal_id = refused["fault"]["context"]["proposal_id"]
    ended = proposals.get_proposal(proposal_id)
    assert (ended.state, ended.tx_signature) == (proposals.STATE_FAILED, "")
    assert limits.reservation_state(proposal_id) == ""
    # Activity keeps the refusal as a not-sent receipt, never as a transfer
    status, listed = _post_get_transfers()
    assert status == 200
    row = next(t for t in listed["transfers"] if t["proposal_id"] == proposal_id)
    assert row["record_kind"] == "proposal_receipt" and row["state_label"] == "Not sent", row


def _post_get_transfers() -> tuple[int, dict]:
    from core.web.api.wallet_api import handle_wallet_get

    response = handle_wallet_get("/api/wallet/transfers", {}, client_host="127.0.0.1")
    return response.status, json.loads(response.body)


@pytest.mark.parametrize("register", [custody.register_external_signer_wallet, custody.create_watch_only_wallet], ids=["connected", "watch_only"])
def test_a_solana_mainnet_preview_from_a_non_pilot_account_asks_no_credential_and_its_approval_never_prompts(mainnet, register):
    _evm, sol = mainnet
    sender, destination = b58encode(bytes([61]) * 32), b58encode(bytes([62]) * 32)
    account = register(sender, network=chains.SOLANA_MAINNET)
    status, prepared = _post("/api/wallet/propose", {"wallet_id": account.wallet_id, "network": chains.SOLANA_MAINNET, "destination": destination,
                                                     "amount_minor": 1_000_000, "asset": "SOL", "origin": "user"})
    assert status == 200 and prepared["proposal"]["state"] == proposals.STATE_PENDING_APPROVAL, prepared
    proposal_id = prepared["proposal"]["proposal_id"]
    status, preview = _post("/api/wallet/quote", {"proposal_id": proposal_id})
    assert status == 200, preview
    blocked = preview["quote"]["fields"]["approval_unavailable"]
    assert blocked["code"] == "wallet_send_needs_vool_wallet"
    assert "only a VOOL Wallet created on this device sends here" in blocked["message"]
    assert not any(text in blocked["message"] for text in MISLEADING)
    # the page's chosen language reaches the refusal words (the vool_ui_locale cookie the picker writes)
    response = handle_wallet_post("/api/wallet/quote", {"proposal_id": proposal_id}, client_host="127.0.0.1", headers={**HEADERS, "cookie": "vool_ui_locale=lt"})
    assert response.status == 200
    assert json.loads(response.body)["quote"]["fields"]["approval_unavailable"]["message"].startswith("Ši paskyra negali siųsti iš VOOL šiame tinkle")
    status, preview = _post("/api/wallet/quote", {"proposal_id": proposal_id})
    assert status == 200 and preview["quote"]["fields"]["approval_unavailable"]["message"] == blocked["message"], "no cookie: English again"
    # the approval door refuses before the approver is asked (a device approver would raise Touch ID), typed
    from core.wallet import lifecycle

    approver = _CountingApprover(approval.PinApprover("482913"))
    with pytest.raises(WalletFault) as refused:
        lifecycle.default_lifecycle().approve_pilot_transfer(proposal_id, quote_id=preview["quote"]["quote_id"], quote_digest=preview["quote"]["digest"], approver=approver)
    assert (refused.value.code, refused.value.context.get("reason")) == ("wallet_send_needs_vool_wallet", "pilot_approval_needs_pilot_wallet")
    assert not any(text in refused.value.user_message for text in MISLEADING)
    assert approver.asked == 0 and proposals.approval_refusals(proposal_id) == 0
    after = proposals.get_proposal(proposal_id)
    assert (after.state, after.tx_signature) == (proposals.STATE_PENDING_APPROVAL, "")
    assert limits.reservation_state(proposal_id) == ""
    assert quotes.get_quote(preview["quote"]["quote_id"])["state"] == quotes.STATE_OPEN, "a refused custody consumes no quote"
    # the same through the real approve door with a PIN body: same truthful refusal, nothing asked
    status, door = _post("/api/wallet/approve", {"proposal_id": proposal_id, "quote_id": preview["quote"]["quote_id"], "quote_digest": preview["quote"]["digest"], "pin": "482913"})
    assert status >= 400 and door["error"] == "wallet_send_needs_vool_wallet", door
    assert "sendTransaction" not in [call.get("method") for call in sol.calls]
