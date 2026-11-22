""""How much is <a sum>" asks for the sum, not a price.

Real-app gate on the installed 3dfb2f34 (2026-11-22, web lookups off): "qucik q: how much is 68 plus 53!" got "I didn't
look anything up for this ... so I'm not sending it": the value opener "how much is" was read as a price question
whatever followed it. What follows is now read by the runtime's own calculation grammar; a thing after the opener is
still a price. Numbers, operators and wordings are built from parts at random.
"""
from __future__ import annotations

import itertools
import random

import pytest

from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode
from core.execution_requirements import requirements_for

_RNG = random.Random(20261122)
_LEADS = ("", "quick q: ", "qucik q: ", "hey ", "ok so ", "lol ")
_OPENERS = ("how much is", "how much does")
_OPERATORS = ("plus", "minus", "times", "+", "-", "*", "x", "divided by")
_ENDS = ("", "?", "!", " thx", " pls", " come to", " make")


def _sums() -> list[str]:
    rows = []
    for lead, opener, operator, end in itertools.product(_LEADS, _OPENERS, _OPERATORS, _ENDS):
        a, b = _RNG.randint(11, 9999), _RNG.randint(2, 999)
        rows.append(f"{lead}{opener} {a} {operator} {b}{end}")
    return _RNG.sample(rows, 40)


@pytest.mark.parametrize("text", _sums())
def test_the_value_of_a_sum_is_no_lookup(text):
    assert live_info_mode(None, text, interpretation=None) == "", text
    assert requirements_for(text).answer_mode == "DIRECT", text


_THINGS = ("the iphone 17", "a pint in london", "bitcoin right now", "a flight to lisbon", "a day pass in vienna",
           "the new switch console", "gold today")


@pytest.mark.parametrize("text", [f"{lead}how much is {thing}" for lead, thing in itertools.product(_LEADS[:3], _THINGS)])
def test_the_value_of_a_thing_is_still_a_lookup(text):
    assert live_info_mode(None, text, interpretation=None) == "fresh_lookup", text
    assert requirements_for(text).answer_mode != "DIRECT", text
