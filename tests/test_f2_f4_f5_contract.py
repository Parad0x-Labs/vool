"""I1-I10: the mechanical invariants, at the boundary that OWNS each one.

Every invariant is asserted twice here -- once at its producer (the stage that establishes it) and
once at its consumer (the stage that would break if it were false). The PRODUCT boundary for the
same ten lives in `tests/test_requirement_authority_product.py`, and the load-bearing mutations in
`tests/test_f2_f4_f5_sabotage.py`. An invariant proven at only one tier is a survivor, not a proof:
a hand-built impossible state can show a constructor refuses it while the real pipeline never
builds one, and a product assertion can pass on a turn that never reached the seam.

    I1   every executable provider argument references a proven, affirmed, role-owned span
    I2   every required UserRequirement has >= 1 RequirementRealization
    I3   every realization has exactly one: bound nodes XOR typed non-execution evidence
    I4   dedup preserves the ordered realization-id set exactly
    I5   every successful node result reduces to every realization bound to it
    I6   a SATISFIED requirement contributes zero failure lines
    I7   a disposition other than FULFILLED can never persist as RuntimeTaskOutcome.FULFILLED
    I8   after the claim, no path returns None or NOT_CLAIMED
    I9   a dependent realization cannot SATISFY unless its prerequisites did
    I10  UNKNOWN / UNCLAIMED / ABSTAINED spans have zero reachable provider arguments
"""
from __future__ import annotations

import json

import pytest

from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
from core.conductor.planner import plan_conductor_turn
from core.conductor.product_decision import (
    ClaimViolationError,
    ConductorClaim,
    ConductorClaimGate,
    ExecutionReport,
    ProductDecision,
    ProductDisposition,
    reduce_execution_report,
)
from core.conductor.realization import (
    BoundExecutionPlan,
    CapabilityLookupState,
    NonExecutionEvidence,
    NonExecutionReason,
    RealizationBinding,
    RealizationLedger,
    RealizationState,
    RequirementRealization,
    display_subject,
    plan_realizations,
    reduce_bound_plan,
    resolve_capabilities,
)
from core.conductor.registry import NodeContext, execution_key, operation_spec
from core.conductor.requirement_projection import resolve_ledger
from core.conductor.requirements import capture_requirements
from core.conductor.scheduler import run_conductor_plan
from core.runtime_task_outcome import (
    FulfillmentStatus,
    RuntimeTaskOutcome,
    terminal_fulfillment_outcome,
)
from tests.conductor_product import compose_product, decide
from tests.semantic_proposer import coordinated, frame, proposer, role
from tests.test_canonical_obligation_floor import (  # noqa: F401  (fetchers is a fixture)
    _clause,
    _Generation,
    fetchers,
)


def _drive(prompt: str, *clauses: dict, generation=None, propose_semantics=None):
    plan = plan_conductor_turn(
        prompt,
        ask_model=lambda _s, _p: json.dumps(list(clauses)),
        plan_id="c",
        propose_semantics=propose_semantics,
    )
    assert plan is not None, "the conductor declined a turn it must claim"
    outcomes = run_conductor_plan(
        plan, context=NodeContext(timeout_s=5.0, run_generation=generation)
    )
    return plan, outcomes, compose_product(plan, outcomes)


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


def _realization(rid: str, requirement: str = "req:1", family: str = "market_quote", **kw):
    return RequirementRealization(
        realization_id=rid,
        requirement_id=requirement,
        family=family,
        source_surface=kw.pop("surface", "the price of Gold"),
        display_subject=kw.pop("display_subject", "Gold"),
        **kw,
    )


def _bound(*pairs) -> BoundExecutionPlan:
    """`(realization, node_ids_or_evidence)` into a plan, exercising the real constructor."""
    realizations = tuple(r for r, _ in pairs)
    bindings = []
    for realization, target in pairs:
        if isinstance(target, NonExecutionEvidence):
            bindings.append(
                RealizationBinding(realization.realization_id, non_execution=target)
            )
        else:
            bindings.append(
                RealizationBinding(realization.realization_id, bound_node_ids=tuple(target))
            )
    return BoundExecutionPlan(RealizationLedger(realizations), tuple(bindings))


def _outcome(node_id: str, *, state=NodeLifecycle.SUCCEEDED, operation="market_quote"):
    node = ConductorNode(node_id=node_id, operation=operation, request_text="x")
    return NodeOutcome(node=node, state=state, result={"ok": 1} if state is NodeLifecycle.SUCCEEDED else None)


# --- I1: every executable provider argument traces to a proven, affirmed, role-owned span --------


