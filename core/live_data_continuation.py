"""A bare continuation of a live-data request is still that request.

The defect this module exists to close
--------------------------------------
Measured live on c6eed761 (2026-08-14), Local Only and Auto lanes alike. Turn 1 is answered
correctly by the live-data lane::

    U: what is the current air temperature in Tromso right now?
    A: Tromso: Light drizzle, 10 C ... Source: [wttr.in](https://wttr.in/tromso)
       route=deterministic:live_data_typed_plan  web_calls=1  receipts=1

Turn 2 carries no independent request of its own, and every shape of it fell through to the plain
model lane with ``web_calls=0`` and ``receipts=0``::

    "and?"            -> "I can't access live data or the internet to check the current
                          temperature."                      <- a capability LIE; it just did
    "well?"           -> "The current temperature in Tromso is 9 C with light drizzle."
                                                              <- FABRICATED; the fetch said 10 C
    "what about now"  -> "10 C. According to the weather report from wttr.in, as of 08:30 AM."
                                                              <- FABRICATED PROVENANCE; wttr.in was
                                                                 never contacted on that turn

``core.execution_requirements.requirements_for`` reads the CURRENT turn's text alone, and
``_live_data_classification`` matches narrow price/weather recognizers against it. "and?" carries no
such term, so the live-data requirement was never derived and the model was left holding a question
it had no evidence for. That module's own docstring already records the identical failure from the
other direction -- a weather/market request the generic classifier called DIRECT, after which "the
model HALLUCINATED fake prices while claiming real-time data from major exchanges". This is the same
hole reached through continuation rather than through phrasing.

The rule
--------
An utterance that carries NO independent request of its own, arriving on a conversation whose last
exchange was live data, IS that request again. It inherits the prior request text -- entity, intent
and temporal frame together -- and re-enters the live-data lane, which fetches again and produces
real receipts.

Deliberately conservative in one direction: inheritance requires the residue to be EMPTY once
continuation scaffolding and temporal markers are stripped. If any content word survives, the user
introduced something ("what about Berlin", "and the humidity?") and this module declines rather than
guessing which part of the prior request the new word replaces. Those turns keep exactly the
behaviour they have today; narrowing the hole is not licence to invent a wider one.

This is `core.followup_subject_continuity`'s rule -- a follow-up BINDS TO THE PRIOR SUBJECT, and its
residue decides whether it is bare -- applied to the live-data lane instead of the market lane. It
is stated positively, as "what counts as carrying no request", rather than as a list of the
follow-up words to catch: that list is unbounded and would fail on the first phrasing nobody
thought of.

What the "conservative in one direction" paragraph above cost, and what replaced it
--------------------------------------------------------------------------------
Declining the moment a content word survived was not a safe default; it was the release defect the
gauntlet already had three strict xfails for (`tests/gauntlet/test_release_conversation_gauntlet.py`,
G1). Measured at 3cc69da7 through the real `/api/chat` seam::

    U: Get weather for Kaunas.   -> weather_lookup(Kaunas), 18 C, real receipt
    U: What about Tallinn?       -> NO live-data plan. Provider never asked for tallinn.
    U: Which one is warmer?      -> NO plan, no entities, answered from nothing.

The cause was not the strictness of the gate. It was that the state a follow-up resolved against
was the prior TEXT -- `prior_live_data_request` returns a string -- so the ONLY inheritance the
module could express was "re-run that whole string". With no slot to put "Tallinn" into, declining
was the only correct move available to it.

So the state changed rather than the threshold. A fulfilled live-data turn now records a typed
OBLIGATION (`core.runtime_continuity.remember_live_data_obligation`): which operation ran, which
entities filled its slot, and the user's own phrasing of the request. A follow-up resolves against
that, which makes exactly three outcomes expressible -- and each one is a rebinding of the SAME
obligation, never a new guess:

    RE-ASK     residue is empty            -> the obligation again, most recent binding
                                              ("and?", "well?", "what about now")
    REBIND     residue fills the slot      -> the obligation with the new entity substituted
                                              ("What about Tallinn?", "and ethereum?")
    AGGREGATE  residue refers to the set   -> the obligation over every accumulated entity
                                              ("which one is warmer?", "both")

Why this is not a wider hole than the old rule
----------------------------------------------
REBIND does not guess which part of the prior request the new word replaces. It substitutes the
residue for the PREVIOUSLY GROUNDED ENTITY in the user's own sentence -- "Get weather for Kaunas."
becomes "Get weather for Tallinn." -- and only when that entity is literally present to replace. The
result is a request in the user's own wording, which then faces every recognizer it would have faced
had they typed it: `_live_data_classification`, `_extract_weather_locations_with_confidence`, and
`_is_plausible_weather_location` below the planner. Nothing here decides that a word is a city; it
decides only that the turn is asking the previous question again about a different subject, and lets
the existing authorities rule on the subject. That is why it needs no city list, and why it carries
over to the market lane -- "and ethereum?" after a bitcoin quote -- with no new code.

AGGREGATE is gated on closed-class grammar (a set reference: "both", "them", "which one"), never on
what is being compared. "Warmer" is not in this module and must not be: a comparative vocabulary is
open-ended domain trivia, and the first phrasing nobody listed would fail exactly the way the
original follow-up list would have.

Inheritance still BREAKS on a turn that states a request of its own, and the break is proved from
two independent directions: the residue rule (a turn saying something new resolves to no slot and no
set), and the walk-back in `prior_live_data_request` (an unrelated request between the obligation
and the follow-up ends the search). The obligation record's `absorbed` list is what keeps the second
of those honest across a rebind -- see the note on it in `storage/migrations.py`.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: How far back a continuation may bind, counted in USER TURNS. A bare utterance refers to what was
#: recently said, not to an arbitrarily old subject; beyond this the referent is not recoverable and
#: guessing is worse.
#:
#: Counted in user turns rather than in messages, which it was until a long ordinary conversation
#: exposed the difference. History interleaves assistant replies, so six MESSAGES is three
#: exchanges: after "Vilnius" / "and Riga?" / "no i meant Warsaw", the founding request had already
#: scrolled out of the window and "which one is warmer" found no obligation on the table -- while the
#: obligation record itself still held all three cities. A window that expires faster than the
#: obligation it guards is not a safety bound, it is a truncation bug.
#:
#: Widening is safe in the one direction that matters: the walk-back still STOPS on the first
#: unrelated real request it meets, so a longer reach can only step over turns the obligation
#: already absorbed, never over a topic change.
_HISTORY_LOOKBACK = 6

#: Scaffolding a continuation is MADE of. None of it is content: stripping it is what reveals
#: whether the user actually introduced anything. Temporal markers belong here because "and now?"
#: re-asks the same question about the same subject -- the time frame is already part of the
#: inherited request, which was itself a request for the current value.
_SCAFFOLDING = frozenset(
    [
        "and", "so", "well", "then", "ok", "okay", "k", "yeah", "yep", "yes", "no", "but",
        "what", "about", "how", "hows", "is", "it", "that", "this", "the", "a", "an",
        # Temporal markers, including the abbreviations people actually type for them: "rn" is
        # "right now" and "atm" is "at the moment". They belong to this category rather than being
        # separate cases -- the whole category is here because a continuation re-asks the same
        # question about the same subject, and the time frame is already part of the inherited
        # request, which was itself a request for the current value.
        "now", "today", "currently", "current", "right", "still", "yet", "again", "there",
        "rn", "atm",
        "please", "pls", "plz", "thanks", "thx", "ty", "cheers", "mate",
        "tell", "me", "you", "u", "give", "show", "say", "go", "come", "on", "at",
        "any", "anything", "more", "else", "update", "updates", "latest", "news",
        "temp", "temperature", "weather", "price", "value", "reading", "figure", "number",
        "whats", "hows", "wheres", "wat", "wot", "huh", "eh", "hm", "hmm",
    ]
)

#: The CONTENT-BEARING subset of `_SCAFFOLDING`: nouns that name a live-data DOMAIN (weather,
#: markets, news) rather than grammar. Stripping them is only legitimate for an obligation of
#: that noun's own domain -- "and the price?" strips "price" for a GOLD obligation and reads as
#: a bare nudge re-asking gold, when the noun in fact makes it a new, underspecified PRICE
#: request (measured CT-201: the gold price was served for "and the weather there?"). A residue
#: emptied only by such a noun the obligation does not hold is not a nudge;
#: `continuation_inheritance` declines it and the model lane asks. Held means either the noun
#: appears in the obligation's own recorded vocabulary (request text, slots, absorbed turns,
#: founding prior -- "weather" for a weather thread asking about "the weather") or the
#: obligation's operation is the noun's owning operation ("temp"/"temperature" for
#: weather_lookup, whose request text may name none of them). "update"/"updates"/"latest" stay
#: plain scaffolding: they refresh the SAME subject ("any update?"), they never select a domain.
_SCAFFOLDING_DOMAIN_NOUNS = frozenset(
    {
        "news",
        "temp", "temperature", "weather", "price", "value", "reading", "figure", "number",
    }
)
_DOMAIN_NOUN_OPERATIONS = {
    "weather": "weather_lookup",
    "temp": "weather_lookup",
    "temperature": "weather_lookup",
    "price": "market_quote",
    "value": "market_quote",
    "reading": "market_quote",
    "figure": "market_quote",
    "number": "market_quote",
    "news": "",
}
#: Nouns of one domain group co-occur: a thread that already says "weather" is a weather
#: thread, so its bare "temp?" re-asks it even though the request text never wrote "temp".
#: When no recorded obligation names an operation, this group is the domain evidence.
_DOMAIN_NOUN_GROUPS = {
    "weather_lookup": frozenset({"weather", "temp", "temperature"}),
    "market_quote": frozenset({"price", "value", "reading", "figure", "number"}),
    "": frozenset({"news"}),
}

#: Mid-sentence-CAPITALISED closed-class connectives. In natural typing these words are
#: lowercase anywhere but sentence start; Title-case input capitalises them everywhere, and a
#: span that contains one is a phrase about its subject ("Write About Winter"), not a name.
_TITLE_CASED_PHRASE_MARKERS = frozenset(
    {"About", "And", "Or", "But", "Vs", "Versus", "With", "Without", "Not", "Into", "Onto"}
)

#: An interrogative about the conversation itself: inverted auxiliary + demonstrative
#: ("was that what i asked about?"). Grammar, not vocabulary -- it names no subject.
_META_QUESTION_RE = re.compile(
    r"\b(?:was|is|were|did|does|do)\s+(?:that|it|this)\s+(?:what|the)\b", re.IGNORECASE
)

_TOKEN_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
_DIGIT_RE = re.compile(r"\d")

#: An assistant turn that rendered a live value carries the source form the live-data contracts
#: emit (`core.weather_result_contract`, `core.live_quote_contract`). Used as corroboration that the
#: prior exchange really was live data even when the user's own wording has scrolled out of reach.
_LIVE_RENDERING_RE = re.compile(r"Source:\s*\[[^\]]+\]\([^)]+\)", re.IGNORECASE)


#: A reference to the SET the obligation has accumulated, rather than to any one member of it.
#: Closed-class grammar only -- pronouns, quantifiers and the selector "which". Deliberately NOT a
#: comparative vocabulary: "warmer"/"cheaper"/"faster" is an open-ended list, and what makes
#: "which one is warmer?" an aggregate is "which one", not "warmer".
#:
#: A `compared?\s+(?:to|with)` arm was written here and then removed: no case could reach it, because
#: "compare X to Y" names its own subjects and is a complete request rather than a follow-up, while
#: "compare them" already matches on `them`. Kept out rather than left as an arm no test defends --
#: the same call `_is_plausible_weather_location` made when it dropped its own unreachable branch.
_SET_REFERENCE_RE = re.compile(
    r"\b(?:both|all|either|them|these|those|they|each)\b"
    r"|\bwhich\s+(?:one|ones|of|is|are|was|were)\b",
    re.IGNORECASE,
)

#: The longest residue that can still be a bare slot filler. A place or asset name is a name --
#: "new york city" is three words. Past this the turn is saying something, not naming something.
_SLOT_FILLER_MAX_TOKENS = 3

#: Below this length a token is too short for typo recovery to mean anything: at two or three
#: characters nearly every word is within one edit of nearly every other, so a recovery bound that
#: short would match noise. Grounded subjects are names -- cities, assets -- and names are longer.
_RECOVERY_MIN_TOKEN = 4

#: Words a clarification uses to talk about THE CONVERSATION rather than about the world: first
#: person, the verbs of asking and answering, ordinals and small counts, and the relative-time
#: words that place an earlier turn. Closed-class and domain-free, like `_SCAFFOLDING`: none of
#: them can be a subject. They exist so `_clarification_recovers_the_obligation` can demand that
#: EVERY content word of a clarification is accounted for -- a slot, scaffolding, set grammar or
#: dialogue-meta -- and refuse the turn otherwise. Measured 2026-09-06 through the real native
#: window: "give me comparision review Vw passat vs vw golf, like how long they been maming it,
#: total sale, most popuplar regions where sold, engines and so on" rebound a gold market_quote
#: obligation, because "they" is set grammar, "golf" is one edit from the slot "Gold", and the only
#: new-subject check looked at capitalised words. Twelve of its words named things the obligation
#: never held; this class is what lets that be seen without naming any of them.
#: The words an AGGREGATE follow-up may add beyond set grammar: the vocabulary of comparison
#: itself. Closed-class -- verbs and nouns of comparing and the irregular degree words -- while
#: regular comparatives ("warmer", "cheapest") are recognised by their SHAPE in
#: `_aggregate_says_nothing_but_a_comparison`, so no domain adjective is ever listed here.
_COMPARISON_WORDS = frozenset(
    [
        "compare", "compared", "comparing", "comparison", "contrast", "rank", "ranked", "ranking",
        "order", "difference", "differences", "differ", "differs", "prefer", "preferred",
        "more", "less", "most", "least", "better", "worse", "best", "worst", "same", "different",
        "similar", "between", "other", "others",
    ]
)

_DIALOGUE_META = frozenset(
    [
        "i", "we", "my", "our", "u",
        "ask", "asked", "asking", "answer", "answered", "answers", "question", "questions",
        "said", "say", "told", "mean", "meant", "meaning", "wrote", "typed",
        "was", "were", "for", "of", "in", "with", "to",
        "follow", "followup", "up", "last", "previous", "prior", "earlier", "before", "ago",
        "yesterday", "recently", "first", "second", "third", "one", "two", "three", "four",
        "time", "times", "twice", "once", "same", "just", "only", "also", "too", "both", "correct",
    ]
)


def _is_typo_of(token: str, target: str) -> bool:
    """Whether `token` is the user's own mistyping of `target`, or `target` itself.

    Two edit shapes are accepted, mirroring the misspelling tolerance
    `core.live_data_plan._is_non_entity_filler` already applies to filler words: a single
    substitution/insertion/deletion, and an equal-length transposition ("kauans" for "kaunas" --
    two substitutions under plain edit distance, one keystroke swap in fact). Anything further is
    a different word, not a typo: "bouma" stays nothing like "tallinn" no matter how the budget is
    counted. Comparison happens on casefolded text; the result is a recovery decision, not a
    rendering.
    """

    source, goal = token.casefold(), target.casefold()
    if source == goal:
        return True
    if len(source) < _RECOVERY_MIN_TOKEN or len(goal) < _RECOVERY_MIN_TOKEN:
        return False
    if len(source) == len(goal) and sorted(source) == sorted(goal):
        return True
    if abs(len(source) - len(goal)) > 1:
        return False
    if len(source) == len(goal):
        return sum(1 for a, b in zip(source, goal, strict=False) if a != b) == 1
    shorter, longer = (source, goal) if len(source) < len(goal) else (goal, source)
    index_short = index_long = 0
    skipped = False
    while index_short < len(shorter) and index_long < len(longer):
        if shorter[index_short] == longer[index_long]:
            index_short += 1
            index_long += 1
            continue
        if skipped:
            return False
        skipped = True
        index_long += 1
    return True

#: Closed-class grammar that means the turn is asking ABOUT its subjects rather than naming one.
#: Interrogatives, auxiliaries and explicit comparison links only -- every token here is a function
#: word, so the set is bounded by the language and not by any domain. A residue carrying any of them
#: is never a subject: "which one warmer" is a question, "Bergen" is a name.
#:
#: Load-bearing on TITLE-CASED input specifically, which is what a phone keyboard produces. The
#: weather branch's proper-noun signal is capitalisation, so "Which One Is Warmer?" passes it and
#: would rebind to a place called "Which One Warmer" without this. Measured: removing this line left
#: the whole regression family green until
#: `test_a_title_cased_set_reference_still_aggregates` was added, which is why that test exists.
#:
#: Coordinators and correction words are DELIBERATELY absent. "or ethereum?" and "Bergen instead?"
#: are subject substitutions, and listing them here would refuse the very turns this repair is for.
_NON_SUBJECT_GRAMMAR_RE = re.compile(
    r"\b(?:which|who|whom|whose|why|when|where|whether"
    r"|be|been|being|am|do|does|did|done|can|could|will|would|shall|should|may|might|must"
    r"|has|have|had|than|versus|vs)\b",
    re.IGNORECASE,
)


def _continuation_residue(text: Any) -> list[str] | None:
    """The content tokens left once continuation scaffolding is stripped, or None.

    None means "not continuation-shaped at all" -- the turn carries digits or is long enough to be
    stating its own request, and no inheritance of any kind may be derived from it. An empty list
    means the turn is a bare nudge. A non-empty list is what the caller offers to the obligation's
    own recognizers as a candidate slot filler or set reference.
    """

    raw = str(text or "").strip()
    if not raw:
        return None
    if _DIGIT_RE.search(raw):
        # "and 3 days?" changes the request and must not silently inherit a single-day one.
        return None
    tokens = [token.casefold() for token in _TOKEN_RE.findall(raw)]
    if not tokens:
        # Punctuation only ("?", "..."): a continuation, and it introduces nothing.
        return []
    if _is_lookup_instruction(tokens):
        # "check it on the internet", "why dont u jsut check it on inernet ffs": an instruction to
        # PERFORM the pending lookup, naming nothing new. That is the obligation again, with the
        # user's own emphasis on it -- measured on the owner's 2026-09-10 23:43 turn (c647b707),
        # where it was adjudicated for entity ambiguity and asked back about "one specific place,
        # person or thing" while the price ask it referred to was still on the table.
        return []
    if len(tokens) > 6:
        # Long enough to be saying something; do not treat it as a follow-up at all.
        return None
    return [token for token in tokens if token not in _SCAFFOLDING]


#: The runtime's OWN lookup capabilities, as a user names them ("check it online", "look it up",
#: "search the web for it"), and the framing a demand for one arrives in. Closed classes: the
#: verbs are the capability vocabulary, the media are where a lookup happens, the framing is how a
#: person presses ("why dont you just ..."). A token outside all three -- a city, an asset, a
#: subject of any kind -- means the turn names something, and the instruction reading declines.
_LOOKUP_VERBS = frozenset(
    {"check", "checking", "look", "looking", "lookup", "search", "searching", "verify", "confirm",
     "google", "find", "fetch", "query", "consult", "browse"}
)
_LOOKUP_MEDIA = frozenset(
    {"internet", "online", "web", "net", "website", "site", "source", "sources", "exchange",
     "market", "markets", "data", "live", "real", "actual", "properly", "yourself", "up", "for",
     "in", "of", "from", "with", "quickly", "first", "instead"}
)
_REQUEST_FRAMING = frozenset(
    {"why", "dont", "don", "t", "cant", "can", "wont", "won", "not", "u", "you", "ya", "just",
     "simply", "already", "then", "could", "couldnt", "would", "should", "ffs", "bro", "man", "dude",
     "lol", "pls", "please", "plz", "cmon", "omg", "seriously", "literally", "even"}
)
_LOOKUP_INSTRUCTION_MAX_TOKENS = 12


def _fold_lookup_token(token: str) -> str:
    """`token` itself when the vocabulary holds it, else the one vocabulary word it mistypes.

    The fold is the runtime's ONE typo budget (`core.typo_fold.near_miss`: a transposition or a
    single inserted/deleted letter -- "jsut", "inernet"), never a substitution: "wind" is one
    substitution from "find", and folding it would have read "and the wind?" as an instruction
    to look something up instead of the attribute follow-up it is.
    """
    if token in _LOOKUP_VERBS or token in _LOOKUP_MEDIA or token in _REQUEST_FRAMING or token in _SCAFFOLDING:
        return token
    from core.typo_fold import near_miss

    hits = [
        word
        for word in (*_LOOKUP_VERBS, *_LOOKUP_MEDIA, *_REQUEST_FRAMING)
        if len(word) >= _RECOVERY_MIN_TOKEN and near_miss(token, word)
    ]
    return hits[0] if len(hits) == 1 else token


def _is_lookup_instruction(tokens: list[str]) -> bool:
    """Whether the tokens are ONLY an instruction to perform the pending lookup, naming nothing."""
    if not tokens or len(tokens) > _LOOKUP_INSTRUCTION_MAX_TOKENS:
        return False
    folded = [_fold_lookup_token(token) for token in tokens]
    if not any(token in _LOOKUP_VERBS for token in folded):
        return False
    return all(
        token in _LOOKUP_VERBS or token in _LOOKUP_MEDIA or token in _REQUEST_FRAMING or token in _SCAFFOLDING
        for token in folded
    )


def utterance_carries_no_independent_request(text: Any) -> bool:
    """Whether `text` introduces nothing of its own -- pure continuation scaffolding.

    Digits count as content: "and 3 days?" changes the request and must not silently inherit a
    single-day one.
    """

    return _continuation_residue(text) == []


def _messages(source_context: Mapping[str, Any] | None) -> list[dict]:
    history = list((source_context or {}).get("conversation_history") or [])
    return [item for item in history if isinstance(item, dict)]


def _asks_about_the_obligation(
    content: str,
    *,
    slots: list[str] | tuple[str, ...] | None,
    operation: str,
) -> bool:
    """Whether `content` is a short turn ABOUT a subject this obligation already holds.

    The discriminator is the obligation's OWN SLOTS, not a vocabulary of recall phrasings -- "what
    did it say", "remind me", "which did you check" is an unbounded list and the first phrasing
    nobody thought of would end the thread again. A turn that names Riga when Riga is already a
    subject here is talking about this thread whatever verb it uses.

    Both bounds matter. It must be continuation-SHAPED (short, no digits), so a real request that
    happens to mention a known city -- "book me a flight to Riga next Tuesday" -- is still an
    interruption and still ends the walk-back. And it must introduce no NEW subject, so a turn that
    names a different city is a rebind rather than an aside.
    """

    known = [str(slot).strip() for slot in (slots or ()) if str(slot).strip()]
    if not known:
        return False
    residue = _continuation_residue(content)
    if not residue:
        # None -> not continuation-shaped; [] -> a bare nudge, already handled by the caller.
        return False
    lowered = {token.casefold() for token in residue}
    if not any(
        all(part.casefold() in lowered for part in slot.split()) for slot in known
    ):
        return False
    return not _slot_filler_for(operation, _residue_span(content, residue))


def prior_live_data_request(
    source_context: Mapping[str, Any] | None,
    *,
    absorbed: Mapping[str, Any] | list[str] | tuple[str, ...] | None = None,
    current_text: Any = None,
    slots: list[str] | tuple[str, ...] | None = None,
    operation: str = "",
) -> str:
    """The most recent user message that was itself a live-data request, or "".

    Read through the same authority the live-data lane uses, so this module cannot drift into a
    second opinion about what a live-data request is.

    `absorbed` is the set of raw user texts a recorded obligation has ALREADY answered. Those are
    stepped over like a bare nudge, because they are part of the obligation rather than an
    interruption of it. Without this the walk-back stops at the rebinding turn itself: "What about
    Tallinn?" names no weather, so on the turn after it the nearest "real request" is an
    unclassifiable one and the obligation looks abandoned. Anything NOT in this set still ends the
    search, which is what keeps an unrelated request between the two from being stepped over.
    """

    from core.execution_requirements import _live_data_classification

    already = {
        " ".join(str(item).split()).strip().casefold()
        for item in (absorbed or ())
        if str(item).strip()
    }
    # The turn being resolved is normally the last message in this history, and it must not answer
    # as its own anchor. That never mattered while only BARE utterances could inherit -- "and?" was
    # skipped by the nudge branch below on the way past itself -- and it is the whole reason a
    # rebinding turn found nothing: "What about Tallinn?" is not bare, so the walk-back stopped on
    # it, classified it as not-live-data, and reported that the thread had been abandoned one
    # message after it started. Dropped ONCE, so a genuinely repeated request still anchors.
    pending_self = " ".join(str(current_text or "").split()).strip().casefold()
    user_messages = [
        message
        for message in _messages(source_context)
        if str(message.get("role") or "").strip().lower() == "user"
    ]
    for message in reversed(user_messages[-_HISTORY_LOOKBACK:]):
        content = " ".join(str(message.get("content") or "").split())
        if not content:
            continue
        if pending_self and content.casefold() == pending_self:
            pending_self = ""
            continue
        if utterance_carries_no_independent_request(content):
            # Another bare nudge; keep walking back to the request they are all binding to.
            # Deliberately ahead of the classification below: `_SCAFFOLDING` holds the domain's own
            # question words, so a lone "weather?" is a nudge that would otherwise classify as a
            # live-data request and answer as its own anchor.
            continue
        # A real live-data request is ALWAYS a valid anchor, including one this obligation was born
        # from -- which is why this sits above the absorbed check rather than below it. Ordered the
        # other way, the founding request ("Get weather for Kaunas.") was in `absorbed`, got stepped
        # over as though it were a continuation of itself, and the walk-back ran off the end.
        if _live_data_classification(content) is not None:
            return content
        if content.casefold() in already:
            # A turn this obligation already answered -- part of it, not an interruption. This is
            # why a rebinding turn does not look like an abandoned thread to the turn after it.
            continue
        if _asks_about_the_obligation(content, slots=slots, operation=operation):
            # A RECALL about a subject the obligation already holds ("what did it say about Riga").
            # It is about this thread, so it does not end it. Measured before this: the recall turn
            # read as an unrelated request, the walk-back stopped on it, and the "and now?" AFTER it
            # fell through to the model holding the previous turn's readings -- which is the
            # fabricated-freshness failure at the top of this file, reopened one turn later.
            continue
        # The NEAREST real request wins, whatever it is. A non-live-data request in between ends the
        # search rather than being stepped over to reach an older live-data turn the user has moved
        # on from. This line is the break-inheritance rule.
        return ""
    return ""


def _session_id(source_context: Mapping[str, Any] | None) -> str:
    context = source_context or {}
    return str(context.get("runtime_session_id") or context.get("session_id") or "").strip()


def _recorded_obligation(source_context: Mapping[str, Any] | None) -> dict[str, Any] | None:
    session_id = _session_id(source_context)
    if not session_id:
        return None
    try:
        from core.runtime_continuity import recall_live_data_obligation

        return recall_live_data_obligation(session_id)
    except Exception:
        return None


#: A list joiner between two subjects in the user's own sentence -- ", ", " and ", " & ". Consumed
#: along with a subject that is being removed, so deleting one leaves no dangling conjunction.
_LIST_JOINER_RE = re.compile(r"\s*(?:,\s*(?:and\s+|&\s+)?|\s+and\s+|\s*&\s*)$", re.IGNORECASE)


def _rebound_request(request_text: str, *, old_slots: list[str], new_slots: list[str]) -> str:
    """`request_text` with EVERY previously grounded entity replaced by `new_slots`.

    Substitution rather than composition, so the result is the USER'S OWN sentence asking about a
    different subject -- which keeps the operation word ("weather", "price") that the classifier
    reads, and keeps this module out of the business of phrasing requests. Returns "" when no old
    entity is literally there to replace: declining beats guessing where the subject sat.

    Every old subject, not just the most recent one, because an AGGREGATE turn writes its own
    multi-subject sentence back as the obligation's request text. Replacing only the last one then
    left the earlier subjects in place, and the next rebind inherited them -- measured in an ordinary
    conversation::

        U: whats the weather in Vilnius / and Riga? / no i meant Warsaw
        U: which one is warmer      -> "whats the weather in Vilnius and Riga and Warsaw"  (correct)
        U: actually Tallinn         -> asked for vilnius, riga AND tallinn                 (wrong)

    The user asked about Tallinn. Three lookups and a comparison table is not a smaller mistake than
    none: it spends real requests and answers a question nobody put.
    """

    haystack = str(request_text or "")
    wanted = [str(slot).strip() for slot in (old_slots or []) if str(slot).strip()]
    if not haystack or not wanted or not new_slots:
        return ""

    spans: list[tuple[int, int]] = []
    for slot in wanted:
        for match in re.finditer(rf"(?<!\w){re.escape(slot)}(?!\w)", haystack, re.IGNORECASE):
            spans.append((match.start(), match.end()))
    if not spans:
        return ""
    spans.sort()
    merged: list[tuple[int, int]] = []
    for start, end in spans:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))

    # The first subject position becomes the new subject list; every later one is deleted along with
    # the joiner that introduced it, so "A and B and C" -> "X" rather than "X and  and ".
    out = haystack[: merged[0][0]] + " and ".join(new_slots)
    cursor = merged[0][1]
    for start, end in merged[1:]:
        between = haystack[cursor:start]
        out += _LIST_JOINER_RE.sub("", between)
        cursor = end
    out += haystack[cursor:]
    return " ".join(out.split()).strip()


def _residue_span(text: Any, residue: list[str]) -> str:
    """The candidate subject as the user wrote it -- original case, correction opening removed.

    Original case matters because a slot filler is a proper noun, and handing "tallinn" to the
    recognizers instead of "Tallinn" throws away the only signal the weather branch has.

    The correction opening is stripped through `core.followup_subject_continuity`'s own
    `CORRECTION_OPENING_RE` rather than a second list here, so the two modules cannot disagree about
    what opens a correction. Found by driving a long ordinary conversation, not by unit test:

        U: whats the weather in Vilnius   -> grounded
        U: and Riga?                      -> rebound, grounded
        U: no i meant Warsaw              -> NOTHING. "i" and "meant" are not scaffolding, so the
                                             residue read as "i meant Warsaw", the proper-noun check
                                             failed on "i", and no rebind happened.
        U: which one is warmer            -> NOTHING, because the failed correction was then an
                                             unrelated request and the walk-back ended on it.

    A correction is the single most common follow-up shape there is, and one that missed poisoned
    every turn after it. Stripping happens ONLY here, on the subject candidate -- `_continuation_residue`
    is deliberately untouched, so what counts as a BARE nudge is exactly what it was.
    """

    if not residue:
        return ""
    from core.followup_subject_continuity import CORRECTION_OPENING_RE

    stripped = CORRECTION_OPENING_RE.sub("", str(text or "").strip(), count=1)
    kept = [
        token for token in _TOKEN_RE.findall(stripped) if token.casefold() not in _SCAFFOLDING
    ]
    return " ".join(kept).strip()


def _slot_filler_for(operation: str, candidate: str, *, original_text: str = "") -> str:
    """`candidate` as a SUBJECT for this obligation, or "".

    Two bounds added 2026-09-06 from the conversation-truth audit, both class-level:

    * CT-203 -- the candidate must be NOTHING BUT the subject. `price_assets_named` answers "does
      this span name an asset?", never "is this turn asking for that asset's price?"; a residue
      that names an asset AND says something else ("gold mined", "poem gold", "history gold") is a
      new request about the asset, not a re-ask of its quote. Every residue token has to be an
      alias token; any other content word declines the rebind and the turn reaches the model.
    * CT-202 -- authorization is read on the text AS TYPED, not on the correction-stripped span.
      `_residue_span` removes the correction opening ("not", "no i meant") so a proper noun can be
      recognised, but that strip also removes the negation that tells "not gold, silver" apart from
      "gold, silver". The alias must be authorized in `original_text`, where the market authority
      sees the cue at the mention's own position and refuses the corrected-away asset.

    Two different authorities, because the two operations genuinely differ in what can be known
    locally:

    * ``market_quote`` has a closed alias table, so `price_assets_named` -- the same authority the
      plan builder resolves against -- answers outright, at any capitalisation. "and ethereum?"
      rebinds; "and the volume?" does not, because `volume` names no asset.

    * ``weather_lookup`` has no such table and cannot get one. `tools/web/web_research` states the
      limitation in its own source: a name cannot be told from a second city "without a full
      gazetteer; this is a named, accepted limitation". `_is_plausible_weather_location` is a
      STRUCTURAL filter (no brackets, colons, bare pronouns) and accepts "warmer" and "pls" --
      measured, not assumed -- so it can reject malformed spans but can never confirm placehood.

      The signal that IS available is orthographic: a place is a proper noun, and the user
      capitalised it. That is a language-level fact, not a geography list, and it is checked against
      the text AS TYPED so it cannot be faked by normalisation upstream.

    NAMED LIMITATION, and it is the safe direction: an all-lowercase city in a bare follow-up
    ("what about tallinn?") does not rebind. It keeps exactly the behaviour it has today -- the turn
    reaches the model -- because the alternative is that "and the humidity?" rebinds to a lookup for
    a place called humidity. A withheld rebind costs one deterministic turn; a wrong one invents a
    subject the user never named. `test_a_lowercase_unknown_noun_does_not_rebind_the_obligation`
    pins this rather than leaving it as an accident.
    """

    probe = str(candidate or "").strip()
    if not probe or len(probe.split()) > _SLOT_FILLER_MAX_TOKENS:
        return ""
    if _NON_SUBJECT_GRAMMAR_RE.search(probe):
        # Interrogative or comparative structure survived the strip: the turn is asking ABOUT the
        # subjects, not naming a new one.
        return ""
    try:
        if operation == "market_quote":
            from core.agent_runtime.fast_live_info_price import price_assets_named

            # The obligation on the table IS `market_quote`, so this thread's domain is already
            # established and the bare residue of a correction ("i mean btc") needs no market word
            # of its own. Passed explicitly rather than inferred: an entity table may never assert
            # its own domain, but a caller that holds one may state it.
            aliases = [str(a).strip() for a in (price_assets_named(probe, domain_already_authorized=True) or ()) if str(a).strip()]
            if not aliases:
                return ""
            from core.followup_subject_continuity import strip_correction_language
            alias_tokens = {tok for alias in aliases for tok in _TOKEN_RE.findall(alias.casefold())}
            subject_span = strip_correction_language(probe)
            if any(tok.casefold() not in alias_tokens for tok in _TOKEN_RE.findall(subject_span)):
                # CT-203: an unexplained content word beside the asset means a new request
                # ("gold mined", "much gold", "poem gold"); correction and coordinator words are
                # stripped through the follow-up module's own vocabulary first, so "solana instead"
                # and "or ethereum?" still read as the bare subject they are.
                return ""
            if original_text:
                authorized = {
                    str(a).strip().casefold()
                    for a in (price_assets_named(original_text, domain_already_authorized=True) or ())
                }
                aliases = [alias for alias in aliases if alias.casefold() in authorized]
            return aliases[0] if aliases else ""
        if operation == "weather_lookup":
            from tools.web.web_research import _is_plausible_weather_location

            if not all(word[:1].isupper() for word in probe.split() if word):
                return ""
            if _TITLE_CASED_PHRASE_MARKERS & {
                token for token in _TOKEN_RE.findall(str(original_text or ""))
            }:
                # CT-204: a Capitalised preposition/conjunction mid-turn ("Write About Winter",
                # "Code About Python Now") is Title-case PHRASE typing, not a place name -- the
                # scaffolding strip ate the preposition and left an all-capitalised fragment that
                # phone auto-capitalisation produced. Natural typing lowercases these ("what
                # about Tallinn?"), so the marker is checked as typed. A withheld rebind costs
                # one deterministic turn; a wrong one invents a place the user never named.
                return ""
            return probe if _is_plausible_weather_location(probe) else ""
    except Exception:
        return ""
    return ""


def _recovered_slot(
    slot: str, token_casefolds: set[str], tokens: list[str]
) -> tuple[bool, bool]:
    """Whether the turn recovers this grounded slot, and whether only by way of a typo.

    A slot recovers when every word of it is present in the turn either verbatim or as a typo
    (`_is_typo_of`). The second return value is the load-bearing half: a turn whose subjects
    arrived CLEAN did not need recovery, and something else is going on in it -- "I'm booking
    flights to Kaunas and Tallinn, them two" is a new request that happens to mention the old
    subjects. Only broken spelling is evidence that the user is pointing BACK at the referents
    the conversation already grounded.
    """

    parts = [part for part in str(slot).split() if part]
    if not parts:
        return False, False
    exact = all(part.casefold() in token_casefolds for part in parts)
    if exact:
        return True, False
    for part in parts:
        if not any(_is_typo_of(token, part) for token in tokens):
            return False, False
    return True, True


def _clarification_recovers_the_obligation(
    text: Any,
    *,
    source_context: Mapping[str, Any] | None,
) -> str:
    """A long, typo-ridden clarification still re-asks the obligation it restates.

    Measured live on c2829100 through the real `/api/chat` seam, fourth turn of an ordinary
    grounded conversation::

        U: Get weather for Kaunas.            -> kaunas, 18 C, real receipt
        U: What about Tallinn?                -> tallinn, 12 C, real receipt
        U: Which one is warmer?               -> both, aggregate answer
        U: I asked about Kauans and talling
           weather, last question was follow
           up for them two right?             -> the model lane, answer from nothing

    The shape gate (`_continuation_residue`) correctly declines the turn -- sixteen tokens is a
    statement, not a nudge, and the bound is what keeps ordinary long turns from ever reaching the
    database. But this turn is not an ordinary statement: both its subjects are the conversation's
    own grounded referents, MISTYPED, and the user is asking about them. "Kauans" is the only
    spelling of Kaunas the turn offers, so the strict paths -- which need the subject spelled
    cleanly enough to rebind -- can never serve it.

    So the clarification path, and its bounds, one per measured failure mode:

    * NO DIGITS. Same rule as the shape gate: "and 3 days?" changes the request.
    * CLOSED-CLASS SET GRAMMAR must be present (`_SET_REFERENCE_RE`), checked BEFORE the database
      is touched so an ordinary long turn still costs a regex and nothing more. "them two" is the
      user pointing at the accumulated set; without such a word the turn is not talking about the
      set at all.
    * EVERY slot the obligation holds must recover, and at least one ONLY through a typo. A turn
      that names some subjects cleanly and others not at all ("Kauans and the usual, them two
      right?") does not recover a binding the user did not restate; a turn that names everything
      cleanly carried its content through intact and the existing lanes already own it.
    * NO NEW SUBJECT alongside: a capitalized word that recovers no grounded slot may be a fresh
      referent ("...them two, plus Bergen?"), and rebinding would swallow it. Sentence-initial
      capitals are position, not naming, so the first word is exempt.

    What fires is the same AGGREGATE rebinding the strict path expresses -- the obligation's own
    request text with the recovered slots substituted in -- so the lookups, receipts and honesty
    validators downstream are exactly the ones any other aggregate faces. There is no city list
    here and no new recognizer: the vocabulary the typos are matched against is the conversation's
    OWN recorded slots, and nothing else.
    """

    raw = " ".join(str(text or "").split()).strip()
    if not raw or _DIGIT_RE.search(raw):
        return ""
    if not _SET_REFERENCE_RE.search(raw):
        return ""
    if _META_QUESTION_RE.search(raw):
        # CT-206: "and golf, them two, was that what i asked about?" questions WHAT WAS ASKED
        # instead of restating it. A clarification re-names the obligation's subjects; an
        # interrogative about the conversation itself ("was/is/... that/it this what/the ...")
        # is the user asking whether some newcomer WAS the request -- introducing that newcomer
        # (here one typo away from the real slot) must reach the model, not rebind. Closed-class
        # grammar only; declarative restatements ("i asked about kauans and talling weather,
        # them two right?") do not invert and keep recovering.
        return ""

    obligation = _recorded_obligation(source_context)
    if not obligation:
        return ""
    operation = str(obligation.get("operation") or "")
    slots = [str(item).strip() for item in (obligation.get("slots") or []) if str(item).strip()]
    request_text = str(obligation.get("request_text") or "").strip()
    if not operation or not slots or not request_text:
        return ""

    raw_tokens = _TOKEN_RE.findall(raw)
    token_casefolds = {token.casefold() for token in raw_tokens}
    recovered: list[str] = []
    typo_recovered = False
    for slot in slots:
        hit, via_typo = _recovered_slot(slot, token_casefolds, raw_tokens)
        if not hit:
            return ""
        typo_recovered = typo_recovered or via_typo
        recovered.append(slot)
    if not typo_recovered:
        return ""

    for index, token in enumerate(raw_tokens):
        if index == 0 or len(token) <= 2:
            continue
        if token[0].isupper() and not any(
            _is_typo_of(token, part) for slot in slots for part in slot.split()
        ):
            # A capitalized word that is none of the recovered referents may be a new subject
            # arriving inside the clarification. Declining keeps today's behaviour -- the turn
            # stays a statement -- rather than guessing it away.
            return ""

    if _content_the_obligation_does_not_hold(raw, slots=slots):
        # The turn says something of its own. A clarification RESTATES the obligation -- its
        # words are the obligation's subjects (however mistyped), set grammar, scaffolding and talk
        # about the conversation itself, and nothing else. Any word outside those classes is the
        # user naming something the obligation never held, and rebinding would answer the old
        # question in place of the new one. Capitalisation is not the test here: the user who typed
        # "vw golf" in lower case still asked about a car.
        return ""

    return _rebound_request(request_text, old_slots=slots, new_slots=recovered)


#: "which city is", "which place was", "which coin has": the noun between the selector and its verb
#: names the KIND of member being selected from the set, not a new subject. A determiner phrase is
#: grammar, so the noun is recognised by its position and never by a list of domain nouns.
_SELECTOR_NOUN_RE = re.compile(
    r"\bwhich\s+([^\W\d_]+)\s+(?:is|are|was|were|has|have|had|does|do|did|will|would|should)\b",
    re.IGNORECASE,
)


def _names_domain_the_obligation_does_not_hold(
    text: Any, *, obligation: Mapping[str, Any] | None, prior: str
) -> bool:
    """Whether the turn's scaffolding-stripped emptiness depends on a DOMAIN noun this
    obligation never held (CT-201).

    The residue gate strips every `_SCAFFOLDING` token, domain nouns included, so
    "and the weather there?" after a gold turn reads as a bare nudge. A nudge re-asks
    the SAME subject; a noun from another live-data domain makes the turn a new
    request. The noun is stripped legitimately only when the obligation itself is
    about that noun -- decided against the obligation's own recorded vocabulary
    (request text, slots, absorbed turns, founding prior), never a domain list.
    """
    raw = str(text or "")
    domain_nouns = {
        folded
        for folded in (token.casefold() for token in _TOKEN_RE.findall(raw))
        if folded in _SCAFFOLDING_DOMAIN_NOUNS or folded.rstrip("s") in _SCAFFOLDING_DOMAIN_NOUNS
    }
    if not domain_nouns:
        return False
    vocabulary_parts: list[str] = [str(prior or "")]
    if obligation:
        vocabulary_parts.append(str(obligation.get("request_text") or ""))
        vocabulary_parts.extend(str(slot) for slot in (obligation.get("slots") or []))
        vocabulary_parts.extend(str(turn) for turn in (obligation.get("absorbed") or []))
    vocabulary = {
        token.casefold()
        for part in vocabulary_parts
        for token in _TOKEN_RE.findall(str(part or ""))
    }
    vocabulary = {word for word in vocabulary if word} | {word.rstrip("s") for word in vocabulary}
    operation = str((obligation or {}).get("operation") or "")
    for noun in domain_nouns:
        if noun in vocabulary or noun.rstrip("s") in vocabulary:
            continue
        owner = _DOMAIN_NOUN_OPERATIONS.get(noun, "")
        if owner and operation == owner:
            continue
        if owner and (vocabulary & _DOMAIN_NOUN_GROUPS[owner]):
            # No recorded obligation (or a different one): the thread's own vocabulary
            # already speaks this noun's domain ("weather" in the prior text holds "temp?").
            continue
        return True
    return False


def _content_the_obligation_does_not_hold(text: Any, *, slots: list[str]) -> list[str]:
    """The turn's words that no closed class and no grounded slot accounts for.

    A word is accounted for when it is scaffolding (singular or plural), dialogue-meta, set grammar
    or non-subject grammar, the noun of a "which <noun> <verb>" selector, or one of the obligation's
    own slot words verbatim or as a typo. Tokens of two letters or fewer are ignored, as the capital
    check in the clarification path ignores them: at that length there is nothing to recognise. The
    result is returned rather than a bool so a caller (and a test) can name exactly which words made
    the turn a statement.
    """

    raw = str(text or "")
    selector_nouns = {match.group(1).casefold() for match in _SELECTOR_NOUN_RE.finditer(raw)}
    slot_parts = [part for slot in slots for part in str(slot).split() if part]
    unexplained: list[str] = []
    for token in _TOKEN_RE.findall(raw):
        if len(token) <= 2:
            continue
        folded = token.casefold()
        if folded in selector_nouns:
            continue
        if folded in _SCAFFOLDING or folded.rstrip("s") in _SCAFFOLDING or folded in _DIALOGUE_META:
            continue
        if _SET_REFERENCE_RE.fullmatch(token) or _NON_SUBJECT_GRAMMAR_RE.fullmatch(token):
            continue
        if any(_is_typo_of(token, part) for part in slot_parts):
            continue
        unexplained.append(token)
    return unexplained


def _allocation_continuation(text: Any, source_context: Mapping[str, Any] | None) -> tuple[str, str]:
    """Correct the targets of the same calculation, preserving its sum and other asks.

    Only an explicit correction or a bare request to finish can bind. The nearest
    independent user turn must belong to this obligation; a topic change ends it.
    """
    body = str(text or "").strip()
    correction = re.fullmatch(r"(?:no[, ]+)?(?:i\s+)?(?:meant|ment|mean|actually)\s+(.+?)[.!?]*", body, re.I)
    reask = utterance_carries_no_independent_request(body) or bool(re.fullmatch(
        r"(?:(?:ok|okay|so|please)[, ]+)*(?:answer|finish|complete)\s+(?:the\s+)?(?:original|previous)\s+(?:question|request|calculation)(?:\s+in\s+full)?[.!?]*", body, re.I,
    ))
    if not correction and not reask:
        return "", ""
    obligation = _recorded_obligation(source_context)
    if not obligation:
        return "", ""
    from core.conductor.operations import (
        _PURCHASE_OBJECTS_RE,
        _purchase_objects,
        _resolve_role,
        allocation_purchase_roles,
    )
    from core.input_normalizer import normalize_user_text

    request = normalize_user_text(str(obligation.get("request_text") or "")).normalized_text
    allocation = allocation_purchase_roles(request)
    if allocation is None:
        return "", ""
    anchors = {" ".join(str(t).split()).casefold() for t in [obligation.get("request_text"), *(obligation.get("absorbed") or [])] if t}
    pending = " ".join(body.split()).casefold()
    anchored = False
    for item in reversed(_messages(source_context)):
        if item.get("role") != "user":
            continue
        previous = " ".join(str(item.get("content") or "").split()).casefold()
        if pending and previous == pending:
            pending = ""
            continue
        if previous in anchors:
            anchored = True
            break
        if utterance_carries_no_independent_request(previous):
            continue
        break
    if not anchored:
        return "", ""
    if not correction:
        return request, INHERIT_REASK
    targets = _purchase_objects("buy " + correction.group(1))
    if len(targets) != allocation.parts or any(_resolve_role(t, allow_currency=True) is None for t in targets):
        return "", ""
    matches = list(_PURCHASE_OBJECTS_RE.finditer(request))
    if not matches:
        return "", ""
    start, end = matches[-1].span("objects")
    return request[:start] + ", ".join(targets) + request[end:], INHERIT_CLARIFICATION


def continuation_inheritance(
    text: Any,
    *,
    source_context: Mapping[str, Any] | None,
) -> tuple[str, str]:
    """The live-data request this utterance re-asks, or "" when it does not.

    Two halves must hold, as they always did: the utterance must carry no request of its own, and a
    live-data obligation must already be on the table. What changed is that "carries no request of
    its own" no longer means "is empty once stripped" -- a turn whose whole residue is a subject for
    the obligation already open is still not making a request of its own, it is rebinding that one.

    The shape gate is FIRST for a reason beyond reading order: this module now touches a database,
    and an ordinary request must not pay for that. Measured on this branch, 400 calls each:

        ordinary turn (>6 tokens, exits at the shape gate) .... 0.002 ms
        follow-up shaped, no session in context ............... 0.186 ms
        full rebind, obligation read and substituted .......... 1.236 ms

    So the read is paid only by turns that are already follow-up shaped -- roughly 5 ms across the
    four calls a real turn makes, against a turn that is otherwise making model and network calls.

    The one exception to the shape gate is the typo-ridden CLARIFICATION
    (`_clarification_recovers_the_obligation`): a turn too long to be a nudge whose subjects are
    nonetheless the obligation's own, mistyped. Its database read is gated on a closed-class set
    reference, so an ordinary long turn still exits at the shape gate for the price of one regex.
    """

    allocation_request = _allocation_continuation(text, source_context)
    if allocation_request[0]:
        return allocation_request
    residue = _continuation_residue(text)
    if residue is None:
        # The turn is stating something of its own. Nothing is inherited -- this is the branch that
        # keeps an unrelated new turn from replaying the previous subject. The single exception is
        # the clarification below, whose every bound is stated where it is checked.
        recovered = _clarification_recovers_the_obligation(text, source_context=source_context)
        return recovered, (INHERIT_CLARIFICATION if recovered else "")

    obligation = _recorded_obligation(source_context)
    prior = prior_live_data_request(
        source_context,
        absorbed=(obligation or {}).get("absorbed"),
        current_text=text,
        slots=(obligation or {}).get("slots"),
        operation=str((obligation or {}).get("operation") or ""),
    )
    if not prior:
        return "", ""

    if not residue:
        # RE-ASK: the obligation again, at its CURRENT binding. `prior` is the founding request, and
        # after a rebind the two differ -- measured: "Vilnius" / "and Riga?" / "no i meant Warsaw" /
        # "actually Tallinn" / "and now?" re-asked VILNIUS, because the walk-back returns the oldest
        # text in the thread while the obligation had long since moved on. A bare nudge means "that
        # question again", and "that question" is whatever it most recently became.
        if _names_domain_the_obligation_does_not_hold(text, obligation=obligation, prior=prior):
            # CT-201: the residue only looked empty because a DOMAIN noun ("weather", "price",
            # "news"...) was stripped, and this obligation never held that noun. The turn is a
            # new, underspecified request in that noun's domain, not a nudge -- decline and let
            # the model lane ask.
            return "", ""
        recorded = str((obligation or {}).get("request_text") or "").strip()
        return (recorded or prior), INHERIT_REASK

    # Past here the turn named something. Only a recorded obligation can say whether that something
    # is a subject for it, so without one there is nothing to rebind and the turn keeps the
    # behaviour it has always had.
    if not obligation:
        return "", ""
    operation = str(obligation.get("operation") or "")
    slots = [str(item) for item in (obligation.get("slots") or []) if str(item).strip()]
    request_text = str(obligation.get("request_text") or "") or prior
    if not operation or not slots:
        return "", ""

    # REBIND before AGGREGATE: a turn that names a subject is asking about THAT subject, even when
    # it also carries set grammar ("which is warmer, Riga or Vilnius?").
    filler = _slot_filler_for(operation, _residue_span(text, residue), original_text=str(text or ""))
    if filler:
        return _rebound_request(request_text, old_slots=slots, new_slots=[filler]), INHERIT_REBIND

    if (
        _SET_REFERENCE_RE.search(str(text or ""))
        and len(slots) > 1
        and _aggregate_says_nothing_but_a_comparison(text, slots=slots)
    ):
        return _rebound_request(request_text, old_slots=slots, new_slots=slots), INHERIT_AGGREGATE

    return "", ""


#: How `continuation_inheritance` arrived at an inherited request. The plan builder reads this to
#: decide which canonical demand units a subtask may bind (CT-302): a bare re-ask or an aggregate
#: maps the WHOLE raw turn onto the inherited request; a rebind or a clarification names the new
#: subject in the raw text, so only units whose own text carries it may bind.
INHERIT_REASK = "reask"
INHERIT_REBIND = "rebind"
INHERIT_AGGREGATE = "aggregate"
INHERIT_CLARIFICATION = "clarification"


def continuation_inherits_live_data(text: Any, *, source_context: dict[str, Any] | None = None) -> str:
    """The request this turn re-asks, rebinds or clarifies -- or "" when it states its own."""
    return continuation_inheritance(text, source_context=source_context)[0]


def typo_tokens_for(text: Any, *needles: str) -> tuple[str, ...]:
    """The tokens of `text` that are the needle verbatim or one typo away from it (same rule as the
    clarification path), so a receipt can bind the unit the user actually wrote, misspelt."""
    tokens = _TOKEN_RE.findall(str(text or ""))
    found: list[str] = []
    for needle in needles:
        target = str(needle or "").strip()
        if not target:
            continue
        for token in tokens:
            if token.casefold() == target.casefold() or _is_typo_of(token.casefold(), target.casefold()):
                if token not in found:
                    found.append(token)
    return tuple(found)


#: A comparative/superlative by shape is a comparison only in predicate position: directly
#: after a linking verb ("which one IS warmer") or the superlative determiner ("the warmest").
#: The same letter-shape names ordinary nouns elsewhere -- measured CT-205, "which one has a
#: river?" / "which one has water?" re-asked a weather comparison because "river" and "water"
#: end in -er; possession ("has a river") marks the word as the OBJECT being asked about, a new
#: subject the obligation never held.
_COMPARATIVE_PREDICATE_PREDECESSORS = frozenset(
    {
        "is", "are", "was", "were", "be", "been", "being", "am",
        "feel", "feels", "felt", "get", "gets", "got", "look", "looks", "looked",
        "seem", "seems", "seemed", "sound", "sounds", "sounded", "the",
    }
)


def _shape_word_is_a_comparison(text: Any, word: str) -> bool:
    """Whether an unexplained -er/-est-shaped word sits in comparative predicate position."""
    tokens = [token.casefold() for token in _TOKEN_RE.findall(str(text or ""))]
    for index, token in enumerate(tokens):
        if token != word:
            continue
        if index == 0:
            # A leading shape word ("warmer?") has no predicate to sit in; declining is the
            # safe direction -- the turn reaches the model instead of re-asking the set.
            return False
        if tokens[index - 1] in _COMPARATIVE_PREDICATE_PREDECESSORS:
            return True
    return False


def _aggregate_says_nothing_but_a_comparison(text: Any, *, slots: list[str]) -> bool:
    """Whether a short set-grammar turn only compares the set, rather than asking something new.

    "which one is warmer?", "compare them", "both please" re-ask the obligation for its whole set.
    "are they good cars?", "do they sell tires?", "which one should i buy?" are new questions that
    happen to contain "they" or "which one" -- measured 2026-09-06 after a two-city weather thread,
    every one of them re-asked the weather. The set grammar is the same in both groups; what
    differs is what else the turn says. An aggregate adds at most ONE content word, and that word
    is the comparison: a closed-class comparison word, or a comparative/superlative by shape IN
    PREDICATE POSITION (after a linking verb or "the"). Two content words, or one that compares
    nothing, is the user asking about something the obligation never held.
    """

    unexplained = _content_the_obligation_does_not_hold(text, slots=slots)
    if not unexplained:
        return True
    if len(unexplained) > 1:
        return False
    word = unexplained[0].casefold()
    if word in _COMPARISON_WORDS:
        return True
    if len(word) >= 5 and word.endswith(("er", "est")):
        return _shape_word_is_a_comparison(text, word)
    return False


__all__ = [
    "continuation_inherits_live_data",
    "prior_live_data_request",
    "utterance_carries_no_independent_request",
]
