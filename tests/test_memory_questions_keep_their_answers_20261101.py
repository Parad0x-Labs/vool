"""Memory questions keep their own lane and their newest value (BEAM miss sort, 2026-11-01).

The reader had the right answer, or the right records, and a lane or a selector lost it:
- "By what date am I aiming to ..." was answered with today's date by the date fast path;
- "In what order did I bring up ..." was answered with a chat-housekeeping refusal;
- "it's already at 55%" said the same day did not replace "40%" for "my most recent update".
Each test has its own words; the fast paths and chat actions they must not break are checked too.
"""
from __future__ import annotations

from datetime import datetime, timezone

from core.agent_runtime.fast_paths_utility import date_time_fast_path
from core.agent_runtime.housekeeping_lane import parse_housekeeping
from core.temporal_selection import AsOfIntent, TemporalCandidate, apply_temporal_selection

NOW = datetime(2026, 10, 31, 10, 0, tzinfo=timezone.utc)


def _date_answer(text):
    return date_time_fast_path(None, text, source_surface="api", now_utc=NOW)


def test_a_remembered_target_date_is_not_todays_date():
    assert _date_answer("By what date am I aiming to have the garden shed painted?") is None
    assert _date_answer("What date did I book the dentist for?") is None


def test_todays_date_still_comes_from_the_clock():
    assert _date_answer("what's the date today?") is not None
    assert _date_answer("what is the date") is not None


def test_an_ordered_memory_question_is_not_a_chat_action():
    assert parse_housekeeping(
        "In what order did I bring up my worries about the roof repair across our conversations? "
        "Mention ONLY and ONLY three items.") is None
    assert parse_housekeeping("In what order did we discuss the three holiday options?") is None


def test_chat_actions_still_parse():
    assert parse_housekeeping("pin this chat") is not None
    assert parse_housekeeping("rename this chat to Garden") is not None


def test_a_same_day_subjectless_update_replaces_the_earlier_value():
    day = 1710493200.0
    candidates = [
        TemporalCandidate("old", "I've knitted 30% of the blanket for my niece by March 3", statement_at=day, seq=1),
        TemporalCandidate("new", "considering it's already at 45% by March 9", statement_at=day, seq=2),
    ]
    result = apply_temporal_selection(
        candidates, intent=AsOfIntent(),
        question="What percentage of the blanket had I knitted as of my most recent update back in March?",
    )
    assert result["new"].eligible
    assert not result["old"].eligible


def test_same_day_values_without_source_order_stay_undetermined():
    # No order known: the selector must not invent one from retrieval rank.
    day = 1725969600.0
    candidates = [
        TemporalCandidate("a", "The monthly rent amount is $900", statement_at=day),
        TemporalCandidate("b", "The monthly rent amount is $950", statement_at=day),
    ]
    result = apply_temporal_selection(candidates, intent=AsOfIntent(), question="What's my current rent?")
    assert all(item.eligible for item in result.values())
    assert all(item.reason == "chronology-undetermined" for item in result.values())
