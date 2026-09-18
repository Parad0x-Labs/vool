from __future__ import annotations

import re
from typing import Any

from core.agent_runtime.fast_live_info_rendering import first_live_quote
from core.followup_subject_continuity import correction_reopens_the_market_lane
from core.retrieval_constraints import analyze_retrieval_constraints

# NOTE: "quote" was removed — it is an x402 flow stage word ("request, quote, verify, settle,
# receipt"), so treating it as a price marker routed x402 explanations to crypto quotes. All
# markers are matched on word boundaries (see _has_price_marker) so "rate" no longer matches
# inside "sepa-rate", which is how a `.null`/payment-rails prompt became a Polkadot price.
# NOTE: "value" was removed for the same reason as "quote". It is the ordinary English word for
# the contents of a variable, a JSON key or a register, so it carried three unrelated prompts into
# the market lane in one blind test set: `... key "auth_status" and value "denied"`, `What is the
# final value of Q?` (registers P/Q/R, where `Op 1:` also resolved `op` -> Optimism off the live
# ticker index), and `key "code" with integer value 200`. Each was answered with a live crypto
# quote and the model was never called. "worth", "how much", "cost", "trading at", "market cap",
# "price" and "rate" still carry every genuine phrasing; a bare "value of X" now reaches the model
# instead of the price lane, which is the correct owner for an ambiguous word.
_PRICE_REQUEST_MARKERS = (
    "price",
    "cost",
    "worth",
    "rate",
    "market cap",
    "trading at",
    "how much",
)
_PRICE_MARKER_RE = re.compile(
    r"\b(?:price|cost|worth|rate|market cap|trading at|how much|"
    r"(?:24h|24|24hr|day|daily|weekly|monthly|ytd|percent)\s+change|"
    r"change (?:today|now))\b"
    r"|%\s*change\b",
    re.IGNORECASE,
)
# A turn about the Web0 stack or the x402 payment flow must never trigger a market-price lookup,
# even when it mentions an asset name (x402 is always "on Solana") or contains the stage word
# "quote". This guards the recovery path that otherwise harvests an asset subject from history.
# NOTE: finance-ambiguous words ("settle", "receipt", "arweave"/AR) are intentionally NOT standalone
# guard tokens — they wrongly blocked legit price queries ("what price did bitcoin settle at?"). A
# real x402 flow still trips the guard via >= 2 of _X402_FLOW_WORD_RE below.
_WEB0_X402_GUARD_RE = re.compile(
    r"(?:web[\s-]?0\b|\bx402\b|\.null\b|\bdot null\b|null_registrar|\bnulla\b|\bopenclaw\b|"
    r"dark[\s-]?null|payment rail|payment rails)",
    re.IGNORECASE,
)
_X402_FLOW_WORD_RE = re.compile(
    r"\b(?:request|quote|verify|settle|settlement|receipt|pay|payment|stage|stages)\b",
    re.IGNORECASE,
)


def _has_price_marker(text: str) -> bool:
    return bool(_PRICE_MARKER_RE.search(str(text or "")))


def looks_like_web0_or_x402_context(text: str) -> bool:
    low = str(text or "")
    if _WEB0_X402_GUARD_RE.search(low):
        return True
    return len(_X402_FLOW_WORD_RE.findall(low)) >= 2
_PRICE_ASSET_ALIASES = (
    "bitcoin",
    "btc",
    "ethereum",
    # "ether" is the ordinary name of the unit, and "how much is one ether in usd right now" is
    # listed in tests/test_price_lane_does_not_claim_the_word_value.py as a phrasing the lane must
    # keep. It was never in this table: measured at 960c0240, `_extract_price_asset_alias` returned
    # "" for that sentence and the lane claimed it only through the display-helper arm in
    # `looks_like_grounded_price_lookup` that this change deletes. The table already carries the
    # long name and the ticker for every other asset; this is the missing third form, not a mapping
    # written to satisfy one prompt. Longest-first matching keeps "ethereum" from reporting as
    # "ether".
    "ether",
    "eth",
    "solana",
    "sol",
    "cardano",
    "ada",
    "polkadot",
    "dot",
    "dogecoin",
    "doge",
    "ripple",
    "xrp",
    "litecoin",
    "ltc",
    "avalanche",
    "avax",
    "chainlink",
    "link",
    "polygon",
    "matic",
    "binance",
    "bnb",
    "brent crude",
    "brent",
    "wti crude",
    "wti",
    "gold",
    "silver",
    "xau",
    "xag",
)
_PRICE_FOLLOWUP_PREFIX_RE = re.compile(
    r"^(?:no[\s,]+)?(?:i\s+mean|meant|what\s+about|how\s+about|btw|by\s+the\s+way)\b[\s,:-]*",
    re.IGNORECASE,
)


