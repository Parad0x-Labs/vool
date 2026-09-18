"""Real timing telemetry proves concurrency -- not "completed in 0.43s".

Live-verified once against the real benchmark (not part of this suite): all 7 subtasks' RUNNING
intervals overlapped, max_concurrent=7, 21/21 possible pairs overlapping. These tests reproduce the
same proof deterministically offline, using `time.sleep` in fake fetchers so overlap is measured in
whole seconds rather than depending on real network timing.
"""

from __future__ import annotations

import time
from unittest import mock

from core.agent_runtime.live_data_plan import build_live_data_plan
from core.agent_runtime.live_data_runner import (
    concurrency_report,
    run_live_data_plan,
    run_live_data_plan_sequential,
)
from core.live_quote_contract import LiveQuoteResult
from core.weather_result_contract import WeatherResult

BENCHMARK = (
    "Give me the current price and 24-hour change for gold, silver, Bitcoin, and BNB, "
    "and the weather in Kaunas, Tallinn, and Warsaw."
)
_SLEEP_S = 0.2  # each fake fetch takes this long -- long enough that sequential-vs-concurrent
# total time differs by whole seconds, not floating-point noise.


def _slow_crypto(coin_ids, **_kwargs):
    time.sleep(_SLEEP_S)
    table = {"bitcoin": LiveQuoteResult(asset_key="bitcoin", asset_name="Bitcoin", symbol="BTC", value=64000.0, currency="USD", as_of="", source_label="t", source_url="https://x", kind="crypto", change_percent=0.1),
             "binancecoin": LiveQuoteResult(asset_key="binancecoin", asset_name="Binancecoin", symbol="BNB", value=591.0, currency="USD", as_of="", source_label="t", source_url="https://x", kind="crypto", change_percent=-1.2)}
    return [table[c] for c in coin_ids if c in table]


def _slow_market(_query, targets, **_kwargs):
    time.sleep(_SLEEP_S)
    table = {"gold": LiveQuoteResult(asset_key="gold", asset_name="Gold", symbol="GC", value=4320.0, currency="USD", as_of="", source_label="t", source_url="https://x", kind="market", change_percent=0.4),
             "silver": LiveQuoteResult(asset_key="silver", asset_name="Silver", symbol="SI", value=62.0, currency="USD", as_of="", source_label="t", source_url="https://x", kind="market", change_percent=-0.3)}
    return [table[t.asset_key] for t in targets if t.asset_key in table]


def _slow_weather(location, **_kwargs):
    time.sleep(_SLEEP_S)
    table = {"kaunas": 28.0, "tallinn": 22.0, "warsaw": 34.0}
    if location not in table:
        return None
    return WeatherResult(location=location, place_label=location.title(), condition="Sunny", temperature_c=table[location], feels_like_c=table[location], humidity_pct=50.0, wind_kmph=10.0, observed_at="12:00 PM", source_label="wttr.in", source_url=f"https://wttr.in/{location}")


def _mocks():
    return (
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_slow_crypto),
        mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=_slow_market),
        mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_slow_weather),
    )


def test_the_benchmark_produces_overlapping_running_intervals() -> None:
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    with _mocks()[0], _mocks()[1], _mocks()[2]:
        outcomes = run_live_data_plan(plan, approval_decisions={}, timeout_s=5)
    report = concurrency_report(outcomes)
    assert report.timed_subtask_count == 7
    assert report.max_concurrent >= 2
    assert len(report.overlapping_pairs) >= 1
    assert report.proves_concurrency is True


def test_every_subtask_has_queued_started_completed_and_duration() -> None:
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    with _mocks()[0], _mocks()[1], _mocks()[2]:
        outcomes = run_live_data_plan(plan, approval_decisions={}, timeout_s=5)
    for outcome in outcomes:
        assert outcome.queued_at is not None
        assert outcome.started_at is not None
        assert outcome.completed_at is not None
        assert outcome.duration_s is not None
        assert outcome.duration_s >= _SLEEP_S * 0.8  # allow scheduling jitter, not zero-duration
        assert outcome.queued_at_iso and outcome.started_at_iso and outcome.completed_at_iso


def test_total_wall_clock_is_close_to_one_fetch_not_seven() -> None:
    """The actual concurrency proof in wall-clock terms: 7 subtasks at 0.2s each would take 1.4s
    sequential; run concurrently they must finish in well under that."""
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    t0 = time.monotonic()
    with _mocks()[0], _mocks()[1], _mocks()[2]:
        run_live_data_plan(plan, approval_decisions={}, timeout_s=5)
    elapsed = time.monotonic() - t0
    assert elapsed < _SLEEP_S * 3  # generous margin; sequential would be ~1.4s, concurrent ~0.2-0.4s


def test_sabotage_sequential_execution_fails_the_concurrency_assertion() -> None:
    """Proves the overlap proof is load-bearing, not a tautology: running the SAME plan through
    the sequential executor (built only for this sabotage) produces zero overlapping pairs and
    max_concurrent == 1, and the concurrency assertion genuinely fails."""
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    with _mocks()[0], _mocks()[1], _mocks()[2]:
        outcomes = run_live_data_plan_sequential(plan, approval_decisions={}, timeout_s=5)
    report = concurrency_report(outcomes)
    assert report.max_concurrent == 1
    assert report.overlapping_pairs == ()
    assert report.proves_concurrency is False


def test_a_waiting_approval_subtask_is_excluded_from_the_concurrency_math() -> None:
    """A subtask that never ran (WAITING_APPROVAL) must not be counted as zero-duration overlap --
    it has no timing at all and must be excluded from timed_subtask_count."""
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")

    class _Denied:
        allowed = False
        effect = "require_approval"
        reason = "needs approval"

    gold_id = next(t.subtask_id for t in plan.subtasks if t.entity == "Gold")
    with _mocks()[0], _mocks()[1], _mocks()[2]:
        outcomes = run_live_data_plan(plan, approval_decisions={gold_id: _Denied()}, timeout_s=5)
    report = concurrency_report(outcomes)
    assert report.timed_subtask_count == 6  # gold excluded, the other 6 timed
    gold_outcome = next(o for o in outcomes if o.subtask.entity == "Gold")
    assert gold_outcome.started_at is None
    assert gold_outcome.completed_at is None
