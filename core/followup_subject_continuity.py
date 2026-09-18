"""What a correction or follow-up utterance is actually ABOUT — read before any entity recognizer.

The defect this module exists to close
--------------------------------------
Live, 2026-08-11:

    U: lets build a castle. where do we start?
    A: Sure! Let's start by choosing a location. Where would you like to build your castle ...
    U: no i mean real castle
    A: Reallink is $0.0739 USD ...

Traced to its first wrong production decision in
`core.agent_runtime.fast_live_info_price.recover_price_lookup_query`. That function has a
"short subject follow-up" branch for the shape "no i mean SOL" — a correction whose whole content
is the asset the user meant instead. Its guard was:

    bool(explicit_subject) and bool(_PRICE_FOLLOWUP_PREFIX_RE.match(lowered))

`explicit_subject` comes from `price_assets_named`, which resolves bare tickers against
CoinGecko's rank-ordered index. `REAL` is a listed symbol (`reallink`, inside the top 250), so the
word "real" in "no i mean real castle" resolved to an asset, the correction prefix "no i mean"
matched, and the turn was rewritten to the query `real price now`. Nothing in that branch ever
looked at "castle", and nothing ever asked whether the conversation had anything to do with the
market.

Two independent things were missing, and both are the same missing idea:

  1. `price_request_leaves_unresolved_content` — the guard whose entire job is "this message names
     something my alias table cannot account for, so decline" — early-returns False when the
     message has no price marker. The follow-up branch is precisely the branch that fires WITHOUT
     a price marker, so the leftover guard was inert exactly where it was needed. "castle" was
     unaccounted-for content and nothing looked at it.
  2. A branch named "follow-up" never read the turn it was following up on. It fires identically
     with `source_context=None`. A correction that refines a castle-building conversation and a
     correction that refines a Solana price conversation were indistinguishable to it.

The rule
--------
A correction or follow-up utterance BINDS TO THE PRIOR SUBJECT. It does not open a new independent
request, and naming something is not the same as asking about it. So a correction may re-open the
market lane only when BOTH hold:

  * its residue — what is left once the correction opening is stripped — is nothing but the
    asset(s) already recognized plus ordinary market/correction scaffolding. If any content word
    survives, THAT word is the subject the user is correcting toward, not the ticker.
  * the market is already on the table: either the exchange being corrected was a market exchange,
    or the correction itself carries market semantics (`core.market_intent`).

This is the same invariant `tests/test_an_asset_name_alone_is_not_a_market_request.py` pins for
ordinary turns (SENTINEL G4: an asset NAME alone is never evidence of a market request), applied to
the one lane that had its own private answer to the question. It is stated here once, positively,
rather than as a blacklist of the non-market sentences to reject — that list is unbounded and would
fail on the first phrasing nobody thought of. "no i mean real *keep*", "no i mean real *bridge*",
"no i mean real *bakery*" all fall out for free, because none of them is bare.

Direction of failure
--------------------
Every predicate here is SUBTRACTIVE with respect to the market lane: it can withhold the fast,
deterministic price answer, never produce one. A false negative costs one tool-enabled turn, where
a real tool and the model that drives it resolve what the alias table could not — the asymmetry
`price_request_leaves_unresolved_content` already documents and deliberately chose. A false
positive is a fabricated subject change, which is the incident above.

Boundary, stated rather than implied
------------------------------------
A correction OPENING is matched at the start of the message only. A mid-sentence contrast
("physical castle not code", "real-world castle") is a different shape and is NOT claimed here —
those utterances name their own subject, so they need no inheritance, and reading contrast anywhere
in a sentence is how "not for this" once resurrected a stale subject
(`tests/test_cross_turn_subject_contamination.py`).
"""

from __future__ import annotations

import re

from core.market_intent import market_semantics_present

