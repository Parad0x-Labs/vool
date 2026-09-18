"""Domain before entity: an entity table may never establish its own domain.

Human test at 2fae1489, through the shipped app::

    U: Based on my exact current city and today's date from your system context, search the web
       for three highly-rated, mid-priced dinner restaurants near me that are open tonight.
       Format the results into a clean Markdown table with the columns: Restaurant Name,
       Cuisine, Price Range, and Rating.

    A: Near: USD 1.62 (24h change: -0.88%). Source: CoinGecko

The ordinary English preposition "near" became a crypto asset and preempted the whole turn.

Why there is no ordinary-word denylist anywhere in production
--------------------------------------------------------------
Because the collision is not the word. Measured at the base, "restaurants near me tonight" does NOT
hijack -- the hijack needs market vocabulary somewhere in the turn ("mid-priced", "Price Range"),
predicated of RESTAURANTS, which a turn-global gate then spent authorizing an unrelated token.
A denylist would have to grow by one entry per CoinGecko listing forever and would still miss the
next one.

So the collision terms below are TEST data, discovered from the live index rather than invented, and
`core/semantic_claim_authority.py` names none of them. The live rank-ordered index really resolves
every one of these ordinary English words as an asset -- ape, apt, cake, dash, dot, edge, grass,
kite, night, pump, ray, render, sand, sky, gram, beat, mana, neo, rune -- which is exactly why the
architecture, and not a list, has to make them harmless.
"""

from __future__ import annotations

import re

import pytest

from core.execution_requirements import _live_data_classification

#: The live CoinGecko index, pinned. Every pair below was READ OFF the real rank-ordered index on
#: 2026-08-16 by resolving system-dictionary words against it; none was invented.
#:
#: Pinning is not a convenience, it is what makes this file mean anything. Under pytest the network
#: guard blocks the index fetch, so `resolve_tokens` returns nothing and NONE of the ordinary words
#: below resolve as assets at all -- every "must not hijack" assertion would pass for the wrong
#: reason, and would keep passing with the repair deleted. Measured: without this fixture, 9 of the
#: positive controls fail and the negatives go vacuous.
_REAL_INDEX = {
    "near": "near", "rain": "rain", "atom": "cosmos", "real": "reallink", "hash": "hash-2",
    "meta": "meta-2-2", "ape": "apecoin", "apt": "aptos", "cake": "pancakeswap-token",
    "dash": "dash", "dot": "polkadot", "edge": "edgex", "grass": "grass", "kite": "kite-2",
    "night": "midnight-3", "pump": "pump-fun", "ray": "raydium", "render": "render-token",
    "sand": "the-sandbox", "sky": "sky", "gram": "the-open-network", "beat": "audiera",
    "mana": "decentraland", "neo": "neo", "rune": "thorchain", "iota": "iota", "ada": "cardano",
    "doge": "dogecoin", "bonk": "bonk", "leo": "leo-token",
}


@pytest.fixture(autouse=True)
def _pinned_live_index(monkeypatch):
    """Make the live index deterministic and PRESENT, so the negatives are not vacuous."""

    def _resolve(text, skip_spans=None):
        skips = list(skip_spans or [])
        out = []
        for match in re.finditer(r"[a-z0-9]+", str(text or "").lower()):
            start, end = match.span()
            if any(start >= s and end <= e for s, e in skips):
                continue
            coin = _REAL_INDEX.get(match.group(0))
            if coin:
                out.append((start, coin))
        return out

    import core.agent_runtime.fast_live_info_price as price_mod
    from tools.web import coin_index

    monkeypatch.setattr(coin_index, "resolve_tokens", _resolve, raising=True)
    monkeypatch.setattr(price_mod, "resolve_tokens", _resolve, raising=False)


def test_the_pinned_index_really_is_live_for_these_tests():
    """Anti-vacuity: prove the collision tokens resolve at all before asserting they are refused."""
    from tools.web.coin_index import resolve_tokens

    assert resolve_tokens("near"), "the index fixture is not installed; every negative below is vacuous"
    assert resolve_tokens("ape") and resolve_tokens("render")


def routes_market(text: str) -> bool:
    verdict = _live_data_classification(text)
    return bool(verdict and "market_prices" in (verdict[2] or ()))


