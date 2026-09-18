"""A deterministic purchase decomposition is RUNTIME-OWNED: no proposer call, no projected twin,
and its role-bound derivations are not generation nodes.

Measured on the owner's turn (2026-09-10, build 772256a7, served events): "what is the price of ETH
and how much of silver I can buy if I sell 1 eth? also how much of gold" -- the purchase arm minted
the correct five clauses, but every one carried `origin="model"`, so `build_plan_from_clauses`
(a) bought a 6.8 s semantic-proposer call and (b) PROJECTED two extra `quantitative_reasoning`
nodes over the same asks ("how much of gold", no roles, no payment leg). One projected node took
the single generation slot for 66 s (a free cloud reasoning model), the two role-bound derivations
waited behind it as generation nodes, and all four died at the 75 s plan deadline with their
quotes already fetched: "could not be answered: the runtime ran out of time (missing: steps,
values)".

Three laws, each with its control:
  * runtime-owned clauses consult no proposer and mint no twin (control: the same clauses with
    `origin="model"` DO, which is exactly the served 7-node plan);
  * a derivation whose roles are bound at plan time is not a generation node (control: the same
    node forced back to `needs_generation=True` starves behind a slow explanation);
  * a purchase-shaped clause a model labelled as an explanation is re-typed to a derivation.
"""
from __future__ import annotations

import time
from dataclasses import replace
from types import SimpleNamespace
from unittest import mock

from core.conductor.planner import (
    ProposedClause,
    _deterministic_purchasable_amount_plan,
    build_plan_from_clauses,
    plan_conductor_turn,
)
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan
from core.conductor.shared_context import extract_shared_context
from tests.semantic_proposer import frame, proposer

OWNER = 'what is the price of ETH and how much of silver I can buy if I sell 1 eth? also how much of gold "'
FRESH = "what is sol going for and how much brent could I pick up if I sell 3 sol? also how much silver"

_QUOTES = {
    "silver": 64.67, "gold": 4402.0, "ethereum": 2433.8, "binancecoin": 709.29,
    "solana": 140.0, "brent_crude": 106.78,
}


def _fake_market(subtask, timeout_s=None):
    key = str(
        getattr(subtask, "asset_key", "") or getattr(subtask, "entity", "") or ""
    ).casefold().replace(" ", "_")
    assert key, "the market subtask names no asset"
    for name, price in _QUOTES.items():
        if name in key or key in name:
            return SimpleNamespace(
                result={"price": price, "currency": "USD", "source": "test feed", "asset_key": name},
                failure_reason="",
            )
    return SimpleNamespace(result=None, failure_reason=f"no quote for {key}")


def _never(_system, prompt):
    raise AssertionError("a model was consulted: " + prompt[:80])


def _twin_proposer(text: str):
    """The bounded proposer's reading of the owner class: one quantitative frame per ask, exactly
    the frames whose projection produced the served plan's two extra nodes."""
    frames = []
    for scope in ("how much of silver I can buy if I sell 1 eth", "how much of gold",
                  "how much brent could I pick up if I sell 3 sol", "how much silver"):
        if scope in text:
            frames.append(frame("quantitative_reasoning", scope=scope, predicate="how much"))
    return proposer(*frames)


# -- law 1: runtime-owned, so no proposer and no twin -----------------------------------------------


def test_the_purchase_arm_marks_every_clause_runtime_owned():
    for text in (OWNER, FRESH):
        clauses = _deterministic_purchasable_amount_plan(text)
        assert clauses, text
        assert {clause.origin for clause in clauses} == {"certain_recognizer"}, [
            (c.request, c.origin) for c in clauses
        ]


def test_a_runtime_owned_purchase_plan_never_consults_the_proposer_and_mints_no_twin():
    calls: list[str] = []

    def recording_proposer(system: str, prompt: str) -> str:
        calls.append(prompt)
        return _twin_proposer(prompt)(system, prompt)

    for text in (OWNER, FRESH):
        plan = plan_conductor_turn(text, ask_model=_never, propose_semantics=recording_proposer, plan_id="owned")
        assert plan is not None
        assert calls == [], "the proposer was consulted for a runtime-owned decomposition"
        derivations = [n for n in plan.nodes if n.operation == "quantitative_reasoning"]
        assert len(derivations) == 2, [n.node_id for n in plan.nodes]
        assert all(n.arguments.get("roles") for n in derivations), "a derivation without bound roles"
        assert len(plan.nodes) == 5, [n.node_id for n in plan.nodes]


def test_the_control_the_served_plan_had_model_origin_clauses_and_grew_projected_twins():
    """NEGATIVE CONTROL, the served defect reproduced: the very same clauses with `origin="model"`
    consult the proposer and gain two role-less derivation nodes (7 nodes, as served)."""
    calls: list[str] = []

    def recording_proposer(system: str, prompt: str) -> str:
        calls.append(prompt)
        return _twin_proposer(prompt)(system, prompt)

    clauses = [replace(c, origin="model") for c in _deterministic_purchasable_amount_plan(OWNER)]
    plan = build_plan_from_clauses(
        clauses,
        original_request=OWNER,
        plan_id="control",
        shared_context=extract_shared_context(OWNER),
        propose_semantics=recording_proposer,
    )
    assert calls, "the control did not consult the proposer"
    twins = [
        n for n in plan.nodes
        if n.operation == "quantitative_reasoning" and not n.arguments.get("roles")
    ]
    assert twins and all(n.needs_generation for n in twins), [n.node_id for n in plan.nodes]
    assert len(plan.nodes) == 7, [n.node_id for n in plan.nodes]


