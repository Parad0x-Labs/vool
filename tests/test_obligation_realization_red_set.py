"""The reproductions a review agent used to veto the detection-only floor.

The first repair made an omitted obligation VISIBLE. That is not enough and the veto was right: a
serviceable request the planner forgot must be EXECUTED, not reported. "The planner did not create
a node" is a fact about the planner, never an answer to the user.

Two structural defects were measured in the previous candidate, both by construction rather than by
luck:

* the floor read the PLANNER'S CLAUSE for subjects, so a planner that dropped "and Nairobi" from its
  own clause text produced a plan the floor agreed with completely -- the obligation was never
  created. Measured: a two-city request planned as one city yielded an EMPTY obligation set.
* the coverage gate compared obligation ids against the ids of nodes carrying them, and the planner
  created each obligation TOGETHER with such a node. The two sets were equal by construction. Over
  150 plans and 300 obligations: zero absent, 150/150 complete. A check that cannot fail is not a
  check.

Every case below drives the real planner, scheduler and composer. Fetchers are replaced; nothing
else is. `_Generation` writes only the SHAPE of a computation and never a value, so a figure exists
only when the runtime carried the operands.
"""
from __future__ import annotations

import pytest

from core.conductor.obligations import ObligationOrigin, ObligationState
from tests.semantic_proposer import coordinated, frame, proposer
from tests.test_canonical_obligation_floor import (  # noqa: F401  (fetchers is a fixture)
    _COMMODITY,
    _CRYPTO,
    _TEMPS,
    _clause,
    _drive,
    _Generation,
    fetchers,
)


def _state(plan, outcomes, composed, subject: str) -> ObligationState:
    """The typed terminal state the production coverage path assigns to `subject`."""
    del composed
    node_states = {}
    from core.conductor.obligations import node_obligation_state as _obligation_state

    for outcome in outcomes:
        node_states[outcome.node.node_id] = _obligation_state(outcome)
    coverage = plan.obligations.coverage(node_states)
    for item in coverage.outcomes:
        if item.obligation.subject.casefold() == subject.casefold():
            return item.state
    return ObligationState.ABSENT


def _origin(plan, subject: str) -> ObligationOrigin | None:
    for obligation in plan.obligations.obligations:
        if obligation.subject.casefold() == subject.casefold():
            return obligation.origin
    return None


def _node_for(outcomes, entity: str):
    for outcome in outcomes:
        if str(outcome.node.arguments.get("entity") or "").casefold() == entity.casefold():
            return outcome
    return None


# --- A1: the reported turn. The dropped asset must EXECUTE. --------------------------------------


A1 = (
    "Search the live web for the current trading price of 1 Bitcoin (BTC) and 1 ounce of Gold in "
    "USD. Once you have both live prices, calculate exactly how many ounces of Gold it would take "
    "to buy exactly one Bitcoin today."
)


@pytest.mark.usefixtures("fetchers")
def test_a1_the_dropped_asset_is_executed_and_the_ratio_is_computed():
    """Gold is serviceable. The recognizer dropped it; the runtime must run it anyway."""
    _plan, outcomes, composed = _drive(
        A1,
        _clause(
            "the current trading price of 1 Bitcoin (BTC) and 1 ounce of Gold in USD",
            "market_quote",
        ),
        _clause(
            "calculate exactly how many ounces of Gold it would take to buy exactly one Bitcoin "
            "today",
            "quantitative_reasoning",
            (0,),
        ),
        generation=_Generation(("gold per bitcoin", "bitcoin_price / gold_price", "oz")),
    )
    gold = _node_for(outcomes, "Gold")
    assert gold is not None and gold.succeeded, "the dropped asset was not executed"
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert derived.succeeded, derived.failure_reason
    assert derived.result["steps"][0]["value"] == pytest.approx(
        _CRYPTO_FREE_BTC / _COMMODITY["gold"][0]
    )
    assert composed.complete


#: The market fetchers in the shared fixture quote gold; bitcoin comes from the crypto stub.
_CRYPTO_FREE_BTC = 64000.0


@pytest.fixture(autouse=True)
def _bitcoin_quote(monkeypatch):
    """Bitcoin added to the shared crypto stand-in, so A1 has both operands."""
    import tests.test_canonical_obligation_floor as base

    monkeypatch.setitem(base._CRYPTO, "bitcoin", (_CRYPTO_FREE_BTC, 1.0))


# --- A2 / A3: a place the PLANNER dropped from its own clause ------------------------------------


