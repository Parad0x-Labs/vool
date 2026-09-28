"""Deterministic clause-level representation of one user turn.

This module owns structural request boundaries, stable clause identity, source spans, conservative
request-kind classification, and clause-local response shapes.  It deliberately does not select
providers, operations, tools, or execution order; those are downstream policy decisions that can
consume this representation without reparsing the user's words.
"""

from __future__ import annotations

import re
from bisect import bisect_left
from collections.abc import Callable
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any


@dataclass(frozen=True)
class ResponseConstraint:
    """A response-shape contract, either global or attached to one clause."""

    exact_words: int | None = None
    max_words: int | None = None
    exact_sentences: int | None = None
    max_sentences: int | None = None
    list_items: int | None = None
    one_item_per_line: bool = False
    per_item_sentences: int | None = None
    # An EXPLICIT presentation request ("as a table", "as a mermaid diagram"): one of the
    # closed format tokens in core.response_constraints.PRESENTATION_FORMATS. An explicit
    # request outranks the answer's default presentation; an absent one means the model
    # chooses a shape only when the content has it.
    presentation_format: str | None = None
    presentation_formats: tuple[str, ...] = ()
    # Who authored this contract. None/absent = the explicit parser read it from the user's
    # own turn. "automatic" = the C19 presentation selector DERIVED it from the answer's
    # shape (never from user text) and flows it through the one bounded repair. A derived
    # contract must never claim the user asked for its shape.
    origin: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        # An explicit contract serializes byte-identically to before the C19
        # selector existed: its dict carries no ``origin`` key at all. Only a
        # derived (automatic) contract names its origin on the wire.
        if payload.get("origin") is None:
            payload.pop("origin", None)
        if not payload.get("presentation_formats"):
            payload.pop("presentation_formats", None)
        return payload

    @property
    def requested_formats(self) -> tuple[str, ...]:
        return self.presentation_formats or ((self.presentation_format,) if self.presentation_format else ())

    @property
    def wants_layout(self) -> bool:
        return self.list_items is not None or self.one_item_per_line

    @property
    def wants_presentation(self) -> bool:
        return self.presentation_format is not None


class ClauseKind(str, Enum):
    """Only the coarse request kinds a deterministic request head can establish."""

    KNOW = "know"
    COMPUTE = "compute"
    OBSERVE = "observe"
    ACT = "act"
    TRANSFORM = "transform"
    CREATE = "create"
    RECALL = "recall"
    CLARIFY = "clarify"
    #: A sentence that CONSTRAINS how the turn may be answered rather than asking for anything --
    #: "Do NOT search the web for this.", "Never use the internet." It is deliberately NOT
    #: `UNKNOWN`: `UNKNOWN` is permissive, appearing in `accepted_kinds` for most operations in
    #: `core.conductor.operations`, so a prohibition classified UNKNOWN gets picked up and served.
    #: It is also not a content-request kind, so the obligation floor stops reporting an
    #: instruction the runtime OBEYED as an unanswered demand -- measured on build 7de469ba,
    #: acceptance turn 7: `- Do NOT search the web for this. - no part of the plan covered this
    #: request`. `core.conductor.planner` already documents that a retrieval prohibition is "not a
    #: request"; this is that decision made available to every caller instead of one call site.
    CONSTRAINT = "constraint"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class TurnClause:
    """One structurally independent request with exact offsets into ``TurnIR.source_text``."""

    clause_id: str
    ordinal: int
    label: str | None
    start: int
    end: int
    request_start: int
    request_end: int
    original_text: str
    request_text: str
    kind: ClauseKind
    response_shape: ResponseConstraint | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "clause_id": self.clause_id,
            "ordinal": self.ordinal,
            "label": self.label,
            "start": self.start,
            "end": self.end,
            "request_start": self.request_start,
            "request_end": self.request_end,
            "original_text": self.original_text,
            "request_text": self.request_text,
            "kind": self.kind.value,
            "response_shape": (
                self.response_shape.to_dict() if self.response_shape is not None else None
            ),
        }


@dataclass(frozen=True)
class TurnIR:
    """Structural truth derived from one untouched user turn."""

    source_text: str
    clauses: tuple[TurnClause, ...]
    preamble: str = ""
    preamble_start: int | None = None
    preamble_end: int | None = None
    global_response_shape: ResponseConstraint | None = None

    @property
    def governing_instruction_span(self) -> tuple[int, int] | None:
        """A bare request head introducing the explicit list, not a separate demand.

        This is source structure only, never permission to execute the children.
        Heads with their own object and commands without a following list retain
        their ordinary request meaning.
        """
        if self.preamble_start is None or not self.clauses or not self.clauses[0].label:
            return None
        match = re.search(
            r"(?:^|[.!?]\s+|\n[ \t]*)(?P<head>[A-Za-z]+)[ \t]*:[ \t]*\Z",
            self.preamble,
        )
        if match is None or classify_clause_kind(match.group("head")) is ClauseKind.UNKNOWN:
            return None
        return self.preamble_start + match.start("head"), self.preamble_start + match.end()

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_text": self.source_text,
            "preamble": self.preamble,
            "preamble_start": self.preamble_start,
            "preamble_end": self.preamble_end,
            "global_response_shape": (
                self.global_response_shape.to_dict()
                if self.global_response_shape is not None
                else None
            ),
            "clauses": [clause.to_dict() for clause in self.clauses],
        }


