"""Whether a request is actually ABOUT the market — the one authority every market lane reads.

SENTINEL G4, 2026-08-06: "Explain gold structure." was answered as `market_quote("Gold")`, and so
was "Not price — I mean the chemical structure of gold." Traced to its first wrong production
decision: `core/execution_requirements._live_data_classification` admitted a turn to LIVE_DATA on
`bool(price_assets_named(text))` alone — an asset alias appearing ANYWHERE in the message. `gold` is
a metal before it is a ticker, so naming it was treated as asking what it costs.

The invariant this module exists to hold:

    A commodity/asset NAME alone is never evidence of a market request.
    Market routing requires evidence that the REQUEST is about market semantics.

So entity detection ("which assets are named?") and domain admission ("is this a market question?")
are two different questions, and this module owns the second one. It knows nothing about assets on
purpose — it cannot be made to admit a turn by naming one.

Why one module rather than a check per call site
------------------------------------------------
Three independent recognizers (`_looks_like_price_query`, `_looks_like_price_query_all`,
`_looks_like_market_quote_query{,_all}`) each hand-rolled the same "does this want a quote?" test,
and `_live_data_classification` had a fourth reading that skipped it entirely. Four readings of one
question is how they came to disagree. Every one of them now reads this.

Deliberately NOT a phrase blacklist
------------------------------------
Nothing here enumerates the non-market sentences to reject ("structure", "chemical", "atomic", ...)
— that list is unbounded and would fail on the first phrasing nobody thought of. This is the
inverse: a bounded, positive market vocabulary that a market request must AFFIRM, with everything
else falling through to the ordinary lane by default. Adding a new way to ask about gold's
electron configuration requires no change here; it is already non-market, because it says nothing
about the market.

What this module DOES change about market detection
----------------------------------------------------
Stated exactly, because an earlier version of this file claimed "every phrasing that reached the
market lane before still reaches it" and that was measurably false. Against the released base
(`5d24af39`), on a 127-phrase corpus driven through the real planning seam, this module changes
three things and nothing else:

  1. an asset NAME alone no longer admits the market lane          (the G4 defect — intended)
  2. a market term under an explicit negation no longer counts     (the G4 correction — intended)
  3. a longer word that merely CONTAINS a market term no longer
     counts: know/snow/nowhere/costume/priceless/worthwhile/
     noteworthy/supermarket/marketing/grateful/accurate/
     corporate/moderate/generate/separate                          (substring safety — intended)

Legitimate inflections ("prices", "quotes", "costs", "rates", ...) are NOT in that list: they are
admitted, by explicit listing in `MARKET_TERMS`. The first draft of this module dropped them by
accident and ARGUS measured it; see `MARKET_TERMS` for the full account.

The recency markers remain an independent admission and are unchanged from base — including the
false positive that makes "Explain gold structure today." market. See `RECENCY_MARKERS`.
"""

from __future__ import annotations

import re

