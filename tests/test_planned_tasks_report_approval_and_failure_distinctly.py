"""A planned sub-task waiting on approval must never be read as a successful answer.

Reproduces the exact reported incident: a multipart request split into a market task and a weather
task; the market task's tool call needed Manual-mode approval, and `merge_outcomes` had no way to
tell that apart from an ordinary answer, so the sub-turn's own "Manual mode requires approval for
this exact action." text was concatenated directly in front of the (also malformed) weather answer
-- one reply that looked like it partially answered but was actually one approval prompt glued to
one wrong lookup, with no way for the reader to tell which was which.

`response_class` (not `task_outcome`, which is computed but discarded before it reaches the top of
`_run_once_inner`'s return dict -- verified by reading `action_fast_path_result`'s actual `return
{...}` statement in core/agent_runtime/fast_command_surface.py) is the field that survives, and
`response_policy_classification.action_response_class` already maps a pending-approval task_outcome
to `ResponseClass.APPROVAL_REQUIRED` and a failure to `TASK_FAILED_USER_SAFE`/`SYSTEM_ERROR_USER_SAFE`
1:1. This reuses that existing, tested classification rather than inventing a parallel one.
"""

from __future__ import annotations

from core.agent_runtime.turn_planner import (
    PlannedTask,
    PlannedTaskNeedsApprovalError,
    TaskOutcome,
    merge_outcomes,
    run_plan,
)
from core.agent_runtime.turn_planner_hook import build_planner_run_one


def test_a_pending_approval_outcome_is_not_ok_and_is_not_a_failure() -> None:
    outcome = TaskOutcome(task=PlannedTask(index=0, request="x"), outcome_kind="pending_approval", pending_message="Manual mode requires approval.")
    assert outcome.ok is False
    assert outcome.needs_approval is True


def test_merge_outcomes_keeps_a_pending_approval_out_of_the_answered_text() -> None:
    """The exact reported shape: one task needs approval, the sibling task answers normally.

    Before this fix, `merge_outcomes` had no `pending` bucket at all -- a pending-approval
    TaskOutcome could only be constructed as an ordinary `answer`, so its approval text sat in the
    same `answered` list as the weather table and the two were joined with no separator marking
    which was which.
    """
    market = TaskOutcome(
        task=PlannedTask(index=0, request="gold, silver, bitcoin and bnb price and 24-hour change"),
        outcome_kind="pending_approval",
        pending_message="Manual mode requires approval for this exact action.",
    )
    weather = TaskOutcome(
        task=PlannedTask(index=1, request="weather in kaunas, tallinn, and warsaw"),
        answer="| City | Conditions |\n| --- | --- |\n| Kaunas | Sunny, 21 C |",
    )
    merged = merge_outcomes([market, weather])
    assert "needs your approval" in merged
    assert "gold, silver, bitcoin and bnb" in merged
    assert "| City | Conditions |" in merged
    # The approval sentence must not appear glued directly onto the table with no marker between
    # them -- it must be introduced by the distinct "needs your approval" header.
    approval_index = merged.index("Manual mode requires approval")
    header_index = merged.index("needs your approval")
    assert header_index < approval_index


def test_a_genuine_failure_still_reports_under_the_existing_could_not_answer_header() -> None:
    ok = TaskOutcome(task=PlannedTask(index=0, request="a"), answer="answered fine")
    failed = TaskOutcome(task=PlannedTask(index=1, request="b"), error="ConnectionError")
    merged = merge_outcomes([ok, failed])
    assert "answered fine" in merged
    assert "I could not answer this part of your message" in merged
    # The request is named; the failure DETAIL is reader-facing prose. A bare exception-class
    # token is runtime internals (the 2026-09-08 live leak was "(RuntimeError: qwen2.5:7b
    # failed...)"), so it renders through the fail-closed scrubber as a generic phrase.
    assert "b (could not be completed)" in merged
    assert "ConnectionError" not in merged


def test_run_one_raises_needs_approval_when_response_class_is_approval_required() -> None:
    """Proves the exact field the hook reads. Sabotage: change the check to read `mode` or
    `task_outcome` (which is discarded before this dict is returned) and this goes red."""

    class _FakeAgent:
        def _run_once_inner(self, *_args, **_kwargs):
            return {
                "response": "Manual mode requires approval for this exact action.",
                "response_class": "approval_required",
            }

    run_one = build_planner_run_one(_FakeAgent(), session_id="s1", source_context={})
    task = PlannedTask(index=0, request="gold, silver, bitcoin and bnb price")
    try:
        run_one(task, {})
        raised = False
    except PlannedTaskNeedsApprovalError as exc:
        raised = True
        assert "Manual mode requires approval" in str(exc)
    assert raised, "expected PlannedTaskNeedsApprovalError to be raised"


def test_run_one_raises_a_plain_error_when_response_class_is_task_failed() -> None:
    class _FakeAgent:
        def _run_once_inner(self, *_args, **_kwargs):
            return {
                "response": "I wasn't able to complete that action.",
                "response_class": "task_failed_user_safe",
            }

    run_one = build_planner_run_one(_FakeAgent(), session_id="s1", source_context={})
    task = PlannedTask(index=0, request="anything")
    threw = False
    try:
        run_one(task, {})
    except PlannedTaskNeedsApprovalError:
        raise AssertionError("a genuine failure must not be classified as pending_approval")
    except Exception:
        threw = True
    assert threw


def test_run_plan_end_to_end_separates_the_approval_pending_market_task_from_the_answered_weather_task() -> None:
    """The literal benchmark shape, driven through the real `run_plan`/`merge_outcomes` pair."""

    class _FakeAgent:
        def _run_once_inner(self, request: str, **_kwargs):
            if "gold" in request:
                return {
                    "response": "Manual mode requires approval for this exact action.",
                    "response_class": "approval_required",
                }
            return {
                "response": "| City | Conditions |\n| --- | --- |\n| Kaunas | Sunny, 21 C |",
                "response_class": "generic_conversation",
            }

    run_one = build_planner_run_one(_FakeAgent(), session_id="s1", source_context={})
    tasks = [
        PlannedTask(index=0, request="gold, silver, bitcoin and bnb price and 24-hour change"),
        PlannedTask(index=1, request="weather in kaunas, tallinn, and warsaw"),
    ]
    outcomes = run_plan(tasks, run_one=run_one, max_workers=1)
    merged = merge_outcomes(outcomes)

    assert "| City | Conditions |" in merged
    assert "needs your approval" in merged
    assert "gold, silver, bitcoin and bnb" in merged
    # The weather table is a genuine answer -- it must not be listed as a failed/pending part.
    weather_outcome = next(o for o in outcomes if "kaunas" in o.task.request)
    assert weather_outcome.ok is True
    market_outcome = next(o for o in outcomes if "gold" in o.task.request)
    assert market_outcome.needs_approval is True
    assert market_outcome.ok is False
