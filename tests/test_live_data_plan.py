"""The typed plan for the exact market/weather benchmark: seven subtasks, typed, immutable.

    Market: gold, silver, Bitcoin, BNB
    Weather: Kaunas, Tallinn, Warsaw

Each subtask has an immutable id, a typed operation, typed arguments, a required result-field
list, a tool assignment, and a permission-checkable intent -- built before any tool executes, from
the same recognizers the deterministic fetch lanes already trust. The full user prompt must never
become a city or ticker: proven below by construction, not merely asserted.
"""

from __future__ import annotations

from unittest import mock

from core.agent_runtime.live_data_plan import (
    SubtaskLifecycle,
    SubtaskOutcome,
    build_live_data_plan,
    evaluate_approval_policy,
)
from core.mode_permission_policy import reset_mode_permission_state, set_active_mode

BENCHMARK = (
    "Give me the current price and 24-hour change for gold, silver, Bitcoin, and BNB, "
    "and the weather in Kaunas, Tallinn, and Warsaw."
)


def test_the_benchmark_produces_exactly_seven_typed_subtasks() -> None:
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    assert plan is not None
    assert len(plan.subtasks) == 7
    assert len(plan.market_subtasks()) == 4
    assert len(plan.weather_subtasks()) == 3


def test_every_market_asset_is_named_correctly() -> None:
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    keys = {task.arguments["asset_key"] for task in plan.market_subtasks()}
    assert keys == {"gold", "silver", "bitcoin", "binancecoin"}


def test_every_weather_city_is_named_correctly_and_never_a_prompt_fragment() -> None:
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    locations = {task.arguments["location"] for task in plan.weather_subtasks()}
    assert locations == {"kaunas", "tallinn", "warsaw"}
    # The literal proof the handover asked for: no subtask's location argument contains prompt
    # prose from outside the weather clause.
    for task in plan.weather_subtasks():
        assert "give" not in task.arguments["location"]
        assert "price" not in task.arguments["location"]
        assert len(task.arguments["location"].split()) <= 2


def test_every_subtask_id_is_unique_and_stable_across_two_builds() -> None:
    plan_a = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    plan_b = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-2")
    ids_a = [task.subtask_id for task in plan_a.subtasks]
    ids_b = [task.subtask_id for task in plan_b.subtasks]
    assert len(ids_a) == len(set(ids_a))                 # unique within one plan
    assert ids_a == ids_b                                 # stable given the same plan_id + text


def test_subtasks_are_immutable() -> None:
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    task = plan.subtasks[0]
    try:
        task.entity = "tampered"  # type: ignore[misc]
        raised = False
    except Exception:
        raised = True
    assert raised, "LiveDataSubtask must be frozen"


def test_required_result_fields_are_declared_before_execution() -> None:
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    for task in plan.subtasks:
        assert task.required_result_fields
        if task.operation == "market_quote":
            assert "price" in task.required_result_fields
        else:
            assert "condition" in task.required_result_fields


def test_a_single_asset_request_still_builds_a_one_subtask_plan() -> None:
    plan = build_live_data_plan("what is the current price of bitcoin", plan_id="p", attempt_id="a")
    assert plan is not None
    assert len(plan.subtasks) == 1
    assert plan.subtasks[0].arguments["asset_key"] == "bitcoin"


def test_a_non_live_data_request_builds_no_plan() -> None:
    assert build_live_data_plan("what is the capital of France", plan_id="p", attempt_id="a") is None


def test_a_no_price_keyword_phrasing_still_builds_a_plan() -> None:
    """Found live: "Market data only for Bitcoin and gold." was classified LIVE_DATA by
    requirements_for() (via price_assets_named, which names assets without needing a "price"
    keyword) but built ZERO subtasks here, because the primary extraction path
    (_looks_like_price_query_all/_looks_like_market_quote_query_all) both gate on _PRICE_KEYWORDS
    first -- "market data" doesn't contain the literal word "price". The turn fell through to the
    model with tools_required=True and no plan able to satisfy it. Fixed with a fallback pass over
    price_assets_named's own hits, resolved against the same alias tables without the keyword gate.
    """
    plan = build_live_data_plan("Market data only for Bitcoin and gold.", plan_id="p", attempt_id="a")
    assert plan is not None
    keys = {task.arguments["asset_key"] for task in plan.market_subtasks()}
    assert keys == {"bitcoin", "gold"}
    kinds = {task.arguments["asset_key"]: task.arguments["kind"] for task in plan.market_subtasks()}
    assert kinds["bitcoin"] == "crypto"
    assert kinds["gold"] == "commodity"


def test_the_market_data_phrasing_is_served_by_the_vocabulary_not_the_presence_fallback() -> None:
    """SENTINEL G4 repair, 2026-08-06 -- this test previously asserted the OPPOSITE, and the
    repair made its premise obsolete rather than breaking the product.

    It used to read: patch `price_assets_named` to return nothing, and "Market data only for
    Bitcoin and gold." collapses to no plan -- proving an ungated presence fallback was the only
    thing serving that phrasing. That was true because "market data" was not in the market
    vocabulary, so the keyword-gated recognizers genuinely could not see it, and an ungated
    fallback was added to compensate.

    The G4 repair had to make that vocabulary authoritative anyway (asset PRESENCE stopped being
    evidence of a market request -- see `core.market_intent`), which meant putting "market data"
    and "market"/"markets" into it. That is the fix the original incident always wanted: the
    phrasing is now resolved by the primary recognizers directly, and the compensating fallback is
    subsumed rather than load-bearing.

    The product contract itself is unchanged and still asserted directly above, in
    `test_a_no_price_keyword_phrasing_still_builds_a_plan`. What is asserted here is only WHICH
    path delivers it -- and the sabotage now targets the element that actually is load-bearing.
    """
    with mock.patch("core.agent_runtime.fast_live_info_price.price_assets_named", return_value=[]):
        plan = build_live_data_plan("Market data only for Bitcoin and gold.", plan_id="p", attempt_id="a")
        benchmark_plan = build_live_data_plan(BENCHMARK, plan_id="p2", attempt_id="a2")
    # Unchanged product outcome with the fallback disabled: the vocabulary carries it now.
    assert plan is not None
    assert {task.arguments["asset_key"] for task in plan.market_subtasks()} == {"bitcoin", "gold"}
    assert benchmark_plan is not None and len(benchmark_plan.subtasks) == 7  # unaffected


