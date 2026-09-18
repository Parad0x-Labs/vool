"""A relative offset is part of the question -- computed, or declined, never dropped.

Measured on the served surface, 2026-08-14 at 01:23 EEST::

    U: what time is it in 5h from now in Tokyo? Output exactly one word.
    A: 07:23        (the CURRENT Tokyo time; the correct answer was 12:23)
    U: what time is it in 5h from now in berlin? Output exactly one word.
    A: 00:24        (the CURRENT Berlin time; the correct answer was 05:24)
       both: tool | date_time_fast_path | tool-generated answer, no model

No pattern in the module consumed the offset phrase, so the reference instant was hardwired to
now. Reordered phrasings were worse: "in 5 hours in tokyo what time is it" answered the MACHINE
LOCAL current clock -- wrong instant AND wrong zone -- because "in tokyo" was no longer
phrase-final and the place was lost entirely.

THE INVARIANT these tests defend, not the phrase "5h from now":

    a clock turn carrying a relative offset gets now_utc +/- offset rendered in the named
    place's zone (machine-local when none is named), or NO answer from this lane at all.
    A shift that is understood but not computable here (calendar-scale units, ambiguous
    offsets) DECLINES -- the one answer that is always wrong is the current clock.

Arithmetic is on the UTC instant, conversion to the zone afterwards, which the DST tests pin
across both Europe/Berlin transitions. The clock is pinned through the same keyword-only
``now_utc`` seam the module exposes for tests; no test races the wall clock.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

from core.agent_runtime.fast_paths_utility import (
    date_time_fast_path,
    extract_relative_clock_offset,
    extract_utility_timezone,
)

#: 2026-08-14 22:23 UTC == 01:23 EEST on the reporting machine -- the exact reported instant.
PIN = datetime(2026, 8, 14, 22, 23, tzinfo=timezone.utc)


def _answer(text: str, *, at: datetime = PIN) -> str | None:
    return date_time_fast_path(None, text, source_surface="api", now_utc=at)


def _clock_values(answer: str) -> list[str]:
    return re.findall(r"\b(\d{2}:\d{2})\b", answer)


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


# ---------------------------------------------------------------------------------------------
# G1 -- the two reported turns, verbatim, one-word contract intact
# ---------------------------------------------------------------------------------------------


def test_the_reported_tokyo_turn_returns_the_shifted_instant_in_one_word() -> None:
    """At 22:23 UTC Tokyo is 07:23; five hours from now is 12:23. The live answer was 07:23."""

    answer = _answer("what time is it in 5h from now in Tokyo? Output exactly one word.")

    assert answer == "12:23"


def test_the_reported_berlin_turn_returns_the_shifted_instant_in_one_word() -> None:
    answer = _answer("what time is it in 5h from now in berlin? Output exactly one word.")

    assert answer == "05:23"


# ---------------------------------------------------------------------------------------------
# G2 -- paraphrases: orderings, unit spellings, from now / later / ago, minutes
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        # orderings that used to lose the PLACE as well as the offset
        ("in 5 hours in tokyo what time is it", "will be 12:23 JST"),
        ("what time will it be in tokyo in 5 hours", "will be 12:23 JST"),
        # unit spellings, attached and spaced
        ("what time is it in 5h from now in berlin", "will be 05:23 CEST"),
        ("time in tokyo in 3 hrs", "will be 10:23 JST"),
        ("what time will it be in berlin in 1 hr", "will be 01:23 CEST"),
        # "later" as the frame
        ("what time will it be in tokyo 2 hours later", "will be 09:23 JST"),
        # minutes, including the named-place composition
        ("what time is it in 45 min in new york", "will be 19:08 EDT"),
        ("in 30 minutes what time will it be in sao paulo", "will be 19:53"),
        # "ago" subtracts, in both orderings
        ("what time was it 2h ago in berlin", "was 22:23 CEST"),
        ("2h ago in berlin what time was it", "was 22:23 CEST"),
    ),
)
def test_paraphrases_compute_the_offset_in_the_target_zone(text: str, expected: str) -> None:
    answer = _answer(text)

    assert answer is not None, text
    assert expected in answer, (text, answer)


@pytest.mark.parametrize(
    ("text", "delta_minutes"),
    (
        ("what time is it 5 hours from now", 300),
        ("what time is it in 90 minutes", 90),
        ("what time is it in half an hour", 30),
        ("what time is it in an hour", 60),
        ("what time is it in 5 hrs", 300),
        ("what time is it 5 h from now", 300),
    ),
)
def test_no_place_applies_the_offset_to_the_machine_local_clock(text: str, delta_minutes: int) -> None:
    """No place named -> machine-local zone with the offset applied, asserted two ways.

    First structurally (the two clock values in the answer differ by exactly the offset), then
    against an independent zoneinfo computation of the pinned instant, so the test holds on any
    machine in any zone or season.
    """

    answer = _answer(text)

    assert answer is not None, text
    values = _clock_values(answer)
    assert len(values) == 2, (text, answer)
    assert (_minutes(values[1]) - _minutes(values[0])) % (24 * 60) == delta_minutes % (24 * 60)
    expected = (PIN + timedelta(minutes=delta_minutes)).astimezone().strftime("%H:%M")
    assert values[1] == expected, (text, answer)


def test_the_prose_answer_states_the_shift_never_bare_current_time() -> None:
    """ "Current time is X" alone for a shifted instant is the dropped-offset defect itself."""

    answer = _answer("what time is it in 5h from now in berlin")

    assert answer == "Current time in Berlin is 00:23 CEST; in 5 hours it will be 05:23 CEST."


# ---------------------------------------------------------------------------------------------
# G3 -- day rollover and DST: UTC arithmetic first, zone conversion second
# ---------------------------------------------------------------------------------------------


def test_plus_five_hours_over_midnight_in_berlin_shows_the_post_midnight_time() -> None:
    pinned = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)  # 22:00 in Berlin

    answer = _answer("what time will it be in berlin in 5 hours", at=pinned)

    assert answer is not None
    assert "will be 03:00 CEST" in answer


def test_the_date_less_one_word_answer_over_midnight_is_still_just_the_clock() -> None:
    pinned = datetime(2026, 8, 14, 20, 0, tzinfo=timezone.utc)

    answer = _answer("what time will it be in berlin in 5 hours? Output exactly one word.", at=pinned)

    assert answer == "03:00"


def test_fall_back_transition_shifts_through_utc_not_wall_clock() -> None:
    """Berlin falls back 2026-10-25 (01:00 UTC). 01:30 CEST + 5h is 05:30 CET, never 06:30."""

    pinned = datetime(2026, 10, 24, 23, 30, tzinfo=timezone.utc)  # 01:30 CEST in Berlin

    answer = _answer("what time will it be in berlin in 5 hours", at=pinned)

    assert answer is not None
    assert "05:30 CET" in answer
    assert "06:30" not in answer


def test_spring_forward_transition_shifts_through_utc_not_wall_clock() -> None:
    """Berlin springs forward 2026-03-29 (01:00 UTC). 01:30 CET + 2h is 04:30 CEST, never 03:30."""

    pinned = datetime(2026, 3, 29, 0, 30, tzinfo=timezone.utc)  # 01:30 CET in Berlin

    answer = _answer("what time will it be in berlin in 2 hours", at=pinned)

    assert answer is not None
    assert "04:30 CEST" in answer
    assert "03:30" not in answer


# ---------------------------------------------------------------------------------------------
# G4 -- understood but not computable: DECLINE, never the current clock
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        # calendar-scale units change the DATE; this lane declines to the next one.
        # Before the repair this answered the machine's CURRENT local time.
        "what time will it be in 3 days",
        "what time will it be in tokyo in 2 weeks",
        "what time was it yesterday at this time in tokyo",
        "yesterday at this time in tokyo",
        # two competing offsets make the reference instant ambiguous
        "what time is it in 2 hours or in 3 hours",
    ),
)
def test_a_shift_this_lane_cannot_compute_declines_the_claim(text: str) -> None:
    assert _answer(text) is None, text


# ---------------------------------------------------------------------------------------------
# G5 -- negative controls: no offset means nothing changed, and near-misses stay out
# ---------------------------------------------------------------------------------------------


def test_a_plain_what_time_turn_is_unchanged() -> None:
    answer = _answer("what time is it")

    expected = PIN.astimezone().strftime("Current time is %H:%M %Z.")
    assert answer == expected
    assert "will be" not in answer


def test_a_street_number_is_not_an_offset() -> None:
    """ "in 5 High Street" has no unit token: same plain current-time answer as before."""

    answer = _answer("what time is it in 5 High Street")

    assert answer == PIN.astimezone().strftime("Current time is %H:%M %Z.")


def test_a_duration_question_is_not_a_clock_ask() -> None:
    assert _answer("how long is 5h in minutes") is None


def test_a_scheduled_event_in_n_days_is_not_a_clock() -> None:
    assert _answer("what time is the meeting in 3 days") is None


def test_an_offset_bound_to_another_clause_keeps_the_plain_now_answer() -> None:
    """ "the deploy finishes in 5 hours, what time is it" wants NOW; the offset is the deploy's."""

    answer = _answer("the deploy finishes in 5 hours, what time is it")

    assert answer == PIN.astimezone().strftime("Current time is %H:%M %Z.")
    assert "will be" not in answer


def test_a_flight_duration_next_to_a_place_is_not_an_offset() -> None:
    assert _answer("5h flight to tokyo lands at what time") is None


def test_an_offset_free_turn_produces_no_offset_object() -> None:
    """base == cleaned by construction when no offset phrase exists: the identity regression."""

    assert extract_relative_clock_offset("what time is it in tokyo") is None
    assert extract_relative_clock_offset("what time is it") is None
    answer = _answer("what time is it in tokyo")
    assert answer == PIN.astimezone(ZoneInfo("Asia/Tokyo")).strftime("Current time in Tokyo is %H:%M %Z.")


# ---------------------------------------------------------------------------------------------
# G6 -- the follow-up seam: the zone survives offset phrasings for the stored payload
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    (
        "what time is it in 5h from now in tokyo? output exactly one word",
        "in 5 hours in tokyo what time is it",
        "what time will it be in tokyo in 5 hours",
    ),
)
def test_zone_extraction_survives_every_offset_ordering(text: str) -> None:
    """`turn_frontdoor` re-extracts the zone from the raw turn to store the follow-up payload;
    an offset phrasing must not make it store an empty timezone."""

    assert extract_utility_timezone(text) == ("Asia/Tokyo", "Tokyo")
