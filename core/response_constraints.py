"""Structural response-shape constraints derived from the current user turn."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from core.incomplete_answer import (
    inspect_answer_completeness,
    items_share_a_line,
    list_items,
)
from core.turn_ir import ResponseConstraint, parse_turn_ir

# A colon BETWEEN DIGITS belongs to the value, exactly as a hyphen does in a date: "06:49" is
# one word by any reading a user would give it, and "2026-08-15" already counted as one.
# Measured: without this, no clock value can satisfy "answer in exactly one word" -- the
# runtime declines a question it can answer correctly, and a weaker lane replies "15".
_WORD_RE = re.compile(r"\b[\w'-]+(?::\d+[\w'-]*)*\b", re.UNICODE)
_SENTENCE_RE = re.compile(r"[^.!?]+(?:[.!?]+|$)", re.MULTILINE)
# An enumeration marker ends in `.` and so satisfies `_SENTENCE_RE` on its own. Counting it as a
# sentence is what produced the v0.5.0 smoke failure (QA-050-016/017/021): a three-part answer under
# a one-sentence constraint was trimmed at the first sentence boundary and shipped as the literal
# string `1.`, which then passed every remaining check because it is one word and one "sentence".
_ENUMERATION_MARKER_ONLY_RE = re.compile(
    r"^\s*(?:\(?\d{1,3}[.)]|\(?[A-Za-z][.)]|\(?[ivxIVX]{1,4}[.)]|[-*•‣▪▫◦])\s*$",
)
# These words are strong signals that a short answer was cut off before its
# complement. They are deliberately narrower than a general grammar check.
_DANGLING_CONNECTORS = frozenset(
    {
        "and", "or", "but", "because", "if", "so", "than", "as", "of",
        "for", "with", "from", "into", "onto", "to", "in", "on", "at", "by",
    }
)
_DANGLING_DETERMINERS = frozenset(
    {"a", "an", "the", "each", "every", "either", "neither", "another"}
)
_NON_ANSWER_PREFIX_RE = re.compile(
    r"^\s*(?:"
    r"i\s+(?:cannot|can['’]t|could\s+not|couldn['’]t|am\s+unable)"
    r"|unable\s+to"
    r")\b",
    re.IGNORECASE,
)
_OPEN_QUESTION_RE = re.compile(
    r"\b(?:how|why|what|which|where|when|describe|explain)\b",
    re.IGNORECASE,
)
_YES_NO_QUESTION_RE = re.compile(
    r"^\s*(?:is|are|am|was|were|do|does|did|can|could|will|would|should|"
    r"has|have|had|may|might|must)\b",
    re.IGNORECASE,
)
_LOW_INFORMATION_SHORT_ANSWERS = frozenset(
    {
        "different",
        "same",
        "various",
        "varied",
        "something",
        "anything",
        "whatever",
        "unknown",
        "unclear",
    }
)
_TWO_WORD_COORDINATE_RE = re.compile(
    r"^\s*(?P<first>[\w'-]+)\s+(?:and|or)\s+(?P<second>[\w'-]+)[.!?]?\s*$",
    re.IGNORECASE,
)
_SPELLED_WORD_COUNTS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
    "eleven": 11,
    "twelve": 12,
    "thirteen": 13,
    "fourteen": 14,
    "fifteen": 15,
    "sixteen": 16,
    "seventeen": 17,
    "eighteen": 18,
    "nineteen": 19,
    "twenty": 20,
}
_SHORT_WORD_SHAPE_RE = re.compile(
    r"(?:"
    r"\b(?:answer|respond|reply|say|return|give|use|write|end)"
    r"(?:\s+\w+){0,5}?\s+(?:in|with|using)?\s*"
    r"(?:exactly\s+)?(?P<verb_count>one|two|three|four|1|2|3|4|a single|single)"
    r"[ -]?words?\b"
    r"|"
    r"\b(?:exactly\s+)?(?P<noun_count>one|two|three|four|1|2|3|4|a single|single)"
    r"[ -]?word(?:s)?\s*(?::|(?:answer|response|reply|mood|description)\b)"
    r"|"
    r"^\s*(?P<standalone_count>one|two|three|four|1|2|3|4|a single|single)"
    r"[ -]?word(?:s)?[.!]?\s*$"
    r")",
    re.IGNORECASE,
)
# "a five-word title", "a 5 word headline" -- the attributive singular. `_SHORT_WORD_SHAPE_RE`
# above stops at four and only in front of a fixed noun list (answer/response/reply/mood/
# description), so the v0.5.0 smoke run's five-word title (QA-050-020) parsed to NO constraint at
# all: nothing validated the length, nothing repaired it, and a four-word title was accepted.
# Attributive `N-word <noun>` is a length specification whatever the noun is, so no list is kept --
# the singular `word` before another noun carries the whole signal, and the plural form ("five
# words") belongs to the exact-count rules below.
_ATTRIBUTIVE_WORD_COUNT_RE = re.compile(
    r"\b(?P<count>\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)"
    r"[\s-]word\b(?!s)\s+(?P<subject>[a-z][\w-]{2,})",
    re.IGNORECASE,
)
_EXACT_WORDS_RE = re.compile(
    r"\b(?:in|using|with)?\s*exactly\s+(?P<count>\d{1,3})\s+words?\b",
    re.IGNORECASE,
)
_SPELLED_EXACT_WORDS_RE = re.compile(
    r"\b(?:in|using|with)?\s*exactly\s+"
    r"(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|"
    r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
    r"eighteen|nineteen|twenty)\s+words?\b",
    re.IGNORECASE,
)
_FOLLOWUP_EXACT_WORDS_RE = re.compile(
    r"^\s*(?:(?:can|could|would)\s+you\s+)?"
    r"(?:make|put|say|give|turn|rewrite|condense|shorten|summarize)\s+"
    r"(?:that|this|it)\s+(?:in|using|with)\s+exactly\s+"
    r"(?P<count>one|two|three|four|1|2|3|4|a single|single)\s+words?\b",
    re.IGNORECASE,
)
_MAX_WORDS_RE = re.compile(
    r"(?:"
    r"\b(?:at most|no more than|under)\s+(?P<lead>\d{1,3})\s+words?\b"
    r"|"
    r"\b(?P<trail>\d{1,3})\s+words?\s+or\s+(?:fewer|less)\b"
    r"|"
    r"\b(?:in|using|with)\s+(?P<in_count>\d{1,3})\s+words?\s+max(?:imum)?\b"
    r")",
    re.IGNORECASE,
)
# A sentence budget stated as a budget, whatever verb introduced it: "in one sentence", "with two
# sentences", "using exactly three sentences". This replaces the closed verb list `_SHORT_SENTENCE_
# SHAPE_RE` used to gate on -- answer|respond|reply|say|return|give|use|write|end|confirm|explain --
# which is why "Answer in one sentence: ..." carried a shape contract and the identical "Tell me in
# one sentence: ..." carried none. The trigger verb was the whole authority, and "tell" was simply
# missing from the list. The preposition is the signal instead: a budget is a budget however the
# sentence opens.
_SENTENCE_BUDGET_RE = re.compile(
    r"\b(?:in|with|using|to)\s+(?:exactly\s+|just\s+|only\s+)?"
    r"(?P<count>one|two|three|four|five|1|2|3|4|5|a\s+single|single)"
    r"(?:\s+(?:short|brief|concise|complete))?\s+sentences?\b",
    re.IGNORECASE,
)
_EXACT_SENTENCES_RE = re.compile(
    r"\b(?:in|using|with)?\s*exactly\s+(?P<count>\d{1,2})\s+sentences?\b",
    re.IGNORECASE,
)
_MAX_SENTENCES_RE = re.compile(
    r"(?:"
    r"\b(?:at most|no more than)\s+(?P<lead>\d{1,2})\s+sentences?\b"
    r"|"
    r"\b(?P<trail>\d{1,2})\s+sentences?\s+or\s+(?:fewer|less)\b"
    r")",
    re.IGNORECASE,
)
_SHORT_SENTENCE_SHAPE_RE = re.compile(
    r"\b(?:answer|respond|reply|say|return|give|use|write|end|confirm|explain)"
    # Identifiers such as ``MARIGOLD-8342`` are ordinary user text between the
    # verb and the shape request. Treat them as one token so they cannot make a
    # sentence constraint disappear before the router validates the response.
    r"(?:\s+[\w-]+){0,7}?\s+(?:in|with|using)?\s*"
    r"(?:exactly\s+)?(?P<count>one|two|three|four|1|2|3|4|a single|single)"
    r"(?:\s+short)?\s+sentences?\b",
    re.IGNORECASE,
)


# An explicit STRUCTURAL LAYOUT: the request does not merely want brevity, it wants a shape on the
# page. "as a numbered list", "one per line", "each item on its own line", "in bullet points".
_LAYOUT_SHAPE_RE = re.compile(
    r"\b(?:numbered|bulleted|bullet[- ]point(?:ed)?)\s+(?:list|items?|points?)\b"
    r"|\bas\s+(?:a\s+)?(?:numbered|bulleted|bullet(?:ed)?)?\s*list\b"
    r"|\bin\s+bullet\s+points?\b"
    r"|\b(?:one|each)\s+(?:item|entry|point|line|colour|color|word|thing)?\s*"
    r"(?:per|on\s+(?:its|a|their)\s+own|on\s+(?:a\s+)?separate)\s+line\b"
    r"|\bone\s+per\s+line\b"
    r"|\beach\s+on\s+(?:its|a)\s+own\s+line\b",
    re.IGNORECASE,
)
# The layout forms that specifically demand one item per line, as opposed to a list in any shape.
_ONE_PER_LINE_RE = re.compile(
    r"\b(?:one|each)\s+(?:item|entry|point|colour|color|word|thing)?\s*"
    r"(?:per|on\s+(?:its|a|their)\s+own|on\s+(?:a\s+)?separate)\s+line\b"
    r"|\bone\s+per\s+line\b"
    r"|\beach\s+on\s+(?:its|a)\s+own\s+line\b",
    re.IGNORECASE,
)
# How many items the list should hold, when the request quantifies them. Bounded to 1-20 and to a
# one- or two-digit numeral, so a year ("the 2019 results as a bullet list") can never be a count.
_LIST_COUNT_RE = re.compile(
    r"\b(?:exactly\s+|the\s+|these\s+)?"
    r"(?P<count>one|two|three|four|five|six|seven|eight|nine|ten|\d{1,2})\s+"
    r"(?:[a-z][a-z-]{2,}\s+){0,2}?"
    r"(?:items?|points?|entries|lines?|steps?|reasons?|examples?|options?|colours?|colors?|"
    r"names?|words?|things?|bullets?)\b",
    re.IGNORECASE,
)

# ---------------------------------------------------------------------------
# Explicit presentation formats (C19). An explicit request wins over the
# answer's default presentation; the closed vocabulary and the anchors are
# deliberately narrow so a topic noun ("org chart", "graph database", "tree of
# life") never binds a shape to the reply.
# ---------------------------------------------------------------------------
#: The closed set of presentation formats a turn may explicitly request.
PRESENTATION_FORMATS: tuple[str, ...] = ("table", "timeline", "tree", "mermaid", "chart")

#: Who authored a response contract. Absent/None = the explicit parser read it from the
#: user's own turn; "automatic" = the C19 presentation selector derived it from the
#: answer's own shape. The value rides the contract so the repair instruction can stay
#: honest about who asked for the shape (an automatic repair never says "requested").
CONSTRAINT_ORIGINS: tuple[str, ...] = ("automatic",)

#: Chat rendering capabilities, not evidence that an individual request was fulfilled.
#: Chart admission requires a valid bounded payload; textual fallbacks are not chart passes.
SURFACE_RENDERS_PRESENTATION: dict[str, bool] = {
    "table": True,
    "timeline": True,  # rendered as a dated, ordered text structure
    "tree": True,  # rendered as an indented text hierarchy
    "mermaid": True,  # shipped local renderer, isolated from the native bridge
    "chart": True,  # bounded chart JSON rendered by the shipped local library
    # C19 automatic-selection vocabulary. The automatic selector (core.presentation_selection)
    # reads this same map: no election may target a format whose value is False, and the
    # explicit-request-only formats stay excluded from election even if their flag flips.
    "comparison_matrix": True,  # a table shape: subjects along shared attribute columns
    "numbered_steps": True,  # an ordered text list
    "bullets": True,  # an unordered text list
    "prose": True,
    "pie_chart": False,  # no renderer; honest form is a values table (explicit lanes only)
}

_PRESENTATION_TABLE_RE = re.compile(
    r"\b(?:as|in|into|to|use|produce|create|show|present|render)\s+(?:an?\s+|one\s+)?"
    r"(?:(?:compact|concise|small|simple|readable|detailed|comparison|markdown|pipe)\s+){0,3}table\b"
    r"|\b(?:markdown|pipe)\s+table\b"
    r"|\btable\s+format\b"
    r"|\btabular\s+form\b",
    re.IGNORECASE,
)
_PRESENTATION_TIMELINE_RE = re.compile(
    r"\b(?:as|in|into)\s+(?:an?\s+)?timeline\b"
    r"|\b(?:make|build|give|draw|create|show|present|format|render)\s+(?:me\s+)?(?:an?\s+)?timeline\b"
    r"|\btimeline\s+(?:view|format|layout)\b",
    re.IGNORECASE,
)
_PRESENTATION_TREE_RE = re.compile(
    r"\b(?:as|in|into)\s+(?:a\s+)?tree\b"
    r"|\bas\s+(?:a\s+)?tree\s+(?:structure|view|diagram|hierarchy)\b"
    r"|\btree\s+(?:view|format|layout|diagram|structure)\b"
    r"|\bhierarchy\s+(?:view|tree|layout)\b",
    re.IGNORECASE,
)
_PRESENTATION_MERMAID_RE = re.compile(r"\bmermaid\b", re.IGNORECASE)
_PRESENTATION_CHART_RE = re.compile(
    r"\b(?:as|in|into)\s+(?:an?\s+)?(?:chart|graph|plot)\b"
    r"|\b(?:bar|line|pie|column)\s+chart\b"
    r"|\bchart\s+(?:view|format|form)\b"
    r"|\bplot\s+the\s+(?:data|values|numbers)\b",
    re.IGNORECASE,
)
_PRESENTATION_SHAPE_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("table", _PRESENTATION_TABLE_RE),
    ("timeline", _PRESENTATION_TIMELINE_RE),
    ("tree", _PRESENTATION_TREE_RE),
    ("mermaid", _PRESENTATION_MERMAID_RE),
    ("chart", _PRESENTATION_CHART_RE),
)

# Structural acceptance for a delivered answer, per format. Lenient by design:
# these detect the requested SHAPE, never its quality, and never mutate text.
_MARKDOWN_TABLE_RE = re.compile(
    r"^\s*\|[^\n]+\|\s*\n\s*\|[\s:|-]+\|(?:\s*\n\s*\|[^\n]+\|)*", re.MULTILINE
)
_DATE_LINE_RE = re.compile(
    r"^\s*(?:[-*•]|\d+[.)])?\s*(?:\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4}|\d{3,4}s?|"
    r"(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2})",
    re.IGNORECASE,
)
_INDENTED_LINE_RE = re.compile(r"^\s{2,}\S")
_BOX_DRAWING_RE = re.compile(r"[├└│┌┐┘┤┬┴┼]")
_NUMERIC_TOKEN_RE = re.compile(r"(?<![\w.])(?:\d+\.\d+|\d{1,3}(?:,\d{3})+|\d+)(?![\w.])")


def presentation_shape_spans(text: str) -> list[tuple[int, int]]:
    """Every span of `text` that names a presentation shape, from the parser's OWN matchers.

    One vocabulary: the same regexes `_parse_response_constraint` reads a format request with.
    Exposed so a caller can ask "what is left of this turn once its shape language is removed"
    without re-stating the format words anywhere else.
    """
    value = str(text or "")
    spans: list[tuple[int, int]] = []
    for _name, pattern in _PRESENTATION_SHAPE_RES:
        for match in pattern.finditer(value):
            spans.append((match.start(), match.end()))
    return sorted(set(spans))


def residual_words_outside_shape(text: str) -> list[str]:
    """The words of `text` that are neither shape language nor shape-instruction filler.

    "present the comparison as a table" -> ["present", "comparison"]; "explain photosynthesis in
    a table" -> ["explain", "photosynthesis"]. What the residual REFERS to is the caller's question
    (a subject of its own, or the previous answer); this only says what is there.
    """
    value = str(text or "")
    remainder = value
    for start, end in sorted(presentation_shape_spans(value), reverse=True):
        remainder = remainder[:start] + " " + remainder[end:]
    return [
        word
        for word in _WORD_RE.findall(remainder.lower())
        if word not in _SHAPE_INSTRUCTION_FILLER
    ]


def _presentation_answer_has_format(text: str, presentation_format: str) -> bool:
    """Whether the delivered answer carries the requested presentation shape.

    Chart and Mermaid require closed top-level blocks under the canonical label
    (``core.presentation.fences``), after the same value-preserving fence canonicalisation `enforce_response_constraint` ships. Mermaid also requires
    a supported declaration; only its sandbox parser can establish full syntax/render success.
    Measured on a651de73:
    a chart payload that satisfied the grammar byte for byte was rejected twice per turn for
    carrying a ```json label, and a ```mermaid block nested inside a bare wrapper fence passed
    the old keyword search while the page would have shown it as code.
    """
    cleaned = str(text or "")
    if presentation_format == "table":
        return bool(_MARKDOWN_TABLE_RE.search(cleaned))
    if presentation_format == "mermaid":
        from core.presentation.fences import (
            MERMAID_FENCE_TAG,
            fenced_blocks,
            mermaid_body_has_declaration,
            normalize_presentation_fences,
        )

        canonical = normalize_presentation_fences(cleaned, requested_formats=("mermaid",)).text
        blocks = [block for block in fenced_blocks(canonical) if block.tag == MERMAID_FENCE_TAG]
        return bool(blocks) and all(
            block.terminated and mermaid_body_has_declaration(block.body) for block in blocks
        )
    if presentation_format == "timeline":
        lines = [line for line in cleaned.splitlines() if line.strip()]
        dated = sum(1 for line in lines if _DATE_LINE_RE.match(line))
        return dated >= 2
    if presentation_format == "tree":
        if _BOX_DRAWING_RE.search(cleaned):
            return True
        lines = [line for line in cleaned.splitlines() if line.strip()]
        return sum(1 for line in lines if _INDENTED_LINE_RE.match(line)) >= 2
    if presentation_format == "chart":
        from core.presentation.chart_payload import chart_bodies, parse_chart_body
        from core.presentation.fences import normalize_presentation_fences

        canonical = normalize_presentation_fences(cleaned, requested_formats=("chart",)).text
        bodies = chart_bodies(canonical)
        if not bodies:
            return False
        try:
            for body in bodies:
                parse_chart_body(body)
            return True
        except (ValueError, TypeError, OverflowError):
            return False
    return False


# Openers that sit in front of the real head of a clause without being it.
_CLAUSE_LEAD_FILLER_RE = re.compile(
    r"^(?:please\s+|pls\s+|now\s+|next\s+|first\s+|second\s+|third\s+|finally\s+|lastly\s+"
    r"|(?:can|could|would|will)\s+you\s+(?:please\s+)?|i\s+(?:want|need)\s+you\s+to\s+"
    r"|\d{1,2}[.)]\s*|[-*•]\s*)+",
    re.IGNORECASE,
)
# Verbs that make a clause a request of its own. Deliberately a closed list of ordinary task verbs:
# a clause is only counted when it OPENS with one, so ordinary prose that merely contains the word
# never turns a sentence into a second request.
_REQUEST_VERBS = frozenset(
    {
        "add", "analyse", "analyze", "answer", "build", "calculate", "check", "choose", "compare",
        "compute", "confirm", "convert", "count", "create", "define", "delete", "describe",
        "do", "draft", "explain", "find", "fix", "generate", "give", "identify", "list", "look",
        "make", "name", "open", "pick", "plan", "print", "read", "recommend", "remove", "rename",
        "reply", "respond", "return", "review", "rewrite", "run", "say", "search", "send", "show",
        "sort", "suggest", "summarise", "summarize", "tell", "translate", "use", "write",
    }
)
_QUESTION_OPENERS = frozenset(
    {
        "what", "why", "how", "when", "where", "which", "who", "whose", "whom",
        "is", "are", "was", "were", "do", "does", "did", "can", "could", "will",
        "would", "should", "has", "have", "had", "may", "might", "must",
    }
)
# What may remain in a clause once its shape language is removed and still leave it a BARE shape
# instruction: the instruction verb, the thing it refers to, and grammatical filler.
_SHAPE_INSTRUCTION_FILLER = frozenset(
    {
        "answer", "respond", "reply", "say", "return", "give", "keep", "use", "put", "make",
        "write", "word", "words", "sentence", "sentences", "response", "responses", "text",
        "please", "pls", "just", "only", "me", "it", "that", "this", "your", "the", "a", "an",
        "in", "with", "using", "to", "as", "of", "at", "no", "more", "than", "most", "under",
        "exactly", "single", "short", "brief", "concise", "maximum", "max", "fewer", "less",
        "or", "and", "can", "could", "would", "you", "i", "want", "need", "let", "s",
        # Presentation verbs: the instruction half of "present/show/format X as a table". They
        # carry no subject of their own, exactly like "give"/"put"/"write" above.
        "present", "show", "display", "render", "format", "reformat", "formatted", "convert",
        "turn", "express", "lay", "out", "again", "instead", "rather",
    }
)


@dataclass(frozen=True)
class ConstraintCheck:
    compliant: bool
    word_count: int
    sentence_count: int
    violations: tuple[str, ...]
    #: The requested presentation formats the text does not carry, in request order.  Named so a
    #: repair can say what was missing instead of restating every format the turn asked for.
    missing_formats: tuple[str, ...] = ()


@dataclass(frozen=True)
class ConstraintApplication:
    text: str
    compliant: bool
    structurally_trimmed: bool
    violations: tuple[str, ...]
    #: Whether a fence label was canonicalised or a wrapper fence unwrapped (bytes inside every
    #: block untouched) so the shipped text carries the labels the renderers read.
    fences_normalized: bool = False


@dataclass(frozen=True)
class _SentenceSpan:
    start: int
    end: int
    text: str


def _sentence_spans(text: str) -> list[_SentenceSpan]:
    """Sentences, with an enumeration marker attached to the item it introduces.

    `1. Paris is the capital.` is one sentence, not two. A marker with nothing after it is not a
    sentence at all -- it is the start of an item that was never written -- so it contributes
    nothing to the count and cannot become a trim boundary.
    """

    spans: list[_SentenceSpan] = []
    pending_start: int | None = None
    body = str(text or "").strip()
    for match in _SENTENCE_RE.finditer(body):
        fragment = match.group(0)
        if not fragment.strip():
            continue
        start = match.start() if pending_start is None else pending_start
        if _ENUMERATION_MARKER_ONLY_RE.match(fragment):
            pending_start = start
            continue
        spans.append(_SentenceSpan(start=start, end=match.end(), text=body[start:match.end()]))
        pending_start = None
    return spans


def _has_incomplete_short_tail(
    constraint: ResponseConstraint,
    words: list[re.Match[str]],
) -> bool:
    """Detect high-confidence unfinished tails in constrained replies.

    This is intentionally not a grammar engine. It never judges an
    unconstrained reply or a one-word answer, so valid answers such as
    ``Ready`` and complete fragments such as ``New York`` remain valid.
    """
    if not words or (constraint.exact_words or constraint.max_words) is None:
        return False
    if len(words) <= 1:
        return False
    final_word = words[-1].group(0).lower().strip("'-")
    return (
        final_word in _DANGLING_CONNECTORS
        or final_word in _DANGLING_DETERMINERS
    )


def _looks_like_non_answer(text: str, words: list[re.Match[str]]) -> bool:
    """Reject a clipped apology that happens to meet a short word count."""
    if len(words) > 3:
        return False
    return bool(_NON_ANSWER_PREFIX_RE.match(str(text or "").strip()))


def _bounded_count(raw: str | None, *, maximum: int) -> int | None:
    if not raw:
        return None
    value = int(raw)
    if value < 1 or value > maximum:
        return None
    return value


def _short_word_count(raw: str | None) -> int | None:
    clean = " ".join(str(raw or "").lower().split())
    if clean in {"a single", "single"}:
        return 1
    if clean in _SPELLED_WORD_COUNTS:
        return _SPELLED_WORD_COUNTS[clean]
    return _bounded_count(clean, maximum=4)


def _spelled_or_digit_count(raw: str | None, *, maximum: int) -> int | None:
    """A count written either way -- "five" or "5" -- bounded to `maximum`."""
    clean = " ".join(str(raw or "").lower().split())
    if clean in _SPELLED_WORD_COUNTS:
        value = _SPELLED_WORD_COUNTS[clean]
        return value if 1 <= value <= maximum else None
    return _bounded_count(clean, maximum=maximum)


def _clauses(text: str) -> list[tuple[int, int, str]]:
    """Request clauses as exact source spans over the normalized string.

    Response constraints no longer maintain a second boundary grammar.  The structural-only mode
    avoids recursively asking each clause to parse response shapes while the whole-turn parser is
    deciding whether one of those shapes may be global.
    """

    turn = parse_turn_ir(text, response_shape_parser=None)
    return [
        (clause.request_start, clause.request_end, clause.request_text)
        for clause in turn.clauses
    ]


def _is_request_clause(clause: str) -> bool:
    """Whether this clause asks for something of its own."""
    body = _CLAUSE_LEAD_FILLER_RE.sub("", clause.strip().lower()).strip()
    if not body:
        return False
    head = _WORD_RE.match(body)
    if head is None:
        return False
    first = head.group(0)
    return first in _REQUEST_VERBS or first in _QUESTION_OPENERS


def _clause_at(text: str, position: int) -> bool:
    """Whether the clause containing `position` is itself asking for something."""
    for start, end, body in _clauses(text):
        if start <= position < end:
            return _is_request_clause(body)
    return False


def _carries_only_shape(clause: str, shape_spans: list[tuple[int, int]], offset: int) -> bool:
    """Whether a clause is a bare shape instruction rather than a request wearing one.

    "Answer in one word" is a bare instruction: strip the shape language and nothing is left but the
    verb and its filler, so it governs whatever the rest of the turn asks for. "Explain
    photosynthesis in one sentence" is a request that happens to carry a shape, and what it governs
    is itself.
    """
    remainder = clause
    for start, end in sorted(shape_spans, reverse=True):
        local_start, local_end = start - offset, end - offset
        if local_start < 0 or local_end > len(clause):
            continue
        remainder = remainder[:local_start] + " " + remainder[local_end:]
    residual = [
        word
        for word in _WORD_RE.findall(remainder.lower())
        if word not in _SHAPE_INSTRUCTION_FILLER
    ]
    return not residual


def _constraint_governs_whole_turn(text: str, shape_spans: list[tuple[int, int]]) -> bool:
    """Whether a parsed shape constraint may bound the WHOLE answer to this turn.

    The v0.5.0 smoke run (QA-050-016) asked for three unrelated things and attached "in one
    sentence" to the first of them. The runtime adopted that as the shape of the entire reply, and
    the enforcement that followed deleted the other two answers. A length attached to one request is
    a fact about that request; only an instruction addressed to the reply itself is a fact about the
    reply.

    So: a bare shape instruction always governs -- that is what the user wrote it for -- and a shape
    riding on a specific request governs only when that request is the whole turn. When it is not,
    no constraint is returned at all: the user's own words still reach the model, which is free to
    honour them for the clause they belong to, and the runtime simply stops enforcing a bound it
    cannot correctly scope.
    """
    if not shape_spans:
        return True
    clauses = _clauses(text)
    if len(clauses) <= 1:
        return True
    # Judge each shape at its own host. Combining the earliest and latest spans into one range made
    # two local constraints in different clauses look like one boundary-straddling global rule.
    for shape_span in shape_spans:
        shape_start, shape_end = shape_span
        hosts = [
            (start, end, body)
            for start, end, body in clauses
            if start <= shape_start and end >= shape_end
        ]
        if not hosts:
            # Shape language before the first explicit item is a whole-response preamble, e.g.
            # "Use exactly twenty words overall: A) ... B) ...".
            continue
        host_start, _, host_body = hosts[0]
        if _carries_only_shape(host_body, [shape_span], host_start):
            continue
        other_requests = sum(
            1
            for start, _, body in clauses
            if start != host_start and _is_request_clause(body)
        )
        if other_requests:
            return False
    return True


def _parse_response_constraint(
    user_text: str,
    *,
    require_global_scope: bool,
) -> ResponseConstraint | None:
    """Parse explicit response-shape language with optional whole-turn scope enforcement."""
    # Preserve request boundaries while normalizing whitespace.  Collapsing newlines here erased
    # numbered-list structure before `_constraint_governs_whole_turn` could scope a trailing
    # "5-word title" to its own item.  Periods are boundaries too, so same-line multi-part Q&A is
    # protected by the same rule.
    text = re.sub(r"[ \t\r\f\v]+", " ", str(user_text or "")).strip()
    if not text:
        return None
    short_shape_match = _SHORT_WORD_SHAPE_RE.search(text)
    exact_words = (
        _short_word_count(
            next(
                (
                    value
                    for value in short_shape_match.groupdict().values()
                    if value
                ),
                None,
            )
        )
        if short_shape_match
        else None
    )
    attributive_match = _ATTRIBUTIVE_WORD_COUNT_RE.search(text)
    if attributive_match and not _clause_at(text, attributive_match.start()):
        # "a five-word title is short" is a sentence ABOUT a length, not a request for one. The
        # other shape rules carry their own request verb or an explicit noun; this one is general
        # enough to need the clause it sits in to actually be asking for something.
        attributive_match = None
    if attributive_match:
        attributive_words = _spelled_or_digit_count(
            attributive_match.group("count"),
            maximum=20,
        )
        if attributive_words is not None:
            exact_words = attributive_words
    exact_match = _EXACT_WORDS_RE.search(text)
    if exact_match:
        exact_words = _bounded_count(exact_match.group("count"), maximum=200)
    spelled_exact_match = _SPELLED_EXACT_WORDS_RE.search(text)
    if spelled_exact_match:
        exact_words = _short_word_count(spelled_exact_match.group("count"))
    followup_exact_words_match = _FOLLOWUP_EXACT_WORDS_RE.search(text)
    if followup_exact_words_match:
        exact_words = _short_word_count(
            followup_exact_words_match.group("count")
        )
    max_words_match = _MAX_WORDS_RE.search(text)
    max_words = (
        _bounded_count(
            next(
                (
                    value
                    for value in max_words_match.groupdict().values()
                    if value
                ),
                None,
            ),
            maximum=200,
        )
        if max_words_match
        else None
    )
    exact_sentences_match = _EXACT_SENTENCES_RE.search(text)
    exact_sentences = (
        _bounded_count(
            exact_sentences_match.group("count"),
            maximum=20,
        )
        if exact_sentences_match
        else None
    )
    short_sentence_match = _SHORT_SENTENCE_SHAPE_RE.search(text)
    if short_sentence_match and exact_sentences is None:
        exact_sentences = _short_word_count(short_sentence_match.group("count"))
    # The de-lexicalised rule. It runs after the verb-anchored one and only fills a gap it left, so
    # every phrasing the old rule caught still parses identically -- what changes is that the
    # phrasings it missed for want of a verb ("Tell me in one sentence ...") now parse too.
    sentence_budget_match = _SENTENCE_BUDGET_RE.search(text)
    if sentence_budget_match and exact_sentences is None:
        exact_sentences = _short_word_count(sentence_budget_match.group("count"))
    max_sentences_match = _MAX_SENTENCES_RE.search(text)
    max_sentences = (
        _bounded_count(
            next(
                (
                    value
                    for value in max_sentences_match.groupdict().values()
                    if value
                ),
                None,
            ),
            maximum=20,
        )
        if max_sentences_match
        else None
    )
    layout_match = _LAYOUT_SHAPE_RE.search(text)
    list_items = None
    one_item_per_line = False
    if layout_match is not None:
        one_item_per_line = bool(_ONE_PER_LINE_RE.search(text))
        list_items = _requested_item_count(text)
    presentation_format = None
    presentation_match = None
    presentation_formats = []
    for format_name, shape_re in _PRESENTATION_SHAPE_RES:
        match = shape_re.search(text)
        if match is not None:
            presentation_formats.append(format_name)
            if presentation_match is None:
                presentation_match = match
                presentation_format = format_name
    if all(
        value is None
        for value in (
            exact_words,
            max_words,
            exact_sentences,
            max_sentences,
            list_items,
        )
    ) and not one_item_per_line and presentation_format is None:
        return None
    shape_spans = [
        (
            # Keep the noun being shaped ("title" in "Give a 5-word title") in the host clause
            # when deciding whether this is a bare whole-reply instruction. Removing the entire
            # attributive match made "Give 5-word title" look like bare filler and incorrectly
            # applied five words to every answer in a three-part turn.
            (match.start(), match.start("subject"))
            if match is attributive_match
            else match.span()
        )
        for match in (
            short_shape_match,
            attributive_match,
            exact_match,
            spelled_exact_match,
            followup_exact_words_match,
            max_words_match,
            exact_sentences_match,
            short_sentence_match,
            sentence_budget_match,
            max_sentences_match,
            layout_match,
            presentation_match,
        )
        if match is not None
    ]
    if require_global_scope and not _constraint_governs_whole_turn(text, shape_spans):
        if not presentation_formats:
            return None
        # Formats require inclusion, not a whole-answer length. A clause-local
        # sentence/word limit must neither truncate siblings nor erase their visuals.
        exact_words = max_words = exact_sentences = max_sentences = list_items = None
        one_item_per_line = False
    return _reconcile_shapes(
        exact_words=exact_words,
        max_words=max_words,
        exact_sentences=exact_sentences,
        max_sentences=max_sentences,
        list_items=list_items,
        one_item_per_line=one_item_per_line,
        presentation_format=presentation_format,
        presentation_formats=tuple(presentation_formats) if len(presentation_formats) > 1 else (),
    )


def parse_clause_response_constraint(user_text: str) -> ResponseConstraint | None:
    """Parse the response shape of one already-segmented request clause.

    This is the shape extractor used by :func:`core.turn_ir.parse_turn_ir`.  Callers handling an
    unsplit user turn should use :func:`parse_response_constraint`, whose global-scope guard keeps
    a clause-local limit from truncating sibling answers.
    """

    return _parse_response_constraint(user_text, require_global_scope=False)


def parse_response_constraint(user_text: str) -> ResponseConstraint | None:
    """Parse only response-shape language that governs the whole current turn."""

    # A structured batch can carry a distributive constraint such as "answer each using a single
    # word".  That is one word PER bound row, not one word for the entire reply.  This parser feeds
    # the whole-response validator before ``raw_output_contract`` is attached, so letting the
    # ordinary word matcher see it collapses three valid rows into one word and the stricter batch
    # binder then (correctly) rejects the result.  Preserve only the whole-output layout here; the
    # structured batch contract validates each row's word count at its own boundary.
    from core.structured_batch import parse_structured_batch

    structured = parse_structured_batch(user_text)
    if structured is not None:
        return ResponseConstraint(
            list_items=len(structured.labels),
            one_item_per_line=True,
        )

    return _parse_response_constraint(user_text, require_global_scope=True)


def _requested_item_count(text: str) -> int | None:
    """How many items the request asked the list to hold, or None.

    Counted only OUTSIDE the DISTRIBUTIVE layout phrases. "one per line" puts the numeral "one"
    directly in front of a noun this rule reads, so a naive search takes the layout instruction
    itself as the quantity and pins every such request to a single item -- which then rejects every
    correct multi-item answer to it. The layout says how to lay the items out; it never says how
    many there are.

    The NAMING layout phrases are deliberately not excluded: "exactly three numbered items"
    overlaps `numbered items`, and that is exactly the count this rule exists to read.
    """
    layout_spans = [match.span() for match in _ONE_PER_LINE_RE.finditer(text)]
    for match in _LIST_COUNT_RE.finditer(text):
        start, end = match.span()
        if any(start < spread_end and spread_start < end for spread_start, spread_end in layout_spans):
            continue
        count = _spelled_or_digit_count(match.group("count"), maximum=20)
        if count is not None:
            return count
    return None


def _reconcile_shapes(
    *,
    exact_words: int | None,
    max_words: int | None,
    exact_sentences: int | None,
    max_sentences: int | None,
    list_items: int | None,
    one_item_per_line: bool,
    presentation_format: str | None = None,
    presentation_formats: tuple[str, ...] = (),
) -> ResponseConstraint:
    """Resolve a request that states more than one shape into a single contract.

    "In one sentence, as a numbered list with each item on its own line" states two shapes that
    cannot both bind the whole answer: a three-item list is three sentences by any counting, so
    enforcing the sentence budget across it rejects the very answer the layout asked for. Measured
    live: a correct `1. Red / 2. Blue / 3. Yellow` was refused repeatedly, on a local 7B and on a
    frontier cloud model alike, and the user got "I couldn't produce a complete response within the
    requested format" at 1,200-1,600 tokens a try.

    **The layout wins, and the sentence budget becomes a fact about each item.** That is the reading
    that keeps both halves of the request: the user gets their list, and "one sentence" still means
    something -- keep each item to one sentence -- rather than being silently dropped or destroying
    the answer. It travels as `per_item_sentences`, which reaches the prompt and the repair
    instruction but is never counted across the whole reply, because that arithmetic is the defect.

    A request with no layout is untouched: "exactly one sentence explaining why the sky is blue" is
    a sentence budget and stays one.
    """
    if exact_words is not None:
        max_words = exact_words
    if exact_sentences is not None:
        max_sentences = exact_sentences
    wants_layout = list_items is not None or one_item_per_line
    per_item_sentences = None
    if wants_layout and max_sentences is not None:
        per_item_sentences = exact_sentences or max_sentences
        exact_sentences = None
        max_sentences = None
    return ResponseConstraint(
        exact_words=exact_words,
        max_words=max_words,
        exact_sentences=exact_sentences,
        max_sentences=max_sentences,
        list_items=list_items,
        one_item_per_line=one_item_per_line,
        per_item_sentences=per_item_sentences,
        presentation_format=presentation_format,
        presentation_formats=presentation_formats,
    )


def response_constraint_from_metadata(
    metadata: dict[str, Any] | None,
) -> ResponseConstraint | None:
    payload = dict((metadata or {}).get("response_constraint") or {})
    if not payload:
        return None
    constraint = ResponseConstraint(
        exact_words=_bounded_count(
            str(payload.get("exact_words") or ""),
            maximum=200,
        ),
        max_words=_bounded_count(
            str(payload.get("max_words") or ""),
            maximum=200,
        ),
        exact_sentences=_bounded_count(
            str(payload.get("exact_sentences") or ""),
            maximum=20,
        ),
        max_sentences=_bounded_count(
            str(payload.get("max_sentences") or ""),
            maximum=20,
        ),
        list_items=_bounded_count(
            str(payload.get("list_items") or ""),
            maximum=20,
        ),
        one_item_per_line=bool(payload.get("one_item_per_line")),
        per_item_sentences=_bounded_count(
            str(payload.get("per_item_sentences") or ""),
            maximum=20,
        ),
        presentation_format=(
            str(payload.get("presentation_format"))
            if str(payload.get("presentation_format") or "") in PRESENTATION_FORMATS
            else None
        ),
        origin=(
            str(payload.get("origin"))
            if str(payload.get("origin") or "") in CONSTRAINT_ORIGINS
            else None
        ),
        presentation_formats=tuple(dict.fromkeys(
            value for value in payload.get("presentation_formats", ())
            if value in PRESENTATION_FORMATS
        )) if isinstance(payload.get("presentation_formats"), (list, tuple)) else (),
    )
    # `one_item_per_line` is a bool, so "all values are None" is the wrong emptiness test for it --
    # a layout-only contract (False for every count, True here) would be discarded on the way back
    # from metadata and the turn would lose the shape it was validated against. Same for an
    # explicit presentation format: a format-only contract carries no counts at all.
    if not constraint.one_item_per_line and constraint.presentation_format is None and all(
        value is None for key, value in constraint.to_dict().items() if key not in ("one_item_per_line", "presentation_format")
    ):
        return None
    return constraint


def check_response_constraint(
    text: str,
    constraint: ResponseConstraint,
) -> ConstraintCheck:
    words = list(_WORD_RE.finditer(str(text or "")))
    sentences = _sentence_spans(text)
    violations: list[str] = []
    if (
        constraint.exact_words is not None
        and len(words) != constraint.exact_words
    ):
        violations.append("exact_words")
    if (
        constraint.max_words is not None
        and len(words) > constraint.max_words
    ):
        violations.append("max_words")
    if (
        constraint.exact_sentences is not None
        and len(sentences) != constraint.exact_sentences
    ):
        violations.append("exact_sentences")
    if (
        constraint.max_sentences is not None
        and len(sentences) > constraint.max_sentences
    ):
        violations.append("max_sentences")
    if constraint.wants_layout:
        items = list_items(text)
        if constraint.list_items is not None and len(items) != constraint.list_items:
            violations.append("list_items")
        if constraint.one_item_per_line and items_share_a_line(text):
            # "1. Red 2. Blue 3. Yellow" holds three items and is not one-per-line.
            violations.append("one_item_per_line")
        if constraint.one_item_per_line and not items:
            violations.append("one_item_per_line")
    missing_formats = tuple(
        fmt for fmt in constraint.requested_formats if not _presentation_answer_has_format(text, fmt)
    )
    if missing_formats:
        # An explicit format request wins over the answer's default presentation: a prose
        # answer to "show it as a table" is a shape violation like any other, which routes it
        # into the same bounded repair — never into a silently re-rendered answer.
        violations.append("presentation_format")
    if not violations and _looks_like_non_answer(str(text or ""), words):
        violations.append("non_answer_refusal")
    if (
        not violations
        and _has_incomplete_short_tail(constraint, words)
    ):
        violations.append("incomplete_fragment")
    # A draft that was cut off is a shape violation like any other, which is what routes it into the
    # one bounded repair the router already runs. Checked last and only when nothing else fired, so
    # a reply that is merely too long keeps its own, more specific violation.
    if not violations and inspect_answer_completeness(text).incomplete:
        violations.append("incomplete_answer")
    return ConstraintCheck(
        compliant=not violations,
        word_count=len(words),
        sentence_count=len(sentences),
        violations=tuple(violations),
        missing_formats=missing_formats,
    )


def short_answer_needs_grounding_retry(
    text: str,
    constraint: ResponseConstraint,
    user_text: str,
) -> bool:
    """Detect a one-word placeholder answer to an open-ended question.

    This is a quality gate, not an answer generator: it asks the model for one
    bounded repair when a shape-compliant draft is plainly non-specific. Binary
    questions and ordinary useful one-word answers remain untouched.
    """
    if constraint.exact_words != 1:
        return False
    words = list(_WORD_RE.finditer(str(text or "")))
    if len(words) != 1:
        return False
    answer = words[0].group(0).casefold().strip("'-")
    if answer not in _LOW_INFORMATION_SHORT_ANSWERS:
        return False
    question = " ".join(str(user_text or "").split())
    if ":" in question:
        question = question.rsplit(":", 1)[-1].strip()
    return bool(_OPEN_QUESTION_RE.search(question)) and not bool(
        _YES_NO_QUESTION_RE.match(question)
    )


_MISSING_FORMAT_REASONS = {
    "table": "no Markdown pipe table (header row, separator row, data rows) was found",
    "chart": (
        "no fenced block labelled exactly chart holding a valid chart JSON object was found; "
        "a json label, a bare fence, a bare JSON line or a table is not the chart"
    ),
    "mermaid": (
        "no top-level fenced block labelled exactly mermaid was found; a diagram nested "
        "inside another fence or written without its own fence is not the diagram"
    ),
    "timeline": "no dated, ordered timeline lines were found",
    "tree": "no indented or box-drawn tree hierarchy was found",
}


def formatting_retry_instruction(
    constraint: ResponseConstraint,
    *,
    missing_formats: tuple[str, ...] | list[str] = (),
) -> str:
    """The one repair instruction for a shape violation.

    Chart and mermaid requirements state the fence label byte for byte, because the label is what
    the renderer keys on and it is the one thing a model cannot infer from "a fenced chart JSON
    block" (measured on a651de73: ```json and bare ``` fences, twice per turn, never ```chart).
    `missing_formats` (from `ConstraintCheck.missing_formats`) lets a retry say which requested
    format the failed draft lacked, instead of restating every format the turn asked for.
    """
    from core.presentation.fences import CHART_FENCE_TAG, MERMAID_FENCE_TAG

    requirements: list[str] = []
    if constraint.exact_words is not None:
        requirements.append(
            f"exactly {constraint.exact_words} word(s)"
        )
    elif constraint.max_words is not None:
        requirements.append(
            f"no more than {constraint.max_words} word(s)"
        )
    if constraint.exact_sentences is not None:
        requirements.append(
            f"exactly {constraint.exact_sentences} sentence(s)"
        )
    elif constraint.max_sentences is not None:
        requirements.append(
            f"no more than {constraint.max_sentences} sentence(s)"
        )
    if constraint.list_items is not None:
        requirements.append(f"exactly {constraint.list_items} numbered items")
    if constraint.one_item_per_line:
        requirements.append("each item on its own line")
    if constraint.per_item_sentences is not None:
        # The half of the request the layout displaced. Said to the model, never counted across the
        # answer -- that arithmetic is what rejected the correct list in the first place.
        requirements.append(
            f"at most {constraint.per_item_sentences} sentence(s) per item"
        )
    for format_name in constraint.requested_formats:
        if str(getattr(constraint, "origin", "") or "") == "automatic":
            # C19 slice 2: an election-derived repair speaks honestly about who asked.
            # It never claims the user requested the shape — the election read the
            # answer's own content, not the prompt.
            if format_name == "chart":
                return (
                    "Answer the original user request with a substantive answer, not this "
                    "instruction. Do not apologize, refuse, or mention formatting. Present "
                    "the answer as a table using only the values already in your answer; "
                    "never invent a value, and keep every citation, number and receipt "
                    "verbatim."
                )
            return (
                "Answer the original user request with a substantive answer, not this "
                "instruction. Do not apologize, refuse, or mention formatting. Present the "
                f"answer as a {format_name} using only the values already in your answer; "
                "never invent a value, and keep every citation, number and receipt verbatim."
            )
        if format_name == "mermaid":
            requirements.append(
                "the diagram itself as a fenced mermaid code block whose opening fence line is "
                f"exactly ```{MERMAID_FENCE_TAG} and whose closing line is ``` on its own, at the "
                "top level of the answer (never nested inside another fence); the chat renders "
                "this source locally. Include a valid diagram declaration such as flowchart LR "
                "before the edges; use stable node IDs with quoted labels, for example "
                'A["label"] --> B["label"]. Use only the requested labels and relationships. '
                "Do not include init directives or YAML configuration. "
                "A depicted workflow establishes structure, not completed activity. "
                "In the diagram and any conclusion, describe steps as completed only when "
                "the supplied facts or observed results establish that they occurred; otherwise "
                "describe the depicted process without inventing execution or outcomes. "
                "Keep explanatory prose outside the diagram block"
            )
        elif format_name == "chart":
            requirements.append(
                "a fenced chart JSON block whose opening fence line is exactly "
                f"```{CHART_FENCE_TAG} (the label must be {CHART_FENCE_TAG}, not json), whose "
                "closing line is ``` on its own, and whose only content is the JSON object: "
                '{"type":"bar","data":{"labels":["label"],'
                '"datasets":[{"label":"unit","data":[0]}]}}. Replace the example with ONLY '
                'the labels, units and values already established; never invent or recalculate '
                'values. Supported types are bar, line, pie and doughnut. Optional title and '
                'six-digit hex background are allowed; no options, plugins or executable code. '
                'Pie charts require actual parts of a known whole. Keep citations and working '
                'outside the chart block; a table alone does not fulfill a chart request'
            )
        else:
            requirements.append(f"the answer presented as a {format_name}")
    joined = " and ".join(requirements)
    missing = [
        str(name) for name in missing_formats
        if str(name) in constraint.requested_formats
    ]
    preface = ""
    if missing:
        reasons = "; ".join(
            f"{name}: {_MISSING_FORMAT_REASONS.get(name, 'the requested shape was not found')}"
            for name in missing
        )
        preface = (
            f"The previous draft did not satisfy the requested {', '.join(missing)} "
            f"({reasons}). Keep every value, table row and diagram already correct. "
        )
    return (
        f"{preface}"
        "Answer the original user request with a substantive answer, not this "
        "instruction. Do not apologize, refuse, or mention formatting. Return "
        f"only the answer using {joined}."
    )


def exact_word_retry_contract(
    constraint: ResponseConstraint,
) -> dict[str, Any] | None:
    """Return a provider-enforced word-array schema for exact-count repairs."""
    count = constraint.exact_words
    if count is None or count < 4:
        return None
    return {
        "json_schema": {
            "type": "object",
            "properties": {
                "words": {
                    "type": "array",
                    # Keep the provider grammar portable. Ollama/llama.cpp
                    # rejects the otherwise-valid string ``pattern`` here
                    # before inference starts. Exact length is constrained by
                    # the array schema; one-word/non-empty content is still
                    # enforced locally by ``exact_word_retry_text``.
                    "items": {"type": "string"},
                    "minItems": count,
                    "maxItems": count,
                }
            },
            "required": ["words"],
            "additionalProperties": False,
        }
    }


def exact_word_retry_text(
    text: str,
    constraint: ResponseConstraint,
) -> str | None:
    """Validate and join model-generated words from a structured repair."""
    count = constraint.exact_words
    if count is None:
        return None
    try:
        payload = json.loads(str(text or ""))
    except (TypeError, ValueError):
        return None
    words = payload.get("words") if isinstance(payload, dict) else None
    if (
        not isinstance(words, list)
        or len(words) != count
        or set(payload) != {"words"}
    ):
        return None
    normalized: list[str] = []
    for item in words:
        word = str(item or "").strip().strip(
            ".,!?;:()[]{}\"“”‘’"
        )
        if _WORD_RE.fullmatch(word) is None:
            return None
        normalized.append(word)
    result = " ".join(normalized)
    return result if check_response_constraint(result, constraint).compliant else None


def compress_short_coordinate_phrase(
    text: str,
    constraint: ResponseConstraint,
) -> str:
    """Preserve two model-generated terms when only their conjunction exceeds the limit."""
    original = str(text or "").strip()
    if constraint.exact_words != 2:
        return original
    coordinate = _TWO_WORD_COORDINATE_RE.fullmatch(original)
    if coordinate is None:
        return original
    return f"{coordinate.group('first')}, {coordinate.group('second')}"


def constraint_safe_fallback(constraint: ResponseConstraint) -> str:
    """Return an honest, shape-compliant fallback after a failed repair."""
    if constraint.wants_layout:
        # Said in the shape that was asked for, so the reply is not itself a second format
        # violation, and said plainly: this is the sentence the user saw on every attempt of the
        # live defect, and it must now only appear when the answer really could not be produced.
        return "- No usable answer was produced for this request."
    if constraint.presentation_format is not None:
        # Text/table fallback: this surface can always render plain text and tables, so the
        # honest failure lands in a shape the user can read. It fabricates NOTHING — no
        # values, no diagram syntax pretending to be a rendered diagram, no rows for data
        # that never arrived. An unsupported format (mermaid/chart on this surface) falls
        # back to exactly this text/table form rather than a fake rendering.
        if constraint.presentation_format in ("table", "chart"):
            return (
                "| Result |\n|---|\n"
                "| No usable answer was produced for this request. |"
            )
        return "No usable answer was produced for this request."
    if constraint.exact_words == 1:
        return "Unavailable."
    if constraint.exact_words == 2:
        return "No answer."
    if constraint.exact_words == 3:
        return "No usable answer."
    if constraint.exact_words == 4:
        return "No usable answer available."
    if constraint.exact_words == 5:
        return "No complete answer is available."
    if constraint.exact_words == 6:
        return "Unable to satisfy the requested format."
    if constraint.exact_words == 7:
        return "I cannot provide a complete answer now."
    if constraint.exact_words is not None:
        modifier_count = constraint.exact_words - 8
        modifiers = [
            "reliable",
            "grounded",
            "concise",
            "clear",
            "relevant",
            "safe",
            "honest",
            "helpful",
            "direct",
            "substantive",
            "accurate",
            "usable",
        ]
        selected_modifiers = [
            modifiers[index % len(modifiers)]
            for index in range(modifier_count)
        ]
        words = [
            "I",
            "cannot",
            "provide",
            "a",
            "complete",
            *selected_modifiers,
            "answer",
            "right",
            "now",
        ]
        return " ".join(words) + "."
    if constraint.max_words is not None:
        return "Unavailable."
    return "I couldn't produce a complete response within the requested format."


def enforce_response_constraint(
    text: str,
    constraint: ResponseConstraint,
) -> ConstraintApplication:
    """Apply a deterministic upper bound if the one retry still overproduces.

    Presentation fences are canonicalised first (labels the renderers read, wrapper fences
    unwrapped; block bytes untouched), so the text that ships is the text the check judged.
    """
    original = str(text or "").strip()
    fences_normalized = False
    if constraint.requested_formats:
        from core.presentation.fences import normalize_presentation_fences

        normalized = normalize_presentation_fences(
            original, requested_formats=constraint.requested_formats
        )
        if normalized.changed:
            original = normalized.text.strip()
            fences_normalized = True
    bounded = original
    structurally_trimmed = False
    coordinate_compressed = compress_short_coordinate_phrase(
        bounded,
        constraint,
    )
    if coordinate_compressed != bounded:
        # Preserve both model-generated descriptors while dropping only the
        # conjunction. This is a structural compression, not a canned answer.
        bounded = coordinate_compressed
        structurally_trimmed = True
    if constraint.max_sentences is not None:
        sentences = _sentence_spans(bounded)
        if len(sentences) > constraint.max_sentences:
            bounded = bounded[
                : sentences[constraint.max_sentences - 1].end
            ].strip()
            structurally_trimmed = True
    if constraint.max_words is not None:
        words = list(_WORD_RE.finditer(bounded))
        if len(words) > constraint.max_words:
            bounded = bounded[: words[constraint.max_words - 1].end()]
            bounded = bounded.rstrip(" \t\r\n,;:-")
            structurally_trimmed = True
    # A trim may shorten an answer; it may never manufacture a broken one. When the cut leaves a
    # fragment that reads as cut off -- a bare list marker, a clause ending on a preposition -- the
    # original text is restored and the shape violation stands, so the caller repairs or falls back
    # honestly instead of shipping the fragment as the answer.
    if structurally_trimmed and inspect_answer_completeness(bounded).incomplete:
        bounded = original
        structurally_trimmed = False
    check = check_response_constraint(bounded, constraint)
    return ConstraintApplication(
        text=bounded,
        compliant=check.compliant,
        structurally_trimmed=structurally_trimmed,
        violations=check.violations,
        fences_normalized=fences_normalized,
    )


__all__ = [
    "PRESENTATION_FORMATS",
    "SURFACE_RENDERS_PRESENTATION",
    "ConstraintApplication",
    "ConstraintCheck",
    "ResponseConstraint",
    "check_response_constraint",
    "compress_short_coordinate_phrase",
    "constraint_safe_fallback",
    "enforce_response_constraint",
    "exact_word_retry_contract",
    "exact_word_retry_text",
    "formatting_retry_instruction",
    "parse_clause_response_constraint",
    "parse_response_constraint",
    "presentation_shape_spans",
    "residual_words_outside_shape",
    "response_constraint_from_metadata",
    "short_answer_needs_grounding_retry",
]
