"""The largest-file recognizer reads a mistyped head the way the demand grain already does.

Measured live 2026-09-07 (packaged app): "what is the alrgest single file on my machine?" --
one transposition away from "largest" -- matched no machine recognizer, so the turn fell to
the model lane, which proposed a sandbox command with a working directory outside the
workspace. The runtime owns a deterministic tool for exactly this question
(`machine.find_largest`) and a closed typo budget for request heads (adjacent transposition or
one inserted/deleted character, never a substitution). The recognizer now spends that budget
on its own vocabulary before matching; a substitution still misses, and the correct spelling
is unchanged.
"""
from __future__ import annotations

import pytest

from core.execution.constants import machine_largest_intent


def test_the_operators_typo_reaches_the_largest_file_tool() -> None:
    assert machine_largest_intent("what is the alrgest single file on my machine?") == "machine.find_largest"
    assert machine_largest_intent("whats the largets file on this computer") == "machine.find_largest"
    assert machine_largest_intent("show me the bigest folders on my mac") == "machine.find_largest"


def test_the_budget_is_closed_a_substitution_does_not_fold() -> None:
    assert machine_largest_intent("what is the lorgest single file on my machine?") is None
    assert machine_largest_intent("what is the largest single file on my machine?") == "machine.find_largest"
    assert machine_largest_intent("how do i find large files on a mac in general?") is None


@pytest.mark.parametrize(
    "question",
    [
        "what's the heaviest file sitting in my Downloads?",
        "hugest folder on this machine?",
        "which files are the fatest in ~/Movies",
    ],
)
def test_plainer_size_superlatives_reach_the_largest_tool(question: str) -> None:
    assert machine_largest_intent(question) == "machine.find_largest"


@pytest.mark.parametrize(
    "question",
    [
        "the heaviest element in the periodic table",
        "who is the heaviest boxer ever",
    ],
)
def test_plainer_superlatives_without_a_file_or_folder_noun_stay_out(question: str) -> None:
    assert machine_largest_intent(question) is None
