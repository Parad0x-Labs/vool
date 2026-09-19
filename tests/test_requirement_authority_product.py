"""PRODUCT BOUNDARY: what the provider is actually called with, what ships, what is persisted.

The owner-boundary suite proves the types refuse the wrong state. This one proves the wrong state
never reaches the seam that matters. They are separate tiers on purpose: a constructor that raises
on an impossible value proves nothing about a pipeline that never builds one, and a green pipeline
proves nothing about a receipt that reclassifies it afterwards.

Three seams, in order of how much damage a defect at each does:

* PROVIDER  -- a spy counts calls and captures arguments. A subject nobody proved must buy zero.
* DISPATCH  -- the real `_maybe_answer_conductor_turn` on the real agent. What ships, and whether
               anything can still return None after the claim.
* PERSIST   -- `terminal_fulfillment_outcome`, which is what a receipt and a checkpoint record.
"""
from __future__ import annotations

import json
from unittest import mock

import pytest

from core.conductor.planner import plan_conductor_turn
from core.conductor.product_decision import (
    ConductorClaim,
    ExecutionReport,
    ProductDisposition,
    reduce_execution_report,
)
from core.conductor.realization import RealizationState
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan
from core.runtime_task_outcome import FulfillmentStatus, terminal_fulfillment_outcome
from tests.conductor_product import compose_product, decide
from tests.semantic_proposer import coordinated, frame, proposer, role
from tests.test_canonical_obligation_floor import (
    _clause,
    _Generation,
    fetchers,
)


def _market(*assets: str, scope: str = "", polarity: str = "affirmed"):
    return frame(
        "market_quote",
        scope=scope or f"the price of {' and '.join(assets)}",
        predicate="price",
        roles=coordinated("asset_subject", *assets),
        polarity=polarity,
    )


def _weather(*places: str, scope: str = "", polarity: str = "affirmed"):
    return frame(
        "weather_lookup",
        scope=scope or f"the weather in {' and '.join(places)}",
        predicate="weather",
        roles=coordinated("location", *places),
        polarity=polarity,
    )


def _drive(prompt: str, *clauses: dict, generation=None, propose_semantics=None):
    plan = plan_conductor_turn(
        prompt,
        ask_model=lambda _s, _p: json.dumps(list(clauses)),
        plan_id="p",
        propose_semantics=propose_semantics,
    )
    assert plan is not None, "the conductor declined a turn it must claim"
    outcomes = run_conductor_plan(
        plan, context=NodeContext(timeout_s=5.0, run_generation=generation)
    )
    return plan, outcomes, compose_product(plan, outcomes)


# --- PROVIDER: what the adapter is called with ---------------------------------------------------


class _WeatherSpy:
    """Records every location the weather provider is asked about, and answers nothing else."""

    def __init__(self) -> None:
        self.locations: list[str] = []

    def __call__(self, location: str, *_a, **_k):
        self.locations.append(str(location))
        return {"temperature_c": 10.0, "condition": "clear", "location": str(location)}


def _weather_calls(text: str, *frames) -> list[str]:
    """Drive capture -> resolve -> realize and report exactly what the provider would receive."""
    from core.conductor.realization import plan_realizations, resolve_capabilities
    from core.conductor.requirement_projection import resolve_ledger
    from core.conductor.requirements import capture_requirements

    ledger = resolve_ledger(capture_requirements(text, propose=proposer(*frames)))
    realizations = resolve_capabilities(plan_realizations(ledger))
    return [
        str(r.arguments.get("entity") or r.arguments.get("location") or "")
        for r in realizations.realizations
        if r.family == "weather_lookup" and r.arguments
    ]


@pytest.mark.usefixtures("fetchers")
def test_two_coordinated_places_become_exactly_two_provider_arguments():
    calls = _weather_calls(
        "Give me the weather in Oslo and Tromso",
        _weather("Oslo", "Tromso", scope="the weather in Oslo and Tromso"),
    )
    assert [c.casefold() for c in calls] == ["oslo", "tromso"]


@pytest.mark.usefixtures("fetchers")
@pytest.mark.parametrize(
    "tail",
    [
        "my grandmother's recipe",
        "the release notes",
        "customer data",
        "core/conductor/planner.py",
        "https://example.com/report",
        "the Q3 report",
        "the blue folder",
    ],
)
def test_text_no_proof_claimed_buys_no_provider_argument(tail):
    """I10 AT THE PROVIDER. The residual-tail rule bought calls for exactly these shapes."""
    calls = _weather_calls(
        f"Give me the weather in Oslo and {tail}",
        _weather("Oslo", scope="the weather in Oslo"),
    )
    assert [c.casefold() for c in calls] == ["oslo"]
    assert all(tail.casefold() not in c.casefold() for c in calls)


