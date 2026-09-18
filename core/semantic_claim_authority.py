"""Resolution is not authorization: which MENTIONS a financial specialist may claim.

The defect this module exists to close
--------------------------------------
Human test at 2fae1489, through the shipped app::

    U: Based on my exact current city and today's date from your system context, search the web
       for three highly-rated, mid-priced dinner restaurants near me that are open tonight.
       Format the results into a clean Markdown table with the columns: Restaurant Name,
       Cuisine, Price Range, and Rating.

    A: Near: USD 1.62 (24h change: -0.88%). Source: CoinGecko

The ordinary English preposition "near" became a crypto asset and preempted the whole turn.

`core.market_intent` already holds "an asset NAME alone is never evidence of a market request", and
it holds. This is the *next* wrong decision, one layer down, and both operands of it are TURN-GLOBAL::

    # tools/web/web_research._looks_like_price_query_all
    if not market_quote_intent_present(lowered):      # does this TURN mention markets anywhere?
        return []
    ... then resolve every alias and every CoinGecko-index token ANYWHERE in the turn ...

Nothing binds the two to each other. The restaurant request really does contain market vocabulary
("mid-priced", "Price Range", "Rating") -- predicated of RESTAURANTS -- and that turn-global True
was then spent authorizing the resolution of an unrelated token elsewhere in the sentence. Measured
at the base: the same sentence *without* a price word does not hijack, which is precisely why a
denylist of ordinary words is the wrong repair. The collision is not "near"; it is that domain
evidence about one subject licenses entity resolution about another.

The invariant
-------------
    A specialist may resolve an entity only where the request's domain evidence is predicated of
    THAT MENTION. Entity-table membership never establishes its own domain, and capitalisation
    carries no authority at all.

So authority is answered PER MENTION, not per turn. `market_quote_intent_present(text)` asks a
question no resolver should have been allowed to spend; `mention_is_market_authorized(text, start,
end)` asks the question that actually licenses a lookup.

What authorizes a mention
-------------------------
Four sources, each a bounded, closed-class rule rather than a vocabulary of the words to reject:

1. **Explicit `$TOKEN` syntax.** `$NEAR`, `$near`, `$BTC` self-authorize with no supporting market
   context, because the sigil IS the declaration of asset intent. This is the ONLY token syntax
   allowed to self-authorize, and at the base it did not work at all.
2. **Bound market evidence.** A market term from `core.market_intent.MARKET_TERMS` close enough to
   the mention that nothing else could be its subject -- separated only by FUNCTION words. "NEAR
   price", "price of NEAR", "gold prices", "rates for gold" bind; "mid-priced dinner restaurants
   near me" does not, because the content nouns "dinner restaurants" sit between the price word and
   the mention and are what the price is predicated of.
3. **A market-domain heading.** A clause led by a domain NOUN ("Market data only for Bitcoin and
   gold.", "Markets: Ethereum, Solana, gold, silver.") scopes its own list. Deliberately narrower
   than `MARKET_TERMS`: a leading price PREDICATE does not scope a clause, which is what keeps
   "how much does a taxi near the station cost" ordinary.
4. **Coordination with a curated asset.** Two or more CURATED assets joined by a coordinator
   ("compare BTC and ETH", "BTC vs ETH") are market evidence in themselves -- a coordinated list of
   well-known tickers is not a sentence of ordinary English. Restricted to the curated table on
   purpose: the unbounded CoinGecko index is exactly where the ordinary-word collisions live.

And one DIS-qualifier, because bound evidence alone cannot separate "price of a kite at the beach
shop" from "price of NEAR" -- they are the same syntax. An asset is a proper or mass noun and does
not take an indefinite article or a possessive, so "a kite", "a cake", "my dash" are common nouns
whatever price word sits beside them. Definite "the" is deliberately not a disqualifier: "the gold
price" is ordinary phrasing for a real question.

Known limitation, stated rather than papered over
--------------------------------------------------
A collision token used as a bare NOUN MODIFIER with a market word bound to it -- "the cost of render
farm time" -- is still authorized. A "followed by a content noun means modifier" rule was written for
exactly that sentence and then REMOVED: measured, it refused six genuine requests, including "How
much is gold right now?" and four of the Mnemosyne list reproductions. One invented sentence does not
justify a rule that costs real ones. The shapes it was aimed at ("gold medals", "a silver lining")
are already ordinary through the determiner rule or through carrying no bound market term at all.

Why this generalises past "near"
--------------------------------
Nothing here names near, rain, atom, real, link, hash or meta -- all of which the live index really
does resolve (measured). An ordinary word used ordinarily has no market term bound to it, so it is
unauthorized by construction, and a CoinGecko listing nobody has seen yet is harmless on the day it
appears. The test corpus may name collisions; production code may not, and does not.

Scope
-----
Crypto/market asset admission and the FX/currency mirror (`core.currency_intent` reads the same
contract). Place/location recognition does NOT share this seam -- it resolves through
`tools.web.web_research._extract_weather_locations` with its own plausibility filter and never
consults `market_quote_intent_present` -- so it is recorded rather than folded in.
"""