@pytest.mark.usefixtures("fetchers")
def test_a2_a_place_the_planner_dropped_is_executed_not_assumed_away():
    """The planner's clause names one city; the user asked about two.

    This is the case the previous floor could not see at all: it enumerated subjects inside the
    clause, and the clause no longer mentioned Nairobi.
    """
    plan, outcomes, composed = _drive(
        "Get the current weather in Reykjavik and Nairobi, work out the temperature difference "
        "between them, and say what that difference means for travel.",
        _clause("the current weather in Reykjavik", "weather_lookup"),
        _clause("work out the temperature difference between them", "quantitative_reasoning", (0,)),
        generation=_Generation(
            ("difference", "reykjavik_temperature_c - nairobi_temperature_c", "C")
        ),
    )
    assert _origin(plan, "Nairobi") is ObligationOrigin.PLANNER_OMITTED, (
        f"Nairobi was not recorded as a planner omission: "
        f"{[(o.subject, o.origin.value) for o in plan.obligations.obligations]}"
    )
    nairobi = _node_for(outcomes, "Nairobi")
    assert nairobi is not None, "the planner-dropped city produced no node at all"
    assert _state(plan, outcomes, composed, "Nairobi") is not ObligationState.ABSENT


@pytest.mark.usefixtures("fetchers")
def test_a2_a_dropped_place_cannot_yield_a_confident_comparison(monkeypatch):
    """When the dropped place cannot be observed, the dependent must not answer anyway."""
    import tests.test_canonical_obligation_floor as base

    # Nairobi is realizable but the fetcher has no reading for it: a real attempt that fails.
    monkeypatch.delitem(base._TEMPS, "nairobi", raising=False)
    plan, outcomes, composed = _drive(
        "Get the current weather in Reykjavik and Nairobi, work out the temperature difference "
        "between them, and say what that difference means for travel.",
        _clause("the current weather in Reykjavik", "weather_lookup"),
        _clause("work out the temperature difference between them", "quantitative_reasoning", (0,)),
        generation=_Generation(
            ("difference", "reykjavik_temperature_c - nairobi_temperature_c", "C")
        ),
    )
    assert _state(plan, outcomes, composed, "Nairobi") is ObligationState.EXECUTION_FAILED
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert not derived.succeeded, (
        f"a difference was reported as {derived.result!r} while one of its two readings failed"
    )


@pytest.mark.usefixtures("fetchers")
def test_a3_three_places_with_two_dropped_by_the_planner():
    """Three named, one planned. Both omissions execute, and Hobart does not block the figure.

    SLOT-LEVEL PREREQUISITE NARROWING, driven through the proof boundary that now owns role
    ownership. The derived frame names Porto and Oslo, so those two slots are what it cannot answer
    without; a third city in the same lookup having no reading must not refuse an answer the user
    can have. That narrowing needs the weather frame's roles to be PROVEN, which for open-ended
    natural language means the bounded path -- so the proposer below stands in for the model the
    runtime injects in production.
    """
    proposal = proposer(
        frame(
            "weather_lookup",
            scope="the current weather in Porto, Hobart and Oslo",
            predicate="weather",
            roles=coordinated("location", "Porto", "Hobart", "Oslo"),
        ),
        frame(
            "quantitative_reasoning",
            scope="work out how much warmer Porto is than Oslo",
            predicate="work out",
        ),
    )
    plan, outcomes, _composed = _drive(
        "Give me the current weather in Porto, Hobart and Oslo, and work out how much warmer "
        "Porto is than Oslo.",
        _clause("the current weather in Porto", "weather_lookup"),
        _clause("work out how much warmer Porto is than Oslo", "quantitative_reasoning", (0,)),
        generation=_Generation(("warmer by", "porto_temperature_c - oslo_temperature_c", "C")),
        propose_semantics=proposal,
    )
    for place in ("Hobart", "Oslo"):
        assert _node_for(outcomes, place) is not None, f"{place} produced no node"
        assert _origin(plan, place) is ObligationOrigin.PLANNER_OMITTED
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    # Oslo was realized, so the figure the user asked for is available.
    assert derived.succeeded, derived.failure_reason
    assert derived.result["steps"][0]["value"] == pytest.approx(_TEMPS["porto"] - _TEMPS["oslo"])


# --- A5: an unsupported prerequisite -------------------------------------------------------------


@pytest.mark.usefixtures("fetchers")
def test_a5_an_unsupported_subject_cannot_produce_a_fabricated_dependent():
    """Platinum can be priced by nothing here. The ratio must not be answered anyway."""
    _plan, outcomes, _composed = _drive(
        "Get the live prices of Silver and Platinum in USD and work out how many ounces of Silver "
        "one ounce of Platinum buys.",
        _clause("the live prices of Silver and Platinum in USD", "market_quote"),
        _clause(
            "work out how many ounces of Silver one ounce of Platinum buys",
            "quantitative_reasoning",
            (0,),
        ),
        generation=_Generation(("silver per platinum", "platinum_price / silver_price", "oz")),
    )
    assert _node_for(outcomes, "Silver").succeeded
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert not derived.succeeded, (
        f"a ratio was produced as {derived.result!r} with no price for one of its two operands"
    )


