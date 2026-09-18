"""The follow-up proceed reading: whether a message says "go ahead", and whether it also asks for something of its own.

`core.agent_runtime.checkpoints.prepare_runtime_checkpoint` resumes the session's pending turn on a go-ahead and replaces
the message with the stored request. That is right for "yes", "go ahead" and "carry on in the Work folder": they point at
the pending work. It is wrong for a message that brings a request of its own -- 'go ahead and delete my Apple note
"Groceries"' answers the delete question with that delete, approved in its own words -- and wrong when the go-ahead words
sit inside a value the message names ('delete my apple note titled Go ahead list').
"""
from __future__ import annotations

import re

#: A whole message that is a go-ahead.
_PROCEED_PATTERNS: frozenset[str] = frozenset({
    "proceed", "carry on", "continue", "do it", "do all", "go ahead",
    "start working", "yes", "yes proceed", "yes do it", "ok do it",
    "ok proceed", "ok go ahead", "deliver it", "submit it", "just do it",
    "yes pls", "yes please", "all good carry on", "proceed with next steps",
    "proceed with that", "all good", "no proceed",
})
#: A go-ahead phrase anywhere in a message, between spaces.
_PROCEED_PHRASES: tuple[str, ...] = (
    "proceed", "carry on", "continue", "do it", "do all", "go ahead", "start working", "just do it",
)
#: A research or Hive go-ahead anywhere in a message, read as text.
_PROCEED_MARKERS: tuple[str, ...] = (
    "do research", "start research", "run research", "deliver to hive", "deliver to the hive", "deliver it to hive",
    "deliver it to the hive", "submit to hive", "submit to the hive", "post to hive", "research and deliver",
    "research it", "do it properly",
)
#: Where each go-ahead phrase and marker sits, under the boundaries `reads_as_proceed` reads them with: a phrase between
#: whitespace or the punctuation a message is trimmed of, a marker anywhere.
_GO_AHEAD_RES: tuple[re.Pattern[str], ...] = tuple(
    re.compile(r"(?<![^\s?!.,])" + r"\s+".join(map(re.escape, phrase.split())) + r"(?![^\s?!.,])", re.IGNORECASE)
    for phrase in _PROCEED_PHRASES
) + tuple(re.compile(r"\s+".join(map(re.escape, marker.split())), re.IGNORECASE) for marker in _PROCEED_MARKERS)
#: A negator in the message's own words. Approval is read in a request's own words without polarity (the strict xfails
#: in tests/test_operator_approval_words.py): 'delete my Apple note "Plan", actually don't do it' parses as approved. A
#: follow-up that negates keeps the reading it had before requests of its own were read here -- it resumes the pending
#: turn, which asks again -- instead of running as a request whose approval the parser cannot read.
_NEGATOR_RE = re.compile(
    r"\b(?:not|no|never|nothing|none|neither|nor|nevermind|cannot)\b"
    r"|n['’]t\b"
    r"|\b(?:dont|doesnt|didnt|wont|cant|shouldnt|wouldnt|isnt|arent|wasnt|werent|havent|hasnt|aint)\b",
    re.IGNORECASE,
)


def _proceed_key(text: str) -> str:
    return " ".join(str(text or "").strip().lower().split())


def reads_as_proceed(text: str) -> bool:
    """Whether `text` holds a go-ahead: the whole message, a go-ahead phrase between spaces, or a research marker."""
    compact = _proceed_key(text).strip(" \t\n\r?!.,")
    if compact in _PROCEED_PATTERNS:
        return True
    padded = f" {compact} "
    if any(f" {phrase} " in padded for phrase in _PROCEED_PHRASES):
        return True
    return any(marker in compact for marker in _PROCEED_MARKERS)


def _request_values(text: str) -> tuple[str, ...]:
    from core.operator.parser import unquoted_request_values

    return unquoted_request_values(text)


def is_proceed_message(text: str) -> bool:
    """Whether the message's own words hold a go-ahead.

    A go-ahead word inside a value the message names is not the user's: 'yes, delete the apple note "Yes list" in the Go
    ahead folder' confirms that delete, and 'delete my apple note titled Go ahead list' names a note. Values are the quoted
    values and folder and account names (`core.operator.parser.request_own_words`) and the values the parser read from
    outside quotes (`core.operator.parser.unquoted_request_values`). The raw words are read first, so a message without a
    go-ahead word never reaches the value readers.
    """
    if not reads_as_proceed(text):
        return False
    from core.operator.parser import request_own_words

    return reads_as_proceed(request_own_words(text, data=_request_values(text)))


def proceed_carries_its_own_request(text: str) -> bool:
    """Whether a go-ahead message also asks for something of its own, so it runs as that request instead of resuming.

    'go ahead and delete my Apple note "Groceries"', 'delete my Apple note "Do it later", do it' and 'proceed to rename my
    Apple note "Plan" to "Plan v2"' carry their own requests. "yes", "go ahead and delete it", "carry on in the Work
    folder" and "continue where you stopped" carry none: they point at the pending work. The go-ahead words are set aside
    where the proceed reading finds them -- in the message's own words, never inside a value -- and the request
    interpretation reads what is left (`core.agent_runtime.answer_coverage.asks_for_work_of_its_own`). A whole-message
    go-ahead carries nothing, and neither does a message whose own words negate (`_NEGATOR_RE`).
    """
    from core.agent_runtime.answer_coverage import asks_for_work_of_its_own
    from core.operator.apple_notes import words_outside_values
    from core.operator.parser import request_own_words

    value = str(text or "")
    values = _request_values(value)
    own = request_own_words(value, data=values)
    if _proceed_key(own).strip(" \t\n\r?!.,") in _PROCEED_PATTERNS or _NEGATOR_RE.search(own):
        return False
    # Offsets kept: every value is masked in place, so a span found here is the same span of the message.
    words = words_outside_values(value, data=values)
    spans = tuple(match.span() for pattern in _GO_AHEAD_RES for match in pattern.finditer(words))
    return asks_for_work_of_its_own(value, set_aside=spans)


class ProceedIntentSupportMixin:
    _PROCEED_PATTERNS: frozenset[str] = _PROCEED_PATTERNS

    def _looks_like_explicit_resume_request(self, text: str) -> bool:
        normalized = self._resume_request_key(text)
        return normalized in {
            "continue",
            "resume",
            "retry",
            "try again",
            "continue please",
            "resume please",
            "keep going",
            "go on",
            "pick up where you left off",
        }

    def _looks_like_resume_request(self, text: str) -> bool:
        return self._looks_like_explicit_resume_request(text) or self._is_proceed_message(text)

    def _resume_request_key(self, text: str) -> str:
        return _proceed_key(text)

    def _is_proceed_message(self, text: str) -> bool:
        return is_proceed_message(text)

    def _reads_as_proceed(self, text: str) -> bool:
        return reads_as_proceed(text)
