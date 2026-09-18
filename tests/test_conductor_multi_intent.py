"""The measured defect, its two harder cousins, and the mutations that prove the fixes are real.

The regression these lock is not hypothetical. On the shipped build:

    "What is 137 x 29? Explain the calculation briefly.
     Also get the current weather for Kaunas and Tallinn and tell me which city is warmer."

answered the weather and the comparison, and the arithmetic was absent from the reply entirely.
`test_the_shipped_defect_no_longer_loses_the_arithmetic` asserts `3973` is present, and the
`test_main_still_loses_it` control asserts the old path still drops it -- so the first test is
measuring a repair rather than a coincidence.

Planner replies are injected here on purpose. These tests exercise the RUNTIME's handling of a
plan -- expansion, dependency ordering, isolation, completeness -- and injecting the model's
structural output is how that behaviour is isolated from a model's day-to-day variance. They are
not evidence that a model produces good plans; the live drives recorded in the slice report are.
What is never injected is an *answer*: every number in every assertion below is computed by the
runtime from a fetcher result.
"""
from __future__ import annotations

import json
import time
from unittest import mock

import pytest

from core.conductor.node import NodeLifecycle, NodeOutcome
from core.conductor.planner import plan_conductor_turn
from core.conductor.receipts import concurrency_report
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan
from core.live_quote_contract import LiveQuoteResult
from core.weather_result_contract import WeatherResult
from tests.conductor_product import compose_product

ARITHMETIC_AND_WEATHER = (
    "What is 137 x 29? Explain the calculation briefly. "
    "Also get the current weather for Kaunas and Tallinn and tell me which city is warmer."
)

WEATHER_AND_REPO = (
    "Get weather for Kaunas and Tallinn, inspect the provider retry implementation, "
    "then tell me which city is warmer and whether retries have a circuit breaker."
)

KILLER = (
    "Check BTC and ETH prices, get weather for Kaunas and Tallinn, "
    "inspect the provider retry implementation, then tell me which market moved most, "
    "which city is warmer, and whether the retry implementation has a circuit breaker. "
    "Run independent work in parallel where possible."
)

_TEMPS = {"kaunas": 28.0, "tallinn": 22.0}
_QUOTES = {
    "bitcoin": (64000.0, 1.4),
    "ethereum": (3100.0, -3.8),
}
_FETCH_DELAY_S = 0.25


def _weather(location, **_kwargs):
    time.sleep(_FETCH_DELAY_S)
    if location not in _TEMPS:
        return None
    return WeatherResult(
        location=location,
        place_label=location.title(),
        condition="Sunny",
        temperature_c=_TEMPS[location],
        feels_like_c=_TEMPS[location],
        humidity_pct=50.0,
        wind_kmph=10.0,
        observed_at="12:00 PM",
        source_label="wttr.in",
        source_url=f"https://wttr.in/{location}",
    )


def _crypto(coin_ids, **_kwargs):
    time.sleep(_FETCH_DELAY_S)
    out = []
    for coin in coin_ids:
        if coin not in _QUOTES:
            continue
        value, change = _QUOTES[coin]
        out.append(
            LiveQuoteResult(
                asset_key=coin,
                asset_name=coin.title(),
                symbol=coin[:3].upper(),
                value=value,
                currency="USD",
                as_of="",
                source_label="t",
                source_url="https://x",
                kind="crypto",
                change_percent=change,
            )
        )
    return out


def _plan_reply(*clauses: dict) -> str:
    return json.dumps(list(clauses))


def _fake_search(_ctx, query, limit=40):
    """A stand-in workspace index with the shape the real search returns.

    Modelled on the real repository state so the conclusion node has a real distinction to draw:
    `retry_policy.py` implements retries, `circuit_breaker.py` exists, and the two do not meet.
    """
    index = {
        "retry": ["core/retry_policy.py", "core/runtime_tool_contracts.py"],
        "provider": ["core/provider_routing.py", "core/retry_policy.py"],
        "circuit": ["core/circuit_breaker.py"],
        "breaker": ["core/circuit_breaker.py"],
        "retries": ["core/retry_policy.py"],
    }
    matches = [{"path": path, "line": 1, "snippet": query} for path in index.get(query, [])]
    return {"query": query, "ok": True, "matches": matches}


