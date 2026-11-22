"""A message no tool fits is not handed the first tool on the judge's list.

Memory-faults run on b43e6aced (2026-11-22, Mac, local qwen3:8b): "kiek bus 23 kart 4?" (23 times 4) was judged as
choice 1 of 11 built-in tools, Reminders. The reminder tool was seated, the model set out to "set a reminder", and the
person got an approval card instead of 92. A small model reading a message that fits no listed tool tends to name the
first one listed.

"None of them" is now a choice of its own, numbered 0 and listed first, and the tools keep their numbers from 1. The
judge here stands in for that small model: it answers with the first number on the last list of tools it is shown,
whatever the message. The sums are built from parts at random, in several languages.
"""
from __future__ import annotations

import json
import random
import re
from unittest import mock

import pytest

from core import intent_arbiter, plugin_ask
from core.request_trust import OWNER_LOCAL_KEY

_RNG = random.Random(20261123)
OWNER = {OWNER_LOCAL_KEY: True, "surface": "openclaw", "platform": "openclaw", "session_id": "chat-first-choice"}
KEYS = [key for key, _description, _seats in plugin_ask.BUILTIN_CHOICES]
SUMS = ("kiek bus {a} kart {b}?", "wie viel ist {a} mal {b}", "cuánto es {a} por {b}", "what's {a} times {b}?",
        "combien font {a} fois {b} ?", "{a} padauginti iš {b} kiek gausis?")


def _first_listed(prompt: str) -> int:
    tools = [line for line in prompt.splitlines() if line.startswith("Tools: ")][-1]
    return int(re.search(r"(\d+)\. \"", tools).group(1))


@pytest.fixture()
def first_choice_judge(monkeypatch):
    monkeypatch.setenv("VOOL_INTENT_ARBITER", "1")
    monkeypatch.setenv("VOOL_KEEP_JUDGE_MODEL", "qwen3:8b")
    monkeypatch.setattr(intent_arbiter, "_local_models_disabled", lambda: False)
    monkeypatch.setattr(plugin_ask, "_installed_with_tools", lambda: [])
    intent_arbiter.reset_breaker()
    plugin_ask.forget()

    def post(_url, json=None, **_kwargs):
        reply = mock.Mock()
        reply.raise_for_status = mock.Mock()
        content = json["messages"][-1]["content"]
        reply.json.return_value = {"message": {"content": _dumps({"answer": [_first_listed(content)]})}}
        return reply

    with mock.patch("requests.post", side_effect=post):
        yield
    intent_arbiter.reset_breaker()
    plugin_ask.forget()


def _dumps(value) -> str:
    return json.dumps(value)


@pytest.mark.parametrize("form", SUMS)
def test_a_sum_judged_by_a_first_choice_model_is_given_no_tool(first_choice_judge, form):
    text = form.format(a=_RNG.randint(12, 99), b=_RNG.randint(3, 19))
    plugin_ask.judge_turn(text, source_context=OWNER, session_id="chat-first-choice")
    assert plugin_ask.judged_builtins(text) == (), text


def test_none_is_listed_first_and_the_tools_keep_their_numbers():
    descriptions = [description for _key, description, _seats in plugin_ask.BUILTIN_CHOICES]
    prompt = intent_arbiter._plugin_ask_prompt("kiek bus 23 kart 4?", "", descriptions)
    tools = [line for line in prompt.splitlines() if line.startswith("Tools: ")][-1]
    numbers = [int(number) for number in re.findall(r"(\d+)\. \"", tools)]
    assert numbers == list(range(0, len(descriptions) + 1))
    assert intent_arbiter._numbers_reader(len(descriptions))('{"answer": [0]}') == "0"
    named = KEYS.index("reminders") + 1
    assert intent_arbiter._numbers_reader(len(descriptions))(f'{{"answer": [0, {named}]}}') == str(named)
