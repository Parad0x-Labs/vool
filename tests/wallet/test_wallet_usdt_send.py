"""USDT on Solana from a chat request: "send Tomas 10 USDT" -> the contact's one wallet that can take USDT -> ONE proposal
-> a quote in USDT with the fee (and, for a recipient without a USDT account, that account's rent) in SOL -> PIN
approval -> one signed send -> the principal proven from the chain's own token balances -> holds settled per asset.

SIMULATION chain: the loopback Solana node answers as Mainnet (its genesis hash) and executes what it is sent, including
the Associated Token Account program's CreateIdempotent; the wallet's own custody signs with its real sealed key. The
chat door is the production model tool (core.runtime_execution_tools wallet.propose / wallet.status); Contacts are real.
"""
from __future__ import annotations

import json
import uuid

import pytest

from core.contacts import store
from tests.contacts import protected_changes
from tests.wallet._simulated_solana import (
    MAINNET_GENESIS,
    TOKEN_ACCOUNT_SIZE,
    USDC_MAINNET_MINT,
    SimulatedSolanaNode,
    rent_minimum,
)

pytestmark = [pytest.mark.safety]

SOLANA_MAINNET = "solana:5eykt4UsFv8P8NJdTREpY1vzqKqZKvdp"
BASE = "eip155:8453"
USDT_MINT = "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"
EVM_A = "0x5aAeb6053F3E94C9b9A09f33669435E7Ef1BeAed"
PIN = "482913"
OWNER = store.ACTOR_OWNER
FEE = 5_000
ACCOUNT_RENT = rent_minimum(TOKEN_ACCOUNT_SIZE)


@pytest.fixture
def chain(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_WALLET_ENABLED", "1")
    monkeypatch.setenv("VOOL_WALLET_X402_ALLOW_LOOPBACK", "1")  # the simulated node listens on loopback
    for name in ("VOOL_WALLET_NETWORK_ENVIRONMENT", "VOOL_WALLET_TESTNET_RPC_URL", "VOOL_WALLET_GLOBAL_DAILY_MINOR"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "blackbox"))
    from core.blackbox import store as store_module
    from core.wallet import chains, environment, lifecycle

    monkeypatch.setattr(lifecycle, "_CONFIRM_BUDGET_SECONDS", 2.0)
    protected_changes.reset()
    store_module.reset_default_store()
    chains.invalidate_chain_identity()
    with SimulatedSolanaNode(genesis_hash=MAINNET_GENESIS) as node:
        monkeypatch.setenv("VOOL_WALLET_RPC_URLS", json.dumps({SOLANA_MAINNET: node.url}))
        environment.set_active_environment("mainnet")
        node.create_mint(USDT_MINT, decimals=6)
        node.create_mint(USDC_MAINNET_MINT, decimals=6)
        yield node
    chains.invalidate_chain_identity()
    store_module.reset_default_store()
    protected_changes.reset()


def _wallet(node, *, lamports: int = 20_000_000, usdt: int = 25_000_000) -> dict:
    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"t-{uuid.uuid4().hex}", label="Solana")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    wallet = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    node.fund_sol(wallet["address"], lamports)
    if usdt:
        node.fund_token(wallet["address"], USDT_MINT, usdt)
    return wallet


def _person(node, *, usdt_account: bool) -> str:
    from solders.keypair import Keypair

    owner = str(Keypair().pubkey())
    node.fund_sol(owner, 5_000_000)
    if usdt_account:
        node.open_token_account(owner, USDT_MINT)
    return owner


def _propose(**args) -> object:
    from core.runtime_execution_tools import _dispatch_runtime_tool

    return _dispatch_runtime_tool("wallet.propose", dict(args), source_context={"session_id": "usdt-send"})


def _approve(proposal_id: str) -> tuple[dict, dict]:
    from core.wallet import approval, lifecycle, quotes

    quote = quotes.mint_quote(proposal_id)
    result = lifecycle.default_lifecycle().approve_pilot_transfer(proposal_id, quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))
    return quote, result


