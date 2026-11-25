"""A "please" said after the remember verb only asks; it is not part of the fact kept.

iMac gate on f0a19600e: "remember please my barber's name is ..." was kept as "please my barber's name is ...".
Facts and verbs drawn at random.
"""
from __future__ import annotations

import random

import pytest

from core import persistent_memory as pm

FACTS = ("my barber's name is Arūnas", "my locker code is 4471", "the spare key is under the blue pot",
         "my niece Ieva turns nine in March")
VERBS = ("remember", "remember that", "note", "store this")
PLEASE = ("please", "pls", "please,", "please:")


@pytest.mark.parametrize("seed", range(10))
def test_the_please_after_the_verb_is_left_off(seed) -> None:
    rng = random.Random(f"pl-{seed}")
    fact = rng.choice(FACTS)
    text = f"{rng.choice(VERBS)} {rng.choice(PLEASE)} {fact}"
    assert pm._MEMORY_CAPTURE_RE.match(text).group("fact") == fact
    assert pm._REMEMBER_RE.match(text).group(1) == fact


@pytest.mark.parametrize("fact", ["pleasant weather makes my knee hurt", "plsql is the language my team uses"])
def test_a_fact_whose_first_word_only_starts_like_please_is_kept_whole(fact) -> None:
    assert pm._MEMORY_CAPTURE_RE.match(f"remember {fact}").group("fact") == fact
