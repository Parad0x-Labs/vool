"""The obligation floor and the dependency contract, over inputs that never designed them.

Every case here was written to survive deleting the reported reproduction. None of them is about
Bitcoin, gold, ounces or a ratio; the subjects, domains, obligation counts and phrasings are fresh,
and two of the families use a synthetic domain this runtime has never had, so a repair that learned
the reproduction's vocabulary cannot pass them.

What each family probes:

* THREE, FOUR and FIVE-PLUS obligations -- the floor must not be an assertion about two things.
* INDEPENDENT siblings -- one dropped subject must not cancel the siblings that could be served.
* DEPENDENT siblings -- a node whose figures exist only in a live sibling's result.
* LIVE + DETERMINISTIC mixed -- a fetched value and a stated one in the same computation.
* NON-LIVE multipart -- no retrieval anywhere, so the floor cannot be a property of the web path.
* REORDERED and SLOPPY phrasing -- the same obligations written the way a real user types them.
* NEGATIVE CONTROLS -- decompositions that legitimately differ while covering identically. The
  floor must stay silent on these; a rule that fires where there is no defect gets switched off.

Nothing injects an answer. `_Generation` writes only the SHAPE of a computation, naming symbols it
expects the runtime to have bound, and states no number of its own -- so every asserted figure is
the runtime's arithmetic over a fetcher result, and a step evaluates only if the plumbing carried
the value.
"""
from __future__ import annotations

import json

import pytest

from tests.test_canonical_obligation_floor import (  # noqa: F401  (fetchers is a fixture)
    _CRYPTO,
    _TEMPS,
    _accounted_for,
    _clause,
    _drive,
    _Generation,
    fetchers,
)

# --- a synthetic domain, so the mechanism is provably the contract and not market knowledge -------


class _Warehouse:
    """An operation that NAMES more parts than it can SERVE, registered per test.

    Deliberately unlike anything in this runtime: no aliases, no authority check, no plausibility
    filter, no network. It exists to prove the floor reads the registry declaration rather than
    recognizing a domain.
    """

    STOCK = {"flange": 4.0, "grommet": 9.0, "spindle": 6.0, "bushing": 2.0}
    CATALOGUE = ("flange", "grommet", "spindle", "bushing", "trunnion", "gudgeon")

    @classmethod
    def names(cls, clause: str) -> list[str]:
        lowered = clause.casefold()
        return [part for part in cls.CATALOGUE if part in lowered]

    @classmethod
    def expand(cls, clause: str) -> list[dict]:
        return [{"entity": part} for part in cls.names(clause) if part in cls.STOCK]

    @classmethod
    def realize(cls, subject: str) -> dict | None:
        """The capability probe: a stocked part is servable, a catalogued-but-unstocked one is not.

        Both branches matter. `spindle` proves an obligation the planner or the recognizer dropped
        is EXECUTED rather than reported; `trunnion` proves one nothing can serve comes to rest in
        CAPABILITY_UNAVAILABLE without anything being attempted or invented.
        """
        key = " ".join(str(subject or "").casefold().split())
        return {"entity": key} if key in cls.STOCK else None


def _register_warehouse():
    from core.conductor.capabilities import OperationCapability, OperationEffect
    from core.conductor.registry import OperationSpec, register_operation
    from core.turn_ir import ClauseKind

    register_operation(
        OperationSpec(
            name="parts_lookup",
            description="stock level for one or more named parts",
            expand_arguments=_Warehouse.expand,
            run=lambda node, _ctx: {"stock": _Warehouse.STOCK[str(node.arguments["entity"])]},
            render=lambda node, result: f"{node.arguments['entity']}: {result['stock']} in stock",
            required_result_fields=("stock",),
            exported_value_fields=("stock",),
            subjects_named=_Warehouse.names,
            realize_subject=_Warehouse.realize,
            capability=OperationCapability(
                effect=OperationEffect.WORKSPACE_EVIDENCE,
                domain="parts",
                accepted_kinds=frozenset(
                    {ClauseKind.KNOW, ClauseKind.OBSERVE, ClauseKind.UNKNOWN}
                ),
            ),
        ),
        replace=True,
    )


