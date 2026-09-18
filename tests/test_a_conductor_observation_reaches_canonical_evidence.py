"""One canonical evidence contract, proven from both sides at the conductor seam.

Human test at e9d15df0, pinned cloud lane::

    U: Search live web for current BTC price and Gold price, then calculate how many
       ounces of Gold equal one BTC.

    conductor: market_quote:bitcoin SUCCEEDED · market_quote:gold SUCCEEDED
               quantitative_reasoning SUCCEEDED           -> 3/3 succeeded
    visible  : "I didn't run any live lookup on this turn..."

Two live quotes were really fetched and the runtime told the user it had looked nothing up. The
guard was right to ask whether the turn observed anything; it was answered wrongly, because nothing
in `core/conductor` wrote to a channel `core.observation_evidence` reads -- and nothing could, since
`apps.vool_agent` hands `NodeContext` a shallow COPY of `source_context`.

Both halves of the contract are pinned here, because repairing either one alone reintroduces the
other:

    a FAILED / unusable observation      MUST NOT license factual output
    a SUCCESSFUL usable observation      MUST reach canonical evidence exactly once

The discriminator is the effect each operation already declares, never its name -- so no BTC, gold
or weather vocabulary appears in this file's assertions about which nodes count.
"""

from __future__ import annotations

import pytest

from core.conductor.capabilities import OperationCapability, OperationEffect
from core.conductor.evidence import (
    conductor_observations,
    operation_is_an_observation,
    publish_conductor_observations,
)
from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
from core.model_output_guard import turn_ran_observations


def _outcome(operation: str, *, ok: bool, node_id: str = "", fields: tuple[str, ...] = ("price",)):
    node = ConductorNode(
        node_id=node_id or f"n-{operation}",
        operation=operation,
        request_text="",
        required_result_fields=fields,
    )
    if ok:
        return NodeOutcome(
            node=node,
            state=NodeLifecycle.SUCCEEDED,
            result={name: 1 for name in fields},
        )
    return NodeOutcome(node=node, state=NodeLifecycle.FAILED, failure_reason="provider refused")


# ------------------------------------------------- which operations are observations, by DECLARATION


@pytest.mark.parametrize(
    "operation",
    ["market_quote", "weather_lookup", "fx_quote", "place_search", "structured_research"],
)
def test_every_live_observation_family_counts_as_observing(operation):
    """Read off the registry, so a family added later is covered without editing this list."""
    from core.conductor.registry import operation_spec

    spec = operation_spec(operation)
    assert spec is not None, f"{operation} is not registered"
    assert spec.capability.effect is OperationEffect.LIVE_OBSERVATION
    assert operation_is_an_observation(operation) is True


@pytest.mark.parametrize("operation", ["workspace_investigation", "conclusion"])
def test_workspace_evidence_counts_as_observing(operation):
    assert operation_is_an_observation(operation) is True


@pytest.mark.parametrize(
    "operation",
    [
        "calculation",
        "quantitative_reasoning",
        "comparison",
        "factual_explanation",
        "reviewed_safe_knowledge",
        "missing_information",
        "unavailable_action",
        "safe_content_generation",
    ],
)
def test_reasoning_over_facts_is_never_an_observation(operation):
    """The negative control that keeps this from becoming "any successful node is evidence".

    `quantitative_reasoning` is the one that matters most: it SUCCEEDED on the reported turn, and
    counting it would have made the answer look evidenced even if both quotes had failed.
    """
    assert operation_is_an_observation(operation) is False


def test_the_registry_has_no_observation_family_this_module_does_not_know_about():
    """Drift guard: a new effect value must be classified deliberately, not by omission."""
    from core.conductor.registry import known_operations

    for spec in known_operations():
        effect = spec.capability.effect
        expected = effect in {OperationEffect.LIVE_OBSERVATION, OperationEffect.WORKSPACE_EVIDENCE}
        assert operation_is_an_observation(spec.name) is expected, spec.name


# --------------------------------------------------------------- the successful direction (the blocker)


def test_a_successful_observation_node_reaches_canonical_evidence():
    outcomes = [_outcome("market_quote", ok=True, node_id="n1")]
    context: dict = {}
    assert turn_ran_observations(context) is False, "precondition: nothing observed yet"

    published = publish_conductor_observations(context, outcomes)
    assert len(published) == 1
    assert turn_ran_observations(context) is True


def test_the_reported_plan_licenses_its_answer():
    """market_quote x2 + quantitative_reasoning, all succeeded -- the exact reported shape."""
    outcomes = [
        _outcome("market_quote", ok=True, node_id="btc"),
        _outcome("market_quote", ok=True, node_id="gold"),
        _outcome("quantitative_reasoning", ok=True, node_id="ratio", fields=("statement", "value")),
    ]
    context: dict = {}
    published = publish_conductor_observations(context, outcomes)
    assert [e["node_id"] for e in published] == ["btc", "gold"], (
        "only the nodes that OBSERVED may be evidence; the calculation is not"
    )
    assert turn_ran_observations(context) is True


# ------------------------------------------------------- the failed direction (must stay closed)


def test_a_failed_observation_node_licenses_nothing():
    context: dict = {}
    published = publish_conductor_observations(context, [_outcome("market_quote", ok=False)])
    assert len(published) == 1, "the attempt is RECORDED..."
    assert turn_ran_observations(context) is False, "...and is worth nothing as evidence"


