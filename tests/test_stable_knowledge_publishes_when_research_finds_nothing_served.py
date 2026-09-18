"""Served: a stable-knowledge question whose adaptive research finds nothing publishes the model's
answer; a world-facts comparison under the same empty retrieval does not.

Measured 2026-09-07 on the served rig: "my code says IndexError: list index out of range, why does
that happen" was widened to current-information by adaptive research's own ask, two searches found
nothing, and the publication gate refused the model's correct answer for lacking retrieved support.
The widening a provisional lane contributes is now retracted when its retrieval finds nothing
(`core.execution_requirements.retract_provisional_escalation`) -- unless the turn's substance is
facts in the world (`ExecutionRequirements.world_facts_requested`), where an empty retrieval is a
fact the gate must weigh. Both legs run through the same fixture transport with every search
unavailable (`manifest_unavailable.json`).
"""
from __future__ import annotations

import pytest

from tests.test_comparison_coverage_served import CARS, CARS_REPLY, FIX, _Drive

INDEXERROR = "my code says IndexError: list index out of range, why does that happen"
INDEXERROR_REPLY = (
    "An IndexError means you asked a list for a position it does not have. Python lists are "
    "zero-indexed, so a list with 3 items has valid indexes 0, 1 and 2; asking for items[3] raises "
    "IndexError. It usually happens in a loop that runs one step too far, or when the list is empty. "
    "Check len(items) before indexing, or iterate with for item in items instead of by index."
)


@pytest.mark.timeout(600)
def test_a_stable_knowledge_answer_publishes_when_every_search_is_unavailable(tmp_path):
    drive = _Drive(tmp_path, FIX / "manifest_unavailable.json", INDEXERROR_REPLY)
    try:
        out = drive.turn(INDEXERROR)
    finally:
        drive.close()
    answer = out["answer"]
    assert "zero-indexed" in answer and "len(items)" in answer, answer[:400]
    assert "can't publish" not in answer.lower() and "Withheld from this answer" not in answer, answer[:400]
    assert not out["pages"], out["pages"]


@pytest.mark.timeout(600)
def test_a_world_facts_comparison_still_refuses_under_the_same_empty_retrieval(tmp_path):
    drive = _Drive(tmp_path, FIX / "manifest_unavailable.json", CARS_REPLY)
    try:
        out = drive.turn(CARS)
    finally:
        drive.close()
    assert "37 million" not in out["answer"] and "1974" not in out["answer"], out["answer"][:400]
