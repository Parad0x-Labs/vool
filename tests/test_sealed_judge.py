"""SEALED JUDGE LAW — load-bearing proofs.

If the judge is wrong, the council LOST and the bytes stay wrong.
No downstream rescue exists or may exist."""
from __future__ import annotations

import dataclasses
import hashlib

import pytest

from core.council import (
    CouncilRuntime,
    CouncilSpec,
    EscalationPolicy,
    KernelFact,
    Role,
    SeatSpec,
    TaskInput,
    Tier,
)


class Fixed:
    """Scripted seat: fixed R1/ballot text; judge script used verbatim."""

    def __init__(self, r1="", ballot="", adjudicate=""):
        self.r1, self.ballot, self.adj = r1, ballot, adjudicate
        self.capsules = []

    def respond(self, capsule):
        self.capsules.append(capsule)
        if capsule.phase == "R1_BLIND":
            return self.r1
        if capsule.phase == "BALLOT":
            return self.ballot
        if capsule.phase == "ADVISORY":
            return ""
        return self.adj


def sealed_council(judge_text: str):
    spec = CouncilSpec(
        name="sealed",
        seats=(
            SeatSpec("v1", "V1", Role.VOTER, Tier.LOCAL),
            SeatSpec("v2", "V2", Role.CHALLENGER, Tier.LOCAL),
            SeatSpec("j", "JUDGE", Role.JUDGE, Tier.PAID),
        ),
        final_judge_seat_id="j",
        policy=EscalationPolicy(paid_budget=3, judge_batch_size=3, sealed_judge=True),
    )
    models = {
        "v1": Fixed(r1="The total cost is 100 USD because parts sum so."),
        "v2": Fixed(r1="The total cost is 200 USD since items add up."),
        "j": Fixed(adjudicate=judge_text),
    }
    task = TaskInput(task_text="cost?",
                     kernel_facts=(KernelFact("kf", "The invoice total is 150 USD", "t:r1"),))
    return CouncilRuntime(spec, task, models)


JUDGE_RIGHT = (
    "DISPUTE d0: UPHELD_A evidence=receipt:r-1\n"
    "=== FINAL ===\n"
    "The total cost is 100 USD."
)

JUDGE_WRONG = (
    "DISPUTE d0: UPHELD_B evidence=receipt:r-9\n"
    "=== FINAL ===\n"
    "The total cost is 999 USD, which is definitely right."
)


def test_sealed_bytes_are_the_judges_exactly():
    rt = sealed_council(JUDGE_RIGHT)
    receipt = rt.run()
    # byte-exact extraction of the FINAL section — nothing added, nothing fixed
    assert receipt.final_answer == "The total cost is 100 USD."
    assert receipt.final_sha256 == hashlib.sha256(b"The total cost is 100 USD.").hexdigest()
    assert rt._judge_raw_bytes == JUDGE_RIGHT


def test_wrong_judge_is_preserved_and_scored_wrong():
    """Load-bearing anti-cheating proof: the judge errs; NOBODY repairs it."""
    rt = sealed_council(JUDGE_WRONG)
    receipt = rt.run()
    # the wrong claim survives byte-exact — the council LOST, and says so
    assert "999 USD" in receipt.final_answer
    assert receipt.committed_status == "committed"   # well-formed -> committed
    assert receipt.final_sha256 == hashlib.sha256(
        b"The total cost is 999 USD, which is definitely right.").hexdigest()
    # the kernel fact was NOT injected to fix the judge (no rescue path ran)
    assert "invoice total" not in receipt.final_answer


def test_malformed_judge_output_is_refused_not_repaired():
    rt = sealed_council("   \n\n  ")   # empty/malformed
    receipt = rt.run()
    assert receipt.committed_status == "invalid_refused"
    assert receipt.final_answer == ""
    assert receipt.final_sha256 == ""


def test_commit_owner_has_no_semantic_discretion():
    """Mutation attempts on the sealed artifact are structurally impossible:
    the receipt is frozen, re-seal is refused, and no assembly path ran."""
    rt = sealed_council(JUDGE_WRONG)
    receipt = rt.run()
    frozen = dataclasses.replace(receipt)
    assert frozen.final_answer == receipt.final_answer
    with pytest.raises(Exception):
        receipt.final_answer = "repaired text"          # frozen dataclass
    with pytest.raises(RuntimeError):
        rt.run()                                        # double commit refused
    # provenance: the committed bytes trace to the judge call record
    judge_calls = [c for c in receipt.call_records
                   if c.get("phase") == "ADJUDICATE_FINAL"]
    assert len(judge_calls) == 1