@pytest.fixture()
def fetchers():
    with (
        mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather),
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_crypto),
        mock.patch("core.conductor.operations._search_workspace", side_effect=_fake_search),
    ):
        yield


def _run(prompt: str, reply: str) -> tuple:
    plan = plan_conductor_turn(prompt, ask_model=lambda _s, _p: reply, plan_id="t")
    assert plan is not None, "the conductor declined a turn it must claim"
    outcomes = run_conductor_plan(plan, context=NodeContext(timeout_s=5.0))
    return plan, outcomes, compose_product(plan, outcomes)


# --------------------------------------------------------------------------------------
# ACCEPTANCE A -- the exact shipped defect
# --------------------------------------------------------------------------------------

# The explanation rides INSIDE the arithmetic element rather than being dropped, which is what
# `conductor_system_prompt` instructs: "An instruction about HOW to answer -- 'briefly', 'in
# detail', 'explain it', 'step by step' -- is not a request. Keep it inside the element it
# modifies; never make it one." This fixture used to omit those words from every clause, so the
# canonical obligation floor correctly reported the sentence as carried nowhere. Folding it in is
# the compliant plan the prompt asks for, and it keeps this file's `unserved_count == 0`
# assertions meaning what they were written to mean: a well-formed plan leaves no gap. See
# `tests/test_canonical_obligation_floor.py` for the non-compliant case asserted directly.
A_REPLY = _plan_reply(
    {
        "request": "What is 137 x 29? Explain the calculation briefly",
        "operation": "calculation",
        "depends_on": [],
    },
    {
        "request": "get the current weather for Kaunas and Tallinn",
        "operation": "weather_lookup",
        "depends_on": [],
    },
    {"request": "which city is warmer", "operation": "comparison", "depends_on": [1]},
)


def test_the_plan_carves_the_four_required_semantic_nodes(fetchers) -> None:
    plan, _outcomes, _composed = _run(ARITHMETIC_AND_WEATHER, A_REPLY)
    operations = sorted(node.operation for node in plan.nodes)
    assert operations == ["calculation", "comparison", "weather_lookup", "weather_lookup"]

    comparison = next(node for node in plan.nodes if node.operation == "comparison")
    weather_ids = {node.node_id for node in plan.nodes if node.operation == "weather_lookup"}
    # Both cities, not whichever one the model happened to name. A comparison that waits for one
    # arm can answer before the other has a temperature at all.
    assert set(comparison.depends_on) == weather_ids


def test_the_shipped_defect_no_longer_loses_the_arithmetic(fetchers) -> None:
    _plan, _outcomes, composed = _run(ARITHMETIC_AND_WEATHER, A_REPLY)
    assert "3973" in composed.text
    assert "Kaunas" in composed.text
    assert "Tallinn" in composed.text
    assert "28.0" in composed.text and "22.0" in composed.text
    assert composed.complete and composed.unserved_count == 0


@pytest.mark.parametrize(
    "clause",
    [
        "What is 137 x 29?",
        "What is 137 × 29? Explain the calculation briefly.",
        "What is 137 x 29? Explain briefly.",
        "Explain briefly. What is 137 x 29?",
    ],
)
def test_the_calculation_adapter_finds_its_arithmetic_inside_a_clause_with_prose(clause) -> None:
    """Found on the first live drive, and the reason that drive lost the arithmetic.

    `evaluate_direct_math_request` matches a WHOLE request, so one clause of trailing prose was
    enough for the adapter to report "nothing to act on" and for 3973 to never appear. A planner
    that keeps "What is 137 x 29?" together with "Explain the calculation briefly." is being
    reasonable; the adapter accommodates it rather than the prompt forbidding it.
    """
    from core.conductor.operations import _calculation_expand

    expanded = _calculation_expand(clause)
    assert len(expanded) == 1, clause
    from core.conductor.operations import _evaluate_arithmetic

    assert "3973" in str(_evaluate_arithmetic(expanded[0]["expression_text"]))


