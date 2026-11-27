"""One ask for one of VOOL's tools, written as the errand plus how to hand it on, stays whole for the tool lane.

iMac, 3382964ea, qwen3:8b: "Saturday 10:30 I need to grab the dry cleaning, give me a nudge" went to
conductor_multi_intent_plan; no tool ran, no reminder was set, and the reply explained what "give me a nudge"
means. The turn's judge had read the whole message as a Reminders ask, and the conductor has no operation for
VOOL's tools. A real second ask beside a reminder ("tell me a joke, and remind me at 6 to call Ana") must stay two.

The judge's readings are stubs here (what core.plugin_ask records before any lane, and the yes/no that
core.intent_arbiter asks of the turn's model); the request reader and the planners are the real ones.
"""
from __future__ import annotations

import json

import pytest

from core import intent_arbiter, plugin_ask
from core.agent_runtime.agent import _one_tool_ask_keeps_turn_whole
from core.agent_runtime.turn_planner import parts_that_may_only_handle_a_tool_ask, plan_turn

ONE_ASK_AND_HOW_TO_HAND_IT_ON = (
    "Saturday 10:30 I need to grab the dry cleaning, give me a nudge",
    "šeštadienį 10:30 turiu pasiimti drabužius iš valyklos, priminsi man",
    "Samstag um 10:30 muss ich die Reinigung abholen, gib mir Bescheid",
)
A_JOKE_AND_A_REMINDER = (
    ("tell me a joke, and remind me at 6 to call Ana", ["tell me a joke", "remind me at 6 to call Ana"]),
    ("pasakyk anekdotą ir primink 18 val. paskambinti Anai",
     ["pasakyk anekdotą", "primink 18 val. paskambinti Anai"]),
    ("erzähl mir einen Witz und erinnere mich um 6 an Ana", ["erzähl mir einen Witz", "erinnere mich um 6 an Ana"]),
)


@pytest.fixture
def judge(monkeypatch):
    """The turn's judge: Reminders for every message, and a recorded yes/no for "only that tool's job?"."""
    asked: list[tuple[str, str]] = []
    answers: dict[str, str | None] = {}
    monkeypatch.setattr(plugin_ask, "judged_builtins", lambda _text: ("reminders",))

    def only_the_tool_ask(message, tool, **_kwargs):
        asked.append((message, tool))
        return answers.get(message)

    monkeypatch.setattr(intent_arbiter, "arbitrate_only_the_tool_ask", only_the_tool_ask)
    return asked, answers


@pytest.mark.parametrize("text", ONE_ASK_AND_HOW_TO_HAND_IT_ON)
def test_the_errand_and_how_to_hand_it_on_is_one_tool_ask(judge, text):
    asked, answers = judge
    answers[text] = "yes"
    assert _one_tool_ask_keeps_turn_whole(text, {})
    assert asked and asked[0][0] == text and "remind" in asked[0][1].lower()


@pytest.mark.parametrize("text", ONE_ASK_AND_HOW_TO_HAND_IT_ON)
def test_no_answer_from_the_judge_leaves_the_turn_to_its_lanes(judge, text):
    assert not _one_tool_ask_keeps_turn_whole(text, {})


@pytest.mark.parametrize(("text", "parts"), A_JOKE_AND_A_REMINDER)
def test_a_joke_beside_a_reminder_stays_two_asks(judge, monkeypatch, text, parts):
    asked, answers = judge
    answers[text] = "no"  # what the judge answers when the message also asks for something else
    assert not _one_tool_ask_keeps_turn_whole(text, {})
    # The joke in front of the reminder is an ask of its own by shape alone: the judge is not even asked.
    assert not parts_that_may_only_handle_a_tool_ask(text, parts)

    calls: list[str] = []

    def ask_model(_system: str, prompt: str) -> str:
        calls.append(prompt)
        return json.dumps([{"request": part, "depends_on": []} for part in parts])

    # The planners read the turn exactly as they do with no tool picked: nothing here folds a part.
    with_pick = [task.request for task in plan_turn(text, ask_model=ask_model)]
    calls_with_pick = len(calls)
    monkeypatch.setattr(plugin_ask, "judged_builtins", lambda _text: ())
    assert [task.request for task in plan_turn(text, ask_model=ask_model)] == with_pick
    assert len(calls) == 2 * calls_with_pick
    if any(token in text for token in (",", " and ")):  # the generic planner's own joiners admit it
        assert with_pick == parts


def test_an_order_in_front_of_the_reminder_is_never_asked_about(judge):
    asked, answers = judge
    text = A_JOKE_AND_A_REMINDER[0][0]
    answers[text] = "yes"  # even a wrong yes cannot fold a part in front of the facts
    assert not _one_tool_ask_keeps_turn_whole(text, {})
    assert asked == []


def test_the_shape_needs_one_tool_one_part_with_facts_and_no_question_after_it(monkeypatch):
    text = ONE_ASK_AND_HOW_TO_HAND_IT_ON[0]
    errand, handing_on = "Saturday 10:30 I need to grab the dry cleaning", "give me a nudge"
    picks: list[tuple[str, ...]] = [("reminders",)]
    monkeypatch.setattr(plugin_ask, "judged_builtins", lambda _text: picks[0])
    assert parts_that_may_only_handle_a_tool_ask(text, [errand, handing_on]) == {1}
    assert not parts_that_may_only_handle_a_tool_ask(text, [handing_on, errand])
    assert not parts_that_may_only_handle_a_tool_ask(text, [errand, "and what's the capital of France?"])
    assert not parts_that_may_only_handle_a_tool_ask(text, ["what's 17*23", "remind me at 6 to call Ana"])
    picks[0] = ("reminders", "calendar")
    assert not parts_that_may_only_handle_a_tool_ask(text, [errand, handing_on])
    picks[0] = ()
    assert not parts_that_may_only_handle_a_tool_ask(text, [errand, handing_on])


def test_the_judge_question_names_the_tool_and_the_message_and_asks_both_ways():
    prompt = intent_arbiter._only_the_tool_ask_prompt(ONE_ASK_AND_HOW_TO_HAND_IT_ON[0], "Reminders: set a reminder.")
    assert "Reminders: set a reminder." in prompt
    assert ONE_ASK_AND_HOW_TO_HAND_IT_ON[0] in prompt
    assert prompt.count('{"answer": "yes"}') >= 3 and prompt.count('{"answer": "no"}') >= 3
    for example in (*ONE_ASK_AND_HOW_TO_HAND_IT_ON, *(text for text, _parts in A_JOKE_AND_A_REMINDER)):
        assert f'Message: "{example}" -> {{' not in prompt  # the tests' wordings are not the prompt's examples