def test_sabotage_removing_market_from_the_vocabulary_reproduces_the_no_keyword_incident() -> None:
    """The real load-bearing element after the G4 repair: the market vocabulary itself.

    Strip the market-domain words that carry a phrasing naming no price word ("market data",
    "market", "markets") and the exact original incident reproduces -- "Market data only for
    Bitcoin and gold." plans nothing at all, which is the empty-plan bug the ungated fallback was
    originally added to paper over. The ordinary keyword-shaped benchmark is untouched, confirming
    the sabotage is scoped to the no-price-word path and is not just breaking market detection
    wholesale.
    """
    import core.market_intent as market_intent

    survivors = tuple(
        term for term in market_intent.MARKET_TERMS
        if term not in ("market", "markets", "market data", "market price", "market value")
    )
    sabotaged = market_intent._boundary_re(survivors)
    with mock.patch.object(market_intent, "_MARKET_TERM_RE", sabotaged):
        # SINGLE asset, 2026-08-16. The original phrasing here was "... for Bitcoin and gold.", and
        # it stopped reproducing the incident once `core.semantic_claim_authority` landed -- not
        # because the vocabulary stopped being load-bearing, but because a coordinated pair of
        # CURATED assets ("Bitcoin and gold") is now an independent admission path, which is what
        # makes "compare BTC and ETH" work. Two paths meant this sabotage could no longer prove
        # anything about either. Narrowing the phrasing to one asset removes the confound and
        # restores exactly the property this test names; the second path is asserted below rather
        # than left silent.
        plan = build_live_data_plan("Market data only for Bitcoin.", plan_id="p", attempt_id="a")
        coordinated = build_live_data_plan(
            "Market data only for Bitcoin and gold.", plan_id="p3", attempt_id="a3"
        )
        benchmark_plan = build_live_data_plan(BENCHMARK, plan_id="p2", attempt_id="a2")

    assert plan is None, (
        "sabotage setup failed: removing the market-domain vocabulary must reproduce the original "
        "empty-plan incident for a phrasing that names no price word"
    )
    assert coordinated is not None, (
        "the coordinated-curated-assets path is a SECOND, independent admission and does not depend "
        "on the market vocabulary -- stated here so a future reader does not mistake this test for "
        "proving the vocabulary is the only way in"
    )
    assert benchmark_plan is not None and len(benchmark_plan.subtasks) == 7  # unaffected
    # The unsabotaged control: the phrasing this test narrowed away from still plans normally.
    assert build_live_data_plan("Market data only for Bitcoin.", plan_id="p4", attempt_id="a4") is not None


def test_every_subtask_resolves_to_allow_in_manual_mode() -> None:
    """Ties the typed plan to the product-decision approval fix: every one of the seven subtasks
    must resolve without approval in Manual mode, or the benchmark's "no approval card at all"
    expectation silently breaks again."""
    reset_mode_permission_state()
    try:
        set_active_mode("chat-a", "manual", project_id="", client_turn_id="turn-a")
        ctx = {"runtime_session_id": "chat-a", "operating_mode": "manual", "workspace_root": "/tmp"}
        plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
        decisions = evaluate_approval_policy(plan, source_context=ctx)
        assert len(decisions) == 7
        assert all(decision.allowed for decision in decisions.values())
    finally:
        reset_mode_permission_state()


def test_subtask_outcome_starts_planned_and_is_not_ok_until_succeeded() -> None:
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    outcome = SubtaskOutcome(subtask=plan.subtasks[0])
    assert outcome.state is SubtaskLifecycle.PLANNED
    assert outcome.ok is False
    outcome.state = SubtaskLifecycle.SUCCEEDED
    outcome.result = {"price": 4200.0}
    assert outcome.ok is True


def test_plan_and_subtasks_serialize_to_plain_dicts_for_inspection() -> None:
    """"The complete plan must remain inspectable in Activity" -- proven as a real contract: it
    must round-trip through plain dict/JSON-safe structures, not just look printable."""
    import json

    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    payload = plan.to_dict()
    json.dumps(payload)  # must not raise
    assert payload["plan_id"] == "plan-1"
    assert len(payload["subtasks"]) == 7


def test_sabotage_a_market_asset_key_leaking_raw_prompt_text() -> None:
    """Proves the entity boundary is load-bearing: if the market extractor were replaced with
    something that returns the whole message instead of a real asset id, the subtask arguments
    would carry the leak straight through -- exactly the corruption class this plan exists to
    prevent structurally.
    """
    with mock.patch(
        "tools.web.web_research._looks_like_price_query_all",
        return_value=["Give me the current price and 24-hour change for gold silver bitcoin bnb"],
    ):
        plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    leaked = [t for t in plan.market_subtasks() if len(t.arguments["asset_key"].split()) > 3]
    assert leaked, "sabotage should have produced a subtask whose asset_key is prompt prose"
    # This is the negative control: real (unsabotaged) construction never produces this shape.
    real_plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    assert all(len(t.arguments["asset_key"].split()) <= 2 for t in real_plan.market_subtasks())