@pytest.mark.usefixtures("fetchers")
@pytest.mark.parametrize(
    "separator",
    [" and ", " plus ", " also ", " btw ", "/", ": ", " — ", ", ", "\n", " or "],
)
def test_every_separator_variant_produces_the_same_two_provider_arguments(separator):
    scope = f"the weather in Oslo{separator}Tromso"
    calls = _weather_calls(
        f"Give me {scope}", _weather("Oslo", "Tromso", scope=scope)
    )
    assert [c.casefold() for c in calls] == ["oslo", "tromso"], separator


@pytest.mark.usefixtures("fetchers")
def test_a_negated_frame_buys_no_provider_argument_and_its_sibling_still_does():
    """MIXED POLARITY AT THE PROVIDER. A refusal does not erase an affirmed sibling."""
    from core.conductor.realization import plan_realizations, resolve_capabilities
    from core.conductor.requirement_projection import resolve_ledger
    from core.conductor.requirements import capture_requirements

    ledger = resolve_ledger(
        capture_requirements(
            "Never search the weather in Porto, but do get the price of Gold.",
            propose=proposer(
                _weather("Porto", scope="Never search the weather in Porto", polarity="negated"),
                _market("Gold", scope="do get the price of Gold"),
            ),
        )
    )
    realizations = resolve_capabilities(plan_realizations(ledger))
    families = {r.family for r in realizations.realizations}
    assert families == {"market_quote"}
    assert "Porto" not in json.dumps([dict(r.arguments) for r in realizations.realizations])


@pytest.mark.usefixtures("fetchers")
def test_the_mirrored_polarity_pair_is_independent_at_the_provider_too():
    from core.conductor.realization import plan_realizations, resolve_capabilities
    from core.conductor.requirement_projection import resolve_ledger
    from core.conductor.requirements import capture_requirements

    ledger = resolve_ledger(
        capture_requirements(
            "Get the weather in Porto, but do not look up the price of Gold.",
            propose=proposer(
                _weather("Porto", scope="Get the weather in Porto"),
                _market("Gold", scope="do not look up the price of Gold", polarity="negated"),
            ),
        )
    )
    realizations = resolve_capabilities(plan_realizations(ledger))
    assert {r.family for r in realizations.realizations} == {"weather_lookup"}


@pytest.mark.usefixtures("fetchers")
def test_a_subject_that_resolves_to_nothing_is_reported_and_never_fetched():
    """Palladium. One provider call for Gold, a named line for Palladium, and no guess."""
    # Two clauses, one of which the single-domain lanes do not serve -- otherwise the conductor
    # correctly leaves the turn to `_maybe_answer_live_data_turn` and there is nothing to assert.
    plan, outcomes, composed = _drive(
        "Get the price of Gold and Palladium, and what is 137 x 29?",
        _clause("the price of Gold and Palladium", "market_quote"),
        _clause("what is 137 x 29", "calculation"),
        propose_semantics=proposer(_market("Gold", "Palladium")),
    )
    quoted = {
        str(o.node.arguments.get("entity") or "").casefold()
        for o in outcomes
        if o.node.operation == "market_quote"
    }
    assert "palladium" not in quoted
    assert "Palladium" in composed.text
    assert "could not be identified" in composed.text
    decision = decide(plan, outcomes)
    palladium = next(
        o
        for o in decision.outcome_set.outcomes
        if o.realization.display_subject == "Palladium"
    )
    assert palladium.state is RealizationState.UNRESOLVED_SUBJECT


@pytest.mark.usefixtures("fetchers")
def test_a_ratio_over_an_unservable_operand_is_blocked_not_guessed():
    """Gold + Palladium + ratio. The Palladium realization exists, is typed, and blocks the ratio."""
    plan, outcomes, composed = _drive(
        "Get the price of Gold and Palladium and work out the ratio between them.",
        _clause("the price of Gold and Palladium", "market_quote"),
        _clause("work out the ratio between them", "quantitative_reasoning", (0,)),
        generation=_Generation(("ratio", "gold_price / palladium_price", "x")),
        propose_semantics=proposer(
            _market("Gold", "Palladium", scope="the price of Gold and Palladium"),
            frame(
                "quantitative_reasoning",
                scope="work out the ratio between them",
                predicate="work out",
            ),
        ),
    )
    decision = decide(plan, outcomes)
    states = {
        o.realization.display_subject: o.state for o in decision.outcome_set.outcomes
    }
    assert states["Palladium"] is RealizationState.UNRESOLVED_SUBJECT
    ratio = next(
        o
        for o in decision.outcome_set.outcomes
        if o.realization.family == "quantitative_reasoning"
    )
    assert ratio.state in {
        RealizationState.DEPENDENCY_BLOCKED,
        RealizationState.EXECUTION_FAILED,
    }
    assert decision.disposition is not ProductDisposition.FULFILLED
    assert composed.text