def test_a_partial_result_shape_is_not_a_usable_observation():
    """`NodeOutcome.succeeded` requires every declared field; this module must not loosen it."""
    node = ConductorNode(
        node_id="partial", operation="market_quote", request_text="",
        required_result_fields=("price", "currency"),
    )
    outcome = NodeOutcome(node=node, state=NodeLifecycle.SUCCEEDED, result={"price": 1})
    assert outcome.succeeded is False
    context: dict = {}
    publish_conductor_observations(context, [outcome])
    assert turn_ran_observations(context) is False


def test_a_succeeding_calculation_beside_failed_quotes_licenses_nothing():
    """The blocker's mirror image, and the reason the effect gate is not optional."""
    outcomes = [
        _outcome("market_quote", ok=False, node_id="btc"),
        _outcome("market_quote", ok=False, node_id="gold"),
        _outcome("quantitative_reasoning", ok=True, node_id="ratio", fields=("statement", "value")),
    ]
    context: dict = {}
    publish_conductor_observations(context, outcomes)
    assert turn_ran_observations(context) is False


# ------------------------------------------------------------------------------- exactly once


def test_evidence_reaches_the_turn_exactly_once():
    outcomes = [_outcome("market_quote", ok=True, node_id="btc")]
    context: dict = {}
    first = publish_conductor_observations(context, outcomes)
    second = publish_conductor_observations(context, outcomes)
    assert len(first) == 1 and second == []
    node_ids = [
        item.get("node_id") for item in context["runtime_tool_observations"]
    ]
    assert node_ids.count("btc") == 1


def test_an_unknown_operation_is_not_assumed_to_observe():
    assert operation_is_an_observation("no_such_operation_xyz") is False
    assert conductor_observations([_outcome("no_such_operation_xyz", ok=True)]) == []


def test_a_newly_registered_observing_operation_is_covered_without_touching_this_module():
    """The generality claim, exercised: declare the effect and the behaviour follows."""
    from core.conductor.operations import OperationSpec
    from core.conductor.registry import operation_spec, register_operation, unregister_operation

    name = "telescope_reading"
    register_operation(
        OperationSpec(
            name=name,
            description="a brand-new observing family this module has never heard of",
            expand_arguments=lambda *a, **k: [],
            run=lambda *a, **k: {},
            render=lambda *a, **k: "",
            required_result_fields=("magnitude",),
            tool_intent="web.research",
            capability=OperationCapability(
                effect=OperationEffect.LIVE_OBSERVATION,
                domain="astronomy",
                accepted_kinds=frozenset(),
            ),
        )
    )
    try:
        assert operation_spec(name) is not None
        assert operation_is_an_observation(name) is True
        context: dict = {}
        publish_conductor_observations(
            context, [_outcome(name, ok=True, node_id="t1", fields=("magnitude",))]
        )
        assert turn_ran_observations(context) is True
    finally:
        unregister_operation(name)


# ------------------------------------------------------------------------------- the WIRING


def test_the_conductor_dispatch_site_publishes_onto_the_turns_real_context(monkeypatch):
    """Drives `_maybe_answer_conductor_turn` and asserts the REAL context dict was written to.

    Everything above this line passes with the publish call deleted from `apps.vool_agent`, which
    is exactly how a repaired module ships dead: proven in isolation, never wired. Measured --
    removing that one line left the whole rest of this file green.

    So this test asserts on the dict the CALLER owns, not on one the helper was handed. That is the
    specific thing the defect was about: the dispatch site gives `NodeContext` a shallow COPY
    (`dict(source_context or {})`), so evidence written anywhere downstream lands on a detached dict
    and the turn learns nothing.
    """
    import core.agent_runtime.agent as agent_module
    import core.conductor as conductor_pkg
    from apps.vool_agent import VoolAgent

    node = ConductorNode(
        node_id="btc", operation="market_quote", request_text="btc price",
        required_result_fields=("price",),
    )
    outcome = NodeOutcome(node=node, state=NodeLifecycle.SUCCEEDED, result={"price": 1})

    class _Plan:
        plan_id = "conductor-wiring-test"
        nodes = (node,)
        parallel_preferred = False

        def to_dict(self):
            return {"plan_id": self.plan_id}

    class _Composed:
        complete = True
        answered_count = 1
        unserved_count = 0
        text = "Bitcoin: USD 1."

    monkeypatch.setattr(agent_module, "_CONDUCTOR_TEST_HOOK", None, raising=False)
    monkeypatch.setattr(conductor_pkg, "plan_conductor_turn", lambda *a, **k: _Plan(), raising=True)
    monkeypatch.setattr(conductor_pkg, "run_conductor_plan", lambda *a, **k: [outcome], raising=True)
    monkeypatch.setattr(conductor_pkg, "compose_answer", lambda *a, **k: _Composed(), raising=True)

    agent = VoolAgent(backend_name="test-backend", device="conductor-wiring", persona_id="default")
    source_context: dict[str, object] = {"surface": "openclaw"}

    assert turn_ran_observations(source_context) is False

    agent._maybe_answer_conductor_turn(
        effective_input="btc price and gold price then the ratio",
        raw_input="btc price and gold price then the ratio",
        session_id="conductor-wiring-session",
        source_context=source_context,
    )

    assert turn_ran_observations(source_context) is True, (
        "the conductor's successful observation never reached the turn's own context -- this is "
        "the reported blocker: 3/3 nodes succeeded and the answer said no lookup ran"
    )