def test_a5_boundary_an_entity_outside_every_vocabulary_cannot_become_an_obligation():
    """KNOWN LIMITATION, asserted so it cannot quietly change or be quietly claimed as fixed.

    An obligation is discovered by asking each registered operation what subjects of ITS domain a
    request names. `platinum` is in no such vocabulary -- the commodity targets are brent crude, WTI
    crude, gold and silver, and the coin index does not resolve it either -- so nothing in the
    runtime can tell that the word is an asset rather than an adjective. No obligation is created
    for it, and the review requirement that an unsupported prerequisite "must not disappear" is NOT
    met for entities of this kind.

    What IS guaranteed is the other half, and it is guaranteed without any asset knowledge: the
    dependent cannot answer around the missing operand. See the test above.

    The alternative was to add platinum to the tables. That fixes this sentence and nothing else --
    the next unnamed entity behaves identically -- and inferring assets from leftover tokens is the
    mechanism this repository records regressing three times.
    """
    from core.conductor.operations import _market_subjects_named

    named = _market_subjects_named(
        "Get the live prices of Silver and Platinum in USD"
    )
    assert "Platinum" not in named, (
        "platinum is now in the market vocabulary -- this boundary has moved and the limitation "
        "recorded in the capability matrix needs updating"
    )
    assert "Silver" in named


@pytest.mark.usefixtures("fetchers")
def test_a5_a_neutral_constant_cannot_stand_in_for_a_missing_operand():
    """The exact fabrication measured on the live app: `x / 1` presented as an answer.

    The model is handed a step that divides by a literal 1 -- grounded, since 1 is a neutral
    constant -- while the operand it should have used belongs to a subject nothing can serve. The
    step is arithmetically valid and semantically empty, and the runtime must refuse the node on
    the prerequisite rather than on the shape of the expression.
    """
    _plan, outcomes, _composed = _drive(
        "Get the live prices of Silver and Platinum in USD and work out how many ounces of Silver "
        "one ounce of Platinum buys.",
        _clause("the live prices of Silver and Platinum in USD", "market_quote"),
        _clause(
            "work out how many ounces of Silver one ounce of Platinum buys",
            "quantitative_reasoning",
            (0,),
        ),
        generation=_Generation(("silver per platinum", "silver_price / 1", "oz")),
    )
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert not derived.succeeded, (
        f"a neutral constant stood in for a missing required operand: {derived.result!r}"
    )


# --- A6: a real execution failure keeps behaving as it did ---------------------------------------


@pytest.mark.usefixtures("fetchers")
def test_a6_a_real_prerequisite_failure_still_blocks_its_dependent(monkeypatch):
    """The pre-existing DEPENDENCY_FAILED behaviour must survive the rewrite."""
    import tests.test_canonical_obligation_floor as base

    monkeypatch.delitem(base._CRYPTO, "solana", raising=False)
    plan, outcomes, composed = _drive(
        "Get the live prices of Ethereum and Solana in USD, then work out how many Solana one "
        "Ethereum buys.",
        _clause("the live prices of Ethereum and Solana in USD", "market_quote"),
        _clause("work out how many Solana one Ethereum buys", "quantitative_reasoning", (0,)),
        generation=_Generation(("solana per ethereum", "ethereum_price / solana_price", "")),
    )
    assert _state(plan, outcomes, composed, "Solana") is ObligationState.EXECUTION_FAILED
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert not derived.succeeded


# --- A11: the same shape with no reproduction vocabulary -----------------------------------------


@pytest.mark.usefixtures("fetchers")
def test_a11_the_same_repair_with_no_btc_or_gold_anywhere():
    """Ethereum and Silver. Nothing here shares a subject with the reported turn."""
    _plan, outcomes, composed = _drive(
        "Look up the live prices of Ethereum and Silver in USD and work out how many ounces of "
        "Silver one Ethereum buys.",
        _clause("the live prices of Ethereum in USD", "market_quote"),
        _clause(
            "work out how many ounces of Silver one Ethereum buys",
            "quantitative_reasoning",
            (0,),
        ),
        generation=_Generation(("silver per ethereum", "ethereum_price / silver_price", "oz")),
    )
    silver = _node_for(outcomes, "Silver")
    assert silver is not None and silver.succeeded, "the planner-dropped asset was not executed"
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert derived.succeeded, derived.failure_reason
    assert derived.result["steps"][0]["value"] == pytest.approx(
        _CRYPTO["ethereum"][0] / _COMMODITY["silver"][0]
    )
    assert composed.complete


