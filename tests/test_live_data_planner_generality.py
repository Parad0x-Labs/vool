"""The planner is general, not overfit to the one benchmark it was built against.

Six fresh cases, run live through the isolated daemon during development (not part of this
offline suite, which never touches the network) -- every one behaved correctly after the fixes in
this same commit range (89c2063c, 290c676e, d3a9d2ca):

    A. "Current price and daily change for Ethereum, Solana, oil, and copper, plus weather in
       Riga, Helsinki, and Prague." -> 2 markets (ethereum, solana) + 3 weather, correct
       comparisons.
    B. "Weather only for Berlin and Copenhagen." -> weather table only, no Markets block, no
       "largest mover" line.
    C. "Market data only for Bitcoin and gold." -> markets table only, no Weather block, no
       "warmest city" line.
    D. "Weather in Vilnius, Lithuania." -> ONE location, sentence (not table), no trivial
       comparison.
    E. "Weather in Kaunas and Atlantisxyzabc123." -> Kaunas succeeds, the invalid location becomes
       an honest "unavailable" row with its real error, Kaunas is uncorrupted.
    F. "Price of Bitcoin and Qwertycoin999xyz." -> Bitcoin succeeds; the unrecognized asset never
       corrupts Bitcoin's own result.

Checkpoint 6.5 closed the gap cases A and F originally disclosed as a known limitation: oil,
copper, and Qwertycoin999xyz used to be silently absent from the plan entirely (no subtask, no
"unavailable" row, nothing) because market assets were a closed vocabulary with no fallback the
way place names had one. They now each produce an explicit `unsupported_market_entity` subtask
(see `tests/test_unsupported_market_entities_are_preserved.py`), so cases A and F below assert on
the corrected, cardinality-preserving shape rather than the old silent-omission one.

These tests reproduce the entity-extraction and render-shape assertions offline (no network
needed for planning; network calls are mocked for the render-shape checks).
"""

from __future__ import annotations

from core.agent_runtime.live_data_plan import (
    SubtaskLifecycle,
    SubtaskOutcome,
    build_live_data_plan,
)
from core.agent_runtime.live_data_render import render_live_data_answer

# --- A: mixed novel assets + cities, one recognized/unrecognized split within the same message ---


def test_case_a_recognizes_the_two_supported_crypto_assets_and_three_cities() -> None:
    text = "Current price and daily change for Ethereum, Solana, oil, and copper, plus weather in Riga, Helsinki, and Prague."
    plan = build_live_data_plan(text, plan_id="p", attempt_id="a")
    assert plan is not None
    market = plan.market_subtasks()
    resolved_keys = {t.arguments["asset_key"] for t in market if t.operation == "market_quote"}
    assert {"ethereum", "solana"} <= resolved_keys
    # Checkpoint 6.5: every named asset produces an explicit subtask — resolved
    # OR unsupported, never silently dropped. "oil" is now SERVED (it resolves
    # to the Brent crude benchmark, measured live 2026-08-29: the bare word
    # used to fall out of the plan entirely); copper stays unsupported.
    assert "brent_crude" in resolved_keys
    unsupported_text = {t.arguments["requested_text"] for t in market if t.operation == "unsupported_market_entity"}
    assert {"copper"} <= unsupported_text
    weather_locations = {t.arguments["location"] for t in plan.weather_subtasks()}
    assert weather_locations == {"riga", "helsinki", "prague"}


def test_case_a_no_exact_prompt_dependency_different_wording_same_entities() -> None:
    """The planner must not depend on the exact benchmark's wording -- a differently-phrased,
    differently-ordered message naming the same kind of entities must resolve the same way."""
    text = "What's Ethereum and Solana worth right now? Also weather in Prague, Helsinki, and Riga please."
    plan = build_live_data_plan(text, plan_id="p", attempt_id="a")
    assert plan is not None
    resolved_keys = {t.arguments["asset_key"] for t in plan.market_subtasks() if t.operation == "market_quote"}
    assert resolved_keys >= {"ethereum", "solana"}
    assert {t.arguments["location"] for t in plan.weather_subtasks()} == {"prague", "helsinki", "riga"}