# The market-semantics vocabulary, matched with WORD BOUNDARIES (see `_boundary_re`).
#
# The pre-repair test was `any(kw in lowered for kw in _PRICE_KEYWORDS)` — plain substring
# containment over nine base forms. That single test did two different jobs at once, and only one
# of them was wanted:
#
#   WANTED   -- it admitted every ordinary INFLECTION for free: "gold prices" matched because
#               "prices" contains "price"; likewise costs / rates / quotes / values.
#   UNWANTED -- it also admitted any longer word that merely contained a base form:
#               "know"/"snow"/"nowhere" (via the recency marker "now"), "costume", "priceless",
#               "worthwhile", "noteworthy", "supermarket", "marketing", "grateful", "accurate",
#               "corporate", "moderate", "generate", "separate".
#
# The first version of this module replaced containment with word boundaries, which killed the
# UNWANTED half — and, unmeasured at the time, the WANTED half with it, because only the SINGULAR
# base forms were listed. ARGUS measured the result: "Gold prices", "btc and eth prices",
# "gold quotes", "gold costs", "What are the going rates for gold?" all planned a market lookup at
# the released base and planned NOTHING on that first draft. That was a branch-introduced false
# negative, and closing it is what this vocabulary now does: every legitimate inflected form that
# containment used to admit is listed EXPLICITLY, so it is admitted by a real word match rather
# than by an accident of substring arithmetic. Containment is not restored anywhere.
#
# `value`/`rate`/`worth`/`rating` are broader than ideal in isolation ("the rate of thermal
# conductivity of gold", "gold rated highly by chemists" read as market here). That breadth is
# PRE-EXISTING — the released base admitted all of them — and narrowing it is a precision change
# with its own measurement, deliberately NOT bundled into a closure whose whole job is to restore
# what this branch broke. Retained on that basis, not because each one is individually ideal.
MARKET_TERMS: tuple[str, ...] = (
    # --- the nine pre-repair `_PRICE_KEYWORDS` base forms, each followed by its full
    # --- market-semantic morphological family. Containment admitted every one of these; the
    # --- families below are what "legitimate" resolved to after sweeping the whole system
    # --- dictionary for words containing each cue (see the module docstring's inventory note).
    #
    # price: plain, inflected, gradable, and the polarity prefixes that are the whole point of
    # asking ("is gold OVERpriced?" is a price question about gold).
    "price", "prices", "priced", "pricing", "pricey", "pricier", "priciest",
    "priceable", "priceably", "pricer",
    "overprice", "overpriced", "overpricing",
    "underprice", "underpriced", "underpricing",
    "misprice", "mispriced", "mispricing",
    "reprice", "repriced", "repricing",
    "preprice", "outprice",
    # cost
    "cost", "costs", "costed", "costing", "costly", "costlier", "costliest", "costless",
    "costlessness", "costliness", "uncostliness",
    "overcost", "undercost", "aftercost", "becost", "oncost", "millincost",
    # worth -- the QUANTITY-of-worth forms are a claim about an asset's value and are admitted;
    # the DESERVINGNESS forms ("worthy", "worthiness", "worthwhile", "noteworthy") are the merit
    # sense and are excluded. That -less/-ful/-ness vs -y/-iness split is the rule, stated once
    # here and applied uniformly, rather than a per-word judgment call.
    "worth", "worths", "worthless", "worthlessly", "worthlessness",
    "worthful", "worthfulness", "outworth",
    # "wanworth" is a LOW PRICE / bargain -- a price claim, unlike the other worth-compounds.
    "wanworth",
    # value
    "value", "values", "valued", "valuing", "valuer", "valuers", "valueless", "valuelessness",
    "undervaluer", "equivalue", "evalue", "forevalue", "supervalue", "transvalue",
    "overvalue", "overvalued", "overvalues", "overvaluing",
    "undervalue", "undervalued", "undervalues", "undervaluing",
    "devalue", "devalued", "devalues", "devaluing", "devaluation", "devaluations",
    "revalue", "revalued", "revalues", "revaluing", "revaluation", "revaluations",
    "misvalue", "misvalued", "prevalue", "outvalue",
    # rate
    "rate", "rates", "rated", "rating", "ratings", "rater", "raters", "rateless",
    "overrate", "overrated", "overrates",
    "underrate", "underrated", "underrates",
    "misrate", "misrated", "rerate", "rerated", "outrate",
    "counterrate", "multirate", "ratepayer", "ratepaying",
    # quote -- the market sense. "misquote"/"unquote"/"quotee" are the SPEECH sense and are excluded.
    "quote", "quotes", "quoted", "quoting", "quoter", "quoters",
    "requote", "requoted", "overquote", "overquoted", "underquote", "underquoted",
    "prequote", "outquote", "superquote",
    # --- archaic / low-frequency members of the same families, surfaced by the dictionary sweep.
    # --- Individually improbable in real chat; listed because the released base admitted each one
    # --- and leaving any out would be an unclassified compatibility difference. Zero collision
    # --- risk: each is matched whole, by word boundary.
    "disvalue", "nonrated", "overcostly", "quoteless", "ratement", "uncost", "uncostly",
    "underratement", "undervaluement", "unpriced", "unrated", "unvalue", "unvalued",
    # "unquoted" is not archaic at all -- unquoted shares are securities not listed on an exchange.
    "unquoted",
    "how much",
    "trading at",
    "market cap", "market caps",
    # --- ADDED by the G4 repair, never removed: market-domain phrasings containment admitted only
    # --- incidentally, via a price word that happened to also be present. Two are real recorded
    # --- production requests naming NO price word at all, which a strict market-semantics gate
    # --- would otherwise have stopped reaching the market lane (both caught by the existing suite
    # --- when the first draft omitted bare "market"):
    #       "Market data only for Bitcoin and gold."       (live-fixed; see live_data_plan.py)
    #       "Markets: Ethereum, Solana, gold, silver. ..."  (Mnemosyne reproduction B)
    # Several below are subsumed by bare "market" and are kept because they name the real recorded
    # request rather than leaving a future reader to infer it.
    "market", "markets", "market data", "market price", "market prices",
    "market value", "market values",
    "spot price", "spot prices", "exchange rate", "exchange rates",
    "ticker", "tickers", "trading", "traded", "trades",
    "valuation", "valuations",
)

