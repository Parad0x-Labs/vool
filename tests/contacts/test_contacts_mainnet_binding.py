"""Mainnet recipient binding with public-shaped fixtures, unsigned quotes and local RPC only.

No key material or signer is created. The watch-only account exercises the real contact,
proposal, quote and API owners. The RPC double models an EVM chain; no live-chain claim.
"""
from __future__ import annotations

import json

import pytest

from core.contacts import resolver, store
from core.wallet import caller_binding, chains, custody, proposals, quotes
from core.wallet.errors import WalletFault
from core.web.api.wallet_api import handle_wallet_post
from tests.contacts import protected_changes
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

NETWORK = "eip155:1"
SENDER = "0x" + "a" * 40
DESTINATION = "0x" + "b" * 40


@pytest.fixture
def mainnet(monkeypatch):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
    monkeypatch.delenv("VOOL_WALLET_NETWORK_ENVIRONMENT", raising=False)
    monkeypatch.delenv("VOOL_WALLET_UI_CAPABILITY_SHA256", raising=False)
    caller_binding.reset_for_tests()
    protected_changes.reset()
    chains.invalidate_chain_identity()
    with ScriptedEvmNativeChain(chain_id=1) as node:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({NETWORK: node.url}))
        wallet = custody.create_watch_only_wallet(SENDER, network=NETWORK)
        node.balances[SENDER] = 10**18
        yield wallet, node
        assert node.sent == [], "this proof must never submit a transaction, even to the local RPC"
    chains.invalidate_chain_identity()
    protected_changes.reset()


def _contact(name="Aster"):
    return store.create_contact(display_name=name, actor=store.ACTOR_OWNER,
                                endpoints=[{"kind": "wallet", "value": DESTINATION, "network": NETWORK}])


def _proposal(wallet, name="Aster"):
    resolved = resolver.resolve(name, kind="wallet", network=NETWORK)
    assert resolved.ok and resolved.snapshot
    return proposals.propose_transaction(wallet_id=wallet.wallet_id, destination=resolved.snapshot["value"],
        amount_minor=10**12, asset="ETH", origin=proposals.ORIGIN_USER, recipient=resolved.snapshot)


def _quote_door(proposal_id):
    response = handle_wallet_post("/api/wallet/quote", {"proposal_id": proposal_id}, client_host="127.0.0.1",
        headers={"host": "127.0.0.1:8731", "origin": "http://127.0.0.1:8731", "content-type": "application/json"})
    return response.status, json.loads(response.body)


def test_renamed_contact_invalidates_a_mainnet_quote_through_the_wallet_door(mainnet):
    wallet, node = mainnet
    contact = _contact()
    proposal = _proposal(wallet)
    status, preview = _quote_door(proposal.proposal_id)
    assert status == 200, preview
    assert preview["quote"]["fields"]["to_address"] == DESTINATION
    protected_changes.update_contact(contact["contact_id"], display_name="Birch")
    node.calls.clear()
    status, refused = _quote_door(proposal.proposal_id)
    assert status >= 400 and refused["fault"]["code"] == "wallet_recipient_refused", refused
    assert "contact_identity_changed" in json.dumps(refused), refused
    assert node.calls == [], "refuse the stale identity before reading chain state or asking approval"


def test_two_distinct_names_and_an_endpoint_relabel_keep_unsigned_mainnet_quotes(mainnet):
    wallet, node = mainnet
    contact = _contact("Linden")
    proposal = _proposal(wallet, "Linden")
    protected_changes.update_contact(contact["contact_id"],
        change_endpoints=[{"endpoint_id": contact["endpoints"][0]["endpoint_id"], "label": "savings"}])
    status, preview = _quote_door(proposal.proposal_id)
    assert status == 200, preview
    fields = preview["quote"]["fields"]
    assert fields["chain_identity"] == "1" and fields["environment"] == "mainnet"
    assert fields["to_address"] == DESTINATION and fields["amount_minor"] == 10**12
    assert fields["max_total_minor"] == fields["amount_minor"] + fields["fee_max_minor"]
    assert node.sent == []