ResponseShapeParser = Callable[[str], ResponseConstraint | None]
_DEFAULT_SHAPE_PARSER = object()

_EXPLICIT_MARKER_RE = re.compile(
    r"(?m)(?:^|(?<=[\s;]))(?P<label>[A-Za-z]|\d{1,3})\s*[).:\-\]>]\s+"
)
_BULLET_MARKER_RE = re.compile(r"(?m)^[ \t]*(?P<bullet>[-*+•‣▪▫◦·–—])\s+")
_REQUEST_HEAD_RE = re.compile(
    r"(?:(?:please|pls|plz|now|next|first|second|third|finally|physically|actually|immediately)\s+)*"
    r"(?P<head>[A-Za-z][A-Za-z']*)\b",
    re.IGNORECASE,
)
#: The starred prefix group of `_REQUEST_HEAD_RE` is the polynomial shape the scanner
#: flags on uncontrolled turns. `_match_request_head` below reproduces the pattern's
#: exact backtracking semantics (greedy prefix consumption, then the head retried at
#: each earlier stop; the head itself greedy over letters and apostrophes with the
#: `\b` retry that apostrophes make reachable) in one linear pass. The pattern stays
#: for the differential harness that pins the scanner to it.
_REQUEST_PREFIX_WORDS = (
    "please", "pls", "plz", "now", "next", "first", "second", "third",
    "finally", "physically", "actually", "immediately",
)
_HEAD_RUN_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz'")
_HEAD_START_CHARS = frozenset("abcdefghijklmnopqrstuvwxyz")
_WORD_CHAR_RE = re.compile(r"\w", re.UNICODE)


def _is_word_char(ch: str) -> bool:
    return bool(_WORD_CHAR_RE.match(ch))


def _match_request_head(text: str, start: int, end: int) -> str | None:
    """The captured head of `_REQUEST_HEAD_RE.match(text, start, end)`, or None.

    Consume prefix words greedily (each literal word followed by one whole
    whitespace run — a shorter run can never help, since a prefix word and a head
    both start on a word character), then try the head at every stop from the last
    back to the start, exactly as backtracking would. A head is the longest cut of
    the letter-apostrophe run whose end satisfies `\\b`; apostrophes are not word
    characters, so the cut before one can be the boundary the greedy run misses.
    """
    if start >= end:
        return None
    stops: list[int] = [start]
    pos = start
    while pos < end:
        consumed = False
        for word in _REQUEST_PREFIX_WORDS:
            word_end = pos + len(word)
            if (
                word_end < end
                and text[pos:word_end].casefold() == word
                and text[word_end].isspace()
            ):
                after = word_end + 1
                while after < end and text[after].isspace():
                    after += 1
                stops.append(after)
                pos = after
                consumed = True
                break
        if not consumed:
            break
    for stop in reversed(stops):
        if stop >= end or not (text[stop].isascii() and text[stop].lower() in _HEAD_START_CHARS):
            continue
        run_end = stop + 1
        while run_end < end and text[run_end].isascii() and text[run_end].lower() in _HEAD_RUN_CHARS:
            run_end += 1
        cut = run_end
        while cut > stop:
            before_is_word = _is_word_char(text[cut - 1])
            after_is_word = cut < end and _is_word_char(text[cut])
            if before_is_word != after_is_word:
                return text[stop:cut]
            cut -= 1
    return None