@pytest.fixture()
def warehouse():
    from core.conductor.registry import unregister_operation

    _register_warehouse()
    try:
        yield _Warehouse
    finally:
        unregister_operation("parts_lookup")



def _omissions(plan):
    """Obligations the plan did NOT plan for itself.

    Every subject the request names is an obligation now, including the ones the planner got right
    -- that is what lets a planned-but-failed subject have a state at all. A control asserting "the
    floor invented nothing" is therefore about the OMISSIONS, not about the obligation set being
    empty.
    """
    from core.conductor.obligations import ObligationOrigin

    return [
        o for o in plan.obligations.obligations
        if o.origin is not ObligationOrigin.PLANNED_BY_MODEL
    ]


# --- obligation counts ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fetchers")
def test_three_obligations_two_independent_one_dependent():
    """Three: two live temperatures and a figure derived from both."""
    generation = _Generation(("difference", "porto_temperature_c - reykjavik_temperature_c", "C"))
    _plan, outcomes, composed = _drive(
        "Check the temperature in Porto and Reykjavik, and calculate the difference between them.",
        _clause("the temperature in Porto and Reykjavik", "weather_lookup"),
        _clause("calculate the difference between them", "quantitative_reasoning", (0,)),
        generation=generation,
    )
    assert composed.complete and not composed.absent_obligations
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert derived.result["steps"][0]["value"] == pytest.approx(
        _TEMPS["porto"] - _TEMPS["reykjavik"]
    )


@pytest.mark.usefixtures("fetchers")
def test_four_obligations_with_one_subject_the_operation_cannot_serve(warehouse):
    """Four parts named, three stocked. The fourth is reported, and the other three still run."""
    _plan, outcomes, composed = _drive(
        "Look up the stock of the flange, the grommet, the spindle and the trunnion, then add up "
        "the total units on hand.",
        _clause(
            "the stock of the flange, the grommet, the spindle and the trunnion", "parts_lookup"
        ),
        _clause("add up the total units on hand", "quantitative_reasoning", (0,)),
        generation=_Generation(
            ("total", "flange_stock + grommet_stock + spindle_stock", "units")
        ),
    )
    assert _accounted_for("trunnion", outcomes, composed)
    served = {
        str(o.node.arguments.get("entity"))
        for o in outcomes
        if o.node.operation == "parts_lookup" and o.succeeded
    }
    assert served == {"flange", "grommet", "spindle"}, (
        "an unservable subject cancelled siblings that could be served"
    )
    # The total was asked over FOUR parts and one of them cannot be looked up. A figure summing the
    # three that resolved is not that total, and presenting it as one is the fabrication this
    # invariant exists to prevent -- so the dependent must not answer at all.
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert not derived.succeeded, (
        f"the dependent produced {derived.result!r} while a required input was unavailable"
    )


@pytest.mark.usefixtures("fetchers")
def test_five_plus_obligations_are_not_terminated_after_the_first_specialised_part(warehouse):
    """S9 as a property: a long request is not finished by satisfying one part of it.

    Six named subjects across two clauses plus a derived figure. Two of the six cannot be served,
    and both must be reported while the other four are answered.
    """
    _plan, outcomes, composed = _drive(
        "Get the stock of the flange, the grommet, the spindle, the bushing, the trunnion and the "
        "gudgeon, and work out how many units those come to in total.",
        _clause(
            "the stock of the flange, the grommet, the spindle, the bushing, the trunnion and "
            "the gudgeon",
            "parts_lookup",
        ),
        _clause("work out how many units those come to in total", "quantitative_reasoning", (0,)),
        generation=_Generation(
            (
                "total",
                "flange_stock + grommet_stock + spindle_stock + bushing_stock",
                "units",
            )
        ),
    )
    for missing in ("trunnion", "gudgeon"):
        assert _accounted_for(missing, outcomes, composed), f"{missing} vanished from the turn"
    served = sum(1 for o in outcomes if o.node.operation == "parts_lookup" and o.succeeded)
    assert served == 4, "the four stocked parts did not all run"
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert not derived.succeeded, (
        "a total over six parts was answered while two of them could not be looked up"
    )


# --- dependency propagation over unseen shapes ---------------------------------------------------