def test_a_clause_with_no_arithmetic_at_all_still_expands_to_nothing() -> None:
    """The control for the fix above: loosening the match must not make everything a calculation."""
    from core.conductor.operations import _calculation_expand

    assert _calculation_expand("Explain the calculation briefly.") == []
    assert _calculation_expand("get the weather for Kaunas") == []
    assert _calculation_expand("") == []


def test_main_still_loses_the_arithmetic_so_the_repair_is_measured_not_assumed() -> None:
    """The control. Without it, the test above proves only that some code produced '3973'.

    This drives the path the shipped build actually took: `requirements_for` classifies the whole
    message LIVE_DATA on the strength of two city names, and `build_live_data_plan` then enumerates
    only what its two recognizers know. The arithmetic is not refused -- there is nowhere in that
    plan for it to be represented at all.
    """
    from core.agent_runtime.live_data_plan import build_live_data_plan
    from core.execution_requirements import requirements_for

    assert requirements_for(ARITHMETIC_AND_WEATHER).answer_mode == "LIVE_DATA"
    legacy = build_live_data_plan(ARITHMETIC_AND_WEATHER, plan_id="p", attempt_id="a")
    assert legacy is not None
    assert {task.operation for task in legacy.subtasks} == {"weather_lookup"}
    assert not any("137" in task.entity or "29" in task.entity for task in legacy.subtasks)


def test_the_warmer_city_is_computed_from_results_not_phrased_by_a_model(fetchers) -> None:
    _plan, outcomes, composed = _run(ARITHMETIC_AND_WEATHER, A_REPLY)
    comparison = next(o for o in outcomes if o.node.operation == "comparison")
    assert comparison.succeeded
    assert comparison.result["winner_value"] == 28.0
    assert comparison.result["winner_node_id"].endswith("kaunas")
    # The rendered sentence names Kaunas because the arithmetic picked it, not because a model
    # decided 28 sounds warmer than 22.
    assert "Kaunas has the highest temperature" in composed.text


# --------------------------------------------------------------------------------------
# ACCEPTANCE B -- weather and repository work in one message
# --------------------------------------------------------------------------------------

B_REPLY = _plan_reply(
    {"request": "Get weather for Kaunas and Tallinn", "operation": "weather_lookup", "depends_on": []},
    {
        "request": "inspect the provider retry implementation",
        "operation": "workspace_investigation",
        "depends_on": [],
    },
    {"request": "which city is warmer", "operation": "comparison", "depends_on": [0]},
    {
        "request": "whether retries have a circuit breaker",
        "operation": "conclusion",
        "depends_on": [1],
    },
)


def test_weather_and_repository_work_are_independently_represented(fetchers) -> None:
    plan, outcomes, composed = _run(WEATHER_AND_REPO, B_REPLY)
    operations = sorted(node.operation for node in plan.nodes)
    assert operations == [
        "comparison",
        "conclusion",
        "weather_lookup",
        "weather_lookup",
        "workspace_investigation",
    ]
    assert all(outcome.succeeded for outcome in outcomes), [
        (o.node.node_id, o.failure_reason) for o in outcomes if not o.succeeded
    ]
    assert composed.complete and composed.unserved_count == 0


def test_the_weather_adapter_receives_only_the_two_cities(fetchers) -> None:
    """The contamination guard, and the reason an adapter is handed its clause and not the message.

    On main `_extract_weather_locations` runs against the whole normalized turn, which is how a
    real subtask ended up with `location='price of bitcoin'` and fetched wttr.in for it. Here the
    weather adapter never sees the repository clause, so there is no phrasing of it that could
    become a city.
    """
    plan, _outcomes, _composed = _run(WEATHER_AND_REPO, B_REPLY)
    locations = sorted(
        str(node.arguments.get("location"))
        for node in plan.nodes
        if node.operation == "weather_lookup"
    )
    assert locations == ["kaunas", "tallinn"]

    forbidden = ("retry", "provider", "circuit", "breaker", "implementation", "inspect")
    for node in plan.nodes:
        if node.operation != "weather_lookup":
            continue
        blob = " ".join(str(value) for value in node.arguments.values()).lower()
        assert not any(term in blob for term in forbidden), node.arguments


