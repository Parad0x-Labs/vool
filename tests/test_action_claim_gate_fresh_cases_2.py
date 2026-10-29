"""R4 action-claim gate on thirty cases written after REVIEW2 (tests/action_claim_fresh_cases_2.py)."""
from __future__ import annotations

import pytest

from tests.action_claim_fresh_cases_2 import CLAIM, PASS, UNLINED_LANGUAGE_CLAIMS
from tests.test_action_claim_gate_review_corpus import _fresh, gate  # noqa: F401  (fixture + helper)


@pytest.mark.parametrize("text", PASS)
def test_second_fresh_replies_without_claims_pass_unchanged(text: str) -> None:
    published, record = gate(text)
    assert published == text, record


@pytest.mark.parametrize("text", CLAIM)
def test_second_fresh_claims_are_removed_and_corrected(text: str) -> None:
    published, record = gate(text)
    assert record["publication"] == "action_claims_corrected", record
    for sentence in record["claimed_sentences"]:
        assert sentence not in published, (sentence, published)


@pytest.mark.parametrize("text", UNLINED_LANGUAGE_CLAIMS)
def test_replies_in_a_language_without_lines_are_left_untouched(text: str) -> None:
    published, record = gate(text)
    assert published == text
