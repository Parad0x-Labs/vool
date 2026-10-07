"""Answer directives around one question do not make a multi-request conductor turn.

Measured 2026-10-06 on the memory port's official-150 parity run: a reader turn that wraps one recall
question in answer directives was planned as the question plus an unresolved node for the
directives. The conductor's knowledge node answers without the chat's memory records, so the user got
"I don't know" beside "Could not be answered: - Answer using the imported prior conversations ...",
while the ordinary path answers the same question from those records.
"""
from __future__ import annotations

import json

from core.conductor import plan_conductor_turn

DIRECTIVES = (
    "Answer using the imported prior conversations. You may derive only answers supported by those "
    "records. If the records do not support an answer, say you do not know. Give a concise final answer."
)
QUESTION = "How often does Audrey meet up with other dog owners for tips and playdates?"


def _reply(*clauses: dict) -> str:
    return json.dumps(list(clauses))


def test_one_question_wrapped_in_directives_is_left_to_the_ordinary_path() -> None:
    reply = _reply(
        {"request": QUESTION, "operation": "factual_explanation", "depends_on": []},
        {"request": DIRECTIVES, "operation": "answer_policy", "depends_on": []},
    )
    plan = plan_conductor_turn(DIRECTIVES + "\n" + QUESTION, ask_model=lambda _s, _p: reply, plan_id="t")
    assert plan is None


def test_two_real_requests_inside_directives_still_get_a_plan() -> None:
    text = "Keep it short. What is 137 x 29? Also get the current weather for Kaunas and Tallinn."
    reply = _reply(
        {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
        {"request": "get the current weather for Kaunas and Tallinn", "operation": "weather_lookup", "depends_on": []},
        {"request": "Keep it short.", "operation": "answer_policy", "depends_on": []},
    )
    plan = plan_conductor_turn(text, ask_model=lambda _s, _p: reply, plan_id="t")
    assert plan is not None
    assert {"calculation", "weather_lookup"} <= {node.operation for node in plan.nodes}




def test_a_directive_planned_as_an_explanation_is_not_a_request() -> None:
    """The measured shape (official-150 parity run): the planner turned the directives into an
    unresolved node and a factual_explanation of "Give a concise final answer.", and left the real
    question unresolved. Only the question is a request, so the turn stays on the ordinary path."""
    question = "When did Caroline draw a self-portrait? Use DATE of CONVERSATION to answer with an approximate date."
    reply = _reply(
        {"request": DIRECTIVES.rsplit(" Give", 1)[0], "operation": "answer_policy", "depends_on": []},
        {"request": "Give a concise final answer.", "operation": "factual_explanation", "depends_on": []},
        {"request": question, "operation": "answer_policy", "depends_on": []},
    )
    assert plan_conductor_turn(DIRECTIVES + "\n" + question, ask_model=lambda _s, _p: reply, plan_id="t") is None
