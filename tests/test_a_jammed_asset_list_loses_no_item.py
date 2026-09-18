"""A coordinated asset list keeps every member, however the user typed the separators.

Measured live at 5e39b9de: "ok price bnb usdt and TRX" produced a live-data plan with ONE
subtask (market:binancecoin); USDT and TRX disappeared before execution -- no failed subtasks,
no unresolved rows, they simply ceased to exist. The same turn shape with different separators
("price of eth and btc now? also price of SOL") preserved 3/3.

The death point was the authorization walk in `core.semantic_claim_authority`: a price
predicate distributes over a coordinated list, but the list-binding rule only walked through
coordinators and function words, so a jammed (asyndetic) list -- items with no separators,
the ordinary way to type one -- treated each resolved member after the first as a
chain-breaking content word and unauthorized it. The domain's own membership authorities (the
curated table and the rank-bounded coin index) had already proven all three tokens; the
binding grammar ignored that proof. The between-check now also passes through tokens the
domain itself proves are fellow list members. No asset names anywhere in the repair: it is
about what a list IS, not about which tickers exist.

The coin index is network-fetched with a disk cache, so these tests inject one -- the same
pattern as `tests/test_an_unlisted_ticker_is_resolved_not_dropped.py`.
"""
from __future__ import annotations

import time

import pytest

from core.agent_runtime.fast_live_info_price import price_assets_named
from core.agent_runtime.live_data_plan import build_live_data_plan
from core.semantic_claim_authority import mention_is_market_authorized
from tools.web import coin_index
from tools.web.web_research import _looks_like_price_query_all

_FAKE_INDEX = {
    "bnb": "binancecoin",
    "usdt": "tether",
    "trx": "tron",
    "btc": "bitcoin",
    "eth": "ethereum",
    "sol": "solana",
    "sui": "sui",
    "tia": "celestia",
    "real": "reallink",
    "ton": "the-open-network",
}

_THREE_IDS = ["binancecoin", "tether", "tron"]

HUMAN_WORDING = "ok price bnb usdt and TRX"

PARAPHRASES = [
    "price bnb usdt trx please",
    "what is the price of bnb, usdt and trx",
    "bnb usdt trx price now",
    "how much are bnb and usdt and trx right now",
    "check prices: bnb usdt trx",
]

SLOPPY_VARIANTS = [
    "price bnb  usdt   trx",
    "price of bnb usdt, and trx?",
    "whats the price for bnb usdt trx rn",
    "price bnb usdt trx!!",
    "price bnb,usdt,trx",
]

# A jammed list of CURATED aliases needs no index at all -- the pass-through must work there too.
CURATED_JAMMED = [
    "price btc eth sol",
    "price of btc, eth, sol",
    "btc eth sol price please",
    "how much is btc eth and sol today",
    "price btc and eth and sol",
]

NEGATIVE_CONTROLS = [
    # Measured G4 incident: a market WORD beside an index-resolved token is not a list.
    "three highly-rated, mid-priced dinner restaurants near me",
    # No market semantics at all.
    "Explain gold structure.",
    # A coordinator joining ordinary nouns is not an asset list.
    "the sky is clear and the grass is green",
]

ADVERSARIAL_NEAR_MISS = [
    # "ton" resolves in the index and is a unit; a weight question is not a quote request.
    "how many tons of gravel fit in a truck",
]


@pytest.fixture(autouse=True)
def injected_index(monkeypatch):
    coin_index.reset_cache_for_test()
    monkeypatch.setattr(coin_index, "_memory_index", dict(_FAKE_INDEX), raising=False)
    monkeypatch.setattr(coin_index, "_memory_stamp", time.time(), raising=False)
    yield
    coin_index.reset_cache_for_test()


def _ids(text: str) -> list[str]:
    return _looks_like_price_query_all(text)


@pytest.mark.parametrize("text", [HUMAN_WORDING, *PARAPHRASES, *SLOPPY_VARIANTS])
def test_a_jammed_list_preserves_every_item(text: str) -> None:
    assert _ids(text) == _THREE_IDS


@pytest.mark.parametrize("text", CURATED_JAMMED)
def test_a_jammed_curated_list_preserves_every_item(text: str) -> None:
    assert _ids(text) == ["bitcoin", "ethereum", "solana"]


def test_the_live_data_plan_gets_every_subtask() -> None:
    plan = build_live_data_plan(HUMAN_WORDING, plan_id="p", attempt_id="a", source_context=None)
    assert plan is not None
    assert len(plan.subtasks) == 3


@pytest.mark.parametrize("text", NEGATIVE_CONTROLS + ADVERSARIAL_NEAR_MISS)
def test_no_list_is_invented_where_none_exists(text: str) -> None:
    assert price_assets_named(text) == []
    assert _ids(text) == []


def test_the_rhymes_noun_is_not_a_list_member() -> None:
    """"rain" resolves in the index and is ordinary English in THIS message: it is chained to
    "give", which is no asset, so the member pass-through must not reach it -- while the genuine
    BTC obligation in the same message is still served (the protected sibling test's case)."""
    text = "Tell me the weather in Vilnius, write a rhyme about rain, and give me BTC price"
    ids = _ids(text)
    assert "rain" not in ids
    assert "bitcoin" in ids


def test_positive_controls_keep_their_shape() -> None:
    # ETH + BTC + SOL -> exactly 3, unchanged.
    assert _ids("price of eth and btc now? also price of SOL") == ["ethereum", "bitcoin", "solana"]
    # A single-asset request still resolves.
    assert _ids("price of bnb") == ["binancecoin"]
    # Coordinator-linked pairs keep working.
    assert _ids("price of SUI and TIA") == ["sui", "celestia"]


def test_sabotage_without_member_passthrough_the_items_are_lost() -> None:
    """Reverting the between-check to function-words-only must lose USDT and TRX again."""
    import core.semantic_claim_authority as sca

    text = HUMAN_WORDING.lower()
    start_u, start_t = text.index("usdt"), text.index("trx")
    assert mention_is_market_authorized(text, start_u, start_u + 4, curated=False)
    assert mention_is_market_authorized(text, start_t, start_t + 3, curated=False)

    real = sca._only_list_fillers_between
    sca._only_list_fillers_between = lambda toks, lo, hi, members: False
    try:
        assert not mention_is_market_authorized(text, start_u, start_u + 4, curated=False)
        assert not mention_is_market_authorized(text, start_t, start_t + 3, curated=False)
    finally:
        sca._only_list_fillers_between = real
    assert mention_is_market_authorized(text, start_u, start_u + 4, curated=False)
