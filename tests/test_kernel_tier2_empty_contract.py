"""TIER 2 (empty-if-true render contract) — block A, review-20260820-141451.

t12: "If 2+2=4, output ABSOLUTELY NOTHING (zero bytes). Otherwise output `FAIL`."
shipped the truth sentence instead of nothing. When the empty-output contract's
condition is established true by a calc receipt, the visible bytes must be empty; the
else-branch literal never ships.
"""
from core.kernel import repl
from core.kernel.evidence_types import TypedClaim, render_typed_answer

Q = "If 2+2=4, output ABSOLUTELY NOTHING (zero bytes). Otherwise output `FAIL`."


def test_empty_if_true_satisfied_when_condition_holds():
    receipts = {"ob1-calc1": "computed locally: Check 2+2 equals 4: 2 + 2 == 4 = true; sources: user"}
    assert repl._empty_if_true_satisfied(Q, receipts) is True


def test_empty_if_true_not_satisfied_when_condition_false():
    receipts = {"ob1-calc1": "computed locally: 2 + 2 == 5 = false"}
    assert repl._empty_if_true_satisfied(Q, receipts) is False


def test_empty_if_true_requires_the_empty_output_demand():
    receipts = {"ob1-calc1": "2 + 2 == 4 = true"}
    assert repl._empty_if_true_satisfied("What is 2+2? Output the number.", receipts) is False


def test_empty_if_true_requires_an_established_condition():
    # the empty demand is present but no calc receipt establishes the condition true
    assert repl._empty_if_true_satisfied(Q, {"user": "the user's message"}) is False


def test_empty_conversational_claim_renders_zero_bytes():
    # the suppression ships an empty conversational claim -> zero visible bytes
    assert render_typed_answer([TypedClaim(text="", ctype="conversational", ref="")], {}) == ""