@pytest.mark.parametrize("changed", [
    {"resolution": "future_resolution"}, {"contact_id": "ct-not-this-person"},
    {"display_name": "Another person"}, {"chain_network": "eip155:8453"},
    {"kind": "email"}, {"canonical": "another-destination"},
    {"value": "0x" + "c" * 40},
    {"resolution": {}}, {"resolution": []},
])
def test_unknown_or_mismatched_saved_bindings_fail_closed(mainnet, changed):
    _contact()
    snapshot = resolver.resolve("Aster", kind="wallet", network=NETWORK).snapshot
    assert snapshot
    check = resolver.verify_snapshot({**snapshot, **changed})
    assert not check.ok, check


def test_wallet_proposal_refuses_an_unknown_recipient_contract(mainnet):
    wallet, node = mainnet
    _contact()
    snapshot = resolver.resolve("Aster", kind="wallet", network=NETWORK).snapshot
    assert snapshot
    with pytest.raises(WalletFault) as refused:
        proposals.propose_transaction(wallet_id=wallet.wallet_id, destination=DESTINATION,
            amount_minor=10**12, asset="ETH", origin=proposals.ORIGIN_USER,
            recipient={**snapshot, "resolution": "future_resolution"})
    assert refused.value.code == "wallet_recipient_refused"
    assert node.calls == [] and proposals.list_proposals() == []


def test_proposal_refuses_a_forged_value_with_a_real_saved_fingerprint(mainnet):
    wallet, node = mainnet
    _contact()
    snapshot = resolver.resolve("Aster", kind="wallet", network=NETWORK).snapshot
    assert snapshot
    forged = "0x" + "c" * 40
    with pytest.raises(WalletFault) as refused:
        proposals.propose_transaction(wallet_id=wallet.wallet_id, destination=forged,
            amount_minor=10**12, asset="ETH", origin=proposals.ORIGIN_USER,
            recipient={**snapshot, "value": forged})
    assert refused.value.code == "wallet_recipient_refused"
    assert node.calls == [] and proposals.list_proposals() == []


def test_a_persisted_forged_value_cannot_get_a_new_mainnet_quote(mainnet):
    from core.wallet.store import connection

    wallet, node = mainnet
    _contact()
    proposal = _proposal(wallet)
    forged = "0x" + "c" * 40
    with connection() as conn:
        conn.execute("UPDATE wallet_proposals SET destination = ?, recipient_json = ? WHERE proposal_id = ?",
            (forged, json.dumps({**proposal.recipient, "value": forged}), proposal.proposal_id))
    with pytest.raises(WalletFault) as refused:
        quotes.mint_quote(proposal.proposal_id)
    assert refused.value.code == "wallet_recipient_refused" and node.calls == []


def test_a_persisted_unknown_recipient_contract_cannot_be_quoted(mainnet):
    from core.wallet.store import connection

    wallet, node = mainnet
    _contact()
    proposal = _proposal(wallet)
    # An older installation may already hold a record from a contract it could not
    # understand. The consuming quote door must refuse it as well as new admission.
    with connection() as conn:
        conn.execute("UPDATE wallet_proposals SET recipient_json = ? WHERE proposal_id = ?",
            (json.dumps({**proposal.recipient, "resolution": "future_resolution"}), proposal.proposal_id))
    status, refused = _quote_door(proposal.proposal_id)
    assert status >= 400 and refused["error"] == "wallet_recipient_refused", refused
    assert node.calls == []


def test_changed_address_still_invalidates_mainnet_quote(mainnet):
    wallet, node = mainnet
    contact = _contact()
    proposal = _proposal(wallet)
    protected_changes.update_contact(contact["contact_id"], change_endpoints=[
        {"endpoint_id": contact["endpoints"][0]["endpoint_id"], "value": "0x" + "c" * 40}])
    with pytest.raises(WalletFault) as refused:
        quotes.mint_quote(proposal.proposal_id)
    assert refused.value.code == "wallet_recipient_refused"
    assert node.sent == []


