"""The deterministic two-table answer + derived comparisons, from structured outcomes only.

Live-verified once against the real benchmark (not part of this suite): rendered exactly a Markets
table, a Weather table, "Largest absolute 24-hour mover: Binancecoin (-1.29%)", and "Warmest
current city: Warsaw (34 C)" -- Binancecoin's move (1.29%) is smaller in raw percent than gold's
positive move only in sign, but larger in absolute value than gold (0.36%), silver (0.30%), and
bitcoin (0.12%), confirming the comparison is by absolute value, not raw value. These tests cover
the same contract with synthetic outcomes so they are deterministic and offline.
"""

from __future__ import annotations

from core.agent_runtime.live_data_plan import LiveDataSubtask, SubtaskLifecycle, SubtaskOutcome
from core.agent_runtime.live_data_render import largest_absolute_mover, render_live_data_answer, warmest_city


def _market_task(entity: str, asset_key: str) -> LiveDataSubtask:
    return LiveDataSubtask(
        subtask_id=f"p:market:{asset_key}", entity=entity, operation="market_quote",
        arguments={"asset_key": asset_key, "kind": "crypto"},
        required_result_fields=("price", "change_24h_pct", "source", "retrieved_at"),
        tool="market_prices", tool_intent="web.research",
    )


def _weather_task(entity: str, location: str) -> LiveDataSubtask:
    return LiveDataSubtask(
        subtask_id=f"p:weather:{location}", entity=entity, operation="weather_lookup",
        arguments={"location": location}, required_result_fields=("condition", "source", "observed_at"),
        tool="weather", tool_intent="web.research",
    )


def _ok(task: LiveDataSubtask, result: dict) -> SubtaskOutcome:
    return SubtaskOutcome(subtask=task, state=SubtaskLifecycle.SUCCEEDED, result=result)


def _failed(task: LiveDataSubtask, reason: str) -> SubtaskOutcome:
    return SubtaskOutcome(subtask=task, state=SubtaskLifecycle.FAILED, failure_reason=reason)


GOLD = _market_task("Gold", "gold")
SILVER = _market_task("Silver", "silver")
BITCOIN = _market_task("Bitcoin", "bitcoin")
BNB = _market_task("Binancecoin", "binancecoin")
KAUNAS = _weather_task("Kaunas", "kaunas")
TALLINN = _weather_task("Tallinn", "tallinn")
WARSAW = _weather_task("Warsaw", "warsaw")


def _full_success_outcomes() -> list[SubtaskOutcome]:
    return [
        _ok(GOLD, {"price": 4320.70, "currency": "USD", "change_24h_pct": 0.36, "unit_label": "per troy ounce", "source": "Yahoo Finance", "source_url": "https://yf/gold", "retrieved_at": "2026-08-06 07:30 UTC"}),
        _ok(SILVER, {"price": 62.10, "currency": "USD", "change_24h_pct": -0.30, "unit_label": "per troy ounce", "source": "Yahoo Finance", "source_url": "https://yf/silver", "retrieved_at": "2026-08-06 07:30 UTC"}),
        _ok(BITCOIN, {"price": 64169.0, "currency": "USD", "change_24h_pct": 0.12, "source": "CoinGecko", "source_url": "https://cg/btc", "retrieved_at": "2026-08-06 13:12 UTC"}),
        _ok(BNB, {"price": 591.56, "currency": "USD", "change_24h_pct": -1.29, "source": "CoinGecko", "source_url": "https://cg/bnb", "retrieved_at": "2026-08-06 13:12 UTC"}),
        _ok(KAUNAS, {"condition": "Sunny", "temperature_c": 28.0, "high_c": 30.0, "low_c": 19.0, "source": "wttr.in", "source_url": "https://wttr.in/kaunas", "observed_at": "01:05 PM"}),
        _ok(TALLINN, {"condition": "Partly Cloudy", "temperature_c": 22.0, "high_c": 24.0, "low_c": 15.0, "source": "wttr.in", "source_url": "https://wttr.in/tallinn", "observed_at": "11:56 AM"}),
        _ok(WARSAW, {"condition": "Sunny", "temperature_c": 34.0, "high_c": 35.0, "low_c": 20.0, "source": "wttr.in", "source_url": "https://wttr.in/warsaw", "observed_at": "12:45 PM"}),
    ]