def test_i1_producer_arguments_are_built_only_from_role_owned_surfaces():
    ledger = resolve_ledger(
        capture_requirements(
            "Get the price of Gold and Silver",
            propose=proposer(_market("Gold", "Silver")),
        )
    )
    realizations = resolve_capabilities(plan_realizations(ledger))
    owned = {
        slot.surface
        for requirement in ledger.requirements
        for slot in requirement.slots
    }
    for realization in realizations.realizations:
        assert realization.display_subject in owned
        assert realization.lookup.state is CapabilityLookupState.AVAILABLE


def test_i1_consumer_a_negated_frame_reaches_no_provider_argument():
    ledger = resolve_ledger(
        capture_requirements(
            "Get the price of Gold but never the price of Silver",
            propose=proposer(
                _market("Gold", scope="the price of Gold"),
                _market("Silver", scope="never the price of Silver", polarity="negated"),
            ),
        )
    )
    realizations = resolve_capabilities(plan_realizations(ledger))
    subjects = {r.display_subject for r in realizations.realizations}
    assert subjects == {"Gold"}
    assert all("Silver" not in json.dumps(dict(r.arguments)) for r in realizations.realizations)


# --- I2: every required requirement has at least one realization ---------------------------------


def test_i2_producer_every_executable_requirement_realizes_before_any_argument_exists():
    ledger = resolve_ledger(
        capture_requirements(
            "Get the price of Gold and Palladium and work out the ratio between them, and "
            "region: eu-west-1",
            propose=proposer(
                _market("Gold", "Palladium", scope="the price of Gold and Palladium"),
                frame(
                    "quantitative_reasoning",
                    scope="work out the ratio between them",
                    predicate="work out",
                ),
            ),
        )
    )
    # `plan_realizations` alone -- no capability lookup, no arguments, no nodes.
    realizations = plan_realizations(ledger)
    assert all(r.arguments == {} and r.execution_key == "" for r in realizations.realizations)
    assert {r.requirement_id for r in realizations.realizations} == {
        r.requirement_id for r in ledger.executable
    }
    assert set(r.requirement_id for r in ledger.executable) <= set(
        r.requirement_id for r in realizations.realizations
    )


def test_i2_consumer_a_bound_plan_cannot_omit_a_realization():
    a, b = _realization("r1"), _realization("r2")
    with pytest.raises(ValueError, match="ordered realization-id set"):
        BoundExecutionPlan(
            RealizationLedger((a, b)),
            (RealizationBinding("r1", bound_node_ids=("n1",)),),
        )


def test_i2_realization_ids_are_unique():
    with pytest.raises(ValueError, match="unique"):
        RealizationLedger((_realization("r1"), _realization("r1")))


# --- I3: exactly one of bound nodes / typed non-execution evidence -------------------------------


@pytest.mark.parametrize(
    "nodes,evidence",
    [
        ((), None),
        (("n1",), NonExecutionEvidence(NonExecutionReason.CAPABILITY_UNAVAILABLE)),
    ],
)
def test_i3_producer_a_binding_refuses_neither_and_both(nodes, evidence):
    with pytest.raises(ValueError, match="exactly one"):
        RealizationBinding("r1", bound_node_ids=nodes, non_execution=evidence)