_KNOW_HEADS = frozenset(
    {
        "answer", "define", "describe", "explain", "give", "identify", "list", "name",
        "provide", "say", "suggest", "tell", "what", "what's", "whats", "which", "who",
        "why", "how", "where", "when", "is", "are", "was", "were", "do", "does", "did",
        "can", "could", "would", "should", "has", "have", "had", "may", "might", "must",
    }
)
_COMPUTE_HEADS = frozenset({"calc", "calculate", "compute", "count"})
_OBSERVE_HEADS = frozenset({"browse", "check", "find", "inspect", "look", "read", "search"})
_ACT_HEADS = frozenset(
    {
        "append", "call", "close", "contact", "defuse", "delete", "deploy", "dial", "disarm", "download",
        "drive", "edit", "email", "execute", "feed", "fix", "fly", "land", "move", "notify", "open", "park",
        "dm", "message", "perform", "phone", "pour", "post", "punch", "publish", "push", "submit", "slap", "sms", "text",
        "transmit", "walk",
        "remove", "rename", "run", "save", "send", "share", "shut", "start", "stop", "switch",
        "turn", "unplug", "upload",
    }
)
_TRANSFORM_HEADS = frozenset(
    {"convert", "rephrase", "rewrite", "reword", "summarise", "summarize", "translate"}
)
_CREATE_HEADS = frozenset(
    {"build", "create", "draft", "generate", "make", "plan", "print", "return", "write"}
)
#: An object this runtime would author, together with the act of placing or naming it.  "Define"
#: spells two different requests -- asking what a term means, and bringing a named artifact into
#: existence -- and grammar alone cannot separate "define a class in OOP" from "define a class in
#: models.py".  Requiring BOTH an artifact noun and an explicit naming or location complement is
#: the linguistic signature of authoring, so a bare conceptual definition never matches.
_AUTHORED_ARTIFACT_RE = re.compile(
    r"\b(?:variable|constant|function|method|class|column|field|table|schema|endpoint|route|"
    r"migration|fixture|helper|handler|component)\b"
    r"(?:[^.!?;\n]{0,60}?\b(?:called|named)\b"
    r"|[^.!?;\n]{0,60}?\bin\s+(?:the\s+)?[\w./-]*\.\w{1,5}\b"
    r"|[^.!?;\n]{0,60}?\bin\s+the\s+[\w-]+\s+(?:file|table|module|schema|database|script|class)\b)",
    re.IGNORECASE,
)
# Leading-boundary-stripped form for span queries whose clause begins with the artifact noun.
_AUTHORED_LEADING_RE = re.compile(
    r"(?:variable|constant|function|method|class|column|field|table|schema|endpoint|route|"
    r"migration|fixture|helper|handler|component)\b"
    r"(?:[^.!?;\n]{0,60}?\b(?:called|named)\b"
    r"|[^.!?;\n]{0,60}?\bin\s+(?:the\s+)?[\w./-]*\.\w{1,5}\b"
    r"|[^.!?;\n]{0,60}?\bin\s+the\s+[\w-]+\s+(?:file|table|module|schema|database|script|class)\b)",
    re.IGNORECASE,
)
_RECALL_RE = re.compile(
    r"\b(?:earlier|previous|prior|last\s+(?:answer|message|title)|remember|recall|said\s+before)\b",
    re.IGNORECASE,
)
_CONNECTOR_PREFIX_RE = re.compile(
    r"(?:(?:and|also|plus|then|and\s+then|and\s+also)\s*,?\s+)+",
    re.IGNORECASE,
)
_REQUEST_CONNECTOR_RE = re.compile(
    r"\b(?:and\s+then|and\s+also|and|then|also|plus)\b\s*,?\s+",
    re.IGNORECASE,
)


#: Every head any kind claims. Used only to decide whether a head match actually landed on a
#: request word, so the framing-prefix retry below stays a fallback and never reclassifies a
#: clause this function already placed.
#: `classify_clause_kind` also acts on heads that live in no head SET -- "bake"/"print" reach
#: ClauseKind.ACT through the physical-appliance check below. They must be listed here or the
#: framing-prefix retry treats them as unrecognised and skips past the very clause they own:
#: "Bake a chocolate cake in my physical oven right now." regressed to UNKNOWN exactly that way,
#: caught by tests/test_set2_action_sibling_contracts.py. Keep in sync with the special cases.
_SPECIAL_CASED_HEADS: frozenset[str] = frozenset({"print", "bake"})
# These open requests but do not establish an effect kind by themselves. "Get a
# quote" and "get a file" need their capability owner's reading of the object.
_AMBIGUOUS_REQUEST_HEADS: frozenset[str] = frozenset({"get", "fetch", "show"})

_ALL_REQUEST_HEADS: frozenset[str] = frozenset(
    set(_KNOW_HEADS)
    | set(_COMPUTE_HEADS)
    | set(_OBSERVE_HEADS)
    | set(_ACT_HEADS)
    | set(_TRANSFORM_HEADS)
    | set(_CREATE_HEADS)
    | set(_SPECIAL_CASED_HEADS)
    | set(_AMBIGUOUS_REQUEST_HEADS)
)


#: A framing phrase may precede the request inside one clause: "From memory:", "Off the top of your
#: head,". Bounded to a short lead ending at the FIRST ':' or ',' so this cannot swallow a real
#: clause -- clause splitting has already run, so what remains is one request at most.
_FRAMING_PREFIX_RE = re.compile(r"[^:,?!.]{1,48}[:,]\s*")