def _holds(proposal_id: str) -> list[tuple]:
    from core.wallet.store import connection

    with connection() as conn:
        return [tuple(row) for row in conn.execute(
            "SELECT proposal_id, asset, amount_minor, fee_minor, state FROM wallet_spend_ledger WHERE proposal_id IN (?, ?) ORDER BY proposal_id",
            (proposal_id, f"{proposal_id}:fee")).fetchall()]


def test_send_tomas_10_usdt_picks_his_solana_wallet_opens_his_usdt_account_and_proves_the_payment(chain) -> None:
    node = chain
    wallet = _wallet(node)
    tomas = _person(node, usdt_account=False)
    store.create_contact(display_name="Tomas Berg", actor=OWNER, endpoints=[
        {"kind": "wallet", "value": tomas}, {"kind": "wallet", "value": EVM_A, "network": BASE}])

    asked = _propose(destination="Tomas Berg", asset="USDT", amount="10")
    assert asked.ok and asked.status == "pending_approval", asked.response_text
    proposal = asked.details["proposal"]
    assert (proposal["asset"], int(proposal["amount_minor"]), proposal["destination"], proposal["network"]) == ("USDT", 10_000_000, tomas, SOLANA_MAINNET), \
        "Base cannot take USDT from this wallet, so his Solana wallet is the one destination"
    assert node.distinct_sends() == 0, "a proposal signs and sends nothing"

    quote, result = _approve(proposal["proposal_id"])
    fields = quote["fields"]
    assert (fields["asset"], fields["mint"], fields["decimals"], fields["amount_human"], fields["gas_asset"]) == ("USDT", USDT_MINT, 6, "10", "SOL")
    assert (fields["create_recipient_account"], fields["recipient_account_rent_minor"]) == (True, ACCOUNT_RENT)
    assert fields["fee_parts"] == {"base_minor": FEE, "account_rent_minor": ACCOUNT_RENT} and fields["fee_max_minor"] == FEE + ACCOUNT_RENT
    review = fields["review"]
    assert "opening their USDT account" in review["network_line"], review["network_line"]
    assert any(w["code"] == "new_recipient_token_account" for w in review["warnings"]), review["warnings"]

    assert result["transfer"]["state"] == "confirmed", result["transfer"]
    assert node.token_balance(tomas, USDT_MINT) == 10_000_000 and node.token_balance(wallet["address"], USDT_MINT) == 15_000_000
    assert node.sol_balance(wallet["address"]) == 20_000_000 - FEE - ACCOUNT_RENT and node.distinct_sends() == 1
    pid = proposal["proposal_id"]
    assert _holds(pid) == [(pid, "USDT", 10_000_000, 0, "settled"), (f"{pid}:fee", "SOL", 0, FEE + ACCOUNT_RENT, "settled")], \
        "the principal is held in USDT; the fee and the opened account's rent are what left in SOL"


def test_a_second_send_to_an_existing_usdt_account_opens_nothing_and_charges_only_the_fee(chain) -> None:
    node = chain
    wallet = _wallet(node)
    tomas = _person(node, usdt_account=True)
    store.create_contact(display_name="Tomas Berg", actor=OWNER, endpoints=[{"kind": "wallet", "value": tomas}])

    asked = _propose(destination="Tomas Berg", asset="USDT", amount="2.5")
    quote, result = _approve(asked.details["proposal"]["proposal_id"])
    assert (quote["fields"]["create_recipient_account"], quote["fields"]["fee_max_minor"], quote["fields"]["cues"]) == (False, FEE, [])
    assert result["transfer"]["state"] == "confirmed"
    assert node.token_balance(tomas, USDT_MINT) == 2_500_000 and node.sol_balance(wallet["address"]) == 20_000_000 - FEE


