"""Runtime joins the 2026-09-08 migration audit CONFIRMED and this lane does not repair.

Each test states the law a DUAL_RUN promotion needs and is a STRICT expected-failure: it documents
today's behaviour by failing, and the day the join is fixed it turns into an unexpected pass that
must be un-marked in the same change. Nothing here is a claim of conservation; it is the register of
what still converts graph identity back into text, index and order joins.
"""
from __future__ import annotations

import dataclasses

import pytest

FIVE = (
    "What's the weather in London? What's the weather in Rome? Which is warmer? "
    "What's the gold price? What's the silver price? Put the answers in a table. No web."
)


@pytest.mark.xfail(strict=True, reason="audit I01: verify_plan checks no invented words, not requested-slot coverage")
def test_a_three_of_five_plan_is_refused_by_plan_verification() -> None:
    from core.agent_runtime.turn_planner import PlannedTask, verify_plan

    plan = [
        PlannedTask(0, "What's the weather in London?"),
        PlannedTask(1, "What's the gold price?"),
        PlannedTask(2, "What's the silver price?"),
    ]
    assert verify_plan(plan, FIVE) is False, "two requested slots vanished from the plan and verification passed"


@pytest.mark.xfail(strict=True, reason="audit D02: run_plan advances on completed indexes, not successful typed results")
def test_a_dependent_does_not_run_after_its_prerequisite_failed() -> None:
    from core.agent_runtime.turn_planner import PlannedTask, run_plan

    ran: list[int] = []

    def run_one(task, done):
        ran.append(task.index)
        if task.index == 0:
            raise RuntimeError("prerequisite failed")
        return "answer"

    run_plan([PlannedTask(0, "a"), PlannedTask(1, "b", depends_on=(0,))], run_one=run_one, max_workers=1)
    assert ran == [0], "the dependent ran on a prerequisite that produced no result"


@pytest.mark.xfail(strict=True, reason="audit P01: conserve_request_for_resume replaces, rather than unions, the new turn's prohibitions")
def test_a_resume_cannot_drop_the_new_turns_prohibition() -> None:
    from core.agent_runtime.agent import conserve_request_for_resume
    from core.turn_contract import TurnRequest
    from core.turn_prohibitions import prohibitions_from_text

    current = TurnRequest.from_ingress(user_text="continue, no web", source_context={}, request_id="r", turn_id="t", session_id="s")
    current = dataclasses.replace(current, prohibitions=prohibitions_from_text("continue, no web"))
    assert "web" in current.prohibitions.families
    resumed = conserve_request_for_resume(current, "What is the price of gold?")
    assert "web" in resumed.prohibitions.families, "the no-web restriction stated on resume was lost"
