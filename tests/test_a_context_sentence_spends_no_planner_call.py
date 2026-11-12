"""What the person tells about themselves beside one question buys no planner call, in either planner.

Case X11 (qwen3:8b, the Oct 28 rechecks): "what should i post each day next week? i sell handmade
candles at markets" was split into the question and "i sell handmade candles at markets", and the reply
ended in "Could not be answered: - i sell handmade candles at markets". The conductor has dropped such
a clause since Oct 29, but still spent the planner call (27 to 36 s of those turns) to learn that one
request was left; declined there, the generic planner then spent its own. Both now read the turn
minus what it does not ask before calling.

The first wording is the measured one; the rest are new wordings. The planner model is a stub that
returns the measured split, so a planner call shows up as a recorded prompt.
"""
from __future__ import annotations

import json

import pytest

from core.agent_runtime.turn_planner import plan_turn
from core.conductor.planner import plan_conductor_turn, without_sentences_that_ask_nothing

ONE_REQUEST_AND_CONTEXT = (
    ("what should i post each day next week? i sell handmade candles at markets",
     "what should i post each day next week?", "i sell handmade candles at markets"),
    ("i sell handmade candles at markets. what should i post each day next week?",
     "what should i post each day next week?", "i sell handmade candles at markets."),
    ("give me a post idea for every day next week. we bake sourdough for a farmers market",
     "give me a post idea for every day next week.", "we bake sourdough for a farmers market"),
    ("i make leather wallets by hand, what should i post this week?",
     "what should i post this week?", "i make leather wallets by hand"),
    ("any ideas what to post daily next week? my shop sells vintage records",
     "any ideas what to post daily next week?", "my shop sells vintage records"),
    ("my sister lives in Rome. whats the weather there?",
     "whats the weather there?", "my sister lives in Rome."),
)


def _recording(split: list[str], calls: list[str]):
    def ask_model(_system: str, prompt: str) -> str:
        calls.append(prompt)
        return json.dumps([{"request": c, "operation": "factual_explanation", "depends_on": []} for c in split])

    return ask_model


@pytest.mark.parametrize(("text", "asked", "context"), ONE_REQUEST_AND_CONTEXT)
def test_one_request_and_a_context_sentence_spends_no_planner_call(text, asked, context):
    calls: list[str] = []
    ask_model = _recording([asked, context], calls)

    assert without_sentences_that_ask_nothing(text) == asked.strip()
    assert plan_conductor_turn(text, ask_model=ask_model, plan_id="t") is None
    assert plan_turn(text, ask_model=ask_model) == []
    assert calls == []


def test_two_requests_beside_a_context_sentence_still_plan_without_it():
    text = "I run a bakery. Whats the weather in Paris? Also convert 100 USD to EUR."
    calls: list[str] = []
    split = ["I run a bakery.", "Whats the weather in Paris?", "convert 100 USD to EUR."]
    reply = json.dumps([
        {"request": split[0], "operation": "factual_explanation", "depends_on": []},
        {"request": split[1], "operation": "weather_lookup", "depends_on": []},
        {"request": split[2], "operation": "fx_quote", "depends_on": []},
    ])

    def ask_model(_system: str, prompt: str) -> str:
        calls.append(prompt)
        return reply

    plan = plan_conductor_turn(text, ask_model=ask_model, plan_id="t")
    assert len(calls) == 1
    assert plan is not None
    texts = [node.request_text for node in plan.nodes]
    assert not any("bakery" in node for node in texts), texts
    assert any("Paris" in node for node in texts) and any("USD" in node for node in texts), texts


@pytest.mark.parametrize("text", [
    "What is 137 x 29? Also get the current weather for Vilnius",
    "Get the current weather for Vilnius. Then convert 100 USD to EUR.",
])
def test_a_turn_with_no_context_sentence_is_unchanged(text):
    assert without_sentences_that_ask_nothing(text) == text
