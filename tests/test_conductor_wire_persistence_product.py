"""What a real conductor turn persists, read through the real persistence front door.

`tests/test_wire_and_emergency_envelope.py` owns the validation rule. This file drives a genuine
`VoolAgent._maybe_answer_conductor_turn` and hands its actual result to
`terminal_fulfillment_outcome` -- the function persistence calls -- so the rule is pinned where a
record is really written and not only where it is really unit-tested.

The corrupted-disposition cases stand in for the thing that motivated the allow-list: a serialized
decision does not have to come from this version of this runtime, and a validator whose unknown
case is "accept" would wave through whatever a version skew, a truncation or an edit produced.
"""
from __future__ import annotations

import json

import pytest

from core.runtime_task_outcome import (
    WIRE_INCONSISTENCY_CODE,
    WIRE_UNKNOWN_DISPOSITION_CODE,
    FulfillmentStatus,
    terminal_fulfillment_outcome,
)
from tests.test_obligation_floor_production_dispatch import (  # noqa: F401
    PROMPT,
    _dispatch,
    agent,
    fetchers,
    model_seams,
)


@pytest.fixture()
def dispatched(agent, fetchers, model_seams):  # noqa: F811
    """The decision a real turn produced, as it crosses the serialization boundary.

    Only the decision travels: the full result also carries the live source context, which holds a
    lock and is not a wire payload.
    """
    result = _dispatch(agent)
    assert result is not None, "the harness turn did not reach the conductor"
    decision = result["conductor_product_decision"]
    return json.loads(json.dumps(decision))


def _wire(decision: dict, **overrides) -> dict:
    payload = json.loads(json.dumps(decision))
    outcome_status = overrides.pop("fulfillment_status", None)
    payload.update(overrides)
    if outcome_status is not None:
        payload["runtime_task_outcome"]["fulfillment_status"] = outcome_status
    return {"conductor_product_decision": payload}


def test_a_real_fulfilled_turn_persists_as_fulfilled(dispatched):
    """The negative control, and the reason the rule has to be an allow-list rather than a ban."""
    assert dispatched["disposition"] == "fulfilled"
    outcome = terminal_fulfillment_outcome(_wire(dispatched))
    assert outcome.fulfillment_status is FulfillmentStatus.FULFILLED
    assert not outcome.failure_codes


@pytest.mark.parametrize("disposition", ["made_up_disposition", "", "success", "nothing_captured"])
def test_a_real_turn_whose_disposition_is_unrecognized_fails_closed(dispatched, disposition):
    outcome = terminal_fulfillment_outcome(_wire(dispatched, disposition=disposition))
    assert outcome.fulfillment_status is FulfillmentStatus.FAILED
    assert WIRE_UNKNOWN_DISPOSITION_CODE in outcome.failure_codes


def test_a_real_turn_whose_status_is_unrecognized_fails_closed(dispatched):
    outcome = terminal_fulfillment_outcome(_wire(dispatched, fulfillment_status="mostly"))
    assert outcome.fulfillment_status is FulfillmentStatus.FAILED
    assert WIRE_INCONSISTENCY_CODE in outcome.failure_codes


def test_a_real_turn_whose_halves_disagree_fails_closed(dispatched):
    outcome = terminal_fulfillment_outcome(_wire(dispatched, disposition="partially_fulfilled"))
    assert outcome.fulfillment_status is FulfillmentStatus.FAILED
    assert WIRE_INCONSISTENCY_CODE in outcome.failure_codes


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_a_claimed_integrity_failure_persists_as_failed(agent):  # noqa: F811
    """The other real disposition this path produces, end to end."""
    from unittest import mock

    with mock.patch("core.conductor.compose_answer", side_effect=RuntimeError("boom")):
        result = _dispatch(agent)
    decision = json.loads(json.dumps(result["conductor_product_decision"]))
    assert decision["disposition"] == "integrity_failure"
    outcome = terminal_fulfillment_outcome({"conductor_product_decision": decision})
    assert outcome.fulfillment_status is FulfillmentStatus.FAILED
    assert WIRE_UNKNOWN_DISPOSITION_CODE not in outcome.failure_codes
    assert WIRE_INCONSISTENCY_CODE not in outcome.failure_codes