def test_crypto_off_refuses_quote_without_rpc(mainnet, monkeypatch):
    wallet, node = mainnet
    _contact()
    proposal = _proposal(wallet)
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "0")
    node.calls.clear()
    status, refused = _quote_door(proposal.proposal_id)
    assert status >= 400 and "wallet_disabled" in json.dumps(refused), refused
    assert node.calls == []


def test_mainnet_balance_does_not_claim_the_payment_tokens_cannot_be_sent(mainnet, monkeypatch):
    from core.web.api.wallet_api import handle_wallet_get

    with ScriptedEvmNativeChain(chain_id=8453, fee_model="op_stack") as node:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({"eip155:8453": node.url}))
        wallet = custody.create_watch_only_wallet(SENDER, network="eip155:8453")
        node.balances[SENDER] = 10**18
        response = handle_wallet_get("/api/wallet/balance", {"wallet_id": [wallet.wallet_id]}, client_host="127.0.0.1")
        balance = json.loads(response.body)["balance"]
        assert response.status == 200 and balance["state"] == "read"
        assert balance["balance_minor"] == str(10**18) and balance["asset"] == "ETH"
        assert "tokens are not included" in balance["note"]
        assert "are not sent" not in balance["note"] and node.sent == []


def test_wallet_screen_proposal_resolves_a_name_on_mainnet_and_binds_the_contact(mainnet, monkeypatch):
    from core.vool_wallet import b58encode
    from tests.wallet._rig import MAINNET_GENESIS, ScriptedRpc

    sender, destination = b58encode(bytes([42]) * 32), b58encode(bytes([43]) * 32)
    with ScriptedRpc(genesis_hash=MAINNET_GENESIS) as node:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({chains.SOLANA_MAINNET: node.url}))
        wallet = custody.register_external_signer_wallet(sender, network=chains.SOLANA_MAINNET)
        contact = store.create_contact(display_name="Willow", actor=store.ACTOR_OWNER,
            endpoints=[{"kind": "wallet", "value": destination, "network": chains.SOLANA_MAINNET}])
        response = handle_wallet_post("/api/wallet/propose", {"wallet_id": wallet.wallet_id,
            "network": chains.SOLANA_MAINNET, "destination": "Willow", "amount_minor": 1_000_000,
            "asset": "SOL", "origin": "user"}, client_host="127.0.0.1")
        body = json.loads(response.body)
        assert response.status == 200, body
        proposal = body["proposal"]
        assert proposal["state"] == "pending_approval" and proposal["destination"] == destination
        assert proposal["recipient"]["contact_id"] == contact["contact_id"]
        assert any(c["method"] == "simulateTransaction" for c in node.calls)
        assert node.send_count() == 0
        rejected = handle_wallet_post("/api/wallet/reject", {"proposal_id": proposal["proposal_id"]}, client_host="127.0.0.1")
        assert rejected.status == 200 and json.loads(rejected.body)["cancelled"] is True
        from core.wallet import limits, transfers

        [receipt] = transfers.list_transfers()
        assert receipt["proposal_id"] == proposal["proposal_id"] and receipt["state"] == "cancelled"
        assert receipt["record_kind"] == "proposal_receipt" and receipt["charged_fee_minor"] == "0"
        assert receipt["dispatched"] is False and receipt["tx_id"] == "" and receipt["explorer_url"] == ""
        assert transfers.latest_receipt(proposal["proposal_id"])["receipt_id"] == receipt["receipt_id"]
        assert limits.reservation_state(proposal["proposal_id"]) == "" and node.send_count() == 0


def test_wallet_screen_asks_when_two_mainnet_addresses_fit_and_proposes_nothing(mainnet):
    wallet, node = mainnet
    store.create_contact(display_name="Willow", actor=store.ACTOR_OWNER, endpoints=[
        {"kind": "wallet", "value": DESTINATION, "network": NETWORK},
        {"kind": "wallet", "value": "0x" + "c" * 40, "network": NETWORK}])
    response = handle_wallet_post("/api/wallet/propose", {"wallet_id": wallet.wallet_id,
        "destination": "Willow", "amount_minor": 1, "asset": "ETH"}, client_host="127.0.0.1")
    body = json.loads(response.body)
    assert response.status == 400 and body["error"] == "wallet_recipient_refused"
    assert body["fault"]["context"]["reason"] == "recipient_ambiguous"
    assert "Which one" in body["message"] and DESTINATION in body["message"]
    assert proposals.list_proposals() == [] and node.calls == []