# -- law 2: role-bound derivations are not generation nodes ------------------------------------------


def test_role_bound_derivations_are_dispatched_as_model_free_work():
    plan = plan_conductor_turn(OWNER, ask_model=_never, propose_semantics=_never, plan_id="free")
    assert plan is not None
    for node in plan.nodes:
        if node.operation == "quantitative_reasoning":
            assert node.needs_generation is False, node.node_id
    with mock.patch("core.agent_runtime.live_data_runner._run_market_subtask", _fake_market):
        outcomes = run_conductor_plan(
            plan, context=NodeContext(run_generation=None, timeout_s=5.0), plan_deadline_s=10.0
        )
    assert all(o.succeeded for o in outcomes), [(o.node.node_id, o.failure_reason) for o in outcomes]
    figures = {o.node.node_id.rsplit(":", 1)[-1]: o.result for o in outcomes if o.node.operation == "quantitative_reasoning"}
    values = sorted(v for r in figures.values() for v in r["values"].values())
    assert values == sorted([2433.8 / 64.67, 2433.8 / 4402.0])


def _mixed_plan():
    """Two quotes, one role-bound derivation and one explanation that buys a generation."""
    original = (
        "price of silver and oil; how much oil can I buy with 1 kg silver; "
        "and explain what brent crude is"
    )
    clauses = [
        ProposedClause(0, "price of silver", "market_quote", ()),
        ProposedClause(1, "price of oil", "market_quote", ()),
        ProposedClause(2, "how much oil can I buy with 1 kg silver", "quantitative_reasoning", (0, 1)),
        ProposedClause(3, "explain what brent crude is", "factual_explanation", ()),
    ]
    plan = build_plan_from_clauses(
        clauses, original_request=original, plan_id="mixed", shared_context=extract_shared_context(original)
    )
    ops = {n.operation for n in plan.nodes}
    assert {"market_quote", "quantitative_reasoning", "factual_explanation"} <= ops, [
        (n.node_id, n.operation) for n in plan.nodes
    ]
    return plan


def _slow_generation(_system, _prompt):
    time.sleep(2.5)
    return "Brent crude is a North Sea benchmark."


def test_a_derivation_no_longer_waits_behind_a_slow_explanation_for_the_generation_slot():
    plan = _mixed_plan()
    with mock.patch("core.agent_runtime.live_data_runner._run_market_subtask", _fake_market):
        outcomes = run_conductor_plan(
            plan, context=NodeContext(run_generation=_slow_generation, timeout_s=5.0), plan_deadline_s=1.2
        )
    by_op = {o.node.operation: o for o in outcomes}
    assert by_op["quantitative_reasoning"].succeeded, by_op["quantitative_reasoning"].failure_reason
    assert "barrels" in by_op["quantitative_reasoning"].rendered
    # The explanation is the one that ran out of time: the deadline is real and it bit the
    # generation node, not the arithmetic.
    assert not by_op["factual_explanation"].succeeded


def test_the_control_a_derivation_flagged_as_generation_starves_behind_the_slow_explanation():
    """NEGATIVE CONTROL for the scheduling cause: the same plan with the derivation forced back
    to `needs_generation=True` dies at the deadline, exactly as the served turn did."""
    from core.conductor.graph import ConductorGraph

    plan = _mixed_plan()
    forced = [
        replace(n, needs_generation=True) if n.operation == "quantitative_reasoning" else n
        for n in plan.nodes
    ]
    plan = replace(plan, graph=ConductorGraph(tuple(forced)))
    with mock.patch("core.agent_runtime.live_data_runner._run_market_subtask", _fake_market):
        outcomes = run_conductor_plan(
            plan, context=NodeContext(run_generation=_slow_generation, timeout_s=5.0), plan_deadline_s=1.2
        )
    by_op = {o.node.operation: o for o in outcomes}
    assert not by_op["quantitative_reasoning"].succeeded
    assert by_op["quantitative_reasoning"].failure_code.value == "plan_deadline_expired"


# -- law 3: an explanation label cannot replace a purchase derivation ---------------------------------


def test_a_purchase_clause_a_model_labelled_as_an_explanation_is_re_typed_to_a_derivation():
    original = "what is the brent price? how much crude can I get if I sell 2 kg of silver?"
    clauses = [
        ProposedClause(0, "what is the brent price?", "market_quote", ()),
        ProposedClause(1, "how much crude can I get if I sell 2 kg of silver?", "factual_explanation", ()),
    ]
    plan = build_plan_from_clauses(
        clauses, original_request=original, plan_id="retype", shared_context=extract_shared_context(original)
    )
    ops = [(n.operation, n.request_text) for n in plan.nodes]
    assert not any(op == "factual_explanation" and "crude can I get" in text for op, text in ops), ops
    derivations = [n for n in plan.nodes if n.operation == "quantitative_reasoning"]
    assert derivations, ops
    assert all(n.arguments.get("roles") for n in derivations)
