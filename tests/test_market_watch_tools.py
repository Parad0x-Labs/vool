"""The watch tools from a chat: add, list, pause, resume, remove, and what they refuse.

Everything goes through the model's door (``execute_runtime_tool``). No host is contacted: adding
a watch only checks its requirement (a stored key, the web-lookups switch) and saves a row.
"""
from __future__ import annotations

import pytest

from tests._credential_intelligence_support import (
    isolated_home,
    vault_home,
)

HELIUS_KEY = "1b2c3d4e-5f60-4a7b-8c9d-0e1f2a3b4c5d"
SOL = "7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU"
EVM = "0xde0b295669a9fd93d5f28d9ec85e40f4cb697bae"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


@pytest.fixture(autouse=True)
def _clear_watch_rows():
    """The conftest's shared test database is not reset for these tables; this file clears them."""
    from core import market_watch
    from storage.db import get_connection

    market_watch._ensure_tables()
    conn = get_connection()
    try:
        conn.execute("PRAGMA secure_delete=ON")
        for table in ("market_watches", "market_watch_events"):
            conn.execute(f"DELETE FROM {table}")
        conn.commit()
    finally:
        conn.close()
    yield


def _run(intent, args):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool(intent, args, source_context={})


def _store_helius():
    from core.credential_store import store_credential

    store_credential("chain.data.helius", HELIUS_KEY, label="fixture")


def _rows():
    from core import market_watch

    return market_watch.list_watches()


# --------------------------------------------------------------------------- refusals at add time


def test_wallet_add_without_a_key_names_the_key_and_saves_nothing(vault_home):
    out = _run("watch.add", {"kind": "wallet", "chain": "solana", "subject": SOL})
    assert not out.ok and out.status == "requirement_missing"
    assert "Helius, Alchemy or Solscan" in out.response_text and "Settings" in out.response_text
    assert "Nothing was saved" in out.response_text
    assert _rows() == []


def test_price_add_with_web_off_says_how_to_turn_it_on(vault_home):
    out = _run("watch.add", {"kind": "price", "chain": "solana", "subject": USDC_MINT, "rule": {"above": 0.5}})
    assert not out.ok and out.status == "requirement_missing"
    assert "web access is off" in out.response_text and "Turn on web access" in out.response_text
    assert _rows() == []


def test_a_bad_address_is_refused(vault_home, enable_web):
    out = _run("watch.add", {"kind": "price", "chain": "solana", "subject": "not-an-address", "rule": {"above": 1}})
    assert not out.ok and out.status == "bad_subject"
    assert "is not a Solana address" in out.response_text


def test_an_unknown_chain_is_refused(vault_home, enable_web):
    out = _run("watch.add", {"kind": "price", "chain": "cardano", "subject": SOL, "rule": {"above": 1}})
    assert not out.ok and out.status == "unknown_chain"
    assert "cardano" in out.response_text and "Nothing was saved" in out.response_text


def test_an_evm_address_without_a_chain_asks_which_chain(vault_home, enable_web):
    out = _run("watch.add", {"kind": "wallet", "subject": EVM})
    assert not out.ok and out.status == "chain_needed"
    assert "Which chain is it on?" in out.response_text


def test_a_chain_without_public_pool_prices_is_refused_for_a_price_watch(vault_home, enable_web):
    out = _run("watch.add", {"kind": "price", "chain": "robinhood", "subject": EVM, "rule": {"above": 1}})
    assert not out.ok and out.status == "unknown_chain"
    assert "Price watches cover" in out.response_text and "Robinhood Chain" in out.response_text


@pytest.mark.parametrize("rule", [
    {}, {"above": "abc"}, {"above": -1}, {"above": 1, "below": 2},
    {"move_pct": 150, "window": "1h"}, {"move_pct": -20, "window": "2h"}, {"move_pct": -20},
])
def test_a_rule_that_is_not_one_clear_condition_is_refused(vault_home, enable_web, rule):
    out = _run("watch.add", {"kind": "price", "chain": "solana", "subject": SOL, "rule": rule})
    assert not out.ok and out.status == "invalid_rule", rule
    assert _rows() == []


def test_the_kind_and_the_subject_must_be_there(vault_home, enable_web):
    assert _run("watch.add", {"chain": "solana", "subject": SOL}).status == "missing_kind"
    assert _run("watch.add", {"kind": "price", "chain": "solana", "rule": {"above": 1}}).status == "missing_subject"
    assert _run("watch.add", {"kind": "portfolio", "chain": "solana", "subject": SOL}).status == "invalid_kind"


# --------------------------------------------------------------------------- adding and the list


def test_a_wallet_add_with_a_saved_key_needs_no_network(vault_home):
    _store_helius()
    out = _run("watch.add", {"kind": "wallet", "chain": "solana", "subject": SOL, "label": "Dev fund"})
    assert out.ok, out.response_text
    [watch] = _rows()
    assert watch["state"] == "active" and watch["label"] == "Dev fund"
    assert watch["last_seen"] == {}  # no poll has run: the baseline is still ahead
    assert "Now watching" in out.response_text


