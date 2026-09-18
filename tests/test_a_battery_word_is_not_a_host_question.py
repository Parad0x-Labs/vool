"""A battery or uptime word is not a request for this host's live state.

Instance twelve, found on the closing regression sweep. Measured live 2026-07-30 on a71214e:
"Write me a short LinkedIn post about which apps are using battery on modern laptops." was
answered "Current state of this host:" in 0.9s.

`machine_host_state_intent` already carved out the general-knowledge framings -- its
_HOST_STATE_GENERAL_RE exists precisely so "how long does a MacBook battery last?" is not read as
a battery report. It said nothing about a request to WRITE something on the subject, and
`_BATTERY_RE` is a bare word match. The same shape, one guard along.
"""
from __future__ import annotations

import pytest

from core.execution.constants import machine_host_state_intent

PROSE_WITH_A_HOST_STATE_WORD = (
    "Write me a short LinkedIn post about which apps are using battery on modern laptops.",
    "The battery on my bike light died halfway home in the rain.",
    "Draft an email to IT saying my laptop battery drains in two hours.",
    "Summarise this review of the new MacBook battery life.",
    "Recap the ticket about the laptop battery replacement policy.",
    "My son wants a laptop for university, what should we look for?",
    "In the standup, note that the deploy needs a restart afterwards.",
    "Translate: the computer must be plugged in before the update starts.",
    "The peace process has been running since the reboot of negotiations in 2019.",
    "Her laptop was stolen from the cafe on Pilies street last Tuesday.",
    "Document the restart procedure so the night shift can follow it.",
    "The uptime figure in their marketing deck does not match their status page.",
)

REAL_HOST_STATE_QUESTIONS = (
    "is my battery charging",
    "what is the uptime of this machine",
    "how long has this machine been up",
    "is this a laptop or a desktop",
    "whats my battery at",
    "am i plugged in",
    "when did i last reboot",
    "battery status",
    "uptime",
    "is my laptop on battery or ac",
)


@pytest.mark.parametrize("prose", PROSE_WITH_A_HOST_STATE_WORD)
def test_prose_with_a_host_state_word_does_not_read_this_host(prose: str) -> None:
    assert machine_host_state_intent(prose) is None, prose


@pytest.mark.parametrize("question", REAL_HOST_STATE_QUESTIONS)
def test_a_real_host_state_question_still_reads_this_host(question: str) -> None:
    assert machine_host_state_intent(question) == "machine.host_state", question