# A refusal or reframe that opens a correction. Bounded positive vocabulary, anchored at the start
# of the message — see the module docstring on why mid-sentence contrast is deliberately excluded.
_REFUSAL_OPENING = r"(?:no|nah|nope|not|nvm|never\s*mind|nevermind|sorry)"
_REFRAME_OPENING = (
    r"(?:i\s*mean(?:t)?|i\s+meant|meant|i\s+said|i'?m\s+asking|im\s+asking|actually|"
    r"what\s+about|how\s+about|btw|by\s+the\s+way|to\s+be\s+clear|to\s+clarify|instead|rather)"
)
_SEP = r"[\s,.:;!-]*"
# Either half alone opens a correction ("no ..." / "i mean ..."), and the two compose
# ("no i mean ..."). Written as one anchored alternation rather than two passes so the LONGEST
# opening is what gets stripped: "no i mean sol" must leave "sol", not "i mean sol".
CORRECTION_OPENING_RE = re.compile(
    rf"^(?:{_REFUSAL_OPENING}{_SEP}{_REFRAME_OPENING}|{_REFRAME_OPENING}|{_REFUSAL_OPENING}\b){_SEP}",
    re.IGNORECASE,
)

# Trailing address/filler that carries no subject: "nah real castle bro".
_TRAILING_FILLER_RE = re.compile(
    r"(?:[\s,]+(?:bro|dude|man|mate|pls|please|thanks|thx|ok|okay|yeah|yep|though|k))+[\s.!?]*$",
    re.IGNORECASE,
)

# Coordinators that can open a bare follow-up without naming anything themselves ("or ethereum?").
_COORDINATOR_OPENING_RE = re.compile(r"^(?:or|and|but|then|also)\b[\s,]*", re.IGNORECASE)
# The reframe half of a correction, when it TRAILS the subject ("solana instead", "ethereum rather").
_TRAILING_REFRAME_RE = re.compile(rf"(?:{_SEP}{_REFRAME_OPENING})+[\s.!?]*$", re.IGNORECASE)


def strip_correction_language(text: str) -> str:
    """`text` with its correction openings, trailing reframes and opening coordinators removed.

    The ONE place the correction vocabulary is applied to a whole span, so the continuation
    module's rebind rule ("the residue must be nothing but the subject") reads corrections the
    same way this module's own gate does: "no i meant silver" -> "silver", "solana instead" ->
    "solana", "or ethereum?" -> "ethereum?". Nothing here is a subject word, so stripping it can
    only ever REVEAL the subject; a word this vocabulary does not know is left in place and counts
    as content, which is the safe direction.
    """
    value = str(text or "").strip()
    previous = None
    while value and value != previous:
        previous = value
        value = CORRECTION_OPENING_RE.sub("", value, count=1).strip()
        value = _COORDINATOR_OPENING_RE.sub("", value, count=1).strip()
        value = _TRAILING_REFRAME_RE.sub("", value).strip()
        value = _TRAILING_FILLER_RE.sub("", value).strip()
    return value


# Ordinary market-question and correction scaffolding: words that can appear in a bare asset
# correction without making it about something else. Kept LOCAL to this module rather than imported
# from the price lane, so this module never depends on the lane it gates (and so a future edit to
# the price lane's own leftover list cannot silently widen what counts as "bare" here).
#
# Deliberately NOT exhaustive, and it does not need to be: an unlisted word only makes a correction
# read as non-bare, which withholds the fast path. That is the safe direction (see the module
# docstring), so being incomplete here can never produce a wrong answer.
_SUBJECT_NEUTRAL_TOKENS = frozenset({
    # price-question vocabulary
    "price", "prices", "cost", "costs", "worth", "value", "rate", "rates", "market", "cap",
    "trading", "trade", "quote", "quotes", "chart", "charts", "usd", "usdt", "dollar", "dollars",
    "how", "much", "many", "what", "whats", "hows", "the", "and", "for", "its", "that", "this",
    "these", "those", "now", "today", "current", "currently", "latest", "recent", "right",
    # asset-class words that name no particular asset
    "coin", "coins", "token", "tokens", "crypto", "cryptos", "ticker", "symbol", "asset",
    # correction / conversational scaffolding
    "tell", "give", "show", "about", "please", "pls", "you", "know", "mean", "meant", "btw",
    "way", "just", "still", "again", "actually", "instead", "rather", "sorry", "bro", "dude",
    "man", "mate", "okay", "yeah", "yes", "not", "nope", "nah",
})