def recover_price_lookup_query(
    user_input: str,
    *,
    source_context: dict[str, Any] | None,
) -> str:
    constraints = analyze_retrieval_constraints(user_input)
    # P0 POLICY CONSERVATION — a price child runs a clean slice of a turn whose
    # parent may have frozen retrieval elsewhere in the message; the canonical
    # request riding the context widens this reading, so a frozen parent vetoes
    # the lookup exactly as the slice's own words would.
    from core.turn_prohibitions import conserve_retrieval_constraints

    constraints = conserve_retrieval_constraints(constraints, source_context)
    if constraints.forbids("market_prices"):
        return ""
    user_input = constraints.eligible_text
    lowered = " ".join(str(user_input or "").strip().lower().split())
    if not lowered:
        return ""
    if looks_like_web0_or_x402_context(lowered):
        return ""
    # The market lane answers ONE kind of question: a price. A conversion between measurement units
    # ("2 ounces of gold in grams") and a purchasable-amount derivation ("how much gold would that
    # get me") both name an asset and both say "how much", and neither is a price. Measured on the
    # packaged build 784cf713 (2026-09-08): this recovery re-admitted both after the mode classifier
    # had declined them, and each was answered with a sourced gold quote.
    from core.conductor.operations import asks_for_a_purchasable_amount
    from core.measurement_medium import requests_a_unit_conversion

    if requests_a_unit_conversion(lowered) or asks_for_a_purchasable_amount(lowered):
        return ""
    explicit_subject = _extract_price_asset_alias(lowered)
    has_price_marker = _has_price_marker(lowered)
    # A correction follow-up ("no i mean SOL") answers a price question with no price word in it,
    # so this branch is the one place in this module that fires with `_has_price_marker` False --
    # which also made `price_request_leaves_unresolved_content` below inert on it, since that guard
    # early-returns on a message with no price marker. Nothing here asked what the correction was
    # about, and nothing asked what it was correcting. Live on 2026-08-11, after two turns about
    # building a castle, "no i mean real castle" matched the prefix, resolved `real` against the
    # CoinGecko rank index (symbol REAL -> `reallink`, inside the top 250), and was rewritten to
    # `real price now`; the word "castle" was never looked at. `core.followup_subject_continuity`
    # is the authority that now decides it: a correction binds to the PRIOR subject, and re-opens
    # the market lane only when its residue is the asset alone AND the market is already on the
    # table. It is subtractive -- it can only withhold this branch, never admit a turn to it.
    looks_like_short_subject_followup = (
        bool(explicit_subject)
        and bool(_PRICE_FOLLOWUP_PREFIX_RE.match(lowered))
        and correction_reopens_the_market_lane(
            lowered,
            names=price_assets_named(lowered, domain_already_authorized=True),
            source_context=source_context,
        )
    )
    if explicit_subject and (has_price_marker or looks_like_short_subject_followup):
        # A price-shaped message naming something this runtime's alias table cannot resolve
        # (an unlisted ticker, or any other leftover content) must not be silently answered as if
        # only the recognized part existed. Three straight attempts to instead GUESS which leftover
        # word is the missing asset (a stopword blocklist, then a list-connector structural test)
        # each shipped a real regression -- see this module's git history and
        # tests/test_every_asset_asked_for_is_answered.py. Decline instead: the caller
        # (`prepare_live_info_request`) routes a decline to the tool-enabled lane so a real tool
        # can resolve what the alias table cannot, rather than a regex guessing at it.
        if price_request_leaves_unresolved_content(user_input):
            return ""
        # `_extract_price_asset_alias` is first-match-only, which is why "BNB and SOL" recovered
        # to "bnb price now" and SOL vanished before any search ran -- silently, since nothing
        # downstream ever saw its name. `price_assets_named` keeps every alias this runtime
        # recognizes (deterministic, no guessing) so a multi-recognized-asset request still answers
        # every asset named, not just the first.
        subjects = price_assets_named(str(user_input or ""), domain_already_authorized=True)
        if len(subjects) > 1:
            return f"{', '.join(subjects)} price now"
        return f"{explicit_subject} price now"
    # Harvest a prior asset from history only for a genuine follow-up: either an explicit
    # follow-up prefix ("what about now?") OR a bare price question that names NO topic of its
    # own ("what's the current price?"). A query that names its own topic -- even a non-crypto
    # one like "what is oil worth?" -- must NOT be rewritten to a previous asset's price. That
    # was the oil->Solana cross-entity contamination.
    if has_price_marker and (_PRICE_FOLLOWUP_PREFIX_RE.match(lowered) or not _has_own_topic_noun(lowered)):
        recent_subject = _recent_price_subject(source_context)
        if recent_subject:
            return f"{recent_subject} price now"
    return ""


