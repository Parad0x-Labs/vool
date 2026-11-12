"""A sentence that only shapes the reply is never planned as its own request.

Measured 2026-11-12 in the real app on the v0.8 head 247124ed (auto model selection run 1,
qwen3.5:4b): "What is a good way to keep basil alive on a windowsill? Two sentences." spent a
planner call (38 s of a 79 s turn), was split into the question and "Two sentences.", and the reply
ended in "Could not be answered: Two sentences." with "1 of 1 derived step failed" on its record.
The directive grammar only knew verb-led directives ("Keep it to one sentence."), not a bare shape.

Two guards: a turn that is one request plus such sentences spends no planner call at all, and a
planner that still proposes one as a clause has it dropped like any remark. The first wording is
the measured one; the rest are new wordings. Authored wording only; the planner model is a stub.
"""
from __future__ import annotations

import json

import pytest

from core.agent_runtime.turn_planner import plan_turn, without_reply_directives
from core.conductor.planner import plan_conductor_turn

ONE_REQUEST_AND_A_SHAPE = (
    ("What is a good way to keep basil alive on a windowsill? Two sentences.",
     "What is a good way to keep basil alive on a windowsill?", "Two sentences."),
    ("How do I keep my sourdough starter alive? In one sentence.",
     "How do I keep my sourdough starter alive?", "In one sentence."),
    ("Explain how a heat pump works. Two sentences.",
     "Explain how a heat pump works.", "Two sentences."),
    ("Why do cats purr? Short answer please.",
     "Why do cats purr?", "Short answer please."),
    ("How do bees make honey? Keep it to two sentences.",
     "How do bees make honey?", "Keep it to two sentences."),
    ("Describe a good morning stretch routine. Three bullet points.",
     "Describe a good morning stretch routine.", "Three bullet points."),
    ("What should I pack for a weekend hike? 50 words max.",
     "What should I pack for a weekend hike?", "50 words max."),
)


def _split(*clauses: str) -> str:
    return json.dumps([{"request": c, "operation": "factual_explanation", "depends_on": []} for c in clauses])


@pytest.mark.parametrize(("text", "asked", "shape"), ONE_REQUEST_AND_A_SHAPE)
def test_one_request_and_a_reply_shape_spends_no_planner_call(text, asked, shape):
    calls: list[str] = []

    def ask_model(_system: str, prompt: str) -> str:
        calls.append(prompt)
        return _split(asked, shape)  # the measured split, if the planner were asked

    assert plan_conductor_turn(text, ask_model=ask_model, plan_id="t") is None
    assert plan_turn(text, ask_model=ask_model) == []
    assert calls == []
    assert without_reply_directives(text) == asked


@pytest.mark.parametrize(("text", "asked", "shape"), ONE_REQUEST_AND_A_SHAPE)
def test_a_reply_shape_proposed_as_a_clause_is_not_a_node(text, asked, shape):
    """Beside two real requests the planner is still asked; the shape it proposes is no node."""
    turn = f"What is 137 x 29? Also get the current weather for Vilnius. {shape}"
    reply = json.dumps([
        {"request": "What is 137 x 29?", "operation": "quantitative_reasoning", "depends_on": []},
        {"request": "get the current weather for Vilnius", "operation": "weather_observation", "depends_on": []},
        {"request": shape, "operation": "factual_explanation", "depends_on": []},
    ])
    plan = plan_conductor_turn(turn, ask_model=lambda _s, _p: reply, plan_id="t")
    assert plan is not None
    texts = [node.request_text for node in plan.nodes]
    assert any("Vilnius" in node for node in texts) and any("137" in node for node in texts), texts
    assert not any(shape.rstrip(".") in node for node in texts), texts


@pytest.mark.parametrize("text", [
    "Two sentences were missing from my report. Which page were they on?",
    "Get the current weather for Vilnius. Then convert 100 USD to EUR.",
    "What is 137 x 29? Also get the current weather for Vilnius",
])
def test_sentences_that_ask_something_are_still_planned(text):
    calls: list[str] = []

    def ask_model(_system: str, prompt: str) -> str:
        calls.append(prompt)
        return "[]"

    plan_conductor_turn(text, ask_model=ask_model, plan_id="t")
    assert calls, "a turn with two asking sentences still reaches the planner"
    assert without_reply_directives(text) == text
