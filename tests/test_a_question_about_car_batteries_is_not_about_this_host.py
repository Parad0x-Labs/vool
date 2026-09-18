"""A long question that merely contains "battery" is not a question about this machine.

Measured 2026-08-06, session `openclaw:83851217bbea49b1dcde`: a question about the best-selling
battery-electric CAR was answered "Current state of this host: Uptime 22h 18m, Chassis desktop
(Mac16,3), Battery: none" in 5.7s, via `tool_selected | Running machine.host_state`.

`_HOST_STATE_GENERAL_RE` -- the general-knowledge carve-out that exists for exactly this -- returned
False on that phrasing, and `_BATTERY_RE` matched the word inside "battery-electric". The carve-out
had already been extended once for the same shape: the comment above the gate records
"Write me a short LinkedIn post about which apps are using battery on modern laptops" being answered
with host state.

That is three vocabulary fixes for one failure mode. Length is the discriminator a phrase nobody
anticipated cannot defeat: a question about this host is short and direct.
"""
from __future__ import annotations

import pytest

from core.execution.constants import _MAX_HOST_STATE_QUESTION_WORDS, machine_host_state_intent

EV = ("What is the best-selling battery-electric car model of all time worldwide? Compare the Tesla "
      "Model 3 and Nissan Leaf by cumulative global deliveries or sales. Identify the most common "
      "battery-capacity configuration and the country where it sold the most. Use current evidence "
      "retrieved during this turn. Do not guess.")


@pytest.mark.parametrize(
    "prompt",
    [
        EV,
        "Write me a short LinkedIn post about which apps are using battery on modern laptops.",
        ("Compare EV battery chemistries across manufacturers and explain which offers the best "
         "cycle life, then tell me how uptime of the charging network affects total cost of "
         "ownership over ten years of typical use."),
    ],
)
def test_a_long_question_mentioning_battery_is_not_a_host_state_question(prompt) -> None:
    assert machine_host_state_intent(prompt) is None


@pytest.mark.parametrize(
    "prompt",
    [
        "is this machine on battery?",
        "how long has this machine been up for",
        "is this a laptop or a desktop?",
        "what's my uptime",
        "am i on battery or plugged in",
    ],
)
def test_a_real_host_state_question_is_still_answered(prompt) -> None:
    """The control. This lane exists because uptime and battery change minute to minute and were
    previously answered by a canned specs block or a guessed number."""
    assert machine_host_state_intent(prompt) == "machine.host_state"
    assert len(prompt.split()) <= _MAX_HOST_STATE_QUESTION_WORDS


def test_the_cap_is_generous_enough_for_natural_phrasing() -> None:
    """A cap so tight that an ordinary question fails it would trade one defect for another."""
    assert _MAX_HOST_STATE_QUESTION_WORDS >= 12
    assert len(["how", "long", "has", "this", "machine", "been", "up", "for"]) < _MAX_HOST_STATE_QUESTION_WORDS