# Generic price/question/function words that do NOT constitute a topic of the turn's own.
_PRICE_GENERIC_TOKENS = frozenset({
    "price", "cost", "worth", "value", "rate", "market", "cap", "trading", "how", "much",
    "what", "whats", "hows", "are", "the", "for", "its", "that", "this", "these", "those",
    "now", "today", "current", "currently", "latest", "recent", "tell", "give", "show", "and",
    "right", "help", "about", "going", "down", "maybe", "please", "pls", "you", "know", "mean",
    "meant", "btw", "way", "just", "still", "again",
})


def _has_own_topic_noun(text: str) -> bool:
    """True when the turn contains a content word of its own (e.g. 'oil', 'gold') beyond
    generic price/question words -- meaning it is NOT a bare anaphoric price follow-up."""
    tokens = re.findall(r"[a-z]+", str(text or "").lower())
    return any(len(token) >= 3 and token not in _PRICE_GENERIC_TOKENS for token in tokens)


def user_forbids_web(query: str) -> bool:
    """Compatibility wrapper for the shared explicit-retrieval constraint authority."""

    constraints = analyze_retrieval_constraints(query)
    return constraints.forbids_all_tools or constraints.forbids_external_retrieval


def looks_like_grounded_price_lookup(query: str) -> bool:
    constraints = analyze_retrieval_constraints(query)
    if constraints.forbids("market_prices"):
        return False
    candidate = constraints.eligible_text
    lowered = " ".join(str(candidate or "").strip().lower().split())
    if not lowered:
        return False
    if looks_like_web0_or_x402_context(lowered):
        return False
    if not _has_price_marker(lowered):
        return False
    # A price marker ALONE does not claim this lane. "price", "cost", "worth", "rate" and "how
    # much" are ordinary English for the cost of ANYTHING, so the marker establishes only that a
    # cost was asked about -- never that the subject is a traded asset. The second arm here used to
    # be `extract_price_lookup_subject(candidate)`, a DISPLAY helper whose job is to strip price
    # scaffolding off a bare ticker for echoing back; it returns non-empty for any sentence with a
    # content word in it, so it read as evidence on every cost question in the language. Measured
    # in the shipped app: "How much is a flight from Vilnius to Rome next weekend?" was claimed
    # here and answered "I couldn't map `is a flight from Vilnius to Rome next we...". The turn was
    # never an asset quote. A display helper may never be a claim predicate.
    return bool(_extract_price_asset_alias(lowered) or bare_unresolved_asset_subject(candidate))