def test_a_derived_node_starts_only_after_every_dependency_finished(fetchers) -> None:
    """Ordering asserted on the clock, not on list position."""
    _plan, outcomes, _composed = _run(WEATHER_AND_REPO, B_REPLY)
    by_id = {outcome.node.node_id: outcome for outcome in outcomes}
    derived = [o for o in outcomes if o.node.depends_on]
    assert derived, "this plan must contain derived nodes"
    for outcome in derived:
        for dep in outcome.node.depends_on:
            assert by_id[dep].completed_at is not None
            assert outcome.started_at >= by_id[dep].completed_at, (
                f"{outcome.node.node_id} started before {dep} finished"
            )


def test_the_circuit_breaker_conclusion_is_scoped_to_the_files_the_investigation_found(
    fetchers,
) -> None:
    """The answer that separates a real conclusion from a filename match.

    A `CircuitBreaker` class existing somewhere is not the question; whether the retry path uses
    one is. The conclusion node only reaches that distinction because it reads the investigation
    node's structured file set and asks about its own subject *within* it.
    """
    _plan, outcomes, composed = _run(WEATHER_AND_REPO, B_REPLY)
    conclusion = next(o for o in outcomes if o.node.operation == "conclusion")
    assert conclusion.succeeded
    assert conclusion.result["present_in_scope"] is False
    assert conclusion.result["exists_elsewhere"] is True
    assert "core/circuit_breaker.py" in conclusion.result["elsewhere_files"]
    assert "No —" in composed.text and "not on this path" in composed.text


# --------------------------------------------------------------------------------------
# ACCEPTANCE C -- markets, weather and repository in one message
# --------------------------------------------------------------------------------------

C_REPLY = _plan_reply(
    {"request": "Check BTC and ETH prices", "operation": "market_quote", "depends_on": []},
    {"request": "get weather for Kaunas and Tallinn", "operation": "weather_lookup", "depends_on": []},
    {
        "request": "inspect the provider retry implementation",
        "operation": "workspace_investigation",
        "depends_on": [],
    },
    {"request": "which market moved most", "operation": "comparison", "depends_on": [0]},
    {"request": "which city is warmer", "operation": "comparison", "depends_on": [1]},
    {
        "request": "whether the retry implementation has a circuit breaker",
        "operation": "conclusion",
        "depends_on": [2],
    },
)


def test_every_requested_output_maps_to_a_plan_node(fetchers) -> None:
    plan, _outcomes, composed = _run(KILLER, C_REPLY)
    operations = sorted(node.operation for node in plan.nodes)
    assert operations == [
        "comparison",
        "comparison",
        "conclusion",
        "market_quote",
        "market_quote",
        "weather_lookup",
        "weather_lookup",
        "workspace_investigation",
    ]
    # Five independent roots -- two assets, two cities, one repository search.
    assert len(plan.graph.roots) == 5
    assert composed.complete
    assert set(composed.provenance) == {node.node_id for node in plan.nodes}


def test_the_killer_answers_all_three_derived_questions(fetchers) -> None:
    _plan, outcomes, composed = _run(KILLER, C_REPLY)
    assert all(o.succeeded for o in outcomes), [
        (o.node.node_id, o.failure_reason) for o in outcomes if not o.succeeded
    ]
    # Largest absolute mover is Ethereum at -3.8 against Bitcoin's +1.4 -- computed, and the sign
    # is exactly why `max_abs` rather than `max`.
    mover = next(
        o for o in outcomes
        if o.node.operation == "comparison" and o.result["metric"] == "change_24h_pct"
    )
    assert mover.result["winner_node_id"].endswith("ethereum")
    assert "Ethereum has the largest absolute 24h change" in composed.text
    assert "Kaunas has the highest temperature" in composed.text
    assert "circuit breaker" in composed.text.lower()