# --- ordering and prerequisite count -------------------------------------------------------------


@pytest.mark.usefixtures("fetchers")
def test_three_prerequisites_feed_one_derived_result():
    """A derived figure over three live observations, one of them planner-omitted."""
    _plan, outcomes, _composed = _drive(
        "Get the current temperature in Porto, Oslo and Riga, and work out their combined total.",
        _clause("the current temperature in Porto and Oslo", "weather_lookup"),
        _clause("work out their combined total", "quantitative_reasoning", (0,)),
        generation=_Generation(
            (
                "combined",
                "porto_temperature_c + oslo_temperature_c + riga_temperature_c",
                "C",
            )
        ),
    )
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert derived.succeeded, derived.failure_reason
    assert derived.result["steps"][0]["value"] == pytest.approx(
        _TEMPS["porto"] + _TEMPS["oslo"] + _TEMPS["riga"]
    )


@pytest.mark.usefixtures("fetchers")
@pytest.mark.parametrize(
    "prompt",
    [
        # dependent stated FIRST
        "Work out how many ounces of Silver one Ethereum buys — look up the live prices of "
        "Ethereum and Silver in USD.",
        # dependent stated LAST
        "Look up the live prices of Ethereum and Silver in USD. Work out how many ounces of "
        "Silver one Ethereum buys.",
        # sloppy, lowercase, trailing filler
        "look up the live prices of ethereum and silver in usd then work out how many ounces of "
        "silver one ethereum buys thx",
        # typo in a load-bearing word
        "look up teh live prices of Ethereum and Silver in USD, then work out how many ounces of "
        "Silver one Ethereum buys",
    ],
    ids=["dependent-first", "dependent-last", "sloppy", "typo"],
)
def test_realization_survives_how_a_real_user_words_it(prompt):
    """The planner drops Silver in every one of these. The runtime must still execute it."""
    _plan, outcomes, _composed = _drive(
        prompt,
        _clause("the live prices of Ethereum in USD", "market_quote"),
        _clause(
            "work out how many ounces of Silver one Ethereum buys",
            "quantitative_reasoning",
            (0,),
        ),
        generation=_Generation(("silver per ethereum", "ethereum_price / silver_price", "oz")),
    )
    silver = _node_for(outcomes, "Silver")
    assert silver is not None and silver.succeeded, "Silver was not realized for this phrasing"
    derived = next(o for o in outcomes if o.node.operation == "quantitative_reasoning")
    assert derived.succeeded, derived.failure_reason


# --- negative controls ---------------------------------------------------------------------------


@pytest.mark.usefixtures("fetchers")
def test_a_fully_planned_turn_realizes_nothing_extra():
    """NEGATIVE CONTROL. Realization must not invent work on a plan that dropped nothing."""
    plan, outcomes, composed = _drive(
        "Get the live prices of Ethereum and Solana in USD, then work out how many Solana one "
        "Ethereum buys.",
        _clause("the live prices of Ethereum and Solana in USD", "market_quote"),
        _clause("work out how many Solana one Ethereum buys", "quantitative_reasoning", (0,)),
        generation=_Generation(("solana per ethereum", "ethereum_price / solana_price", "")),
    )
    omissions = [
        o for o in plan.obligations.obligations
        if o.origin is not ObligationOrigin.PLANNED_BY_MODEL
    ]
    assert omissions == [], (
        f"realization invented omissions on a plan that dropped nothing: "
        f"{[(o.subject, o.origin.value) for o in omissions]}"
    )
    market_nodes = [o for o in outcomes if o.node.operation == "market_quote"]
    assert len(market_nodes) == 2, "an extra market node was realized"
    assert composed.complete


@pytest.mark.usefixtures("fetchers")
def test_an_obligation_the_planner_omitted_and_the_runtime_served_is_complete():
    """Origin and state are orthogonal, and production keeps both.

    A request the planner forgot and the runtime then executed is PLANNER_OMITTED + SATISFIED. The
    answer is complete; the planner still needs work. Collapsing the two would force a choice
    between reporting a turn as broken and losing the diagnostic.
    """
    plan, outcomes, composed = _drive(
        "Look up the live prices of Ethereum and Silver in USD and work out how many ounces of "
        "Silver one Ethereum buys.",
        _clause("the live prices of Ethereum in USD", "market_quote"),
        _clause(
            "work out how many ounces of Silver one Ethereum buys",
            "quantitative_reasoning",
            (0,),
        ),
        generation=_Generation(("silver per ethereum", "ethereum_price / silver_price", "oz")),
    )
    assert _origin(plan, "Silver") is ObligationOrigin.PLANNER_OMITTED
    assert _state(plan, outcomes, composed, "Silver") is ObligationState.SATISFIED
    assert composed.complete, "a turn whose every obligation was satisfied reported incomplete"