#: Where a second request may begin inside one clause. Used only to ask the prohibition authority
#: about each part separately; it never re-splits the turn, which clause parsing already did.
_CLAUSE_BOUNDARY_SPLIT_RE = re.compile(r"[,;]\s*|(?<=[.?!])\s+")

#: A sentence end followed by more text -- i.e. this span holds more than one sentence.
_SENTENCE_TAIL_RE = re.compile(r"[.?!]\s+\S")


#: An inverted temporal interrogative -- the question's interrogative sits behind its object
#: ("In what year did the Berlin Wall fall?", "On which continent is Egypt?"). The head word
#: the plain regex sees is the PREPOSITION, which no head set claims, so the clause read as
#: UNKNOWN and then died in every conductor family, because a plain question carries no
#: explanation-shape cue (F41, measured on acceptance turn 18). Re-heading on the
#: interrogative -- "what"/"which"/"whose" are already KNOW heads -- classifies the sentence
#: as the knowledge question it is. Three prepositions and three interrogatives, no subject
#: matter: this recognizes a sentence SHAPE, not a topic.
_INVERTED_INTERROGATIVE_RE = re.compile(
    r"(?:in|on|at)\s+(?P<interrogative>what|which|whose)\b",
    re.IGNORECASE,
)



def _is_whole_clause_constraint(clean: str) -> bool:
    """Whether this clause only CONSTRAINS the answer and asks for nothing.

    Delegated to `core.retrieval_constraints`, which already owns prohibition recognition for this
    runtime and returns both the negative clauses and the text that survives them. A second opinion
    here is how two parts of one system come to disagree about the same sentence -- the reason
    `_is_content_request` delegates to this module in the first place.

    The test is not "contains a prohibition" but "is NOTHING BUT one": `Do NOT search the web for
    this, what is 2+2?` carries a real demand and must stay a demand, and only its prohibition half
    is a constraint.
    """

    from core.retrieval_constraints import analyze_retrieval_constraints

    # ONE sentence only. `_starts_request` calls this function on every SUFFIX of the turn to find
    # clause boundaries, so a prohibition in the LAST sentence would otherwise make the entire tail
    # read as a constraint -- which split "Fly to Paris right now and bring me back a croissant."
    # in two and cost the deterministic unavailable-action plan its turn
    # (tests/test_followup_unavailable_effect_siblings.py caught it). A constraint is a property of
    # a clause; deciding it over a multi-sentence span is answering a different question.
    if _SENTENCE_TAIL_RE.search(clean):
        return False

    try:
        if not analyze_retrieval_constraints(clean).has_prohibition:
            return False
    except Exception:
        return False

    # Read eligibility per segment, not across the whole clause. The negative span is
    # greedy across a comma -- `Do NOT search the web for this, what is
    # 2+2?` comes back as ONE negative clause with `eligible_text='?'` -- which is the right,
    # conservative direction for retrieval eligibility (it removes MORE from the search text) and
    # exactly the wrong direction here, where it would hide a real demand and silently drop it.
    # Same authority, asked per segment instead, so neither behaviour has to change.
    for segment in _CLAUSE_BOUNDARY_SPLIT_RE.split(clean):
        candidate = segment.strip()
        if not candidate:
            continue
        try:
            # Read each segment after removing its prohibition. An inline restriction
            # ("get the rate without using the web") leaves a positive request head;
            # a pure ban ("do not search the web") leaves no request at all.
            eligible = analyze_retrieval_constraints(candidate).eligible_text.strip()
            head = _match_request_head(eligible, 0, len(eligible))
            if head is not None and head.casefold() in _ALL_REQUEST_HEADS:
                return False
        except Exception:
            return False
    return True


