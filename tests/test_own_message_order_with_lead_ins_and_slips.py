""""What was my last question" and "what did I ask first" are read from this chat's transcript, with a lead-in word
or a slip.

Real-app check v3 on fe98e849 (installed app, local qwen3:8b, 2026-11-22): "um what was my last question :)" was
answered by the model with the message before the last one, and "ok what did i ask frist here thx" was answered with
the fourth message of six. Neither reached the transcript reader: "last question" had no reader at all, and a lead-in
word or a slip kept the first-question reader from seeing its shape. The wording is built from parts at random.
"""
from __future__ import annotations

import random

import pytest

from tests.test_first_question_recall import _recall, _seed_chat, chat_home

LEADS = ["", "um ", "ok ", "hmm ", "Alright ", "so ", "hey "]
TAILS = ["", " :)", " thx", "?", " pls", " ty"]
LAST = ["what was my last question", "whats my previous question", "what was my last message", "what did I just ask",
        "what did i just say", "what was my most recent question here", "what was my lsat quesiton"]
FIRST = ["what did i ask frist here", "what was my first question", "what did I ask you first",
         "what was my frist quesiton", "what did i ask first in this chat"]
QUESTIONS = ["so how many unread emails do I have", "um senders?", "which one matters most", "whats 48 plus 12",
             "what's new in space exploration", "tl;dr the top result?"]


@pytest.mark.parametrize("seed", range(8))
def test_the_last_and_first_question_come_from_the_transcript(chat_home, seed):
    rng = random.Random(f"order-{seed}")
    asked = rng.sample(QUESTIONS, rng.randint(2, 5))
    _seed_chat(f"chat-{seed}", asked)
    last = rng.choice(LEADS) + rng.choice(LAST) + rng.choice(TAILS)
    recalled = _recall(last, session_id=f"chat-{seed}")
    assert recalled is not None and recalled.found, (last, recalled)
    assert recalled.response == f'Your last question in this chat was: "{asked[-1]}"', (last, recalled.response)
    first = rng.choice(LEADS) + rng.choice(FIRST) + rng.choice(TAILS)
    recalled = _recall(first, session_id=f"chat-{seed}")
    assert recalled is not None and recalled.response == f'Your first question in this chat was: "{asked[0]}"', (first, recalled)


@pytest.mark.parametrize(
    "ask",
    [
        "what is the last question on the exam",
        "my last question is about taxes",
        "I wonder what my last question to the doctor should be",
        "what was my last message to Bob about",
        "um what's the last thing on the agenda",
    ],
)
def test_a_last_question_that_is_not_this_chats_keeps_its_lane(chat_home, ask):
    _seed_chat("chat-n", ["what's the biggest file on my machine"])
    assert _recall(ask, session_id="chat-n") is None


@pytest.mark.parametrize("ask", ["um what was my last question :)", "ok what did i ask frist here thx",
                                 "hey whats my previous question"])
def test_the_file_loop_leaves_an_order_question_to_the_transcript(ask):
    from core.clean_loop import route
    from tests.test_clean_local_loop import ON, OWNER, _local_brain

    route.note_clean_turn("sess-order")  # even right after a files turn
    decision = route.decide(ask, source_context=OWNER, session_id="sess-order", resolve_brain=_local_brain, env=ON)
    assert not decision.admit and decision.reason == "same_chat_recall", decision