# --- DISPATCH: the real seam --------------------------------------------------------------------


class _Agent:
    """The narrowest thing that can drive `_maybe_answer_conductor_turn` end to end."""

    def __init__(self) -> None:
        from apps.vool_agent import VoolAgent

        self.agent = object.__new__(VoolAgent)
        self.events: list[dict] = []
        self.agent._emit_runtime_event = lambda *_a, **k: self.events.append(dict(k))
        self.agent._agent_node_emitter = lambda *_a, **_k: (lambda *_x, **_y: None)
        self.agent._execute_tool_intent = lambda *_a, **_k: {}
        self.agent.hive_activity_tracker = None
        self.agent._fast_path_result = self._fast_path_result

    @staticmethod
    def _fast_path_result(**kwargs):
        return {
            "response": kwargs["response"],
            "confidence": kwargs["confidence"],
            "reason": kwargs["reason"],
            "source_context": kwargs.get("source_context") or {},
        }

    def dispatch(self, prompt: str, *clauses: dict, propose_semantics=None, generation=None):
        from core.agent_runtime import turn_planner_hook

        context: dict = {}
        with mock.patch.object(
            turn_planner_hook, "build_planner_ask_model",
            lambda *_a, **_k: (lambda _s, _p: json.dumps(list(clauses))),
        ), mock.patch.object(
            turn_planner_hook, "build_conductor_ask_model",
            lambda *_a, **_k: generation,
        ), mock.patch.object(
            turn_planner_hook, "build_pinned_paid_turn_scope", lambda *_a, **_k: None
        ), mock.patch(
            "core.conductor.planner.capture_requirements",
            lambda text, **_k: _captured(text, propose_semantics),
        ):
            return self.agent._maybe_answer_conductor_turn(
                effective_input=prompt,
                raw_input=prompt,
                session_id="s",
                source_context=context,
            ), context


def _captured(text, propose_semantics):
    from core.conductor.requirements import capture_requirements as real

    return real(text, propose=propose_semantics)


@pytest.mark.usefixtures("fetchers")
def test_a_fulfilled_turn_ships_with_its_exact_runtime_outcome():
    agent = _Agent()
    result, context = agent.dispatch(
        "Get the price of Gold and what is 137 x 29?",
        _clause("the price of Gold", "market_quote"),
        _clause("what is 137 x 29", "calculation"),
        propose_semantics=proposer(_market("Gold", scope="the price of Gold")),
    )
    assert result is not None, "the conductor declined a turn it served completely"
    decision = result["conductor_product_decision"]
    assert decision["disposition"] == ProductDisposition.FULFILLED.value
    assert decision["claim"] == ConductorClaim.CLAIMED_SUCCESS.value
    assert decision["runtime_task_outcome"]["fulfillment_status"] == "fulfilled"
    assert context["conductor_disposition"] == "fulfilled"
    assert "Could not be answered" not in result["response"]


@pytest.mark.usefixtures("fetchers")
def test_a_partial_turn_ships_the_answer_and_the_named_gap_together():
    agent = _Agent()
    result, _context = agent.dispatch(
        "Get the price of Gold and Palladium, and what is 137 x 29?",
        _clause("the price of Gold and Palladium", "market_quote"),
        _clause("what is 137 x 29", "calculation"),
        propose_semantics=proposer(_market("Gold", "Palladium")),
    )
    assert result is not None
    decision = result["conductor_product_decision"]
    assert decision["disposition"] == ProductDisposition.PARTIALLY_FULFILLED.value
    assert decision["claim"] == ConductorClaim.CLAIMED_PARTIAL.value
    assert decision["runtime_task_outcome"]["fulfillment_status"] == "partially_fulfilled"
    assert "Palladium" in result["response"], "the gap was not named"


@pytest.mark.usefixtures("fetchers")
def test_a_claimed_turn_never_falls_through_when_composition_breaks():
    """I8 AT THE DISPATCH SEAM. The blanket `except Exception: return None` cannot come back."""
    agent = _Agent()
    with mock.patch(
        "core.conductor.compose_answer", side_effect=TypeError("keyword argument")
    ):
        result, _context = agent.dispatch(
            "Get the price of Gold and what is 137 x 29?",
            _clause("the price of Gold", "market_quote"),
            _clause("what is 137 x 29", "calculation"),
            propose_semantics=proposer(_market("Gold", scope="the price of Gold")),
        )
    assert result is not None, "a claimed turn fell through to the ordinary lane"
    decision = result["conductor_product_decision"]
    assert decision["claim"] == ConductorClaim.CLAIMED_INTEGRITY_FAILURE.value
    assert decision["runtime_task_outcome"]["fulfillment_status"] == "failed"
    assert any("post_claim:TypeError" in c for c in decision["integrity_codes"])
    assert "could not account for every part" in result["response"]