# THE INDIVIDUALLY JUDGED EXCLUSIONS — cue-derived words this vocabulary deliberately does not
# admit, each held back on a stated sense, so this compatibility question does not reopen.
#
# This tuple is one bucket of four. The other three (two hundred more words that resolve into named
# sense FAMILIES, and the words the alignment rule catches that are not this morpheme at all) live
# beside the guard test in `tests/test_market_intent.py`, because they describe the SWEEP's
# over-generation rather than anything the matcher does. Together the four are exhaustive over the
# compatibility surface, and that exhaustiveness is what the guard test asserts.
#
# Method (finite, repeatable, and now EXECUTABLE IN THE SUITE): sweep the 235,976-word system
# dictionary for every word containing any single-word base cue (price/cost/worth/value/rate/quote)
# or recency marker (now/today/current/latest), plus the modern inflections the 1934 dictionary
# predates. Partition that surface with an affix grammar into MORPHEME-ALIGNED forms (a cue under
# real prefixes/suffixes) and accidental collisions (aberrate, accost, accurate, acknowledge,
# corporate, generate, separate, costume, supermarket, know, snow, ...). Every cue-aligned form must
# then land in a declared bucket, and `tests/test_market_intent.py::test_no_cue_aligned_word_is_left
# _unclassified` fails, naming the word, if any of them lands in none.
#
# THAT GUARD IS THE POINT, and it is the correction to how this list was built the first time. The
# first pass used one affix list to GENERATE candidates and a different, shorter one to CLASSIFY
# them: the classifier omitted `-able` and `-ful`, so `priceable` and `worthful` were silently
# counted as accidental collisions instead of being judged. ARGUS caught both by hand. The defect
# was never those two words -- it was that a too-narrow alignment rule sends unjudged words into the
# accidental bucket, where nothing looks at them again.
#
# So the alignment rule is now deliberately OVER-generating (it catches "berate", "costal", "karate"
# -- words that are not this cue at all) and every word it catches must be judged explicitly. Over-
# generation is safe: it only ever forces another judgment. Under-generation is the bug. Widening
# the rule from the original narrow one took the count from 2 words to 21 to 209; those 209 resolve
# into four named sense FAMILIES (rib anatomy, costume, the X-worthy merit compounds, the pennyworth
# quantity nouns) plus the 33 individually judged words listed below. The families and the rule live
# with the guard test, which fails on any word that lands in no bucket at all.
#
# The 33 individually judged cue-derived words held back on meaning:
#
#   priceless, pricelessness, unpriceably, invalued  idiom: BEYOND price/value, not a price claim.
#                                                    ARGUS requires "priceless" non-market by name.
#   eigenvalue                                       linear algebra, not an asset's value.
#   misquote, unquote, quotee, misquoter,            the SPEECH sense of "quote", not a market quote.
#   bequote, forequoted, semiquote
#   worthy, unworthy, worthiest, worthiness,         the MERIT sense of "worth" -- the same family
#   unworthiness, unworth, disworth, preworthy,      as "worthwhile"/"noteworthy", which ARGUS
#   preworthiness, enworthed, worthship,             also requires non-market. ("worthship" is an
#   worthward, dearworth, loveworth, worshipworth    old spelling of "worship".)
#   derate, derater, disrate, prorate                engineering (reduce rated capacity), nautical
#                                                    (reduce in rank), and proportional allocation
#                                                    -- none of them a market rate.
#   coster, oncostman                                a costermonger is a fruit hawker; an oncostman
#                                                    is a mine worker paid from oncost. Neither is
#                                                    asking what something costs.
#
# `worthless` is deliberately NOT in this list and IS market vocabulary: "is gold worthless?" is a
# claim about an asset's value, which is exactly the distinction ARGUS drew against "priceless".
# `worthful` follows it for the same reason and by the same rule -- it is the direct antonym, a
# claim about how much worth a thing has. The 1934 dictionary also glosses it "worthy; noble", so
# this one is a judgment between two live senses rather than a clear-cut call; it is resolved toward
# the value sense because splitting an antonym pair across the two buckets would leave the -less/
# -ful rule stated above incoherent, and because admitting errs toward base compatibility.
MARKET_TERM_EXCLUSIONS_BY_SENSE: tuple[str, ...] = (
    # price / value: beyond-price and beyond-value idioms
    "priceless", "pricelessness", "unpriceably", "invalued",
    # value: a distinct technical sense that is not an asset's worth
    "eigenvalue",
    # quote: the SPEECH sense
    "misquote", "unquote", "quotee", "misquoter", "bequote", "forequoted", "semiquote",
    # worth: the MERIT sense
    "worthy", "unworthy", "worthiest", "worthiness", "unworthiness", "unworth", "disworth",
    "preworthy", "preworthiness", "enworthed", "worthship", "worthward",
    "dearworth", "loveworth", "worshipworth",
    # rate: engineering (reduce rated capacity), nautical (reduce in rank), proportional allocation
    "derate", "derater", "disrate", "prorate",
    # cost: a fruit hawker (costermonger), and a mine worker paid from oncost
    "coster", "oncostman",
)