def bare_unresolved_asset_subject(text: str) -> str:
    """The one bare noun an asset-quote request names, when this runtime cannot resolve it.

    This is the positive asset evidence for a turn whose subject is NOT in the alias table or the
    live index -- "what is Seth price?", "SUI price?", "what is ARB worth". Those are genuine
    market requests that deserve the "give me the exact ticker" clarification, and the lane must
    keep them. It must keep them without also claiming "How much is a hotel in Rome for 3 nights?".
    Returns the subject in the USER'S OWN CASING, so a caller echoing it quotes them verbatim.

    Two independent conditions, both required, neither sufficient:

    1. **The whole request reduces to one content token.** Everything else in the sentence is a
       price marker, a market term, or closed-class scaffolding. This is the evidence a per-mention
       test cannot supply: `mention_is_market_authorized` alone authorizes "prague" in "what does a
       train ticket from Berlin to Prague cost" (a market word is bound to it with nothing between,
       which is syntactically identical to "NEAR price") and "airline" in "...tell me the airline
       and price" (bound across a coordinator). Both measured, not assumed. Requiring that nothing
       ELSE in the turn could be the subject is what separates a bare quote request from a cost
       question that happens to end next to a price word.

    2. **That token is in asset position**, per `core.semantic_claim_authority` -- the runtime's
       existing authority on which mentions a request licenses as market assets, asked at call time
       so it cannot drift from a private copy here. Its determiner rule is what carries the weight:
       an asset is a proper or mass noun and does not take an indefinite article or a possessive,
       so "how much is a flight?" reduces to one content token and is still refused, because "a
       flight" is a common noun whatever price word sits beside it.

    `domain_already_authorized` is deliberately left at its default False. There is no prior-turn
    market obligation in play here -- this function is being asked to ESTABLISH the domain, so
    asserting it as an input would make the test answer itself.
    """

    collapsed = " ".join(str(text or "").strip().split())
    if not collapsed:
        return ""
    lowered = collapsed.lower()
    # Lazy: `core.semantic_claim_authority` reaches back into this module for the curated alias
    # table, so a top-level import here is a cycle.
    from core.market_intent import market_term_spans
    from core.semantic_claim_authority import (
        _FUNCTION_WORDS,
        _WORD_RE,
        mention_is_market_authorized,
    )

    covered = [match.span() for match in _PRICE_MARKER_RE.finditer(lowered)]
    covered.extend((start, end) for start, end in market_term_spans(lowered))
    residue: list[tuple[int, int, str]] = []
    for match in _WORD_RE.finditer(lowered):
        start, end = match.span()
        if any(start >= low and end <= high for low, high in covered):
            continue
        # `_FUNCTION_WORDS` is the authority's own closed class -- the words that cannot be a
        # subject -- and `_PRICE_GENERIC_TOKENS` is this module's price-question scaffolding.
        # Neither is a denylist of words to reject: a word wrongly present here only costs the
        # turn a deterministic quote it can still get from the tool lane.
        if match.group(0) in _FUNCTION_WORDS or match.group(0) in _PRICE_GENERIC_TOKENS:
            continue
        residue.append((start, end, match.group(0)))
        if len(residue) > 1:
            return ""
    if len(residue) != 1:
        return ""
    start, end, _word = residue[0]
    if not mention_is_market_authorized(lowered, start, end, curated=False):
        return ""
    return collapsed[start:end]


def _extract_price_asset_alias(text: str) -> str:
    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return ""
    from core.semantic_claim_authority import mention_is_market_authorized

    for alias in sorted(_PRICE_ASSET_ALIASES, key=len, reverse=True):
        for match in re.finditer(rf"\b{re.escape(alias)}\b", lowered):
            # The static loop used to return on a bare regex hit, consulting no authority at all --
            # the one production path into this lane that the domain gate did not cover. "Not price
            # -- I mean the chemical structure of gold." matched `gold` here and was rewritten to
            # "gold price now" with the negation never looked at. Caught by the release gauntlet's
            # G4 case, not by the focused suites.
            #
            # `domain_already_authorized=True` because this function is the naming PRE-gate for a
            # correction whose domain comes from the thread; the authority still applies the
            # negation rule, which is what this call is for.
            if mention_is_market_authorized(
                lowered, match.start(), match.end(), curated=True, domain_already_authorized=True
            ):
                return alias
    # This is the gate `recover_price_lookup_query` checks first: an empty return here abandons the
    # deterministic lane entirely, whatever `price_assets_named` would have found. Teaching only
    # that function about the live index was not enough -- measured in the running app on
    # 2026-08-04, "price of SUI and TIA" still spent 60.74s in the model lane and came back with no
    # answer, because this function had never heard of either ticker. Static aliases keep their
    # existing precedence and ordering; the index is consulted only when they find nothing at all.
    # Naming-only, deliberately: this is the PRE-gate `recover_price_lookup_query` checks before it
    # applies the real domain test (`_has_price_marker`, or a correction whose market is already on
    # the table via `correction_reopens_the_market_lane`). Asking the authority here would gate the
    # turn twice and refuse the bare corrections the caller exists to serve.
    resolved = price_assets_named(lowered, domain_already_authorized=True)
    return resolved[0] if resolved else ""


