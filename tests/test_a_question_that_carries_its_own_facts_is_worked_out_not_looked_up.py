"""A question about the person's own life that states what it needs is worked out by the model, never "not saved".

iMac gate53 on efb8e474c: "my flight boards at 6:50 and the flight is 1 hour 55 minutes long, so when do I land?"
was read as a recall, nothing saved named it, and the reply was "I don't have that saved". A question that only asks
about something told before stays "not saved". All names and wordings here are invented for this contract.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter, persistent_memory, plugin_ask

_WHOLE = "Does the message itself give everything needed"


@pytest.fixture(autouse=True)
def _a_recall_turn_in_a_new_chat(monkeypatch):
    monkeypatch.setattr(intent_arbiter, "_judge_model_for", lambda source_context: "")
    monkeypatch.setattr(plugin_ask, "judged_memory_ask", lambda text: "recall")
    monkeypatch.setattr(plugin_ask, "judged_tool_ask", lambda text: ())
    monkeypatch.setattr(persistent_memory, "_chat_earlier_user_turns", lambda session_id: [])
    intent_arbiter.bind_turn_judge({}, None)
    yield
    intent_arbiter.bind_turn_judge({}, None)


def _judge(whole: str, asked: list[str]):
    def answer(prompt: str) -> str:
        asked.append(prompt)
        if _WHOLE in prompt:
            return '{"answer": "%s"}' % whole
        return '{"answer": "no"}'  # not a question about the world
    return answer


@pytest.mark.parametrize("text", [
    "my bus leaves at 7:20 and the ride is 45 minutes, when do I get to work?",
    "turiu 12 eurų, o bilietas kainuoja 4, kiek bilietų galiu nusipirkti?",
])
def test_a_question_that_states_its_own_facts_goes_to_the_model(text):
    asked: list[str] = []
    intent_arbiter.bind_turn_judge({}, _judge("yes", asked))
    assert persistent_memory._judged_recall_with_nothing_behind_it(text, session_id="chat-new", source_context={}) is None
    whole = [p for p in asked if _WHOLE in p]
    assert len(whole) == 1 and text.split()[1] in whole[0]


@pytest.mark.parametrize("verdict", ["no", "maybe"])
def test_a_question_about_something_told_before_is_still_not_saved(verdict):
    intent_arbiter.bind_turn_judge({}, _judge(verdict, []))
    assert persistent_memory._judged_recall_with_nothing_behind_it(
        "what is my landlord's name?", session_id="chat-new",
        source_context={}) == persistent_memory.JUDGED_RECALL_NOTHING
