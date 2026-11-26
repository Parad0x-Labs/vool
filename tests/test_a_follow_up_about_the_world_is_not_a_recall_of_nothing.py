"""A follow-up about the world the chat asks about is answered by the model, never "I don't have that saved".

iMac gate31 on ceadac1d4 (qwen3:8b): after "who wrote the novel Moby-Dick?", the follow-up "when was he born?" was
read alone by the memory question as a recall, nothing saved named it, this chat's messages do not tell the answer,
and the reply was "I don't have that saved". The chat asks about a public person: the model answers it. A question
about the person's own life with nothing saved is still "not saved". All names here are invented or public.
"""
from __future__ import annotations

import pytest

from core import intent_arbiter, persistent_memory, plugin_ask


@pytest.fixture(autouse=True)
def _a_recall_turn(monkeypatch):
    monkeypatch.setattr(intent_arbiter, "_judge_model_for", lambda source_context: "")
    monkeypatch.setattr(plugin_ask, "judged_memory_ask", lambda text: "recall")
    monkeypatch.setattr(plugin_ask, "judged_tool_ask", lambda text: ())
    intent_arbiter.bind_turn_judge({}, None)
    yield
    intent_arbiter.bind_turn_judge({}, None)


def _judge(world: str, asked: list[str]):
    def answer(prompt: str) -> str:
        asked.append(prompt)
        if "Is the question about the world" in prompt:
            return '{"answer": "%s"}' % world
        return '{"answer": "no"}'  # this chat's messages do not tell the answer
    return answer


def test_a_follow_up_about_a_public_person_is_handed_to_the_model(monkeypatch):
    monkeypatch.setattr(persistent_memory, "_chat_earlier_user_turns",
                        lambda session_id: ["kas sukūrė operą „Karmen“?"])
    asked: list[str] = []
    intent_arbiter.bind_turn_judge({}, _judge("yes", asked))
    assert persistent_memory._judged_recall_with_nothing_behind_it(
        "o kur jis mirė?", session_id="chat-opera", source_context={}) is None
    world = [p for p in asked if "Is the question about the world" in p]
    assert len(world) == 1 and "Karmen" in world[0] and "kur jis mirė" in world[0]


def test_a_question_about_the_persons_own_life_is_still_not_saved(monkeypatch):
    monkeypatch.setattr(persistent_memory, "_chat_earlier_user_turns",
                        lambda session_id: ["my cousin Edvinas moved to Kaunas last spring"])
    intent_arbiter.bind_turn_judge({}, _judge("no", []))
    assert persistent_memory._judged_recall_with_nothing_behind_it(
        "what street does he live on now?", session_id="chat-cousin",
        source_context={}) == persistent_memory.JUDGED_RECALL_NOTHING


def test_an_unread_world_question_keeps_not_saved(monkeypatch):
    monkeypatch.setattr(persistent_memory, "_chat_earlier_user_turns", lambda session_id: ["hello"])
    intent_arbiter.bind_turn_judge({}, _judge("perhaps", []))
    assert persistent_memory._judged_recall_with_nothing_behind_it(
        "where did I park the van?", session_id="chat-van",
        source_context={}) == persistent_memory.JUDGED_RECALL_NOTHING


def test_a_new_chat_is_not_asked_the_world_question(monkeypatch):
    monkeypatch.setattr(persistent_memory, "_chat_earlier_user_turns", lambda session_id: [])
    intent_arbiter.bind_turn_judge({}, lambda prompt: pytest.fail("asked a judge in a new chat"))
    assert persistent_memory._judged_recall_with_nothing_behind_it(
        "what colour is my kayak?", session_id="chat-new",
        source_context={}) == persistent_memory.JUDGED_RECALL_NOTHING


def test_the_world_prompt_shows_the_chat_oldest_first():
    prompt = intent_arbiter._world_follow_up_prompt("and the second one?", ["newest ask", "older ask"])
    assert prompt.index('"older ask"') < prompt.index('"newest ask"')
