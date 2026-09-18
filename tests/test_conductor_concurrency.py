"""The overlap proof itself: its arithmetic, its edge cases, and its agreement with the lane it replaces.

`proves_concurrency` is the only thing standing between "the runtime ran these at the same time"
and a model saying so. If the sweep is wrong, every concurrency claim in the product is wrong, so
it is tested as arithmetic on hand-built intervals rather than only through the scheduler.

The cross-check against `core.agent_runtime.live_data_runner.concurrency_report` is deliberate.
The conductor implements its own sweep rather than importing the live-data lane's -- a generic
scheduler depending on the weather/markets lane inverts the layering this whole design exists to
correct -- and duplicated logic drifts unless something notices. This file is that something.
"""
from __future__ import annotations

import time
from unittest import mock

import pytest

from core.conductor.node import ConductorNode, NodeLifecycle, NodeOutcome
from core.conductor.receipts import concurrency_report, plan_receipt
from core.conductor.registry import NodeContext
from core.conductor.scheduler import run_conductor_plan, run_conductor_plan_sequential
from core.weather_result_contract import WeatherResult
from tests.conductor_product import compose_product


def _outcome(node_id: str, start: float | None, end: float | None) -> NodeOutcome:
    return NodeOutcome(
        node=ConductorNode(node_id=node_id, operation="calculation", request_text="x"),
        state=NodeLifecycle.SUCCEEDED,
        result={},
        started_at=start,
        completed_at=end,
    )


# --------------------------------------------------------------------------------------
# The sweep as arithmetic
# --------------------------------------------------------------------------------------


def test_two_overlapping_intervals_prove_concurrency() -> None:
    report = concurrency_report([_outcome("a", 0.0, 2.0), _outcome("b", 1.0, 3.0)])
    assert report.max_concurrent == 2
    assert report.overlapping_pairs == (("a", "b"),)
    assert report.proves_concurrency


def test_strictly_sequential_intervals_prove_nothing() -> None:
    report = concurrency_report([_outcome("a", 0.0, 1.0), _outcome("b", 2.0, 3.0)])
    assert report.max_concurrent == 1
    assert report.overlapping_pairs == ()
    assert report.proves_concurrency is False


def test_a_handoff_at_an_identical_instant_is_not_overlap() -> None:
    """Half-open intervals, on purpose. Counting a touch as overlap would let a strictly serial run
    -- where each node starts exactly as the last ends -- claim concurrency it never had."""
    report = concurrency_report([_outcome("a", 0.0, 1.0), _outcome("b", 1.0, 2.0)])
    assert report.max_concurrent == 1
    assert report.overlapping_pairs == ()
    assert report.proves_concurrency is False


def test_many_nodes_do_not_substitute_for_overlap() -> None:
    """Seven sequential nodes still have seven timed nodes. Count is not evidence."""
    outcomes = [_outcome(f"n{i}", float(i), float(i) + 0.5) for i in range(7)]
    report = concurrency_report(outcomes)
    assert report.timed_node_count == 7
    assert report.proves_concurrency is False


def test_nodes_that_never_ran_are_excluded_rather_than_counted_as_instantaneous() -> None:
    """An unresolved or dependency-failed node has no interval. Treating it as a zero-length one
    would let a plan full of skipped work report a healthy timed count."""
    report = concurrency_report(
        [_outcome("a", 0.0, 2.0), _outcome("b", 1.0, 3.0), _outcome("skipped", None, None)]
    )
    assert report.timed_node_count == 2
    assert report.proves_concurrency


def test_a_single_node_cannot_overlap_itself() -> None:
    report = concurrency_report([_outcome("only", 0.0, 5.0)])
    assert report.max_concurrent == 1
    assert report.proves_concurrency is False


def test_no_timed_nodes_at_all_is_reported_honestly() -> None:
    report = concurrency_report([_outcome("a", None, None)])
    assert report.timed_node_count == 0
    assert report.max_concurrent == 0
    assert report.proves_concurrency is False


# --------------------------------------------------------------------------------------
# Agreement with the lane this replaces
# --------------------------------------------------------------------------------------


