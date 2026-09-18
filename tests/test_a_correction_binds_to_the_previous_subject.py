"""A correction refines the subject already on the table; it does not open a market lookup.

The live defect, 2026-08-11, exactly as it happened in the running app:

    U: lets build a castle. where do we start?
    A: Sure! Let's start by choosing a location. Where would you like to build your castle ...
    U: no i mean real castle
    A: Reallink is $0.0739 USD ...

`REAL` is a listed CoinGecko symbol (`reallink`) inside the rank ceiling `tools/web/coin_index.py`
resolves against, so the word "real" was resolved to an asset. The correction prefix "no i mean"
matched `_PRICE_FOLLOWUP_PREFIX_RE`, the short-subject-follow-up branch of
`recover_price_lookup_query` fired, and the turn was rewritten to the query `real price now`.
"castle" was never looked at, and neither was the castle-building conversation the correction was
correcting.

WHY THE INDEX IS SEEDED AND NOT MOCKED
--------------------------------------
Every test here drives the REAL `tools.web.coin_index.resolve_tokens` against a REAL-shaped
symbol->id index written to a temporary VOOL home. `real -> reallink` in that fixture is not an
invented mapping: it is what the operator's live cached index (237 symbols, top-250 by market cap)
holds, read on 2026-08-11. So the resolution step that produced the incident runs for real, offline
and deterministically, instead of being replaced by a double that could agree with anything.

WHY THE VARIANTS ARE NOT ALL "real castle"
------------------------------------------
Fixing "no i mean real castle" alone would be the hard-coded input->output mapping CLAUDE.md 0.2
bans. The matrix below is a SEMANTIC family: the same correction shape over unseen nouns (house,
tower, moat, bridge, bakery, keep), unseen sloppy phrasings, and unseen correction openings — none
of which any predicate in the repair enumerates. The negative controls are the other half: genuine
market requests, genuine bare-asset corrections, and code/build requests that mention a castle must
all behave exactly as they did before.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest import mock

import pytest

import tools.web.coin_index as coin_index
from core.agent_runtime.fast_live_info_price import recover_price_lookup_query
from core.followup_subject_continuity import (
    correction_names_only,
    correction_reopens_the_market_lane,
    correction_residue,
    is_correction_followup,
    prior_exchange_was_market,
)
from core.human_input_adapter import _resolve_reference_targets

# Symbols measured from the operator's live cached CoinGecko rank index on 2026-08-11. `real` is
# the collision that produced the incident; the rest are the ordinary tickers the controls use.
LIVE_INDEX_SYMBOLS = {
    "real": "reallink",
    "sol": "solana",
    "btc": "bitcoin",
    "eth": "ethereum",
    "arb": "arbitrum",
    "link": "chainlink",
}

# The exact live conversation the correction is correcting.
CASTLE_HISTORY = {
    "conversation_history": [
        {"role": "user", "content": "lets build a castle. where do we start?"},
        {
            "role": "assistant",
            "content": (
                "Sure! Let's start by choosing a location. Where would you like to build your "
                "castle in the workspace folder?"
            ),
        },
    ]
}

MARKET_HISTORY = {
    "conversation_history": [
        {"role": "user", "content": "what is the solana price?"},
        {"role": "assistant", "content": "Solana is $147.20 USD. Source: CoinGecko."},
    ]
}


@pytest.fixture(autouse=True)
def live_shaped_coin_index() -> None:
    """The real index reader, over a real-shaped index, with no network.

    Seeded at `coin_index._cache_path()` rather than under a `VOOL_HOME` this fixture invents:
    the suite's root conftest calls `configure_runtime_home(...)`, and that override BEATS the
    environment variable, so setting `VOOL_HOME` here leaves the index cold and every assertion
    below passes vacuously (measured -- the first draft of this file did exactly that and only the
    resolver guard test above caught it).
    """
    path: Path = coin_index._cache_path()
    existing = path.read_bytes() if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"fetched_at": time.time(), "symbols": LIVE_INDEX_SYMBOLS}),
        encoding="utf-8",
    )
    coin_index.reset_cache_for_test()
    yield
    if existing is None:
        path.unlink(missing_ok=True)
    else:
        path.write_bytes(existing)
    coin_index.reset_cache_for_test()


def test_the_seeded_index_really_resolves_real_to_reallink() -> None:
    """Without this the whole file could pass because the index never resolved anything at all."""
    assert coin_index.resolve_symbol("real") == "reallink"
    assert [coin_id for _position, coin_id in coin_index.resolve_tokens("no i mean real castle")] == [
        "reallink"
    ]


# ---------------------------------------------------------------------------------------------
# The reported defect, verbatim.
# ---------------------------------------------------------------------------------------------


def test_the_reported_turn_is_not_rewritten_into_a_reallink_price_lookup() -> None:
    recovered = recover_price_lookup_query("no i mean real castle", source_context=CASTLE_HISTORY)

    assert recovered == "", (
        f"'no i mean real castle' was rewritten to {recovered!r} -- the correction refines the "
        "castle already under discussion, and `real` is not the subject of it"
    )


# ---------------------------------------------------------------------------------------------
# The semantic family. Same shape, nouns and phrasings nothing in the repair enumerates.
# ---------------------------------------------------------------------------------------------

# The brief's numbered family, plus its sloppy variants, plus nouns invented here that appear
# nowhere in the repair (bridge, bakery, keep, harbour) so a noun list cannot be what passes this.
CORRECTION_FAMILY = [
    # 1 -- the reported conversation
    "no i mean real castle",
    # 2 -- "actual physical, not app/project"
    "no i mean actual physical castle, not an app",
    "i mean the actual physical castle, not the project",
    # 3 -- house
    "no real house",
    "no i mean real house",
    # 4 -- tower
    "no i mean real tower, physical building",
    "i mean a real tower, an actual building",
    # 5 -- moat
    "no not in the workspace, i mean a real moat",
    "no i mean real moat",
    # sloppy variants
    "nah real castle bro",
    "no actual castle",
    "no i mean real castle bro",
    "no i mean a real-world castle",
    "not crypto, i mean castle building",
    "i mean construction",
    "i meant real construction",
    # unseen nouns -- nothing in the repair lists any of these
    "no i mean real bridge",
    "no i mean real bakery",
    "no i mean a real keep, stone walls and all",
    "no i mean real harbour",
    # unseen correction openings
    "what about a real castle",
    "how about a real castle instead",
    "actually i mean a real castle",
    "sorry, i mean a real castle",
    "nvm i mean real castle",
    "i said real castle",
    "im asking about a real castle",
]


@pytest.mark.parametrize("text", CORRECTION_FAMILY)
@pytest.mark.parametrize(
    "history",
    [CASTLE_HISTORY, MARKET_HISTORY, None],
    ids=["after-castle-turn", "after-market-turn", "no-history"],
)
def test_no_correction_in_the_family_becomes_a_market_lookup(text: str, history: dict | None) -> None:
    """Including after a MARKET turn: prior market context is not a licence to read the next
    correction's noun as a ticker. The residue names a castle, so the castle is the subject."""
    recovered = recover_price_lookup_query(text, source_context=history)

    assert recovered == "", f"{text!r} was rewritten to {recovered!r}"