#: A TICKER as a person writes one: `$BASE`, or a short symbol followed by "coin"/"token"
#: ("BASE coin", "base coins"). Neither form is a word of ordinary English; both say "this is an
#: exchange symbol" before anything is looked up. Measured on the owner's turns (2026-09-10,
#: c647b707): "check $BASE price" and "$BASE coin should not be hard to find the value of" named
#: no curated alias, so no live-data lane claimed them and a model answered that it "doesn't have
#: real-time pricing" -- a capability the runtime has. The symbol is what the live-data plan
#: resolves (curated tables, then the coin index); an unresolved symbol is a STATED unsupported
#: entity with the identifying question, never a silent fall-through.
#: `\s?` because the served runtime reads `interpret_request`'s NORMALIZED text, which writes
#: `$BASE` as `$ BASE` (measured on rig s75, 2026-09-11: the in-process probe matched, the
#: daemon never did, and the turn fell to a model).
_TICKER_DOLLAR_RE = re.compile(r"\$\s?([A-Za-z]{2,6})\b")
_TICKER_COIN_RE = re.compile(r"\b([A-Za-z]{2,6})\s+(?:coins?|tokens?)\b", re.IGNORECASE)
_NOT_A_TICKER = frozenset(
    {"the", "this", "that", "any", "some", "each", "every", "one", "two", "new", "old", "meme",
     "stable", "alt", "my", "your", "our", "their", "his", "her", "its", "no", "few", "many",
     "more", "most", "all", "both", "what", "which", "base"} - {"base"}
)


def ticker_mentions(text: str, *, dollar_only: bool = False) -> list[str]:
    """Every ticker the message writes as one (`$SYM`, `SYM coin/token`), upper-cased, in order.

    `dollar_only` keeps the `$SYM` form alone: the dollar sign IS market notation, so such a
    mention establishes the market domain by itself ("$BASE coin should not be hard to find the
    value of"), while the coin/token form still needs the request to say something about the
    market before it is read as a lookup.
    """
    value = str(text or "")
    found: list[str] = []
    patterns = (_TICKER_DOLLAR_RE,) if dollar_only else (_TICKER_DOLLAR_RE, _TICKER_COIN_RE)
    for pattern in patterns:
        for match in pattern.finditer(value):
            symbol = match.group(1).upper()
            if symbol.casefold() in _NOT_A_TICKER or symbol in found:
                continue
            found.append(symbol)
    return found


def price_assets_named(text: str, *, domain_already_authorized: bool = False) -> list[str]:
    """Every asset alias the message AUTHORIZES, in the order it names them.

    `domain_already_authorized` is for callers holding independent domain evidence this text does
    not carry -- a live `market_quote` obligation from the previous turn, which is what makes the
    bare corrections "i mean btc" / "what about solana" / "or ethereum?" market requests. The domain
    is still established BEFORE the entity is resolved; it was established by the thread rather than
    by this sentence. See `core.semantic_claim_authority`.

    `_extract_price_asset_alias` returns the first and stops, which is why "btc price now? and sol
    price please" answered Bitcoin and said nothing about Solana. Longest-first matching keeps
    `bitcoin` from being reported twice as `btc`.
    """

    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return []
    hits: list[tuple[int, str]] = []
    claimed: list[tuple[int, int]] = []
    for alias in sorted(_PRICE_ASSET_ALIASES, key=len, reverse=True):
        for match in re.finditer(rf"\b{re.escape(alias)}\b", lowered):
            start, end = match.span()
            # A longer alias already covering this span wins: "bitcoin" must not also report "btc".
            if any(start >= s and end <= e for s, e in claimed):
                continue
            claimed.append((start, end))
            hits.append((start, alias))
    # Which of those mentions this REQUEST authorizes as market assets. Membership in the table
    # answers "could this token name an asset?"; it never answers "is this request about that
    # asset?", and reading the first as the second is what let the preposition in "restaurants near
    # me" become a CoinGecko quote. See `core.semantic_claim_authority`.
    from core.semantic_claim_authority import mention_is_market_authorized

    authorized_spans = {
        (start, end)
        for start, end in claimed
        if mention_is_market_authorized(
            lowered, start, end, curated=True, domain_already_authorized=domain_already_authorized,
            sibling_spans=tuple(claimed),
        )
    }
    hits = [
        (start, alias)
        for start, alias in hits
        if any(start == s for s, _e in authorized_spans)
    ]
    # `_PRICE_ASSET_ALIASES` is a fast offline shortcut, not the set of assets this runtime
    # supports. A ticker it never listed -- ARB was the live example on 2026-08-04 -- is resolved
    # against CoinGecko's rank-ordered index so the deterministic lane can answer it directly
    # instead of declining the whole turn. This is NOT the guess-which-leftover-token-is-an-asset
    # mechanism that regressed three times (see `price_request_leaves_unresolved_content` below):
    # nothing here decides a token is an asset, the index either lists the symbol or it does not.
    # Lazy import keeps tools.web off this module's import path at daemon boot.
    from tools.web.coin_index import resolve_tokens

    for start, _coin_id in resolve_tokens(lowered, skip_spans=claimed):
        match = re.match(r"[a-z0-9]+", lowered[start:])
        if not match:
            continue
        end = start + len(match.group(0))
        if not mention_is_market_authorized(
            lowered, start, end, curated=False, domain_already_authorized=domain_already_authorized,
            sibling_spans=tuple(claimed),
        ):
            continue
        hits.append((start, match.group(0)))
    return [alias for _position, alias in sorted(hits)]