# --------------------------------------------------------------------------- 1. the reported turn

REPORTED = (
    "Based on my exact current city and today's date from your system context, search the web for "
    "three highly-rated, mid-priced dinner restaurants near me that are open tonight. Format the "
    "results into a clean Markdown table with the columns: Restaurant Name, Cuisine, Price Range, "
    "and Rating."
)


def test_the_reported_restaurant_turn_is_not_a_market_request():
    assert routes_market(REPORTED) is False


# ------------------------------------------- 2. clean ordinary-language collisions (>=5)
#
# Each names a token the live index resolves as an asset, used as ordinary English. The harder half
# also carries real market vocabulary predicated of something ELSE -- which is the shape that
# actually broke, and the shape a turn-global gate cannot survive.

ORDINARY_CLEAN = [
    "restaurants near me tonight",
    "find a pharmacy near me",
    "write me a short rhyme about rain",
    "send me the link when you get a chance",
    "explain a hash table to me",
    "I need a real example of this pattern",
    "put a dot on the map where the office is",
    "render the video at 720p please",
    "the sky is clear and the grass is green",
    "we walked along the sand at night",
    # market vocabulary present, predicated of something else entirely:
    "how much does a taxi near the station cost",
    "rate this restaurant near me",
    "what is the price of a kite at the beach shop",
    "value my dash to the shop at ten minutes",
    "a cheap hotel near the airport, rated highly",
]


@pytest.mark.parametrize("text", ORDINARY_CLEAN)
def test_ordinary_language_never_establishes_the_market_domain(text):
    assert routes_market(text) is False, f"{text!r} was claimed by the market lane"


# ------------------------------------------------- 3. sloppy / typo / real-user shapes (>=5)

ORDINARY_SLOPPY = [
    "resturants near me tonite",
    "pharmacy near me pls",
    "whats the cost of a taxi near the airport thx",
    "rhyme about rain pls",
    "send teh link",
    "gimme a real exmaple",
    "how much for a cake near me???",
    "put a dot on teh map",
    "render this vid pls thx",
    "wat time does the shop near me close",
]


@pytest.mark.parametrize("text", ORDINARY_SLOPPY)
def test_sloppy_ordinary_language_never_establishes_the_market_domain(text):
    assert routes_market(text) is False, f"{text!r} was claimed by the market lane"


# ------------------------------------------------------------------ 4. ALL CAPS / rage typing
#
# Capitalisation carries ZERO authority. Shouting is not a market signal.

ALL_CAPS = [
    "FIND A PHARMACY NEAR ME",
    "RESTAURANTS NEAR ME TONIGHT",
    "SEND ME THE LINK",
    "ATOM IS A WORD IN THIS SENTENCE",
    "TRY THIS COMMAND AGAIN",
    "WHY IS THE GRASS SO LONG",
    "RENDER THE DAMN VIDEO",
    "HOW MUCH DOES A TAXI NEAR THE STATION COST",
]


@pytest.mark.parametrize("text", ALL_CAPS)
def test_shouting_carries_no_market_authority(text):
    assert routes_market(text) is False, f"{text!r} was claimed by the market lane"


def test_case_alone_changes_nothing_either_way():
    """The same sentence in three cases must reach the same verdict, both directions."""
    for ordinary in ("restaurants near me tonight", "the sand at night"):
        verdicts = {routes_market(ordinary.lower()), routes_market(ordinary.upper()),
                    routes_market(ordinary.title())}
        assert verdicts == {False}, ordinary
    for market in ("near price", "gold price today"):
        verdicts = {routes_market(market.lower()), routes_market(market.upper()),
                    routes_market(market.title())}
        assert verdicts == {True}, market


# ---------------------------------------------------- 5. crypto/market POSITIVE controls (>=3)
#
# Bare assets WITH independent market context. `$` is not required for a normal market request.

MARKET_POSITIVE = [
    "NEAR price",
    "NEAR market cap",
    "price of NEAR",
    "what is Bitcoin worth today?",
    "buy BTC",
    "compare BTC and ETH",
    "BTC vs ETH",
    "ETH price in EUR",
    "gold prices",
    "gold price today.",
    "What are the going rates for gold?",
    "btc price now? and sol price please",
    "Market data only for Bitcoin and gold.",
    "Markets: Ethereum, Solana, gold, silver.",
    "Has gold gone up today?",
    "silver spot price",
]


