"""Transfer proposals consume Contacts: "prepare 0.1 SOL to Alex" pays only an address Contacts saved for that network,
asks when several fit, binds who the destination was resolved to into the quote the owner approves, and refuses the
approval once that saved destination changed -- before any credential is asked for. Looking a contact up never signs or
sends.

What executes: the production wallet owner (pilot custody, proposals, lifecycle.prepare, quotes, approve_pilot_transfer)
against loopback Solana-dialect and EVM nodes answering as Devnet and Base Sepolia (SIMULATED CHAIN), the runtime wallet
tool door, and the production Contacts store. No live chain, funds, model or network.
"""
from __future__ import annotations

import json
import uuid

import pytest
from tests.wallet._rig import DEVNET_GENESIS, ScriptedRpc
from tests.wallet._rig_evm_native import ScriptedEvmNativeChain

from core.contacts import resolver, store
from tests.contacts import protected_changes
from core.wallet.errors import WalletFault

pytestmark = [pytest.mark.safety]

SOLANA_DEVNET = "solana:EtWTRABZaYq6iMfeYKouRu166VU2xqa1"
SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
BASE_SEPOLIA = "eip155:84532"
ETH_SEPOLIA = "eip155:11155111"
PIN = "482913"
OWNER = store.ACTOR_OWNER
EVM_ZOE = "0xfB6916095ca1df60bB79Ce92cE3Ea74c37c5d359"


@pytest.fixture
def chain(monkeypatch, tmp_path):
    """Crypto on, Test networks selected, loopback Devnet and Base Sepolia nodes, Solana Devnet's pilot lane ready."""
    with ScriptedRpc(genesis_hash=DEVNET_GENESIS) as sol, ScriptedEvmNativeChain(chain_id=84532, fee_model="op_stack") as base:
        sol.real_signature = True
        sol.status_keyed = True
        monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
        monkeypatch.setenv("VOOL_WALLET_NETWORK_ENVIRONMENT", "testnet")
        monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", sol.url)
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({BASE_SEPOLIA: base.url}))
        monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")
        for name in ("VOOL_WALLET_UI_CAPABILITY_SHA256", "VOOL_ALLOWED_HOSTS"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
        from core.blackbox import store as blackbox_store
        from core.wallet import capabilities, chains, lifecycle

        monkeypatch.setattr(capabilities, "PILOT_TRANSFER_READY_ROWS", frozenset({SOLANA_DEVNET}))
        monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 1.0)
        blackbox_store.reset_default_store()
        chains.invalidate_chain_identity()
        protected_changes.reset()
        yield {"sol": sol, "base": base}
        protected_changes.reset()
        chains.invalidate_chain_identity()
        blackbox_store.reset_default_store()


def _sol_key() -> str:
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    from core.vool_wallet import b58encode

    return b58encode(Ed25519PrivateKey.generate().public_key().public_bytes_raw())


def _pilot(network: str = SOLANA_DEVNET, label: str = "main pocket", per_tx_minor: int = 0) -> dict:
    from core.wallet import limits, pilot_custody

    created = pilot_custody.create_pilot_wallet(network=network, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"c-{uuid.uuid4().hex}", label=label)
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    wallet = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    if per_tx_minor:
        asset = "SOL" if network.startswith("solana") else "ETH"
        limits.set_limits(wallet["wallet_id"], asset, limits.SpendLimits(per_tx_minor, per_tx_minor * 5, per_tx_minor * 3))
    return wallet


def _propose(args: dict):
    from core.runtime_execution_tools import _dispatch_runtime_tool

    return _dispatch_runtime_tool("wallet.propose", dict(args), source_context={"session_id": "contacts-wallet"})


def _count(table: str) -> int:
    from core.wallet.store import connection

    with connection() as conn:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def test_original_prepare_point_one_sol_to_alex_proposes_to_his_saved_devnet_address(chain) -> None:
    # the owner's own cap admits 0.1 SOL plus fees (the default per-transaction cap is exactly 0.1 SOL)
    wallet = _pilot(per_tx_minor=1_000_000_000)
    alex_address = _sol_key()
    alex = store.create_contact(display_name="Alex Chen", actor=OWNER, endpoints=[{"kind": "wallet", "value": alex_address, "network": SOLANA_DEVNET, "label": "main"}])
    result = _propose({"destination": "Alex", "amount": "0.1", "asset": "SOL"})
    assert result.ok, (result.status, result.response_text)
    proposal = result.details["proposal"]
    assert (proposal["destination"], proposal["network"], proposal["amount_minor"], proposal["state"], proposal["wallet_id"]) == (
        alex_address, SOLANA_DEVNET, 100_000_000, "pending_approval", wallet["wallet_id"])
    recipient = proposal["recipient"]
    assert (recipient["resolution"], recipient["contact_id"], recipient["fingerprint"]) == ("contact", alex["contact_id"], alex["endpoints"][0]["fingerprint"])
    assert f"Alex Chen · main · {alex_address} on Solana Devnet" in result.response_text
    assert "Nothing has been signed" in result.response_text
    assert chain["sol"].sent == [] and _count("wallet_signing_requests") == 0
    from core.wallet.status import wallet_status

    [row] = [p for p in wallet_status()["pending"] if p["proposal_id"] == proposal["proposal_id"]]
    # saved_for added by the release side-task (an exact-address destination names its saved
    # contacts beside the address); a name-resolved proposal carries no exact-address matches.
    assert row["recipient"] == {"saved": True, "display_name": "Alex Chen", "label": "main", "verification": "user_entered", "warning": "", "saved_for": []}