from __future__ import annotations

import re

from core.market_intent import market_term_spans

#: Closed-class grammar: if only these sit between a market term and a mention, nothing else could
#: be the market term's subject. Bounded by the language rather than by any domain, the same basis
#: `core.live_data_continuation._NON_SUBJECT_GRAMMAR_RE` already uses. This is NOT a list of words
#: to reject -- it is the list of words that cannot be a subject.
_FUNCTION_WORDS = frozenset(
    ["a", "an", "the", "this", "that", "these", "those", "my", "your", "our", "its", "his", "her", "their", "whose", "of", "in", "on", "at", "to", "for", "from", "by", "with", "without", "into", "onto", "over", "under", "about", "as", "per", "and", "or", "nor", "but", "so", "then", "than", "versus", "vs", "v", "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did", "doing", "done", "has", "have", "had", "having", "can", "could", "will", "would", "shall", "should", "may", "might", "must", "i", "you", "he", "she", "it", "we", "they", "me", "him", "us", "them", "what", "which", "who", "whom", "when", "where", "how", "why", "there", "here", "now", "today", "tonight", "currently", "current", "latest", "only", "just", "also", "even", "still", "yet", "very", "much", "many", "more", "most", "some", "any", "all", "each", "both", "please", "pls", "thx", "thanks", "s", "t", "re", "ve", "ll", "d", "m"]
)

#: Market DOMAIN nouns -- the subset of `MARKET_TERMS` that names the market itself rather than
#: predicating a price of something. Only these may head a clause and scope the list inside it.
#: "how much"/"cost"/"rate" are price predicates and deliberately absent: they are exactly what an
#: ordinary sentence about the cost of a taxi contains.
_MARKET_DOMAIN_HEADINGS = (
    "market data", "market prices", "market price", "market value", "market values",
    "market cap", "market caps", "markets", "market",
    "spot price", "spot prices", "exchange rate", "exchange rates",
    "tickers", "ticker", "trading", "valuation", "valuations",
)

#: Transaction verbs. Asking to buy or sell an asset is market intent even with no price word --
#: "buy BTC" named no price at the base and was not admitted. A bounded, positive vocabulary.
_TRANSACTION_TERMS = frozenset(("buy", "buys", "buying", "bought", "sell", "sells", "selling", "sold",
                      "purchase", "purchases", "purchasing", "swap", "swaps", "swapping",
                      "trade", "trades", "trading", "convert", "converts", "converting"))

#: How many tokens away a market term may sit and still bind. Wide enough for "price of NEAR" and
#: "rates for gold", narrow enough that a term two clauses away cannot reach.
_BIND_WINDOW_TOKENS = 4

_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9\-]*")
_COORDINATOR = frozenset({"and", "or", "vs", "versus", "v", "&", ","})

#: Determiners a proper/mass asset noun cannot take. Closed class, and deliberately WITHOUT "the":
#: "the gold price" is ordinary phrasing for a real market question.
_COMMON_NOUN_DETERMINERS = frozenset(
    {"a", "an", "my", "your", "his", "her", "its", "our", "their", "another", "every", "each"}
)

def _tokens(lowered: str) -> list[tuple[int, int, str]]:
    """(start, end, word) for every word in `lowered`, in order."""

    return [(m.start(), m.end(), m.group(0)) for m in _WORD_RE.finditer(lowered)]


