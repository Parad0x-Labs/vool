"""The floor at the seam that actually ships an answer to a user.

Everything else in this family proves the conductor package behaves. This file proves the property
survives the one call the product actually makes: `VoolAgent._maybe_answer_conductor_turn`. A
guard that holds in `compose_answer` and is ignored at dispatch is not a guard, and the difference
is invisible from inside the package.

Driven through the real method with the real plan/run/compose path. Only the seams that would leave
this machine are replaced: the planner model, the generation model, and the network fetchers.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from core.conductor.node import NodeLifecycle
from tests.test_canonical_obligation_floor import (  # noqa: F401  (fetchers is a fixture)
    _CRYPTO,
    fetchers,
)

PROMPT = (
    "Fetch the live prices of Ethereum and Solana, then work out how many Solana one "
    "Ethereum buys."
)

PLAN_REPLY = json.dumps(
    [
        {
            "request": "the live prices of Ethereum and Solana",
            "operation": "market_quote",
            "depends_on": [],
        },
        {
            "request": "work out how many Solana one Ethereum buys",
            "operation": "quantitative_reasoning",
            "depends_on": [0],
        },
    ]
)

GENERATION_REPLY = json.dumps(
    {
        "steps": [
            {
                "label": "solana per ethereum",
                "expression": "ethereum_price / solana_price",
                "unit": "",
            }
        ],
        "cannot_determine": [],
    }
)


@pytest.fixture()
def agent():
    from apps.vool_agent import VoolAgent

    instance = VoolAgent.__new__(VoolAgent)
    instance.hive_activity_tracker = None
    instance._emit_runtime_event = lambda *_a, **_k: None
    instance._agent_node_emitter = lambda *_a, **_k: None
    instance._execute_tool_intent = lambda *_a, **_k: None
    instance._fast_path_result = lambda **kwargs: dict(kwargs)
    return instance


@pytest.fixture()
def model_seams():
    with (
        mock.patch(
            "core.agent_runtime.turn_planner_hook.build_planner_ask_model",
            return_value=lambda _s, _p: PLAN_REPLY,
        ),
        mock.patch(
            "core.agent_runtime.turn_planner_hook.build_conductor_ask_model",
            return_value=lambda _s, _p: GENERATION_REPLY,
        ),
        mock.patch(
            "core.agent_runtime.turn_planner_hook.build_pinned_paid_turn_scope",
            return_value=None,
        ),
    ):
        yield


def _outcome_set(state):
    """A minimal outcome set pinned to one state, for driving the dispatch's own branches."""
    from core.conductor.realization import (
        RealizationOutcome,
        RequirementOutcomeSet,
        RequirementRealization,
    )

    realization = RequirementRealization(
        realization_id="r",
        requirement_id="req",
        family="market_quote",
        source_surface="x",
        display_subject="x",
    )
    return RequirementOutcomeSet(
        outcomes=(RealizationOutcome(realization=realization, state=state),)
    )


def _dispatch(agent, source_context: dict | None = None):
    return agent._maybe_answer_conductor_turn(
        effective_input=PROMPT,
        raw_input=PROMPT,
        session_id="s",
        source_context={} if source_context is None else source_context,
    )


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_the_product_seam_ships_the_dependent_value(agent):
    """The whole repair, end to end, through the method the product calls.

    The ratio is the runtime's arithmetic over two fetched prices. Nothing in this test states it,
    and it can only appear if the plan ran, the quotes succeeded, and the exported values reached
    the node that declared an edge to them.
    """
    result = _dispatch(agent)
    assert result is not None, "the conductor declined a turn it must claim"
    expected = _CRYPTO["ethereum"][0] / _CRYPTO["solana"][0]
    assert f"{expected:,.4f}".rstrip("0").rstrip(".") in result["response"], result["response"]


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_the_product_seam_refuses_a_turn_whose_reduction_lost_a_node(agent):
    """P8. A realization bound to a node the report never accounted for is a MISSING REDUCTION.

    Forced by dropping one real outcome on its way out of the scheduler, so the state under test is
    one the runtime can actually reach -- not a hand-built object handed to the seam. Everything
    else about the turn stays real and would otherwise ship.
    """
    from core.conductor import run_conductor_plan as real_run

    def _lossy(plan, **kwargs):
        outcomes = list(real_run(plan, **kwargs))
        return outcomes[1:]

    with mock.patch("core.conductor.run_conductor_plan", side_effect=_lossy):
        result = _dispatch(agent)
    # NOT None. Falling through would hand a message whose bookkeeping the runtime knows is broken
    # to a narrower lane, and the breakage would never surface. The turn is claimed and refused.
    assert result is not None, "an integrity failure fell through to another lane"
    assert result["reason"] == "conductor_integrity_failure"
    assert "complete" in result["response"].casefold()
    decision = result["conductor_product_decision"]
    assert decision["claim"] == "claimed_integrity_failure"
    assert decision["runtime_task_outcome"]["fulfillment_status"] == "failed"


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_the_seam_refuses_a_result_that_escaped_its_prerequisite_closure(agent):
    """P10. The fabrication shape fails closed at the product boundary.

    The dependent's own outcome survives and succeeds; its prerequisite's is dropped. That is
    exactly "a figure produced from a closure that was not there", and the figure must not ship.
    """
    from core.conductor import run_conductor_plan as real_run

    def _drop_prerequisite(plan, **kwargs):
        outcomes = list(real_run(plan, **kwargs))
        return [o for o in outcomes if o.node.operation != "market_quote"]

    with mock.patch("core.conductor.run_conductor_plan", side_effect=_drop_prerequisite):
        result = _dispatch(agent)
    assert result is not None, "a closure escape fell through instead of failing closed"
    assert result["reason"] == "conductor_integrity_failure"
    expected = _CRYPTO["ethereum"][0] / _CRYPTO["solana"][0]
    assert f"{expected:,.4f}".rstrip("0").rstrip(".") not in result["response"], (
        "the fabricated result was shipped inside the integrity refusal"
    )


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_a_fully_served_turn_is_not_refused_by_any_of_the_above(agent):
    """NEGATIVE CONTROL for both. The integrity path is not simply always on."""
    result = _dispatch(agent)
    assert result is not None
    assert result["reason"] == "conductor_multi_intent_plan"
    assert result["conductor_product_decision"]["claim"] == "claimed_success"




