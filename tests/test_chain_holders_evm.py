"""EVM token holders through a keyed provider (Moralis), beside the unchanged Solana answer.

Loopback fixtures scripted with each provider's documented request and answer shapes; the real hosts
are never contacted. Every lookup goes through the model's door (``execute_runtime_tool``).
"""
from __future__ import annotations

import dataclasses

import pytest

from tests._credential_intelligence_support import FakeProviderServer, isolated_home, vault_home

MORALIS_KEY = "eyJhbGciOiJIUzI1NiJ9.fixture.moralis-not-real"
HELIUS_KEY = "1b2c3d4e-5f60-4a7b-8c9d-0e1f2a3b4c5d"
# WETH on Ethereum mainnet: a well-known public contract, not a person's wallet.
WETH = "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
H1 = "0x1111111111111111111111111111111111111111"
H2 = "0x2222222222222222222222222222222222222222"
H3 = "0x3333333333333333333333333333333333333333"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
TA1 = "acct1aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"
TA2 = "acct2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa2"


def _owners_page(items: list[dict]) -> dict:
    return {"cursor": None, "pageCount": 1, "total": len(items), "result": items}


# Moralis' documented owners item: ``balance`` in raw base units, ``balance_formatted`` divided by decimals
OWNERS = _owners_page([
    {"balance": "487337153594364705095712", "balance_formatted": "487337.153594364705095712", "is_contract": False,
     "owner_address": H1, "owner_address_label": None, "entity": None, "entity_logo": None,
     "usd_value": "1468345622.53", "percentage_relative_to_total_supply": 4.8733},
    {"balance": "120500000000000000000", "balance_formatted": "120.5", "is_contract": False,
     "owner_address": H2, "owner_address_label": None, "entity": None, "entity_logo": None,
     "usd_value": "363064.5", "percentage_relative_to_total_supply": 0.0012},
    {"balance": "750000000000000000", "balance_formatted": "0.75", "is_contract": True,
     "owner_address": H3, "owner_address_label": None, "entity": None, "entity_logo": None,
     "usd_value": "2259.75", "percentage_relative_to_total_supply": 0.0000075},
])


@pytest.fixture
def moralis(monkeypatch):
    """Point the Moralis reads at one loopback fake answering the documented owners shape."""
    from core import chain_providers as cp

    def install(server: FakeProviderServer) -> None:
        base = server.url.rstrip("/")
        monkeypatch.setattr(cp, "MORALIS_API_ORIGIN", f"{base}/moralis/api/v2.2")
        patched = dict(cp.CHAIN_PROVIDERS)
        patched["moralis"] = dataclasses.replace(patched["moralis"], verify_url=f"{base}/moralis/api/v2.2/erc20/{WETH}/owners")
        monkeypatch.setattr(cp, "CHAIN_PROVIDERS", patched)

    return install


def _lookup(**arguments):
    from core.runtime_execution_tools import execute_runtime_tool

    return execute_runtime_tool("web.chain_lookup", arguments, source_context={})


def _store(slot: str, key: str) -> None:
    from core.credential_store import store_credential

    store_credential(slot, key, label="fixture")


# --------------------------------------------------------------------------- no key / wrong chain

def test_holders_with_no_key_names_the_provider_to_add(vault_home, moralis):
    with FakeProviderServer([]) as server:
        moralis(server)
        out = _lookup(query="holders", chain="ethereum", token=WETH)
        assert not out.ok and out.status == "key_missing"
        assert "Moralis key" in out.response_text and "Settings" in out.response_text
        assert server.requests == []  # nothing went online


def test_a_chain_no_holders_provider_serves_says_so(vault_home):
    out = _lookup(query="holders", chain="robinhood", token=WETH)
    assert not out.ok and out.status == "not_offered"
    assert "token holders on Robinhood Chain" in out.response_text


def test_a_bare_evm_address_names_every_chain_it_could_be_on(vault_home):
    out = _lookup(query="holders", token=WETH)
    assert not out.ok and out.status == "chain_needed"
    assert "Arbitrum" in out.response_text and "Ethereum" in out.response_text


# --------------------------------------------------------------------------- the read

def test_holders_with_a_key_ranked_rows_and_the_explorer_link(vault_home, moralis):
    _store("chain.data.moralis", MORALIS_KEY)
    with FakeProviderServer([(200, OWNERS)]) as server:
        moralis(server)
        out = _lookup(query="holders", chain="ethereum", token=WETH, limit=3)
        assert out.ok, out.response_text
        [req] = server.requests
        assert req["path"].endswith(f"/erc20/{WETH.lower()}/owners")
        assert "chain=eth" in req["query"] and "limit=3" in req["query"]
        # the key rides the X-API-Key header, never the URL
        assert req["header_sha256"].get("x-api-key") and MORALIS_KEY not in req["query"]
        assert "Source: Moralis, Ethereum." in out.response_text
        rows = out.details["rows"]
        assert [(r["rank"], r["address"]) for r in rows] == [(1, H1), (2, H2), (3, H3)]
        assert rows[0]["amount"] == "487337.153594364705095712" and rows[0]["percent"] == "4.87"
        assert "Top 3 holder(s)" in out.response_text and "held by 0x1111111111111111111111111111111111111111" in out.response_text
        assert "(4.87% of supply)" in out.response_text
        assert f"https://etherscan.io/address/{WETH.lower()}" in out.response_text


