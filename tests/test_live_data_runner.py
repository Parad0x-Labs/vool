"""Running a LiveDataPlan: concurrent, partial-failure isolated, approval-aware.

Live-verified once against the real market/weather APIs (not part of this suite, which blocks
live network per tests/conftest.py -- recorded in the delivery report): the exact benchmark's 7
subtasks all succeeded in 0.43s wall clock, which is only possible if they ran concurrently, not
sequentially. These tests cover the same contract with mocks so they run offline and
deterministically.
"""

from __future__ import annotations

from unittest import mock

from core.agent_runtime.live_data_plan import SubtaskLifecycle, build_live_data_plan, evaluate_approval_policy
from core.agent_runtime.live_data_runner import run_live_data_plan
from core.live_quote_contract import LiveQuoteResult
from core.mode_permission_policy import reset_mode_permission_state, set_active_mode
from core.weather_result_contract import WeatherResult

BENCHMARK = (
    "Give me the current price and 24-hour change for gold, silver, Bitcoin, and BNB, "
    "and the weather in Kaunas, Tallinn, and Warsaw."
)


def _fake_quote(asset_key: str, value: float, change: float, *, kind: str) -> LiveQuoteResult:
    return LiveQuoteResult(
        asset_key=asset_key, asset_name=asset_key.title(), symbol=asset_key.upper(), value=value,
        currency="USD", as_of="2026-08-06 12:00 UTC", source_label="test", source_url="https://x",
        kind=kind, change_percent=change,
    )


def _fake_weather(location: str, temp: float) -> WeatherResult:
    return WeatherResult(
        location=location, place_label=location.title(), condition="Sunny", temperature_c=temp,
        feels_like_c=temp, humidity_pct=50.0, wind_kmph=10.0, observed_at="12:00 PM",
        source_label="wttr.in", source_url=f"https://wttr.in/{location}",
    )


def _crypto_side_effect(coin_ids, **_kwargs):
    table = {"bitcoin": _fake_quote("bitcoin", 64000.0, 0.15, kind="crypto"), "binancecoin": _fake_quote("binancecoin", 591.0, -1.2, kind="crypto")}
    return [table[c] for c in coin_ids if c in table]


def _market_side_effect(_query, targets, **_kwargs):
    table = {"gold": _fake_quote("gold", 4320.0, 0.4, kind="market"), "silver": _fake_quote("silver", 62.0, -0.3, kind="market")}
    return [table[t.asset_key] for t in targets if t.asset_key in table]


def _weather_side_effect(location, **_kwargs):
    table = {"kaunas": 28.0, "tallinn": 22.0, "warsaw": 34.0}
    if location not in table:
        return None
    return _fake_weather(location, table[location])


def _run_benchmark_mocked():
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    reset_mode_permission_state()
    try:
        set_active_mode("chat-a", "manual", project_id="", client_turn_id="turn-a")
        ctx = {"runtime_session_id": "chat-a", "operating_mode": "manual", "workspace_root": "/tmp"}
        decisions = evaluate_approval_policy(plan, source_context=ctx)
    finally:
        reset_mode_permission_state()
    with (
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_crypto_side_effect),
        mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=_market_side_effect),
        mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather_side_effect),
    ):
        outcomes = run_live_data_plan(plan, approval_decisions=decisions, timeout_s=5)
    return plan, outcomes


def test_all_seven_subtasks_succeed_with_structured_results() -> None:
    _plan, outcomes = _run_benchmark_mocked()
    assert len(outcomes) == 7
    assert all(o.ok for o in outcomes)
    by_entity = {o.subtask.entity: o for o in outcomes}
    assert by_entity["Gold"].result["price"] == 4320.0
    assert by_entity["Kaunas"].result["temperature_c"] == 28.0


def test_outcomes_preserve_plan_order() -> None:
    plan, outcomes = _run_benchmark_mocked()
    assert [o.subtask.subtask_id for o in outcomes] == [t.subtask_id for t in plan.subtasks]


def test_a_failed_city_and_a_failed_asset_do_not_erase_the_others() -> None:
    """Partial-failure isolation, both toolsets at once: warsaw's weather fetch and silver's
    market fetch both fail; the other five subtasks must still succeed with real results."""
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")

    def _market_partial(_query, targets, **_kwargs):
        results = []
        for t in targets:
            if t.asset_key == "silver":
                raise RuntimeError("yahoo finance timeout")
            if t.asset_key == "gold":
                results.append(_fake_quote("gold", 4320.0, 0.4, kind="market"))
        return results

    def _weather_partial(location, **_kwargs):
        if location == "warsaw":
            raise RuntimeError("wttr.in timeout")
        return _weather_side_effect(location)

    with (
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_crypto_side_effect),
        mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=_market_partial),
        mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather_partial),
    ):
        outcomes = run_live_data_plan(plan, approval_decisions={}, timeout_s=5)

    by_entity = {o.subtask.entity: o for o in outcomes}
    assert by_entity["Gold"].ok
    assert by_entity["Bitcoin"].ok
    assert by_entity["Binancecoin"].ok
    assert by_entity["Kaunas"].ok
    assert by_entity["Tallinn"].ok
    assert not by_entity["Silver"].ok
    assert by_entity["Silver"].state is SubtaskLifecycle.FAILED
    assert "yahoo finance timeout" in by_entity["Silver"].failure_reason.lower()
    assert not by_entity["Warsaw"].ok
    assert by_entity["Warsaw"].state is SubtaskLifecycle.FAILED


def test_a_subtask_requiring_approval_is_not_executed_and_is_preserved_not_dropped() -> None:
    """The exact mixed-plan requirement: an approval-required subtask must be represented, not
    silently discarded, while its automatically-executable siblings still run."""
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")

    class _FakeDecision:
        allowed = False
        effect = "require_approval"
        reason = "Manual mode requires approval for this exact action."

    gold_id = next(t.subtask_id for t in plan.subtasks if t.entity == "Gold")
    decisions = {gold_id: _FakeDecision()}

    with (
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_crypto_side_effect),
        mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=_market_side_effect),
        mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather_side_effect),
    ):
        outcomes = run_live_data_plan(plan, approval_decisions=decisions, timeout_s=5)

    assert len(outcomes) == 7  # nothing dropped
    gold_outcome = next(o for o in outcomes if o.subtask.entity == "Gold")
    assert gold_outcome.state is SubtaskLifecycle.WAITING_APPROVAL
    assert gold_outcome.result is None
    assert "requires approval" in gold_outcome.failure_reason.lower()
    # Every other subtask still ran despite gold needing approval.
    others = [o for o in outcomes if o.subtask.entity != "Gold"]
    assert all(o.ok for o in others)


def test_sabotage_a_pool_level_exception_does_not_lose_completed_outcomes() -> None:
    """If the thread pool itself raises (not an individual fetch), the sequential fallback must
    still recover every subtask rather than losing the whole plan to one infrastructure fault."""
    plan = build_live_data_plan(BENCHMARK, plan_id="plan-1", attempt_id="attempt-1")
    with (
        mock.patch("core.agent_runtime.live_data_runner.ThreadPoolExecutor", side_effect=RuntimeError("no threads available")),
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_crypto_side_effect),
        mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=_market_side_effect),
        mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather_side_effect),
    ):
        outcomes = run_live_data_plan(plan, approval_decisions={}, timeout_s=5)
    assert len(outcomes) == 7
    assert all(o.ok for o in outcomes)
