"""A world question is answered as asked when the file the model guessed for it is not there.

iMac gate38 (Offline, qwen3:8b): "who wrote Romeo and Juliet?" was judged to need none of the person's things; the
model still read Desktop/Romeo_and_Juliet.txt, and the turn ended failed on "There is no file at ...". Fresh wordings.
"""
from __future__ import annotations

import pytest

from core import plugin_ask
from core.agent_runtime.turn_reasoning import _a_missing_file_the_ask_never_named

MISSED = {"success": False, "status": "not_found", "response": "There is no file at `Desktop/Hamlet.txt`."}


@pytest.fixture(autouse=True)
def _fresh():
    plugin_ask.forget()
    yield
    plugin_ask.forget()


def test_a_judged_world_question_is_not_ended_by_the_miss():
    asked = "which composer wrote the Brandenburg concertos?"
    plugin_ask._record_builtins(asked, ())
    assert _a_missing_file_the_ask_never_named(MISSED, asked)


@pytest.mark.parametrize("picked", [("find",), ("read",), ("notes",)])
def test_an_ask_for_the_persons_things_keeps_the_miss(picked):
    asked = "open my hamlet essay draft"
    plugin_ask._record_builtins(asked, picked)
    assert not _a_missing_file_the_ask_never_named(MISSED, asked)


def test_with_no_judgement_the_miss_stands():
    assert not _a_missing_file_the_ask_never_named(MISSED, "who painted the night watch?")


@pytest.mark.parametrize("execution", [
    {"success": True, "status": "executed"},
    {"success": False, "status": "not_allowed"},
])
def test_other_outcomes_stand(execution):
    asked = "how tall is the eiffel tower?"
    plugin_ask._record_builtins(asked, ())
    assert not _a_missing_file_the_ask_never_named(execution, asked)
