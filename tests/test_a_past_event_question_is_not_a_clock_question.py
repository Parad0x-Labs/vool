"""A past-event or habitual time question is not a clock question.

The deterministic clock lane answers questions ABOUT THE CLOCK. On the frozen
base e821457d it claimed past-tense event questions on the shared words "what
time" and answered them with the machine clock: the saved LongMemEval case
q051a848 -- "What time did I reach the clinic on Monday?" -- was delivered as
"Current time is 13:40 EEST."

REGRESSION rows restate the saved failure shapes. FRESH ACCEPTANCE rows use
different wording, facts, roles and domains, and were frozen before their
first execution.
"""

from __future__ import annotations

from datetime import datetime, timezone

from core.agent_runtime.fast_paths_utility import date_time_fast_path

_NOW = datetime(2026, 9, 29, 10, 40, tzinfo=timezone.utc)


def _lane_answer(question: str) -> str | None:
    return date_time_fast_path(None, question, source_surface="api", now_utc=_NOW)


# ---------------------------------------------------------------- regression


def test_clinic_arrival_question_is_declined() -> None:
    # q051a848: the saved misroute. The lane must decline; the arrival time is
    # a historical fact the conversation may hold, never the machine clock.
    assert _lane_answer("What time did I reach the clinic on Monday?") is None


def test_habitual_departure_question_is_declined() -> None:
    assert _lane_answer("What time do I usually leave for work?") is None


def test_past_move_date_question_is_declined() -> None:
    assert _lane_answer("What was the date I moved to Berlin?") is None


# ------------------------------------------------------- positive controls
# The lane's own contract must survive the decline unchanged.


def test_current_time_question_is_still_answered() -> None:
    answer = _lane_answer("What time is it right now?")
    assert answer is not None and "Current time" in answer


def test_todays_date_question_is_still_answered() -> None:
    answer = _lane_answer("what's the date today")
    assert answer is not None and "2026-09-29" in answer


def test_current_half_of_a_mixed_turn_keeps_the_lane() -> None:
    # A turn that also asks about now keeps the clock for its current half.
    answer = _lane_answer("I landed at 9. What time is it now?")
    assert answer is not None and "Current time" in answer


def test_timezone_clock_question_is_still_answered() -> None:
    answer = _lane_answer("what time is it in Tokyo?")
    assert answer is not None and "Current time" in answer


# ---------------------------------------------------------- fresh acceptance


def test_fresh_ferry_departure_question_is_declined() -> None:
    assert _lane_answer("What time did the ferry from Nida leave last Thursday?") is None


def test_fresh_assistant_promise_question_is_declined() -> None:
    # Assistant history: when the runtime said something is a past fact too.
    assert _lane_answer("What time did you say the backup would start?") is None


def test_fresh_habitual_shift_question_is_declined() -> None:
    assert _lane_answer("What time does Marius normally start his shift?") is None


def test_fresh_wedding_day_date_question_is_declined() -> None:
    assert _lane_answer("What was the date of our wedding in Prague?") is None


def test_fresh_league_finish_question_is_declined() -> None:
    assert _lane_answer("What time was the final whistle in the cup match?") is None


def test_fresh_current_clock_in_past_wording_stays_answered() -> None:
    # Different wording from every regression row, same present reference.
    answer = _lane_answer("Tell me the current time in Vilnius please")
    assert answer is not None and "Current time" in answer


def test_fresh_multi_city_clock_stays_answered() -> None:
    answer = _lane_answer("what time is in rome now and in paris?")
    assert answer is not None and "Rome" in answer and "Paris" in answer