@pytest.mark.parametrize("text", MARKET_POSITIVE)
def test_a_bare_asset_with_market_context_still_works(text):
    assert routes_market(text) is True, f"{text!r} is a genuine market request and was refused"


# ------------------------------------------------------------------- 8. explicit $TOKEN syntax
#
# The ONE syntax allowed to self-authorize with no supporting market word. Broken at the base:
# `$NEAR` classified as nothing at all.

#: `$ NEAR` with a space is not a typo -- it is what `core.input_normalizer` produces from `$NEAR`,
#: and the form the classifier actually receives. Measured at the real seam: `$NEAR` classified as
#: market and the app still answered it as ordinary chat, because the sigil was no longer adjacent
#: by the time authority was asked.
DOLLAR_TOKEN = ["$NEAR", "$Near", "$near", "$BTC", "$eth", "$SOL", "$ NEAR", "$ BTC", "$  near"]


@pytest.mark.parametrize("text", DOLLAR_TOKEN)
def test_dollar_token_self_authorizes(text):
    assert routes_market(text) is True, f"{text!r} is explicit asset syntax and must self-authorize"


def test_the_sigil_is_what_authorizes_not_the_letters():
    """`$NEAR` is market; the same letters without the sigil, in a sentence, are not."""
    assert routes_market("$NEAR") is True
    assert routes_market("NEAR") is False
    assert routes_market("the cafe is near") is False


# ------------------------------------------------------------------------ 6/7. the FX mirror
#
# ISO membership does not establish FX domain either. FX holds this through its own structural
# gate (an amount plus a direction), not through the market seam -- asserted here so the two
# contracts are visibly the same contract.

FX_POSITIVE = [
    "100 EUR to TRY",
    "convert 500 TRY to EUR",
    "what is 20 GBP in EUR?",
    "1000 TRY to USD?",
    "300 SEK in EUR",
]
FX_ORDINARY = [
    "TRY this command again",
    "CAN you explain this?",
    "ALL good?",
    "SEK is not relevant here",
    "I will TRY again later",
    "CAN we ship this today?",
    "please TRY harder",
]


@pytest.mark.parametrize("text", FX_POSITIVE)
def test_a_real_conversion_still_resolves(text):
    from core.currency_intent import fx_conversion_intent

    assert fx_conversion_intent(text) is not None, f"{text!r} is a real FX conversion"


@pytest.mark.parametrize("text", FX_ORDINARY)
def test_iso_membership_alone_never_establishes_fx(text):
    from core.currency_intent import fx_conversion_intent

    assert fx_conversion_intent(text) is None, f"{text!r} was claimed by the FX lane"
    assert routes_market(text) is False, f"{text!r} was claimed by the market lane"


# ------------------------------------------------------------------ 9. multipart, mixed domain


def test_one_asset_obligation_does_not_erase_its_unrelated_siblings():
    """Crypto may own the BTC clause. It may not replace the request."""
    text = "Tell me the weather in Vilnius, write a rhyme about rain, and give me BTC price"
    verdict = _live_data_classification(text)
    assert verdict is not None, "the live-data lane should still claim the parts it can serve"
    toolsets = set(verdict[2] or ())
    assert "market_prices" in toolsets, "the BTC obligation is real and must be served"
    assert "weather" in toolsets, (
        "the weather sibling was erased by the asset match -- recognizing one financial entity "
        "must never become authority over the whole turn"
    )
    from core.agent_runtime.live_data_plan import build_live_data_plan

    plan = build_live_data_plan(text, plan_id="p", attempt_id="a")
    assert plan is not None
    operations = {task.operation for task in plan.subtasks}
    assert "market_quote" in operations and "weather_lookup" in operations, operations
    # "rain" is an index-resolvable token used as ordinary English in the very same message.
    assets = {str(t.arguments.get("asset_key") or "") for t in plan.market_subtasks()}
    assert not any("rain" in a for a in assets), f"the rhyme's 'rain' became an asset: {assets}"


# ------------------------- 10. unseen collisions, discovered from the live resolver, not invented