# Head-gated content cues, hoisted from the classification body so the span index below can
# precompute their positions once per turn. Bounded match length is what makes that safe.
_PHYSICAL_APPLIANCE_RE = re.compile(
    r"\b(?:physical|local)\b[^.!?;\n]{0,60}\b(?:printer|oven)\b"
    r"|\b(?:printer|oven)\b[^.!?;\n]{0,60}\bphysical\b",
    re.IGNORECASE,
)
_REWRITE_RUNTIME_TARGET_RE = re.compile(
    r"\b(?:your|the\s+runtime(?:'s)?|the\s+assistant(?:'s)?)\b"
    r"[^.!?;\n]{0,80}\b(?:internal|core|system|runtime|source)\b"
    r"[^.!?;\n]{0,40}\b(?:code|instructions?|identity|name)\b",
    re.IGNORECASE,
)
_GENERATIVE_NOUN_RE = re.compile(
    r"\b(?:title|heading|headline|name|caption|recipe|code|script|program)\b",
    re.IGNORECASE,
)
# Same patterns without the leading ``\b``: a clause cut out of the turn has no character before
# its first one, so a keyword that BEGINS the clause is a hit for the clause even when the full
# turn has a word character glued before it (only reachable right after a connector strip).
_RECALL_LEADING_RE = re.compile(
    r"(?:earlier|previous|prior|last\s+(?:answer|message|title)|remember|recall|said\s+before)\b",
    re.IGNORECASE,
)
_PHYSICAL_LEADING_RE = re.compile(
    r"(?:physical|local)\b[^.!?;\n]{0,60}\b(?:printer|oven)\b"
    r"|(?:printer|oven)\b[^.!?;\n]{0,60}\bphysical\b",
    re.IGNORECASE,
)
_REWRITE_LEADING_RE = re.compile(
    r"(?:your|the\s+runtime(?:'s)?|the\s+assistant(?:'s)?)\b"
    r"[^.!?;\n]{0,80}\b(?:internal|core|system|runtime|source)\b"
    r"[^.!?;\n]{0,40}\b(?:code|instructions?|identity|name)\b",
    re.IGNORECASE,
)
_GENERATIVE_LEADING_RE = re.compile(
    r"(?:title|heading|headline|name|caption|recipe|code|script|program)\b",
    re.IGNORECASE,
)
# "Tell them/him/her" is an act, not knowledge; checked anchored at the clause head.
_TELL_TARGET_RE = re.compile(r"tell\s+(?:them|him|her)\b", re.IGNORECASE)


class _SpanKeywordTable:
    """Match positions of one bounded-length keyword pattern, for span-membership queries.

    Answers ``does pattern.search(text[lo:hi]) match`` without rescanning the span: matches
    found once over the whole text cover everything strictly inside, the span's trailing edge
    (a cut string can END a match the full text would have continued past ``hi``) is rechecked
    against a bounded tail window, and a match beginning exactly at ``lo`` is rechecked without
    the leading word boundary. Unbounded patterns must not use this table.
    """

    __slots__ = ("_ends", "_leading", "_max_len", "_pattern", "_starts")

    def __init__(self, pattern: re.Pattern[str], leading: re.Pattern[str], max_len: int) -> None:
        self._pattern = pattern
        self._leading = leading
        self._max_len = max_len
        self._starts: tuple[int, ...] = ()
        self._ends: tuple[int, ...] = ()

    def index(self, text: str) -> None:
        matches = list(self._pattern.finditer(text))
        self._starts = tuple(match.start() for match in matches)
        self._ends = tuple(match.end() for match in matches)

    def hits(self, text: str, lo: int, hi: int) -> bool:
        starts = self._starts
        i = bisect_left(starts, lo)
        n = len(starts)
        while i < n:
            start = starts[i]
            if start >= hi:
                break
            if self._ends[i] <= hi:
                return True
            if start < hi - self._max_len:
                # A bounded match that started this far before hi cannot still be open at hi;
                # no recorded match can hit this span.
                break
            i += 1
        window = hi - self._max_len
        if window < lo:
            window = lo
        if self._pattern.search(text, window, hi) is not None:
            return True
        return self._leading.match(text, lo, hi) is not None


class _ClauseScanIndex:
    """Clause-classification inputs for one whole turn, computed in a single pass.

    ``_unmarked_spans`` asks about the clause starting at every structural separator. Each
    question the classification asks of a clause -- where does it end, could it carry a
    retrieval prohibition, does it contain a recall word or a head-gated cue -- is answered
    from these tables instead of slicing the suffix and rescanning it.
    """

    __slots__ = (
        "authored",
        "clause_ends",
        "directive_starts",
        "generative",
        "physical",
        "recall",
        "rewrite",
    )

    def __init__(self, text: str) -> None:
        from core.retrieval_constraints import negated_directive_positions

        self.clause_ends: tuple[int, ...] = tuple(
            match.start() for match in _CLAUSE_BOUNDARY_SPLIT_RE.finditer(text)
        )
        self.directive_starts: tuple[int, ...] = negated_directive_positions(text)
        self.recall = _SpanKeywordTable(_RECALL_RE, _RECALL_LEADING_RE, 40)
        self.physical = _SpanKeywordTable(_PHYSICAL_APPLIANCE_RE, _PHYSICAL_LEADING_RE, 200)
        self.rewrite = _SpanKeywordTable(_REWRITE_RUNTIME_TARGET_RE, _REWRITE_LEADING_RE, 320)
        self.authored = _SpanKeywordTable(_AUTHORED_ARTIFACT_RE, _AUTHORED_LEADING_RE, 320)
        self.generative = _SpanKeywordTable(_GENERATIVE_NOUN_RE, _GENERATIVE_LEADING_RE, 40)
        for table in (self.recall, self.physical, self.rewrite, self.authored, self.generative):
            table.index(text)

    def clause_end(self, position: int, text_end: int) -> int:
        ends = self.clause_ends
        i = bisect_left(ends, position)
        return ends[i] if i < len(ends) else text_end

    def directive_in(self, lo: int, hi: int) -> bool:
        # Conservative on purpose: a directive merely STARTING inside the span runs the string
        # authority, which then answers exactly. Only the absence of starts can skip it.
        starts = self.directive_starts
        i = bisect_left(starts, lo)
        return i < len(starts) and starts[i] < hi