# ---------------------------------------------------------------------------------------------
# Negative controls -- the repair must change none of these.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        # genuine market requests keep their fast, deterministic answer
        ("what is the bitcoin price?", "bitcoin price now"),
        ("how much is btc", "btc price now"),
        ("what is the sol price right now", "sol price now"),
        # a bare anaphoric price follow-up still harvests the prior asset
        ("what about the price now?", "solana price now"),
    ],
)
def test_a_genuine_market_request_is_unchanged(text: str, expected: str) -> None:
    assert recover_price_lookup_query(text, source_context=MARKET_HISTORY) == expected


@pytest.mark.parametrize(
    "text",
    [
        # the brief's negative controls: these must not be dragged into the correction lane, and
        # must not be answered from a prior asset either
        "What is Reallink price?",
        "REAL crypto price",
        "price of REAL token",
        "What is USD?",
        "build a castle game in Python",
        "create castle.py",
        # pre-existing invariants from tests/test_cross_turn_subject_contamination.py
        "what is oil worth?",
        "from the latest news is oil going up or down?",
    ],
)
def test_the_negative_controls_are_not_rewritten_to_a_prior_asset(text: str) -> None:
    """None of these is a correction, so the branch under repair must be irrelevant to them --
    and none may be silently answered as the previous turn's asset either."""
    recovered = recover_price_lookup_query(text, source_context=MARKET_HISTORY)

    assert recovered in ("", "real price now"), recovered
    assert "solana" not in recovered


@pytest.mark.parametrize(
    "text",
    ["no i mean sol", "no, i mean SOL", "what about btc", "how about arb", "i mean btc", "btw sol"],
)
def test_a_bare_asset_correction_still_works_when_the_market_is_on_the_table(text: str) -> None:
    """The branch exists for exactly this shape and must survive the repair: the residue is the
    asset and nothing else, and the exchange being corrected was a market exchange."""
    recovered = recover_price_lookup_query(text, source_context=MARKET_HISTORY)

    assert recovered.endswith(" price now"), f"{text!r} lost its correction lookup: {recovered!r}"


