"""A price word alone must not claim the market lane.

The defect, measured in the shipped app
---------------------------------------
    USER: How much is a flight from Vilnius to Rome next weekend?
    APP:  I couldn't map `is a flight from Vilnius to Rome next weekend` to a known traded asset
          or commodity quote. If you mean a stock, token, ETF, or product, give me the exact
          ticker or full name.

Two independent faults, and a test that asserted only the second would have passed on the first.

1. **A price marker alone claimed the lane.** "price", "cost", "worth", "rate" and "how much" are
   ordinary English for the cost of ANYTHING. `unresolved_price_lookup_response` gated its
   clarification on the alias lookup having FAILED -- reading "a price word appeared and no ticker
   resolved" as "you asked me for a quote and I could not resolve it", when it equally means "this
   was never an asset quote". The whole class is "any price word with no recognized asset": travel,
   hotels, taxis, rent, groceries.

2. **A display helper stood in as a claim predicate.** `looks_like_grounded_price_lookup` ended
   `or extract_price_lookup_subject(candidate)`. That helper strips price scaffolding by regex so a
   bare ticker can be echoed back; it returns non-empty for any sentence containing a content word,
   so it read as market evidence on every cost question in the language. It also supplied the text
   the app quoted, which is why the user saw their own sentence with its first two words deleted.

Both arms had to move together: with only the emitter gated, the turn was still claimed at
`fast_live_info_runtime_dispatch.py:40-47`. So every case below asserts BOTH seams.

Blast radius was never travel-specific. At the base, `looks_like_grounded_price_lookup` was already
carrying "how much is one ether in usd right now" -- a MARKET must-claim in
`tests/test_price_lane_does_not_claim_the_word_value.py` -- through the display-helper arm rather
than through any asset evidence, so that suite was green while the mechanism under it was broken.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_live_info_price import (
    bare_unresolved_asset_subject,
    extract_price_lookup_subject,
    looks_like_grounded_price_lookup,
    unresolved_price_lookup_response,
)

#: Ordinary cost questions. Every one was claimed by the market lane and answered with the mangled
#: clarification above; the shipped-app reproduction is first.
NOT_AN_ASSET_QUOTE = {
    "flight": "How much is a flight from Vilnius to Rome next weekend?",
    "return_ticket": (
        "Find me the cheapest return ticket Vilnius to London in October and tell me the airline "
        "and price."
    ),
    "train": "What does a train ticket from Berlin to Prague cost and how long does it take?",
    "hotel": "How much is a hotel in Rome for 3 nights?",
    "taxi": "What is the cost of a taxi from Rome FCO to Piazza Navona?",
    # Not in the reported corpus -- the same class, to keep the fix from being read as travel-only.
    "coffee": "how much does a cup of coffee cost in Vilnius",
    "rent": "how much is rent in Berlin",
    "haircut": "what does a haircut cost",
    "attraction": "what is the price of the eiffel tower ticket",
}

#: Genuine asset quotes. The lane must still claim and answer each one. The first six are the
#: reported must-keep corpus; "ether", "brent crude" and "Seth" are the ones no alias-table lookup
#: resolved at the base, so a naive "require a recognized asset" fix would have dropped them.
ASSET_QUOTE = {
    "ether": "how much is one ether in usd right now",
    "bitcoin": "price of bitcoin",
    "cardano": "market cap of cardano",
    "gold": "what is gold trading at",
    "brent": "barrel of brent crude cost",
    "seth": "what is Seth price?",
    "bitcoin_full": "what is the current price of bitcoin",
    "gold_ounce": "what is gold worth per ounce today",
    "solana": "what is solana trading at",
    "brent_barrel": "what does a barrel of brent crude cost right now",
    "unlisted_ticker": "what is ARB worth",
    "bare_ticker": "SUI price?",
}


def _clarification(prompt: str) -> str:
    return unresolved_price_lookup_response(query=prompt, notes=[], mode="fresh_lookup")


@pytest.mark.parametrize("name", sorted(NOT_AN_ASSET_QUOTE))
def test_an_ordinary_cost_question_is_claimed_by_neither_price_seam(name: str) -> None:
    """BOTH seams, in one test, because either one alone still ships the defect.

    `looks_like_grounded_price_lookup` decides whether the fast path keeps a note-less fresh lookup
    instead of deferring to the reasoning lane; `unresolved_price_lookup_response` writes the text
    the user actually reads. The existing suite asserted the first kind of thing and stayed green
    through this bug, so the second assertion is the one that names the cause.
    """

    prompt = NOT_AN_ASSET_QUOTE[name]
    assert looks_like_grounded_price_lookup(prompt) is False, (
        f"{name}: the market lane claimed an ordinary cost question. It would hold the turn at "
        "fast_live_info_runtime_dispatch instead of letting the research lane answer it."
    )
    assert _clarification(prompt) == "", (
        f"{name}: the market lane answered an ordinary cost question with its "
        "'give me the exact ticker' clarification. This was never an asset quote."
    )


def test_the_shipped_app_reproduction_no_longer_produces_its_mangled_echo() -> None:
    """The exact user-visible string, asserted as a string.

    `extract_price_lookup_subject` really does still mangle this sentence -- it is a display helper
    with no grammar behind it. The fix is that nothing routes on it or quotes it, not that it was
    taught English.
    """

    prompt = NOT_AN_ASSET_QUOTE["flight"]
    assert extract_price_lookup_subject(prompt) == "is a flight from Vilnius to Rome next weekend"
    assert "is a flight" not in _clarification(prompt)
    assert _clarification(prompt) == ""


@pytest.mark.parametrize("name", sorted(ASSET_QUOTE))
def test_every_genuine_asset_quote_is_still_claimed(name: str) -> None:
    prompt = ASSET_QUOTE[name]
    assert looks_like_grounded_price_lookup(prompt) is True, (
        f"{name}: the market lane stopped claiming a real asset quote"
    )


def test_an_unresolvable_asset_subject_still_gets_its_clarification_and_is_quoted_verbatim() -> None:
    """Decision on the "Seth price" boundary case: KEPT, and kept structurally.

    "what is Seth price?" is a real asset quote whose ticker this runtime cannot resolve, and
    "give me the exact ticker or full name" is the right answer to it. It is locked by
    tests/test_vool_web_freshness_and_lookup.py::
    test_ambiguous_price_lookup_fails_honestly_instead_of_answering_biography, and dropping it to
    make the flight case easier would have removed a working capability.

    It is distinguishable without naming it: the entire request reduces to ONE content token
    sitting in asset position. A flight question does not. And the word quoted back is now the
    user's own, in the user's own casing.
    """

    body = _clarification("what is Seth price?")
    assert "`Seth`" in body, f"the subject must be quoted verbatim, got: {body!r}"
    assert "exact ticker or full name" in body
    assert bare_unresolved_asset_subject("what is Seth price?") == "Seth"


def test_one_bound_market_word_is_not_enough_on_its_own() -> None:
    """Anti-vacuity control for the "reduces to one content token" condition.

    `mention_is_market_authorized` is the runtime's per-mention authority and it is genuinely
    satisfied here: "Prague cost" puts a market term directly beside the mention with nothing
    between, which is the same syntax as "NEAR price". A fix built on that authority ALONE would
    therefore have claimed the train question. Measured, not assumed -- the first assertion is what
    makes the second one mean something.
    """

    from core.semantic_claim_authority import mention_is_market_authorized

    prompt = "what does a train ticket from berlin to prague cost and how long does it take?"
    start = prompt.index("prague")
    assert mention_is_market_authorized(prompt, start, start + len("prague"), curated=False) is True
    assert bare_unresolved_asset_subject(prompt) == ""
    assert looks_like_grounded_price_lookup(prompt) is False


def test_the_real_dispatcher_hands_a_cost_question_to_the_reasoning_lane(make_agent) -> None:
    """Both seams together, through the function that actually produced the shipped output.

    `build_live_info_response_result` is what the app ran: it asks the emitter first (line 31), and
    if that stays quiet it asks the claim predicate (line 42) before deciding whether a note-less
    fresh lookup stays in the fast path. Gating only one of the two leaves the turn claimed here,
    which is why this drives the dispatcher rather than the two predicates in isolation. The state
    is the app's own -- fresh_lookup mode, no notes -- and the agent is the real one.
    """

    from core.agent_runtime.fast_live_info_runtime_dispatch import build_live_info_response_result

    agent = make_agent()

    def dispatch(prompt: str):
        return build_live_info_response_result(
            agent,
            session_id="price-lane-claim",
            user_input=prompt,
            query=prompt,
            live_mode="fresh_lookup",
            notes=[],
            source_context={"surface": "openclaw", "platform": "openclaw"},
            interpretation=None,
            response_class=None,
        )

    assert dispatch(NOT_AN_ASSET_QUOTE["flight"]) is None, (
        "the fast path kept a flight-cost question instead of deferring to the reasoning lane"
    )
    for name in ("hotel", "taxi", "train", "return_ticket"):
        assert dispatch(NOT_AN_ASSET_QUOTE[name]) is None, name

    # And the lane still answers the request it exists for, through the same dispatcher.
    kept = dispatch("what is Seth price?")
    assert kept is not None
    assert "couldn't map `Seth`" in str(kept["response"])


def test_sabotage_restoring_the_display_helper_as_a_claim_predicate_reproduces_both_faults() -> None:
    """Revert the fix at its seam and require the reported defect back, in both seams at once.

    Putting `extract_price_lookup_subject` back in the evidence position is exactly what the base
    did. If this does not bite, the assertions above are passing for some other reason.
    """

    from core.agent_runtime import fast_live_info_price as mod

    prompt = NOT_AN_ASSET_QUOTE["flight"]
    original = mod.bare_unresolved_asset_subject
    mod.bare_unresolved_asset_subject = mod.extract_price_lookup_subject
    try:
        claimed = looks_like_grounded_price_lookup(prompt)
        echoed = _clarification(prompt)
    finally:
        mod.bare_unresolved_asset_subject = original

    assert claimed is True, (
        "SABOTAGE DID NOT BITE: with the display helper back in the evidence position the market "
        "lane must claim the flight question again."
    )
    assert "is a flight from Vilnius to Rome next weekend" in echoed, (
        "SABOTAGE DID NOT BITE: with the display helper back, the app must quote the user's "
        f"sentence with its first two words deleted again. Got: {echoed!r}"
    )
    # And the restore must not have leaked into the rest of the suite.
    assert looks_like_grounded_price_lookup(prompt) is False
    assert _clarification(prompt) == ""


def test_sabotage_neutralising_the_asset_position_check_readmits_a_common_noun() -> None:
    """Anti-vacuity control for the second condition.

    "how much is a flight?" already reduces to one content token, so the token count alone does not
    refuse it -- the authority's indefinite-article rule does. Neutralise the authority and the
    common noun must be accepted as a subject again.

    Asserted on `bare_unresolved_asset_subject` rather than on the claim predicate on purpose: with
    the authority stubbed True, `_extract_price_asset_alias` would also loosen, and a claim-level
    assertion could pass through that instead of through the seam under test.
    """

    from core import semantic_claim_authority

    prompt = "how much is a flight?"
    assert bare_unresolved_asset_subject(prompt) == ""

    original = semantic_claim_authority.mention_is_market_authorized
    semantic_claim_authority.mention_is_market_authorized = lambda *a, **k: True
    try:
        readmitted = bare_unresolved_asset_subject(prompt)
    finally:
        semantic_claim_authority.mention_is_market_authorized = original

    assert readmitted == "flight", (
        "SABOTAGE DID NOT BITE: with the asset-position authority stubbed out, 'a flight' must be "
        f"accepted as a bare asset subject again. Got: {readmitted!r}"
    )
    assert bare_unresolved_asset_subject(prompt) == ""