def _classify_clause_span(
    text: str, start: int, end: int, index: _ClauseScanIndex
) -> ClauseKind:
    """``classify_clause_kind`` over a span of the source turn -- see that function's contract.

    Same steps in the same order; every question about span content is answered from the
    caller's index (built once per turn) instead of a fresh scan, and the connector/head
    parsing uses ``pos``/``endpos`` matches instead of slicing the clause out.
    """
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    connectors = _CONNECTOR_PREFIX_RE.match(text, start, end)
    if connectors is not None:
        start = connectors.end()
    if start >= end:
        return ClauseKind.UNKNOWN
    # A prohibition needs a negated directive inside the clause; when none even starts here the
    # constraint authority below would answer False, so it is not run. When one does, the
    # ORIGINAL string authority decides -- on the exact clause text, exactly as before.
    if index.directive_in(start, end) and _is_whole_clause_constraint(text[start:end]):
        return ClauseKind.CONSTRAINT
    if index.recall.hits(text, start, end):
        return ClauseKind.RECALL
    head = _match_request_head(text, start, end)
    clean_start = start
    if head is None or head.casefold() not in _ALL_REQUEST_HEADS:
        # A framing prefix must not hide the request behind it. "From memory: what is the boiling
        # point of water at sea level in Celsius?" heads on "From", which no head set claims, so
        # the clause read as UNKNOWN and the composer reported an answerable question as "the
        # available answer path does not safely match this request" (build 7fe94596 AND 7de469ba,
        # acceptance turn 7 -- the same text answers normally once the prefix is gone).
        #
        # Deliberately a FALLBACK, reached only where the answer would otherwise be UNKNOWN, so it
        # can add a classification but never change one this function already made.
        retried = _head_after_framing_prefix_span(text, start, end)
        if retried is None:
            retried = _head_after_inverted_preposition_span(text, start, end)
        if retried is None:
            return ClauseKind.UNKNOWN
        head, clean_start = retried
    head = head.casefold()
    if head in {"print", "bake"} and index.physical.hits(text, clean_start, end):
        return ClauseKind.ACT
    if head in _COMPUTE_HEADS:
        return ClauseKind.COMPUTE
    if head in _OBSERVE_HEADS:
        return ClauseKind.OBSERVE
    if head == "tell" and _TELL_TARGET_RE.match(text, clean_start, end):
        return ClauseKind.ACT
    if head in _ACT_HEADS:
        return ClauseKind.ACT
    if head == "rewrite" and index.rewrite.hits(text, clean_start, end):
        # Rewriting text supplied by the user is a transform. Rewriting the running assistant's
        # own protected implementation is a requested side effect and must enter effect admission.
        return ClauseKind.ACT
    if head in _TRANSFORM_HEADS:
        return ClauseKind.TRANSFORM
    if head in _CREATE_HEADS:
        return ClauseKind.CREATE
    if head in _KNOW_HEADS:
        # "Give a title" and "Name a heading" are generative even though ordinary factual
        # requests using the same verbs remain KNOW.  This narrow noun check avoids pretending a
        # deterministic parser understands the subject more deeply than it does.
        if head in {"give", "name", "suggest"} and index.generative.hits(text, clean_start, end):
            return ClauseKind.CREATE
        # Same narrow-object policy for the one KNOW head that is also an ordinary authoring verb.
        # Without this, "define a variable called total in the report file" reaches read-only
        # knowledge admission and a request to change something is answered as an explanation.
        if head == "define" and index.authored.hits(text, clean_start, end):
            return ClauseKind.CREATE
        return ClauseKind.KNOW
    return ClauseKind.UNKNOWN


def _head_after_framing_prefix_span(
    text: str, start: int, end: int
) -> tuple[str, int] | None:
    """Retry the request head once, past a leading framing phrase. `None` when nothing changes."""

    prefix = _FRAMING_PREFIX_RE.match(text, start, end)
    if prefix is None:
        return None
    remainder_start = prefix.end()
    while remainder_start < end and text[remainder_start].isspace():
        remainder_start += 1
    remainder_end = end
    while remainder_end > remainder_start and text[remainder_end - 1].isspace():
        remainder_end -= 1
    if remainder_start >= remainder_end:
        return None
    head = _match_request_head(text, remainder_start, remainder_end)
    if head is None or head.casefold() not in _ALL_REQUEST_HEADS:
        return None
    return head, remainder_start


