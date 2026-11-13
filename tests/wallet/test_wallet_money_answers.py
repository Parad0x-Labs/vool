"""Owner money questions in chat are answered from the wallet's own records, read-only.

What executes: core.wallet.money_answers (the reader the chat front door and the wallet.status tool use), real Crypto
Pilot custody, proposals and limits, and the scripted Solana node from tests.wallet._rig. No model, no public network.
The laws: a read never prepares, signs or sends a payment; amounts are coin amounts; crypto off, no wallet and an
unknown network say so plainly and never show a number; a send order is not a read question.
"""
from __future__ import annotations

import re

import pytest

from tests.wallet._rig import DESTINATION

PIN = "482915"

pytestmark = [pytest.mark.safety]

ASKED = (
    "how much money do i have across all my wallets?",
    "how much did the agent wallet spend yesterday and on what?",
    "how much is left in the agent wallet?",
    "what are the spending limits for the agent?",
    "is any payment waiting for my approval?",
    "show me my last few payments",
    "how much usdc do i have on base",
    "what's left in my burner",
)
NOT_ASKED = (
    "what's the price of SOL right now?",
    "how does the spend cap work?",
    "send 0.01 SOL to 9xQeWvG816bUx9EPjHmaT23yvVM2ZWbrrpZb9PusVFin",
    "what is a crypto wallet?",
    "how much did my openrouter credits cost this month?",
    "find the notes about my wallet",
    "approve the payment",
    "set the agent's daily limit to 5 USDC",
)
DEV_WORDS = re.compile(r"minor|lamport|wei\b|pocket_sealed|eip155|solana:|pending_approval|wallet-[0-9a-f]{6}", re.IGNORECASE)


@pytest.mark.parametrize("text", ASKED)
def test_owner_money_reads_are_recognised(text):
    from core.wallet import money_answers

    assert money_answers.is_money_question(text), text


@pytest.mark.parametrize("text", NOT_ASKED)
def test_prices_explanations_orders_and_other_money_are_not_wallet_reads(text):
    from core.wallet import money_answers

    assert not money_answers.is_money_question(text), text


def _wallet():
    from core.wallet import custody

    return custody.create_pocket_wallet(acknowledged_warning=True, confirmation_phrase=custody.POCKET_CONFIRMATION_PHRASE, pin=PIN).profile


def _proposal_count() -> int:
    from core.wallet import proposals

    return len(proposals.list_proposals(limit=500))


def test_totals_and_history_read_the_wallet_and_never_start_a_payment(wallet_env):
    from core.wallet import money_answers

    _wallet()
    before = _proposal_count()
    total = money_answers.answer("how much money do i have across all my wallets?")
    assert "5 SOL" in total.text and "Solana Devnet" in total.text, total.text
    history = money_answers.answer("show me my last few payments")
    assert "No payments have been made from your wallets yet." in history.text, history.text
    for reply in (total, history):
        assert not DEV_WORDS.search(reply.text), reply.text
    assert _proposal_count() == before and wallet_env["rpc"].send_count() == 0


def test_a_waiting_payment_is_listed_in_coin_amounts_and_stays_waiting(wallet_env):
    from core.runtime_execution_tools import _wallet_model_tool
    from core.wallet import money_answers, proposals

    _wallet()
    proposed = _wallet_model_tool("wallet.propose", {"destination": DESTINATION, "amount_minor": 50_000_000, "asset": "SOL", "memo": "rent"}, {"session_id": "s1"})
    proposal_id = proposed.details["observation"]["proposal_id"]
    assert "0.05 SOL" in proposed.response_text.splitlines()[0], proposed.response_text
    waiting = money_answers.answer("is any payment waiting for my approval?")
    assert "1 payment is waiting for your approval" in waiting.text and "0.05 SOL" in waiting.text, waiting.text
    assert "Nothing is paid until you approve" in waiting.text and not DEV_WORDS.search(waiting.text), waiting.text
    assert proposals.get_proposal(proposal_id).state == proposals.STATE_PENDING_APPROVAL
    assert wallet_env["rpc"].send_count() == 0


def test_limits_are_the_wallets_own_caps_in_coin_amounts(wallet_env):
    from core.wallet import money_answers

    _wallet()
    reply = money_answers.answer("what are my wallet limits?")
    assert "never pay without your approval" in reply.text and " SOL a payment" in reply.text and " SOL a day" in reply.text, reply.text
    agent = money_answers.answer("what are the spending limits for the agent?").text
    assert agent.startswith("The assistant has no wallet of its own") and "every payment asks you first" in agent, agent


def test_crypto_off_says_so_and_reads_nothing(wallet_env, monkeypatch):
    from core.wallet import money_answers

    monkeypatch.setenv("VOOL_WALLET_ENABLED", "0")
    reply = money_answers.answer("how much money do i have across all my wallets?")
    assert reply.text.startswith("Crypto is switched off in VOOL") and "Nothing was read and nothing was paid." in reply.text
    assert not re.search(r"\d", reply.text), reply.text


def test_no_wallet_says_so_and_never_shows_a_number(wallet_env):
    from core.wallet import money_answers

    for question in ("how much money do i have across all my wallets?", "how much did the agent spend yesterday?", "whats my balance on solana"):
        reply = money_answers.answer(question)
        assert "You don't have a wallet on" in reply.text and not re.search(r"\d", reply.text), reply.text


def test_an_unknown_network_is_named_not_guessed(wallet_env):
    from core.wallet import money_answers

    _wallet()
    reply = money_answers.answer("how much do i have on polygon?")
    assert reply.text.startswith("VOOL has no wallet on Polygon") and "Solana Devnet" in reply.text, reply.text
    assert "SOL" not in reply.text.replace("Solana", ""), reply.text


def test_a_balance_that_cannot_be_read_is_unknown_not_zero(wallet_env, monkeypatch):
    from core.wallet import money_answers

    _wallet()
    monkeypatch.setenv("VOOL_WALLET_TESTNET_RPC_URL", "http://127.0.0.1:9")
    reply = money_answers.answer("how much SOL do i have?")
    assert "unknown, not zero" in reply.text and "0 SOL" not in reply.text, reply.text