def _market_term_token_indexes(toks: list[tuple[int, int, str]], lowered: str) -> set[int]:
    """Token indexes at which market evidence begins.

    The vocabulary and its matcher belong to `core.market_intent` and are asked for at CALL time,
    never copied here: a private copy drifts, and cannot be sabotaged through the module that owns
    it. `market_term_spans` already applies that module's clause splitting and negation handling, so
    "Not price -- I mean the chemical structure of gold" contributes no binding evidence.

    `_TRANSACTION_TERMS` is the one local addition, and it is local on purpose. It only ever
    authorizes a mention it is BOUND to; adding it to `MARKET_TERMS` would widen
    `market_semantics_present` for every caller in the runtime, which is a compatibility change with
    its own inventory and guard test (`tests/test_market_intent.py`) and no defect asking for it.
    """

    marked: set[int] = set()
    for start, end in market_term_spans(lowered):
        for index, (tok_start, tok_end, _word) in enumerate(toks):
            # EVERY token the span covers is market evidence, not just the one
            # where the span begins: for a multiword anchor ("24 change", "day
            # change", "market cap") the binding window must be able to reach
            # the asset from the anchor's content word ("change"), which can
            # sit closer to the asset than the span's first token. Measured
            # live: "24 change on eth" failed to bind eth because only "24"
            # was marked and "change" broke the function-words-only rule.
            if tok_start < end and tok_end > start:
                marked.add(index)
    for index, (_start, _end, word) in enumerate(toks):
        if word in _TRANSACTION_TERMS:
            marked.add(index)
    return marked


def _market_term_span_start_indexes(toks: list[tuple[int, int, str]], lowered: str) -> set[int]:
    """Token indexes where a market-evidence span BEGINS (one per span)."""
    starts: set[int] = set()
    for start, _end in market_term_spans(lowered):
        for index, (tok_start, tok_end, _word) in enumerate(toks):
            if tok_start <= start < tok_end:
                starts.add(index)
                break
    return starts


def _token_index_covering(toks: list[tuple[int, int, str]], start: int) -> int | None:
    for index, (tok_start, tok_end, _word) in enumerate(toks):
        if tok_start <= start < tok_end:
            return index
    return None


def _only_function_words_between(toks: list[tuple[int, int, str]], lo: int, hi: int) -> bool:
    """Whether every token strictly between `lo` and `hi` is a function word."""

    return all(toks[i][2] in _FUNCTION_WORDS for i in range(lo + 1, hi))


def _domain_member_token_indexes(toks: list[tuple[int, int, str]], lowered: str) -> set[int]:
    """Token indexes the DOMAIN'S OWN membership authority proves are assets.

    This is the identity the coordination grammar below used to re-derive and get wrong: a run of
    jammed list items ("price bnb usdt and TRX") has no coordinator between "bnb" and "usdt", so
    the function-words-only walk treated each resolved member as a chain-breaking content word and
    unauthorized every item after the first -- measured live, 3 requested assets became 1 fetched
    quote. Membership is answered by the curated table and the rank-bounded coin index, never by
    this module: "dinner" and "restaurants" resolve in neither, so "mid-priced dinner restaurants
    near me" still blocks NEAR exactly as before.
    """

    members: set[int] = set()
    try:
        from tools.web.coin_index import resolve_tokens

        resolved_starts = {start for start, _coin_id in resolve_tokens(lowered, skip_spans=())}
    except Exception:
        resolved_starts = set()
    for index, (tok_start, _tok_end, word) in enumerate(toks):
        if tok_start in resolved_starts or _is_curated_alias(word):
            members.add(index)
    return members


def _only_list_fillers_between(
    toks: list[tuple[int, int, str]], lo: int, hi: int, member_indexes: set[int]
) -> bool:
    """Whether everything strictly between `lo` and `hi` is list filler: a function word, or a
    token the domain itself proves is a member of the same list ("price bnb usdt and TRX" -- bnb
    and usdt sit between "price" and TRX and ARE members, not chain breakers)."""

    return all(
        toks[i][2] in _FUNCTION_WORDS or i in member_indexes for i in range(lo + 1, hi)
    )