def test_chain_card_default_policy_preserves_accounts_and_registry_admission(mainnet, monkeypatch):
    from core.wallet import capabilities

    wallet, node = mainnet
    monkeypatch.setattr(capabilities, "DEFAULT_VISIBLE_CHAIN_KEYS", frozenset({"solana"}), raising=False)
    rows = capabilities.network_rows(active_environment="mainnet", wallets=custody.list_wallets())
    assert {r["network"] for r in rows} == {chains.SOLANA_MAINNET, chains.SOLANA_DEVNET, NETWORK}
    assert next(r for r in rows if r["network"] == NETWORK)["accounts"][0]["wallet_id"] == wallet.wallet_id
    # Presentation never disables the row. An account created there becomes visible
    # even if its chain is not in the default card set.
    added = custody.create_watch_only_wallet(SENDER, network="eip155:8453")
    rows = capabilities.network_rows(active_environment="mainnet", wallets=custody.list_wallets())
    assert next(r for r in rows if r["network"] == "eip155:8453")["accounts"][0]["wallet_id"] == added.wallet_id
    assert chains.resolve_network("eip155:8453").is_mainnet and node.calls == []


def test_contact_list_carries_the_chain_identity_the_wallet_picker_checks(mainnet):
    from core.web.api.contacts_api import contacts_read

    _contact()
    status, listing = contacts_read("list", {})
    assert status == 200
    [endpoint] = listing["contacts"][0]["endpoints"]
    assert endpoint["chain_network"] == endpoint["network"] == NETWORK


@pytest.mark.parametrize("change", ["receipt_state", "receipt_signature", "principal_charge", "fee_charge", "unknown_state", "proposal_signature"])
def test_uncertain_or_inconsistent_refusal_is_never_presented_as_not_sent(mainnet, change):
    from core.wallet import lifecycle, transfers
    from core.wallet.store import connection

    wallet, node = mainnet
    _contact()
    proposal = _proposal(wallet)
    ended = lifecycle.end_refused(proposal.proposal_id, proposals.STATE_FAILED,
        expected_state=proposal.state, fault_code="wallet_simulation_failed", reason="simulation_failed")
    assert ended and transfers.latest_receipt(proposal.proposal_id)["record_kind"] == "proposal_receipt"
    with connection() as conn:
        if change == "receipt_state":
            conn.execute("UPDATE wallet_receipts SET state = 'broadcast' WHERE proposal_id = ?", (proposal.proposal_id,))
        elif change == "receipt_signature":
            conn.execute("UPDATE wallet_receipts SET tx_signature = 'uncertain' WHERE proposal_id = ?", (proposal.proposal_id,))
        elif change in ("principal_charge", "fee_charge"):
            raw = conn.execute("SELECT payload_json FROM wallet_receipts WHERE proposal_id = ?", (proposal.proposal_id,)).fetchone()[0]
            payload = json.loads(raw)
            field = "charged_amount_minor" if change == "principal_charge" else "charged_fee_minor"
            payload["refusal"][field] = 1
            conn.execute("UPDATE wallet_receipts SET payload_json = ? WHERE proposal_id = ?", (json.dumps(payload), proposal.proposal_id))
        elif change == "unknown_state":
            conn.execute("UPDATE wallet_proposals SET state = 'broadcast' WHERE proposal_id = ?", (proposal.proposal_id,))
        else:
            conn.execute("UPDATE wallet_proposals SET tx_signature = 'uncertain' WHERE proposal_id = ?", (proposal.proposal_id,))
    assert transfers.latest_receipt(proposal.proposal_id) is None
    assert transfers.list_transfers() == [] and node.sent == []
