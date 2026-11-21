"""A plain English greeting is never answered in another language.

On the installed app "hi" came back as "Hola." in an English chat: the English opener pool held a
Spanish line, picked at random. The pool is English only now, and a greeting in another language
gets that language's own word back.
"""
from __future__ import annotations

import random

import pytest

from core.agent_runtime import fast_paths_utility as fp

ENGLISH = ["hi", "hey", "hello", "yo", "sup", "hiya", "heya", "howdy", "hello there", "hey there", "whats up",
           "good morning", "good afternoon", "good evening", "good day", "morning", "evening"]
NOT_ENGLISH = ("hola", "hallo", "bonjour", "ciao", "salut", "privet", "labas", "hej", "ola")


@pytest.mark.parametrize("phrase", ENGLISH)
def test_english_greetings_get_english_openers(phrase: str) -> None:
    for seed in range(300):
        random.seed(seed)
        reply = fp.build_greeting_reply(phrase, want_tail=True).lower()
        words = {w.strip(".,!?—'") for w in reply.split()}
        assert not words & set(NOT_ENGLISH), (phrase, reply)


def test_every_pool_is_english() -> None:
    pools = [fp._GENERIC_OPENERS, fp._MORNING_OPENERS, fp._AFTERNOON_OPENERS, fp._EVENING_OPENERS, fp._NIGHT_OPENERS, fp._DAY_OPENERS]
    for pool in pools:
        for line in pool:
            assert not {w.strip(".,!?—'").lower() for w in line.split()} & set(NOT_ENGLISH), line


@pytest.mark.parametrize(("phrase", "word"), [("hola", "hola"), ("hallo", "hallo")])
def test_a_greeting_in_another_language_gets_its_own_word_back(phrase: str, word: str) -> None:
    for seed in range(50):
        random.seed(seed)
        assert fp.build_greeting_reply(phrase, want_tail=False).lower().startswith(word)