# --- B: weather-only ---


def test_case_b_weather_only_renders_no_markets_block_or_mover_line() -> None:
    outcomes = [
        SubtaskOutcome(
            subtask=build_live_data_plan("weather only for Berlin and Copenhagen", plan_id="p", attempt_id="a").weather_subtasks()[i],
            state=SubtaskLifecycle.SUCCEEDED,
            result={"condition": "Sunny", "temperature_c": 20.0 + i, "source": "wttr.in", "source_url": "https://x"},
        )
        for i in range(2)
    ]
    rendered = render_live_data_answer(None, outcomes)
    assert "**Weather**" in rendered
    assert "**Markets**" not in rendered
    assert "Largest absolute 24-hour mover" not in rendered
    assert "Warmest current city" in rendered


# --- C: market-only ---


def test_case_c_market_only_renders_no_weather_block_or_warmest_line() -> None:
    plan = build_live_data_plan("Market data only for Bitcoin and gold.", plan_id="p", attempt_id="a")
    assert plan is not None
    assert len(plan.market_subtasks()) == 2
    assert len(plan.weather_subtasks()) == 0
    outcomes = [
        SubtaskOutcome(subtask=plan.market_subtasks()[0], state=SubtaskLifecycle.SUCCEEDED, result={"price": 64000.0, "currency": "USD", "change_24h_pct": 0.1, "source": "CoinGecko", "source_url": "https://x"}),
        SubtaskOutcome(subtask=plan.market_subtasks()[1], state=SubtaskLifecycle.SUCCEEDED, result={"price": 4320.0, "currency": "USD", "change_24h_pct": 0.4, "source": "Yahoo Finance", "source_url": "https://x"}),
    ]
    rendered = render_live_data_answer(None, outcomes)
    assert "**Markets**" in rendered
    assert "**Weather**" not in rendered
    assert "Warmest current city" not in rendered
    assert "Largest absolute 24-hour mover" in rendered


# --- D: "City, Country" stays one location ---


def test_case_d_city_comma_country_is_one_location_not_two() -> None:
    plan = build_live_data_plan("Weather in Vilnius, Lithuania.", plan_id="p", attempt_id="a")
    assert plan is not None
    assert len(plan.weather_subtasks()) == 1
    assert plan.weather_subtasks()[0].arguments["location"] == "vilnius, lithuania"


def test_case_d_single_location_renders_as_a_sentence() -> None:
    plan = build_live_data_plan("Weather in Vilnius, Lithuania.", plan_id="p", attempt_id="a")
    outcome = SubtaskOutcome(
        subtask=plan.weather_subtasks()[0], state=SubtaskLifecycle.SUCCEEDED,
        result={"condition": "Patchy rain nearby", "temperature_c": 29.0, "source": "wttr.in", "source_url": "https://x"},
    )
    rendered = render_live_data_answer(None, [outcome])
    assert "|" not in rendered
    assert "Vilnius" in rendered


# --- E: conjunction-separated cities become separate tasks; invalid location -> unavailable ---