def test_novel_zoe_with_two_saved_addresses_asks_and_a_labelled_choice_proposes(chain) -> None:
    _pilot()
    savings, spending = _sol_key(), _sol_key()
    store.create_contact(display_name="Zoë Ng", actor=OWNER, endpoints=[
        {"kind": "wallet", "value": savings, "network": SOLANA_DEVNET, "label": "savings"},
        {"kind": "wallet", "value": spending, "network": SOLANA_DEVNET, "label": "spending"}])
    before = _count("wallet_proposals")
    ask = _propose({"destination": "Zoë", "amount": "0.0005", "asset": "SOL"})
    assert ask.ok and ask.status == "recipient_ambiguous", (ask.status, ask.response_text)
    assert "Which one" in ask.response_text and "Nothing was proposed, signed or sent." in ask.response_text
    assert {c["label"] for c in ask.details["recipient"]["choices"]} == {"savings", "spending"}
    assert _count("wallet_proposals") == before
    chosen = _propose({"destination": "Zoë Ng (spending)", "amount": "0.0005", "asset": "SOL"})
    assert chosen.ok and chosen.details["proposal"]["destination"] == spending, (chosen.status, chosen.response_text)


def test_a_saved_address_is_used_only_on_its_own_network_and_environment(chain) -> None:
    wallet = _pilot()
    mainnet_address = _sol_key()
    store.create_contact(display_name="Mara Okafor", actor=OWNER, endpoints=[
        {"kind": "wallet", "value": mainnet_address, "network": SOLANA_MAINNET}, {"kind": "wallet", "value": EVM_ZOE, "network": BASE_SEPOLIA}])
    before = _count("wallet_proposals")
    refused = _propose({"destination": "Mara Okafor", "amount": "0.0005", "asset": "SOL"})
    assert refused.ok and refused.status == "recipient_has_no_address", (refused.status, refused.response_text)
    assert "on Solana" in refused.response_text and _count("wallet_proposals") == before
    from core.wallet import proposals

    base_snapshot = resolver.resolve("Mara Okafor", kind="wallet", network=BASE_SEPOLIA).snapshot
    devnet_destination = _sol_key()
    with pytest.raises(WalletFault) as other_network:
        proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=devnet_destination, amount_minor=500_000, asset="SOL",
                                      origin=proposals.ORIGIN_MODEL, network=SOLANA_DEVNET, recipient={**base_snapshot, "value": devnet_destination})
    assert other_network.value.code == "wallet_recipient_refused" and "recipient_saved_for_another_network" in json.dumps(other_network.value.to_dict())
    with pytest.raises(WalletFault) as other_value:
        proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=devnet_destination, amount_minor=500_000, asset="SOL",
                                      origin=proposals.ORIGIN_MODEL, network=SOLANA_DEVNET, recipient=dict(base_snapshot))
    assert "recipient_names_another_destination" in json.dumps(other_value.value.to_dict())
    assert _count("wallet_proposals") == before


def test_the_quote_binds_the_recipient_and_a_changed_destination_is_refused_before_any_credential(chain) -> None:
    _pilot()
    alex_address = _sol_key()
    alex = store.create_contact(display_name="Alex Chen", actor=OWNER, endpoints=[{"kind": "wallet", "value": alex_address, "network": SOLANA_DEVNET, "label": "main"}])
    proposal_id = _propose({"destination": "Alex Chen", "amount": "0.0005", "asset": "SOL"}).details["proposal"]["proposal_id"]
    from core.wallet import lifecycle, quotes

    quote = quotes.mint_quote(proposal_id)
    fields = quote["fields"]
    assert (fields["recipient_saved"], fields["recipient_label"], fields["to_address"], fields["recipient_warning"]) == (True, "Alex Chen · main", alex_address, "")
    assert fields["recipient_fingerprint"] == alex["endpoints"][0]["fingerprint"]
    protected_changes.update_contact(alex["contact_id"], change_endpoints=[{"endpoint_id": alex["endpoints"][0]["endpoint_id"], "value": _sol_key()}])

    class NeverAsked:
        calls = 0

        def approve(self, challenge):
            NeverAsked.calls += 1
            raise AssertionError("the owner must not be asked for a credential")

    with pytest.raises(WalletFault) as refused:
        lifecycle.default_lifecycle().approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=NeverAsked())
    assert refused.value.code == "wallet_recipient_refused" and "contact_endpoint_changed" in json.dumps(refused.value.to_dict())
    assert NeverAsked.calls == 0 and chain["sol"].sent == []
    with pytest.raises(WalletFault) as requote:
        quotes.mint_quote(proposal_id)
    assert requote.value.code == "wallet_recipient_refused"