@pytest.mark.usefixtures("fetchers")
def test_a_turn_this_layer_captured_nothing_for_still_declines():
    """NEGATIVE CONTROL for the line above. NOT_CLAIMED is the one state that may hand over."""
    from core.conductor.product_decision import reduce_execution_report

    decision = reduce_execution_report(ExecutionReport())
    assert decision.claim is ConductorClaim.NOT_CLAIMED
    assert decision.runtime_task_outcome is None


# --- PERSIST: what a receipt records --------------------------------------------------------------


def _decision_payload(disposition: ProductDisposition, **overrides):
    from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
    from core.conductor.realization import (
        BoundExecutionPlan,
        NonExecutionEvidence,
        NonExecutionReason,
        RealizationBinding,
        RealizationLedger,
        RequirementRealization,
    )

    realization = RequirementRealization(
        realization_id="r1",
        requirement_id="req:1",
        family="market_quote",
        source_surface="the price of Gold",
        display_subject="Gold",
    )
    if disposition is ProductDisposition.FULFILLED:
        binding = RealizationBinding("r1", bound_node_ids=("n1",))
        node = ConductorNode(node_id="n1", operation="market_quote", request_text="x")
        outcomes = (NodeOutcome(node=node, state=NodeLifecycle.SUCCEEDED, result={"ok": 1}),)
    else:
        binding = RealizationBinding(
            "r1", non_execution=NonExecutionEvidence(NonExecutionReason.CAPABILITY_UNAVAILABLE)
        )
        outcomes = ()
    plan = BoundExecutionPlan(RealizationLedger((realization,)), (binding,))
    decision = reduce_execution_report(
        ExecutionReport(bound_plan=plan, node_outcomes=outcomes)
    )
    payload = decision.to_dict()
    payload.update(overrides)
    return decision, payload


def test_the_persisted_outcome_is_the_decisions_own_and_not_derived_from_prose():
    decision, payload = _decision_payload(ProductDisposition.BLOCKED)
    assert decision.disposition is ProductDisposition.BLOCKED
    outcome = terminal_fulfillment_outcome(
        {
            "response": "Could not be answered:\n- Gold — is not something this runtime can look up",
            "conductor_product_decision": payload,
        }
    )
    assert outcome.fulfillment_status is FulfillmentStatus.BLOCKED


def test_a_non_empty_answer_cannot_make_an_unfulfilled_turn_fulfilled():
    """I7 AT THE RECEIPT. A confident-looking string is not evidence that the work happened."""
    _decision, payload = _decision_payload(ProductDisposition.BLOCKED)
    outcome = terminal_fulfillment_outcome(
        {
            "response": "Here is a long, confident, entirely non-empty reply.",
            "conductor_product_decision": payload,
        }
    )
    assert outcome.fulfillment_status is not FulfillmentStatus.FULFILLED


def test_a_fulfilled_decision_persists_as_fulfilled():
    """NEGATIVE CONTROL. The seam is not simply pessimistic."""
    _decision, payload = _decision_payload(ProductDisposition.FULFILLED)
    outcome = terminal_fulfillment_outcome(
        {"response": "Gold is 2000 USD.", "conductor_product_decision": payload}
    )
    assert outcome.fulfillment_status is FulfillmentStatus.FULFILLED


def test_an_integrity_failure_persists_as_failed_and_is_not_retryable():
    from core.conductor.product_decision import ConductorClaimGate

    _decision, _payload = _decision_payload(ProductDisposition.FULFILLED)
    gate = ConductorClaimGate()
    gate.enter(_decision_payload(ProductDisposition.FULFILLED)[0])
    failed = gate.integrity_failure(RuntimeError("dispatch is broken"))
    outcome = terminal_fulfillment_outcome(
        {
            "response": "anything at all",
            "conductor_product_decision": failed.to_dict(),
        }
    )
    assert outcome.fulfillment_status is FulfillmentStatus.FAILED
    assert not outcome.retryable, "rebuilding the same broken accounting is not a retry"


def test_the_decision_is_read_from_the_turn_context_when_it_is_not_on_the_payload():
    _decision, payload = _decision_payload(ProductDisposition.BLOCKED)
    outcome = terminal_fulfillment_outcome(
        {"response": "a reply"}, source_context={"conductor_product_decision": payload}
    )
    assert outcome.fulfillment_status is FulfillmentStatus.BLOCKED


# --- one piece of work is one provider call -------------------------------------------------------

