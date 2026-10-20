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


def _usdc_accounts(sol, owners) -> None:
    """The USDC mint and token accounts on the local Solana mainnet double, so a payment-channel row prepares as on the chain."""
    from solders.pubkey import Pubkey
    from solders.token.state import Mint, TokenAccount, TokenAccountState

    from core.wallet import svm_tokens

    usdc = chains.asset_for(chains.SOLANA_MAINNET, "USDC")
    sol.accounts[usdc.address] = {"owner": svm_tokens.TOKEN_PROGRAM, "data": bytes(Mint(mint_authority=None, supply=10**12, decimals=usdc.decimals, is_initialized=True, freeze_authority=None))}
    for owner, amount in owners:
        sol.accounts[svm_tokens.associated_account(owner, usdc.address)] = {"owner": svm_tokens.TOKEN_PROGRAM, "data": bytes(TokenAccount(
            mint=Pubkey.from_string(usdc.address), owner=Pubkey.from_string(owner), amount=amount, delegate=None, state=TokenAccountState.Initialized,
            is_native=None, delegated_amount=0, close_authority=None))}


@pytest.mark.parametrize("origin", [proposals.ORIGIN_USEPOD, proposals.ORIGIN_DNA_FEE])
def test_a_payment_channel_payment_from_a_connected_account_on_mainnet_gets_no_signing_request(mainnet, origin):
    """Receive and view only on mainnet covers every lane: a UsePod or fee payment in USDC from a connected account is refused
    at the external signing door before any hold or signing request, and the card says so instead of offering a signer."""
    from solders.keypair import Keypair

    from core.wallet import external_signing, lifecycle, status

    _evm, sol = mainnet
    sender, destination = str(Keypair().pubkey()), str(Keypair().pubkey())
    _usdc_accounts(sol, [(sender, 10**9), (destination, 0)])
    account = custody.register_external_signer_wallet(sender, network=chains.SOLANA_MAINNET)
    proposal = proposals.propose_transaction(wallet_id=account.wallet_id, destination=destination, amount_minor=1000, asset="USDC", origin=origin, network=chains.SOLANA_MAINNET)
    prepared = lifecycle.default_lifecycle().prepare(proposal.proposal_id)
    assert prepared.state == proposals.STATE_PENDING_APPROVAL
    status_code, refused = _post("/api/wallet/approve", {"proposal_id": proposal.proposal_id, "method": "external"})
    assert status_code >= 400 and refused["error"] == "wallet_send_needs_vool_wallet", refused
    assert refused["fault"]["context"]["reason"] == "connected_account_view_only_on_mainnet", refused
    assert "only a VOOL Wallet created on this device sends here" in refused["message"]
    assert external_signing.open_request_for_proposal(proposal.proposal_id) is None, "no bytes to sign left VOOL"
    assert limits.reservation_state(proposal.proposal_id) == "", "nothing was held"
    after = proposals.get_proposal(proposal.proposal_id)
    assert (after.state, after.tx_signature) == (proposals.STATE_PENDING_APPROVAL, ""), "refused, not ended: the owner can still reject it"
    row = next(r for r in status.wallet_status()["pending"] if r["proposal_id"] == proposal.proposal_id)
    assert row["mainnet"] is True and row["pilot_transfer"] is False
    assert row["send_unavailable"]["code"] == "wallet_send_needs_vool_wallet"
    assert "only a VOOL Wallet created on this device sends here" in row["send_unavailable"]["message"]
    # the reject door still works: refusing to send never strands the request
    status_code, rejected = _post("/api/wallet/reject", {"proposal_id": proposal.proposal_id})
    assert status_code == 200, rejected
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_REJECTED


def test_a_testnet_row_from_a_connected_account_keeps_its_signer(monkeypatch):
    """The view-only rule is a Mainnet rule: a connected account's test-network row still offers its own signer."""
    from core.wallet import status

    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
    chains.invalidate_chain_identity()
    account = custody.register_external_signer_wallet(b58encode(bytes([63]) * 32), network=chains.SOLANA_DEVNET)
    proposal = proposals.propose_transaction(wallet_id=account.wallet_id, destination=b58encode(bytes([64]) * 32), amount_minor=1000, asset="SOL", origin="user", network=chains.SOLANA_DEVNET)
    row = status._pending_row(proposal)
    assert (row["mainnet"], row["send_unavailable"]) == (False, None)


@pytest.mark.parametrize("door", ["pin", "external"])
@pytest.mark.parametrize("register", [custody.register_external_signer_wallet, custody.create_watch_only_wallet], ids=["connected", "watch_only"])
def test_every_legacy_door_refuses_a_view_only_account_on_mainnet_before_any_hold(mainnet, register, door):
    """The same refusal from the legacy PIN door (approve and execute) and the external signing door, for a connected and a
    watch-only account: nothing is held, no signing request opens, no credential is consumed."""
    from solders.keypair import Keypair

    from core.wallet import external_signing, lifecycle

    _evm, sol = mainnet
    sender, destination = str(Keypair().pubkey()), str(Keypair().pubkey())
    _usdc_accounts(sol, [(sender, 10**9), (destination, 0)])
    account = register(sender, network=chains.SOLANA_MAINNET)
    proposal = proposals.propose_transaction(wallet_id=account.wallet_id, destination=destination, amount_minor=1000, asset="USDC", origin=proposals.ORIGIN_USEPOD, network=chains.SOLANA_MAINNET)
    lifecycle.default_lifecycle().prepare(proposal.proposal_id)
    body = {"proposal_id": proposal.proposal_id, **({"method": "external"} if door == "external" else {"pin": "482913"})}
    status_code, refused = _post("/api/wallet/approve", body)
    assert status_code >= 400 and refused["error"] == "wallet_send_needs_vool_wallet", refused
    assert refused["fault"]["context"]["reason"] == "connected_account_view_only_on_mainnet", refused
    assert external_signing.open_request_for_proposal(proposal.proposal_id) is None
    assert limits.reservation_state(proposal.proposal_id) == ""
    assert proposals.get_proposal(proposal.proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert "sendTransaction" not in [call.get("method") for call in sol.calls]