def test_renders_exactly_two_tables_then_two_comparison_lines() -> None:
    rendered = render_live_data_answer(None, _full_success_outcomes())
    assert rendered.count("**Markets**") == 1
    assert rendered.count("**Weather**") == 1
    assert rendered.index("**Markets**") < rendered.index("**Weather**")
    assert "Largest absolute 24-hour mover:" in rendered
    assert "Warmest current city:" in rendered
    assert rendered.index("**Weather**") < rendered.index("Largest absolute 24-hour mover:")


def test_each_market_row_carries_source_and_retrieval_timestamp() -> None:
    rendered = render_live_data_answer(None, _full_success_outcomes())
    markets_block = rendered.split("**Weather**")[0]
    assert "Retrieved At" in markets_block
    assert "2026-08-06 07:30 UTC" in markets_block  # gold/silver
    assert "2026-08-06 13:12 UTC" in markets_block  # bitcoin/bnb


def test_each_weather_row_carries_source_and_observed_timestamp() -> None:
    rendered = render_live_data_answer(None, _full_success_outcomes())
    weather_block = rendered.split("**Weather**")[1]
    assert "Source Timestamp" in weather_block
    assert "01:05 PM" in weather_block
    assert "11:56 AM" in weather_block
    assert "12:45 PM" in weather_block


def test_each_weather_row_carries_todays_high_and_low() -> None:
    rendered = render_live_data_answer(None, _full_success_outcomes())
    weather_block = rendered.split("**Weather**")[1]
    assert "Today's High (C)" in weather_block
    assert "Today's Low (C)" in weather_block
    assert "30" in weather_block and "19" in weather_block  # Kaunas high/low
    assert "35" in weather_block and "20" in weather_block  # Warsaw high/low


def test_a_missing_high_low_marks_only_those_fields_unavailable() -> None:
    """A source that returns a current reading but no daily forecast still reports condition and
    temperature -- only High/Low go unavailable, never the whole row."""
    outcome = _ok(KAUNAS, {"condition": "Sunny", "temperature_c": 28.0, "source": "wttr.in", "source_url": "https://wttr.in/kaunas", "observed_at": "01:05 PM"})
    rendered = render_live_data_answer(None, [outcome, _ok(TALLINN, {"condition": "Cloudy", "temperature_c": 15.0, "source": "wttr.in", "source_url": "https://x", "observed_at": "-"})])
    assert "Sunny" in rendered
    assert "28" in rendered
    assert "unavailable" in rendered


def test_largest_mover_is_by_absolute_value_not_raw_signed_value() -> None:
    """Binancecoin's -1.29% is the largest move in ABSOLUTE terms even though gold's +0.36% is a
    larger raw (signed) number in one naive ordering -- proves the comparison uses abs()."""
    entity, change = largest_absolute_mover(_full_success_outcomes())
    assert entity == "Binancecoin"
    assert change == -1.29


def test_warmest_city_picks_the_real_maximum() -> None:
    entity, temp = warmest_city(_full_success_outcomes())
    assert entity == "Warsaw"
    assert temp == 34.0


def test_a_missing_market_result_is_marked_unavailable_not_dropped() -> None:
    outcomes = _full_success_outcomes()
    outcomes[1] = _failed(SILVER, "yahoo finance timeout")  # silver fails
    rendered = render_live_data_answer(None, outcomes)
    assert "Silver" in rendered
    assert "unavailable" in rendered.lower()
    assert "yahoo finance timeout" in rendered.lower()
    # Gold, bitcoin, bnb are untouched.
    assert "4,320.70" in rendered
    assert "591.56" in rendered


def test_a_missing_weather_result_excludes_it_from_the_warmest_comparison_safely() -> None:
    outcomes = _full_success_outcomes()
    outcomes[6] = _failed(WARSAW, "wttr.in timeout")  # warsaw (the actual warmest) fails
    entity, temp = warmest_city(outcomes)
    # Warsaw was the real maximum (34C) -- with it missing, the comparison must fall back to the
    # next real value (Kaunas, 28C), never crash and never silently substitute 0 or None.
    assert entity == "Kaunas"
    assert temp == 28.0
    rendered = render_live_data_answer(None, outcomes)
    assert "Warmest current city: Kaunas" in rendered


def test_every_asset_and_every_city_missing_reports_unavailable_not_a_crash() -> None:
    all_failed = [
        _failed(GOLD, "x"), _failed(SILVER, "x"), _failed(BITCOIN, "x"), _failed(BNB, "x"),
        _failed(KAUNAS, "x"), _failed(TALLINN, "x"), _failed(WARSAW, "x"),
    ]
    assert largest_absolute_mover(all_failed) is None
    assert warmest_city(all_failed) is None
    rendered = render_live_data_answer(None, all_failed)
    assert "Largest absolute 24-hour mover: unavailable" in rendered
    assert "Warmest current city: unavailable" in rendered