# Market MOVEMENT is market semantics even with no price word: "has gold gone up today?" is asking
# about the market, not about metallurgy. Listed separately from MARKET_TERMS because it answers a
# different question (did the market move?) rather than (what does it cost?).
MARKET_MOVEMENT_TERMS: tuple[str, ...] = (
    "gone up", "gone down", "went up", "went down", "going up", "going down",
    # Present-tense forms: "did btc go up today" is the same movement question
    # as "has btc gone up today", and only the perfect forms were admitted
    # (measured live 2026-08-29: the present-tense ask fell to the model).
    "go up", "goes up", "go down", "goes down",
    "rallied", "rally", "surged", "surge", "plunged", "plunge",
    "all-time high", "all time high", "market movement", "market moved",
    # Price CHANGE over a stated window is market movement with no movement
    # verb: "24h change on eth?", "day change on gold?" are asking whether the
    # market moved. Before this family was admitted, the asset in such a clause
    # could not even bind to the market lane (measured live: "1000 usd to eur
    # and then to gold? also btc price and 24 change on eth" planned gold+
    # bitcoin and silently dropped eth). The window is what keeps the family
    # precise, so BARE "change" is deliberately NOT admitted: "climate change",
    # "change my booking", "change the subject" are the act-of-altering sense,
    # not price deltas, and a bare admit fails those negative controls (measured
    # in test_live_data_change_attribution.py). Same exclusion rule for
    # "exchange"/"interchange"/"shortchange" (act-of-swapping; "exchange rate"
    # is admitted explicitly in MARKET_TERMS as the phrase) and for "game
    # changer"/"sea change" (idiom).
    "24 change", "24h change", "24hr change", "24 hour change", "24-hour change",
    "% change", "percent change",
    "day change", "daily change", "week change", "weekly change",
    "month change", "monthly change", "year change", "yearly change", "ytd change",
    "change today", "changes today", "changed today", "changing today",
    "change now", "changes now", "changed now",
)