def test_case_e_and_separated_cities_are_two_tasks_and_invalid_one_isolates() -> None:
    plan = build_live_data_plan("Weather in Kaunas and Atlantisxyzabc123.", plan_id="p", attempt_id="a")
    assert plan is not None
    assert len(plan.weather_subtasks()) == 2
    locations = {t.arguments["location"] for t in plan.weather_subtasks()}
    assert "kaunas" in locations
    kaunas_task = next(t for t in plan.weather_subtasks() if t.arguments["location"] == "kaunas")
    other_task = next(t for t in plan.weather_subtasks() if t.arguments["location"] != "kaunas")
    outcomes = [
        SubtaskOutcome(subtask=kaunas_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Sunny", "temperature_c": 28.0, "source": "wttr.in", "source_url": "https://x"}),
        SubtaskOutcome(subtask=other_task, state=SubtaskLifecycle.FAILED, failure_reason="HTTPError: HTTP Error 500: Internal Server Error"),
    ]
    rendered = render_live_data_answer(None, outcomes)
    assert "Kaunas" in rendered and "Sunny" in rendered  # uncorrupted
    assert "unavailable" in rendered.lower()
    assert "Warmest current city: Kaunas" in rendered  # comparison excludes the failed one safely


# --- F: one recognized + one unknown asset ---


def test_case_f_unknown_asset_becomes_an_explicit_unsupported_subtask_not_a_silent_drop() -> None:
    """Checkpoint 6.5: the unrecognized asset must produce its own subtask -- an
    `unsupported_market_entity` -- rather than vanish, and must never corrupt Bitcoin's own
    result. Two requested entities now means two subtasks and two rendered rows, not one."""
    plan = build_live_data_plan("Price of Bitcoin and Qwertycoin999xyz.", plan_id="p", attempt_id="a")
    assert plan is not None
    market = plan.market_subtasks()
    assert len(market) == 2  # requested cardinality preserved: resolved + unsupported
    bitcoin_task = next(t for t in market if t.operation == "market_quote")
    unsupported_task = next(t for t in market if t.operation == "unsupported_market_entity")
    assert bitcoin_task.arguments["asset_key"] == "bitcoin"
    assert unsupported_task.arguments["requested_text"] == "qwertycoin999xyz"
    outcomes = [
        SubtaskOutcome(
            subtask=bitcoin_task, state=SubtaskLifecycle.SUCCEEDED,
            result={"price": 64371.0, "currency": "USD", "change_24h_pct": 0.13, "source": "CoinGecko", "source_url": "https://x"},
        ),
        SubtaskOutcome(
            subtask=unsupported_task, state=SubtaskLifecycle.UNSUPPORTED_ENTITY,
            failure_reason="not a recognized market entity",
        ),
    ]
    rendered = render_live_data_answer(None, outcomes)
    assert "Bitcoin" in rendered and "64,371" in rendered
    assert "unavailable" in rendered.lower()  # the unsupported asset is shown, not silently absent


# --- Live 2026-08-29: a typo'd filler word became a phantom asset -------------------------

def test_misspelled_filler_never_becomes_a_phantom_asset() -> None:
    """"price of gold nwo" planned an unsupported entity for the typo of "now", which rendered a
    phantom "Nwo — not a recognized market entity" row and, by making a one-asset turn look like a
    comparison, attached "Largest absolute 24-hour mover" the operator never asked for."""
    plan = build_live_data_plan(
        "what is price of gold nwo and how much 500 euros would buy it?", plan_id="p", attempt_id="a"
    )
    assert plan is not None
    ops = [(s.operation, s.subtask_id.rsplit(":", 1)[-1]) for s in plan.subtasks]
    assert ops == [("market_quote", "gold")], f"phantom entity planned: {ops}"


def test_genuine_unknown_assets_still_surface() -> None:
    """The guard must not silently swallow an asset the operator really named."""
    plan = build_live_data_plan("price of bitcoin and shibainu9000", plan_id="p", attempt_id="a")
    assert plan is not None
    ops = [s.operation for s in plan.subtasks]
    assert ops.count("market_quote") == 1
    assert "unsupported_market_entity" in ops, "an unknown asset must never vanish silently"


def test_one_requested_asset_never_gets_a_comparison_line() -> None:
    """A ranking of one is not an answer — the comparison belongs to turns that asked for it."""
    from core.agent_runtime.live_data_render import render_live_data_answer
    from core.agent_runtime.live_data_runner import SubtaskLifecycle, SubtaskOutcome

    plan = build_live_data_plan("price of gold", plan_id="p", attempt_id="a")
    outcomes = [
        SubtaskOutcome(
            subtask=plan.market_subtasks()[0], state=SubtaskLifecycle.SUCCEEDED,
            result={"price": 4504.1, "currency": "USD", "change_24h_pct": -3.43,
                    "source": "Yahoo Finance", "source_url": "https://x"},
        )
    ]
    rendered = render_live_data_answer(plan, outcomes)
    assert "Largest absolute 24-hour mover" not in rendered
