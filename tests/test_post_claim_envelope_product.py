"""The post-claim envelope, at the seam that actually ships a turn.

`tests/test_wire_and_emergency_envelope.py` proves `emergency_decision_payload` in isolation. This
file proves the property survives the one call the product makes,
`VoolAgent._maybe_answer_conductor_turn`, with the real plan/run/reduce/compose path -- because a
guard that holds in the helper and is not reached at dispatch is not a guard.
"""
from __future__ import annotations

from unittest import mock

import pytest

from tests.test_obligation_floor_production_dispatch import (  # noqa: F401
    PROMPT,
    _dispatch,
    agent,
    fetchers,
    model_seams,
)


def _boom(*_a, **_k):
    raise RuntimeError("injected post-claim fault")


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_the_control_turn_is_claimed_and_fulfilled(agent):  # noqa: F811
    result = _dispatch(agent)
    assert result is not None
    assert result["conductor_product_decision"]["disposition"] == "fulfilled"


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_a_composition_fault_is_claimed_as_an_integrity_failure(agent):  # noqa: F811
    with mock.patch("core.conductor.compose_answer", side_effect=_boom):
        result = _dispatch(agent)
    assert result is not None, "a composition fault sent the turn to another lane"
    assert result["conductor_product_decision"]["disposition"] == "integrity_failure"
    assert result["reason"] == "conductor_integrity_failure"


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_a_dispatch_envelope_fault_still_returns_a_claimed_result(agent):  # noqa: F811
    agent._fast_path_result = _boom
    result = _dispatch(agent)
    assert result is not None, "a dispatch-envelope fault sent the turn to another lane"
    assert result["reason"] == "conductor_integrity_failure"
    assert result["task_outcome"] == "failed"
    assert result["conductor_product_decision"]["disposition"] == "integrity_failure"


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_a_broken_serializer_on_the_last_resort_path_still_returns_a_claimed_result(agent):  # noqa: F811
    """BOTH post-claim exits broken at once: the envelope, and the decision's own `to_dict`.

    This is the case that used to raise out of `_maybe_answer_conductor_turn` entirely -- the dict
    literal written to be unfailable called `failed.to_dict()` inside itself.
    """
    from core.conductor.product_decision import ProductDecision

    real = ProductDecision.to_dict
    calls = {"n": 0}

    def _boom_after_the_claim(self):
        # The FIRST `to_dict` is pre-claim: it builds the payload that rides the turn context, and
        # a fault there is a legal decline. Everything after it is post-claim and may not decline.
        calls["n"] += 1
        if calls["n"] > 1:
            raise RuntimeError("injected serializer fault")
        return real(self)

    agent._fast_path_result = _boom
    with mock.patch.object(ProductDecision, "to_dict", _boom_after_the_claim):
        result = _dispatch(agent)

    assert calls["n"] > 1, "the post-claim serializer was never reached"
    assert result is not None, "a broken serializer erased the conductor's claim"
    assert result["reason"] == "conductor_integrity_failure"
    assert result["task_outcome"] == "failed"
    decision = result["conductor_product_decision"]
    assert decision["disposition"] == "integrity_failure"
    assert decision["runtime_task_outcome"]["fulfillment_status"] == "failed"


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_the_last_resort_path_survives_a_decision_that_cannot_be_read_at_all(agent):  # noqa: F811
    """A second, independent reading of the same invariant.

    The test above breaks `to_dict` after the claim. This one replaces the claim gate's decision
    with an object on which NOTHING can be read, so the last-resort path has no attribute to fall
    back on either. It must still return a claimed, failed result.
    """
    from core.conductor.product_decision import ConductorClaimGate

    class _Unreadable:
        def __getattr__(self, _name):
            raise RuntimeError("every attribute raises")

        def to_dict(self):
            raise RuntimeError("every attribute raises")

    agent._fast_path_result = _boom
    with mock.patch.object(ConductorClaimGate, "integrity_failure", lambda _s, _e: _Unreadable()):
        with mock.patch("core.conductor.compose_answer", side_effect=_boom):
            result = _dispatch(agent)

    assert result is not None, "an unreadable decision erased the conductor's claim"
    assert result["reason"] == "conductor_integrity_failure"
    assert result["task_outcome"] == "failed"
    assert result["conductor_product_decision"]["disposition"] == "integrity_failure"


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_a_base_exception_after_the_claim_is_still_claimed(agent):  # noqa: F811
    """`except BaseException`: a KeyboardInterrupt must not un-claim a committed turn."""

    def _hard(*_a, **_k):
        raise KeyboardInterrupt("hard stop")

    with mock.patch("core.conductor.compose_answer", side_effect=_hard):
        result = _dispatch(agent)
    assert result is not None
    assert result["conductor_product_decision"]["disposition"] == "integrity_failure"