@pytest.mark.usefixtures("fetchers")
def test_a_derived_node_runs_when_the_message_itself_states_no_number():
    """The plan-time admission gate, driven by a message with no figures at all.

    This is the case a projection-only repair passes vacuously. `extract_shared_context` finds no
    numeric facts here, so before the admission gate was decided against dependency exports the
    computing clause was UNRESOLVED at plan time -- refused before any lookup ran, with nothing
    downstream able to revive it.
    """
    from core.conductor.shared_context import extract_shared_context

    prompt = (
        "Fetch the live prices of Ethereum and Solana, then work out how many Solana one "
        "Ethereum buys."
    )
    assert not extract_shared_context(prompt).has_numbers, (
        "this case is only meaningful while the message states no numbers of its own"
    )
    _plan, outcomes, _composed = _drive(
        prompt,
        _clause("the live prices of Ethereum and Solana", "market_quote"),
        _clause("work out how many Solana one Ethereum buys", "quantitative_reasoning", (0,)),
        generation=_Generation(("solana per ethereum", "ethereum_price / solana_price", "")),
    )
    derived = [o for o in outcomes if o.node.operation == "quantitative_reasoning"]
    assert derived, "the computing clause was never planned as a node"
    assert derived[0].succeeded, derived[0].failure_reason
    assert derived[0].result["steps"][0]["value"] == pytest.approx(
        _CRYPTO["ethereum"][0] / _CRYPTO["solana"][0]
    )


@pytest.mark.usefixtures("fetchers")
def test_a_live_value_and_a_stated_value_combine_in_one_computation():
    """Mixed provenance: one operand fetched, one written by the user."""
    _plan, outcomes, _composed = _drive(
        "Look up the current price of Solana, then work out what 12 of them would cost.",
        _clause("the current price of Solana", "market_quote"),
        _clause("work out what 12 of them would cost", "quantitative_reasoning", (0,)),
        generation=_Generation(("cost of twelve", "solana_price * 12", "USD")),
    )
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert derived.succeeded, derived.failure_reason
    assert derived.result["steps"][0]["value"] == pytest.approx(_CRYPTO["solana"][0] * 12)


@pytest.mark.usefixtures("fetchers")
def test_two_siblings_of_one_operation_do_not_collide_in_the_dependent(warehouse):
    """Entity-scoped naming: each sibling's export is distinct, so neither overwrites the other."""
    _plan, outcomes, _composed = _drive(
        "Check the stock of the flange and the bushing, and work out how many more flanges there "
        "are than bushings.",
        _clause("the stock of the flange and the bushing", "parts_lookup"),
        _clause(
            "work out how many more flanges there are than bushings",
            "quantitative_reasoning",
            (0,),
        ),
        generation=_Generation(("surplus", "flange_stock - bushing_stock", "units")),
    )
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert derived.succeeded, derived.failure_reason
    assert derived.result["steps"][0]["value"] == pytest.approx(4.0 - 2.0)


@pytest.mark.usefixtures("fetchers")
def test_a_dependency_that_did_not_succeed_contributes_nothing(warehouse):
    """S6 as a property. A partial result is not an input.

    `NodeOutcome.result` is populated BEFORE the required-field check runs, so a FAILED node can
    carry a partial mapping. Feeding that to a dependent as though it were an input is how a
    missing field becomes a confident number.
    """
    from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
    from core.conductor.scheduler import _derived_facts

    producer = ConductorNode(
        node_id="p",
        operation="parts_lookup",
        request_text="stock of the flange",
        arguments={"entity": "flange"},
        required_result_fields=("stock", "location"),
    )
    consumer = ConductorNode(
        node_id="c", operation="quantitative_reasoning", request_text="total", depends_on=("p",)
    )
    partial = NodeOutcome(
        node=producer,
        state=NodeLifecycle.FAILED,
        result={"stock": 4.0},
        failure_reason="result missing required fields: location",
    )
    assert not partial.succeeded
    assert _derived_facts(consumer, {"p": partial}) == {}, (
        "a dependency that failed its own contract handed its partial result downstream"
    )

    whole = NodeOutcome(
        node=producer, state=NodeLifecycle.SUCCEEDED, result={"stock": 4.0, "location": "A1"}
    )
    assert _derived_facts(consumer, {"p": whole}) == {"flange_stock": 4.0}


