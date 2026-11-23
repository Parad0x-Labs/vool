"""A frame that asks not to save is never read as a frame for keeping, in any language the privacy reader reads.

Served on 261a03892 and every head since (tests/test_private_chat_served_workflow.py, red in CI): "bitte nicht
speichern: my cat is scared of thunder" (please don't save: ...) was answered "Locked in. I'll remember that." and
the fact was saved, without the model ever being asked. The everyday-remember lane read any short frame before a colon
as "keep this". Now a frame the privacy reader reads as "don't save / don't record" keeps nothing, and a frame that asks
to keep ("nicht vergessen", "for the record") still keeps. The facts are built from parts at random.
"""
from __future__ import annotations

import random

import pytest

from core.memory.everyday_remember import everyday_lead

_RNG = random.Random(20261123)
_FACTS = ("my locker is {n}", "my cat is scared of thunder", "my bike is the {colour} one", "my gate code is {n}")
_COLOURS = ("red", "grey", "green", "blue")
DONT_SAVE = ("bitte nicht speichern", "nicht speichern", "ne pas enregistrer", "no lo guardes", "don't save this",
             "do not record this", "nie zapisuj")
KEEP = ("nicht vergessen", "for the record", "fun fact", "just so you know")


def _fact() -> str:
    return _RNG.choice(_FACTS).format(n=_RNG.randint(10, 999), colour=_RNG.choice(_COLOURS))


@pytest.mark.parametrize("frame", DONT_SAVE)
def test_a_dont_save_frame_keeps_nothing(frame):
    text = f"{frame}: {_fact()}"
    assert everyday_lead(text) is None, text


@pytest.mark.parametrize("frame", KEEP)
def test_a_keeping_frame_still_keeps_the_fact(frame):
    fact = _fact()
    lead = everyday_lead(f"{frame}: {fact}")
    assert lead is not None and lead.fact == fact, (frame, fact, lead)