def test_sabotage_comparing_raw_value_instead_of_absolute_value() -> None:
    """Proves the abs()-based selection is load-bearing: a naive max-by-raw-value would pick gold
    (+0.36, the largest signed number) instead of the real largest move, binancecoin (-1.29)."""
    outcomes = _full_success_outcomes()
    changes = [
        (o.subtask.entity, o.result["change_24h_pct"])
        for o in outcomes if o.subtask.operation == "market_quote"
    ]
    naive_pick = max(changes, key=lambda item: item[1])  # sabotage: no abs()
    real_pick = largest_absolute_mover(outcomes)
    assert naive_pick[0] == "Gold"
    assert real_pick[0] == "Binancecoin"
    assert naive_pick[0] != real_pick[0]


def test_market_only_plan_omits_the_weather_table_and_warmest_line() -> None:
    outcomes = [o for o in _full_success_outcomes() if o.subtask.operation == "market_quote"]
    rendered = render_live_data_answer(None, outcomes)
    assert "**Markets**" in rendered
    assert "**Weather**" not in rendered
    assert "Warmest current city" not in rendered
    assert "Largest absolute 24-hour mover" in rendered


def test_a_single_city_renders_as_a_sentence_not_a_table_with_no_trivial_comparison() -> None:
    """Found live: "Weather in Vilnius, Lithuania." rendered a one-row table plus "Warmest current
    city: Vilnius (29 C)" -- technically correct but forcing the multi-city table shape onto a
    request that named exactly one place. Output shape must follow the request."""
    rendered = render_live_data_answer(None, [_ok(WARSAW, {"condition": "Sunny", "temperature_c": 34.0, "source": "wttr.in", "source_url": "https://wttr.in/warsaw", "observed_at": "12:45 PM"})])
    assert "|" not in rendered  # no table
    assert "**Weather**" not in rendered
    assert "Warmest current city" not in rendered  # nothing to compare against itself
    assert "Warsaw" in rendered and "Sunny" in rendered and "34" in rendered


def test_a_single_asset_renders_as_a_sentence_not_a_table_with_no_trivial_comparison() -> None:
    rendered = render_live_data_answer(None, [_ok(BITCOIN, {"price": 64169.0, "currency": "USD", "change_24h_pct": 0.12, "source": "CoinGecko", "source_url": "https://cg/btc", "retrieved_at": "2026-08-06 13:12 UTC"})])
    assert "|" not in rendered
    assert "**Markets**" not in rendered
    assert "Largest absolute 24-hour mover" not in rendered
    assert "Bitcoin" in rendered and "64,169.00" in rendered


def test_one_single_market_and_one_multi_weather_shapes_independently() -> None:
    """Each side's shape is decided independently -- one asset (sentence) alongside three cities
    (table + comparison) in the same answer."""
    outcomes = [
        _ok(BITCOIN, {"price": 64169.0, "currency": "USD", "change_24h_pct": 0.12, "source": "CoinGecko", "source_url": "https://cg/btc"}),
        _ok(KAUNAS, {"condition": "Sunny", "temperature_c": 28.0, "source": "wttr.in", "source_url": "https://wttr.in/kaunas"}),
        _ok(TALLINN, {"condition": "Partly Cloudy", "temperature_c": 22.0, "source": "wttr.in", "source_url": "https://wttr.in/tallinn"}),
        _ok(WARSAW, {"condition": "Sunny", "temperature_c": 34.0, "source": "wttr.in", "source_url": "https://wttr.in/warsaw"}),
    ]
    rendered = render_live_data_answer(None, outcomes)
    assert "**Markets**" not in rendered  # single asset -> sentence
    assert "Bitcoin" in rendered
    assert "**Weather**" in rendered  # three cities -> table
    assert "Warmest current city: Warsaw" in rendered
    assert "Largest absolute 24-hour mover" not in rendered  # only one asset, nothing to compare


def test_an_unavailable_single_result_still_reports_honestly() -> None:
    from core.agent_runtime.live_data_plan import SubtaskLifecycle, SubtaskOutcome

    failed = SubtaskOutcome(subtask=WARSAW, state=SubtaskLifecycle.FAILED, failure_reason="wttr.in timeout")
    rendered = render_live_data_answer(None, [failed])
    assert "unavailable" in rendered.lower()
    assert "wttr.in timeout" in rendered
