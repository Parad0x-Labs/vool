"""Bounded voolble temporal boundary regressions; exposed acceptance cases."""
from datetime import datetime, timezone

import pytest

from core.temporal_selection import AsOfIntent, TemporalCandidate, apply_temporal_selection, resolve_question_as_of

UTC = timezone.utc

def _moment(text):
    return datetime.fromisoformat(text).replace(tzinfo=UTC)

def _candidate(key, body, recorded=None):
    return TemporalCandidate(key=key, body=body, role="user", authority="observed-user-statement",
                             statement_at=_moment(recorded).timestamp() if recorded else None)

SURVEY_FIRST = "I am Elena Ruiz. My first estuary-survey assignment ran from 2024-03-01 through 2024-06-30 inclusive: March, April, May, and June, four complete calendar months. Julian Voss is a different surveyor; Julian's continuous assignment ran from 2024-01-15 through 2024-10-15 inclusive."
SURVEY_SECOND = "I, Elena Ruiz, resumed the estuary survey from 2024-09-01 through 2025-02-28 inclusive: September, October, November, December, January, and February, six complete calendar months. I was not assigned during July or August 2024, and I have no assignment beginning after 2025-02-28 in this record. Julian's January-to-October 2024 interval belongs only to Julian."
MUSEUM_OLD = "The museum object called the copper astrolabe has storage code M-08 and lives in the north drawer in a felt sleeve. This arrangement is effective from 2025-07-02 through 2025-07-08 inclusive."
MUSEUM_NEW = "Effective 2025-07-09, the same copper astrolabe moves to storage code M-14 in the east rack in a linen sleeve. M-08, north drawer, and felt sleeve are the old arrangement, not the current one."

@pytest.mark.parametrize("question", [
    "How many complete calendar months did I, Elena Ruiz, work on the estuary survey across my two recorded assignments? Give both exact ISO date intervals and the total, without counting Julian's interval.",
    "On 2024-07-10, which of Elena Ruiz and Julian Voss was assigned to the estuary survey? Explain using their recorded intervals rather than assuming that their assignments were shared.",
])
def test_historical_multi_record_closed_windows_without_asof_are_not_clock_expired(question):
    candidates = [_candidate("first", SURVEY_FIRST, "2024-07-01"),
                  _candidate("second", SURVEY_SECOND, "2025-03-01")]
    verdicts = apply_temporal_selection(candidates, intent=AsOfIntent(), multi_record=True,
                                       past_only=True, question=question, now_utc=_moment("2025-03-01"))
    assert verdicts["first"].eligible and verdicts["second"].eligible
    assert all(v.reason == "operand-kept" for v in verdicts.values())

@pytest.mark.parametrize("question", [
    "On 2025-07-08, before the later move and rename, what storage code, location, and sleeve did the copper astrolabe have?",
    "In 2025-07-08, what storage code did the copper astrolabe have?",
    "As of 2025-07-08, what storage code did the copper astrolabe have?",
    "What storage code did the copper astrolabe have on 2025-07-08?",
])
def test_iso_query_date_resolves_full_calendar_day(question):
    intent = resolve_question_as_of(question, now_utc=_moment("2026-10-02"))
    assert intent.as_of_end is not None
    assert intent.as_of_end.date().isoformat() == "2025-07-08"
    assert intent.as_of_end.hour == 23 and intent.origin == "question-text"

@pytest.mark.parametrize("question", [
    "On 2025-02-30, what code did the record have?",
    "As of 2025-13-01, what code did the record have?",
    "What is the current storage code for the object scheduled to move on 2027-07-08?",
    "What is today's code, and is the deadline on 2027-07-08?",
    'The example says "On 2025-07-08, what code?"; what is the current code?',
    "What is the current record for catalog ID 2025-07-08?",
])
def test_invalid_or_incidental_iso_dates_do_not_create_cutoff(question):
    assert resolve_question_as_of(question, now_utc=_moment("2026-10-02")).as_of_end is None

def test_iso_historical_museum_eligibility_keeps_old_window_and_excludes_later_record():
    question = "On 2025-07-08, before the later move and rename, what storage code, location, and sleeve did the copper astrolabe have?"
    intent = resolve_question_as_of(question, now_utc=_moment("2026-10-02"))
    assert intent.present
    verdicts = apply_temporal_selection([
        _candidate("old", MUSEUM_OLD, "2025-07-02"),
        _candidate("new", MUSEUM_NEW, "2025-07-09")], intent=intent, multi_record=True,
        past_only=True, question=question, now_utc=_moment("2026-10-02"))
    assert verdicts["old"].eligible
    assert not verdicts["new"].eligible
    assert verdicts["new"].reason == "future-relative-to-as-of"

def test_explicit_asof_still_excludes_window_expired_before_cutoff():
    intent = resolve_question_as_of("As of July 15 2025, how many slots were assigned?")
    verdicts = apply_temporal_selection([_candidate("old", MUSEUM_OLD, "2025-07-02")],
        intent=intent, multi_record=True, now_utc=_moment("2026-10-02"))
    assert not verdicts["old"].eligible
    assert verdicts["old"].reason == "window-expired-before-as-of"

def test_missing_asof_preserves_existing_future_state_boundary():
    verdicts = apply_temporal_selection([
        _candidate("future", "The total is 9 crates effective from 2027-07-01 through 2027-07-30 inclusive.", "2027-07-01")],
        intent=AsOfIntent(), multi_record=True, now_utc=_moment("2026-10-02"))
    assert not verdicts["future"].eligible
    assert verdicts["future"].reason == "future-relative-to-now"

def test_no_cutoff_and_no_clock_preserve_unknown_chronology():
    verdicts = apply_temporal_selection([_candidate("unknown", "The total is 9 crates.")],
        intent=AsOfIntent(), multi_record=True)
    assert verdicts["unknown"].eligible
    assert verdicts["unknown"].effective_time is None