def _head_after_inverted_preposition_span(
    text: str, start: int, end: int
) -> tuple[str, int] | None:
    """Retry the request head once, behind an inverted interrogative's object.

    Reached only where the answer would otherwise be UNKNOWN, so it can add a classification
    but never change one already made.
    """

    prefix = _INVERTED_INTERROGATIVE_RE.match(text, start, end)
    if prefix is None:
        return None
    remainder_start = prefix.start("interrogative")
    while remainder_start < end and text[remainder_start].isspace():
        remainder_start += 1
    remainder_end = end
    while remainder_end > remainder_start and text[remainder_end - 1].isspace():
        remainder_end -= 1
    if remainder_start >= remainder_end:
        return None
    head = _match_request_head(text, remainder_start, remainder_end)
    if head is None or head.casefold() not in _ALL_REQUEST_HEADS:
        return None
    return head, remainder_start


def classify_clause_kind(request_text: str) -> ClauseKind:
    """Return a conservative kind without inferring tools, authority, or live-data needs."""

    text = str(request_text or "")
    return _classify_clause_span(text, 0, len(text), _ClauseScanIndex(text))


def _quoted_positions(text: str) -> tuple[bool, ...]:
    """Mask quote contents while preserving exact source offsets."""

    pairs = {'"': '"', "“": "”", "'": "'", "‘": "’"}
    mask = [False] * len(text)
    closing: str | None = None
    for index, char in enumerate(text):
        if closing is not None:
            mask[index] = True
            if char == closing:
                closing = None
            continue
        if (
            char in {"'", "’"}
            and index > 0
            and index + 1 < len(text)
            and text[index - 1].isalnum()
            and text[index + 1].isalnum()
        ):
            continue
        if char in pairs:
            mask[index] = True
            closing = pairs[char]
    return tuple(mask)


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end


@dataclass(frozen=True)
class _Marker:
    start: int
    content_start: int
    label: str | None


def _structural_markers(text: str, quoted: tuple[bool, ...]) -> tuple[_Marker, ...]:
    explicit = tuple(
        _Marker(match.start("label"), match.end(), match.group("label"))
        for match in _EXPLICIT_MARKER_RE.finditer(text)
        if not quoted[match.start("label")]
        and (
            not text[: match.start("label")].strip()
            or text[: match.start("label")].rstrip()[-1] in ".!?;:\n·–—-*+•"
        )
    )
    bullets = tuple(
        _Marker(match.start(), match.end(), None)
        for match in _BULLET_MARKER_RE.finditer(text)
        if not quoted[match.start("bullet")]
    )
    if len(explicit) >= 2:
        return explicit
    if len(bullets) >= 2:
        return bullets
    return ()


def _starts_request(text: str, position: int, index: _ClauseScanIndex) -> bool:
    # A later request must not turn an earlier list member into a request head:
    # "Kaunas and Tallinn, tell me ..." keeps both cities in the observation.
    # Framing classification applies to complete clauses, not across a possible
    # boundary while deciding where those clauses begin.
    #
    # Callers hand over a position whose character is not whitespace, so the clause runs to the
    # next recorded split boundary (or the end of the turn) without slicing the suffix out; the
    # classification reads the caller's whole-turn index instead of rescanning the clause.
    clause_end = index.clause_end(position, len(text))
    return _classify_clause_span(text, position, clause_end, index) is not ClauseKind.UNKNOWN


def _unmarked_spans(text: str, quoted: tuple[bool, ...]) -> tuple[tuple[int, int], ...]:
    """Split only at structural separators whose following text has a request head."""

    # One index per turn answers, for every separator at once, where each candidate clause
    # ends and whether it could carry a prohibition, a recall word or a head-gated cue; the
    # per-separator classification then never slices or rescans the remaining text.
    index = _ClauseScanIndex(text)
    boundaries: list[tuple[int, int]] = []
    for at, char in enumerate(text):
        if quoted[at] or char not in ",;\n.!?":
            continue
        next_start = at + 1
        while next_start < len(text) and text[next_start].isspace():
            next_start += 1
        if char == "," and _REQUEST_CONNECTOR_RE.match(text, next_start):
            # The connector already owns this boundary; retain its preceding
            # punctuation in the source span as before.
            continue
        if next_start >= len(text) or not _starts_request(text, next_start, index):
            continue
        previous_end = at + 1 if char in ".!?" else at
        boundaries.append((previous_end, next_start))
    for match in _REQUEST_CONNECTOR_RE.finditer(text):
        if quoted[match.start()]:
            continue
        next_start = match.end()
        if next_start < len(text) and _starts_request(text, next_start, index):
            boundaries.append((match.start(), next_start))

    spans: list[tuple[int, int]] = []
    start = 0
    for previous_end, next_start in sorted(boundaries):
        if previous_end < start:
            continue
        clean_start, clean_end = _trim_span(text, start, previous_end)
        if clean_start < clean_end:
            spans.append((clean_start, clean_end))
        start = next_start
    clean_start, clean_end = _trim_span(text, start, len(text))
    if clean_start < clean_end:
        spans.append((clean_start, clean_end))
    return tuple(spans)


