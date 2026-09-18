"""One user attempt -> at most one authoritative executable plan.

Authoritative classifier: `core.execution_requirements.requirements_for()` (via
`_live_data_classification`) decides whether a turn is LIVE_DATA. Nothing else makes that call --
`build_live_data_plan` does not re-derive it, it only enumerates entities once the caller already
knows the answer is yes (see its own docstring).

Why the generic text-splitting planner (`turn_planner.plan_turn`, reached through
`_maybe_answer_planned_turn`) cannot also claim the same request: `_run_once_inner` calls
`_maybe_answer_live_data_turn` FIRST (apps/vool_agent.py:473) and returns immediately if it
succeeds (line 478-479) -- `_maybe_answer_planned_turn` is structurally never reached for that
turn, not merely "its result is ignored" or "the second one wins a race". This is a sequential
short-circuit in a single-threaded control-flow function, not two independent lanes that could
both fire.

Single plan_id: `_maybe_answer_live_data_turn` generates `plan_id = f"livedata-{uuid4().hex[:12]}"`
exactly once, in one function that runs at most once per turn.

Activity representation: `live_data_plan_created`/`live_data_plan_completed` runtime events, keyed
by that one plan_id (see apps/vool_agent.py and core/turn_trace.py:collect_turn_trace).

Extending to future live-data domains: add a new recognizer to `_live_data_classification`'s
has_X checks, a new `LiveDataSubtask.operation` value, and a runner function for it -- all within
the SAME `LiveDataPlan`/`build_live_data_plan`/`run_live_data_plan` architecture. A new domain
never needs a second independent planner; `allowed_toolsets` already generalizes to N toolsets, not
just two.
"""

from __future__ import annotations

from unittest import mock


def test_the_live_data_hook_short_circuits_before_the_generic_planner_is_ever_called(make_agent) -> None:
    """Direct proof of the invariant: when the live-data hook succeeds, the generic planner is
    never invoked at all -- not called-and-ignored, never called."""
    agent = make_agent()
    fake_live_data_result = {
        "response": "**Markets**\n\n| Asset | Price |\n| --- | --- |\n| Bitcoin | $64,000 |",
        "response_class": "utility_answer",
        "confidence": 0.9,
    }
    with (
        mock.patch.object(agent, "_maybe_answer_live_data_turn", return_value=fake_live_data_result) as live_data_mock,
        mock.patch.object(agent, "_maybe_answer_planned_turn") as planned_mock,
    ):
        result = agent.run_once(
            "Give me the current price of bitcoin",
            source_context={"surface": "openclaw", "platform": "openclaw", "operating_mode": "manual"},
        )
    assert live_data_mock.called
    assert not planned_mock.called, "the generic planner must never be invoked once the live-data hook claims the turn"
    assert "Bitcoin" in str(result.get("response") or "")


def test_a_non_live_data_turn_reaches_the_generic_planner_normally(make_agent) -> None:
    """The inverse: when the live-data hook declines (returns None, the common path), the generic
    planner IS reached -- proving the short-circuit is conditional, not a permanent block."""
    agent = make_agent()
    with (
        mock.patch.object(agent, "_maybe_answer_live_data_turn", return_value=None) as live_data_mock,
        mock.patch.object(agent, "_maybe_answer_planned_turn", return_value=None) as planned_mock,
    ):
        agent.run_once(
            "what is the capital of France and also who wrote Hamlet",
            source_context={"surface": "openclaw", "platform": "openclaw", "operating_mode": "manual"},
        )
    assert live_data_mock.called
    assert planned_mock.called


# Sabotage evidence (run manually against the real source, not embedded as a permanent test that
# mutates production code during CI): removing the `if live_data_result is not None: return
# _guard_final_result(live_data_result)` guard at apps/vool_agent.py:478-479 and re-running
# test_the_live_data_hook_short_circuits_before_the_generic_planner_is_ever_called above turns it
# red -- `planned_mock.called` becomes True, proving the guard (not the test's own mocking) is what
# enforces "at most one authoritative plan claims a turn". Restored immediately after, byte-
# identical to the committed source; reported in the commit message for this file, not re-run here.