# --- the typed states must be distinguishable, and production must use the distinction ----------


@pytest.mark.usefixtures("fetchers")
def test_capability_unavailable_and_execution_failed_are_not_the_same_state(monkeypatch):
    """Two obligations fail in the SAME turn for two different reasons, and keep different states.

    Asserted together rather than separately: a mutation that collapses one into the other passes
    any test that only ever sees one of them at a time. `trunnion` is catalogued and nothing can
    serve it -- nothing is attempted. `bushing` is servable and its lookup raises -- a real attempt
    that failed. A reader owes a different sentence for each, and completion logic that cannot tell
    them apart has only free text to reason over.
    """
    from core.conductor.capabilities import OperationCapability, OperationEffect
    from core.conductor.registry import OperationSpec, register_operation, unregister_operation
    from core.turn_ir import ClauseKind

    stock = {"flange": 4.0, "bushing": 2.0}
    catalogue = ("flange", "bushing", "trunnion")

    def _names(clause: str) -> list[str]:
        return [part for part in catalogue if part in clause.casefold()]

    def _run(node, _ctx):
        entity = str(node.arguments["entity"])
        if entity == "bushing":
            raise RuntimeError("the stock system refused the query")
        return {"stock": stock[entity]}

    register_operation(
        OperationSpec(
            name="parts_lookup",
            description="stock level for one or more named parts",
            expand_arguments=lambda clause: [
                {"entity": p} for p in _names(clause) if p in stock
            ],
            run=_run,
            render=lambda node, result: f"{node.arguments['entity']}: {result['stock']} in stock",
            required_result_fields=("stock",),
            exported_value_fields=("stock",),
            subjects_named=_names,
            realize_subject=lambda s: (
                {"entity": s.casefold()} if s.casefold() in stock else None
            ),
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
    try:
        plan, outcomes, composed = _drive(
            "Check the stock of the flange, the bushing and the trunnion, and say which is lowest.",
            _clause("the stock of the flange, the bushing and the trunnion", "parts_lookup"),
            _clause("say which is lowest", "factual_explanation", (0,)),
        )
    finally:
        unregister_operation("parts_lookup")

    assert _state(plan, outcomes, composed, "trunnion") is (
        ObligationState.CAPABILITY_UNAVAILABLE
    ), "a subject nothing can serve was not typed as a capability gap"
    assert _state(plan, outcomes, composed, "bushing") is ObligationState.EXECUTION_FAILED, (
        "a subject that was attempted and failed was not typed as an execution failure"
    )
    assert _state(plan, outcomes, composed, "flange") is ObligationState.SATISFIED


# --- a whole sentence the planner dropped ---------------------------------------------------------


@pytest.mark.usefixtures("fetchers")
def test_a_whole_sentence_the_planner_dropped_is_still_reported():
    """The omitted-clause floor, asserted on its own.

    A sentence carried by no clause names no operation, so nothing can realize it -- it comes to
    rest in CAPABILITY_UNAVAILABLE, and the reader must still be told it went unanswered. Without
    this the sentence is simply absent from a confident reply, which is the original defect one
    level up from a dropped subject.
    """
    plan, _outcomes, composed = _drive(
        "Look up the live prices of Ethereum and Silver in USD and work out how many ounces of "
        "Silver one Ethereum buys. Explain what a stablecoin is.",
        _clause("the live prices of Ethereum in USD", "market_quote"),
        _clause(
            "work out how many ounces of Silver one Ethereum buys",
            "quantitative_reasoning",
            (0,),
        ),
        generation=_Generation(("silver per ethereum", "ethereum_price / silver_price", "oz")),
    )
    dropped = [
        o
        for o in plan.obligations.obligations
        if o.origin is ObligationOrigin.CLAUSE_OMITTED
    ]
    assert dropped, (
        f"a whole sentence carried by no clause produced no obligation: "
        f"{[(o.text, o.origin.value) for o in plan.obligations.obligations]}"
    )
    assert any("stablecoin" in o.text.casefold() for o in dropped)
    assert "stablecoin" in composed.text.casefold(), (
        "the dropped sentence is recorded but never reaches the reader"
    )
