"""Reminders read the times people say (tool-seating Job 5, audit R5; Job 5b fixes).

A bare hour after "at" ("tomorrow at 9"), a part of the day ("next tuesday afternoon",
"this evening", "tonight"), a day of the month with no month ("on the 3rd at 11") and a past
day word ("remind me yesterday ...") are read the way people say them, per the reviewer's
rules. Numbers that are not times ("buy 9 eggs", "the 3rd time") never become clock times,
and a stored reminder's subject stays the thing to do, not the schedule.

Job 5b: an ordinal or bare hour is a time only when what FOLLOWS it is not a noun ("the 2nd
show", "at 9 Elm Street"), so a stated day and time survive an ordinal in the subject;
"at 12 tonight" is the midnight that ends tonight; "last night" and "last friday" are past
days; "the 30th of next month" names the month; "at 9.30" after "at" is 9:30.

Job 5c: midnight in any words ("midnight", "12am", "12 midnight", "00:00") said with today
or tonight is 00:00 the next day, in any word order, with and without "at"; a date the
sentence names keeps its own day. An ordinal followed directly by a part-of-day noun
("the 3rd night", "the 2nd morning") counts a thing, not a date, while "the 3rd at night"
and "the 3rd in the evening" stay dates; and the stored subject drops a leading decimal
clock ("at 9.30 tomorrow to leave" stores "leave").

Job 6d: the compound "an hour and a half" reads as 90 minutes (it stayed refused in
Job 6c), and a midnight lead leaves the stored subject whole ("remind me tonight at
midnight to lock the shed" stores "lock the shed").

LABELLED: injected clock (both the UTC and the zoned door), isolated home; no live provider.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import pytest

from tests.pa_beta_gate._pc_calendar_rig import Clock, prepare_home

pytestmark = [pytest.mark.pa_beta]

T0 = datetime(2026, 10, 28, 8, 0, tzinfo=timezone.utc)  # Wednesday 10:00 in Europe/Athens (EET, UTC+2)
ATHENS = ZoneInfo("Europe/Athens")
NOW_ATHENS = T0.astimezone(ATHENS)


@pytest.fixture
def clock(tmp_path, monkeypatch):
    prepare_home(tmp_path, monkeypatch)
    from core.time_authority import CLOCK

    fixed = Clock(T0, monkeypatch)
    monkeypatch.setattr(CLOCK, "now_for_timezone", lambda name: fixed.now.astimezone(ZoneInfo(name)))
    return fixed


def _run(text: str, *, session_id: str):
    from core.local_operator_actions import dispatch_operator_action, parse_operator_action_intent

    intent = parse_operator_action_intent(text)
    assert intent is not None, f"parser missed a reminder request: {text!r}"
    return dispatch_operator_action(intent, task_id=f"task-{uuid.uuid4().hex[:8]}", session_id=session_id)


def _pending(session_id: str):
    from core.operator.reminders import list_reminders
    from storage.db import get_connection

    return list_reminders(session_id=session_id, get_connection_fn=get_connection)


def _parse(text: str, *, now: datetime | None = None):
    from core.operator.when import parse_when_expression

    return parse_when_expression(text, now_fn=lambda: now or NOW_ATHENS)


# ---------------------------------------------------------------------------
# The audit asks, end to end through dispatch (Wednesday 10:00 Europe/Athens)
# ---------------------------------------------------------------------------

AUDIT_ASKS = [
    # (text, expected due_at_utc prefix)
    ("remind me tomorrow at 9", "2026-10-29T07:00"),                    # 9:00 EET, the time was just given
    ("remind me next tuesday afternoon to call mom", "2026-11-03T13:00"),  # Nov 3 15:00 EET
    ("remind me on the 3rd at 11 about the accountant", "2026-11-03T09:00"),  # Oct 3 passed -> Nov 3, 11:00 EET
    ("remind me this evening at 7", "2026-10-28T17:00"),                # today 19:00 EET
    ("remind me tonight", "2026-10-28T18:00"),                          # today 20:00 EET
]


@pytest.mark.parametrize("text,due_prefix", AUDIT_ASKS)
def test_the_audit_asks_read_the_time_the_person_said(clock, text, due_prefix):
    session = f"audit-{uuid.uuid4().hex[:6]}"
    result = _run(text, session_id=session)
    assert result.ok, result.response_text
    [row] = _pending(session)
    assert row["due_at_utc"].startswith(due_prefix), (row["due_at_utc"], row["note"])


def test_a_past_day_word_is_not_offered_a_time(clock):
    result = _run("remind me yesterday to email sarah", session_id="pastday")
    assert not result.ok and result.status == "invalid_request", result.response_text
    assert "already passed" in result.response_text, result.response_text
    assert "Which time on yesterday" not in result.response_text
    assert "yesterday at" not in result.response_text  # never suggests a time on the past day
    assert _pending("pastday") == []


# ---------------------------------------------------------------------------
# Rule A: a bare hour after "at"
# ---------------------------------------------------------------------------

RULE_A_ROWS = [
    # (text, expected due_at_utc prefix) -- Wednesday 10:00 Athens clock
    ("remind me tomorrow at 9", "2026-10-29T07:00"),        # 7-11 with no part word: the morning
    ("remind me tomorrow at 11", "2026-10-29T09:00"),
    ("remind me tomorrow at 12", "2026-10-29T10:00"),       # 12 is noon
    ("remind me tomorrow at 5", "2026-10-29T15:00"),        # 1-6 with no part word: 13:00-18:00
    ("remind me tomorrow at 1", "2026-10-29T11:00"),
    ("remind me tomorrow at 21", "2026-10-29T19:00"),       # 13-23 is the 24-hour clock
    ("remind me tomorrow at 23", "2026-10-29T21:00"),
    ("remind me at 9 tonight", "2026-10-28T19:00"),         # a part-of-day word decides: 21:00 EET
    ("remind me tomorrow at 7 in the evening", "2026-10-29T17:00"),
    ("remind me tomorrow at 3 in the afternoon", "2026-10-29T13:00"),
    ("remind me tomorrow at 10 in the morning", "2026-10-29T08:00"),
    ("remind me on friday at 6", "2026-10-30T16:00"),       # 1-6 -> 18:00 EET on Friday Oct 30
]


@pytest.mark.parametrize("text,due_prefix", RULE_A_ROWS)
def test_a_bare_hour_after_at_is_read(clock, text, due_prefix):
    reading = _parse(text)
    assert reading.ok, reading.problem
    assert reading.due_at_utc.startswith(due_prefix), (text, reading.due_at_utc)


# ---------------------------------------------------------------------------
# Rule B: a part of the day with no clock time
# ---------------------------------------------------------------------------

RULE_B_ROWS = [
    ("remind me tomorrow morning", "2026-10-29T07:00"),     # 09:00 EET
    ("remind me next tuesday afternoon to call mom", "2026-11-03T13:00"),  # 15:00 EET
    ("remind me tomorrow evening", "2026-10-29T16:00"),     # 18:00 EET
    ("remind me tomorrow night", "2026-10-29T18:00"),       # 20:00 EET
    ("remind me this evening", "2026-10-28T16:00"),         # this evening is today: 18:00 EET
    ("remind me tomorrow at noon", "2026-10-29T10:00"),     # noon with a day word
    ("remind me tomorrow at midday", "2026-10-29T10:00"),
    ("remind me at noon", "2026-10-28T10:00"),              # bare part word: today 12:00 EET, still ahead
]


@pytest.mark.parametrize("text,due_prefix", RULE_B_ROWS)
def test_a_part_of_the_day_with_no_clock_time_is_read(clock, text, due_prefix):
    reading = _parse(text)
    assert reading.ok, reading.problem
    assert reading.due_at_utc.startswith(due_prefix), (text, reading.due_at_utc)


def test_this_evening_never_rolls_to_tomorrow(clock):
    """Said at 23:00, "this evening" is still today: an honest past time the reminder flow
    refuses, not a silent move to tomorrow evening."""
    late = datetime(2026, 10, 28, 21, 0, tzinfo=timezone.utc).astimezone(ATHENS)  # 23:00 Athens
    reading = _parse("remind me this evening at 7", now=late)
    assert reading.ok and reading.due_at_utc.startswith("2026-10-28T17:00"), reading
    from core.operator.when import has_passed

    assert has_passed(reading)


# ---------------------------------------------------------------------------
# Rule C: a day of the month with no month
# ---------------------------------------------------------------------------

NOVEMBER = datetime(2026, 11, 10, 8, 0, tzinfo=timezone.utc).astimezone(ATHENS)   # Tue 10:00 Athens
LATE_JANUARY = datetime(2027, 1, 31, 21, 30, tzinfo=timezone.utc).astimezone(ATHENS)  # Jan 31 23:30 Athens

RULE_C_ROWS = [
    # (text, now, ok, due prefix or problem substring)
    ("remind me on the 3rd at 11 about the accountant", None, True, "2026-11-03T09:00"),  # Oct 3 passed -> Nov 3
    ("remind me the 21st at 11", None, True, "2026-11-21T09:00"),                        # Oct 21 passed -> Nov 21
    ("remind me on the 30th at 9", None, True, "2026-10-30T07:00"),                       # still ahead this month
    ("remind me on the 15th", None, False, "which time on the 15th"),                     # no clock time: asked, not dated
    ("remind me on the 31st at 9", NOVEMBER, False, "november has no day 31"),            # never moved to another day
    ("remind me on the 30th at 10", LATE_JANUARY, False, "february has no day 30"),       # Jan 30 passed -> Feb lacks it
    ("remind me on the 3rd tomorrow at 11", None, False, "is not tomorrow"),               # a day word that disagrees
]


@pytest.mark.parametrize("text,now,ok,expected", RULE_C_ROWS)
def test_a_day_of_the_month_with_no_month_is_read(clock, text, now, ok, expected):
    reading = _parse(text, now=now)
    assert reading.ok is ok, (text, reading.problem or reading.due_at_utc)
    if ok:
        assert reading.due_at_utc.startswith(expected), (text, reading.due_at_utc)
    else:
        assert expected in reading.problem.lower(), (text, reading.problem)


def test_an_ordinal_that_counts_or_names_is_not_a_date(clock):
    for text in ("the 3rd time", "my 2nd attempt", "the 4th floor", "the 2nd half", "the 1st round"):
        reading = _parse(f"remind me tomorrow to note {text} it happens")
        assert not reading.ok, text
        assert "which time on tomorrow" in reading.problem.lower(), (text, reading.problem)


# ---------------------------------------------------------------------------
# Rule D: a past day word with no clock time
# ---------------------------------------------------------------------------

RULE_D_ROWS = [
    "remind me yesterday to email sarah",
    "remind me yesterday morning to email sarah",
    "remind me yesterday evening",
]


@pytest.mark.parametrize("text", RULE_D_ROWS)
def test_a_past_day_word_asks_for_a_future_time(clock, text):
    reading = _parse(text)
    assert not reading.ok, text
    assert "already passed" in reading.problem, (text, reading.problem)
    assert "which time on yesterday" not in reading.problem.lower()
    # the suggestion is a future one, never a time on the past day
    assert "yesterday at 9:00" not in reading.problem.lower(), reading.problem


def test_a_past_day_with_a_clock_time_is_still_read_for_callers_that_need_one(clock):
    """A calendar search or move may name a past time; only reminder callers refuse it."""
    from core.operator.when import has_passed

    reading = _parse("remind me yesterday at 9:00")
    assert reading.ok and reading.due_at_utc.startswith("2026-10-27T07:00"), reading
    assert has_passed(reading)


# ---------------------------------------------------------------------------
# Numbers that are not times
# ---------------------------------------------------------------------------

NOT_A_TIME_ROWS = [
    # (text, ok, expected due prefix or problem substring)
    ("remind me to buy 9 eggs tomorrow", False, "which time on tomorrow"),   # 9 is not after "at"
    ("remind me tomorrow to drink 2 litres of water", False, "which time on tomorrow"),
    ("remind me to transfer 500 euros on friday", False, "which time on friday"),
    ("remind me tomorrow to note the 3rd time it crashes", False, "which time on tomorrow"),
    ("remind me on monday about my 2nd attempt at the draft", False, "which time on monday"),
    ("remind me tomorrow to avoid the 4th floor", False, "which time on tomorrow"),
    ("remind me tomorrow to meet at 9th street", False, "which time on tomorrow"),  # an ordinal after "at" is an address
    ("remind me next week to read chapter 7", False, "could not read a date or time"),
    ("remind me to catch bus 42 tomorrow", False, "which time on tomorrow"),
    ("remind me tomorrow to watch the top 10 videos", False, "which time on tomorrow"),
    ("remind me to call 0800 123 456 tomorrow", False, "which time on tomorrow"),
    ("remind me in 2027 to renew my passport", False, "could not read a date or time"),
    ("remind me tomorrow at 9 to book a table for 4", True, "2026-10-29T07:00"),    # 4 is diners, 9 is the time
    ("remind me tomorrow at 7 to run 5 km", True, "2026-10-29T05:00"),              # 5 is kilometres
    ("remind me tomorrow at 21 to sleep", True, "2026-10-29T19:00"),                # control: after "at" IS the time
    ("remind me tomorrow at 9.30 to stretch", True, "2026-10-29T07:30"),            # after "at", 9.30 is 9:30
    ("remind me at 9.45 tomorrow to leave", True, "2026-10-29T07:45"),
    ("remind me at 9.30 tonight to leave", True, "2026-10-28T19:30"),               # the part word still decides
    ("remind me at 8.15 pm tomorrow to shut the studio", True, "2026-10-29T18:15"),
    ("remind me to run at 9.5 km tomorrow", False, "which time on tomorrow"),       # one decimal digit is a quantity
    ("remind me tomorrow at 9.30 euros to pay", False, "which time on tomorrow"),   # a noun after the decimal
]


# ---------------------------------------------------------------------------
# Job 5b: the reviewer's sentences (S1-S6)
# ---------------------------------------------------------------------------

JOB5B_REVIEW_ROWS = [
    # (text, ok, expected due prefix or problem substring)
    ("remind me tomorrow at 9am to buy tickets for the 2nd show", True, "2026-10-29T07:00"),   # S1: keeps tomorrow
    ("remind me at 9am to bring the 1st aid kit", True, "2026-10-29T07:00"),                   # S1: 9am passed -> tomorrow
    ("remind me tomorrow at 9 to buy tickets for the 2nd show", True, "2026-10-29T07:00"),     # S1 with a bare hour
    ("I live at 9 Elm Street, remind me tomorrow at 10", True, "2026-10-29T08:00"),           # S2: not the first "at 9"
    ("remind me at 12 tonight to take the bins out", True, "2026-10-28T22:00"),                # S3: midnight ends tonight
    ("remind me tonight at 12 to take the bins out", True, "2026-10-28T22:00"),
    ("remind me at 12 this evening to lock up", True, "2026-10-28T22:00"),
    ("remind me last friday to lock up", False, "already passed"),                             # S4: as for "yesterday"
    ("remind me the 30th of next month at 9", True, "2026-11-30T07:00"),                       # S5: next month is November
    ("remind me at 9.30 tomorrow to leave", True, "2026-10-29T07:30"),                         # S6: 9.30 after "at"
]


@pytest.mark.parametrize("text,ok,expected", JOB5B_REVIEW_ROWS)
def test_the_reviewers_fresh_sentences_read_the_stated_day_and_time(clock, text, ok, expected):
    reading = _parse(text)
    assert reading.ok is ok, (text, reading.problem or reading.due_at_utc)
    if ok:
        assert reading.due_at_utc.startswith(expected), (text, reading.due_at_utc)
    else:
        assert expected in reading.problem.lower(), (text, reading.problem)


# ---------------------------------------------------------------------------
# Job 5c: midnight in every wording, with today or tonight, in any order
# ---------------------------------------------------------------------------

MIDNIGHT_ROWS = [
    # (text, expected due_at_utc prefix) -- all 00:00 on 29 October EET = 28 Oct 22:00 UTC
    ("remind me tonight at midnight to post the letter", "2026-10-28T22:00"),   # was 20:00
    ("remind me at midnight tonight to lock up", "2026-10-28T22:00"),           # word before the day
    ("remind me at 12am tonight to lock up", "2026-10-28T22:00"),               # was refused as passed
    ("remind me tonight at 12am to lock up", "2026-10-28T22:00"),
    ("remind me 12am tonight to take the bins out", "2026-10-28T22:00"),        # no "at" at all
    ("remind me midnight tonight to check the door", "2026-10-28T22:00"),
    ("remind me today at midnight to water the plants", "2026-10-28T22:00"),
    ("remind me at midnight today to water the plants", "2026-10-28T22:00"),
    ("remind me at 00:00 tonight to switch the meter", "2026-10-28T22:00"),
    ("remind me tonight at 00:00 to switch the meter", "2026-10-28T22:00"),
    ("remind me at 12 midnight tonight to take the pills", "2026-10-28T22:00"),
    ("remind me tonight at 12 midnight to take the pills", "2026-10-28T22:00"),
    ("remind me at 12.00 am tonight to lock the studio", "2026-10-28T22:00"),
    ("remind me this evening at 12am to shut the shop", "2026-10-28T22:00"),    # the anchored branch
    ("remind me this evening at midnight to shut the shop", "2026-10-28T22:00"),
    ("remind me at midnight", "2026-10-28T22:00"),                              # alone: today's has passed
    # a day the sentence names keeps its own midnight
    ("remind me tomorrow at midnight to post the letter", "2026-10-28T22:00"),  # 00:00 ON tomorrow
    ("remind me on the 3rd at midnight about the accountant", "2026-11-02T22:00"),
    # midnight as a label of a thing never moves the time the person gave
    ("remind me tonight at 9 about the midnight launch", "2026-10-28T19:00"),
    ("remind me tonight at 12pm to eat with the team", "2026-10-28T10:00"),     # 12pm is noon today
]


@pytest.mark.parametrize("text,due_prefix", MIDNIGHT_ROWS)
def test_midnight_in_any_words_ends_the_day_it_is_said_with(clock, text, due_prefix):
    reading = _parse(text)
    assert reading.ok, (text, reading.problem)
    assert reading.due_at_utc.startswith(due_prefix), (text, reading.due_at_utc)


def test_midnight_tonight_stores_the_row_for_tomorrow(clock):
    session = f"midnight-{uuid.uuid4().hex[:6]}"
    result = _run("remind me tonight at midnight to post the letter", session_id=session)
    assert result.ok, result.response_text
    [row] = _pending(session)
    assert row["due_at_utc"].startswith("2026-10-28T22:00"), row["due_at_utc"]  # 29 Oct 00:00 EET


# ---------------------------------------------------------------------------
# Job 5c: an ordinal directly before a part-of-day noun counts a thing
# ---------------------------------------------------------------------------

ORDINAL_PART_ROWS = [
    # (text, ok, expected due prefix or problem substring)
    ("remind me tomorrow at 10 to book the 3rd night at the hotel", True, "2026-10-29T08:00"),
    ("remind me next monday at 9 for the 2nd morning of the course", True, "2026-11-02T07:00"),
    ("remind me on friday at 6 to close the 2nd night shift roster", True, "2026-10-30T16:00"),
    ("remind me tomorrow at 11 to confirm the 1st evening of the fair", True, "2026-10-29T09:00"),
    ("remind me tomorrow to book the 2nd morning slot", False, "which time on tomorrow"),
    # with its own preposition the part word still names a time on the date
    ("remind me on the 3rd at night to take the bins out", True, "2026-11-03T18:00"),
    ("remind me on the 3rd in the evening about the accountant", True, "2026-11-03T16:00"),
    ("remind me on the 21st in the morning to file the report", True, "2026-11-21T07:00"),
]


@pytest.mark.parametrize("text,ok,expected", ORDINAL_PART_ROWS)
def test_an_ordinal_before_a_part_of_day_noun_is_a_thing_not_a_date(clock, text, ok, expected):
    reading = _parse(text)
    assert reading.ok is ok, (text, reading.problem or reading.due_at_utc)
    if ok:
        assert reading.due_at_utc.startswith(expected), (text, reading.due_at_utc)
    else:
        assert expected in reading.problem.lower(), (text, reading.problem)


def test_last_night_stores_nothing_and_never_suggests_a_time_on_it(clock):
    result = _run("remind me last night to lock up", session_id="lastnight")
    assert not result.ok and result.status == "invalid_request", result.response_text
    assert "already passed" in result.response_text, result.response_text
    assert "last night at" not in result.response_text  # never suggests a time on the past day
    assert _pending("lastnight") == []


PAST_DAY_ROWS = [
    "remind me last night to lock up",
    "remind me last friday to lock up",
    "remind me last wednesday to call mom",
    "remind me last sunday to water the plants",
]


@pytest.mark.parametrize("text", PAST_DAY_ROWS)
def test_last_day_words_ask_for_a_future_time(clock, text):
    reading = _parse(text)
    assert not reading.ok, text
    assert "already passed" in reading.problem, (text, reading.problem)


def test_a_last_day_with_a_clock_time_is_still_read_for_callers_that_need_one(clock):
    """A calendar search may name a past time ("last friday at 9:00"); only reminder callers
    refuse it through has_passed."""
    from core.operator.when import has_passed

    reading = _parse("remind me last friday at 9:00 to lock up")
    assert reading.ok and reading.due_at_utc.startswith("2026-10-23T06:00"), reading  # Oct 23 is EEST (UTC+3)
    assert has_passed(reading)
    night = _parse("remind me last night at 10 to lock the studio")  # "night" makes it 22:00
    assert night.ok and night.due_at_utc.startswith("2026-10-27T20:00"), night
    assert has_passed(night)


MONTH_OF_ROWS = [
    # (text, now, ok, due prefix or problem substring)
    ("remind me the 30th of next month at 9", None, True, "2026-11-30T07:00"),
    ("remind me the 30th of this month at 9", None, True, "2026-10-30T07:00"),            # still ahead this month
    ("remind me the 5th of this month at 9", None, True, "2026-10-05T06:00"),             # past: read as said (EEST)
    ("remind me the 31st of next month at 9", None, False, "november has no day 31"),     # asked about, never moved
    ("remind me on the 31st of this month at 9", None, True, "2026-10-31T07:00"),
]


@pytest.mark.parametrize("text,now,ok,expected", MONTH_OF_ROWS)
def test_of_this_or_next_month_names_the_month(clock, text, now, ok, expected):
    reading = _parse(text, now=now)
    assert reading.ok is ok, (text, reading.problem or reading.due_at_utc)
    if ok:
        assert reading.due_at_utc.startswith(expected), (text, reading.due_at_utc)
    else:
        assert expected in reading.problem.lower(), (text, reading.problem)


def test_this_months_past_day_is_refused_not_rolled(clock):
    from core.operator.when import has_passed

    reading = _parse("remind me the 5th of this month at 9")
    assert reading.ok, reading.problem  # "this month" is named: never rolled into November
    assert has_passed(reading)


# ---------------------------------------------------------------------------
# Job 5b: a stated day and time survive an ordinal or number in the subject
# ---------------------------------------------------------------------------

ORDINAL_SUBJECT_ROWS = [
    # (text, expected due prefix) -- Wednesday 10:00 Athens clock, Job 5 bare-hour rules
    ("remind me on friday at 6 to rewatch the 1st episode", "2026-10-30T16:00"),          # 1-6 -> 18:00
    ("remind me tomorrow at 5pm to file the 2nd invoice", "2026-10-29T15:00"),
    ("remind me next monday at 9 to pay the 1st installment of the loan", "2026-11-02T07:00"),
    ("remind me tomorrow at 10 to book the 3rd row seats", "2026-10-29T08:00"),
    ("remind me on saturday at 11 for the 4th round of shots", "2026-10-31T09:00"),
    ("remind me tomorrow at 7 in the evening to drop off the 2nd draft", "2026-10-29T17:00"),
    ("remind me at 9 tonight to watch the 5th episode", "2026-10-28T19:00"),
    ("remind me tomorrow at 21 to set the 2nd alarm", "2026-10-29T19:00"),
    ("remind me on the 5th at 8 to renew the 2nd subscription", "2026-11-05T06:00"),
    ("remind me friday at 3 to pick up the 3rd prize", "2026-10-30T13:00"),
    ("remind me tomorrow at noon to start the 2nd load of laundry", "2026-10-29T10:00"),
    ("remind me this evening at 7 to water the 2nd batch of seedlings", "2026-10-28T17:00"),
    ("remind me next tuesday at 9am to submit the 2nd timesheet", "2026-11-03T07:00"),
    ("remind me tomorrow at 9.30 to call about the 3rd invoice", "2026-10-29T07:30"),
    ("remind me on thursday at 17 to open the 2nd window", "2026-10-29T15:00"),
    ("remind me at 6 tomorrow to take the 3rd pill", "2026-10-29T16:00"),
    ("remind me on sunday at 10 to defrost the 2nd turkey", "2026-11-01T08:00"),
    ("remind me tomorrow at 2pm to return the 2nd library book", "2026-10-29T12:00"),
    ("remind me tonight at 8 to check the 2nd oven", "2026-10-28T18:00"),
    ("remind me on wednesday at 9 to start the 6th chapter", "2026-11-04T07:00"),         # said on a wednesday
    ("remind me tomorrow morning at 8 to fold the 2nd load", "2026-10-29T06:00"),
    ("remind me in 20 minutes to log the 3rd rep", "2026-10-28T08:20"),
    ("remind me tomorrow at 12 to buy the 2nd raffle ticket", "2026-10-29T10:00"),        # 12 is noon
    ("remind me at 5 on friday to close the 3rd ticket", "2026-10-30T15:00"),
    ("remind me tomorrow at 9 to review the 2nd half of the report", "2026-10-29T07:00"),  # "half" is a noun
    ("remind me tomorrow at 8 to pack 2 bags for the trip", "2026-10-29T06:00"),           # a plain number too
]


@pytest.mark.parametrize("text,due_prefix", ORDINAL_SUBJECT_ROWS)
def test_an_ordinal_or_number_in_the_subject_keeps_the_stated_day_and_time(clock, text, due_prefix):
    reading = _parse(text)
    assert reading.ok, (text, reading.problem)
    assert reading.due_at_utc.startswith(due_prefix), (text, reading.due_at_utc)


def test_spans_keep_real_times_and_drop_the_noun_followers(clock):
    """The same structural rule at span level: what is not a time ("at 9 Elm", "the 2nd show")
    has no span; what is a time keeps it."""
    from core.operator.when import time_expression_spans

    def covered(sentence, **kwargs):
        return {sentence[start:end] for start, end in time_expression_spans(sentence, **kwargs)}

    elm = covered("I live at 9 Elm Street, remind me tomorrow at 10")
    assert "tomorrow" in elm and "at 10" in elm
    assert not any("at 9" in span for span in elm), elm
    show = covered("remind me tomorrow at 9am to buy tickets for the 2nd show")
    assert "tomorrow" in show and "at 9am" in show
    assert not any("2nd" in span for span in show), show
    third = covered("remind me on the 3rd at 11 about the accountant")
    assert any("the 3rd" in span for span in third) and "at 11" in third, third
    last = covered("remind me last friday at 9:00 to lock up")
    assert "last friday" in last and "at 9:00" in last, last
    night = covered("remind me tomorrow at 10 to book the 3rd night at the hotel")
    assert "tomorrow" in night and "at 10" in night, night
    assert not any("3rd" in span for span in night), night  # "the 3rd night" counts a thing


@pytest.mark.parametrize("text,ok,expected", NOT_A_TIME_ROWS)
def test_numbers_that_are_not_times_never_become_clock_times(clock, text, ok, expected):
    reading = _parse(text)
    assert reading.ok is ok, (text, reading.problem or reading.due_at_utc)
    if ok:
        assert reading.due_at_utc.startswith(expected), (text, reading.due_at_utc)
    else:
        assert expected in reading.problem.lower(), (text, reading.problem)


def test_a_number_that_is_not_a_time_stores_nothing(clock):
    result = _run("remind me to buy 9 eggs tomorrow", session_id="noisenum")
    assert not result.ok, result.response_text
    assert _pending("noisenum") == []


# ---------------------------------------------------------------------------
# The stored subject is the thing to do, not the schedule
# ---------------------------------------------------------------------------

SUBJECT_ROWS = [
    ("remind me tomorrow at 9 to call the garage", "call the garage"),
    ("remind me to call the garage at 9", "call the garage"),
    ("remind me next tuesday afternoon to call mom", "call mom"),
    ("remind me this evening at 7 to take the bins out", "take the bins out"),
    ("remind me on the 3rd at 11 about the accountant", "the accountant"),
    ("remind me tonight to check the oven", "check the oven"),
    ("remind me tomorrow at 21 to sleep", "sleep"),
    ("remind me at 9 tonight to take my pills", "take my pills"),
    ("remind me tomorrow at 9 to book a table for 4", "book a table for 4"),
    ("remind me tomorrow at 9am to buy tickets for the 2nd show", "buy tickets for the 2nd show"),
    ("remind me at 12 tonight to take the bins out", "take the bins out"),
    ("remind me at 9.30 tomorrow to leave", "leave"),                       # decimal clock stripped
    ("remind me at 8.15 pm tomorrow to shut the studio", "shut the studio"),
    # Job 6d: a midnight lead is a when-word; it leaves the subject whole (due 00:00 on
    # 29 Oct EET, per the midnight rows above).
    ("remind me tonight at midnight to lock the shed", "lock the shed"),
]
# "tomorrow at 9.30 to call mom" still keeps its when-head in the note: the decimal clock
# inside a when-head needs the module-level _LEADING_WHEN_RE in core/operator/handlers.py,
# which this job may not edit (Needs, Job 5c).


@pytest.mark.parametrize("text,subject", SUBJECT_ROWS)
def test_the_stored_subject_is_the_thing_to_do(clock, text, subject):
    session = f"subj-{uuid.uuid4().hex[:6]}"
    result = _run(text, session_id=session)
    assert result.ok, result.response_text
    [row] = _pending(session)
    assert row["note"] == subject, (text, row["note"])


# ---------------------------------------------------------------------------
# Job 6c: a wordy relative hour reads like the minute form
# ---------------------------------------------------------------------------

JOB6C_RELATIVE_ROWS = [
    # "in an hour" / "in half an hour" read like "in 60 minutes" / "in 30 minutes".
    ("can you nudge me in an hour to stand up", "2026-10-28T09:00"),
    ("remind me in half an hour to check the oven", "2026-10-28T08:30"),
    ("nudge me in an hour to stretch", "2026-10-28T09:00"),
    ("ping me in an hour about the parcel", "2026-10-28T09:00"),
    ("could you remind me in an hour to call mum", "2026-10-28T09:00"),
    # Job 6d: the compound reads too -- "an hour and a half" is 90 minutes after the
    # clock (10:00 Athens -> 11:30 = 09:30 UTC), in the same leads as the plain hour.
    ("remind me in an hour and a half to leave", "2026-10-28T09:30"),
    ("could you remind me in an hour and a half to call mum", "2026-10-28T09:30"),
    ("nudge me in an hour and a half to stretch", "2026-10-28T09:30"),
    ("remind me in an hour and a half about the parcel", "2026-10-28T09:30"),
    ("ping me in an hour and a half about the roast", "2026-10-28T09:30"),
]


@pytest.mark.parametrize("text,due_prefix", JOB6C_RELATIVE_ROWS)
def test_a_wordy_relative_hour_reads_like_the_minute_form(clock, text, due_prefix):
    reading = _parse(text)
    assert reading.ok, reading.problem
    assert reading.due_at_utc.startswith(due_prefix), (text, reading.due_at_utc)


def test_an_hour_and_a_half_reads_as_ninety_minutes(clock):
    # Job 6d: the compound reads now -- "an hour and a half" is 90 minutes after the
    # clock (10:00 Athens -> 11:30, 09:30 UTC). Before Job 6d it stayed refused rather
    # than misread.
    reading = _parse("remind me in an hour and a half to leave")
    assert reading.ok, reading.problem
    assert reading.due_at_utc.startswith("2026-10-28T09:30"), reading.due_at_utc
