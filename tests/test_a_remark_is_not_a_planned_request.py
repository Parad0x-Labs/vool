"""What a message says but does not ask is never planned as its own request.

Measured 2026-10-28 on qwen3:8b, the PC and a phone alike: the planner model split "my mate says her life
is like one big locked file, whats she on about?" into two requests. The first asks nothing, no operation
could serve it, and the reply ended in "Could not be answered: - my mate says her life is ..." under a
good answer. "whats my sisters name? you know this one" did the same with "you know this one".

The runtime already reads those spans as the context of, or a remark on, the real question
(`interpret_request`). Authored wording only; the planner model is a stub that returns the split.
"""
from __future__ import annotations

import json

import pytest

from core.conductor.planner import plan_conductor_turn


def _plan(text: str, clauses: list[tuple[str, str, list[int]]]):
    raw = json.dumps([{"request": r, "operation": o, "depends_on": d} for r, o, d in clauses])
    return plan_conductor_turn(text, ask_model=lambda _system, _user: raw)


@pytest.mark.parametrize(
    ("text", "clauses"),
    [
        (
            "my neighbour says his garden is like one long waiting room, whats he on about?",
            [("my neighbour says his garden is like one long waiting room", "factual_explanation", []),
             ("whats he on about?", "factual_explanation", [])],
        ),
        (
            "whats my brothers name? you know this one",
            [("whats my brothers name?", "factual_explanation", []),
             ("you know this one", "factual_explanation", [])],
        ),
    ],
)
def test_a_split_that_leaves_one_request_goes_to_the_ordinary_path(text, clauses):
    assert _plan(text, clauses) is None


def test_two_real_requests_keep_their_plan():
    plan = _plan(
        "What is 137 x 29? Also get the current weather for Vilnius",
        [("What is 137 x 29?", "quantitative_reasoning", []),
         ("get the current weather for Vilnius", "weather_observation", [])],
    )
    assert plan is not None
    texts = [node.request_text for node in plan.nodes]
    assert any("Vilnius" in text for text in texts), texts
    assert any("137" in text for text in texts), texts


def test_no_node_is_left_for_the_remark_beside_two_requests():
    plan = _plan(
        "my sister says the house is like one big oven. What is 137 x 29? Also get the current weather for Vilnius",
        [("my sister says the house is like one big oven", "factual_explanation", []),
         ("What is 137 x 29?", "quantitative_reasoning", []),
         ("get the current weather for Vilnius", "weather_observation", [])],
    )
    assert plan is not None
    assert not any("oven" in node.request_text for node in plan.nodes), [n.request_text for n in plan.nodes]