def test_independent_roots_genuinely_overlap_in_the_killer(fetchers) -> None:
    """Concurrency proved from the runtime's own clocks, never from prose."""
    _plan, outcomes, _composed = _run(KILLER, C_REPLY)
    report = concurrency_report(outcomes)
    assert report.proves_concurrency
    assert report.max_concurrent >= 2

    kinds = {o.node.node_id: o.node.operation for o in outcomes}
    cross_domain = {
        frozenset({kinds[left], kinds[right]}) for left, right in report.overlapping_pairs
    }
    # Overlap ACROSS domains, not merely between two weather fetches. Two cities overlapping only
    # proves the weather adapter is concurrent, which was already true before this slice.
    assert any(len(pair) == 2 for pair in cross_domain), cross_domain


# --------------------------------------------------------------------------------------
# FAIL CLOSED
# --------------------------------------------------------------------------------------


def test_a_clause_no_operation_can_serve_becomes_unresolved_not_dropped(fetchers) -> None:
    """The core promise. A named request that cannot be served is reported, never omitted."""
    reply = _plan_reply(
        {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
        {
            "request": "get the current weather for Kaunas and Tallinn",
            "operation": "weather_lookup",
            "depends_on": [],
        },
        {"request": "Explain the calculation briefly", "operation": "essay", "depends_on": []},
    )
    _plan, outcomes, composed = _run(ARITHMETIC_AND_WEATHER, reply)

    unresolved = [o for o in outcomes if o.state is NodeLifecycle.UNRESOLVED]
    assert len(unresolved) == 1
    assert "no registered operation named 'essay'" in unresolved[0].failure_reason
    assert composed.complete
    assert "Could not be answered:" in composed.text
    assert "Explain the calculation briefly" in composed.text
    # The rest of the message is still answered -- one unservable clause must not cost the others.
    assert "3973" in composed.text and "Kaunas" in composed.text


def test_a_derived_clause_with_no_dependencies_is_refused_rather_than_invented(fetchers) -> None:
    reply = _plan_reply(
        {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
        {"request": "which city is warmer", "operation": "comparison", "depends_on": []},
    )
    _plan, outcomes, composed = _run(ARITHMETIC_AND_WEATHER, reply)
    unresolved = [o for o in outcomes if o.state is NodeLifecycle.UNRESOLVED]
    # The property this test is about: the dependency-less comparison was refused, exactly once,
    # for the stated reason. Asserted by reason rather than by counting every unresolved node --
    # this injected plan also drops two of the message's own sentences, and the canonical
    # obligation floor now reports those as carried-nowhere rather than letting them vanish. Those
    # reports are a different (and correct) thing appearing in the same list.
    refused = [o for o in unresolved if "needs earlier results" in o.failure_reason]
    assert len(refused) == 1
    assert composed.complete


def test_one_failed_branch_does_not_erase_its_successful_siblings() -> None:
    """A systematic fault in one adapter must cost exactly one node."""
    with (
        mock.patch(
            "tools.web.web_research.structured_weather_lookup",
            side_effect=RuntimeError("provider exploded"),
        ),
        mock.patch("core.conductor.operations._search_workspace", side_effect=_fake_search),
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_crypto),
    ):
        _plan, outcomes, composed = _run(KILLER, C_REPLY)

    states = {o.node.operation: o.state for o in outcomes if o.node.operation == "market_quote"}
    assert states == {"market_quote": NodeLifecycle.SUCCEEDED}
    assert all(
        o.state is NodeLifecycle.FAILED for o in outcomes if o.node.operation == "weather_lookup"
    )
    # The warmer-city comparison depended on the failed arm and must say so rather than guess.
    warmer = next(
        o for o in outcomes
        if o.node.operation == "comparison" and "temperature" in str(o.node.arguments)
    )
    assert warmer.state is NodeLifecycle.DEPENDENCY_FAILED
    assert composed.complete
    assert "Bitcoin" in composed.text and "Ethereum" in composed.text
    assert "warmer" in composed.text.lower()
    assert "not attempted" in composed.text


# --------------------------------------------------------------------------------------
# MUTATIONS -- each must fail the NAMED assertion, not merely turn something red
# --------------------------------------------------------------------------------------


def test_mutation_deleting_a_planned_node_turns_the_completeness_check_red(fetchers) -> None:
    """Delete one planned subtask -> completeness RED.

    This is the mutation that matters most: losing a node is precisely the shipped defect, so an
    answer that can be built without noticing would leave the repair unguarded.
    """
    plan, outcomes, composed = _run(ARITHMETIC_AND_WEATHER, A_REPLY)
    assert composed.complete  # control

    survivors = [o for o in outcomes if o.node.operation != "calculation"]
    assert len(survivors) == len(outcomes) - 1
    mutated = compose_product(plan, survivors)

    assert mutated.complete is False
    assert any(node_id.endswith("calculation") for node_id in mutated.missing_node_ids)
    assert "3973" not in mutated.text


def test_mutation_serializing_independent_nodes_turns_the_overlap_proof_red(fetchers) -> None:
    """Serialize independent nodes -> concurrency proof RED where parallel_preferred applies.

    Runs the SAME plan through the sequential scheduler that exists only for this purpose. Without
    a control known to fail, `proves_concurrency` being True is indistinguishable from the code
    merely having run.
    """
    from core.conductor.scheduler import run_conductor_plan_sequential

    plan = plan_conductor_turn(KILLER, ask_model=lambda _s, _p: C_REPLY, plan_id="t")
    assert plan is not None and plan.parallel_preferred

    concurrent_outcomes = run_conductor_plan(plan, context=NodeContext(timeout_s=5.0))
    assert concurrency_report(concurrent_outcomes).proves_concurrency  # control

    serial_outcomes = run_conductor_plan_sequential(plan, context=NodeContext(timeout_s=5.0))
    serial = concurrency_report(serial_outcomes)
    assert serial.proves_concurrency is False
    assert serial.max_concurrent == 1
    assert serial.overlapping_pairs == ()
    # Same work, same answers -- only the overlap evidence differs. If the serial run also lost
    # results the mutation would be proving the wrong thing.
    assert serial.timed_node_count == concurrency_report(concurrent_outcomes).timed_node_count
    assert compose_product(plan, serial_outcomes).complete


def test_mutation_inventing_a_success_without_a_backing_result_turns_binding_red(fetchers) -> None:
    """Invent success without receipt -> answer binding RED.

    A node hand-marked SUCCEEDED with rendered prose but no result carrying its declared fields is
    exactly what a fabricating adapter would produce. `NodeOutcome.succeeded` enforces
    `required_result_fields`, so the fabricated sentence cannot reach the answer -- the check
    `live_data_plan` declares and never performs.
    """
    plan, outcomes, composed = _run(ARITHMETIC_AND_WEATHER, A_REPLY)
    assert "28.0" in composed.text  # control: the real value is there

    fabricated = []
    for outcome in outcomes:
        if outcome.node.operation != "weather_lookup":
            fabricated.append(outcome)
            continue
        fabricated.append(
            NodeOutcome(
                node=outcome.node,
                state=NodeLifecycle.SUCCEEDED,
                result={},  # no condition, no temperature_c -- nothing was actually fetched
                rendered="Kaunas: Blazing, 45.0°C (source: trust me)",
                started_at=outcome.started_at,
                completed_at=outcome.completed_at,
            )
        )

    mutated = compose_product(plan, fabricated)
    assert "45.0" not in mutated.text
    assert "trust me" not in mutated.text
    assert mutated.unserved_count >= 2
    assert "missing: condition, temperature_c" in mutated.text
