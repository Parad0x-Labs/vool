"""The obligation floor names weather places from the units that ask about weather, not from the whole turn.

Measured 2026-09-07 (multi-intent coverage, sloppy wording): "waht is weather in Rome also convert 100 USD
to RUB pls and how much gold i get" produced a weather obligation for a place called "Much Gold I Get";
in fresh wording the extractor mints three ghost places out of one turn. The floor reads the user's
request on purpose (a planner that drops "and Nairobi" must be caught), so the read must be per demand
unit: a place comes only from a unit that asks about the weather.
"""
from __future__ import annotations

import pytest

from core.conductor.operations import _weather_subjects_named


@pytest.mark.parametrize(
    "wording,places",
    [
        ("what's the weather like in Vilnius, also change 250 EUR to GBP and how much silver does that buy", ["Vilnius"]),
        ("temperature in Kaunas today? and then 40 USD to PLN and tell me how much copper i could get", ["Kaunas"]),
        ("forecast for Riga and Tallinn please, plus convert 10 GBP to NOK", ["Riga", "Tallinn"]),
    ],
)
def test_places_come_only_from_the_weather_units(wording, places):
    assert _weather_subjects_named(wording) == places


def test_a_turn_with_no_weather_unit_names_no_place():
    assert _weather_subjects_named("convert 100 USD to RUB and how much gold do i get") == []


# --- the shared extractor itself: a clause ends where the next request opens, and "like" is allowed ---
from tools.web.web_research import _extract_weather_locations


@pytest.mark.parametrize(
    "clause,places",
    [
        ("weather in Oslo also what is 5 plus 5 and tell me the time", ["oslo"]),
        ("what's the weather like in Vilnius", ["vilnius"]),
        ("how is the weather looking in Porto and Faro this weekend", ["porto", "faro"]),
        ("weather in Bergen and convert 30 NOK to SEK", ["bergen"]),
    ],
)
def test_the_extractor_stops_at_the_next_request_and_reads_like(clause, places):
    assert _extract_weather_locations(clause) == places


@pytest.mark.parametrize(
    "clause,places",
    [
        ("temprature in Oslo pls", ["oslo"]),
        ("forcast for Riga tomorrow", ["riga"]),
        ("wether in Vilnius thanks", ["vilnius"]),
        ("whether it is worth visiting Rome", []),
    ],
)
def test_the_extractor_spends_the_typo_budget_on_its_own_words_and_drops_politeness(clause, places):
    assert _extract_weather_locations(clause) == places