def test_holders_on_arbitrum_use_its_own_chain_name_and_explorer(vault_home, moralis):
    _store("chain.data.moralis", MORALIS_KEY)
    with FakeProviderServer([(200, OWNERS)]) as server:
        moralis(server)
        out = _lookup(query="top holders", chain="arbitrum", token=WETH)
        assert out.ok, out.response_text
        assert "chain=arbitrum" in server.requests[0]["query"]
        assert "on Arbitrum" in out.response_text and f"https://arbiscan.io/address/{WETH.lower()}" in out.response_text
        assert "Source: Moralis, Arbitrum." in out.response_text


def test_a_free_plan_used_up_answers_a_named_refusal_with_no_numbers(vault_home, moralis):
    _store("chain.data.moralis", MORALIS_KEY)
    with FakeProviderServer([(429, {"message": "rate limit exceeded"})]) as server:
        moralis(server)
        out = _lookup(query="holders", chain="base", token=WETH)
        assert not out.ok and out.status == "rate_limited"
        assert "daily allowance" in out.response_text and "Moralis" in out.response_text
        assert "0x" not in out.response_text and "%" not in out.response_text  # no figures from a refused read


def test_a_rejected_key_is_never_confused_with_a_plan_problem(vault_home, moralis):
    _store("chain.data.moralis", MORALIS_KEY)
    with FakeProviderServer([(401, {"message": "Invalid API key"})]) as server:
        moralis(server)
        out = _lookup(query="holders", chain="ethereum", token=WETH)
        assert not out.ok and out.status == "key_rejected"
        assert "rejected the saved key" in out.response_text and "Settings" in out.response_text


def test_a_free_plan_whose_daily_allowance_is_used_up_never_reads_as_a_rejected_key(vault_home, moralis):
    # Moralis answers a used-up free allowance with a 401, not a 429: the key is good and must be kept
    _store("chain.data.moralis", MORALIS_KEY)
    body = {"message": "Validation service blocked: Your plan: free-plan-daily total included usage has been consumed, please upgrade your plan"}
    with FakeProviderServer([(401, body)]) as server:
        moralis(server)
        out = _lookup(query="holders", chain="ethereum", token=WETH)
        assert not out.ok and out.status == "quota_exhausted", out.response_text
        assert "keep it" in out.response_text and "rejected" not in out.response_text
        assert "0x" not in out.response_text and "%" not in out.response_text


def test_holder_amounts_are_the_formatted_figure_never_raw_base_units(vault_home, moralis):
    _store("chain.data.moralis", MORALIS_KEY)
    raw_only = _owners_page([{k: v for k, v in OWNERS["result"][0].items() if k != "balance_formatted"}])
    with FakeProviderServer([(200, raw_only)]) as server:
        moralis(server)
        out = _lookup(query="holders", chain="ethereum", token=WETH)
        # without the formatted figure nothing is shown: a raw integer would read as 10^18 times the holding
        assert not out.ok and out.status == "bad_answer"
        assert "487337153594364705095712" not in out.response_text


def test_an_answer_in_an_unknown_shape_shows_no_figures(vault_home, moralis):
    _store("chain.data.moralis", MORALIS_KEY)
    with FakeProviderServer([(200, {"unexpected": True})]) as server:
        moralis(server)
        out = _lookup(query="holders", chain="ethereum", token=WETH)
        assert not out.ok and out.status == "bad_answer"
        assert "shape VOOL does not recognise" in out.response_text


# --------------------------------------------------------------------------- Solana unchanged

def test_solana_holders_answer_is_unchanged(vault_home, monkeypatch):
    from core import chain_providers as cp

    supply = {"jsonrpc": "2.0", "id": 0, "result": {"value": {"amount": "1000000", "decimals": 6}}}
    largest = {"jsonrpc": "2.0", "id": 1, "result": {"value": [
        {"address": TA1, "amount": "400000"}, {"address": TA2, "amount": "100000"}]}}
    _store("chain.data.helius", HELIUS_KEY)
    with FakeProviderServer([(200, supply), (200, largest)]) as server:
        monkeypatch.setattr(cp, "HELIUS_RPC_URL", server.url.rstrip("/") + "/helius/")
        out = _lookup(query="holders", token=USDC_MINT)
        assert out.ok, out.response_text
        assert "Largest 2 token account(s)" in out.response_text
        assert "(40.00% of supply) in token account " + TA1 in out.response_text
        assert out.details["rows"][0] == {"token_account": TA1, "amount": "0.4", "percent": "40.00"}
        assert "Source: Helius, Solana." in out.response_text


# --------------------------------------------------------------------------- Settings rendering

def test_settings_picks_the_new_provider_up_from_chain_providers():
    from core.provider_key_guide import provider_key_guide

    chain = next(group for group in provider_key_guide("en") if group["id"] == "chain")
    row = next(p for p in chain["providers"] if p["provider"] == "moralis")
    assert row["slot"] == "chain.data.moralis" and row["label"] == "Moralis"
    assert len(row["steps"]) == 3 and "admin.moralis.io" in row["steps"][0]
    assert "Ethereum" in row["description"] and "Arbitrum" in row["description"]


def test_the_served_settings_page_carries_the_provider_row():
    from core.vool_settings_page import render_vool_settings_html

    html = render_vool_settings_html()
    assert "Moralis" in html and "admin.moralis.io" in html


def test_the_setup_text_is_translated_not_copied():
    from core.i18n.catalog import catalog_for

    en = catalog_for("en").text("keyguide.provider.moralis.step1")
    de = catalog_for("de").text("keyguide.provider.moralis.step1")
    ja = catalog_for("ja").text("keyguide.provider.moralis.step1")
    assert "admin.moralis.io" in en
    assert de != en and "admin.moralis.io" in de and "Melde dich" in de
    assert ja != en and "サインイン" in ja