def test_an_account_opened_by_someone_else_after_the_quote_is_not_paid_for_twice(chain) -> None:
    node = chain
    wallet = _wallet(node)
    tomas = _person(node, usdt_account=False)
    asked = _propose(destination=tomas, asset="USDT", amount="1", network=SOLANA_MAINNET)
    from core.wallet import approval, lifecycle, quotes

    quote = quotes.mint_quote(asked.details["proposal"]["proposal_id"])
    assert quote["fields"]["create_recipient_account"] is True
    node.open_token_account(tomas, USDT_MINT)  # he opened it himself meanwhile
    result = lifecycle.default_lifecycle().approve_pilot_transfer(quote["proposal_id"], quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))
    assert result["transfer"]["state"] == "confirmed"
    assert node.sol_balance(wallet["address"]) == 20_000_000 - FEE, "CreateIdempotent on an existing account spends no rent"
    assert _holds(quote["proposal_id"])[1] == (f"{quote['proposal_id']}:fee", "SOL", 0, FEE, "settled"), "the hold settles at what actually left"


def test_a_recipient_account_closed_after_the_quote_asks_for_a_fresh_preview(chain) -> None:
    from core.wallet import approval, lifecycle, quotes
    from core.wallet.errors import WalletFault

    node = chain
    _wallet(node)
    tomas = _person(node, usdt_account=True)
    asked = _propose(destination=tomas, asset="USDT", amount="1", network=SOLANA_MAINNET)
    quote = quotes.mint_quote(asked.details["proposal"]["proposal_id"])
    assert quote["fields"]["create_recipient_account"] is False
    del node.token_accounts[node.token_account_address(tomas, USDT_MINT)]
    with pytest.raises(WalletFault) as caught:
        lifecycle.default_lifecycle().approve_pilot_transfer(quote["proposal_id"], quote_id=quote["quote_id"], quote_digest=quote["digest"], approver=approval.PinApprover(PIN))
    assert (caught.value.code, caught.value.context.get("reason")) == ("wallet_quote_expired", "recipient_token_account_changed")
    assert node.distinct_sends() == 0


def test_two_saved_wallets_that_can_take_usdt_are_asked_about_never_guessed(chain) -> None:
    node = chain
    _wallet(node)
    first, second = _person(node, usdt_account=True), _person(node, usdt_account=False)
    store.create_contact(display_name="Tomas Berg", actor=OWNER, endpoints=[
        {"kind": "wallet", "value": first, "label": "main"}, {"kind": "wallet", "value": second, "label": "savings"},
        {"kind": "wallet", "value": EVM_A, "network": BASE}])
    asked = _propose(destination="Tomas Berg", asset="USDT", amount="10")
    assert asked.ok and asked.status == "recipient_ambiguous", asked.response_text
    assert "2 saved wallets that can take USDT" in asked.response_text and "Which one?" in asked.response_text
    assert {c["value"] for c in asked.details["recipient"]["choices"]} == {first, second}, "the Base wallet is not a choice for USDT"
    assert node.distinct_sends() == 0


def test_a_contact_whose_only_wallet_cannot_take_usdt_is_told_so(chain) -> None:
    _wallet(chain)
    store.create_contact(display_name="Tomas Berg", actor=OWNER, endpoints=[{"kind": "wallet", "value": EVM_A, "network": BASE}])
    asked = _propose(destination="Tomas Berg", asset="USDT", amount="10")
    assert asked.status == "recipient_has_no_address", asked.response_text
    assert "Base" in asked.response_text and "cannot send USDT" in asked.response_text