_WORD_RE = re.compile(r"[a-z0-9]+")
_MIN_CONTENT_WORD = 3

# Verbs that head a REQUEST. Their presence in a correction's residue means the user is not
# refining the subject already on the table -- they are issuing a new instruction, and the brief's
# own carve-out applies: a correction binds to the previous subject "unless it introduces a
# complete new independent request".
#
# This boundary is not theoretical. "Actually, help me write the deployment script instead." and
# "Instead, outline the pricing page for me." are corrections by every structural test, and
# `_derive_continuity_state` must let both REPLACE the current goal rather than continue it --
# `tests/gauntlet/test_context_continuity.py` and `tests/pa_beta_gate/
# test_context_continuity_matrix.py` pin that, and a first draft of this module broke all three by
# treating any correction as a follow-up continuation.
#
# Bounded positive vocabulary again, and subtractive again: a verb missing from this list only
# makes a correction look bare, which at worst carries a subject forward that the model can see in
# the transcript anyway.
_REQUEST_VERBS = frozenset({
    "add", "analyse", "analyze", "build", "change", "check", "compare", "compile", "create",
    "debug", "delete", "deploy", "describe", "design", "draft", "explain", "find", "fix",
    "generate", "give", "help", "implement", "install", "list", "make", "open", "outline",
    "plan", "print", "push", "read", "refactor", "remove", "rename", "render", "replace",
    "research", "review", "rewrite", "run", "search", "send", "show", "sketch", "summarise",
    "summarize", "test", "translate", "update", "upload", "write",
})

# A refinement is terse. Past this, the turn is carrying its own content rather than pointing at
# the previous subject -- the same bound `core.task_router.looks_like_conversational_correction`
# already applies for the same reason.
_MAX_REFINEMENT_WORDS = 10

# A rendered quote in a prior assistant message: "$147.20", "Source: CoinGecko". Market semantics
# alone can miss these — a bare rendered quote says nothing about "price" in words.
_QUOTE_RENDERING_RE = re.compile(
    r"\$\s?\d|source:\s*(?:coingecko|coinmarketcap|finance\.yahoo|yahoo|marketwatch|binance)",
    re.IGNORECASE,
)

_HISTORY_LOOKBACK = 10


# How many stacked openings to strip. Corrections chain in ordinary speech -- "actually i mean
# ...", "no, sorry, i meant ...", "nvm actually what about ..." -- and a single pass leaves the
# second opening sitting in the residue, where "mean" would then be read as the subject. Bounded
# rather than unbounded so a pathological input cannot spin, and bounded LOW because three stacked
# openings is already beyond anything observed.
_MAX_STACKED_OPENINGS = 3