@pytest.mark.usefixtures("fetchers")
def test_i3_consumer_the_real_pipeline_gives_every_realization_exactly_one():
    plan, _outcomes, _composed = _drive(
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
    assert len(plan.bound_plan.bindings) == len(plan.bound_plan.realization_ledger)
    for binding in plan.bound_plan.bindings:
        assert bool(binding.bound_node_ids) == (binding.non_execution is None)


# --- I4: dedup changes node cardinality, never realization cardinality ---------------------------


def test_i4_producer_two_realizations_of_one_work_share_a_node_and_keep_both_ids():
    spec = operation_spec("market_quote")
    arguments = {"entity": "Gold", "asset": "gold"}
    key = execution_key(spec, arguments)
    twins = tuple(
        _realization(f"r{i}", execution_key=key, arguments=arguments) for i in (1, 2)
    )
    plan = _bound((twins[0], ("shared",)), (twins[1], ("shared",)))
    assert plan.realization_ledger.ids == ("r1", "r2")
    assert plan.node_ids == ("shared",), "one execution key is one node"


def test_i4_consumer_both_twins_are_accounted_for_by_the_single_node():
    spec = operation_spec("market_quote")
    key = execution_key(spec, {"entity": "Gold", "asset": "gold"})
    twins = tuple(_realization(f"r{i}", execution_key=key) for i in (1, 2))
    plan = _bound((twins[0], ("shared",)), (twins[1], ("shared",)))
    outcomes = reduce_bound_plan(plan, [_outcome("shared")])
    assert [o.state for o in outcomes.outcomes] == [RealizationState.SATISFIED] * 2


def test_i4_meaningfully_different_work_never_false_dedups():
    spec = operation_spec("market_quote")
    gold = execution_key(spec, {"entity": "Gold", "asset": "gold"})
    silver = execution_key(spec, {"entity": "Silver", "asset": "silver"})
    assert gold != silver
    plan = _bound(
        (_realization("r1", execution_key=gold), ("n-gold",)),
        (_realization("r2", execution_key=silver), ("n-silver",)),
    )
    assert plan.node_ids == ("n-gold", "n-silver")


# --- I5: a successful node reduces to every realization bound to it ------------------------------


def test_i5_producer_one_success_satisfies_every_bound_realization():
    plan = _bound(
        (_realization("r1"), ("shared",)),
        (_realization("r2"), ("shared",)),
        (_realization("r3"), ("other",)),
    )
    reduced = reduce_bound_plan(plan, [_outcome("shared"), _outcome("other")])
    assert all(o.state is RealizationState.SATISFIED for o in reduced.outcomes)


def test_i5_consumer_a_node_with_no_outcome_never_reduces_to_satisfied():
    plan = _bound((_realization("r1"), ("missing",)))
    reduced = reduce_bound_plan(plan, [])
    assert reduced.outcomes[0].state is RealizationState.PLANNED
    assert reduced.unaccounted


# --- I6: a satisfied requirement contributes no failure line -------------------------------------


def test_i6_producer_satisfied_realizations_render_nothing():
    plan = _bound((_realization("r1"), ("n1",)))
    reduced = reduce_bound_plan(plan, [_outcome("n1")])
    assert reduced.failure_lines() == ()


def test_i6_consumer_only_the_unsatisfied_one_is_named():
    plan = _bound(
        (_realization("r1", display_subject="Gold"), ("n1",)),
        (
            _realization("r2", display_subject="Palladium"),
            NonExecutionEvidence(NonExecutionReason.UNRESOLVED_SUBJECT),
        ),
    )
    reduced = reduce_bound_plan(plan, [_outcome("n1")])
    lines = reduced.failure_lines()
    assert len(lines) == 1
    assert "Palladium" in lines[0] and "Gold" not in lines[0]


def test_i6_a_failure_line_names_the_role_owned_subject_not_the_amount():
    """`display_subject` comes from the family's DISPLAY roles, in the contract's own order."""
    ledger = resolve_ledger(capture_requirements("convert 500 EUR to JPY"))
    requirement = ledger.requirements[0]
    assert display_subject(requirement, requirement.slots) == "EUR to JPY"


def test_i6_a_place_frame_names_its_service_and_location():
    ledger = capture_requirements(
        "find a locksmith in Porto",
        propose=proposer(
            frame(
                "place_search",
                scope="find a locksmith in Porto",
                predicate="find",
                roles=[role("service", "locksmith"), role("location", "Porto")],
            )
        ),
    )
    requirement = ledger.requirements[0]
    assert display_subject(requirement, requirement.slots) == "locksmith in Porto"


# --- I7: a non-FULFILLED disposition can never persist as FULFILLED ------------------------------


def test_i7_producer_the_decision_refuses_to_hold_a_contradictory_outcome():
    with pytest.raises(ValueError, match="cannot persist as"):
        ProductDecision(
            outcome_set=reduce_bound_plan(BoundExecutionPlan(), []),
            disposition=ProductDisposition.PARTIALLY_FULFILLED,
            claim=ConductorClaim.CLAIMED_PARTIAL,
            runtime_task_outcome=RuntimeTaskOutcome(
                fulfillment_status=FulfillmentStatus.FULFILLED
            ),
        )


@pytest.mark.parametrize(
    "state,expected",
    [
        (RealizationState.SATISFIED, FulfillmentStatus.FULFILLED),
        (RealizationState.EXECUTION_FAILED, FulfillmentStatus.FAILED),
        (RealizationState.UNRESOLVED_SUBJECT, FulfillmentStatus.BLOCKED),
        (RealizationState.CAPABILITY_UNAVAILABLE, FulfillmentStatus.BLOCKED),
    ],
)
def test_i7_consumer_every_disposition_maps_to_one_exact_runtime_outcome(state, expected):
    evidence = {
        RealizationState.EXECUTION_FAILED: None,
        RealizationState.UNRESOLVED_SUBJECT: NonExecutionEvidence(
            NonExecutionReason.UNRESOLVED_SUBJECT
        ),
        RealizationState.CAPABILITY_UNAVAILABLE: NonExecutionEvidence(
            NonExecutionReason.CAPABILITY_UNAVAILABLE
        ),
    }.get(state)
    if evidence is not None:
        plan = _bound((_realization("r1"), evidence))
        outcomes = []
    else:
        plan = _bound((_realization("r1"), ("n1",)))
        outcomes = [
            _outcome(
                "n1",
                state=NodeLifecycle.SUCCEEDED
                if state is RealizationState.SATISFIED
                else NodeLifecycle.FAILED,
            )
        ]
    decision = reduce_execution_report(
        ExecutionReport(bound_plan=plan, node_outcomes=tuple(outcomes), planned_node_ids=())
    )
    assert decision.runtime_task_outcome.fulfillment_status is expected


def test_i7_an_unclaimed_decision_carries_no_runtime_outcome_at_all():
    decision = reduce_execution_report(ExecutionReport())
    assert decision.disposition is ProductDisposition.NOTHING_CAPTURED
    assert decision.claim is ConductorClaim.NOT_CLAIMED
    assert decision.runtime_task_outcome is None


# --- I8: after the claim, no path returns None or NOT_CLAIMED ------------------------------------


def test_i8_producer_the_gate_refuses_to_enter_not_claimed():
    gate = ConductorClaimGate()
    with pytest.raises(ClaimViolationError):
        gate.enter(reduce_execution_report(ExecutionReport()))
    assert gate.claim is ConductorClaim.NOT_CLAIMED


def test_i8_producer_a_claim_is_monotonic():
    gate = ConductorClaimGate()
    plan = _bound((_realization("r1"), ("n1",)))
    decision = reduce_execution_report(
        ExecutionReport(bound_plan=plan, node_outcomes=(_outcome("n1"),))
    )
    gate.enter(decision)
    assert gate.claim is ConductorClaim.CLAIMED_SUCCESS
    with pytest.raises(ClaimViolationError, match="monotonic"):
        gate.enter(decision)


def test_i8_consumer_a_post_claim_fault_becomes_a_claimed_integrity_failure():
    gate = ConductorClaimGate()
    plan = _bound((_realization("r1"), ("n1",)))
    gate.enter(
        reduce_execution_report(
            ExecutionReport(bound_plan=plan, node_outcomes=(_outcome("n1"),))
        )
    )
    failed = gate.integrity_failure(TypeError("keyword argument"))
    assert failed is not None
    assert failed.claim is ConductorClaim.CLAIMED_INTEGRITY_FAILURE
    assert failed.disposition is ProductDisposition.INTEGRITY_FAILURE
    assert failed.runtime_task_outcome.fulfillment_status is FulfillmentStatus.FAILED
    assert any("post_claim:TypeError" in code for code in failed.integrity_codes)
    assert gate.claim is ConductorClaim.CLAIMED_INTEGRITY_FAILURE


def test_i8_integrity_conversion_is_a_post_claim_operation_only():
    with pytest.raises(ClaimViolationError, match="POST-claim"):
        ConductorClaimGate().integrity_failure("anything")


# --- I9: a dependent cannot satisfy unless its prerequisites did ---------------------------------


def test_i9_producer_a_blocked_prerequisite_blocks_its_dependent():
    plan = _bound(
        (
            _realization("prereq"),
            NonExecutionEvidence(NonExecutionReason.UNRESOLVED_SUBJECT),
        ),
        (_realization("dep", prerequisite_realization_ids=("prereq",)), ("n1",)),
    )
    reduced = reduce_bound_plan(plan, [_outcome("n1", state=NodeLifecycle.FAILED)])
    assert reduced.state_of("dep") is RealizationState.DEPENDENCY_BLOCKED


def test_i9_consumer_a_dependent_that_answered_anyway_is_an_integrity_failure():
    """The fabrication shape: a figure produced from a closure that was not there."""
    plan = _bound(
        (
            _realization("prereq"),
            NonExecutionEvidence(NonExecutionReason.UNRESOLVED_SUBJECT),
        ),
        (_realization("dep", prerequisite_realization_ids=("prereq",)), ("n1",)),
    )
    reduced = reduce_bound_plan(plan, [_outcome("n1")])
    assert reduced.state_of("dep") is RealizationState.INTEGRITY_FAILURE
    decision = reduce_execution_report(
        ExecutionReport(bound_plan=plan, node_outcomes=(_outcome("n1"),))
    )
    assert decision.disposition is ProductDisposition.INTEGRITY_FAILURE
    assert decision.claim is ConductorClaim.CLAIMED_INTEGRITY_FAILURE


def test_i9_negative_control_a_satisfied_prerequisite_does_not_block():
    plan = _bound(
        (_realization("prereq"), ("n0",)),
        (_realization("dep", prerequisite_realization_ids=("prereq",)), ("n1",)),
    )
    reduced = reduce_bound_plan(plan, [_outcome("n0"), _outcome("n1")])
    assert reduced.state_of("dep") is RealizationState.SATISFIED


# --- I10: unproven spans reach zero provider arguments -------------------------------------------


@pytest.mark.parametrize(
    "unclaimed",
    [
        "my grandmother's recipe",
        "the release notes",
        "customer data",
        "/etc/passwd",
        "https://example.com/report",
        "the Q3 report",
        "the blue folder",
    ],
)
def test_i10_producer_unclaimed_text_produces_no_realization_and_no_arguments(unclaimed):
    ledger = resolve_ledger(
        capture_requirements(
            f"Get the weather in Oslo and {unclaimed}",
            propose=proposer(_weather("Oslo", scope="the weather in Oslo")),
        )
    )
    realizations = resolve_capabilities(plan_realizations(ledger))
    blob = json.dumps([dict(r.arguments) for r in realizations.realizations])
    assert unclaimed not in blob
    assert [r.display_subject for r in realizations.realizations] == ["Oslo"]


def test_i10_consumer_an_abstained_turn_realizes_nothing():
    from tests.semantic_proposer import raw_proposer

    ledger = resolve_ledger(
        capture_requirements("Get the weather in Oslo", propose=raw_proposer("not json"))
    )
    assert plan_realizations(ledger).realizations == ()


def test_i10_a_defect_lookup_never_reaches_available():
    """A broken projector is a defect, and a defect can never buy a provider call."""
    from core.conductor.realization import WorkSpec, lookup_capability

    result = lookup_capability(
        WorkSpec(family="market_quote", role_surfaces={"asset_subject": ("Gold",)})
    )
    assert result.state is CapabilityLookupState.DEFECT
    assert result.arguments == {}
    assert result.non_execution.reason is NonExecutionReason.INTEGRITY_DEFECT


# --- the two set assertions the arbitration names explicitly ------------------------------------


@pytest.mark.usefixtures("fetchers")
def test_realization_ids_are_distinct_and_cover_every_required_requirement():
    plan, _outcomes, _composed = _drive(
        "Get the price of Gold and Silver and work out the ratio between them.",
        _clause("the price of Gold and Silver", "market_quote"),
        _clause("work out the ratio between them", "quantitative_reasoning", (0,)),
        generation=_Generation(("ratio", "gold_price / silver_price", "x")),
        propose_semantics=proposer(
            _market("Gold", "Silver", scope="the price of Gold and Silver"),
            frame(
                "quantitative_reasoning",
                scope="work out the ratio between them",
                predicate="work out",
            ),
        ),
    )
    ids = list(plan.bound_plan.realization_ledger.ids)
    assert len(ids) == len(set(ids))
    required = {r.requirement_id for r in plan.ledger.executable}
    realized = {r.requirement_id for r in plan.bound_plan.realization_ledger.realizations}
    assert required <= realized


@pytest.mark.usefixtures("fetchers")
def test_a_successful_quantitative_node_never_renders_unavailable():
    """The measured F4 defect: a derived realization with no node, no outcome and a false line."""
    plan, outcomes, composed = _drive(
        "Get the price of Gold and Silver and work out the ratio between them.",
        _clause("the price of Gold and Silver", "market_quote"),
        _clause("work out the ratio between them", "quantitative_reasoning", (0,)),
        generation=_Generation(("ratio", "gold_price / silver_price", "x")),
        propose_semantics=proposer(
            _market("Gold", "Silver", scope="the price of Gold and Silver"),
            frame(
                "quantitative_reasoning",
                scope="work out the ratio between them",
                predicate="work out",
            ),
        ),
    )
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert derived.succeeded, derived.failure_reason
    decision = decide(plan, outcomes)
    ratio = next(
        r
        for r in plan.bound_plan.realization_ledger.realizations
        if r.family == "quantitative_reasoning"
    )
    binding = plan.bound_plan.binding_of(ratio.realization_id)
    assert binding.bound_node_ids, "the ratio realization bound no node"
    assert decision.outcome_set.state_of(ratio.realization_id) is RealizationState.SATISFIED
    assert "is not something this runtime can look up" not in composed.text


# --- binding identity: one piece of work is one node ---------------------------------------------

_BTC_AND_SUM = "What is 137 x 29? Also get the price of BTC."
_BTC_CLAUSES = (
    {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
    {"request": "the price of BTC", "operation": "market_quote", "depends_on": []},
)


def _btc_plan():
    """A turn where the planner's node and the ledger's realization describe the SAME work.

    Both halves are deliberately spelled differently from each other -- the planner stores the whole
    clause "What is 137 x 29?" and calls the asset "Bitcoin"; the ledger captured "137 x 29" and
    "BTC". They are one piece of work only because the OPERATION says so, which is the entire reason
    `execution_key` belongs to the operation rather than to a text comparison.
    """
    return plan_conductor_turn(
        _BTC_AND_SUM,
        ask_model=lambda _s, _p: json.dumps(list(_BTC_CLAUSES)),
        plan_id="x",
        propose_semantics=proposer(_market("BTC", scope="the price of BTC")),
    )


@pytest.mark.usefixtures("fetchers")
def test_a_realization_binds_the_planners_node_instead_of_building_a_second():
    plan = _btc_plan()
    assert plan is not None
    per_operation: dict[str, int] = {}
    for node in plan.nodes:
        per_operation[node.operation] = per_operation.get(node.operation, 0) + 1
    assert per_operation.get("calculation") == 1, "the same sum was scheduled twice"
    assert per_operation.get("market_quote") == 1, "the same asset was priced twice"
    for binding in plan.bound_plan.bindings:
        assert binding.bound_node_ids, "a serviceable realization bound nothing"


@pytest.mark.usefixtures("fetchers")
def test_the_operation_owns_the_identity_of_its_own_work():
    """Spelled differently on both sides, and still one key. Surface comparison cannot do this."""
    plan = _btc_plan()
    calculation = next(n for n in plan.nodes if n.operation == "calculation")
    realization = next(
        r for r in plan.bound_plan.realization_ledger.realizations if r.family == "calculation"
    )
    assert calculation.arguments["expression_text"] != realization.display_subject
    assert execution_key(operation_spec("calculation"), calculation.arguments) == (
        realization.execution_key
    )


# --- polarity is stated, never supplied ----------------------------------------------------------


@pytest.mark.parametrize("stated", ["", "yes", "probably", "AFFIRMED?", None, 1])
def test_a_polarity_the_proposer_did_not_state_yields_no_frame(stated):
    """S3's owner boundary. An omission must not become an assertion."""
    from tests.semantic_proposer import raw_proposer

    payload = json.dumps(
        {
            "frames": [
                {
                    "frame_id": "f1",
                    "family": "weather_lookup",
                    "scope": "the weather in Oslo",
                    "predicate": "weather",
                    "polarity": stated,
                    "roles": [
                        {"role": "location", "text": "Oslo", "group": "g", "ordinal": 0}
                    ],
                }
            ]
        }
    )
    ledger = capture_requirements(
        "Give me the weather in Oslo", propose=raw_proposer(payload)
    )
    assert len(ledger) == 0, f"polarity {stated!r} was treated as an assertion"


def test_a_frame_with_no_polarity_key_at_all_yields_no_frame():
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
    assert len(capture_requirements("the weather in Oslo", propose=raw_proposer(payload))) == 0


def test_a_surface_the_message_does_not_contain_yields_no_frame():
    """S1/S2's owner boundary. A proposer's invention cannot be located, so it owns nothing."""
    from tests.semantic_proposer import raw_proposer

    payload = json.dumps(
        {
            "frames": [
                {
                    "frame_id": "f1",
                    "family": "weather_lookup",
                    "scope": "the weather in Oslo",
                    "predicate": "weather",
                    "polarity": "affirmed",
                    "roles": [
                        {"role": "location", "text": "Oslo", "group": "g", "ordinal": 0},
                        {"role": "location", "text": "Bergen", "group": "g", "ordinal": 1},
                    ],
                }
            ]
        }
    )
    ledger = capture_requirements(
        "Give me the weather in Oslo", propose=raw_proposer(payload)
    )
    assert len(ledger) == 0, "a location the user never wrote was owned"


# --- the dispatch contract, at the seam that owns it ---------------------------------------------


class _DispatchHarness:
    """The narrowest thing that can drive `_maybe_answer_conductor_turn` end to end."""

    def __init__(self) -> None:
        from apps.vool_agent import VoolAgent

        self.agent = object.__new__(VoolAgent)
        self.agent._emit_runtime_event = lambda *_a, **_k: None
        self.agent._agent_node_emitter = lambda *_a, **_k: (lambda *_x, **_y: None)
        self.agent._execute_tool_intent = lambda *_a, **_k: {}
        self.agent.hive_activity_tracker = None
        self.agent._fast_path_result = lambda **kw: {
            "response": kw["response"],
            "confidence": kw["confidence"],
            "reason": kw["reason"],
            "source_context": kw.get("source_context") or {},
        }

    def run(self, prompt: str, *clauses: dict, propose_semantics=None, generation=None):
        from unittest import mock

        from core.agent_runtime import turn_planner_hook

        with mock.patch.object(
            turn_planner_hook,
            "build_planner_ask_model",
            lambda *_a, **_k: (lambda _s, _p: json.dumps(list(clauses))),
        ), mock.patch.object(
            turn_planner_hook, "build_conductor_ask_model", lambda *_a, **_k: generation
        ), mock.patch.object(
            turn_planner_hook, "build_pinned_paid_turn_scope", lambda *_a, **_k: None
        ), mock.patch(
            "core.conductor.planner.capture_requirements",
            lambda text, **_k: _real_capture(text, propose_semantics),
        ):
            return self.agent._maybe_answer_conductor_turn(
                effective_input=prompt,
                raw_input=prompt,
                session_id="s",
                source_context={},
            )


def _real_capture(text, propose_semantics):
    from core.conductor.requirements import capture_requirements as real

    return real(text, propose=propose_semantics)


@pytest.mark.usefixtures("fetchers")
def test_a_claimed_turn_always_carries_its_decision_to_the_dispatch_result():
    """S12's owner boundary. The result the seam returns IS where persistence reads truth."""
    result = _DispatchHarness().run(
        _BTC_AND_SUM,
        *_BTC_CLAUSES,
        propose_semantics=proposer(_market("BTC", scope="the price of BTC")),
    )
    assert result is not None
    decision = result["conductor_product_decision"]
    assert decision["claim"] in {c.value for c in ConductorClaim} - {"not_claimed"}
    assert decision["runtime_task_outcome"]["fulfillment_status"]
    assert decision["disposition"]


@pytest.mark.usefixtures("fetchers")
def test_no_post_claim_path_returns_none_however_the_composer_breaks():
    """S14's owner boundary, over several fault shapes rather than one."""
    from unittest import mock

    for fault in (TypeError("kwarg"), ValueError("bad"), RuntimeError("boom"), KeyError("k")):
        with mock.patch("core.conductor.compose_answer", side_effect=fault):
            result = _DispatchHarness().run(
                _BTC_AND_SUM,
                *_BTC_CLAUSES,
                propose_semantics=proposer(_market("BTC", scope="the price of BTC")),
            )
        assert result is not None, f"{fault!r} produced a fall-through"
        assert (
            result["conductor_product_decision"]["claim"]
            == ConductorClaim.CLAIMED_INTEGRITY_FAILURE.value
        )


# --- F4: one piece of arithmetic is one execution key ---------------------------------------------


@pytest.mark.parametrize(
    "written",
    ["137 x 29", "137×29", "137 * 29", "137*29", "137  x  29", "What is 137 x 29?"],
)
def test_equivalent_arithmetic_shares_one_execution_key(written):
    """Notation is not work. Four spellings were four keys, so one sum ran up to four times."""
    spec = operation_spec("calculation")
    canonical = execution_key(spec, {"expression_text": "137 x 29"})
    assert execution_key(spec, {"expression_text": written}) == canonical


@pytest.mark.parametrize("written", ["29 x 137", "137 + 29", "137 / 29", "138 x 29", "137 x 30"])
def test_meaningfully_different_arithmetic_keeps_a_different_key(written):
    """The other half. Operand order and operator survive, because the identity is the PARSE TREE."""
    spec = operation_spec("calculation")
    assert execution_key(spec, {"expression_text": written}) != execution_key(
        spec, {"expression_text": "137 x 29"}
    )


@pytest.mark.usefixtures("fetchers")
def test_one_sum_written_two_ways_schedules_one_node():
    """The product consequence, through the planner rather than through the key function."""
    plan = plan_conductor_turn(
        "What is 137 x 29? Also 137*29 please, and get the price of BTC.",
        ask_model=lambda _s, _p: json.dumps(
            [
                {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
                {"request": "the price of BTC", "operation": "market_quote", "depends_on": []},
            ]
        ),
        plan_id="k",
        propose_semantics=proposer(_market("BTC", scope="the price of BTC")),
    )
    assert plan is not None
    sums = [n for n in plan.nodes if n.operation == "calculation"]
    assert len(sums) == 1, f"the same sum was scheduled {len(sums)} times"


# --- F4: the capability lookup is exhaustive ------------------------------------------------------


def test_a_registry_that_raises_is_a_typed_defect_not_a_missing_capability():
    """It used to escape, unwind past realization, and be swallowed as a pre-claim decline."""
    from unittest import mock

    from core.conductor.realization import WorkSpec, lookup_capability

    with mock.patch(
        "core.conductor.realization.operation_spec", side_effect=RuntimeError("registry is broken")
    ):
        result = lookup_capability(
            WorkSpec(family="market_quote", role_surfaces={"asset_subject": ("Gold",)})
        )
    assert result.state is CapabilityLookupState.DEFECT
    assert result.state is not CapabilityLookupState.UNAVAILABLE
    assert result.non_execution.reason is NonExecutionReason.INTEGRITY_DEFECT
    assert result.non_execution.state is RealizationState.INTEGRITY_FAILURE


def test_a_defect_lookup_reduces_to_an_integrity_failure_and_refuses_to_ship():
    realization = _realization("r1")
    plan = _bound(
        (realization, NonExecutionEvidence(NonExecutionReason.INTEGRITY_DEFECT, "registry raised"))
    )
    decision = reduce_execution_report(ExecutionReport(bound_plan=plan))
    assert decision.disposition is ProductDisposition.INTEGRITY_FAILURE
    assert decision.claim is ConductorClaim.CLAIMED_INTEGRITY_FAILURE


# --- F5: NOTHING_CAPTURED is representable, and its decline branch is reachable --------------------


def test_an_unclaimed_decision_serializes_without_raising():
    """It used to dereference a None outcome, so the clean decline was reached by AttributeError."""
    decision = reduce_execution_report(ExecutionReport())
    payload = decision.to_dict()
    assert payload["claim"] == ConductorClaim.NOT_CLAIMED.value
    assert payload["disposition"] == ProductDisposition.NOTHING_CAPTURED.value
    assert payload["runtime_task_outcome"] is None
    assert json.dumps(payload)


@pytest.mark.usefixtures("fetchers")
def test_the_pre_claim_decline_branch_is_genuinely_reachable():
    """No plan, no nodes, nothing captured -- and the seam declines by DECIDING, not by raising."""
    harness = _DispatchHarness()
    decision = reduce_execution_report(ExecutionReport())
    assert not decision.claimed
    # And the dispatch seam returns None for it without an exception on the way.
    result = harness.run(
        "What is 137 x 29?",
        {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
    )
    assert result is None, "a single-clause turn is not a conductor turn"


# --- F5: the persistence wire is validated, never re-derived ---------------------------------------


def _wire(disposition: str, status: str) -> dict:
    return {
        "disposition": disposition,
        "claim": "claimed_partial",
        "runtime_task_outcome": {
            "fulfillment_status": status,
            "failure_stage": "conductor_product_decision",
            "failure_codes": [],
            "retryable": True,
        },
        "failure_lines": [],
        "integrity_codes": [],
        "outcome_set": {"outcomes": []},
    }


@pytest.mark.parametrize(
    "disposition,status",
    [
        ("partially_fulfilled", "fulfilled"),
        ("failed", "fulfilled"),
        ("blocked", "fulfilled"),
        ("integrity_failure", "fulfilled"),
        ("failed", "partially_fulfilled"),
    ],
)
def test_a_wire_decision_that_contradicts_itself_persists_as_an_integrity_failure(
    disposition, status
):
    """The constructor cannot guard a dict. VALIDATION, not reclassification: neither half wins.

    A serialized PARTIALLY_FULFILLED decision carrying a FULFILLED outcome claims the turn both did
    and did not serve what was asked. Picking whichever half reads better is what the five rival
    authorities used to do.
    """
    from core.runtime_task_outcome import WIRE_INCONSISTENCY_CODE

    outcome = terminal_fulfillment_outcome(
        {"response": "a confident reply", "conductor_product_decision": _wire(disposition, status)}
    )
    assert outcome.fulfillment_status is FulfillmentStatus.FAILED
    assert WIRE_INCONSISTENCY_CODE in outcome.failure_codes
    assert not outcome.retryable


@pytest.mark.parametrize(
    "disposition,status",
    [
        ("fulfilled", "fulfilled"),
        ("partially_fulfilled", "partially_fulfilled"),
        ("failed", "failed"),
        ("blocked", "blocked"),
        ("integrity_failure", "failed"),
    ],
)
def test_an_agreeing_wire_decision_persists_exactly_as_declared(disposition, status):
    """NEGATIVE CONTROL. The check is a consistency test, not a blanket downgrade."""
    outcome = terminal_fulfillment_outcome(
        {"response": "a reply", "conductor_product_decision": _wire(disposition, status)}
    )
    assert outcome.fulfillment_status is FulfillmentStatus(status)
