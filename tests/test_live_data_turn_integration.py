"""The exact benchmark, driven through the real `agent.run_once` -- not the isolated modules.

This is the wiring proof the isolated unit tests (test_live_data_plan.py, test_live_data_runner.py,
test_live_data_render.py) cannot give: that `_maybe_answer_live_data_turn` is actually reached from
`_run_once_inner`, ahead of the general text-splitting planner, and that its result reaches the
caller in the same shape every other fast path uses. Network calls are mocked (the suite blocks
live network per conftest.py); the wiring itself is real, driven through the production method, not
called directly.
"""

from __future__ import annotations

from unittest import mock

import pytest

from core.live_quote_contract import LiveQuoteResult
from core.mode_permission_policy import reset_mode_permission_state
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
    return WeatherResult(
        location=location, place_label=location.title(), condition="Sunny", temperature_c=table[location],
        feels_like_c=table[location], humidity_pct=50.0, wind_kmph=10.0, observed_at="12:00 PM",
        source_label="wttr.in", source_url=f"https://wttr.in/{location}",
    )


@pytest.fixture(autouse=True)
def _clean_mode_state():
    reset_mode_permission_state()
    yield
    reset_mode_permission_state()


def test_the_benchmark_answers_with_two_tables_through_the_real_run_once(make_agent) -> None:
    agent = make_agent()
    with (
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_crypto_side_effect),
        mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=_market_side_effect),
        mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather_side_effect),
    ):
        result = agent.run_once(
            BENCHMARK,
            source_context={"surface": "openclaw", "platform": "openclaw", "operating_mode": "manual"},
        )

    response = str(result.get("response") or "")
    assert "**Markets**" in response
    assert "**Weather**" in response
    assert "Largest absolute 24-hour mover:" in response
    assert "Warmest current city:" in response
    assert "Manual mode requires approval" not in response
    # The original incident's exact corruption must never appear.
    assert "Give me the" not in response
    assert "Powisle" not in response or "warsaw" in response.lower()  # a real wttr.in district is fine; a raw sentence leak is not
    assert result.get("reason") != "planned_multi_request_turn"  # confirms the NEW path answered, not the old text-splitting planner


def test_a_single_asset_request_is_unaffected_and_still_answers(make_agent) -> None:
    """The new hook must not regress an ordinary single-asset request into a two-table dump."""
    agent = make_agent()
    with mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_crypto_side_effect):
        result = agent.run_once(
            "what is the current price of bitcoin",
            source_context={"surface": "openclaw", "platform": "openclaw", "operating_mode": "manual"},
        )
    response = str(result.get("response") or "")
    assert "Bitcoin" in response
    assert "**Weather**" not in response


@pytest.mark.parametrize("wording", [
    BENCHMARK,
    "Show the spot price and daily change for silver and Bitcoin",
    "Give me prices, 24h changes and market caps for Bitcoin",
])
def test_coordinated_quote_fields_share_the_named_assets(wording):
    from core.agent_runtime.answer_coverage import interpret_request
    from core.live_data_plan import build_live_data_plan

    units = interpret_request(wording).requests
    plan = build_live_data_plan(wording, plan_id="fields", attempt_id="fields", canonical_units=units)
    assert plan is not None
    assert not plan.unclaimed_unit_ids
    assert all("price" not in unit.text.lower() or any(
        asset in unit.text.lower() for asset in ("silver", "bitcoin", "gold")
    ) for unit in units)


@pytest.mark.parametrize("wording", [
    "Give me the gold price and the latest news about Iran",
    "Give me the current price of gold and explain its chemical structure",
    "Give me a joke and the price of Bitcoin",
])
def test_quote_fields_never_absorb_an_independent_request(wording):
    from core.agent_runtime.demand_ownership import demand_coverage

    coverage = demand_coverage(wording)
    assert coverage.mixed
    assert coverage.unit_count == 2


def test_sabotage_disabling_the_live_data_hook_reproduces_the_planner_split_path(make_agent) -> None:
    """Proves the hook is load-bearing and correctly placed ahead of the general planner: with it
    disabled, the SAME benchmark falls through to the old text-splitting planner, whose own known
    behavior for this exact request class is documented (and still covered) elsewhere."""
    agent = make_agent()
    with (
        mock.patch.object(agent, "_maybe_answer_live_data_turn", return_value=None),
        mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=_crypto_side_effect),
        mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=_market_side_effect),
        mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=_weather_side_effect),
    ):
        result = agent.run_once(
            BENCHMARK,
            source_context={"surface": "openclaw", "platform": "openclaw", "operating_mode": "manual"},
        )
    response = str(result.get("response") or "")
    # With the new path disabled, the response is NOT the deterministic two-table answer -- proving
    # the two-table format seen in the first test came from the new hook, not from something else.
    assert "**Markets**" not in response or "**Weather**" not in response