# Ordinary price-question scaffolding beyond `_PRICE_GENERIC_TOKENS` (which is shared with
# `_has_own_topic_noun` and kept narrow on purpose for THAT use). This set is scoped to
# `price_request_leaves_unresolved_content` only: auxiliary verbs and finance-neutral filler that
# show up in a perfectly ordinary single-asset question ("what price did bitcoin SETTLE at",
# "what DOES it cost") and must not, by themselves, read as an unrecognized second asset. Being
# incomplete here only costs an extra tool-enabled turn (see the function's own docstring on why
# that asymmetry is deliberate) -- it can never cause a wrong or fabricated answer -- so this list
# does not need to be exhaustive to be safe.
_PRICE_LEFTOVER_FILLER_TOKENS = frozenset({
    "did", "does", "was", "were", "has", "have", "had", "will", "would", "could",
    "should", "settle", "settled", "settling", "closed", "closing", "opened",
    "opening", "traded", "trades", "stands", "standing", "sitting", "hit",
})


def price_request_leaves_unresolved_content(text: str) -> bool:
    """True when a price-shaped message plausibly names something beyond what this runtime's
    alias table can resolve -- used ONLY to decide ROUTING, never to name or fabricate what the
    extra content is.

    2026-08-03: "ok price for BNB and ARB please?" answered BNB and silently dropped ARB, because
    `_extract_price_asset_alias` was first-match-only. Two follow-up attempts then tried to instead
    GUESS which leftover word was the missing ticker -- a hand-written stopword blocklist first
    ("BTC PRICE?" -> phantom `PRICE` asset, because a blocklist of "common uppercase words" can
    never enumerate every ordinary word a person might type in caps), then a list-connector
    structural test second (missed a lowercase ticker outright, since the candidate regex was
    `[A-Z]{2,6}` only, and missed a connector-less enumeration like "BNB ARB AND SUI"). Three
    real, independently confirmed regressions from the same shape of mechanism: guessing WHICH
    token is an asset in order to name it.

    This does not repeat that mistake. It never claims a specific leftover token IS an asset --
    it only answers "is there anything left in this message once the recognized asset(s) and the
    ordinary price-question scaffolding are accounted for". A caller that gets True here declines
    the fast, deterministic answer and hands the turn to a lane where a real tool (and the model
    that decides how to use it) can resolve the rest -- see `prepare_live_info_request`. A false
    positive costs one extra tool-enabled turn; a false negative repeats the original incident.
    That asymmetry is the point: this is deliberately biased toward "ask a real tool" over "answer
    with what little the alias table covers and say nothing about the rest".
    """

    lowered = " ".join(str(text or "").strip().lower().split())
    if not lowered:
        return False
    if not _has_price_marker(lowered):
        return False
    if looks_like_web0_or_x402_context(lowered):
        return False
    recognized = price_assets_named(lowered)
    if not recognized:
        return False
    alias_words: set[str] = set()
    for alias in recognized:
        alias_words.update(alias.split())
    tokens = re.findall(r"[a-z]+", lowered)
    leftover = [
        token
        for token in tokens
        if len(token) >= 3
        and token not in alias_words
        and token not in _PRICE_GENERIC_TOKENS
        and token not in _PRICE_LEFTOVER_FILLER_TOKENS
    ]
    return bool(leftover)


