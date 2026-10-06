"""Schedule-time interrogatives are memory asks, never clock reads.

Measured on frozen head e1dfdb63 (q90 fresh-closure reconciliation): the dev
corpus questions "What time does the old tram head out?" (F01-13) and "What
time does the earliest boat leave during the cold months?" (F13-01) were
claimed by date_time_fast_path and answered from the wall clock with zero
provider calls -- the stored schedule had no path into the answer. The repair
lives in core.temporal_question_scope: a "what time" ask carried by
do-support/a modal over a real subject is HABITUAL (schedule), so past_only
withdraws it from current-clock contracts exactly like "usually" asks.

Fresh examples below use new domains/entities/wording (ferry winters, pottery
kiln, language class) -- the corpus cases are regression, the others are the
independent confirmation set. Clock-question controls prove the lane keeps
what it owns.
"""
from __future__ import annotations

from core.temporal_question_scope import question_time_scope


def _past_only(text: str) -> bool:
    return question_time_scope(text).past_only


class TestOriginalReproductions:
    def test_f01_13_tram_head_out_is_schedule_ask(self):
        assert _past_only("What time does the old tram head out?")

    def test_f13_01_earliest_boat_cold_months_is_schedule_ask(self):
        assert _past_only(
            "What time does the earliest boat leave during the cold months?")


class TestFreshIndependentExamples:
    def test_night_ferry_winter_schedule(self):
        assert _past_only("What time does the night ferry to Ventspils cast off in winter?")

    def test_kiln_first_firing_start(self):
        assert _past_only("What time does the kiln start its first firing on Saturdays?")

    def test_language_class_start(self):
        assert _past_only("What time does my Spanish class start?")

    def test_shuttle_departure_modal(self):
        assert _past_only("What time will the airport shuttle leave tomorrow?")

    def test_mixed_current_half_keeps_current_requirement(self):
        # "I landed at 9. What time is it now, and what time does my ride leave?"
        # keeps its CURRENT half: past_only must be False so the clock still
        # answers the "now" clause.
        assert not _past_only(
            "I landed at 9. What time is it now, and what time does my ride leave?")


class TestClockQuestionControls:
    def test_plain_clock_ask_stays_current(self):
        assert not _past_only("What time is it?")

    def test_clock_ask_with_place_stays_current(self):
        assert not _past_only("What time is it in Tokyo?")

    def test_clock_ask_now_marker_stays_current(self):
        assert not _past_only("What time is now?")
        assert not _past_only("what time is right now?")

    def test_bare_time_marker_stays_current(self):
        assert not _past_only("What's the time?")

    def test_conversion_frame_stays_current(self):
        # supplied absolute frames are clock arithmetic, not schedules
        assert not _past_only(
            "What time is it in Tokyo when it is 3:00 PM in New York?")

    def test_past_schedule_still_past(self):
        # already covered by the past interrogative head; unchanged
        assert _past_only("What time did the clinic open on Monday?")


class TestFastPathDecline:
    def test_date_time_fast_path_declines_schedule_ask(self):
        from core.agent_runtime.fast_paths_utility import date_time_fast_path

        class _NullAgent:
            pass

        reply = date_time_fast_path(
            _NullAgent(), "What time does the old tram head out?",
            source_surface="api", session_id="sched-test")
        assert reply is None, (
            "clock lane must decline a schedule ask; got "
            f"{reply!r}")

    def test_date_time_fast_path_answers_plain_clock(self):
        from core.agent_runtime.fast_paths_utility import date_time_fast_path

        class _NullAgent:
            pass

        reply = date_time_fast_path(
            _NullAgent(), "What time is it?",
            source_surface="api", session_id="clock-test")
        assert reply is not None and "urrent time" in reply
