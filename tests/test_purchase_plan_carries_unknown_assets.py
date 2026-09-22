"""A purchase derivation beside an asset this runtime cannot quote (2026-09-08, operator's live turn).

Measured on build 0.5.0 (sweep7): "lpg price and how much of silver i can buy if i sell 1 bnb?" served
the silver and BNB quotes and then reported the derivation as "the runtime hit an internal fault
(missing: steps, values)" and LPG as "the available answer path does not safely match". Cause: the
deterministic purchase arm abstained from the WHOLE message because one unit was foreign
(`_purchase_span_text` returned ""), the free model planned the derivation, and its incomplete reply was
typed NODE_EXCEPTION.

Law now: the arm partitions the message -- the purchase units are planned deterministically; a foreign
unit no registered lane claims is carried as an unresolved clause stating the fact; a foreign unit a
registered lane claims (weather) still hands the turn to the model planner. A quantitative node whose
arithmetic could not be set up states that, and never raises.

Held-out wording throughout (never the operator's sentence).
"""
from __future__ import annotations

import pytest

from core.conductor.planner import _deterministic_purchasable_amount_plan, _purchase_partition

PROOF = (
    ("helium price and how much gold do i get if i sell 2 eth", "helium", ("gold", "eth")),
    ("uranium price, and how much gold can I buy if I sell 3 sol", "uranium", ("gold", "sol")),
    ("neon price plus how much silver do i get for 0.5 btc", "neon", ("silver", "btc")),
)


@pytest.mark.parametrize("text, unknown, quoted", PROOF)
def test_the_purchase_is_planned_and_the_unknown_asset_is_named(text: str, unknown: str, quoted: tuple[str, str]) -> None:
    span, foreign = _purchase_partition(text)
    assert span and foreign == (f"{unknown} price",), (span, foreign)
    plan = _deterministic_purchasable_amount_plan(text)
    ops = [c.operation for c in plan]
    assert ops.count("market_quote") == 3 and ops.count("quantitative_reasoning") == 1, ops
    quotes = {c.request for c in plan if c.operation == "market_quote" and not c.unresolved_reason}
    assert quotes == {f"price of {quoted[0]}", f"price of {quoted[1]}"}, quotes
    derivation = next(c for c in plan if c.operation == "quantitative_reasoning")
    assert set(derivation.depends_on) == {c.index for c in plan if c.request in quotes}
    unresolved = [c for c in plan if c.unresolved_reason]
    assert len(unresolved) == 1 and unresolved[0].request == f"{unknown} price"
    assert unknown in unresolved[0].unresolved_reason and "not an asset this runtime can quote" in unresolved[0].unresolved_reason


def test_a_purchase_beside_a_lane_claimed_ask_is_still_left_to_the_model_planner() -> None:
    """The law the span scoping exists for: weather has a lane; the arm must not swallow it."""
    span, foreign = _purchase_partition("how much gold can I buy with 1 bnb and what is the weather in Rome")
    assert span == "" and foreign == ()
    assert _deterministic_purchasable_amount_plan("how much gold can I buy with 1 bnb and what is the weather in Rome") == ()


def test_a_pure_purchase_plans_exactly_as_before() -> None:
    plan = _deterministic_purchasable_amount_plan("how much silver can i get for 3 bnb")
    assert [c.operation for c in plan] == ["market_quote", "market_quote", "quantitative_reasoning"]
    assert all(not c.unresolved_reason for c in plan)


def test_the_unresolved_clause_becomes_the_fail_closed_node_with_its_reason() -> None:
    """Through the real plan builder: the carried reason reaches the node, not a routing excuse."""
    from core.conductor.planner import plan_conductor_turn
    from core.conductor.registry import UNRESOLVED_OPERATION

    text = "helium price and how much gold do i get if i sell 2 eth"
    plan = plan_conductor_turn(text, ask_model=lambda _system, _prompt: "[]")
    assert plan is not None, "the deterministic arm must plan this turn without the model"
    unresolved = [n for n in plan.nodes if n.operation == UNRESOLVED_OPERATION]
    assert len(unresolved) == 1 and "helium" in unresolved[0].unresolved_reason, [(n.operation, n.request_text) for n in plan.nodes]
    assert any(n.operation == "quantitative_reasoning" for n in plan.nodes)


def test_an_arithmetic_plan_the_model_never_returned_is_a_stated_outcome_not_a_fault() -> None:
    from types import SimpleNamespace

    from core.conductor.operations import _quantitative_run
    from core.conductor.registry import NodeContext
    from core.conductor.shared_context import SharedTurnContext

    node = SimpleNamespace(arguments={"clause": "what fraction of the total is the second amount"}, request_text="what fraction of the total is the second amount", depends_on=())
    shared = SharedTurnContext.from_text("the total is 400 units and the second amount is 100 units") if hasattr(SharedTurnContext, "from_text") else None
    ctx = NodeContext(shared_context=shared, derived_facts={"total": 400.0, "second": 100.0}, run_generation=lambda _system, _prompt: "sorry, I cannot help with that")
    result = _quantitative_run(node, ctx)  # must not raise
    assert result["steps"] == [] and result["values"] == {}
    assert result["cannot_determine"] and "model" in result["cannot_determine"][0]
