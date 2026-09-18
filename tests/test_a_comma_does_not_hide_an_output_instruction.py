"""An offset must survive the punctuation the user happened to use.

Found by driving the repaired clock lane with wording its own corpus never contained, which is
the whole point of driving it that way::

    "time in london 90 minutes from now, one word"
        -> "Current time in London is 00:37 BST."          # the shift silently dropped

    "what time is it in 5h from now in Tokyo? Output exactly one word."
        -> correct shifted time                             # the reported wording, green

Same question, same lane, same offset machinery. The only difference is that one states its
output shape after a COMMA and the other after a PERIOD.

WHY. `_relative_offset_binds_to_the_clock_ask` decides whether a relative offset modified the
clock ask or some other clause, by removing the ask, the place and any output-instruction
SEGMENT and asking whether anything substantive remains. It split segments on sentence
punctuation only, so ", one word" never became its own segment; it stayed glued to the ask,
counted as substantive text, and the guard concluded the offset belonged to another clause. The
offset was then dropped and the CURRENT time answered -- a confidently wrong answer, which is
the worst failure this lane has, and worse than declining.

THE REPAIR, generalized twice over: a comma delimits an output instruction as surely as a period
does, and "is this segment an output instruction?" is answered by the repo's OWN answer-shape
parser in addition to the segment-initial verb pattern, so a bare shape stated without a verb
("one word") is recognized without growing a second phrase list.

The negative control is the reason the guard exists at all: in "the deploy finishes in 5 hours,
what time is it in london" the offset belongs to the DEPLOY, and the honest answer is the current
time. Comma-splitting must not break that.
"""

from __future__ import annotations

import datetime as dt
import zoneinfo

import pytest

from core.agent_runtime.fast_paths_utility import (
    _relative_offset_binds_to_the_clock_ask,
    _segment_states_the_output_shape,
    date_time_fast_path,
)

#: A pinned instant, so the expectations below are arithmetic rather than wall-clock luck.
PIN = dt.datetime(2026, 8, 15, 22, 10, tzinfo=dt.timezone.utc)


def _answer(text: str) -> str:
    return date_time_fast_path(None, text, source_surface="api", now_utc=PIN) or ""


def _at(zone: str, minutes: int) -> str:
    return (PIN + dt.timedelta(minutes=minutes)).astimezone(zoneinfo.ZoneInfo(zone)).strftime("%H:%M")


# -------------------------------------------------------------------------------------------
# The reported miss and its family
# -------------------------------------------------------------------------------------------


def test_the_comma_shaped_turn_states_the_shifted_time() -> None:
    """The measured defect, verbatim."""

    answer = _answer("time in london 90 minutes from now, one word")

    assert _at("Europe/London", 90) in answer, answer


@pytest.mark.parametrize(
    ("text", "zone", "minutes"),
    (
        ("time in london 90 minutes from now, one word", "Europe/London", 90),
        ("whats the time in tokyo in 3 hours, one word", "Asia/Tokyo", 180),
        ("time in berlin in 2h, just the time please", "Europe/Berlin", 120),
        ("time in denver 2 hrs ago, one word", "America/Denver", -120),
    ),
)
def test_an_offset_survives_whatever_punctuation_precedes_the_shape(
    text: str, zone: str, minutes: int
) -> None:
    """Different cities, units, signs and shape wordings -- one rule."""

    assert _at(zone, minutes) in _answer(text), text


@pytest.mark.parametrize(
    ("text", "zone", "minutes"),
    (
        ("what time is it in vilnius in 45 mins, one word only", "Europe/Berlin", 45),
        ("time in tokyo in 2 hours, answer in one word", "Asia/Tokyo", 120),
    ),
)
def test_a_shape_this_lane_cannot_confirm_declines_rather_than_answering_now(
    text: str, zone: str, minutes: int
) -> None:
    """The BOUNDARY, stated honestly rather than papered over.

    With the offset now binding, these turns state a shape ("one word only") that the answer-shape
    parser does not bind on the whole sentence, so the lane cannot prove it can honour the stated
    form and declines -- handing the turn to a lane that can. Measured: it returns "".

    That is the acceptable direction and the assertion that matters: what must NEVER happen is the
    current clock presented as the answer to a question that asked for a shifted one. Answering
    the shifted time would also pass here; answering `now` is the defect.
    """

    answer = _answer(text)

    assert _at(zone, 0) not in answer, f"answered the CURRENT time for a shifted ask: {answer!r}"
    assert answer == "" or _at(zone, minutes) in answer, answer


def test_the_period_shaped_turn_still_works() -> None:
    """The wording the original repair was built against must not regress."""

    answer = _answer("what time is it in 5h from now in Tokyo? Output exactly one word.")

    assert _at("Asia/Tokyo", 300) in answer


# -------------------------------------------------------------------------------------------
# Negative controls -- the guard must keep doing its job
# -------------------------------------------------------------------------------------------


def test_an_offset_owned_by_another_clause_still_answers_now() -> None:
    """The case the guard exists for: the deploy takes 5 hours; the ask is the current time.

    If comma-splitting made every offset bind, this would answer five hours from now -- a new
    confidently wrong answer traded for the old one.
    """

    answer = _answer("the deploy finishes in 5 hours, what time is it in london")

    assert _at("Europe/London", 0) in answer
    assert _at("Europe/London", 300) not in answer


def test_the_guard_still_rejects_a_substantive_trailing_clause() -> None:
    assert _relative_offset_binds_to_the_clock_ask("the deploy finishes , what time is it") is False


@pytest.mark.parametrize(
    "text",
    (
        "how many minutes is 5h?",
        "what time is the meeting in 3 days",
        "meet me in 5 High Street",
    ),
)
def test_a_non_clock_turn_is_still_declined(text: str) -> None:
    """Declining hands the turn to a lane that can reason; answering a clock here would be wrong."""

    assert _answer(text) == ""


# -------------------------------------------------------------------------------------------
# The recognizer itself
# -------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "segment", ("one word", "output exactly one word", "no markdown", "answer in one word")
)
def test_an_output_shape_segment_is_recognized(segment: str) -> None:
    assert _segment_states_the_output_shape(segment) is True, segment


@pytest.mark.parametrize(
    "segment", ("the deploy finishes", "time in london", "what time is it", "my flight lands")
)
def test_a_real_clause_is_never_mistaken_for_an_output_shape(segment: str) -> None:
    """The direction that matters: mistaking a clause for a shape instruction would let a
    genuinely misattached offset bind, and the lane would answer a shifted time for a question
    that asked for now."""

    assert _segment_states_the_output_shape(segment) is False, segment
