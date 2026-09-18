"""One definition of "this answer stopped before it finished", for the whole runtime.

The defect this exists to remove, measured on the v0.5.0 smoke run (QA-050-016/017/021). A
multi-part local turn was answered with a numbered list, `enforce_response_constraint` trimmed it on
what `_SENTENCE_RE` called the first sentence -- the enumeration marker `1.` -- and the runtime then
shipped, stored and reported the literal string::

    1.

as a successful answer. Every check it passed on the way out agreed with it: the shape checker
counted one word and one sentence against a one-sentence constraint, `inspect_ordinary_chat_output`
returned `allowed=True`, and so did the same call on the empty string. The same turn against a
pinned cloud model produced the same two characters, which is the tell that the truncation was the
runtime's and not one model's.

What this module is, and what it deliberately is not
----------------------------------------------------

It is **structural and deterministic**. It asks whether the text has the shape of an answer that was
cut off -- a bare list marker, a bullet with nothing under it, a list that stops mid-item, a
sentence that ends on a preposition -- and never whether the content is any good. Judging quality is
a model's job; this is the floor beneath it.

It is **narrow on purpose, and it fails open**. A short answer is not an incomplete one: `Ready.`,
`New York` and `42` are complete answers and none of them is flagged. Every rule below needs a
positive structural signal, so anything this module cannot decide passes. A false positive here
annotates or replaces a correct answer, which is worse than the truncation it would have caught.

Two rules are worth their asymmetry:

* A marker line with no inline content ("1." on its own) is only empty when the next non-blank line
  is another marker or there is no next line at all. A model that writes the marker and puts the
  item on the following line has written a complete list, and reading that as an empty bullet would
  fail a whole legitimate formatting style.
* A dangling final word ("and", "the") counts only when the text does not end on terminal
  punctuation. "It depends on the weather, the season and the." is broken; "Ask about the season,
  the weather and the rest." is not.

Callers
-------

`core/response_constraints.check_response_constraint` raises it as a violation, which routes into
the bounded repair the router already runs for a shape violation -- so an incomplete draft is
retried rather than trimmed into rubbish. `core/agent_runtime/response._validate_final_chat_output`
is the last backstop before the text becomes the reply and the chat memory: an answer with no
content at all is replaced with an honest notice, and a partial one is shipped with its state said
out loud instead of passed off as finished.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Any


class ProviderOutputIncompleteError(RuntimeError):
    """A generation consumer must not parse or publish a known partial provider artifact."""

    def __init__(self, reasons: tuple[str, ...] = ()) -> None:
        self.reasons = reasons
        super().__init__("The provider stopped before completing the requested output.")

# Invisible but not whitespace, so `strip()` leaves them behind and a "blank" answer reads as
# non-empty. Same categories, same reason as `core/agent_runtime/request_authority.py`'s
# `visible_request_text`; kept independent here so this module stays stdlib-only and can be
# imported by leaf modules without pulling in the agent runtime package.
_INVISIBLE_CATEGORIES = frozenset({"Cc", "Cf", "Cs"})

# A line that opens an enumerated or bulleted item: "1.", "1)", "(1)", "-", "*", "a.", "iv)".
_LIST_MARKER_RE = re.compile(
    r"^\s*(?:[-*•‣▪▫◦]|\(?\d{1,3}[.)]|\(?[A-Za-z][.)]|\(?[ivxIVX]{1,4}[.)])\s*",
)
# A marker anywhere in a line, not just at its start -- how "1. Red 2. Blue" is caught. Requires a
# space or line start in front so a decimal ("3.5 kg") and a version ("v1.2") are not markers.
_INLINE_MARKER_RE = re.compile(r"(?:(?<=\s)|^)(?:\(?\d{1,3}[.)]|[-*•‣▪▫◦])\s+\S")
_WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)
_TERMINAL_PUNCTUATION = frozenset(".!?:;\"'`)]}»”’…")

# Words a sentence cannot end on. Same intent as `response_constraints._DANGLING_CONNECTORS`,
# widened with the determiners, and applied only to text that also lacks terminal punctuation.
_DANGLING_TAIL_WORDS = frozenset(
    {
        "and", "or", "but", "because", "if", "so", "than", "as", "of", "for", "with", "from",
        "into", "onto", "to", "in", "on", "at", "by", "about", "over", "under", "between",
        "a", "an", "the", "each", "every", "either", "neither", "another", "which", "that",
    }
)

# Additional tails that are only decisive when independent provider evidence says the output
# budget was reached. They are deliberately NOT part of the general detector: terse answers such
# as "The winner is" can be intentional in an exact-shape task, but at the exact token ceiling the
# same tail is strong evidence that generation stopped mid-clause.
_CAP_DANGLING_TAIL_WORDS = _DANGLING_TAIL_WORDS | frozenset(
    {
        "am", "are", "be", "been", "being", "can", "could", "did", "do", "does",
        "had", "has", "have", "is", "may", "might", "must", "shall", "should", "was",
        "were", "will", "would",
    }
)
_LENGTH_FINISH_REASONS = frozenset(
    {
        "length", "max_length", "max_tokens", "max_output_tokens", "token_limit",
        "output_limit",
    }
)
_CAP_TRAILING_PUNCTUATION = frozenset(",-/=\\–—([{:")

_ANNOUNCED_COUNT_WORDS = {
    "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
}
# "Here are three things", "below are the 4 options" -- an answer stating how many items it is
# about to list. Only the announcing forms; a sentence that merely contains a number is not one.
#
# The determiner may carry a ranking or selecting qualifier before the number, and leaving those
# out silently disabled the guard on the most common shape a list answer takes. Measured live on
# the served surface: "Here are the top 5 best-selling cars globally with their max speeds and
# engine sizes:" followed by four items, the last of them cut mid-value at "..., 1." -- announced
# count resolved to 0 because `top` sat between `the` and `5`, so `fewer_items_than_announced`
# never fired and a four-of-five answer shipped as complete with `done_reason: "stop"`.
#
# Bounded on purpose: these are qualifiers that still announce a COUNT. A word that changes what is
# being counted ("here are the first 5 of 20") is not in this set.
_ANNOUNCED_QUALIFIERS = r"(?:top|best|main|key|most\s+common|following|other|remaining)\s+"
_ANNOUNCED_COUNT_WORDS_RE = r"\d{1,2}|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve"
_ANNOUNCED_ITEMS_RE = re.compile(
    r"\b(?:here\s+are|below\s+are|there\s+are|these\s+are)\s+"
    r"(?:(?:the|my|our|its|their)\s+)?"
    rf"(?:{_ANNOUNCED_QUALIFIERS})?"
    rf"(?P<count>{_ANNOUNCED_COUNT_WORDS_RE})\b",
    re.IGNORECASE,
)

_EMPTY_ANSWER = "empty_answer"
_ENUMERATION_WITHOUT_CONTENT = "enumeration_without_content"
_EMPTY_ENUMERATION_ITEM = "empty_enumeration_item"
_UNFINISHED_ENUMERATION = "unfinished_enumeration"
_DANGLING_TAIL = "dangling_tail"
_FEWER_ITEMS_THAN_ANNOUNCED = "fewer_items_than_announced"
_TABLE_CUT_MID_ROW = "table_cut_mid_row"

# Reasons that mean there is no answer at all, as opposed to a partial one. The distinction decides
# what the final backstop may do: replacing text costs the user nothing when the text carries
# nothing, and costs them the whole answer when it carries most of one.
_NO_CONTENT_REASONS = frozenset({_EMPTY_ANSWER, _ENUMERATION_WITHOUT_CONTENT})

_REASON_DESCRIPTIONS = {
    _EMPTY_ANSWER: "the answer has no visible content",
    _ENUMERATION_WITHOUT_CONTENT: "the answer is list markers with nothing under them",
    _EMPTY_ENUMERATION_ITEM: "a listed item was left blank",
    _UNFINISHED_ENUMERATION: "the list stops on an item that was never written",
    _DANGLING_TAIL: "the last sentence ends mid-clause",
    _FEWER_ITEMS_THAN_ANNOUNCED: "fewer items were listed than the answer said it would list",
    _TABLE_CUT_MID_ROW: "the table stops part-way through a row",
}


@dataclass(frozen=True)
class AnswerCompleteness:
    """Whether an answer looks cut off, and what said so."""

    incomplete: bool = False
    reasons: tuple[str, ...] = ()
    has_content: bool = True

    @property
    def degenerate(self) -> bool:
        """True when nothing usable survived -- there is no partial answer to preserve."""
        return self.incomplete and not self.has_content

    def as_dict(self) -> dict[str, Any]:
        return {
            "incomplete": self.incomplete,
            "reasons": list(self.reasons),
            "has_content": self.has_content,
            "degenerate": self.degenerate,
        }


@dataclass(frozen=True)
class ProviderCompletion:
    """Whether provider evidence says a completion ended before the answer did.

    ``finish_reason`` is provider-authored evidence. ``at_output_limit`` is measured evidence from
    usage and the sealed request ceiling. Text heuristics may only turn the latter into an
    incomplete verdict when a concrete unfinished tail is also present.
    """

    incomplete: bool = False
    reasons: tuple[str, ...] = ()
    finish_reason: str = ""
    output_tokens: int | None = None
    max_output_tokens: int | None = None
    at_output_limit: bool = False
    provider_reported_limit: bool = False
    has_content: bool = True

    def as_dict(self) -> dict[str, Any]:
        return {
            "incomplete": self.incomplete,
            "reasons": list(self.reasons),
            "finish_reason": self.finish_reason,
            "output_tokens": self.output_tokens,
            "max_output_tokens": self.max_output_tokens,
            "at_output_limit": self.at_output_limit,
            "provider_reported_limit": self.provider_reported_limit,
            "has_content": self.has_content,
        }


def _visible(text: Any) -> str:
    return "".join(
        char
        for char in str(text or "")
        if not char.isspace() and unicodedata.category(char) not in _INVISIBLE_CATEGORIES
    )


@dataclass(frozen=True)
class _Line:
    is_marker: bool
    content: str


def _lines(text: str) -> list[_Line]:
    parsed: list[_Line] = []
    for raw in str(text or "").splitlines():
        if not _visible(raw):
            continue
        match = _LIST_MARKER_RE.match(raw)
        if match is None:
            parsed.append(_Line(is_marker=False, content=raw.strip()))
            continue
        parsed.append(_Line(is_marker=True, content=raw[match.end():].strip()))
    return parsed


def _announced_item_count(text: str) -> int:
    match = _ANNOUNCED_ITEMS_RE.search(str(text or ""))
    if match is None:
        return 0
    raw = str(match.group("count") or "").strip().lower()
    if raw.isdigit():
        value = int(raw)
        return value if 2 <= value <= 12 else 0
    return _ANNOUNCED_COUNT_WORDS.get(raw, 0)


#: A markdown table row: a line whose first visible character is a pipe. The separator row
#: ("|---|---|") is a row too, and is skipped where it matters.
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_TABLE_SEPARATOR_RE = re.compile(r"^\s*\|[\s:|-]+\|?\s*$")


def _table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    return [cell.strip() for cell in stripped.split("|")]


def _table_truncation_reasons(text: str) -> list[str]:
    """Whether a markdown table stops part-way through its last row.

    Added because the inspector called a severed table complete. Measured live: a request for specs
    on five cars returned a five-column table whose last line was

        | Ford F-Series | Varies | 4,600 - 6,200 | 495+ |

    -- four values under five headers -- and another returned `| Civic | 2.`, a row cut inside a
    number. Both shipped as finished answers. A table is the one structure where "cut off" is
    unambiguous from shape alone: the header fixes the column count, so a final row that is short,
    or that never closes its last cell, is truncation and not a formatting choice.

    Only the LAST row is judged. A short row in the middle of a table is a formatting decision
    somebody made on purpose; a short row at the end is where the text stopped.
    """

    lines = [line for line in str(text or "").splitlines() if line.strip()]
    rows = [line for line in lines if _TABLE_ROW_RE.match(line)]
    if len(rows) < 2:
        return []
    header = rows[0]
    body = [row for row in rows[1:] if not _TABLE_SEPARATOR_RE.match(row)]
    if not body:
        return []
    # The table has to be the tail of the answer; prose after it means the table finished and the
    # reply moved on.
    if lines[-1] is not body[-1]:
        return []

    expected = len(_table_cells(header))
    last = body[-1]
    if not last.strip().endswith("|"):
        return [_TABLE_CUT_MID_ROW]
    if expected > 1 and len(_table_cells(last)) < expected:
        return [_TABLE_CUT_MID_ROW]
    return []


def inspect_answer_completeness(text: str) -> AnswerCompleteness:
    """Whether `text` has the shape of an answer that was cut off before it finished."""

    if not _visible(text):
        return AnswerCompleteness(incomplete=True, reasons=(_EMPTY_ANSWER,), has_content=False)

    lines = _lines(text)
    markers = [line for line in lines if line.is_marker]
    reasons: list[str] = []

    if markers and not any(line.content for line in lines):
        # Every line is a marker and not one of them carries anything: "1.", "1.\n2.\n3.", "-\n-".
        return AnswerCompleteness(
            incomplete=True,
            reasons=(_ENUMERATION_WITHOUT_CONTENT,),
            has_content=False,
        )

    for index, line in enumerate(lines):
        if not line.is_marker or line.content:
            continue
        # A marker whose item is written on the following line is a complete item, not a blank one.
        next_line = lines[index + 1] if index + 1 < len(lines) else None
        if next_line is not None and not next_line.is_marker:
            continue
        reasons.append(
            _UNFINISHED_ENUMERATION if next_line is None else _EMPTY_ENUMERATION_ITEM
        )

    reasons.extend(_table_truncation_reasons(text))

    announced = _announced_item_count(text)
    if announced and markers and len(markers) < announced:
        reasons.append(_FEWER_ITEMS_THAN_ANNOUNCED)

    stripped = str(text or "").rstrip()
    if stripped and stripped[-1] not in _TERMINAL_PUNCTUATION:
        words = _WORD_RE.findall(stripped)
        if len(words) > 1 and words[-1].casefold().strip("'-") in _DANGLING_TAIL_WORDS:
            reasons.append(_DANGLING_TAIL)

    deduped = tuple(dict.fromkeys(reasons))
    return AnswerCompleteness(
        incomplete=bool(deduped),
        reasons=deduped,
        has_content=not all(reason in _NO_CONTENT_REASONS for reason in deduped) if deduped else True,
    )


def inspect_published_answer_completeness(text: str) -> AnswerCompleteness:
    """A runtime disclosure cannot make its unfinished payload complete.

    Re-derive from the preserved bytes, accepting only the exact notice generated
    for that payload. Quoted notices or arbitrary prose do not carry authority.
    """
    body, separator, tail = str(text or "").rstrip().rpartition("\n\n")
    if separator:
        result = inspect_answer_completeness(body)
        if result.incomplete and tail == partial_answer_notice(result):
            return result
    return inspect_answer_completeness(text)


def _reported_output_tokens(usage: dict[str, Any] | None) -> int | None:
    block = dict(usage or {})
    for key in ("eval_count", "completion_tokens", "output_tokens"):
        value = block.get(key)
        if value is None or isinstance(value, bool):
            continue
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            continue
        if parsed >= 0:
            return parsed
    return None


def _positive_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _unclosed_delimiter(text: str) -> bool:
    """Conservative bracket check for cap-bound text; ignores delimiters inside code fences."""

    outside_fences = "".join(str(text or "").split("```")[::2])
    pairs = {")": "(", "]": "[", "}": "{"}
    stack: list[str] = []
    for char in outside_fences:
        if char in "([{":
            stack.append(char)
        elif char in pairs:
            if stack and stack[-1] == pairs[char]:
                stack.pop()
    return bool(stack)


def _cap_tail_reasons(text: str) -> tuple[str, ...]:
    stripped = str(text or "").rstrip()
    if not stripped:
        return ("empty_answer",)
    reasons = list(inspect_answer_completeness(stripped).reasons)
    if stripped.count("```") % 2:
        reasons.append("unclosed_code_fence")
    if stripped.count("`") % 2 and "```" not in stripped:
        reasons.append("unclosed_inline_code")
    if _unclosed_delimiter(stripped):
        reasons.append("unclosed_delimiter")
    if stripped[-1] in _CAP_TRAILING_PUNCTUATION:
        reasons.append("unfinished_terminal_punctuation")
    words = _WORD_RE.findall(stripped)
    if (
        stripped[-1] not in _TERMINAL_PUNCTUATION
        and len(words) > 1
        and words[-1].casefold().strip("'-") in _CAP_DANGLING_TAIL_WORDS
    ):
        reasons.append("cap_dangling_tail")
    return tuple(dict.fromkeys(reasons))


def inspect_provider_completion(
    text: str,
    *,
    finish_reason: str = "",
    usage: dict[str, Any] | None = None,
    max_output_tokens: int | None = None,
) -> ProviderCompletion:
    """Combine provider termination evidence with a conservative structural cut-off check.

    A provider-declared length stop is authoritative. Without one, reaching the exact sealed token
    ceiling is not enough by itself: the text must also end on an unfinished structural signal.
    Thus complete answers at or near the cap continue to pass.
    """

    normalized_reason = str(finish_reason or "").strip().casefold()
    output_tokens = _reported_output_tokens(usage)
    ceiling = _positive_int(max_output_tokens)
    at_limit = bool(
        output_tokens is not None and ceiling is not None and output_tokens >= ceiling
    )
    provider_reported_limit = normalized_reason in _LENGTH_FINISH_REASONS
    reasons: list[str] = []
    if provider_reported_limit:
        reasons.append(f"provider_finish_reason:{normalized_reason}")
    if at_limit:
        cap_reasons = _cap_tail_reasons(text)
        reasons.extend(f"output_cap:{reason}" for reason in cap_reasons)
    incomplete = provider_reported_limit or bool(reasons)
    return ProviderCompletion(
        incomplete=incomplete,
        reasons=tuple(dict.fromkeys(reasons)),
        finish_reason=normalized_reason,
        output_tokens=output_tokens,
        max_output_tokens=ceiling,
        at_output_limit=at_limit,
        provider_reported_limit=provider_reported_limit,
        has_content=bool(_visible(text)),
    )


def answer_looks_incomplete(text: str) -> bool:
    """Convenience predicate for callers that only need the verdict."""
    return inspect_answer_completeness(text).incomplete


def list_items(text: str) -> tuple[str, ...]:
    """The content of each written list item, in order.

    One definition of "a list item" for the whole runtime. `core/response_constraints.py` validates
    a requested layout against this, so "what counts as an item" cannot drift between the module
    that decides an answer is unfinished and the module that decides it is the wrong shape.

    A marker with nothing after it contributes nothing -- an item that was never written is not an
    item -- which is the same reading `inspect_answer_completeness` already takes of it.
    """
    return tuple(line.content for line in _lines(text) if line.is_marker and line.content)


def items_share_a_line(text: str) -> bool:
    """True when several list markers were crammed onto one line.

    "1. Red 2. Blue 3. Yellow" satisfies a count of three and still is not one-per-line, so a
    layout check needs to see it. Counted per PHYSICAL line, before `_lines` splits anything.
    """
    return any(
        len(_INLINE_MARKER_RE.findall(raw)) > 1 for raw in str(text or "").splitlines()
    )


def describe_incompleteness(result: AnswerCompleteness) -> str:
    """The reasons in plain words, for a user-facing notice or a trace entry."""
    described = [_REASON_DESCRIPTIONS[reason] for reason in result.reasons if reason in _REASON_DESCRIPTIONS]
    if not described:
        return ""
    if len(described) == 1:
        return described[0]
    return f"{', '.join(described[:-1])} and {described[-1]}"


def incomplete_answer_notice(result: AnswerCompleteness | None = None) -> str:
    """The reply for an answer that arrived with nothing usable in it.

    It names the stage rather than apologising: the turn produced output, the output was cut off,
    and asking again re-runs it. Nothing is invented to fill the gap.
    """
    detail = describe_incompleteness(result) if result is not None else ""
    if detail:
        return (
            f"The answer came back cut off -- {detail} -- so there is nothing complete to show you. "
            "Ask again and I'll run it fresh."
        )
    return (
        "The answer came back cut off, so there is nothing complete to show you. "
        "Ask again and I'll run it fresh."
    )


def partial_answer_notice(result: AnswerCompleteness | None = None) -> str:
    """The line appended to an answer that is real but unfinished.

    The answer is kept -- most of it is usable and throwing it away helps nobody -- but it is not
    passed off as finished, which is the whole failure this module exists to stop.
    """
    detail = describe_incompleteness(result) if result is not None else ""
    if detail:
        return f"(Incomplete: {detail}. Ask again for the rest.)"
    return "(Incomplete: this answer stopped before it finished. Ask again for the rest.)"


__all__ = [
    "AnswerCompleteness",
    "ProviderCompletion",
    "answer_looks_incomplete",
    "describe_incompleteness",
    "incomplete_answer_notice",
    "inspect_answer_completeness",
    "inspect_provider_completion",
    "inspect_published_answer_completeness",
    "items_share_a_line",
    "list_items",
    "partial_answer_notice",
]