def test_an_unchanged_contact_approves_and_sends_exactly_once(chain) -> None:
    """Preservation: binding the recipient changes nothing for an approval of the destination the owner reviewed."""
    _pilot()
    alex_address = _sol_key()
    store.create_contact(display_name="Alex Chen", actor=OWNER, endpoints=[{"kind": "wallet", "value": alex_address, "network": SOLANA_DEVNET}])
    proposal_id = _propose({"destination": "Alex Chen", "amount": "0.0005", "asset": "SOL"}).details["proposal"]["proposal_id"]
    from core.wallet import approval, lifecycle, quotes

    quote = quotes.mint_quote(proposal_id)
    outcome = lifecycle.default_lifecycle().approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))
    assert outcome["duplicate"] is False and len(chain["sol"].sent) == 1
    assert outcome["transfer"]["to_address"] == alex_address


def test_novel_evm_contact_on_two_testnets_asks_then_base_is_chosen_and_a_lookalike_is_flagged(chain) -> None:
    _pilot(BASE_SEPOLIA, "base pocket")
    store.create_contact(display_name="Zoë Ng", actor=OWNER, endpoints=[
        {"kind": "wallet", "value": EVM_ZOE, "network": BASE_SEPOLIA}, {"kind": "wallet", "value": EVM_ZOE, "network": ETH_SEPOLIA}])
    before = _count("wallet_proposals")
    ask = _propose({"destination": "Zoë Ng", "amount": "0.001", "asset": "ETH"})
    assert ask.ok and ask.status == "recipient_ambiguous" and _count("wallet_proposals") == before, (ask.status, ask.response_text)
    base = _propose({"destination": "Zoë Ng", "amount": "0.001", "asset": "ETH", "chain": "base"})
    assert base.ok, (base.status, base.response_text)
    assert (base.details["proposal"]["network"], base.details["proposal"]["destination"]) == (BASE_SEPOLIA, EVM_ZOE)
    body = EVM_ZOE.lower()[2:]
    lookalike = "0x" + body[:4] + "0" * 32 + body[-4:]
    flagged = _propose({"destination": lookalike, "amount": "0.001", "asset": "ETH", "chain": "base"})
    assert flagged.ok, (flagged.status, flagged.response_text)
    assert "Check before approving" in flagged.response_text and "Zoë Ng" in flagged.response_text
    assert flagged.details["proposal"]["recipient"]["resolution"] == "explicit"


def test_looking_up_contacts_never_proposes_signs_or_sends(chain) -> None:
    _pilot()
    store.create_contact(display_name="Alex Chen", actor=OWNER, endpoints=[{"kind": "wallet", "value": _sol_key(), "network": SOLANA_DEVNET}])
    from core.runtime_execution_tools import _dispatch_runtime_tool

    for intent, args in (("contacts.search", {"query": "Alex"}), ("contacts.resolve", {"name": "Alex", "kind": "wallet"})):
        out = _dispatch_runtime_tool(intent, args, source_context={"session_id": "contacts-wallet"})
        assert out.ok, (intent, out.status, out.response_text)
    assert _count("wallet_proposals") == 0 and _count("wallet_signing_requests") == 0 and chain["sol"].sent == []


def test_an_exact_address_destination_names_its_saved_contacts_on_the_pending_row(chain) -> None:
    """Release side-task A8 (`saved_for`): a destination given as an exact address that matches
    saved contacts shows those contact names beside the destination on the pending row — the
    resolver's own saved_matches, surfaced; never a substitute for the exact address itself."""
    _pilot()
    match_address = _sol_key()
    store.create_contact(display_name="Exact Match Person", actor=OWNER, endpoints=[{"kind": "wallet", "value": match_address, "network": SOLANA_DEVNET}])
    result = _propose({"destination": match_address, "amount": "0.0005", "asset": "SOL"})
    assert result.ok, (result.status, result.response_text)
    proposal = result.details["proposal"]
    assert proposal["destination"] == match_address
    from core.wallet.status import wallet_status

    [row] = [p for p in wallet_status()["pending"] if p["proposal_id"] == proposal["proposal_id"]]
    assert row["recipient"]["saved"] is False  # the address was given directly, not resolved by name
    assert row["recipient"]["saved_for"] == ["Exact Match Person"], row["recipient"]
    assert row["destination"] == match_address  # the exact address itself stays the destination

    # a plain unknown address still names nobody
    other = _propose({"destination": _sol_key(), "amount": "0.0005", "asset": "SOL"})
    assert other.ok, (other.status, other.response_text)
    [other_row] = [p for p in wallet_status()["pending"] if p["proposal_id"] == other.details["proposal"]["proposal_id"]]
    assert other_row["recipient"]["saved_for"] == [], other_row["recipient"]
