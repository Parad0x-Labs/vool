"""R4 action-claim gate on forty cases written after the review (tests/action_claim_fresh_cases.py)."""
from __future__ import annotations

import pytest

from tests.action_claim_fresh_cases import CLAIM, PASS
from tests.test_action_claim_gate_review_corpus import _fresh, gate  # noqa: F401  (fixture + helper)


@pytest.mark.parametrize("text", PASS)
def test_fresh_replies_without_claims_pass_unchanged(text: str) -> None:
    published, record = gate(text)
    assert published == text, record


@pytest.mark.parametrize("text", CLAIM)
def test_fresh_claims_with_no_tool_run_are_corrected(text: str) -> None:
    published, record = gate(text)
    assert record["publication"] == "action_claims_corrected", record
