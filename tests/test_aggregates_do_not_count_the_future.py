"""A present-intent aggregate cannot count what has not happened yet.

Live incident this pins (q90 dev corpus F08-13, measured on the native lane
at 1fbb4876): "How many bags have the Nether Cove cleans collected in
total?" was asked under a June-15 clock with three imported records — May 5
(14 bags), June 9 (9 bags), June 25 (11 bags). The June-25 record is future
relative to the ask, but the aggregate branch only filtered against an
EXPLICIT as-of; with none in the question text the filter never ran, all
three packed, and the derived line totalled 34 instead of the ask's own
23. Point questions keep future-dated records (a scheduled closing date is
a legitimate answer), so the law lives only in the aggregate branch.
"""

from __future__ import annotations

import datetime as dt
from datetime import timezone

from core.temporal_selection import (
    TemporalCandidate,
    apply_temporal_selection,
    resolve_question_as_of,
)


def _cand(key: str, body: str, statement_iso: str) -> TemporalCandidate:
    return TemporalCandidate(
        key=key, body=body, role="user", authority="",
        statement_at=dt.datetime.fromisoformat(statement_iso).timestamp(),
        event_at=None, recorded_at=None,
    )


_BAGS = [
    _cand("may5", "Nether Cove clean: 14 bags of litter collected.",
          "2026-05-05T10:00:00+00:00"),
    _cand("jun9", "Nether Cove clean: 9 bags.",
          "2026-06-09T10:00:00+00:00"),
    _cand("jun25", "Nether Cove clean: 11 bags.",
          "2026-06-25T10:00:00+00:00"),
]

_QUESTION = "How many bags have the Nether Cove cleans collected in total?"
_NOW = dt.datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
_INTENT = resolve_question_as_of(_QUESTION, now_utc=_NOW)


class TestFutureRelativeToNow:
    def test_reproduction_future_record_excluded_from_present_total(self) -> None:
        assert _INTENT.as_of_end is None  # no as-of in the question text
        verdicts = apply_temporal_selection(
            _BAGS, intent=_INTENT, question=_QUESTION,
            multi_record=True, count_shaped=True, now_utc=_NOW,
        )
        assert verdicts["may5"].eligible
        assert verdicts["jun9"].eligible
        assert not verdicts["jun25"].eligible
        assert verdicts["jun25"].reason == "future-relative-to-now"

    def test_explicit_as_of_still_filters_relative_to_as_of(self) -> None:
        intent = resolve_question_as_of(
            "As of June 15, how many bags in total?", now_utc=_NOW)
        assert intent.as_of_end is not None
        verdicts = apply_temporal_selection(
            _BAGS, intent=intent,
            question="As of June 15, how many bags in total?",
            multi_record=True, count_shaped=True, now_utc=_NOW,
        )
        assert verdicts["may5"].eligible and verdicts["jun9"].eligible
        assert not verdicts["jun25"].eligible
        assert verdicts["jun25"].reason == "future-relative-to-as-of"

    def test_past_records_all_pack_when_nothing_is_future(self) -> None:
        now = dt.datetime(2026, 7, 1, tzinfo=timezone.utc)
        verdicts = apply_temporal_selection(
            _BAGS, intent=resolve_question_as_of(_QUESTION, now_utc=now),
            question=_QUESTION, multi_record=True, count_shaped=True,
            now_utc=now,
        )
        assert all(v.eligible for v in verdicts.values())

    def test_point_questions_keep_future_dated_records(self) -> None:
        closing = _cand(
            "close", "The boardwalk's western loop closes from 6 October 2026.",
            "2026-06-01T09:00:00+00:00")
        verdicts = apply_temporal_selection(
            [closing], intent=resolve_question_as_of(
                "When does the boardwalk close?", now_utc=_NOW),
            question="When does the boardwalk close?",
            multi_record=False, now_utc=_NOW,
        )
        assert verdicts["close"].eligible

    def test_no_now_utc_preserves_previous_behavior(self) -> None:
        verdicts = apply_temporal_selection(
            _BAGS, intent=_INTENT, question=_QUESTION,
            multi_record=True, count_shaped=True,
        )
        # No boundary supplied: the caller could not establish now, so the
        # filter must not guess (same posture as an unresolvable as-of).
        assert all(v.eligible for v in verdicts.values())