def _normalized(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def _split_opening(text: str) -> tuple[str, str]:
    """(opening, rest) for a normalized message, stripping stacked openings left to right."""
    rest = text
    consumed = 0
    for _pass in range(_MAX_STACKED_OPENINGS):
        match = CORRECTION_OPENING_RE.match(rest)
        if match is None or match.end() == 0:
            break
        consumed += match.end()
        rest = rest[match.end() :]
    return text[:consumed].strip(), rest


def correction_opening(text: str) -> str:
    """The correction/refusal opening this message starts with, or "" when it has none."""
    return _split_opening(_normalized(text))[0]


def is_correction_followup(text: str) -> bool:
    """Whether the message opens as a correction or refinement of what came before."""
    return bool(CORRECTION_OPENING_RE.match(_normalized(text)))


def correction_residue(text: str) -> str:
    """What the correction is steering TOWARD: the message minus its opening and trailing filler.

    "no i mean real castle" -> "real castle". "nah real castle bro" -> "real castle".
    """
    residue = _split_opening(_normalized(text))[1]
    return _TRAILING_FILLER_RE.sub("", residue).strip()


def correction_is_a_bare_refinement(text: str) -> bool:
    """True when the correction REFINES the subject already on the table rather than replacing it.

    A bare refinement is a terse noun phrase — "no i mean real castle", "I mean construction",
    "no real house". A correction that heads a new instruction — "Actually, help me write the
    deployment script instead." — is a complete independent request, and binds to nothing: the
    subject it names IS the subject from now on.
    """
    if not is_correction_followup(text):
        return False
    residue = correction_residue(text)
    if not residue:
        return False
    words = _WORD_RE.findall(residue)
    if not words or len(words) > _MAX_REFINEMENT_WORDS:
        return False
    return not any(word in _REQUEST_VERBS for word in words)


def correction_names_only(text: str, *, names: list[str] | tuple[str, ...]) -> bool:
    """True when the correction's residue is `names` and market/correction scaffolding, nothing else.

    This is the "is it BARE?" question. `names` are the entity aliases some recognizer already
    resolved out of the message; anything else with a content word in it means the user is
    correcting toward that something else, and the recognized entity is incidental to it — which is
    exactly the shape of "no i mean real castle", where `real` resolves to a listed ticker and
    `castle` is the entire point of the sentence.

    An empty `names` is never bare: there is nothing for the residue to consist of.
    """
    residue = correction_residue(text)
    if not residue:
        return False
    name_words: set[str] = set()
    for name in names or ():
        name_words.update(_WORD_RE.findall(str(name or "").lower()))
    if not name_words:
        return False
    for token in _WORD_RE.findall(residue):
        if token in name_words or token in _SUBJECT_NEUTRAL_TOKENS:
            continue
        if len(token) < _MIN_CONTENT_WORD:
            continue
        return False
    return True


def prior_exchange_was_market(source_context: dict | None) -> bool:
    """Whether the conversation this turn corrects was already about the market.

    Reads the same `conversation_history` the price lane's own subject harvest reads, and applies
    `core.market_intent` — the one authority for "is this about the market?" — to each message,
    plus a rendered-quote signal for an assistant turn that printed a price without saying the word.
    """
    history = [
        item
        for item in list((source_context or {}).get("conversation_history") or [])
        if isinstance(item, dict)
    ]
    for message in reversed(history[-_HISTORY_LOOKBACK:]):
        content = " ".join(str(message.get("content") or "").split())
        if not content:
            continue
        if market_semantics_present(content) or _QUOTE_RENDERING_RE.search(content):
            return True
    return False


def correction_reopens_the_market_lane(
    text: str,
    *,
    names: list[str] | tuple[str, ...],
    source_context: dict | None,
) -> bool:
    """Whether a correction may be answered as a market lookup for `names`.

    Both halves of the rule in the module docstring, and nothing else: the correction has to be
    BARE (its residue is the asset and scaffolding only), and the market has to already be on the
    table (the exchange being corrected was a market exchange, or this correction itself says
    something about the market).
    """
    if not is_correction_followup(text):
        return False
    if not correction_names_only(text, names=names):
        return False
    # Market semantics are read off the RESIDUE, not the raw message, because the correction
    # opening is not part of what is being asked -- and `core.market_intent` treats a leading "no"
    # as a negation cue governing the next 32 characters, so "no i mean the sol price" reads as
    # price-under-negation on the raw text and as an ordinary price question on the residue. The
    # second reading is the correct one: the "no" rejects the previous ANSWER, not the word after
    # it. Nothing in market_intent changes -- the correction opening simply stops being handed to
    # it as if it were part of the request.
    return market_semantics_present(correction_residue(text)) or prior_exchange_was_market(
        source_context
    )