# Recency markers. These are NOT market semantics on their own — "today" says when, not what — but
# they have admitted the market lane since before this repair, and no control in the G4 brief
# depends on removing them, so removing them here would be an unmeasured global narrowing of market
# detection this repair was explicitly told not to do. They are kept as an independent admission
# for the QUOTE recognizers only (`market_quote_intent_present`), and are absent from
# `market_semantics_present`, which is what gates the entity-presence path the G4 defect came in
# through.
#
# MEASURED, and corrected from an earlier false claim in this file: that split does NOT make
# "Explain gold structure today." non-market. It routes MARKET, because the recency marker alone
# still satisfies `market_quote_intent_present`, which `_looks_like_market_quote_query_all` reads,
# which `_live_data_classification` accepts independently of the entity-presence path. That is
# PRE-EXISTING behavior — the released base routes it market too — and it is deliberately OUT OF
# SCOPE here: the recency false positive is its own precision question with its own measurement,
# and this module's job is the asset-presence defect. Stated rather than implied, because the
# earlier wording asserted the opposite and was wrong.
#
# Matched with word boundaries, unlike the pre-repair substring test: `"now" in "do you know ..."`
# is True, which is why "Do you know the atomic structure of gold?" was admitted to the market lane.
RECENCY_MARKERS: tuple[str, ...] = ("latest", "current", "currently", "right now", "today", "now")


def _boundary_re(terms: tuple[str, ...]) -> re.Pattern[str]:
    """Word-boundary alternation over `terms`, longest first so a prefix cannot shadow a longer
    phrase ("market cap" must not be reported as bare "cap"-less "market")."""
    ordered = sorted(terms, key=len, reverse=True)
    # Lookarounds, not \b: for word terms they are equivalent, and they are
    # also correct for terms led by a non-word character ("% change"), where a
    # leading \b can never match (space->"%" is no word boundary).
    return re.compile(
        r"(?<!\w)(?:" + "|".join(re.escape(term) for term in ordered) + r")(?!\w)",
        re.IGNORECASE,
    )


_MARKET_TERM_RE = _boundary_re(MARKET_TERMS)
_MARKET_MOVEMENT_RE = _boundary_re(MARKET_MOVEMENT_TERMS)
_RECENCY_RE = _boundary_re(RECENCY_MARKERS)

# An explicit correction or exclusion. "Not price — I mean the chemical structure of gold" contains
# the word `price`, and a containment test reads that as wanting a price; the user is saying the
# exact opposite.
_NEGATION_CUE_RE = re.compile(
    r"\b(?:not|no|never|isn'?t|aren'?t|don'?t|dont|doesn'?t|didn'?t|won'?t|wasn'?t|"
    r"rather\s+than|instead\s+of|other\s+than|apart\s+from|besides|forget|ignore|"
    r"nothing\s+to\s+do\s+with|nevermind|never\s+mind)\b",
    re.IGNORECASE,
)

# What ENDS a negation's reach before the term. "Not just price, also market cap" negates `price`
# and then explicitly resumes: `market cap` is being asked for, not excluded. Without this, a
# window-length heuristic alone decides the question by accident -- three filler words happened to
# fit, so the surviving term was silently swallowed too. Deliberately excludes bare "and": "not
# price and cost" negates BOTH, and treating "and" as resumption would break that.
_NEGATION_RESUMPTION_RE = re.compile(r"\b(?:also|but|however|plus)\b", re.IGNORECASE)

# How far a negation cue reaches forward. A cue has to GOVERN the term, not merely appear somewhere
# earlier in the sentence -- in "is the gold price not going up?" the `not` FOLLOWS `price` and
# correctly does not negate it, and in a long sentence an unrelated early "no" must not silence a
# market word twenty words later. A bounded window is a heuristic, stated as one: it errs toward
# KEEPING the market reading (the pre-repair behavior), so a miss here is conservative.
_NEGATION_SCOPE_CHARS = 32

# Clause terminators. A negation in one sentence must not reach across into the next: "Not the
# price. What is gold worth?" is a market request, and only the second clause decides that.
_CLAUSE_SPLIT_RE = re.compile(r"[.?!;\n]+")


def _is_negated(clause: str, term_start: int) -> bool:
    """Whether an explicit negation cue governs the term starting at `term_start` in `clause`."""
    prefix = clause[:term_start]
    cues = list(_NEGATION_CUE_RE.finditer(prefix))
    if not cues:
        return False
    gap = prefix[cues[-1].end() :]
    if len(gap) > _NEGATION_SCOPE_CHARS:
        return False
    return not _NEGATION_RESUMPTION_RE.search(gap)