def _as_live_data_outcomes(intervals):
    from core.agent_runtime.live_data_plan import LiveDataSubtask, SubtaskLifecycle, SubtaskOutcome

    return [
        SubtaskOutcome(
            subtask=LiveDataSubtask(
                subtask_id=f"n{i}",
                entity=f"n{i}",
                operation="weather_lookup",
                arguments={},
                required_result_fields=(),
                tool="weather",
                tool_intent="web.research",
            ),
            state=SubtaskLifecycle.SUCCEEDED,
            result={},
            started_at=start,
            completed_at=end,
        )
        for i, (start, end) in enumerate(intervals)
    ]


@pytest.mark.parametrize(
    "intervals",
    [
        [(0.0, 2.0), (1.0, 3.0)],
        [(0.0, 1.0), (2.0, 3.0)],
        [(0.0, 1.0), (1.0, 2.0)],
        [(0.0, 5.0), (1.0, 2.0), (1.5, 4.0)],
        [(0.0, 1.0)],
        [(0.0, 3.0), (0.0, 3.0), (0.0, 3.0)],
        [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)],
    ],
)
def test_the_conductor_sweep_agrees_with_the_live_data_sweep_on_the_verdict(intervals) -> None:
    """Two implementations, one contract. Drift here is a silent divergence in what the product
    claims about its own concurrency, so it is asserted rather than assumed.

    Agreement is asserted on the VERDICT and on the overlapping pairs -- the two things anything
    downstream consumes. `max_concurrent` is deliberately excluded; see the test below for why.
    """
    from core.agent_runtime.live_data_runner import concurrency_report as live_data_report

    mine = [_outcome(f"n{i}", start, end) for i, (start, end) in enumerate(intervals)]
    ours, live = concurrency_report(mine), live_data_report(_as_live_data_outcomes(intervals))

    assert set(ours.overlapping_pairs) == set(live.overlapping_pairs)
    assert ours.proves_concurrency == live.proves_concurrency
    assert ours.timed_node_count == live.timed_subtask_count


def test_the_live_data_sweep_counts_a_handoff_as_overlap_and_the_conductor_does_not() -> None:
    """A real inconsistency in the existing lane, pinned rather than inherited.

    `live_data_runner.concurrency_report` sorts a start BEFORE an end at an identical instant
    (`key=lambda item: (item[0], -item[1])`, commented "a start at the same instant as an end
    counts as overlap"), while the `SubtaskOutcome.overlaps()` it uses for the pair list is
    half-open. So a strict handoff -- node B starting exactly as node A finishes -- reports
    `max_concurrent == 2` with an EMPTY pair list: the same report disagreeing with itself.

    It is latent today, because `proves_concurrency` requires both halves and the pair list is
    correct, so no false concurrency claim can currently escape. It is not latent for anything that
    reads `max_concurrent` on its own, which is exactly what a UI badge or a receipt column would
    do. The conductor sorts ends first and stays self-consistent.

    This test exists so the divergence is a recorded decision rather than a surprise, and so it
    turns red if either side changes.
    """
    from core.agent_runtime.live_data_runner import concurrency_report as live_data_report

    handoff = [(0.0, 1.0), (1.0, 2.0)]
    ours = concurrency_report([_outcome(f"n{i}", s, e) for i, (s, e) in enumerate(handoff)])
    live = live_data_report(_as_live_data_outcomes(handoff))

    assert ours.max_concurrent == 1
    assert live.max_concurrent == 2
    # Both agree nothing actually overlapped, which is why neither claims concurrency.
    assert ours.overlapping_pairs == () == live.overlapping_pairs
    assert ours.proves_concurrency is False and live.proves_concurrency is False


# --------------------------------------------------------------------------------------
# Through the real scheduler
# --------------------------------------------------------------------------------------

_DELAY_S = 0.3


def _slow_weather(location, **_kwargs):
    time.sleep(_DELAY_S)
    return WeatherResult(
        location=location,
        place_label=location.title(),
        condition="Sunny",
        temperature_c=20.0,
        feels_like_c=20.0,
        humidity_pct=50.0,
        wind_kmph=5.0,
        observed_at="12:00",
        source_label="wttr.in",
        source_url=f"https://wttr.in/{location}",
    )


