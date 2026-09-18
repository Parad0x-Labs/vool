"""A typed plan that cannot serve a requested live intent must SAY so in the rendered answer.

Measured on the served surface (operator transcript, 2026-08-15 12:38): a five-part turn (weather
Warsaw+Manchester + which is warmer, flight prices Warsaw->London Luton/Gatwick, BTC+BNB prices +
which gained most THIS WEEK, hotel deals in London) was claimed whole by the typed live-data plan,
which planned only weather + market subtasks and rendered only the Markets/Weather tables. Flights
and hotels were never mentioned (no tool exists for them), and "gained most this week" was silently
answered with the 24-hour change field -- a different question. The turn completed as if fully
answered.

These tests drive the REAL planner (`build_live_data_plan`) with fresh wording, never the
transcript's exact text, and assert on the rendered answer -- the served surface. The negative
controls pin the over-fire direction: a note claiming "I could not do X" on a turn that never
asked X is the worse failure, so detection must require the explicit noun families.

Sabotage anchor: suppressing the coverage note in `render_live_data_answer` (or making
`coverage_note` return "") must fail `test_reported_turn_shape_note_names_all_three_gaps` while
every control below stays green.
"""

from __future__ import annotations

from core.agent_runtime.live_data_plan import (
    LiveDataPlan,
    LiveDataSubtask,
    SubtaskLifecycle,
    SubtaskOutcome,
    build_live_data_plan,
)
from core.agent_runtime.live_data_render import coverage_note, render_live_data_answer


def _plan(text: str) -> LiveDataPlan:
    plan = build_live_data_plan(text, plan_id="cov-plan", attempt_id="cov-attempt")
    assert plan is not None, f"real planner refused to claim this LIVE_DATA turn: {text!r}"
    return plan


def _synthetic_outcomes(plan: LiveDataPlan) -> list[SubtaskOutcome]:
    """Successful outcomes shaped exactly like the runner's result dicts -- offline, no network."""
    outcomes: list[SubtaskOutcome] = []
    for task in plan.subtasks:
        if task.operation == "market_quote":
            result = {
                "price": 100.0,
                "currency": "USD",
                "change_24h_pct": 1.5,
                "source": "src",
                "retrieved_at": "2026-08-15 12:00 UTC",
            }
        elif task.operation == "weather_lookup":
            result = {
                "condition": "Sunny",
                "temperature_c": 22.0,
                "high_c": 25.0,
                "low_c": 15.0,
                "source": "wttr.in",
                "observed_at": "12:00 PM",
            }
        else:
            outcomes.append(SubtaskOutcome(subtask=task, state=SubtaskLifecycle.UNSUPPORTED_ENTITY))
            continue
        outcomes.append(SubtaskOutcome(subtask=task, state=SubtaskLifecycle.SUCCEEDED, result=result))
    return outcomes


def _rendered(text: str) -> str:
    plan = _plan(text)
    return render_live_data_answer(plan, _synthetic_outcomes(plan))


# --------------------------------------------------------------------------------------------
# The reported turn shape, fresh wording: all three gaps must be named on the served surface.
# --------------------------------------------------------------------------------------------

REPORTED_SHAPE = (
    "Current weather for Warsaw and Manchester -- which city is warmer? Also check flight "
    "prices Warsaw to London (Luton or Gatwick), pull BTC and BNB prices and say which gained "
    "more this week, plus any hotel deals in London."
)


def test_reported_turn_shape_note_names_all_three_gaps():
    answer = _rendered(REPORTED_SHAPE)
    assert "Not covered in this answer" in answer
    low = answer.lower()
    assert "flight prices" in low
    assert "no flight-search tool" in low
    assert "hotel deals" in low
    assert "no hotel-search tool" in low
    assert "weekly (7-day) change" in low
    assert "24-hour change only" in low
    # The note follows the data, never replaces it: the served results are still there.
    # (Only the Markets table is asserted structurally -- how many weather subtasks this
    # wording extracts is the location extractor's business, not this note's.)
    assert "**Markets**" in answer
    assert "Sunny" in answer