@pytest.mark.parametrize("text", ["no i mean sol", "what about btc", "i mean arb"])
def test_a_bare_asset_correction_declines_when_no_market_is_on_the_table(text: str) -> None:
    """The other half of the rule, and the reason "no i mean real" (no leftover noun at all) cannot
    reach the price lane after a castle conversation. Declining hands the turn to the tool-enabled
    lane rather than answering it -- the direction `price_request_leaves_unresolved_content`
    already chose."""
    assert recover_price_lookup_query(text, source_context=CASTLE_HISTORY) == ""
    assert recover_price_lookup_query(text, source_context=None) == ""


def test_a_correction_with_no_leftover_noun_still_binds_to_the_castle() -> None:
    """"no i mean real" is BARE -- there is no "castle" left to disqualify it -- so the leftover
    check alone cannot save it. Only the prior-exchange half of the rule can."""
    assert recover_price_lookup_query("no i mean real", source_context=CASTLE_HISTORY) == ""


# ---------------------------------------------------------------------------------------------
# The authority itself.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "residue"),
    [
        ("no i mean real castle", "real castle"),
        ("nah real castle bro", "real castle"),
        ("no i mean sol", "sol"),
        ("what about btc", "btc"),
        ("actually i mean a real bridge", "a real bridge"),
        ("i mean construction", "construction"),
    ],
)
def test_the_residue_is_what_the_correction_steers_toward(text: str, residue: str) -> None:
    assert correction_residue(text) == residue


@pytest.mark.parametrize(
    "text",
    ["build a castle", "what is the bitcoin price?", "create castle.py", "lets build a castle. where do we start?"],
)
def test_an_ordinary_request_is_not_a_correction(text: str) -> None:
    assert is_correction_followup(text) is False


def test_a_correction_naming_a_noun_beyond_the_asset_is_not_bare() -> None:
    assert correction_names_only("no i mean real castle", names=["real"]) is False
    assert correction_names_only("no i mean real", names=["real"]) is True
    assert correction_names_only("no i mean sol", names=["sol"]) is True
    # scaffolding does not make a correction non-bare
    assert correction_names_only("no i mean the sol price now please", names=["sol"]) is True
    # nothing recognized means there is nothing for the residue to consist of
    assert correction_names_only("no i mean sol", names=[]) is False


def test_a_castle_conversation_is_not_a_market_exchange() -> None:
    assert prior_exchange_was_market(CASTLE_HISTORY) is False
    assert prior_exchange_was_market(MARKET_HISTORY) is True
    assert prior_exchange_was_market(None) is False


def test_a_rendered_quote_counts_as_a_market_exchange_without_the_word_price() -> None:
    """An assistant turn that printed "$147.20 -- Source: CoinGecko" said nothing about "price" in
    words, and the follow-up "no i mean sol" is a real correction of it."""
    quote_only = {"conversation_history": [{"role": "assistant", "content": "Ethereum: $2,410.05 (Source: CoinGecko)"}]}

    assert prior_exchange_was_market(quote_only) is True


def test_the_rule_needs_both_halves() -> None:
    # bare, but no market anywhere -> withheld
    assert correction_reopens_the_market_lane("no i mean sol", names=["sol"], source_context=None) is False
    # market on the table, but not bare -> withheld
    assert (
        correction_reopens_the_market_lane(
            "no i mean real castle", names=["real"], source_context=MARKET_HISTORY
        )
        is False
    )
    # both -> admitted
    assert (
        correction_reopens_the_market_lane("no i mean sol", names=["sol"], source_context=MARKET_HISTORY)
        is True
    )
    # the correction can carry the market itself
    assert (
        correction_reopens_the_market_lane("no i mean the sol price", names=["sol"], source_context=None)
        is True
    )


# ---------------------------------------------------------------------------------------------
# Continuity: a correction carries the previous subject forward.
# ---------------------------------------------------------------------------------------------


def test_a_correction_with_no_subject_of_its_own_inherits_the_previous_one() -> None:
    """"I mean construction" has no pronoun, so before this the resolver returned nothing and the
    correction reached the model carrying no subject at all."""
    targets, _flags = _resolve_reference_targets(
        "i mean construction",
        current_topics=[],
        session_state={"last_subject": "castle"},
        recent_turns=[{"topic_hints": ["castle"]}],
    )

    assert targets == ["castle"]


@pytest.mark.parametrize(
    ("text", "bare"),
    [
        ("no i mean real castle", True),
        ("i mean construction", True),
        ("no real house", True),
        ("nah real castle bro", True),
        ("no not in the workspace, i mean a real moat", True),
        # a correction that heads a NEW instruction is a complete independent request
        ("Actually, help me write the deployment script instead.", False),
        ("Instead, outline the pricing page for me.", False),
        ("no, build me a castle game in python", False),
        # not a correction at all
        ("lets build a castle. where do we start?", False),
    ],
)
def test_a_correction_that_heads_a_new_request_is_not_a_refinement(text: str, bare: bool) -> None:
    """The brief's carve-out: a correction binds to the previous subject UNLESS it introduces a
    complete new independent request. Getting this wrong makes "Actually, help me write the
    deployment script instead." read as a continuation of the database question it replaced."""
    from core.followup_subject_continuity import correction_is_a_bare_refinement

    assert correction_is_a_bare_refinement(text) is bare