_BTC_AND_SUM = "What is 137 x 29? Also get the price of BTC."
_BTC_CLAUSES = (
    {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
    {"request": "the price of BTC", "operation": "market_quote", "depends_on": []},
)


@pytest.mark.usefixtures("fetchers")
def test_a_planner_node_and_a_realization_of_the_same_work_produce_one_provider_call():
    """The planner says "Bitcoin", the ledger says "BTC", and the operation says they are one.

    Two spellings of one asset must not become two quotes. This is the whole reason `execution_key`
    belongs to the OPERATION: a surface comparison reads BTC and Bitcoin as different work, and the
    user is charged twice for the same lookup.
    """
    plan, outcomes, _composed = _drive(
        _BTC_AND_SUM,
        *_BTC_CLAUSES,
        propose_semantics=proposer(_market("BTC", scope="the price of BTC")),
    )
    quotes = [o for o in outcomes if o.node.operation == "market_quote"]
    sums = [o for o in outcomes if o.node.operation == "calculation"]
    assert len(quotes) == 1, f"the same asset was priced {len(quotes)} times"
    assert len(sums) == 1, f"the same sum was scheduled {len(sums)} times"
    assert all(b.bound_node_ids for b in plan.bound_plan.bindings)


@pytest.mark.usefixtures("fetchers")
def test_a_surface_the_message_does_not_contain_buys_no_provider_argument():
    """S1/S2 AT THE PROVIDER. A proposer's invention must be located or refused, never repaired."""
    from core.conductor.realization import plan_realizations, resolve_capabilities
    from core.conductor.requirement_projection import resolve_ledger
    from core.conductor.requirements import capture_requirements
    from tests.semantic_proposer import raw_proposer

    # Both orders. An invented member listed FIRST starts its search at the top of the frame scope,
    # where there is text to land on; listed last it starts past the real member, where there is
    # not. A locator that fabricates a span is only visible in the first case, so asserting only
    # the second would leave the guard proven by an empty string.
    for roles in (
        [
            {"role": "location", "text": "Bergen", "group": "g", "ordinal": 0},
            {"role": "location", "text": "Oslo", "group": "g", "ordinal": 1},
        ],
        [
            {"role": "location", "text": "Oslo", "group": "g", "ordinal": 0},
            {"role": "location", "text": "Bergen", "group": "g", "ordinal": 1},
        ],
    ):
        payload = json.dumps(
            {
                "frames": [
                    {
                        "frame_id": "f1",
                        "family": "weather_lookup",
                        "scope": "the weather in Oslo today",
                        "predicate": "weather",
                        "polarity": "affirmed",
                        "roles": roles,
                    }
                ]
            }
        )
        ledger = resolve_ledger(
            capture_requirements(
                "Give me the weather in Oslo today", propose=raw_proposer(payload)
            )
        )
        realizations = resolve_capabilities(plan_realizations(ledger))
        assert realizations.realizations == (), (
            f"a place the user never wrote reached the provider: {roles!r}"
        )


@pytest.mark.usefixtures("fetchers")
def test_a_polarity_the_proposer_did_not_state_buys_no_provider_argument():
    """S3 AT THE PROVIDER. An omitted field must not be read as permission."""
    from tests.semantic_proposer import raw_proposer

    payload = json.dumps(
        {
            "frames": [
                {
                    "frame_id": "f1",
                    "family": "weather_lookup",
                    "scope": "the weather in Oslo",
                    "predicate": "weather",
                    "roles": [
                        {"role": "location", "text": "Oslo", "group": "g", "ordinal": 0}
                    ],
                }
            ]
        }
    )
    from core.conductor.realization import plan_realizations, resolve_capabilities
    from core.conductor.requirement_projection import resolve_ledger
    from core.conductor.requirements import capture_requirements

    ledger = resolve_ledger(
        capture_requirements("Give me the weather in Oslo", propose=raw_proposer(payload))
    )
    assert resolve_capabilities(plan_realizations(ledger)).realizations == ()


@pytest.mark.usefixtures("fetchers")
def test_an_unresolved_subject_is_named_by_itself_and_not_by_its_clause():
    """S11 AT THE PRODUCT. The line is the SUBJECT, exactly -- never the whole request."""
    _plan, _outcomes, composed = _drive(
        "Get the price of Gold and Palladium, and what is 137 x 29?",
        _clause("the price of Gold and Palladium", "market_quote"),
        _clause("what is 137 x 29", "calculation"),
        propose_semantics=proposer(_market("Gold", "Palladium")),
    )
    lines = [line for line in composed.text.splitlines() if line.startswith("- ")]
    palladium = [line for line in lines if "Palladium" in line]
    assert palladium == [
        "- Palladium — could not be identified, so nothing was looked up for it"
    ], f"the failure line did not name the role-owned subject: {palladium!r}"


@pytest.mark.usefixtures("fetchers")
def test_an_unresolved_subject_is_never_reported_as_a_missing_capability():
    """S10 AT THE PRODUCT. "nobody could tell me what that is" is not "we cannot do that"."""
    _plan, _outcomes, composed = _drive(
        "Get the price of Gold and Palladium, and what is 137 x 29?",
        _clause("the price of Gold and Palladium", "market_quote"),
        _clause("what is 137 x 29", "calculation"),
        propose_semantics=proposer(_market("Gold", "Palladium")),
    )
    assert "could not be identified" in composed.text
    assert "is not something this runtime can look up" not in composed.text


@pytest.mark.usefixtures("fetchers")
def test_a_dependent_that_answered_over_an_unresolved_prerequisite_refuses_to_ship():
    """S9 AT THE PRODUCT. The fabrication shape, with a REAL unresolved prerequisite.

    Palladium never resolves, so the ratio's prerequisite realization is UNRESOLVED_SUBJECT. The
    dependent node is then forced to succeed, which is exactly "a figure produced from a closure
    that was not there" -- and the turn must be claimed and refused, not shipped.
    """
    from core.conductor.node import NodeLifecycle

    plan, outcomes, _composed = _drive(
        "Get the price of Gold and Palladium and work out the ratio between them.",
        _clause("the price of Gold and Palladium", "market_quote"),
        _clause("work out the ratio between them", "quantitative_reasoning", (0,)),
        generation=_Generation(("ratio", "gold_price / palladium_price", "x")),
        propose_semantics=proposer(
            _market("Gold", "Palladium", scope="the price of Gold and Palladium"),
            frame(
                "quantitative_reasoning",
                scope="work out the ratio between them",
                predicate="work out",
            ),
        ),
    )
    for outcome in outcomes:
        if outcome.node.operation == "quantitative_reasoning":
            outcome.state = NodeLifecycle.SUCCEEDED
            outcome.result = {
                field: [{"label": "ratio", "value": 1.0, "unit": "x"}]
                for field in outcome.node.required_result_fields
            } or {"steps": []}
            outcome.failure_reason = ""
            # Complete the forcing: the scheduler's own fulfillment verdict was recorded against
            # the REAL (dependency-failed) run and contradicts the forced state -- leaving it
            # stale made "forced to succeed" secretly "partially fulfilled", which the integrity
            # law correctly reports as blocked rather than fabricated.
            outcome.result_fulfillment = None
    decision = decide(plan, outcomes)
    assert decision.disposition is ProductDisposition.INTEGRITY_FAILURE, (
        "a figure computed over a prerequisite that never resolved was shippable"
    )
    assert decision.claim is ConductorClaim.CLAIMED_INTEGRITY_FAILURE


# --- THE REAL PRODUCER, AT THE PROVIDER ------------------------------------------------------------
#
# Everything above injects a stand-in proposer. That is exactly how the real producer came to be
# dead while the suite was green, so this block builds the production call machinery and asserts on
# the ARGUMENTS that come out the far end of it.

_REAL_MESSAGE = "Give me the weather in Oslo and Tromso, and what is 137 x 29?"
_REAL_CLAUSES = json.dumps(
    [
        {"request": "the weather in Oslo and Tromso", "operation": "weather_lookup", "depends_on": []},
        {"request": "what is 137 x 29", "operation": "calculation", "depends_on": []},
    ]
)
_REAL_FRAMES = json.dumps(
    {
        "frames": [
            {
                "family": "weather_lookup",
                "scope": "the weather in Oslo and Tromso",
                "predicate": "weather",
                "polarity": "affirmed",
                "roles": [
                    {"role": "location", "text": "Oslo"},
                    {"role": "location", "text": "Tromso"},
                ],
            }
        ]
    }
)


class _RealProducerAgent:
    """Production call builders; the only stub is the last hop before a provider."""

    def __init__(self, replies: dict[str, str]) -> None:
        self.replies = replies
        self.calls: list[dict] = []
        agent = self

        class _Router:
            def _invoke_manifest(self, *, manifest, request, output_mode, task, source_context):
                kind = str(request.metadata.get("planner_call_kind") or "")
                agent.calls.append({"kind": kind, "schema": request.contract["json_schema"]})
                return None, type("R", (), {"output_text": agent.replies.get(kind, "")})(), None

        self.memory_router = _Router()


@pytest.fixture()
def real_routing(monkeypatch):
    import core.agent_runtime.audit_routing as audit_routing
    import core.agent_runtime.turn_planner_hook as hook

    monkeypatch.setattr(
        audit_routing, "select_audit_manifests", lambda *_a, **_k: (["manifest"], "")
    )
    monkeypatch.setattr(
        audit_routing, "resolve_routing_mode", lambda _ctx: type("R", (), {"pinned": False})()
    )
    monkeypatch.setattr(hook, "_unpaid_manifests", lambda _m: ["manifest"])


def _real_producer_arguments(replies: dict[str, str], message: str = _REAL_MESSAGE):
    """Provider arguments produced by the PRODUCTION call builders, end to end."""
    from core.agent_runtime.turn_planner_hook import (
        build_planner_ask_model,
        build_semantic_proof_ask_model,
    )
    from core.conductor.realization import plan_realizations, resolve_capabilities
    from core.conductor.requirement_projection import resolve_ledger
    from core.conductor.requirements import capture_requirements

    agent = _RealProducerAgent(replies)
    context: dict = {}
    build_planner_ask_model(agent, context)("You split a user's message", message)
    semantic = build_semantic_proof_ask_model(agent, context)(
        "You label the semantic structure", message
    )
    ledger = resolve_ledger(capture_requirements(message, propose=lambda _s, _p: semantic))
    realizations = resolve_capabilities(plan_realizations(ledger))
    return agent, realizations


@pytest.mark.usefixtures("fetchers")
def test_the_real_producer_reaches_the_provider_with_the_semantic_reply(real_routing):
    """P1-P4 AT THE PROVIDER. A shared cache slot or a swapped schema costs every argument."""
    from core.agent_runtime.turn_planner_hook import PlannerCallKind

    agent, realizations = _real_producer_arguments(
        {
            PlannerCallKind.CLAUSE_DECOMPOSITION.value: _REAL_CLAUSES,
            PlannerCallKind.SEMANTIC_PROOF.value: _REAL_FRAMES,
        }
    )
    assert len(agent.calls) == 2, "the semantic proposer did not reach a provider"
    # The two calls' DISTINCT provider contracts, envelope-aware: since 4c38afde the clause call
    # legitimately wires an OBJECT envelope ({"requests": [...]}) because native structured
    # providers require an object root -- the original top-level-array pin predates that. The
    # invariant this always protected is that the two calls carry DIFFERENT schemas (a shared or
    # swapped slot costs every argument); one "requests" envelope and one "frames" envelope, and
    # the clause envelope still carries the ARRAY contract as its payload one level in.
    clause_calls = [c for c in agent.calls if "requests" in (c["schema"].get("properties") or {})]
    semantic_calls = [c for c in agent.calls if "frames" in (c["schema"].get("properties") or {})]
    assert len(clause_calls) == 1 and len(semantic_calls) == 1, agent.calls
    clause_schema = clause_calls[0]["schema"]
    assert clause_schema["type"] == "object"
    assert clause_schema["properties"]["requests"]["type"] == "array"
    weather = [
        r.display_subject for r in realizations.realizations if r.family == "weather_lookup"
    ]
    assert weather == ["Oslo", "Tromso"], (
        "the production producer bought no weather arguments; the semantic path is dead"
    )


@pytest.mark.usefixtures("fetchers")
def test_the_real_producer_buys_nothing_when_the_semantic_stage_is_starved(real_routing):
    """NEGATIVE CONTROL. No semantic reply, no open-domain argument -- and no guess."""
    from core.agent_runtime.turn_planner_hook import PlannerCallKind

    _agent, realizations = _real_producer_arguments(
        {PlannerCallKind.CLAUSE_DECOMPOSITION.value: _REAL_CLAUSES}
    )
    assert [r.family for r in realizations.realizations] == ["calculation"]


# --- FORMAL GRAMMAR FAIL-CLOSED, AT THE PROVIDER ---------------------------------------------------


def _arguments_for(text: str, *frames):
    from core.conductor.realization import plan_realizations, resolve_capabilities
    from core.conductor.requirement_projection import resolve_ledger
    from core.conductor.requirements import capture_requirements

    ledger = resolve_ledger(
        capture_requirements(text, propose=proposer(*frames) if frames else None)
    )
    return resolve_capabilities(plan_realizations(ledger)).realizations


@pytest.mark.usefixtures("fetchers")
@pytest.mark.parametrize(
    "text",
    [
        "convert this CSV to XML for me",
        "convert JPG to PNG",
        "turn PDF into TXT",
        "never convert EUR to JPY",
        "don't convert USD to EUR",
        "EUR to JPY is mentioned in this document",
        'the string "USD to GBP"',
        "compare CSV to XML",
    ],
)
def test_the_mandated_negative_corpus_buys_no_provider_argument(text):
    """P7/P8b AT THE PROVIDER. Membership and authority, measured where the call would be made."""
    assert _arguments_for(text) == (), f"{text!r} authorized work"


@pytest.mark.usefixtures("fetchers")
def test_a_semantic_refusal_over_a_formal_reading_buys_nothing(real_routing):
    """P8 AT THE PROVIDER. The bounded reader sees the "never"; the grammar cannot."""
    assert (
        _arguments_for(
            "never convert 500 EUR to JPY",
            frame(
                "fx_quote",
                scope="never convert 500 EUR to JPY",
                predicate="convert",
                roles=[role("base_currency", "EUR"), role("quote_currency", "JPY")],
                polarity="negated",
            ),
        )
        == ()
    )


@pytest.mark.usefixtures("fetchers")
def test_two_frames_claiming_one_region_buy_nothing():
    """P9 AT THE PROVIDER. Choosing between two readings is a guess, and a guess spends."""
    assert (
        _arguments_for(
            "Give me the weather in Oslo and Tromso",
            frame(
                "weather_lookup",
                scope="the weather in Oslo and Tromso",
                predicate="weather",
                roles=[role("location", "Oslo")],
                frame_id="a",
            ),
            frame(
                "market_quote",
                scope="weather in Oslo",
                predicate="weather",
                roles=[role("asset_subject", "Oslo")],
                frame_id="b",
            ),
        )
        == ()
    )


@pytest.mark.usefixtures("fetchers")
def test_a_role_span_swallowing_another_member_buys_nothing():
    """P10 AT THE PROVIDER."""
    from core.conductor.realization import plan_realizations, resolve_capabilities
    from core.conductor.requirement_projection import resolve_ledger
    from core.conductor.requirements import capture_requirements
    from tests.semantic_proposer import raw_proposer

    payload = json.dumps(
        {
            "frames": [
                {
                    "family": "weather_lookup",
                    "scope": "the weather in Oslo and Tromso",
                    "predicate": "weather",
                    "polarity": "affirmed",
                    "roles": [
                        {"role": "location", "text": "Oslo and Tromso"},
                        {"role": "location", "text": "Oslo"},
                    ],
                }
            ]
        }
    )
    ledger = resolve_ledger(
        capture_requirements(
            "Give me the weather in Oslo and Tromso", propose=raw_proposer(payload)
        )
    )
    assert resolve_capabilities(plan_realizations(ledger)).realizations == ()


# --- F4/F5 NARROW FIXES, AT THE PRODUCT ------------------------------------------------------------


@pytest.mark.usefixtures("fetchers")
def test_one_sum_written_two_ways_runs_once():
    """P11 AT THE PRODUCT. Four notations were four keys, so one sum ran up to four times."""
    _plan, outcomes, _composed = _drive(
        "What is 137 x 29? Also 137*29 please, and get the price of Gold.",
        _clause("What is 137 x 29?", "calculation"),
        _clause("the price of Gold", "market_quote"),
        propose_semantics=proposer(_market("Gold", scope="the price of Gold")),
    )
    sums = [o for o in outcomes if o.node.operation == "calculation"]
    assert len(sums) == 1, f"the same sum executed {len(sums)} times"


@pytest.mark.usefixtures("fetchers")
def test_a_raising_registry_claims_an_integrity_failure_rather_than_declining(real_routing):
    """P12 AT THE PRODUCT. A software defect must not become a silent hand-off."""
    from core.conductor.realization import (
        plan_realizations,
        resolve_capabilities,
    )
    from core.conductor.requirement_projection import resolve_ledger
    from core.conductor.requirements import capture_requirements

    ledger = resolve_ledger(
        capture_requirements(
            "Get the price of Gold", propose=proposer(_market("Gold", scope="the price of Gold"))
        )
    )
    with mock.patch(
        "core.conductor.realization.operation_spec", side_effect=RuntimeError("registry is broken")
    ):
        realizations = resolve_capabilities(plan_realizations(ledger))
    states = {r.lookup.state.value for r in realizations.realizations}
    assert states == {"defect"}, states
    assert "unavailable" not in states, "a broken registry was reported as a missing capability"


def test_an_unclaimed_decision_survives_serialization_at_the_seam():
    """P13 AT THE PRODUCT. The decline is a DECISION, not an AttributeError on the way to one."""
    decision = reduce_execution_report(ExecutionReport())
    payload = decision.to_dict()
    assert payload["runtime_task_outcome"] is None
    assert json.dumps(payload)
    assert terminal_fulfillment_outcome(
        {"response": "another lane answered", "conductor_product_decision": payload}
    ).fulfillment_status is FulfillmentStatus.FULFILLED


@pytest.mark.parametrize(
    "disposition,status",
    [
        ("partially_fulfilled", "fulfilled"),
        ("failed", "fulfilled"),
        ("blocked", "fulfilled"),
        ("integrity_failure", "fulfilled"),
    ],
)
def test_a_contradictory_wire_decision_never_persists_as_fulfilled(disposition, status):
    """P14 AT THE PRODUCT. The constructor cannot guard a dict; the seam that reads it must."""
    from core.runtime_task_outcome import WIRE_INCONSISTENCY_CODE

    outcome = terminal_fulfillment_outcome(
        {
            "response": "a confident-looking reply",
            "conductor_product_decision": {
                "disposition": disposition,
                "claim": "claimed_partial",
                "runtime_task_outcome": {
                    "fulfillment_status": status,
                    "failure_stage": "conductor_product_decision",
                    "failure_codes": [],
                    "retryable": True,
                },
            },
        }
    )
    assert outcome.fulfillment_status is not FulfillmentStatus.FULFILLED
    assert WIRE_INCONSISTENCY_CODE in outcome.failure_codes