def test_reported_turn_shape_plan_never_gained_flight_or_hotel_subtasks():
    """The gap is plan-level: no operation exists for flights/hotels, so the note is the only
    channel through which these intents can reach the answer at all."""
    plan = _plan(REPORTED_SHAPE)
    operations = {task.operation for task in plan.subtasks}
    assert operations <= {"market_quote", "weather_lookup", "unsupported_market_entity"}


# --------------------------------------------------------------------------------------------
# Negative controls: turns that never asked for the unservable thing get NO note.
# --------------------------------------------------------------------------------------------


def test_control_single_intent_weather_has_no_note():
    answer = _rendered("weather in oslo")
    assert "Not covered" not in answer
    assert "flight" not in answer.lower()
    assert "hotel" not in answer.lower()


def test_control_market_only_turn_has_no_note():
    answer = _rendered("gold and silver price")
    assert "Not covered" not in answer
    assert "weekly" not in answer.lower()
    assert "weather" not in answer.lower()


def test_control_flight_mention_without_commerce_context_has_no_note():
    """'My flight was delayed' is not a fare request -- a bare travel mention must not trigger
    a false 'I could not check flights' apology."""
    answer = _rendered(
        "My flight was delayed for two hours this morning. What's the weather in Paris, "
        "and the BTC price?"
    )
    assert "Not covered" not in answer


def test_control_change_question_without_week_timeframe_has_no_note():
    """24h change IS what the source serves -- a same-day movement question is fully covered."""
    answer = _rendered("which crypto moved most today: btc or eth price")
    assert "Not covered" not in answer
    assert "weekly" not in answer.lower()


def test_control_plan_none_renders_no_note():
    task = LiveDataSubtask(
        subtask_id="p:weather:oslo", entity="Oslo", operation="weather_lookup",
        arguments={"location": "oslo"}, required_result_fields=("condition", "source", "observed_at"),
        tool="weather", tool_intent="web.research",
    )
    outcome = SubtaskOutcome(
        subtask=task, state=SubtaskLifecycle.SUCCEEDED,
        result={"condition": "Sunny", "temperature_c": 20.0, "source": "wttr.in", "observed_at": "12:00 PM"},
    )
    assert "Not covered" not in render_live_data_answer(None, [outcome])


def test_control_weekly_note_requires_market_subtasks():
    """A weather-only plan whose text happens to carry week+change vocabulary must not claim a
    market-source limitation it never touched."""
    task = LiveDataSubtask(
        subtask_id="p:weather:oslo", entity="Oslo", operation="weather_lookup",
        arguments={"location": "oslo"}, required_result_fields=("condition", "source", "observed_at"),
        tool="weather", tool_intent="web.research",
    )
    plan = LiveDataPlan(
        plan_id="p", attempt_id="a",
        original_request="how has the weather in oslo changed this week",
        subtasks=(task,),
    )
    assert coverage_note(plan) == ""


# --------------------------------------------------------------------------------------------
# Anti-overfit family: fresh phrasings of each unservable intent, each firing ONLY its own gap.
# --------------------------------------------------------------------------------------------


def test_family_plane_tickets_phrasing_fires_flight_gap_only():
    plan = _plan("weather in Oslo and plane tickets Vilnius to Oslo")
    note = coverage_note(plan)
    assert "flight prices" in note
    assert "hotel" not in note
    assert "weekly" not in note


def test_family_hotel_rates_phrasing_fires_hotel_gap_only():
    plan = _plan("gold price and any good hotel rates in Rome")
    note = coverage_note(plan)
    assert "hotel deals" in note
    assert "flight" not in note
    assert "weekly" not in note


def test_family_past_week_change_phrasing_fires_weekly_gap_only():
    plan = _plan("bitcoin and bnb price change over the past week")
    note = coverage_note(plan)
    assert "weekly (7-day) change" in note
    assert "flight" not in note
    assert "hotel" not in note


def test_family_airfare_word_is_self_sufficient():
    plan = _plan("weather in Vilnius and the airfare to Berlin")
    note = coverage_note(plan)
    assert "flight prices" in note
