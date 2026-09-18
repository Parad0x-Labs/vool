"""A search snippet describing a different place is not this place's weather, on EITHER renderer.

THE MEASURED DEFECT (2026-08-18, shipped app, route `live_info_fast_path`):

    "what is the weather in Vilnius right now?"
      -> "Weather in Vilnius: Be prepared with the most accurate 10-day forecast for Seattle,
          Washington with highs, lows, chance of precipitation from The Weather Channel and
          Weather.com. Source: [weather.com](https://weather.com/us/washington/city/seattle/tenday)."

A wrong place stated with full confidence, under the requested city's heading, with a citation
pointing at the wrong city -- the part that makes it look checkable.

WHY IT SURVIVED. The gate already existed. `_snippet_names_another_place` was added 2026-08-15
after the same fabrication ("Mount Airy, NC" under "Weather in Villnius") and it returns True on
this very snippet. It was wired into the renderer that parses the location out of the QUERY, and
not into `_weather_note_line`, which serves the grouped-notes renderer -- the single-city path and
every row of the multi-city table. Two roads to one fabrication, a gate on one of them.

WHAT IS ASSERTED. The renderers' own output on real snippet text. The negative controls matter as
much as the positive: a snippet that names the RIGHT city must still render with its source, and a
bare conditions snippet that names no place at all must still render -- the search that produced it
was already scoped to the requested city, which is the legacy behaviour existing release tests pin.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_live_info_weather_rendering import (
    _snippet_names_another_place,
    render_weather_response,
)

SEATTLE_FOR_VILNIUS = (
    "Be prepared with the most accurate 10-day forecast for Seattle, Washington with highs, "
    "lows, chance of precipitation from The Weather Channel and Weather.com"
)
MOUNT_AIRY = "Hourly weather for Mount Airy, NC with chance of rain"
BERLIN_FOR_BERLIN = (
    "Berlin, Berlin, Germany Weather Forecast, with current conditions, wind, air quality, "
    "and what to expect for the next 3 days"
)
BARE_CONDITIONS = "Cloudy. High 14 C, low 12 C."


def _note(location: str, summary: str, url: str = "https://example.invalid/x",
          domain: str = "example.invalid") -> dict[str, object]:
    return {"_weather_location": location, "summary": summary,
            "result_url": url, "origin_domain": domain}


@pytest.mark.parametrize("summary", (SEATTLE_FOR_VILNIUS, MOUNT_AIRY))
def test_a_snippet_naming_another_city_is_not_rendered_as_this_citys_weather(summary: str) -> None:
    out = render_weather_response(query="weather in Vilnius", notes=[_note("Vilnius", summary)])

    assert "different place" in out
    for leaked in ("Seattle", "Washington", "Mount Airy", "NC"):
        assert leaked not in out


def test_the_misleading_source_link_is_dropped_with_the_snippet() -> None:
    """A citation pointing at Seattle under a Vilnius heading is what makes the wrong answer look
    verifiable, so it must not survive the decline."""
    out = render_weather_response(
        query="weather in Vilnius",
        notes=[_note("Vilnius", SEATTLE_FOR_VILNIUS,
                     url="https://weather.com/us/washington/city/seattle/tenday",
                     domain="weather.com")],
    )

    assert "weather.com" not in out
    assert "http" not in out


def test_a_snippet_naming_the_requested_city_still_renders_with_its_source() -> None:
    out = render_weather_response(
        query="weather in Berlin",
        notes=[_note("Berlin", BERLIN_FOR_BERLIN,
                     url="https://www.accuweather.com/en/de/berlin/10178/x",
                     domain="accuweather.com")],
    )

    assert "Germany Weather Forecast" in out
    assert "accuweather.com" in out


def test_a_bare_conditions_snippet_naming_no_place_still_renders() -> None:
    """It carries no competing place, and the search that produced it was already scoped to the
    requested city. Declining here would break weather for every provider that returns conditions
    without repeating the city name."""
    out = render_weather_response(
        query="weather in Vilnius",
        notes=[_note("Vilnius", BARE_CONDITIONS, url="https://wttr.in/vilnius", domain="wttr.in")],
    )

    assert "Cloudy" in out
    assert "different place" not in out
    assert "wttr.in" in out


def test_each_row_of_a_multi_city_table_is_gated_independently() -> None:
    """The table path runs through the same helper, so one bad row must not poison the good ones
    and must not be served either."""
    out = render_weather_response(
        query="weather in Vilnius and Berlin",
        notes=[
            _note("Vilnius", SEATTLE_FOR_VILNIUS, url="https://weather.com/seattle", domain="weather.com"),
            _note("Berlin", BERLIN_FOR_BERLIN, url="https://accuweather.com/berlin", domain="accuweather.com"),
        ],
    )

    assert "different place" in out
    assert "Seattle" not in out
    assert "Germany Weather Forecast" in out


def test_the_gate_itself_already_recognised_this_snippet() -> None:
    """Naming the actual cause: the detector was never wrong, it was only never called here."""
    assert _snippet_names_another_place(SEATTLE_FOR_VILNIUS, "Vilnius") is True
    assert _snippet_names_another_place(BERLIN_FOR_BERLIN, "Berlin") is False
    assert _snippet_names_another_place(BARE_CONDITIONS, "Vilnius") is False


def test_sabotage_removing_the_gate_from_this_renderer_restores_the_fabrication(monkeypatch) -> None:
    """Revert the one call and Seattle is served as Vilnius again, citation and all."""
    from core.agent_runtime import fast_live_info_weather_rendering as rendering

    monkeypatch.setattr(rendering, "_snippet_names_another_place", lambda summary, location: False)
    out = rendering.render_weather_response(
        query="weather in Vilnius",
        notes=[_note("Vilnius", SEATTLE_FOR_VILNIUS,
                     url="https://weather.com/us/washington/city/seattle/tenday",
                     domain="weather.com")],
    )

    assert "Seattle, Washington" in out
    assert "weather.com/us/washington/city/seattle" in out


# =================================================================================================
# A unit of measurement is not a place.
#
# Surfaced by the gate above, in the regression at 19b004ec on 2026-08-18:
#
#   "Search for the current temperature in Celsius and wind direction in Auckland, New Zealand
#    right now."   ->   "Weather in Celsius: ..."
#
# The locative "in Celsius" is structurally identical to the locative "in Auckland", and the scale
# comes first, so it won. The answer was headed with a unit as though it were a city. The place gate
# then declined the snippet -- correctly, and for the wrong reason -- which is how it became visible.
# =================================================================================================


@pytest.mark.parametrize(
    "unit", ("Celsius", "celsius", "celcius", "Fahrenheit", "Kelvin", "mph", "kph", "degrees", "C")
)
def test_a_unit_of_measurement_is_not_a_weather_location(unit: str) -> None:
    from tools.web.web_research import _is_plausible_weather_location

    assert _is_plausible_weather_location(unit) is False


@pytest.mark.parametrize(
    "place",
    (
        "Auckland",
        "Auckland, New Zealand",
        # The reason this arm is a WHOLE-string check and not a token scan.
        "Kelvin Grove",
        "Bar",
        "Vilnius",
        "New York City",
        "Rio de Janeiro",
        "San Carlos de Bariloche Rio Negro Argentina",
    ),
)
def test_real_places_are_untouched_by_the_unit_arm(place: str) -> None:
    from tools.web.web_research import _is_plausible_weather_location

    assert _is_plausible_weather_location(place) is True


def test_sabotage_removing_the_unit_arm_accepts_celsius_as_a_city(monkeypatch) -> None:
    from tools.web import web_research

    monkeypatch.setattr(web_research, "_MEASUREMENT_UNIT_TOKENS", frozenset())
    assert web_research._is_plausible_weather_location("Celsius") is True
    assert web_research._is_plausible_weather_location("Auckland") is True
