"""The word a message opens with, set off by a comma, is not a part of the message to plan or answer.

Packaged 8b4eaa84f on the iMac (qwen3:8b, Manual mode, fresh profile): "yo, what's up?" went route
conductor_multi_intent_plan. The comma alone admitted it to the planner, the planner split it into
"yo" and "what's up?", no operation read "yo", and the reply ended in "Could not be answered: - yo
— I couldn't do this part". Read by shape, in any language: one or two letter words set off at the
head before one question are how the person opens, so no planner call is spent; and an opener the
planner still makes its own part (beside real asks) is never listed as a part that failed.

The planner model is a stub that returns the measured kind of split, so a planner call shows up as
a recorded prompt. Authored wording only.
"""
from __future__ import annotations

import json

import pytest

from core.agent_runtime.answer_coverage import DEMAND_UNANSWERED, unit_answer_evidence
from core.agent_runtime.turn_planner import plan_turn
from core.conductor import compose_answer, plan_conductor_turn, run_conductor_plan
from core.conductor.product_decision import ExecutionReport, reduce_execution_report
from core.conductor.registry import NodeContext

# (message, the opener, what follows it, a plausible reply)
GREETINGS = (
    ("yo, what's up?", "yo", "what's up?", "Not much, just here if you need anything."),
    ("hey, how's it going?", "hey", "how's it going?", "Pretty good, thanks! How about you?"),
    ("labas, kaip sekasi?", "labas", "kaip sekasi?", "Sveiki! Man viskas gerai, ačiū. O jums?"),
    ("na, alles klar bei dir?", "na", "alles klar bei dir?", "Ja, alles gut, danke! Und bei dir?"),
    ("hola, ¿qué tal?", "hola", "¿qué tal?", "¡Muy bien, gracias! ¿Y tú?"),
    ("labas, kaip tau šiandien sekasi?", "labas", "kaip tau šiandien sekasi?", "Gerai, ačiū!"),
    ("hallo! wie läuft dein Tag so?", "hallo", "wie läuft dein Tag so?", "Gut, danke der Nachfrage."),
    ("buenas, ¿cómo va todo por allí?", "buenas", "¿cómo va todo por allí?", "Todo bien por aquí."),
    ("hey Ana, how has your week been going?", "hey Ana", "how has your week been going?", "Busy but good!"),
)


def _recording(split: list[tuple[str, str]], calls: list[str]):
    def ask_model(_system: str, prompt: str) -> str:
        calls.append(prompt)
        return json.dumps([{"request": text, "operation": op, "depends_on": []} for text, op in split])

    return ask_model


@pytest.mark.parametrize(("text", "opener", "rest", "_reply"), GREETINGS)
def test_a_greeting_and_one_question_spends_no_planner_call_and_makes_no_plan(text, opener, rest, _reply):
    calls: list[str] = []
    ask_model = _recording([(opener, "factual_explanation"), (rest, "factual_explanation")], calls)

    assert plan_conductor_turn(text, ask_model=ask_model, plan_id="t") is None
    assert plan_turn(text, ask_model=ask_model) == []
    assert calls == []


@pytest.mark.parametrize(("text", "_opener", "_rest", "reply"), GREETINGS)
def test_a_greeting_answered_in_one_reply_leaves_no_slot_unanswered(text, _opener, _rest, reply):
    """The one reply the ordinary path gives is never accused of missing the opener."""
    assert DEMAND_UNANSWERED not in unit_answer_evidence(text, reply).values()


def _compose(plan) -> str:
    outcomes = run_conductor_plan(
        plan, context=NodeContext(run_generation=lambda *_a, **_k: "{}", timeout_s=5), plan_deadline_s=20)
    decision = reduce_execution_report(ExecutionReport(
        bound_plan=plan.bound_plan, node_outcomes=tuple(outcomes),
        planned_node_ids=tuple(node.node_id for node in plan.nodes),
        requirement_nodes=dict(plan.requirement_nodes)))
    return compose_answer(plan, outcomes, decision).text


@pytest.mark.parametrize(("text", "opener", "sum_part", "other_part"), [
    ("yo, what's 17*23, and how many minutes are in 3 hours?", "yo",
     "what's 17*23", "how many minutes are in 3 hours?"),
    ("labas, kiek bus 17*23 ir kiek minučių yra 3 valandose?", "labas",
     "kiek bus 17*23", "kiek minučių yra 3 valandose?"),
    ("na, was ist 17*23 und wie viele Minuten haben 3 Stunden?", "na",
     "was ist 17*23", "wie viele Minuten haben 3 Stunden?"),
    ("hola, ¿cuánto es 17*23 y cuántos minutos hay en 3 horas?", "hola",
     "¿cuánto es 17*23", "cuántos minutos hay en 3 horas?"),
])
def test_an_opener_the_planner_splits_off_beside_real_asks_is_never_a_failed_part(
        text, opener, sum_part, other_part):
    calls: list[str] = []
    ask_model = _recording(
        [(opener, "factual_explanation"), (sum_part, "calculation"), (other_part, "factual_explanation")], calls)
    plan = plan_conductor_turn(text, ask_model=ask_model, plan_id="t")
    if plan is None:  # the turn went whole to the ordinary path: nothing to list
        return
    answer = _compose(plan)
    assert "391" in answer, answer
    assert "Could not be answered" not in answer, answer


def test_a_plan_whose_only_other_part_is_the_opener_is_not_a_multi_part_plan():
    """Beside one ask the opener leaves one part that asks something: the turn is not the conductor's."""
    text = "yo, what's 17*23 then"
    calls: list[str] = []
    ask_model = _recording([("yo", "factual_explanation"), ("what's 17*23 then", "calculation")], calls)
    assert plan_conductor_turn(text, ask_model=ask_model, plan_id="t") is None
    assert len(calls) == 1


@pytest.mark.parametrize("text", [
    "what's 17*23, and remind me at 6 to call Ana",
    "hey, what's 17*23, and remind me at 6 to call Ana?",
])
def test_a_real_two_part_ask_still_gets_its_two_parts(text):
    split = [("what's 17*23", "calculation"), ("remind me at 6 to call Ana", "unavailable_action")]
    calls: list[str] = []
    plan = plan_conductor_turn(text, ask_model=_recording(split, calls), plan_id="t")
    assert len(calls) == 1
    assert plan is not None
    texts = [node.request_text for node in plan.nodes]
    assert any("17*23" in node for node in texts) and any("call Ana" in node for node in texts), texts

    generic_calls: list[str] = []
    tasks = plan_turn(text, ask_model=_recording(split, generic_calls))
    assert len(generic_calls) == 1
    assert [task.request for task in tasks] == [part for part, _op in split]


@pytest.mark.parametrize("text", [
    # A joining word after the mark still joins: the lead may be a terse ask of its own.
    "weather, and what time is it in Vilnius?",
    # An order after the mark may be a second ask.
    "weather in Rome, remind me at 6 to call Ana",
    # Several questions after the opener are still several.
    "hey, what's 17*23? and what's the weather in Rome?",
])
def test_what_follows_an_opener_still_reaches_the_planner_when_it_may_hold_two_asks(text):
    calls: list[str] = []

    def ask_model(_system: str, prompt: str) -> str:
        calls.append(prompt)
        return "[]"

    plan_conductor_turn(text, ask_model=ask_model, plan_id="t")
    assert calls, "a turn that may hold two asks still reaches the planner"

