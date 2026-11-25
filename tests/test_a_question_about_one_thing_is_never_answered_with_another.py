"""A question about one particular thing is never answered with an unrelated saved fact, and a "don't store that"
closing a told fact keeps that message out without turning the whole chat private.

Gate iMac on 0c3d44046, qwen3:8b, a fresh home:
* "what's the name of Ona's goat?" (nothing saved about Ona) was answered "You told me: my uncle Vytas has a parrot
  called Kiwi". The recall judge named no held fact, and the whole-memory question read the goat question as "tell
  me everything" (three of three runs), so the one fact held was shown as its answer.
* "my aunt Ona's goat is named Snaigė, but please don't store that" switched the chat to private without a word, and
  the next "įsimink: ..." (remember: ...) in that chat was refused as a private chat.
"""
from __future__ import annotations

import random

import pytest

import core.persistent_memory as pm
from core import private_chat
from core.private_chat import ON, parse_private_command

_RNG = random.Random(20261125)


def _held() -> list[str]:
    return _RNG.sample(["my uncle Vytas has a parrot called Kiwi", "my gym locker code is 4471",
                        "mano mašina žalia", "my landlord is Mr. Okafor"], 2)


@pytest.mark.parametrize("asked", ["what's the name of Ona's goat?", "which locker did I say I use at the gym?",
                                   "kaip vadinasi Onos ožka?"])
def test_a_question_about_one_thing_does_not_open_the_whole_memory(asked, monkeypatch):
    import core.intent_arbiter as intent_arbiter

    monkeypatch.setattr(pm, "_turn_may_read_memory", lambda *_a, **_k: True)
    monkeypatch.setattr(pm, "_held_for_judgement", lambda **_k: _held())
    monkeypatch.setattr(pm, "_judged_items_named", lambda *_a, **_k: [])
    # The small model's misreading, as measured: the whole-memory question says yes.
    monkeypatch.setattr(intent_arbiter, "arbitrate_whole_memory_ask", lambda *_a, **_k: "yes")
    monkeypatch.setattr(intent_arbiter, "arbitrate_particular_ask", lambda *_a, **_k: "yes")
    assert pm._judged_recall_reply(asked, session_id="one", policy=None, source_context={},
                                   forgotten_too=False) is None
    # Nobody answered the second question: nothing unrelated is shown either.
    monkeypatch.setattr(intent_arbiter, "arbitrate_particular_ask", lambda *_a, **_k: None)
    assert pm._judged_recall_reply(asked, session_id="one", policy=None, source_context={},
                                   forgotten_too=False) is None


def test_the_particular_question_is_asked_of_the_turns_model(monkeypatch):
    import core.intent_arbiter as intent_arbiter

    seen: dict = {}
    monkeypatch.setattr(intent_arbiter, "_judge_model_for", lambda _ctx: "qwen3:8b")
    monkeypatch.setattr(intent_arbiter, "arbitrate_followup", lambda *_a, **k: seen.update(k) or "yes")
    assert intent_arbiter.arbitrate_particular_ask("what's the name of Ona's goat?", source_context={}) == "yes"
    assert seen["model"] == "qwen3:8b" and "one particular thing" in seen["prompt"]


@pytest.mark.parametrize("text", [
    "my aunt Ona's goat is named Snaigė, but please don't store that",
    "my pin is 1234 dont save this",
    "mano PIN 2580, nesaugok šito",
    "mój PIN to 2580, nie zapisuj tego",
])
def test_a_dont_store_closing_a_told_fact_is_this_turn_only(text):
    command = parse_private_command(text)
    assert command is not None and command.action == ON and command.this_turn_only, (text, command)
    assert not command.rest.lower().endswith((" but", ",")), command.rest


@pytest.mark.parametrize("text", ["don't record this", "private chat", "don't record this\nmy boss is Grumblewick",
                                  "I need to vent, keep this between us"])
def test_a_request_said_alone_or_first_still_switches_the_chat(text):
    command = parse_private_command(text)
    assert command is not None and command.action == ON and not command.this_turn_only, (text, command)


def test_the_turn_is_private_and_the_chat_is_not(monkeypatch):
    switched: list = []
    monkeypatch.setattr(private_chat, "is_private", lambda sid: False)
    monkeypatch.setattr(private_chat, "set_private", lambda sid, on: switched.append(on) or on)
    monkeypatch.setattr(private_chat, "mark_request_private", lambda **_k: None)

    class _Agent:
        def _fast_path_result(self, **kwargs):
            raise AssertionError("a told fact is answered as a turn, not as a switch")

    chat = "openclaw:" + "a" * 20
    door: dict = {"chat_id": chat}
    assert private_chat.observe_private_door("my aunt's goat is Snaigė, but please don't store that",
                                             session_id=chat, source_context=door) is True
    turn: dict = {"chat_id": chat}
    assert private_chat.observe_private_turn(_Agent(), "my aunt's goat is Snaigė, but please don't store that",
                                             session_id=chat, source_context=turn) is None
    assert turn.get(private_chat.PRIVATE_TURN_KEY) is True and switched == []


def test_a_keep_refused_on_a_turn_alone_private_does_not_say_the_chat_is(monkeypatch):
    chat = "openclaw:" + "b" * 20
    monkeypatch.setattr(private_chat, "is_private", lambda sid: False)
    said = private_chat.remember_refused_reply({"chat_id": chat})
    assert said == private_chat.REPLY_NOT_KEPT_THIS_TURN and "Private chat" not in said
    monkeypatch.setattr(private_chat, "is_private", lambda sid: True)
    assert private_chat.remember_refused_reply({"chat_id": chat}) == private_chat.REPLY_REMEMBER_REFUSED
