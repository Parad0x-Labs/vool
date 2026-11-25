"""A question the turn's judge read as about the person's own life is never asked back as world-entity ambiguity.

Gate iMac on 3c92e8df3 (qwen3:8b): in a new chat "what's the name of Tadas's tortoise?" was judged a memory recall,
nothing was held, and the entity probe asked "Are you referring to the person named Tadas or another entity named
Tadas?" instead of the answer that it is not saved. The English recall shapes already kept the probe out; the judge's
reading does it in any wording. Names and pets drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core import plugin_ask
from core.entity_ambiguity import single_plain_know_question

NAMES = ("Tadas", "Ona", "Jurgis", "Milda", "Anselm", "Tobiah")
PETS = ("tortoise", "goat", "canary", "ferret", "goldfish")
ASKS = ("what's the name of {n}'s {p}?", "what is {n}'s {p} called?", "how is {n}'s {p} named?")


@pytest.mark.parametrize("seed", range(6))
def test_a_judged_recall_is_out_of_the_probes_jurisdiction(seed) -> None:
    rng = random.Random(f"recall-{seed}")
    question = rng.choice(ASKS).format(n=rng.choice(NAMES), p=rng.choice(PETS))
    plugin_ask._record_builtins(question, ("memory_recall",))
    try:
        assert single_plain_know_question(question) is False
    finally:
        plugin_ask._drop(question)


def test_a_question_judged_no_memory_ask_is_still_the_probes() -> None:
    question = "what is the capital of the country where Mercury was born?"
    plugin_ask._record_builtins(question, ())
    try:
        assert single_plain_know_question(question) is True
    finally:
        plugin_ask._drop(question)