# --- phrasing variation --------------------------------------------------------------------------


@pytest.mark.usefixtures("fetchers")
@pytest.mark.parametrize(
    "prompt, lookup",
    [
        # reordered: the computation stated before the lookup it needs
        (
            "Work out how many Solana one Ethereum buys — first fetch the live prices of "
            "Ethereum and Solana.",
            "fetch the live prices of Ethereum and Solana",
        ),
        # sloppy: no capitals, missing apostrophe, trailing filler
        (
            "get me the live prices of ethereum and solana then work out how many solana one "
            "ethereum buys thx",
            "the live prices of ethereum and solana",
        ),
        # typo in a load-bearing word, and a comma splice instead of a stop
        (
            "fetch teh live prices of Ethereum and Solana, then work out how many Solana one "
            "Ethereum buys",
            "the live prices of Ethereum and Solana",
        ),
    ],
    ids=["reordered", "sloppy", "typo"],
)
def test_the_dependency_contract_survives_how_a_real_user_types_it(prompt, lookup):
    """Same two obligations, written three ways a real user writes them.

    The clause text handed to each operation differs in case, punctuation, spelling and order.
    None of that may change whether a successful sibling's value reaches the node that needs it.
    """
    _plan, outcomes, _composed = _drive(
        prompt,
        _clause(lookup, "market_quote"),
        _clause("work out how many Solana one Ethereum buys", "quantitative_reasoning", (0,)),
        generation=_Generation(("solana per ethereum", "ethereum_price / solana_price", "")),
    )
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert derived.succeeded, derived.failure_reason
    assert derived.result["steps"], (
        f"the value did not reach the dependent node: {derived.result.get('cannot_determine')!r}"
    )
    assert derived.result["steps"][0]["value"] == pytest.approx(
        _CRYPTO["ethereum"][0] / _CRYPTO["solana"][0]
    )


# --- negative controls ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fetchers")
def test_a_plan_that_covers_everything_reports_no_obligation(warehouse):
    """NEGATIVE CONTROL. Nothing dropped, nothing reported."""
    plan, _outcomes, composed = _drive(
        "Check the stock of the flange and the grommet, and add them up.",
        _clause("the stock of the flange and the grommet", "parts_lookup"),
        _clause("add them up", "quantitative_reasoning", (0,)),
        generation=_Generation(("total", "flange_stock + grommet_stock", "units")),
    )
    assert _omissions(plan) == [], (
        f"the floor invented omissions for a plan that dropped nothing: "
        f"{[(o.text, o.origin.value) for o in _omissions(plan)]}"
    )
    assert composed.complete and composed.reported_obligation_count == 0


@pytest.mark.usefixtures("fetchers")
def test_two_different_decompositions_of_one_request_cover_identically(warehouse):
    """NEGATIVE CONTROL. The floor constrains COVERAGE, never the shape of the plan.

    The same request, decomposed two legitimately different ways -- one clause per part, or one
    clause naming both. A floor that demanded a particular decomposition would fail one of these,
    and that would make it a planner rather than a contract.
    """
    request = "Check the stock of the flange and the grommet."
    together, _o1, composed_together = _drive(
        request,
        _clause("the stock of the flange", "parts_lookup"),
        _clause("the stock of the grommet", "parts_lookup"),
    )
    split, _o2, composed_split = _drive(
        request,
        _clause("the stock of the flange and the grommet", "parts_lookup"),
        _clause("the stock of the flange", "parts_lookup"),
    )
    assert _omissions(together) == []
    assert _omissions(split) == []
    assert composed_together.complete and composed_split.complete
    assert not composed_together.absent_obligations
    assert not composed_split.absent_obligations


