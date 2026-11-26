"""A clause of a message the turn's judge read as a file ask is not bound to the machine's specs, disk or processes by
the conductor's machine read; a message the judge did not read as one keeps that binding.

iMac gate40 on 50f8bed75 (qwen3:8b): "dig up the boiler service record somewhere on this machine and tell me when the
next service is due" was judged Find; the model call timed out, the conductor's fallback plan bound the clause to
machine.inspect_specs by the word "machine", and the reply was the computer's specs. Wordings are fresh.
"""
from __future__ import annotations

import pytest

from core import plugin_ask
from core.conductor.operations import _machine_expand

ASK = "fish out the insurance letter stored on this machine and say when it renews"
CLAUSE = "fish out the insurance letter stored on this machine"


@pytest.fixture(autouse=True)
def _clean():
    plugin_ask.forget()
    yield
    plugin_ask.forget()


def test_a_judged_file_ask_is_not_read_as_the_specs():
    plugin_ask._record_builtins(ASK, ("find",))
    assert _machine_expand(CLAUSE) == []


def test_without_a_file_judgement_the_specs_reading_stands():
    assert [row["intent"] for row in _machine_expand(CLAUSE)] == ["machine.inspect_specs"]
    plugin_ask._record_builtins("what are the specs of this machine?", ())
    assert [row["intent"] for row in _machine_expand("what are the specs of this machine?")] == [
        "machine.inspect_specs"]