def _clause_start(lowered: str, position: int) -> int:
    """Offset of the start of the clause containing `position` (sentence/`;`/`:` bounded)."""

    boundary = max(lowered.rfind(ch, 0, position) for ch in ".;:!?\n")
    return 0 if boundary < 0 else boundary + 1


def _clause_is_market_headed(lowered: str, position: int) -> bool:
    """Whether the clause containing `position` opens with a market DOMAIN noun.

    Two conditions, and the first is what keeps this from being a private second vocabulary:
    `core.market_intent` must recognise the opening term (asked for at call time, so removing a term
    from ITS matcher removes it here), and this module narrows that to the DOMAIN-noun subset. A
    leading price PREDICATE does not scope a clause, which is what keeps "how much does a taxi near
    the station cost" ordinary while "Market data only for Bitcoin and gold." scopes its own list.

    `tests/test_live_data_plan.py::test_sabotage_removing_market_from_the_vocabulary_reproduces_the
    _no_keyword_incident` is what forced this: with a private tuple here, that sabotage stopped
    biting, because the heading rule kept admitting the turn after the vocabulary it claims to read
    had been removed.
    """

    start = _clause_start(lowered, position)
    clause = lowered[start:position]
    stripped = clause.lstrip()
    if not stripped:
        return False
    lead = start + (len(clause) - len(stripped))
    if not any(span_start == lead for span_start, _span_end in market_term_spans(lowered)):
        return False
    return any(stripped.startswith(heading) for heading in _MARKET_DOMAIN_HEADINGS)


def has_explicit_asset_sigil(text: str, start: int) -> bool:
    """Whether a `$` precedes the mention beginning at `start`, across whitespace only.

    The one syntax allowed to self-authorize. `$NEAR` is a declaration of asset intent; `near` in a
    sentence about restaurants is a preposition, and no amount of capitalisation changes that.

    Whitespace is skipped because `core.input_normalizer` rewrites `$NEAR` to `$ NEAR` -- measured
    at the real seam, where `$NEAR` classified as market and the app still answered it as ordinary
    chat, because by the time the text reached here the sigil was no longer adjacent. A strict
    adjacency test is correct about English and wrong about this runtime's own pipeline.
    """

    body = str(text or "")
    index = start - 1
    while index >= 0 and body[index] in " \t":
        index -= 1
    return index >= 0 and body[index] == "$"


def _coordination_chain(toks: list[tuple[int, int, str]], here: int) -> list[int]:
    """Token indexes coordinated with `here` -- "SUI and TIA", "gold, silver, bitcoin".

    A price predicate distributes over a coordinated list: "price of SUI and TIA" prices both, and
    only SUI is adjacent to the word "price". Members are linked by coordinators and function words
    only, so a content word breaks the chain -- "the sky is clear and the grass is green" is two
    sentences' worth of subject, not a list of assets.
    """

    chain = [here]
    for step in (-1, 1):
        index = here + step
        saw_coordinator = False
        while 0 <= index < len(toks):
            word = toks[index][2]
            if word in _COORDINATOR:
                saw_coordinator = True
            elif word in _FUNCTION_WORDS:
                pass
            elif saw_coordinator:
                chain.append(index)
                saw_coordinator = False
            else:
                break
            index += step
    return chain


def price_assets_named(text: str, *, domain_already_authorized: bool = False) -> list[str]:
    """Every asset alias `text` AUTHORIZES as a market asset, in mention order.

    The ranking lives with the alias tables in `core.agent_runtime.fast_live_info_price`; the
    authorization of each mention is decided HERE (`mention_is_market_authorized`). Exposed on
    this module because it is the authority callers reason about -- the conversation-truth kit
    imports it from here -- and imported lazily because the price lane imports this module.
    """
    from core.agent_runtime.fast_live_info_price import price_assets_named as _ranked

    return _ranked(text, domain_already_authorized=domain_already_authorized)