@pytest.mark.usefixtures("fetchers")
def test_a_multipart_turn_with_no_retrieval_at_all_behaves_identically(warehouse):
    """NEGATIVE CONTROL. No network anywhere -- the floor is not a property of the web path.

    `parts_lookup` reads a dict; nothing in this turn can retrieve. Both halves of the repair have
    to hold here exactly as they do on a live turn: an unserved subject is reported, a served one
    exports its value, and a covered plan reports no obligation.
    """
    plan, outcomes, composed = _drive(
        "What is 812 divided by 4? Also check the stock of the flange.",
        _clause("What is 812 divided by 4?", "calculation"),
        _clause("check the stock of the flange", "parts_lookup"),
        generation=_Generation(),
    )
    assert _omissions(plan) == []
    assert composed.complete
    assert "203" in composed.text
    assert any(
        o.node.operation == "parts_lookup" and o.succeeded for o in outcomes
    ), "the non-live lookup did not run"


@pytest.mark.usefixtures("fetchers")
def test_an_operation_declaring_no_subjects_produces_no_residue():
    """NEGATIVE CONTROL. `subjects_named` is opt-in; an operation without it is untouched.

    This is the blast-radius property stated as a test: every operation that did not declare a
    subject enumerator plans exactly as it did before the floor existed.
    """
    from core.conductor.registry import known_operations, named_subjects

    undeclared = [spec for spec in known_operations() if spec.subjects_named is None]
    assert undeclared, "no undeclared operation left to control against"
    for spec in undeclared:
        assert named_subjects(spec, "the flange, the grommet and the trunnion") == ()


@pytest.mark.usefixtures("fetchers")
def test_the_obligation_set_cannot_differ_between_lanes(warehouse):
    """S7. AUTO, local and cloud may execute differently. They may not disagree about the ASK.

    The obligation set is built at plan time from the user's request and the operations' own
    declarations. Nothing about a session, a model, a provider or an execution context is an input
    to it, and this drives the same request through three materially different contexts to hold
    that: a bare context, one carrying a session and a source context, and one with no generation
    seam at all. Execution differs across them; the obligation set must not.
    """
    from core.conductor.planner import plan_conductor_turn
    from core.conductor.registry import NodeContext
    from core.conductor.scheduler import run_conductor_plan

    request = (
        "Check the stock of the flange, the grommet and the trunnion, and total up the units."
    )
    clauses = [
        _clause("the stock of the flange, the grommet and the trunnion", "parts_lookup"),
        _clause("total up the units", "quantitative_reasoning", (0,)),
    ]
    lanes = {
        "bare": NodeContext(timeout_s=5.0, run_generation=_Generation()),
        "session": NodeContext(
            session_id="s-1",
            source_context={"lane": "cloud", "model": "some-cloud-model"},
            timeout_s=9.0,
            run_generation=_Generation(("total", "flange_stock + grommet_stock", "units")),
        ),
        "no_generation_seam": NodeContext(timeout_s=5.0, run_generation=None),
    }
    sets = {}
    for name, context in lanes.items():
        plan = plan_conductor_turn(
            request, ask_model=lambda _s, _p: json.dumps(clauses), plan_id="lane"
        )
        assert plan is not None
        run_conductor_plan(plan, context=context)
        sets[name] = sorted(
            (item.source.value, item.text.casefold())
            for item in plan.obligations.obligations
        )
    distinct = {tuple(value) for value in sets.values()}
    assert len(distinct) == 1, f"lanes disagreed about what the user asked for: {sets}"
    assert ("subject_residue", "trunnion") in sets["bare"], (
        "the dropped subject is missing from the obligation set every lane shares"
    )


def test_a_subject_enumerator_that_raises_cannot_lose_the_plan():
    """ADVERSARIAL. A recognizer fault degrades to the behaviour registries had before it existed."""
    from core.conductor.capabilities import OperationCapability, OperationEffect
    from core.conductor.registry import OperationSpec, named_subjects
    from core.turn_ir import ClauseKind

    def _explode(_clause: str) -> list[str]:
        raise RuntimeError("enumerator exploded")

    spec = OperationSpec(
        name="_hostile_enumerator",
        description="test",
        expand_arguments=lambda _c: [],
        run=lambda _n, _c: {},
        render=lambda _n, _r: "",
        subjects_named=_explode,
        capability=OperationCapability(
            effect=OperationEffect.KNOWLEDGE_ANSWER,
            domain="test",
            accepted_kinds=frozenset({ClauseKind.UNKNOWN}),
        ),
    )
    assert named_subjects(spec, "anything at all") == ()
