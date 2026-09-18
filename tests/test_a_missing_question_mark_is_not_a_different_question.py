"""Punctuation the user skipped must not change the answer.

Found by the seeded hostile driver on the live app (seed 5150):

    U: what time is it in berlin Output exactly one word. asap
    A: 13:12                                        (Berlin was 03:12 -- ten hours out)

The identical question WITH a question mark answered 03:12 correctly the whole time. Nobody types
carefully, and a missing "?" turned a correct deterministic answer into a confidently wrong one
from a model.

Two independent defects stacked, and both had to go:

1. THE OUTPUT INSTRUCTION WAS READ AS PART OF THE QUESTION. Without the "?", the place phrase read
   as "berlin output exactly one word", resolved to no zone, and the clock lane declined -- handing
   the turn to a model. An output instruction is not part of the question; it is now cut before the
   question is parsed, and only when the trailing clause actually states an output shape.

2. THE TWO WORD COUNTERS DISAGREED. With the question parsed, the lane produced "03:12" and then
   REJECTED it against the turn's own one-word contract: `core.response_constraints` counts "03:12"
   as one word (taught earlier the same day), while `core.raw_output_contract` kept its own counter
   and still split on the colon. The same answer was one word by one module and two by the other,
   so the lane declined a correct answer it had already computed.

The second is the more instructive failure: the colon fix had been made, and its sibling was left
alone. A repair applied in one place while an identical path keeps the defect is a second defect
wearing the first one's fix.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo

import pytest

from core.agent_runtime.fast_paths_utility import date_time_fast_path, extract_utility_timezone
from core.raw_output_contract import apply_raw_output_contract, parse_raw_output_contract

PIN = dt.datetime(2026, 8, 15, 1, 12, tzinfo=dt.timezone.utc)


def _answer(text: str) -> str:
    return date_time_fast_path(None, text, source_surface="api", now_utc=PIN) or ""


def _at(zone: str, minutes: int = 0) -> str:
    return (PIN + dt.timedelta(minutes=minutes)).astimezone(zoneinfo.ZoneInfo(zone)).strftime("%H:%M")


def test_the_reported_turn_answers_the_right_city_clock() -> None:
    """The measured turn, verbatim: Berlin was 03:12 and a model said 13:12."""

    assert _at("Europe/Berlin") in _answer("what time is it in berlin Output exactly one word. asap")


@pytest.mark.parametrize(
    ("text", "zone", "minutes"),
    (
        ("what time is it in berlin Output exactly one word. asap", "Europe/Berlin", 0),
        ("what time is it in berlin Output exactly one word", "Europe/Berlin", 0),
        ("what time is it in tokyo answer in one word", "Asia/Tokyo", 0),
        ("what time is it in denver print only the time", "America/Denver", 0),
        # Punctuated controls -- these worked before and must keep working.
        ("what time is it in berlin? Output exactly one word.", "Europe/Berlin", 0),
        ("what time was it in denver 2 hrs ago? Output exactly one word.", "America/Denver", -120),
        ("whats the time in tokyo in 3 hours, one word", "Asia/Tokyo", 180),
    ),
)
def test_the_answer_does_not_depend_on_punctuation(text: str, zone: str, minutes: int) -> None:
    assert _at(zone, minutes) in _answer(text), text


def test_the_place_survives_an_unpunctuated_output_instruction() -> None:
    """The first defect, at its own seam."""

    assert extract_utility_timezone("what time is it in berlin Output exactly one word. asap") == (
        "Europe/Berlin",
        "Berlin",
    )


# ---------------------------------------------------------------------------------------------
# The two word counters must agree
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize("answer", ("03:12", "23:59", "12:30:45"))
def test_a_clock_time_is_one_word_to_the_raw_contract_too(answer: str) -> None:
    """The sibling that was left behind: a separator INSIDE a token is not a boundary.

    `core.response_constraints` already knew this; this module did not, so a one-word clock
    contract was satisfied by one counter and violated by the other.
    """

    contract = parse_raw_output_contract("what time is it in berlin Output exactly one word. asap")

    assert contract is not None and contract.exact_words == 1
    application = apply_raw_output_contract(answer, contract)
    assert application.compliant, f"{answer!r} counted as more than one word"


def test_a_genuinely_multi_word_answer_still_violates_a_one_word_contract() -> None:
    """The control that keeps the counter from becoming permissive."""

    contract = parse_raw_output_contract("what time is it in berlin Output exactly one word. asap")

    assert contract is not None
    assert not apply_raw_output_contract("It is 03:12 in Berlin", contract).compliant


# ---------------------------------------------------------------------------------------------
# The cut must not eat a real question
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ("how many minutes is 5h", "what time is the meeting in 3 days", "meet me in 5 High Street"),
)
def test_a_non_clock_turn_is_still_declined(text: str) -> None:
    assert _answer(text) == "", text


def test_a_trailing_clause_that_is_not_an_output_shape_is_kept() -> None:
    """The cut is confirmed by the shape parser before it is taken, so an ordinary trailing clause
    -- or a place whose name begins with one of those words -- is never truncated away."""

    assert extract_utility_timezone("what time is it in berlin give or take an hour")[0] in {
        "Europe/Berlin",
        "",
    }
