"""Two fail-open holes at the edges of the ProductDecision cutover.

WIRE. `_validated_conductor_outcome` asked whether a RECOGNIZED disposition disagreed with its
outcome. An unrecognized one matched no rule and was waved through, so `made_up_disposition` beside
FULFILLED persisted as FULFILLED. A validator whose unknown case is "accept" only validates the
records that were already going to be fine.

ENVELOPE. The last-resort post-claim exit is a dict literal specifically so nothing left in it can
fail, and it called `decision.to_dict()` inside that literal. Raising there escaped the dispatch as
a second exception -- the one path written to guarantee a claimed turn could itself lose the claim.
"""
from __future__ import annotations

import json

import pytest

from core.conductor.product_decision import (
    ConductorClaim,
    ExecutionReport,
    ProductDisposition,
    emergency_decision_payload,
    reduce_execution_report,
)
from core.runtime_task_outcome import (
    WIRE_INCONSISTENCY_CODE,
    WIRE_UNKNOWN_DISPOSITION_CODE,
    FulfillmentStatus,
    terminal_fulfillment_outcome,
)


def _persist(disposition: str, status: str):
    return terminal_fulfillment_outcome(
        {
            "conductor_product_decision": {
                "disposition": disposition,
                "claim": "claimed",
                "runtime_task_outcome": {"fulfillment_status": status},
            }
        }
    )


# --- the allow-list, and that it IS one -------------------------------------------------------
@pytest.mark.parametrize(
    "disposition",
    ["made_up_disposition", "", "FULFILLED_", "nothing_captured", "success", "complete"],
)
def test_an_unrecognized_disposition_never_persists_as_fulfilled(disposition):
    outcome = _persist(disposition, "fulfilled")
    assert outcome.fulfillment_status is FulfillmentStatus.FAILED
    assert WIRE_UNKNOWN_DISPOSITION_CODE in outcome.failure_codes
    assert outcome.retryable is False


@pytest.mark.parametrize(
    "disposition", ["partially_fulfilled", "failed", "blocked", "integrity_failure"]
)
def test_a_recognized_disposition_may_not_persist_as_fulfilled(disposition):
    outcome = _persist(disposition, "fulfilled")
    assert outcome.fulfillment_status is FulfillmentStatus.FAILED
    assert WIRE_INCONSISTENCY_CODE in outcome.failure_codes


def test_an_unrecognized_fulfillment_status_fails_closed():
    outcome = _persist("fulfilled", "mostly_fulfilled")
    assert outcome.fulfillment_status is FulfillmentStatus.FAILED
    assert WIRE_INCONSISTENCY_CODE in outcome.failure_codes


@pytest.mark.parametrize(
    ("disposition", "status"),
    [
        ("fulfilled", "fulfilled"),
        ("partially_fulfilled", "partially_fulfilled"),
        ("failed", "failed"),
        ("blocked", "blocked"),
        ("integrity_failure", "failed"),
    ],
)
def test_every_agreeing_pair_persists_unchanged(disposition, status):
    """The negative control: validation must not start rejecting truthful records."""
    outcome = _persist(disposition, status)
    assert outcome.fulfillment_status is FulfillmentStatus(status)
    assert WIRE_UNKNOWN_DISPOSITION_CODE not in outcome.failure_codes
    assert WIRE_INCONSISTENCY_CODE not in outcome.failure_codes


def test_the_unclaimed_decision_still_carries_no_outcome_across_the_wire():
    """NOTHING_CAPTURED serializes with no outcome, so it never reaches the validator at all."""
    payload = json.loads(json.dumps(reduce_execution_report(ExecutionReport()).to_dict()))
    assert payload["disposition"] == ProductDisposition.NOTHING_CAPTURED.value
    assert payload["runtime_task_outcome"] is None
    outcome = terminal_fulfillment_outcome({"conductor_product_decision": payload})
    assert outcome.fulfillment_status is not FulfillmentStatus.FULFILLED


# --- the emergency envelope --------------------------------------------------------------------
class _BrokenDecision:
    """A claimed integrity failure whose own serializer is broken."""

    disposition = ProductDisposition.INTEGRITY_FAILURE
    claim = ConductorClaim.CLAIMED_INTEGRITY_FAILURE
    integrity_codes = ("conductor_integrity:accounting_broken", "post_claim:TypeError: boom")

    def to_dict(self):
        raise TypeError("boom")


class _TotallyBrokenDecision:
    """Nothing on it can be read at all."""

    def __getattr__(self, _name):
        raise RuntimeError("every attribute raises")

    def to_dict(self):
        raise RuntimeError("every attribute raises")


def test_the_envelope_serializes_a_healthy_decision_verbatim():
    decision = reduce_execution_report(ExecutionReport())
    assert emergency_decision_payload(decision) == decision.to_dict()


def test_the_envelope_survives_a_broken_serializer_and_carries_the_verdict():
    payload = emergency_decision_payload(_BrokenDecision())
    assert payload["disposition"] == "integrity_failure"
    assert payload["claim"] == "claimed_integrity_failure"
    assert payload["runtime_task_outcome"]["fulfillment_status"] == "failed"
    assert "post_claim:TypeError: boom" in payload["integrity_codes"]
    json.dumps(payload)


def test_the_envelope_survives_a_decision_that_cannot_be_read_at_all():
    payload = emergency_decision_payload(_TotallyBrokenDecision())
    assert payload["disposition"] == "integrity_failure"
    assert payload["runtime_task_outcome"]["fulfillment_status"] == "failed"
    json.dumps(payload)


def test_the_envelope_never_reclassifies_upward():
    """It carries a verdict. There is no input for which it may report success."""

    class _LyingDecision:
        disposition = ProductDisposition.FULFILLED
        claim = ConductorClaim.CLAIMED_SUCCESS
        integrity_codes = ()

        def to_dict(self):
            raise TypeError("boom")

    payload = emergency_decision_payload(_LyingDecision())
    # The disposition it was handed is transcribed, but the OUTCOME this path persists is the one
    # the claim gate guarantees for a post-claim fault. It cannot emit FULFILLED.
    assert payload["runtime_task_outcome"]["fulfillment_status"] == "failed"