def parse_turn_ir(
    user_text: str,
    *,
    response_shape_parser: ResponseShapeParser | object | None = _DEFAULT_SHAPE_PARSER,
) -> TurnIR:
    """Parse one turn without changing its words or claiming planner-level semantics.

    Passing ``response_shape_parser=None`` is the recursion-safe structural-only path used by the
    response-constraint module itself.  Normal callers receive clause-local shapes automatically.
    """

    source = str(user_text or "")
    if response_shape_parser is _DEFAULT_SHAPE_PARSER:
        from core.response_constraints import parse_clause_response_constraint

        shape_parser: ResponseShapeParser | None = parse_clause_response_constraint
    else:
        shape_parser = response_shape_parser  # type: ignore[assignment]

    if not source.strip():
        return TurnIR(source_text=source, clauses=())

    from core.structured_batch import parse_structured_batch

    structured_batch = parse_structured_batch(source)
    quoted = _quoted_positions(source)
    markers = _structural_markers(source, quoted)
    raw_clauses: list[tuple[int, int, int, int, str | None]] = []
    preamble = ""
    preamble_start: int | None = None
    preamble_end: int | None = None

    if structured_batch is not None:
        first_item = structured_batch.items[0]
        clean_pre_start, clean_pre_end = _trim_span(source, 0, first_item.start)
        if clean_pre_start < clean_pre_end:
            preamble = source[clean_pre_start:clean_pre_end]
            preamble_start, preamble_end = clean_pre_start, clean_pre_end
        raw_clauses.extend(
            (
                item.start,
                item.end,
                item.request_start,
                item.request_end,
                item.output_label,
            )
            for item in structured_batch.items
        )
    elif markers:
        first_marker = markers[0]
        clean_pre_start, clean_pre_end = _trim_span(source, 0, first_marker.start)
        if clean_pre_start < clean_pre_end:
            preamble = source[clean_pre_start:clean_pre_end]
            preamble_start, preamble_end = clean_pre_start, clean_pre_end
        for index, marker in enumerate(markers):
            next_start = markers[index + 1].start if index + 1 < len(markers) else len(source)
            start, end = _trim_span(source, marker.start, next_start)
            request_start, request_end = _trim_span(source, marker.content_start, next_start)
            if request_start < request_end:
                raw_clauses.append((start, end, request_start, request_end, marker.label))
    else:
        for start, end in _unmarked_spans(source, quoted):
            raw_clauses.append((start, end, start, end, None))

    clauses: list[TurnClause] = []
    for ordinal, (start, end, request_start, request_end, label) in enumerate(raw_clauses, start=1):
        request_text = source[request_start:request_end]
        response_shape = shape_parser(request_text) if shape_parser is not None else None
        if structured_batch is not None and structured_batch.per_item_exact_words is not None:
            response_shape = ResponseConstraint(
                exact_words=structured_batch.per_item_exact_words,
                max_words=structured_batch.per_item_exact_words,
            )
        clauses.append(
            TurnClause(
                clause_id=f"clause-{ordinal}",
                ordinal=ordinal,
                label=label,
                start=start,
                end=end,
                request_start=request_start,
                request_end=request_end,
                original_text=source[start:end],
                request_text=request_text,
                kind=classify_clause_kind(request_text),
                response_shape=response_shape,
            )
        )

    global_shape = None
    if structured_batch is not None:
        global_shape = ResponseConstraint(
            list_items=len(structured_batch.labels),
            one_item_per_line=True,
        )
    elif shape_parser is not None:
        if preamble:
            global_shape = shape_parser(preamble)
        elif len(clauses) == 1:
            global_shape = clauses[0].response_shape

    return TurnIR(
        source_text=source,
        clauses=tuple(clauses),
        preamble=preamble,
        preamble_start=preamble_start,
        preamble_end=preamble_end,
        global_response_shape=global_shape,
    )


__all__ = [
    "ClauseKind",
    "ResponseConstraint",
    "TurnClause",
    "TurnIR",
    "classify_clause_kind",
    "parse_turn_ir",
]