# A refusal word used as a DISCOURSE particle ("no, silver" / "nah, ethereum") opens a correction;
# it governs nothing. The same word directly before a noun ("no gold", "not gold") is a negator.
_DISCOURSE_REFUSAL_RE = re.compile(r"^(?:no|nah|nope|nvm|never\s*mind|nevermind)$", re.IGNORECASE)
_PUNCTUATION_GAP_RE = re.compile(r"^\s*[,.:;!\-]")


def mention_is_negated(
    text: str,
    start: int,
    *,
    blocking_spans: tuple[tuple[int, int], ...] = (),
) -> bool:
    """Whether an explicit negation cue governs the mention starting at `start` in `text`.

    The per-MENTION reading of the same negation law `market_terms_are_negated` applies to market
    terms: the cue must sit in the mention's own clause, within `_NEGATION_SCOPE_CHARS` before it,
    with nothing resuming the request in between. Three things end a cue's reach before it gets
    to this mention, each a fact about the sentence and none about any asset:

    * an earlier mention (`blocking_spans`) -- a cue governs the FIRST thing it is said of:
      "not gold, silver" negates `gold`, and `silver` is the request;
    * a correction reframe in the gap ("no I MEANT silver", "not gold, WHAT ABOUT silver") -- the
      user is resuming, through the same vocabulary `core.followup_subject_continuity` opens
      corrections with;
    * a refusal word followed by punctuation ("no, silver") -- a discourse particle, not a negator.
    """
    body = str(text or "")
    if not body or start < 0 or start >= len(body):
        return False
    offset = 0
    for clause in _CLAUSE_SPLIT_RE.split(body):
        end = offset + len(clause)
        if offset <= start < end:
            local = start - offset
            prefix = clause[:local]
            cues = list(_NEGATION_CUE_RE.finditer(prefix))
            if not cues:
                return False
            cue = cues[-1]
            gap = prefix[cue.end():]
            if len(gap) > _NEGATION_SCOPE_CHARS:
                return False
            if _NEGATION_RESUMPTION_RE.search(gap):
                return False
            if any(offset + cue.end() <= s and e <= start for s, e in blocking_spans):
                return False
            if _DISCOURSE_REFUSAL_RE.match(cue.group(0)) and _PUNCTUATION_GAP_RE.match(gap):
                return False
            from core.followup_subject_continuity import CORRECTION_OPENING_RE
            return not CORRECTION_OPENING_RE.match(gap.lstrip(" ,.:;!-"))
        # +1 for the single terminator character the split consumed (runs of terminators are
        # rare in a request; a miss here errs toward NOT negating, the conservative direction).
        offset = end + 1
    return False


# "how much" measures a DEGREE when a verb or preposition follows ("how much is gold", "how much
# does gold cost", "how much for gold") and an AMOUNT when a noun follows directly ("how much gold
# is there", "how much money"). Only the degree reading is market semantics; the amount reading
# is a quantity question about the noun. The distinction is the grammar of the quantifier, not a
# list of subjects: any noun after "how much" reads the same way.
_DEGREE_FOLLOWERS = frozenset({
    "is", "are", "was", "were", "am", "be", "does", "do", "did", "has", "have", "had", "will",
    "would", "should", "could", "can", "might", "may", "must", "for", "to", "per", "in", "at",
    "of", "'s", "s", "the",
})
_HOW_MUCH_RE = re.compile(r"^how\s+(?:much|many)$", re.IGNORECASE)
_NEXT_WORD_RE = re.compile(r"^\s*([A-Za-z']+)")


def _match_is_market_reading(clause: str, match: re.Match[str]) -> bool:
    """False for the one market term whose sense depends on what follows it ("how much")."""
    if not _HOW_MUCH_RE.match(match.group(0)):
        return True
    following = _NEXT_WORD_RE.match(clause[match.end():])
    if following is None:
        return True
    return following.group(1).lower() in _DEGREE_FOLLOWERS


def _has_unnegated_match(text: str, pattern: re.Pattern[str]) -> bool:
    """True when `pattern` matches somewhere no explicit negation cue governs it."""
    for clause in _CLAUSE_SPLIT_RE.split(str(text or "")):
        if not clause.strip():
            continue
        for match in pattern.finditer(clause):
            if not _match_is_market_reading(clause, match):
                continue
            if not _is_negated(clause, match.start()):
                return True
    return False


