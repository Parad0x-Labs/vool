"""Unit coverage for `core.market_intent` -- the authority that decides whether a REQUEST is
about the market, separately from which assets it happens to name.

The routing proof lives in `test_an_asset_name_alone_is_not_a_market_request.py`, which drives the
real `VoolAgent.run_once()`. This file pins the predicate's own contract: the vocabulary, the
negation handling, and the two false-positive shapes that put a semantic question into the market
lane before the SENTINEL G4 repair.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from core.market_intent import (
    MARKET_TERM_EXCLUSIONS_BY_SENSE,
    MARKET_TERMS,
    market_quote_intent_present,
    market_semantics_present,
    market_terms_are_negated,
)


@pytest.mark.parametrize(
    "text",
    [
        "Explain gold structure.",
        "What is the chemical structure of gold?",
        "Describe the atomic structure of gold.",
        "Why is gold yellow?",
        "How is gold formed?",
        "What properties does gold have?",
        "Compare gold and copper conductivity.",
        "What is gold used for in electronics?",
        "Explain silver structure.",
        "Explain copper conductivity.",
        "explain the chemical composition of oil",
        "What are the physical properties of silver?",
    ],
)
def test_a_request_that_says_nothing_about_the_market_has_no_market_semantics(text: str) -> None:
    assert market_semantics_present(text) is False
    assert market_quote_intent_present(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "Gold price",
        "Current gold price",
        "What's gold trading at?",
        "Gold market value today",
        "Gold vs silver price",
        "How much is gold right now?",
        "Has gold gone up today?",
        "Current XAU price",
        "BTC price",
        "ETH price",
        "oil price today",
        "what is oil worth?",
        "Market data only for Bitcoin and gold.",
        "Markets: Ethereum, Solana, gold, silver.",
    ],
)
def test_a_request_about_price_or_the_market_has_market_semantics(text: str) -> None:
    assert market_semantics_present(text) is True
    assert market_quote_intent_present(text) is True


# ---------------------------------------------------------------------------------------------
# Negation. A market word the user raised only to rule out is not market evidence.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "Not price — I mean the chemical structure of gold.",
        "Actually not price — explain its atomic structure.",
        "I don't want the price, explain the structure.",
        "No price please, just the atomic structure.",
        "Rather than price, tell me how gold is formed.",
        "Instead of the market value, explain its conductivity.",
    ],
)
def test_a_negated_market_word_is_not_market_evidence(text: str) -> None:
    assert market_semantics_present(text) is False
    assert market_terms_are_negated(text) is True


def test_a_negation_that_follows_the_market_word_does_not_negate_it() -> None:
    """"is the gold price not going up?" is a market question. The cue has to GOVERN the term, so
    only a cue standing before it counts -- otherwise any sentence containing "not" anywhere would
    silently stop being a market request."""
    assert market_semantics_present("Is the gold price not going up?") is True


def test_a_negation_does_not_reach_across_a_sentence_boundary() -> None:
    """"Not the price. What is gold worth?" -- the first clause rules the market out, the second
    asks for it anyway, and the second decides."""
    assert market_semantics_present("Not the price. What is gold worth?") is True


def test_a_partially_negated_message_keeps_the_market_term_that_survives() -> None:
    assert market_semantics_present("Not just price, also market cap.") is True


def test_terms_are_negated_is_false_when_no_market_term_appears_at_all() -> None:
    """"no market words" and "market words, all rejected" are different facts, and this predicate
    reports only the second."""
    assert market_terms_are_negated("Explain gold structure.") is False


# ---------------------------------------------------------------------------------------------
# The substring false positives the pre-repair containment test admitted.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["Do you know the atomic structure of gold?", "Is gold known to conduct electricity?"],
)
def test_now_inside_know_no_longer_admits_the_market_lane(text: str) -> None:
    """`"now" in "know"` is True, which is how a plain containment test over the recency markers
    turned "Do you know ...?" into a live quote request."""
    assert market_quote_intent_present(text) is False


def test_recency_markers_alone_still_admit_the_quote_recognizers_but_not_the_presence_path() -> None:
    """The two predicates differ on purpose, and this is the difference: a bare recency marker was
    an independent admission for the quote recognizers before this repair and stays one (narrowing
    it is an unmeasured change this repair was told not to make), while the entity-presence path
    the G4 defect came in through requires real market semantics.

    NOTE, measured and disclosed: this split does NOT make "Explain gold structure today."
    non-market end to end -- the recency marker still admits it through
    `_looks_like_market_quote_query_all`. That is pre-existing base behaviour and out of scope
    here; see `test_the_recency_false_positive_is_pre_existing_and_unchanged`.
    """
    assert market_quote_intent_present("gold today") is True
    assert market_semantics_present("gold today") is False


# ---------------------------------------------------------------------------------------------
# Inflected forms. The pre-repair containment test admitted these for free; word-boundary matching
# killed them until they were listed explicitly. ARGUS measured the regression on the released base.
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        # price
        "gold prices", "how is gold priced", "gold pricing",
        # cost
        "gold costs", "gold costing", "is gold costly",
        # value
        "gold values", "how is gold valued", "gold valuations",
        # rate
        "gold rates", "what are the going rates for gold?",
        # quote
        "gold quotes", "gold quoted", "gold quoting",
        # market cap
        "gold market caps",
    ],
)
def test_every_legitimate_inflection_the_base_admitted_still_carries_market_semantics(text: str) -> None:
    assert market_semantics_present(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # These CONTAIN a market term as a substring and must not match it. The first group is the
        # original ARGUS attack corpus; the second is specific to the restored PLURAL forms, where
        # a careless re-broadening would recreate the containment bug ("Emirates"/"pirates"/
        # "operates"/"corporates" all end in the letters of "rates").
        "Do you know gold?", "What is known about gold?", "Share your knowledge of gold.",
        "Does snow affect gold mining?", "There is nowhere gold is mined here.",
        "Is a gold costume expensive to make?", "Is gold priceless?",
        "Is gold worthwhile as a conductor?", "Anything noteworthy about gold?",
        "Where is the gold supermarket?", "gold marketing strategy",
        "I am grateful for gold.", "Is that an accurate gold figure?",
        "corporate gold policy", "moderate gold usage", "generate gold particles",
        "separate the gold sample",
        "Emirates gold reserves", "pirates found gold", "operates a gold mine",
        "gold corporates report",
    ],
)
def test_a_longer_word_that_merely_contains_a_market_term_is_not_market(text: str) -> None:
    assert market_semantics_present(text) is False
    assert market_quote_intent_present(text) is False


@pytest.mark.parametrize(
    "text",
    [
        # polarity-prefixed price/value forms -- the shape ARGUS measured as still regressed
        "is gold overpriced", "is gold underpriced", "gold mispriced", "gold repriced",
        "is gold pricey", "is gold pricier", "the priciest gold",
        "is gold overvalued", "is gold undervalued", "gold devalued", "gold revalued",
        # gradable cost forms
        "is gold costlier than silver", "the costliest gold", "is gold costly",
        # worth: the VALUE sense
        "is gold worthless",
        # rate / quote families
        "is gold overrated", "is gold underrated", "gold requoted", "unquoted gold shares",
        "unrated gold bonds",
        # The two ARGUS named after the affix-list gap, plus representatives of the families the
        # completed alignment rule surfaced alongside them.
        "is gold priceable", "is gold worthful",
        "gold costliness", "the oncost of gold", "gold ratepayer levy",
        "who is the gold pricer", "did they undervaluer the gold",
    ],
)
def test_every_legitimate_derived_form_the_base_admitted_carries_market_semantics(text: str) -> None:
    """The full morphological families of the base cues, not just their plurals.

    ARGUS measured a second wave of the same regression after the plural fix: "Is gold overpriced?",
    "Is gold overvalued?", "is gold costlier than silver", "Is gold pricey?", "gold mispriced",
    "Is gold worthless?" all planned a market lookup at the released base and planned nothing here.
    The vocabulary now carries the whole family for each cue, established by a dictionary sweep
    rather than by patching the examples one at a time -- see `MARKET_TERM_EXCLUSIONS_BY_SENSE`.
    """
    assert market_semantics_present(text) is True


@pytest.mark.parametrize(
    "text",
    [
        # morpheme-aligned to a market cue, but NOT a market sense. These are the complete set of
        # thirteen words held back on meaning; every other morpheme-aligned form is admitted.
        "is gold priceless",            # idiom: beyond price
        "did they misquote the gold report", "unquote the gold figure",   # speech sense
        "is gold worthy of study", "an unworthy gold sample",             # merit sense
        "the worthiest gold", "gold worthiness", "gold unworthiness",
        # engineering / nautical. Each sentence carries ONLY the word under test -- an earlier
        # draft wrote "disrate the gold rating", which passes on "rating" (real market vocabulary,
        # retained for base compatibility) and so proved nothing about "disrate".
        "derate the gold furnace", "disrate the gold bosun",
        "the gold coster sells fruit",                                    # costermonger
        # judged alongside priceable/worthful when the alignment rule was completed
        "gold pricelessness", "the gold quotee spoke", "did they misquoter the gold report",
        "a preworthy gold sample", "the gold eigenvalue", "prorate the gold shipment",
        "the gold oncostman clocked in",
    ],
)
def test_a_morpheme_aligned_word_with_a_non_market_sense_is_still_not_market(text: str) -> None:
    assert market_semantics_present(text) is False


@pytest.mark.parametrize(
    "text",
    [
        "Not prices — explain gold's atomic structure.",
        "Not quotes — explain gold structure.",
        "Not costs — explain gold structure.",
        "Not rates — explain gold structure.",
        # the derived forms restored in this closure obey the same negation scan
        "Gold is not overpriced; explain why it is yellow.",
        "Not asking whether gold is overvalued — explain its atomic structure.",
        "Not undervalued — explain gold structure.",
        "Not worthless — explain gold structure.",
        "Not costlier — explain gold structure.",
    ],
)
def test_restoring_the_plurals_did_not_let_them_bypass_negation(text: str) -> None:
    """The inflected forms go through the SAME negation scan as their base forms -- restoring them
    by listing them in the vocabulary cannot route around it, because the vocabulary is what the
    negation scan is applied to."""
    assert market_semantics_present(text) is False


# =================================================================================================
# THE CLOSURE GUARD -- "there are no unclassified cue-aligned held-back words"
# =================================================================================================
#
# WHY THIS EXISTS. The compatibility inventory was first built with a one-off script that used one
# affix list to GENERATE candidates and a shorter one to CLASSIFY them. The classifier omitted
# `-able` and `-ful`, so `priceable` and `worthful` were counted as accidental substring collisions
# instead of being judged, and nothing in the suite could see it -- the only closure test asserted
# "every listed exclusion is absent from the vocabulary", which is the easy direction and is
# vacuously true of a list that is missing entries.
#
# This is the converse, and it is the direction that has teeth: EVERY word the alignment rule
# catches must land in exactly one declared bucket. A future `priceable` -- a real cue-derived word
# nobody has thought about yet -- lands in none of them and fails `test_no_cue_aligned_word_is_left
# _unclassified` by name.
#
# The alignment rule below deliberately OVER-generates: it catches "berate", "costal" and "pentecost",
# which have nothing to do with markets. That is the design. Over-generation only ever forces
# another explicit judgment; under-generation is what caused the defect.

_SYSTEM_DICTIONARY = Path("/usr/share/dict/words")

_CUES = ("price", "cost", "worth", "value", "rate", "quote")

_ALIGNMENT_PREFIXES = (
    "over", "under", "mis", "re", "de", "non", "un", "dis", "pre", "out", "counter", "inter",
    "multi", "sub", "super", "semi", "anti", "co", "ex", "post", "self", "well", "ill", "down",
    "up", "back", "fore", "after", "by", "in", "im", "ir", "il", "a", "be", "en", "em", "for",
    "off", "on", "trans", "ultra", "pro", "par", "per", "pan", "poly", "mono", "bi", "tri", "uni",
    "micro", "macro", "mega", "hyper", "hypo", "extra", "intra",
)

# HEAD: an optional listed prefix, then a cue, then any tail. This is where `priceable`/`worthful`
# had to be caught and were not -- note it constrains no suffix at all, precisely so that no future
# suffix can be "missing" from it.
_HEAD_RULE = re.compile(
    r"^(?:" + "|".join(_ALIGNMENT_PREFIXES) + r")?(?:" + "|".join(_CUES) + r")", re.IGNORECASE
)

# TAIL: compound "X + cue" (pennyworth, oncost, quoteworthy). `rate` is excluded from THIS rule and
# only this rule: `-rate` is the Latin verb ending behind roughly nine hundred accidental collisions
# (accelerate, corporate, celebrate, separate, karate), and every legitimate word ending in it is
# prefix+cue -- overrate, prorate, berate -- which _HEAD_RULE already catches.
_TAIL_RULE = re.compile(r"(?:worth|worthy|worthily|worthiness|price|cost|value|quote)$", re.IGNORECASE)


def _is_cue_aligned(word: str) -> bool:
    return bool(_HEAD_RULE.match(word) or _TAIL_RULE.search(word))


# Inflections the 1934 dictionary predates, which containment admitted just the same.
_MODERN_INFLECTIONS: tuple[str, ...] = (
    "costed", "costlier", "costliest", "costs", "devalued", "mispriced", "misrated", "misvalued",
    "overpriced", "overquoted", "overrated", "overrates", "overvalued", "overvalues", "prices",
    "pricey", "quoted", "quoters", "quotes", "raters", "rates", "repriced", "requoted", "rerated",
    "revalued", "underpriced", "underquoted", "underrated", "underrates", "undervalued",
    "undervalues", "values",
)

# THE FROZEN INVENTORY: every cue-aligned word-shape in the compatibility surface. Re-derived from
# the system dictionary by `test_the_frozen_inventory_still_matches_the_system_dictionary`, so it
# cannot silently drift away from the rule that produced it.
_CUE_ALIGNED_INVENTORY: tuple[str, ...] = (
    "accost", "aftercost", "aimworthiness", "airworthiness", "airworthy", "alecost", "allworthy",
    "becost", "bequote", "berate", "bicostate", "blameworthiness", "blameworthy", "bloodworthy",
    "bribeworthy", "caprice", "cost", "costa", "costaea", "costal", "costalgia", "costally",
    "costander", "costanoan", "costar", "costard", "costata", "costate", "costated", "costean",
    "costeaning", "costectomy", "costed", "costellate", "coster", "costerdom", "costermonger",
    "costicartilage", "costicartilaginous", "costicervical", "costiferous", "costiform",
    "costing", "costipulator", "costispinal", "costive", "costively", "costiveness", "costless",
    "costlessness", "costlier", "costliest", "costliness", "costly", "costmary", "costoabdominal",
    "costoapical", "costocentral", "costochondral", "costoclavicular", "costocolic",
    "costocoracoid", "costodiaphragmatic", "costogenic", "costoinferior", "costophrenic",
    "costopleural", "costopneumopexy", "costopulmonary", "costoscapular", "costosternal",
    "costosuperior", "costothoracic", "costotome", "costotomy", "costotrachelian",
    "costotransversal", "costotransverse", "costovertebral", "costoxiphoid", "costraight",
    "costrel", "costs", "costula", "costulation", "costume", "costumer", "costumery", "costumic",
    "costumier", "costumiere", "costuming", "costumist", "costusroot", "counterrate", "dearworth",
    "dearworthily", "dearworthiness", "decostate", "derate", "derater", "devalue", "devalued",
    "disrate", "disvalue", "disworth", "eigenvalue", "enworthed", "equivalue", "evalue",
    "evenworthy", "extracostal", "faithworthiness", "faithworthy", "fameworthy", "forequoted",
    "forevalue", "groatsworth", "halfpennyworth", "hangworthy", "helpworthy", "honorworthy",
    "intercostal", "intercostally", "intercostobrachial", "intercostohumeral", "intracostal",
    "invalued", "keepworthy", "laughworthy", "loveworth", "loveworthy", "markworthy",
    "millincost", "mispriced", "misquote", "misquoter", "misrate", "misrated", "misvalue",
    "misvalued", "mootworthy", "multicostate", "multirate", "newsworthiness", "newsworthy",
    "noncostraight", "nonrated", "noteworthily", "noteworthiness", "noteworthy", "oathworthy",
    "oncost", "oncostman", "outprice", "outquote", "outrate", "outvalue", "outworth",
    "overcostly", "overprice", "overpriced", "overquoted", "overrate", "overrated", "overrates",
    "overvalue", "overvalued", "overvalues", "painsworthy", "pennyworth", "pentecost",
    "piceworth", "postcostal", "poundworth", "praiseworthy", "praisworthily", "praisworthiness",
    "precostal", "preprice", "prequote", "prevalue", "preworthily", "preworthiness", "preworthy",
    "price", "priceable", "priceably", "priced", "priceite", "priceless", "pricelessness",
    "pricer", "prices", "pricey", "prizeworthy", "prorate", "quote", "quoted", "quotee",
    "quoteless", "quotennial", "quoter", "quoters", "quotes", "quoteworthy", "rate", "rated",
    "ratel", "rateless", "ratement", "ratepayer", "ratepaying", "rater", "raters", "rates",
    "reaccost", "recostume", "reprice", "repriced", "requote", "requoted", "rerate", "rerated",
    "respectworthy", "revalue", "revalued", "roadworthiness", "roadworthy", "seaworthiness",
    "seaworthy", "semicostal", "semicostiferous", "semiquote", "shameworthy", "shillingsworth",
    "showworthy", "sightworthiness", "sightworthy", "sixpennyworth", "songworthy", "stageworthy",
    "subcosta", "subcostal", "subcostalis", "superquote", "supervalue", "talkworthy", "tamworth",
    "thankworthily", "thankworthiness", "thankworthy", "thegnworthy", "threepennyworth",
    "transvalue", "tricostate", "trustworthily", "trustworthiness", "trustworthy",
    "twalpennyworth", "uncost", "uncostliness", "uncostly", "uncostumed", "underprice",
    "underpriced", "underquote", "underquoted", "underrate", "underrated", "underratement",
    "underrates", "undervalue", "undervalued", "undervaluement", "undervaluer", "undervalues",
    "unicostate", "unnoteworthy", "unpraiseworthy", "unpriceably", "unpriced", "unquote",
    "unquoted", "unrated", "unroadworthy", "unseaworthiness", "unseaworthy", "untrustworthily",
    "untrustworthiness", "untrustworthy", "unvalue", "unvalued", "unworth", "unworthily",
    "unworthiness", "unworthy", "value", "valued", "valueless", "valuelessness", "valuer",
    "values", "viewworthy", "wanworth", "wonderworthy", "worshipworth", "worshipworthy", "worth",
    "worthful", "worthfulness", "worthiest", "worthily", "worthiness", "worthless", "worthlessly",
    "worthlessness", "worthship", "worthward", "worthy", "woundworth",
)

# Sense families. Each is a whole class of cue-aligned words with one shared reason, listed as a
# family rather than as two hundred individual entries because the reason is genuinely the same for
# every member. A word matching one of these is judged -- by family -- not unclassified.
_NON_MARKET_SENSE_FAMILIES: tuple[tuple[str, str, str], ...] = (
    (
        "anatomy_rib_costa",
        r"^(?:bi|de|equi|extra|infra|inter|intra|multi|non|post|pre|quadri|semi|sub|super|supra|tri"
        r"|uni|sacro|sterno|lumbo|iliaco?|phrenico?|chondro?|cleido|coraco|dentato|fissi|lati|crebri"
        r"|novem|quinque|triplici|tenui|curvi|sulcato|retro|epi|vertebro)?"
        r"cost(?:a|ae|al|ally|alis|ate|ated|ander|anoan|algia|ean|eaning|ectomy|ellate|i[a-z]*"
        r"|o[a-z]*|ula|ulation|raight|rel|mary|usroot|ard|ar)$",
        "Latin costa = RIB (intercostal, costochondral), plus costard/costmary, unrelated plants. "
        "Not the cue `cost` at all -- caught only because the rule over-generates on purpose.",
    ),
    (
        "clothing_costume",
        r"^(?:un|re)?costum(?:e|ed|er|ery|ic|ier|iere|ing|ist)$",
        "Italian costume = dress. Not the cue `cost`; 'is a gold costume expensive to make?' is one "
        "of the original substring false positives ARGUS requires to stay non-market.",
    ),
    (
        "merit_worthy_compound",
        r"worth(?:y|ily|iness)$",
        "X-worthy = DESERVING of X (noteworthy, trustworthy, seaworthy, praiseworthy). The merit "
        "sense of `worth`, which ARGUS requires non-market by name for noteworthy/worthwhile.",
    ),
    (
        "quantity_of_money_worth",
        r"(?:penny|halfpenny|sixpenny|threepenny|twalpenny|shillings|groats|pounds?|pice)worth$",
        "A pennyworth is an AMOUNT of goods, a quantity noun. 'Sell me a pennyworth' is not a "
        "question about what something is worth.",
    ),
)

# Individual words the over-generating rule catches that are not this morpheme in any sense.
_NOT_THIS_MORPHEME: tuple[tuple[str, str], ...] = (
    ("accost", "Latin ad+costa, to come alongside; not `cost`."),
    ("reaccost", "as accost."),
    ("alecost", "a plant, costmary; not `cost`."),
    ("berate", "to scold. `rate` here is the unrelated verb 'to chide', not a market rate."),
    ("caprice", "Italian capriccio, a whim; not `price`."),
    ("costaea", "a genus; the rib sense, missed by the anatomy family pattern."),
    ("costata", "as costaea."),
    ("costerdom", "the costermonger trade; from costard, an apple."),
    ("costermonger", "a fruit hawker; from costard, an apple."),
    ("pentecost", "a religious festival, Greek pentekoste; not `cost`."),
    ("priceite", "a mineral, named after a person; not `price`."),
    ("quotennial", "Latin quot = how many; not a market quote."),
    ("ratel", "a honey badger; not `rate`."),
    ("tamworth", "a breed of pig and an English town; not `worth`."),
    ("woundworth", "a variant of woundwort, a plant; not `worth`."),
)

_SENSE_FAMILY_PATTERNS = tuple(
    (name, re.compile(pattern, re.IGNORECASE), reason)
    for name, pattern, reason in _NON_MARKET_SENSE_FAMILIES
)


def _bucket_of(word: str) -> str | None:
    """Which declared bucket claims `word`, or None -- and None is what the guard fails on."""
    if word in MARKET_TERMS:
        return "MARKET_TERMS"
    if word in MARKET_TERM_EXCLUSIONS_BY_SENSE:
        return "MARKET_TERM_EXCLUSIONS_BY_SENSE"
    if word in {entry for entry, _ in _NOT_THIS_MORPHEME}:
        return "not-this-morpheme"
    for name, pattern, _ in _SENSE_FAMILY_PATTERNS:
        if pattern.search(word):
            return f"family:{name}"
    return None


def test_no_cue_aligned_word_is_left_unclassified() -> None:
    """THE closure guarantee, in the direction that can actually fail.

    Not "every listed exclusion is absent from the vocabulary" -- that passes trivially on an
    incomplete list, which is how `priceable` and `worthful` survived a green suite. This asserts
    the converse: every word the alignment rule catches is claimed by exactly one declared bucket,
    so a cue-derived word nobody has judged yet fails here by name instead of being counted as an
    accidental collision.
    """
    unclassified = [w for w in _CUE_ALIGNED_INVENTORY if _bucket_of(w) is None]
    assert unclassified == [], (
        f"{len(unclassified)} cue-aligned word(s) in no bucket: {unclassified}. Each must be added "
        f"to MARKET_TERMS (market sense), to MARKET_TERM_EXCLUSIONS_BY_SENSE (cue-derived, other "
        f"sense), or -- if it is not this morpheme at all -- to _NOT_THIS_MORPHEME here."
    )


@pytest.mark.parametrize("unjudged", ["pricewise", "pricehood", "worthlike"])
def test_the_closure_guard_actually_fires_on_an_unjudged_word(unjudged: str) -> None:
    """Exercise the guard's FAILURE path directly, because the passing suite never walks it.

    While the invariant holds, no word reaches `_bucket_of`'s final `return None` -- so mutating
    that line to return a bucket instead changes nothing any test observes, and the guard would
    quietly become vacuous. A mutation run found exactly that survivor, and this closes it: these
    probe words are cue-aligned (so the rule catches them) and deliberately judged nowhere, which is
    precisely the shape `priceable` and `worthful` had before ARGUS found them by hand.

    They are absent from the system dictionary, so they are not inventory entries in disguise --
    `test_the_frozen_inventory_still_matches_the_system_dictionary` would demand they be judged.
    """
    assert _is_cue_aligned(unjudged), "probe word must be cue-aligned or this proves nothing"
    assert unjudged not in _CUE_ALIGNED_INVENTORY
    assert _bucket_of(unjudged) is None


def test_no_word_is_claimed_by_two_buckets() -> None:
    """A word in both the vocabulary and the exclusions means the matcher and the documentation
    disagree, and the guard above would still pass by finding the first bucket."""
    both = sorted(set(MARKET_TERMS) & set(MARKET_TERM_EXCLUSIONS_BY_SENSE))
    assert both == [], f"documented as excluded but present in the vocabulary: {both}"
    for word in MARKET_TERM_EXCLUSIONS_BY_SENSE:
        assert word not in {entry for entry, _ in _NOT_THIS_MORPHEME}, word


def test_every_classification_in_the_inventory_is_real_through_the_predicate() -> None:
    """Bucketing a word is a claim about production behaviour, so check it against production.

    A word listed in MARKET_TERMS that the matcher never actually matches is a dead entry that makes
    the closure look complete while the behaviour is missing; a word held back on sense that the
    matcher admits anyway (through some other entry) means the exclusion is decorative.
    """
    should_match = [w for w in _CUE_ALIGNED_INVENTORY if _bucket_of(w) == "MARKET_TERMS"]
    should_not_match = [w for w in _CUE_ALIGNED_INVENTORY if _bucket_of(w) != "MARKET_TERMS"]
    assert should_match and should_not_match, "inventory lost its content"

    dead = [w for w in should_match if not market_semantics_present(f"is gold {w}")]
    assert dead == [], f"listed as market vocabulary but never matched: {dead}"

    leaked = [w for w in should_not_match if market_semantics_present(f"is gold {w}")]
    assert leaked == [], f"held back on sense but admitted anyway: {leaked}"


@pytest.mark.skipif(
    not _SYSTEM_DICTIONARY.exists(), reason=f"no system dictionary at {_SYSTEM_DICTIONARY}"
)
def test_the_frozen_inventory_still_matches_the_system_dictionary() -> None:
    """The anti-drift half: re-derive the inventory from the real dictionary using the SAME rule
    object the buckets are checked against.

    This is the specific defect being closed. The original sweep used one affix list to generate and
    another to classify, and the two disagreed silently. Here there is one `_is_cue_aligned`, used
    for both, and this test fails the moment the rule and the frozen inventory stop agreeing --
    including when someone widens the rule and forgets to judge what it newly catches.
    """
    dictionary = {
        line.strip().lower()
        for line in _SYSTEM_DICTIONARY.read_text(errors="ignore").splitlines()
        if line.strip().isalpha()
    }
    assert len(dictionary) > 50_000, f"dictionary looks truncated: {len(dictionary)} words"

    rederived = {w for w in dictionary if _is_cue_aligned(w)}
    rederived |= {w for w in _MODERN_INFLECTIONS if _is_cue_aligned(w)}

    missing = sorted(rederived - set(_CUE_ALIGNED_INVENTORY))
    stale = sorted(set(_CUE_ALIGNED_INVENTORY) - rederived)
    assert missing == [], (
        f"the alignment rule now catches {len(missing)} word(s) absent from the frozen inventory: "
        f"{missing[:40]}. Judge each one and add it to the inventory."
    )
    assert stale == [], f"frozen inventory holds words the rule no longer catches: {stale[:40]}"
