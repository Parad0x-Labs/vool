"""Chat: "Send 10 USDC to Maya" from chat on Solana: the saved contact's one Solana wallet -> ONE proposal in USDC -> PIN ->
one signed send -> the principal proven from the chain's own token balances. A name with no saved wallet, or two, is a
question back, never a guess.

SIMULATION chain: the loopback Solana node answers as Mainnet and executes what it is sent; the wallet's own custody
signs with its real sealed key. The chat door is the production model tool (wallet.propose); Contacts are real.
"""
from __future__ import annotations

import pytest

from core.contacts import store
from tests.wallet._simulated_solana import USDC_MAINNET_MINT
from tests.wallet.test_wallet_usdt_send import PIN, SOLANA_MAINNET, _approve, _propose, chain  # noqa: F401  (fixture)

pytestmark = [pytest.mark.safety]
OWNER = store.ACTOR_OWNER


def _wallet(node, *, lamports: int = 20_000_000, usdc: int = 25_000_000) -> dict:
    import uuid

    from core.wallet import pilot_custody

    created = pilot_custody.create_pilot_wallet(network=SOLANA_MAINNET, method="pin", credential=PIN, credential_confirmation=PIN, creation_key=f"t-{uuid.uuid4().hex}", label="Solana")
    revealed = pilot_custody.reveal_pilot_backup(created["wallet_id"], credential=PIN)
    wallet = pilot_custody.acknowledge_pilot_backup(created["wallet_id"], ack_token=revealed["ack_token"])
    node.fund_sol(wallet["address"], lamports)
    if usdc:
        node.fund_token(wallet["address"], USDC_MAINNET_MINT, usdc)
    return wallet


def _person(node) -> str:
    from solders.keypair import Keypair

    owner = str(Keypair().pubkey())
    node.fund_sol(owner, 5_000_000)
    node.open_token_account(owner, USDC_MAINNET_MINT)
    return owner


def test_send_10_usdc_to_kas_resolves_his_saved_wallet_and_proves_the_payment(chain) -> None:
    node = chain
    wallet = _wallet(node)
    maya = _person(node)
    store.create_contact(display_name="Maya", actor=OWNER, endpoints=[{"kind": "wallet", "value": maya}])

    asked = _propose(destination="Maya", asset="USDC", amount="10")
    assert asked.ok and asked.status == "pending_approval", asked.response_text
    proposal = asked.details["proposal"]
    assert (proposal["asset"], int(proposal["amount_minor"]), proposal["destination"], proposal["network"]) == ("USDC", 10_000_000, maya, SOLANA_MAINNET)
    assert "Maya" in asked.response_text and "10 USDC" in asked.response_text, asked.response_text
    assert node.distinct_sends() == 0

    quote, result = _approve(proposal["proposal_id"])
    fields = quote["fields"]
    assert (fields["recipient_saved"], fields["recipient_label"], fields["to_address"], fields["asset"]) == (True, "Maya", maya, "USDC"), \
        "the card names Maya and shows their full saved address before the PIN"
    assert result["transfer"]["state"] == "confirmed", result["transfer"]
    assert node.token_balance(maya, USDC_MAINNET_MINT) == 10_000_000 and node.token_balance(wallet["address"], USDC_MAINNET_MINT) == 15_000_000
    assert node.distinct_sends() == 1


def test_a_name_with_no_saved_wallet_is_asked_back_and_nothing_is_proposed(chain) -> None:
    node = chain
    _wallet(node)
    store.create_contact(display_name="Maya", actor=OWNER, endpoints=[{"kind": "email", "value": "maya@example.com"}])

    asked = _propose(destination="Maya", asset="USDC", amount="10")
    assert asked.status in {"recipient_has_no_address", "recipient_not_found"}, (asked.status, asked.response_text)
    assert "Nothing was proposed" in asked.response_text
    assert node.distinct_sends() == 0


def test_two_saved_solana_wallets_ask_which_one(chain) -> None:
    node = chain
    _wallet(node)
    store.create_contact(display_name="Maya", actor=OWNER, endpoints=[
        {"kind": "wallet", "value": _person(node), "label": "hot"}, {"kind": "wallet", "value": _person(node), "label": "cold"}])

    asked = _propose(destination="Maya", asset="USDC", amount="10")
    assert asked.status == "recipient_ambiguous", (asked.status, asked.response_text)
    assert "Which one?" in asked.response_text
    assert node.distinct_sends() == 0