def market_semantics_present(text: str) -> bool:
    """True when the REQUEST ITSELF is about price/quote/market value/trading/market movement.

    This is the strict reading, and the one that gates every entity-presence path: naming gold is
    not enough, the message has to say something about the market. Recency markers alone
    ("today", "now") deliberately do NOT satisfy this — see `RECENCY_MARKERS`.
    """
    return _has_unnegated_match(text, _MARKET_TERM_RE) or _has_unnegated_match(
        text, _MARKET_MOVEMENT_RE
    )


def market_term_spans(text: str) -> tuple[tuple[int, int], ...]:
    """Every unnegated market-term / market-movement match, as `(start, end)` offsets into `text`.

    The same matchers, the same clause splitting and the same negation handling
    `market_semantics_present` uses -- returning WHERE the evidence is instead of merely whether any
    exists. `core.semantic_claim_authority` needs the positions to decide whether a market term is
    predicated of a particular asset mention, and it must not answer that from a second copy of this
    vocabulary: a private copy cannot be sabotaged through this module, drifts the moment either
    side is edited, and is the "four readings of one question" defect this module was created to
    end. This is the fifth reader, and it reads THESE matchers.

    Recency markers are deliberately absent, exactly as in `market_semantics_present`: "tonight" is
    not evidence that the word beside it is an asset, and admitting it here would make "restaurants
    near me tonight" bind `near` to a market term.
    """

    spans: list[tuple[int, int]] = []
    offset = 0
    for clause in _CLAUSE_SPLIT_RE.split(str(text or "")):
        if clause.strip():
            for pattern in (_MARKET_TERM_RE, _MARKET_MOVEMENT_RE):
                for match in pattern.finditer(clause):
                    if not _match_is_market_reading(clause, match):
                        continue
                    if not _is_negated(clause, match.start()):
                        spans.append((offset + match.start(), offset + match.end()))
        # `re.split` drops the separators, so advance by the clause plus the one delimiter char it
        # was followed by. Offsets only need to be monotonic and inside the right clause for the
        # token-window arithmetic downstream; `tests/test_semantic_claim_authority.py` pins them
        # against real text rather than trusting this arithmetic.
        offset += len(clause) + 1
    return tuple(sorted(set(spans)))


def market_quote_intent_present(text: str) -> bool:
    """The QUOTE recognizers' admission test: market semantics, or a bare recency marker.

    Strictly wider than `market_semantics_present`, because a bare recency marker admits here and
    not there — see `RECENCY_MARKERS`.

    The contract, stated as what the differential actually proves rather than as compatibility
    with the pre-repair containment test. An earlier version of this docstring claimed it was
    "exactly as wide as the pre-repair `wants_quote` containment test was", and that is NOT true
    and cannot be made true: containment admitted 1,805 dictionary word-shapes, the large majority
    of them accidental collisions this deliberately drops. What it admits is:

        bounded legitimate market semantics
          minus anything under an explicit negation
          minus accidental substring collisions
          minus the cue-derived words held back on meaning
            (see MARKET_TERM_EXCLUSIONS_BY_SENSE)

    Every legitimate market word-form the released base admitted is in `MARKET_TERMS`. "Every" is
    not an assurance here -- `tests/test_market_intent.py::test_no_cue_aligned_word_is_left_
    unclassified` re-derives the whole inventory and fails if any word is left in no bucket.
    """
    return market_semantics_present(text) or _has_unnegated_match(text, _RECENCY_RE)


def market_terms_are_negated(text: str) -> bool:
    """True when the message names market terms and EVERY one of them is explicitly negated.

    The signature of a correction ("Not price — I mean the chemical structure of gold"): the user
    raised the market only to rule it out. Reported separately from `market_semantics_present`
    returning False, because "no market words at all" and "market words, all rejected" are
    different facts and a caller may want to log or explain the second one.
    """
    combined = re.compile(
        _MARKET_TERM_RE.pattern + "|" + _MARKET_MOVEMENT_RE.pattern, re.IGNORECASE
    )
    saw_any = False
    for clause in _CLAUSE_SPLIT_RE.split(str(text or "")):
        if not clause.strip():
            continue
        for match in combined.finditer(clause):
            saw_any = True
            if not _NEGATION_CUE_RE.search(clause[: match.start()]):
                return False
    return saw_any
