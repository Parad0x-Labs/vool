"""A message that asks, inside other words, that it is not kept is kept out of every store, by the turn's model.

iMac gate on 64798c0fb (qwen3:8b): "btw my running coach ... has a ferret called ..., but don't store that anywhere"
was judged no memory ask at all, the model said it would keep it in mind, and the message was stored with the chat
(vool_memory.db memory_nodes). The words only decide whether the model is asked; the model's "no" keeps the turn out.
People, pets and refusals drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core import intent_arbiter, private_chat
from core.semantic.semantic_admissions import bound_request_context

PEOPLE = ("my yoga teacher Ona", "my landlord Petras", "our neighbour Mia", "my boss Karl")
PETS = ("a ferret called Bolt", "two goldfish", "a cat named Saga", "an old pony")
REFUSALS = ("but don't store that anywhere", "please never write this down", "keep that part to yourself",
            "this stays between us", "das bitte nicht speichern", "don't keep it in your memory, ok?")
PLAIN = ("what is the capital of Peru?", "remind me at 9 to call mum", "my dog is not a fan of baths",
         "I keep forgetting where I park")


@pytest.fixture
def judged(monkeypatch):
    asked: list[str] = []

    def judge(verdict):
        monkeypatch.setattr(intent_arbiter, "arbitrate_keep_wanted", lambda text, **_k: asked.append(text) or verdict)
        return asked
    return judge


@pytest.mark.parametrize("seed", range(8))
def test_a_judged_refusal_keeps_the_turn_out(seed, judged) -> None:
    rng = random.Random(f"ref-{seed}")
    text = f"{rng.choice(PEOPLE)} has {rng.choice(PETS)}, {rng.choice(REFUSALS)}"
    asked = judged("no")
    context: dict = {private_chat.NOT_ON_KEY: True}
    with bound_request_context(f"req-ref-{seed}"):
        assert private_chat.judge_keep_refusal(text, context, "chat-ref") is True
        assert private_chat.request_is_private()
    assert asked == [text]
    assert context[private_chat.PRIVATE_TURN_KEY] is True and private_chat.NOT_ON_KEY not in context


@pytest.mark.parametrize("verdict", ["yes", None])
def test_without_a_no_the_turn_is_as_it_was(verdict, judged) -> None:
    judged(verdict)
    context: dict = {}
    with bound_request_context(f"req-keep-{verdict}"):
        assert private_chat.judge_keep_refusal("my aunt has a cat, keep that to yourself", context, "chat-k") is False
        assert not private_chat.request_is_private()
    assert private_chat.PRIVATE_TURN_KEY not in context


@pytest.mark.parametrize("text", PLAIN)
def test_a_message_whose_words_ask_nothing_of_the_kind_is_not_put_to_the_model(text, judged) -> None:
    asked = judged("no")
    assert private_chat.judge_keep_refusal(text, {}, "chat-p") is False
    assert asked == []


def test_the_model_is_told_only_this_message_is_kept_out_not_that_the_chat_is_private() -> None:
    block = private_chat.this_turn_prompt_block()
    assert "THIS message is not kept" in block and "not private" in block
    assert "PRIVATE CHAT IS ON" not in block