def test_a_price_add_stores_the_rule_and_a_short_default_label(vault_home, enable_web):
    out = _run("watch.add", {"kind": "price", "chain": "solana", "subject": USDC_MINT, "rule": {"below": 0.02}})
    assert out.ok, out.response_text
    [watch] = _rows()
    assert watch["rule"] == {"below": 0.02, "repeat": False}
    assert watch["label"].startswith("EPjF") and "…" in watch["label"]


def test_an_evm_subject_is_saved_in_one_case(vault_home, enable_web):
    checksummed = "0xDE0B295669A9FD93D5f28d9eC85E40F4CB697BAe"
    out = _run("watch.add", {"kind": "price", "chain": "ethereum", "subject": checksummed, "rule": {"above": 1}})
    assert out.ok, out.response_text
    assert _rows()[0]["subject"] == EVM


def test_adding_the_same_subject_again_updates_instead_of_duplicating(vault_home):
    _store_helius()
    assert _run("watch.add", {"kind": "wallet", "chain": "solana", "subject": SOL, "label": "One"}).ok
    out = _run("watch.add", {"kind": "wallet", "chain": "solana", "subject": SOL, "label": "Two"})
    assert out.ok and "Updated the watch" in out.response_text
    [watch] = _rows()
    assert watch["label"] == "Two"