def _with_states(plan, states: dict[str, NodeLifecycle], **kwargs):
    """Run the plan for real, then force named operations into a terminal state.

    A forced FAILURE, not a dropped outcome: the node is accounted for and it did not succeed,
    which is an ordinary reportable failure rather than an accounting hole. The two must land in
    different dispositions, and this is what keeps that distinction honest.
    """
    # From the SCHEDULER, not from `core.conductor` -- the package attribute is what the test
    # patches, and reaching for it here would call the patch and recurse until the stack ended.
    from core.conductor.scheduler import run_conductor_plan as real_run

    outcomes = list(real_run(plan, **kwargs))
    for outcome in outcomes:
        forced = states.get(outcome.node.operation)
        if forced is not None:
            outcome.state = forced
            outcome.result = None
            outcome.failure_reason = "forced for this test"
    return outcomes


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_an_unfulfilled_turn_ships_but_does_not_claim_fulfilment(agent):
    """P9. A truthful partial result is worth having; it may not carry a complete turn's weight."""
    with mock.patch(
        "core.conductor.run_conductor_plan",
        side_effect=lambda plan, **kw: _with_states(
            plan, {"quantitative_reasoning": NodeLifecycle.FAILED}, **kw
        ),
    ):
        result = _dispatch(agent)
    assert result is not None, "a truthful partial result was discarded"
    decision = result["conductor_product_decision"]
    assert decision["disposition"] == "partially_fulfilled"
    assert decision["claim"] == "claimed_partial"
    # The exact runtime outcome, not a confidence float. A float cannot say which requirements were
    # answered, and the receipt is what a later turn reads back.
    assert decision["runtime_task_outcome"]["fulfillment_status"] == "partially_fulfilled"
    assert result["source_context"]["conductor_disposition"] == "partially_fulfilled", (
        "the turn shipped without its disposition"
    )


@pytest.mark.usefixtures("fetchers", "model_seams")
def test_a_turn_where_nothing_could_be_served_is_claimed_and_told_truthfully(agent):
    """No ordinary-lane fallthrough. The deleted `answered_count < unserved_count` branch.

    That branch discarded a claimed plan whenever the gaps outnumbered the answers, on the
    reasoning that a narrower lane would answer better. Measured on an eight-request message it
    threw away three correct answers and the front door then refused a clause nobody had asked
    about. A turn the conductor planned and could not serve is reported, not handed on.
    """
    with mock.patch(
        "core.conductor.run_conductor_plan",
        side_effect=lambda plan, **kw: _with_states(
            plan,
            {
                "market_quote": NodeLifecycle.FAILED,
                "quantitative_reasoning": NodeLifecycle.FAILED,
            },
            **kw,
        ),
    ):
        result = _dispatch(agent)
    assert result is not None, "a total failure fell through to the ordinary lane"
    decision = result["conductor_product_decision"]
    assert decision["disposition"] == "failed"
    assert decision["claim"] == "claimed_failure"
    assert decision["runtime_task_outcome"]["fulfillment_status"] == "failed"
    assert result["response"].strip(), "the reader was told nothing at all"