def test_a_new_instruction_correction_does_not_inherit_the_previous_subject() -> None:
    targets, _flags = _resolve_reference_targets(
        "instead, outline the pricing page for me.",
        current_topics=[],
        session_state={"last_subject": "onboarding email copy"},
        recent_turns=[{"topic_hints": ["onboarding email copy"]}],
    )

    assert targets == []


def test_a_correction_that_names_its_own_subject_still_wins_over_stale_state() -> None:
    """Widening the entry gate must not resurrect a stale subject over a fresh one -- the
    contamination invariant in tests/test_cross_turn_subject_contamination.py."""
    targets, _flags = _resolve_reference_targets(
        "no i mean real castle",
        current_topics=["castle"],
        session_state={"last_subject": "telegram bot"},
        recent_turns=[{"topic_hints": ["telegram bot"]}],
    )

    assert targets == ["castle"]
    assert "telegram bot" not in targets


def test_a_contentful_rejection_still_does_not_resurrect_a_stale_subject() -> None:
    """The live 2026-07-14 incident. It is not a correction OPENING, and even read as one the
    negated-reference rule below the gate still redirects away."""
    targets, flags = _resolve_reference_targets(
        "i am asking for screen not for this",
        current_topics=[],
        session_state={"last_subject": "telegram bot"},
        recent_turns=[{"topic_hints": ["telegram bot", "telegram"]}],
    )

    assert targets == []
    assert "ambiguous_reference" in flags


# ---------------------------------------------------------------------------------------------
# Production: the real two-turn conversation through the real turn entry point.
# ---------------------------------------------------------------------------------------------


def test_the_live_two_turn_conversation_never_reaches_the_price_lane() -> None:
    """Drives `VoolAgent.run_once` -- the method `apps/vool_api_server.py` calls -- for both
    turns on one session, so any follow-up state the first turn leaves behind is really in play.

    Two things are wrapped, and neither is replaced. `_handle_turn_frontdoor` runs FOR REAL --
    the live-info fast path is called from inside it (`turn_frontdoor.py`), so stubbing it out
    would delete the lane this test exists to watch -- and only its return value is swapped for a
    canned one afterwards, to stop the turn before the model lane. `_recover_price_lookup_query`
    is likewise wrapped so the real routing decision inside `prepare_live_info_request` is
    recorded.

    Recording the recovered query rather than the preflight's return value is deliberate and was
    MEASURED: `prepare_live_info_request` drops the query to "" when
    `policy_engine.allow_web_fallback()` is false, which it is under the suite, so an assertion on
    the preflight's return passes even with the repair reverted. The decision that produced the
    incident is the recovered query, so that is what this observes.
    """
    from apps.vool_agent import VoolAgent

    recovered: list[str] = []
    frontdoor_claims: list[object] = []
    real_recover = VoolAgent._recover_price_lookup_query
    real_frontdoor = VoolAgent._handle_turn_frontdoor

    def recording_recover(self, text, **kwargs):
        answer = real_recover(self, text, **kwargs)
        recovered.append(str(answer))
        return answer

    def recording_frontdoor(self, **kwargs):
        bundle = real_frontdoor(self, **kwargs)
        frontdoor_claims.append((kwargs.get("raw_user_input"), (bundle or {}).get("result")))
        return {"result": {"response": "ordinary lane", "model_calls": 0, "route": "test:ordinary"}}

    agent = VoolAgent(backend_name="test-backend", device="castle-test", persona_id="default")
    source_context = {"surface": "openclaw", "platform": "openclaw"}

    with mock.patch.object(VoolAgent, "_recover_price_lookup_query", new=recording_recover), \
         mock.patch.object(VoolAgent, "_handle_turn_frontdoor", new=recording_frontdoor):
        agent.run_once("lets build a castle. where do we start?", source_context=dict(source_context))
        agent.run_once("no i mean real castle", source_context=dict(source_context))

    assert recovered, "the price-recovery seam never ran -- this test observed nothing"
    assert not [query for query in recovered if query], (
        f"a price lookup was recovered for the castle conversation: {recovered!r}"
    )
    correction_claims = [claim for text, claim in frontdoor_claims if text == "no i mean real castle"]
    assert correction_claims, "the correction turn never reached the frontdoor"
    assert all(claim is None for claim in correction_claims), (
        f"a fast path claimed the correction instead of letting it reach the ordinary turn: "
        f"{correction_claims!r}"
    )