def test_the_cap_is_fifty_watches(vault_home, enable_web):
    from core import market_watch

    _alphabet = "abcdefghijkmnopqrstuvwxyz"  # base58 lowercase, no 'l'

    def subject(i: int) -> str:
        # 44 base58 characters, distinct per row
        return ("w" + _alphabet[i // 23] + _alphabet[i % 23]).ljust(44, "q")

    for i in range(market_watch.MAX_WATCHES):
        assert _run("watch.add", {"kind": "price", "chain": "solana", "subject": subject(i), "label": f"w{i}",
                                  "rule": {"above": i + 1}}).ok
    out = _run("watch.add", {"kind": "price", "chain": "solana", "subject": USDC_MINT, "rule": {"above": 1}})
    assert not out.ok and out.status == "too_many" and "50" in out.response_text
    assert len(_rows()) == market_watch.MAX_WATCHES


def test_list_answers_what_am_i_watching(vault_home, enable_web):
    _run("watch.add", {"kind": "price", "chain": "solana", "subject": USDC_MINT, "label": "USDC",
                       "rule": {"move_pct": -10, "window": "1h", "repeat": True}})
    out = _run("watch.list", {})
    assert out.ok, out.response_text
    assert "1 watch(es)" in out.response_text and "USDC on Solana" in out.response_text
    assert "drops 10% within 1h every time" in out.response_text and "active" in out.response_text


def test_the_empty_list_says_how_to_begin(vault_home):
    out = _run("watch.list", {})
    assert out.ok
    assert "Nothing is being watched" in out.response_text
    assert "watch this wallet" in out.response_text


# --------------------------------------------------------------------------- pause, resume, remove


def test_pause_resume_and_remove_by_id_or_subject(vault_home):
    _store_helius()
    _run("watch.add", {"kind": "wallet", "chain": "solana", "subject": SOL, "label": "Dev fund"})
    watch_id = _rows()[0]["id"]
    out = _run("watch.pause", {"id": watch_id})
    assert out.ok and _rows()[0]["state"] == "paused"
    assert "Paused the watch" in out.response_text
    out = _run("watch.resume", {"subject": SOL})
    assert out.ok and _rows()[0]["state"] == "active"
    out = _run("watch.remove", {"subject": SOL})
    assert out.ok and "Stopped watching" in out.response_text
    assert _rows() == []


def test_remove_deletes_the_rows(vault_home):
    from storage.db import get_connection

    _store_helius()
    _run("watch.add", {"kind": "wallet", "chain": "solana", "subject": SOL})
    watch_id = _rows()[0]["id"]
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO market_watch_events (id, watch_id, at, kind, facts_json, notification_id) VALUES (?, ?, ?, ?, ?, ?)",
            ("mwe-fixture", watch_id, "2026-10-01T00:00:00+00:00", "wallet_tx", "{}", "ntf-fixture"),
        )
        conn.commit()
    finally:
        conn.close()
    assert _run("watch.remove", {"id": watch_id}).ok
    conn = get_connection()
    try:
        assert conn.execute("SELECT COUNT(*) FROM market_watches").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM market_watch_events").fetchone()[0] == 0
    finally:
        conn.close()


def test_removing_something_not_watched_says_so(vault_home):
    out = _run("watch.remove", {"id": "mw-nothere"})
    assert not out.ok and out.status == "unknown_watch"
    assert "no such watch" in out.response_text


def test_resume_without_the_requirement_says_what_to_do(vault_home):
    _store_helius()
    _run("watch.add", {"kind": "wallet", "chain": "solana", "subject": SOL})
    from core.credential_store import delete_credential

    assert delete_credential("chain.data.helius")
    _run("watch.pause", {"subject": SOL})
    out = _run("watch.resume", {"subject": SOL})
    assert not out.ok and out.status == "requirement_missing"
    assert "Settings" in out.response_text
    assert _rows()[0]["state"] == "needs_setup"


# --------------------------------------------------------------------------- contracts and seating


def test_the_watch_contracts_declare_a_local_write_with_no_approval():
    from core.runtime_tool_contracts import runtime_tool_contract_map

    contracts = runtime_tool_contract_map()
    for intent in ("watch.add", "watch.list", "watch.remove", "watch.pause", "watch.resume"):
        contract = contracts[intent]
        assert contract.side_effect_class == "creative_state"
        assert contract.approval_requirement == "none"
        assert contract.capability_id == "market.watch"


@pytest.mark.parametrize(("text", "seated"), [
    ("watch this wallet 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU on solana", "watch.add"),
    ("tell me when BONK goes above 0.00005", "watch.add"),
    ("alert me if FDOG drops 20% in an hour", "watch.add"),
    ("what am I watching", "watch.list"),
    ("stop watching that wallet", "watch.remove"),
    ("pause the watch on my team wallet", "watch.pause"),
    ("resume the FDOG watch", "watch.resume"),
])
def test_a_plain_sentence_names_its_watch_tool(text, seated):
    from core.tool_demand_signals import resolve_demand_signals

    assert seated in resolve_demand_signals(text).explicit_intents


@pytest.mark.parametrize("text", [
    "watch this movie tonight",
    "how did the meeting go",
    "what are you doing",
])
def test_plain_prose_seats_no_watch_tool(text):
    from core.tool_demand_signals import resolve_demand_signals

    assert not any(intent.startswith("watch.") for intent in resolve_demand_signals(text).explicit_intents)


def test_the_offer_seats_the_watch_tool_for_a_plain_sentence():
    from core.capability_graph import model_visible_specs

    specs = model_visible_specs(user_text="watch this wallet 7xKXtg2CW87d97TXJSDpbD5jBkheTqA83TZRuJosgAsU on solana")
    intents = {str(spec.get("intent") or "") for spec in specs}
    assert "watch.add" in intents


def test_rules_in_words():
    from core.market_watch import rule_in_words

    assert rule_in_words({"above": 0.5, "repeat": False}) == "goes above 0.5"
    assert rule_in_words({"below": 0.02, "repeat": False}) == "drops below 0.02"
    assert rule_in_words({"move_pct": -20, "window": "1h", "repeat": True}) == "drops 20% within 1h every time"
    assert rule_in_words({"move_pct": 15, "window": "6h", "repeat": False}) == "rises 15% within 6h"


@pytest.mark.parametrize("text", [
    "Can you track my package to this address?",
    "alert me when my mom calls",
    "Notify me if the deploy goes red",
    "tell me if the build drops",
    "track the price of my flight",
    "watch the contract changes in this PR",
    "Stop watching TV",
    "remove the alert from the dashboard code",
    "pause the alert sound",
    "Stop and watch this video",
])
def test_ordinary_sentences_with_watch_words_seat_no_watch_tool(text):
    from core.tool_demand_signals import resolve_demand_signals

    assert not any(intent.startswith("watch.") for intent in resolve_demand_signals(text).explicit_intents)


@pytest.mark.parametrize(("text", "intent"), [
    ("tell me when BONK goes above 0.00005", "watch.add"),
    ("alert me if FDOG drops 20% in an hour", "watch.add"),
    ("stop watching that wallet", "watch.remove"),
])
def test_a_seated_sentence_puts_its_watch_tool_in_the_offer(text, intent):
    from core.capability_graph import model_visible_specs

    intents = {str(spec.get("intent") or "") for spec in model_visible_specs(user_text=text)}
    assert intent in intents


def test_a_private_chat_refuses_to_add_a_watch_and_saves_nothing(vault_home):
    from core.private_chat import set_private

    _store_helius()
    assert set_private("chat-watch-private", True)
    try:
        from core.runtime_execution_tools import execute_runtime_tool

        out = execute_runtime_tool("watch.add", {"kind": "wallet", "chain": "solana", "subject": SOL},
                                   source_context={"chat_id": "chat-watch-private"})
    finally:
        set_private("chat-watch-private", False)
    assert not out.ok and out.status == "private_chat"
    assert "Private chat is on" in out.response_text and "turn recording back on" in out.response_text
    assert _rows() == []
    # the same words in an ordinary chat save the watch
    assert _run("watch.add", {"kind": "wallet", "chain": "solana", "subject": SOL}).ok