def _three_city_plan():
    import json

    from core.conductor.planner import plan_conductor_turn

    text = (
        "What is 137 x 29? Also get the current weather for Kaunas and Tallinn and Warsaw."
    )
    reply = json.dumps(
        [
            {"request": "What is 137 x 29?", "operation": "calculation", "depends_on": []},
            {
                "request": "get the current weather for Kaunas and Tallinn and Warsaw",
                "operation": "weather_lookup",
                "depends_on": [],
            },
        ]
    )
    plan = plan_conductor_turn(text, ask_model=lambda _s, _p: reply, plan_id="t")
    assert plan is not None
    return plan


def test_three_independent_fetches_finish_in_about_one_fetch_not_three() -> None:
    plan = _three_city_plan()
    with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_slow_weather):
        started = time.monotonic()
        outcomes = run_conductor_plan(plan, context=NodeContext(timeout_s=5.0))
        elapsed = time.monotonic() - started

    report = concurrency_report(outcomes)
    assert report.proves_concurrency
    assert report.max_concurrent >= 3
    # Sequential would be ~0.9s. Generous margin: this asserts the shape, and the interval sweep
    # above is what actually proves overlap.
    assert elapsed < _DELAY_S * 2.5, f"took {elapsed:.2f}s"


def test_the_sequential_twin_runs_the_same_plan_and_proves_no_overlap() -> None:
    """The sabotage control, kept in the tree so the proof above cannot be a tautology."""
    plan = _three_city_plan()
    with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_slow_weather):
        outcomes = run_conductor_plan_sequential(plan, context=NodeContext(timeout_s=5.0))

    report = concurrency_report(outcomes)
    assert report.max_concurrent == 1
    assert report.overlapping_pairs == ()
    assert report.proves_concurrency is False
    # Same answers, only the evidence differs -- otherwise the control would be proving that the
    # sequential runner is broken rather than that overlap is what the fast run had.
    assert all(o.succeeded for o in outcomes)
    assert compose_product(plan, outcomes).complete


def test_the_plan_receipt_records_the_proof_and_one_receipt_per_node() -> None:
    plan = _three_city_plan()
    with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_slow_weather):
        outcomes = run_conductor_plan(plan, context=NodeContext(timeout_s=5.0))

    receipt = plan_receipt(outcomes, plan_id=plan.plan_id, parallel_preferred=plan.parallel_preferred)
    assert receipt["schema"] == "conductor_plan_receipt_v1"
    assert receipt["node_count"] == len(plan.nodes)
    assert receipt["succeeded_count"] == len(plan.nodes)
    assert receipt["concurrency"]["proves_concurrency"] is True
    assert {entry["node_id"] for entry in receipt["nodes"]} == {n.node_id for n in plan.nodes}
    for entry in receipt["nodes"]:
        assert entry["schema"] == "conductor_node_receipt_v1"
        assert entry["started_at_iso"] and entry["completed_at_iso"]
        assert entry["duration_s"] is not None


def test_a_receipt_carries_no_field_a_model_could_have_written() -> None:
    """The whole point of the receipt: every value is observed by the runtime at the seam.

    If a field ever appears here that a model supplies, "I ran these in parallel" becomes evidence
    again and the proof is worthless.
    """
    plan = _three_city_plan()
    with mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_slow_weather):
        outcomes = run_conductor_plan(plan, context=NodeContext(timeout_s=5.0))

    receipt = plan_receipt(outcomes, plan_id=plan.plan_id, parallel_preferred=True)
    allowed = {
        "schema", "plan_id", "node_id", "operation", "tool_intent", "depends_on", "state", "ok",
        "failure_reason", "missing_result_fields", "result_fields", "queued_at_iso",
        "started_at_iso", "completed_at_iso", "duration_s",
        # R3 (AUD-20260829-003): failure_code/receipt_id/failure_detail_full/failure_traceback,
        # added so the receipt can carry the FULL exception text and traceback the served answer
        # must never show. All four are assigned by scheduler.py at the failure site -- observed
        # by the runtime, same as everything else in this set -- never by a model.
        "failure_code", "receipt_id", "failure_detail_full", "failure_traceback",
        # Assessed by the operation from executed output, not copied from model JSON.
        "fulfilled", "fulfillment_status",
    }
    for entry in receipt["nodes"]:
        assert set(entry) == allowed, set(entry) - allowed
        assert entry["fulfilled"] is True
        assert entry["fulfillment_status"] == "fulfilled"