UNSEEN_FROM_REAL_INDEX = [
    ("ape", "please stop being an ape about this"),
    ("apt", "that is an apt description of the problem"),
    ("cake", "bring a cake to the party tonight"),
    ("dash", "I had to dash to the shop before it closed"),
    ("dot", "put a dot on the map where the office is"),
    ("edge", "the cup was right on the edge of the table"),
    ("grass", "the grass needs cutting this weekend"),
    ("kite", "the kite got stuck in a tree"),
    ("night", "we drove through the night to get there"),
    ("pump", "I need to pump the bike tyres"),
    ("ray", "a ray of sunlight came through the window"),
    ("render", "render the animation overnight"),
    ("sand", "there was sand in my shoes all week"),
    ("sky", "the sky went completely dark"),
    ("gram", "add one more gram of yeast to the dough"),
    ("beat", "that song has a great beat"),
    ("mana", "the spell costs too much mana"),
    ("neo", "Neo is the main character in that film"),
    ("rune", "the stone was carved with a rune"),
    ("iota", "he did not care one iota"),
]


@pytest.mark.parametrize(("token", "sentence"), UNSEEN_FROM_REAL_INDEX, ids=[t for t, _ in UNSEEN_FROM_REAL_INDEX])
def test_an_unseen_index_collision_is_harmless_by_construction(token, sentence):
    """Each token really is resolvable by the live index; none is named in production code.

    This is the anti-overfit claim, executed: the repair cannot have been tuned to these, because
    the module under test contains no asset vocabulary at all. If a future listing collides with
    another ordinary word, it lands here already handled.
    """
    assert routes_market(sentence) is False, f"{token!r} hijacked: {sentence!r}"


def test_those_same_tokens_still_work_as_assets_when_the_request_says_so():
    """The other direction on the SAME tokens -- this must not be 'block everything'."""
    for token in ("ape", "dot", "render", "sand", "sky"):
        assert routes_market(f"{token} price") is True, token
        assert routes_market(f"${token}") is True, token


# ------------------------------------------------- the CURATED resolver, pinned in its own right


@pytest.mark.parametrize(
    "text",
    [
        "send me the link",
        "send me the link when you get a chance",
        "click the LINK to see pricing",
        "the link and the atom are unrelated",
    ],
)
def test_the_curated_alias_table_also_reports_only_authorized_mentions(text):
    """`link` IS in the closed alias table, and ordinary English still must not resolve it.

    Pinned directly on `price_assets_named` rather than only through the classifier. Sabotaging the
    authority call on the CURATED arm was survivable while this file asserted only classifier
    verdicts: `_live_data_classification` re-gates on `market_semantics_present`, so the curated
    filter looked inert and a reviewer could have deleted it believing it was dead. It is not dead
    -- `core.live_data_continuation`, `core.agent_runtime.turn_planner_hook` and the generic
    renderer all read this function directly and get no second gate.
    """
    from core.agent_runtime.fast_live_info_price import price_assets_named

    assert price_assets_named(text) == [], f"{text!r} resolved a curated alias it never asked about"


def test_the_curated_table_still_resolves_a_real_market_request():
    from core.agent_runtime.fast_live_info_price import price_assets_named

    assert price_assets_named("gold price") == ["gold"]
    assert price_assets_named("what is the link price") == ["link"]


def test_capitalisation_is_never_consulted_by_the_authority():
    """The seam is fed lowercased text, so case cannot reach it -- asserted, not assumed.

    A sabotage that made `mention_is_market_authorized` return True for a capitalised mention was
    INERT for exactly this reason and survived the matrix. That is a property worth pinning rather
    than a hole: every production caller lowercases first, and this proves both halves -- the
    authority itself is case-blind, and the resolvers hand it lowercased text.
    """
    from core.semantic_claim_authority import mention_is_market_authorized

    for text in ("near price", "NEAR PRICE", "Near Price"):
        lowered = text.lower()
        start = lowered.index("near")
        assert mention_is_market_authorized(lowered, start, start + 4) is True, text

    for text in ("restaurants near me tonight", "RESTAURANTS NEAR ME TONIGHT"):
        lowered = text.lower()
        start = lowered.index("near")
        assert mention_is_market_authorized(lowered, start, start + 4) is False, text