def _purchase_frame_names(lowered: str, word: str) -> bool:
    """Whether the purchasing-power grammar binds `word` as the payment or a target of `lowered`.

    Lazy import: the conductor is a client of this module's callers. A frame with a problem (an
    unpriceable payment, a missing or ambiguous target) authorizes nothing -- unless "and"
    distributes one payment over several targets, which resolves to one clean role set per target.
    """
    needle = str(word or "").strip().casefold()
    if not needle:
        return False
    from core.conductor.operations import distributed_purchase_roles, resolve_purchase_roles

    try:
        roles = resolve_purchase_roles(lowered)
    except Exception:
        return False
    if roles is None:
        return False
    role_sets = [roles] if not roles.problem else list(distributed_purchase_roles(lowered))
    for item in role_sets:
        if item.problem or item.target is None or item.payment is None:
            continue
        if needle in item.target.names() or needle in item.payment.names():
            return True
    return False


def mention_is_market_authorized(
    text: str,
    start: int,
    end: int,
    *,
    curated: bool = False,
    domain_already_authorized: bool = False,
    sibling_spans: tuple[tuple[int, int], ...] = (),
) -> bool:
    """Whether the mention at `[start, end)` may be resolved as a market asset.

    `curated` marks a mention that came from the closed, well-known alias table rather than the
    unbounded live index; it only ever enables the coordination rule, never bypasses the others.

    `domain_already_authorized` is for callers that hold INDEPENDENT domain evidence this sentence
    does not carry -- a live `market_quote` obligation from the previous turn, which is what makes
    "i mean btc", "what about solana" and "or ethereum?" market requests. That is the invariant
    satisfied, not bypassed: the domain was established before the entity was resolved, just by the
    conversation rather than by this sentence. It is a caller-supplied FACT about the thread, never
    something an entity table may assert about itself, which is why it is an explicit argument
    rather than something this module tries to infer.
    """

    body = str(text or "")
    if not body or start >= end:
        return False
    lowered = body.lower()

    if has_explicit_asset_sigil(body, start):
        return True
    if domain_already_authorized:
        # ...unless THIS sentence explicitly withdraws the market reading. "Not price -- I mean the
        # chemical structure of gold." is the user saying the thread's domain does not apply here,
        # and a caller's standing domain evidence may not out-vote that. Without this the flag
        # bypassed `core.market_intent`'s negation handling entirely and the G4 correction went
        # straight back to a market quote -- caught by the release gauntlet, not by the focused
        # suites.
        from core.market_intent import market_terms_are_negated, mention_is_negated

        if market_terms_are_negated(body):
            return False
        # ...and a standing domain never authorizes a mention the sentence itself rules out.
        # "not gold, silver" corrects FROM gold TO silver: the first alias is cue-negated and the
        # second is the request. The guard above keys on market TERMS and cannot see this; the
        # negation law is applied here per mention, at the mention's own position, so a
        # correction can never be answered with the exact thing it corrected away (CT-202).
        return not mention_is_negated(
            body, start, blocking_spans=tuple((s_, e_) for s_, e_ in sibling_spans if e_ <= start)
        )

    # A mention the purchasing-power grammar binds to a ROLE is authorized by that grammar. "If I
    # sell half a bitcoin, how many ounces of silver does that get me?" names its payment and its
    # target through `core.conductor.operations.resolve_purchase_roles` -- the one place the
    # purchase shape is read -- and carries no price word at all. Measured on the built candidate
    # 60a91da3 (2026-09-07): every payment-leg phrasing named no asset here, the conductor's
    # deterministic arm had nothing to quote, and the turn fell to a model lane. Only a FULLY
    # resolved frame authorizes (both roles, no problem), so "I have a dash of salt, how much
    # pepper can I get" -- a payment with no priceable target -- stays a cooking sentence. Read
    # before the determiner rule below: "half a bitcoin" is an amount, not a common noun.
    if _purchase_frame_names(lowered, lowered[start:end]):
        return True

    toks = _tokens(lowered)
    here = _token_index_covering(toks, start)
    if here is None:
        return False

    # An asset is a proper/mass noun: "NEAR", "gold", "BTC". It does not take an indefinite article
    # or a possessive, so "a kite", "a cake", "my dash" are common nouns whatever a price word
    # beside them says -- and "price of a kite at the beach shop" is otherwise syntactically
    # identical to "price of NEAR". Definite "the" is deliberately NOT here: "the gold price" is a
    # perfectly ordinary way to ask.
    if here > 0 and toks[here - 1][2] in _COMMON_NOUN_DETERMINERS:
        return False

    # A "token followed by a content noun is a MODIFIER, not a head" rule was written here and
    # REMOVED, measured rather than reasoned. It was meant for "the cost of render farm time", and
    # it cost six real requests: "How much is gold right now?" ("right"), and four of the Mnemosyne
    # list reproductions. One invented sentence is not worth a rule that refuses genuine market
    # questions, and the same modifier shapes it targeted ("gold medals", "a silver lining") are
    # already ordinary through the determiner rule above or through having no bound market term at
    # all. The remaining gap is stated in this module's Known limitation note instead of being
    # papered over by a rule that does more damage than the case it was for.
    market_indexes = _market_term_token_indexes(toks, lowered)
    # The member-filler rule below keeps its ORIGINAL span-start anchoring: a
    # jammed list binds to the term as a unit ("price bnb usdt"), and letting a
    # mid-span token anchor it re-authorized "how much for a cake NEAR me"
    # (measured: marking "much" put NEAR inside the window with CAKE as an
    # apparent fellow list member). The function-words rule above may use every
    # token a multiword span covers, which is what makes "24 change ON eth" and
    # "day change ON gold" bind at all.
    market_start_indexes = _market_term_span_start_indexes(toks, lowered)

    # Every member of this mention's coordinated list, because a price predicate distributes over
    # the list: "price of SUI and TIA" prices both, and only SUI sits beside the word "price".
    # A jammed list ("price bnb usdt and TRX") coordinates without typing the coordinators, so the
    # binding span may also cross tokens the domain itself proves are fellow list members -- but
    # only when the ANCHOR is one: "write a rhyme about rain, and give me BTC price" chains "rain"
    # to "give", and "give" is no asset; relaxing the span for any anchor re-authorized the
    # ordinary word (measured by test_one_asset_obligation_does_not_erase_its_unrelated_siblings).
    member_indexes: set[int] | None = None
    for anchor in _coordination_chain(toks, here):
        for index in market_indexes:
            if index == anchor:
                continue
            lo, hi = (index, anchor) if index < anchor else (anchor, index)
            if hi - lo > _BIND_WINDOW_TOKENS:
                continue
            if _only_function_words_between(toks, lo, hi):
                return True
        for index in market_start_indexes:
            if index == anchor:
                continue
            lo, hi = (index, anchor) if index < anchor else (anchor, index)
            if hi - lo > _BIND_WINDOW_TOKENS:
                continue
            if member_indexes is None:
                member_indexes = _domain_member_token_indexes(toks, lowered)
            if (
                anchor in member_indexes
                and _only_list_fillers_between(toks, lo, hi, member_indexes)
            ):
                return True

    if _clause_is_market_headed(lowered, start):
        return True

    if curated:
        for neighbour in (here - 2, here + 2):
            if 0 <= neighbour < len(toks):
                between = here + 1 if neighbour > here else here - 1
                if 0 <= between < len(toks) and toks[between][2] in _COORDINATOR:
                    if _is_curated_alias(toks[neighbour][2]):
                        return True
        # "BTC, ETH" -- a bare comma coordination has no token between the two mentions.
        for neighbour in (here - 1, here + 1):
            if 0 <= neighbour < len(toks) and _is_curated_alias(toks[neighbour][2]):
                gap = lowered[toks[min(here, neighbour)][1] : toks[max(here, neighbour)][0]]
                if gap.strip() in {",", "/", "&"}:
                    return True
    return False


def _is_curated_alias(word: str) -> bool:
    """Whether `word` is in the closed, well-known alias table (never the live index)."""

    try:
        from core.agent_runtime.fast_live_info_price import _PRICE_ASSET_ALIASES

        return word in _PRICE_ASSET_ALIASES
    except Exception:
        return False


def authorized_market_spans(text: str, spans) -> list[tuple[int, int]]:
    """The subset of `spans` this request authorizes for market resolution, order preserved."""

    body = str(text or "")
    out: list[tuple[int, int]] = []
    for span in list(spans or []):
        try:
            start, end = int(span[0]), int(span[1])
        except Exception:
            continue
        word = body[start:end].lower()
        if mention_is_market_authorized(body, start, end, curated=_is_curated_alias(word)):
            out.append((start, end))
    return out


__all__ = [
    "authorized_market_spans",
    "has_explicit_asset_sigil",
    "mention_is_market_authorized",
]