def _recent_price_subject(source_context: dict[str, Any] | None) -> str:
    history = [dict(item) for item in list((source_context or {}).get("conversation_history") or []) if isinstance(item, dict)]
    for message in reversed(history[-10:]):
        content = " ".join(str(message.get("content") or "").split()).strip().lower()
        if not content:
            continue
        subject = _extract_price_asset_alias(content)
        if not subject:
            continue
        if _has_price_marker(content) or "source:" in content or "$" in content:
            return subject
    return ""


def unresolved_price_lookup_response(*, query: str, notes: list[dict[str, Any]], mode: str) -> str:
    if mode != "fresh_lookup":
        return ""
    lowered = " ".join(str(query or "").strip().lower().split())
    if not _has_price_marker(lowered):
        return ""
    if first_live_quote(notes) is not None:
        return ""
    if notes_include_grounded_price_signal(notes):
        return ""
    if _extract_price_asset_alias(lowered):
        return ""
    # Reaching here means only "a price word appeared and the alias table resolved nothing". That
    # is TWO different turns, and this message is right for exactly one of them: "you asked for a
    # quote and I cannot resolve the ticker" (say so) versus "this was never an asset quote"
    # (stay out of it entirely and let the reasoning lane answer). A failed alias lookup does not
    # tell them apart, so the gate is now positive evidence that the turn is asset-shaped at all.
    # The subject comes from the same predicate rather than from `extract_price_lookup_subject`,
    # which strips price scaffolding by regex and cannot produce a grammatical noun phrase for
    # anything but a bare ticker -- it is what turned a flight question into the user-visible
    # "I couldn't map `is a flight from Vilnius to Rome next weekend`". This quotes the user's own
    # word, in their own casing, or says nothing.
    subject = bare_unresolved_asset_subject(query)
    if not subject:
        return ""
    return (
        f"I couldn't map `{subject}` to a known traded asset or commodity quote. "
        "If you mean a stock, token, ETF, or product, give me the exact ticker or full name."
    )


def notes_include_grounded_price_signal(notes: list[dict[str, Any]]) -> bool:
    finance_domains = (
        "finance.yahoo.com",
        "coingecko.com",
        "marketwatch.com",
        "bloomberg.com",
        "tradingview.com",
        "investing.com",
    )
    for note in list(notes or []):
        if not isinstance(note, dict):
            continue
        text = " ".join(
            str(part).strip()
            for part in (
                note.get("summary"),
                note.get("result_title"),
                note.get("origin_domain"),
            )
            if str(part).strip()
        )
        lowered = text.lower()
        domain = str(note.get("origin_domain") or "").strip().lower()
        if any(finance_domain in domain for finance_domain in finance_domains) and re.search(r"\d", text):
            return True
        if re.search(r"[$€£¥]\s?\d", text):
            return True
        if re.search(r"\b\d[\d,]*(?:\.\d+)?\s*(?:usd|eur|gbp|jpy|btc|eth)\b", lowered):
            return True
        if any(marker in lowered for marker in ("price", "quote", "market cap", "session change", "24h change")) and re.search(r"\d", text):
            return True
    return False


def extract_price_lookup_subject(query: str) -> str:
    """DISPLAY ONLY -- never a claim predicate, never the text of a user-facing message.

    It deletes price scaffolding by regex with no grammar behind it, so it is correct for a bare
    ticker ("what is the price of BTC now?" -> "BTC") and produces word salad for everything else
    ("How much is a flight from Vilnius to Rome next weekend?" -> "is a flight from Vilnius to
    Rome next weekend"). Both of its former callers are gone: it stood in as evidence of a market
    request in `looks_like_grounded_price_lookup`, and its output was quoted back to the user by
    `unresolved_price_lookup_response`. Those now use `bare_unresolved_asset_subject`, which
    answers a grammatical question and returns the user's own word. Nothing may route on the
    non-emptiness of this function's return value.
    """

    clean = re.sub(r"[\?\!\.,]+", " ", str(query or "")).strip()
    clean = re.sub(
        r"\b(?:what\s+is|what's|whats|tell\s+me|show\s+me|price|cost|worth|value|quote|rate|market\s+cap|"
        r"trading\s+at|how\s+much|current|latest|today|now|right\s+now|for|of|the)\b",
        " ",
        clean,
        flags=re.IGNORECASE,
    )
    return " ".join(clean.split()).strip()