def test_a_token_account_or_a_program_address_is_not_a_wallet_to_open_an_account_for(chain) -> None:
    from solders.keypair import Keypair
    from solders.pubkey import Pubkey

    node = chain
    _wallet(node)
    someone = _person(node, usdt_account=False)
    associated = node.open_token_account(someone, USDC_MAINNET_MINT)  # someone's USDC account address, pasted as a wallet
    keyed_account = str(Keypair().pubkey())  # a token account made at a key's address (on the curve, but not a wallet)
    node.token_accounts[keyed_account] = {"mint": USDC_MAINNET_MINT, "owner": someone, "amount": 0, "frozen": False}
    node.fund_sol(keyed_account, ACCOUNT_RENT)
    program_address = str(Pubkey.find_program_address([b"vault"], Pubkey.from_string(USDT_MINT))[0])  # off the curve: no key can spend
    for destination, reason in ((associated, "recipient_not_a_wallet_key"), (keyed_account, "recipient_is_a_token_account"),
                                (program_address, "recipient_not_a_wallet_key")):
        asked = _propose(destination=destination, asset="USDT", amount="1", network=SOLANA_MAINNET)
        assert (asked.ok, asked.status) == (False, "wallet_recipient_refused"), asked.response_text
        assert asked.details["fault"]["context"]["reason"] == reason, asked.details["fault"]
        assert "Nothing was signed and nothing was sent." in asked.response_text
    assert node.distinct_sends() == 0


def test_only_the_owners_own_requests_move_usdt(chain) -> None:
    from core.wallet import proposals
    from core.wallet.errors import WalletFault

    wallet = _wallet(chain)
    to = _person(chain, usdt_account=True)
    for origin in (proposals.ORIGIN_SKILL, proposals.ORIGIN_PLUGIN, proposals.ORIGIN_USEPOD, proposals.ORIGIN_X402):
        with pytest.raises(WalletFault) as caught:
            proposals.propose_transaction(wallet_id=wallet["wallet_id"], destination=to, amount_minor=1_000_000, asset="USDT", origin=origin, network=SOLANA_MAINNET,
                                          idempotency_key=uuid.uuid4().hex)
        assert caught.value.code == "wallet_network_disabled", (origin, caught.value.context)


def test_a_usepod_requirement_in_usdt_is_not_a_lane_payment(chain) -> None:
    import time

    from core.wallet import usepod
    from core.wallet.errors import WalletFault

    wallet = _wallet(chain)
    pay_to = _person(chain, usdt_account=True)
    requirement = usepod.parse_requirement({
        "correlation_id": f"upo_{uuid.uuid4().hex}", "provider": "usepod", "network": SOLANA_MAINNET, "asset": "USDT", "pay_to": pay_to,
        "amount_minor": 150_000, "expires_at": time.time() + 300, "resource": "http://127.0.0.1:9/proxy/x402/v1/chat/completions", "payer_wallet": wallet["wallet_id"],
    })
    with pytest.raises(WalletFault) as caught:
        usepod.validate_x402_payment(requirement)
    assert (caught.value.code, caught.value.context.get("reason")) == ("wallet_network_disabled", "x402_asset_not_registered_on_row")


def test_on_test_networks_usdt_is_refused_with_where_it_can_go(chain) -> None:
    from core.wallet import environment

    environment.set_active_environment("testnet")
    asked = _propose(destination=_person(chain, usdt_account=True), asset="USDT", amount="1")
    assert not asked.ok and asked.status == "wallet_not_found"
    assert "USDT can be sent on Mainnet only (Solana)" in asked.response_text, asked.response_text


def test_wallet_balances_in_chat_include_usdt(chain) -> None:
    from core.runtime_execution_tools import _dispatch_runtime_tool

    _wallet(chain, lamports=1_500_000_000, usdt=25_000_000)
    result = _dispatch_runtime_tool("wallet.status", {}, source_context={"session_id": "usdt-balances"})
    text = result.response_text
    assert "Balances on Mainnet (each chain's own coin and USDT):" in text, text
    assert "- Solana: 1.5 SOL" in text and "- Solana: 25 USDT" in text, text
    rows = {(row["network"], row["asset"]): row for row in result.details["balances"]}
    assert rows[(SOLANA_MAINNET, "USDT")]["balance_minor"] == "25000000" and rows[(SOLANA_MAINNET, "USDT")]["token"] is True
