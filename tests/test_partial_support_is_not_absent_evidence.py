"""A partially supported answer is reported as partially supported, not as evidence absent.

Measured 2026-09-06 (served comparison, six of seven claims supported): the turn's fulfillment
outcome carried `evidence_binding:retrieved_evidence_absent_from_answer` because the binding judge
grounds only on FULL coverage and `evidence_discarded` fires on anything less. Evidence that six
sentences restate is not absent from the answer; the outcome now names partial support.
"""
from __future__ import annotations

from core.runtime_task_outcome import output_validation_outcome


def _control(coverage: str, supported: int, unsupported: int) -> dict:
    return {
        "evidence_binding": {
            "has_evidence": True, "grounded": coverage == "full", "evidence_discarded": coverage != "full",
            "coverage": coverage,
            "claim_support": {"coverage": coverage, "supported_claim_ids": [f"c{i}" for i in range(supported)],
                              "unsupported_claim_ids": [f"u{i}" for i in range(unsupported)]},
        },
        "final_ui": {"answer_completeness": {"degenerate": False, "has_content": True, "incomplete": False, "reasons": []}},
    }


def test_partial_coverage_reads_partially_supported() -> None:
    outcome = output_validation_outcome(_control("partial", 6, 1))
    assert outcome is not None
    assert outcome["fulfillment_status"] == "partially_fulfilled"
    assert outcome["failure_codes"] == ["evidence_binding:partially_supported"], outcome


def test_no_supported_claim_still_reads_evidence_absent() -> None:
    outcome = output_validation_outcome(_control("none", 0, 3))
    assert outcome is not None
    assert outcome["failure_codes"] == ["evidence_binding:retrieved_evidence_absent_from_answer"], outcome
