"""A live weather claim requires the place the provider resolved to match what was asked.

Measured on the served surface, 2026-08-15::

    U: weather in Talinn now?
       (event log: livedata:weather:talinn_now_? FAILED in 0.3s)
    A: Weather in Talinn: Bouma'chouk, Algeria: Sunny, 31 C (feels like 30 C) ...

"Talinn" is a typo for Tallinn. wttr.in does not error on an unrecognized string -- it geolocates
to some arbitrary nearest area, here "Bouma'chouk, Algeria" -- so the runtime reported a confident
wrong place for a lookup that had already logged a failure. A confidently wrong live claim is the
worst answer class this runtime has.

The invariant: a 200 with parseable conditions is NOT evidence of a PLACE match. The place the
provider actually resolved (nearest_area areaName/country) must CORRESPOND to the request, or the
lookup declines. Correspondence is generous toward SAFE outcomes -- a real match, a country append,
a multi-word overlap, and a typo the provider fixed for us all pass; an exotic alias mapped to an
unrelated string declines rather than fabricates, because declining cannot mislead and fabricating
always can.
"""

from __future__ import annotations

import pytest

from tools.web.web_research import _weather_place_corresponds

# ---------------------------------------------------------------------------------------------
# The reported failure and its class
# ---------------------------------------------------------------------------------------------


def test_a_coordinate_contradiction_is_rejected() -> None:
    """The reported fabrication, with the coordinates the real payload carries: "Talinn"
    geolocated to Algeria, 2,700 km from Tallinn."""

    assert _weather_place_corresponds(
        "Talinn", "Bouma'chouk", "Algeria", "Tipaza", "36.600", "2.417"
    ) is False


def test_a_name_coincidence_does_not_beat_the_coordinates() -> None:
    """Requested Paris, station named Paris -- in Texas. Still a mis-geolocation."""

    assert _weather_place_corresponds(
        "Paris", "Paris", "United States of America", "", "33.662", "-95.555"
    ) is False


def test_a_nearby_station_with_a_different_name_corresponds() -> None:
    """RECORDED reality: stations are districts. Warsaw's is Powisle, 2 km away."""

    assert _weather_place_corresponds(
        "Warsaw", "Powisle", "Poland", "", "52.233", "21.000"
    ) is True


@pytest.mark.parametrize(
    ("requested", "area", "country"),
    (
        ("Tallinn", "Tallinn", "Estonia"),
        ("Paris", "Paris", "France"),
        ("Munchen", "München", "Germany"),
        # Station named differently, NO coordinates in the payload: indistinguishable from a
        # correct answer, so it passes -- requiring name confirmation here is exactly what broke
        # Warsaw, Manchester and Krakow on the served surface. Decline needs CONTRADICTION.
        ("Krakow", "Kleparz", "Poland"),
        ("Springfield", "Shelbyville", "United States"),
    ),
)
def test_without_contradiction_evidence_the_lookup_answers(requested: str, area: str, country: str) -> None:
    assert _weather_place_corresponds(requested, area, country) is True


def test_no_requested_place_is_never_a_mismatch() -> None:
    """The IP-geolocation path passes an empty location; whatever the provider resolves is the
    answer, so there is nothing to contradict and the guard must not reject it."""

    assert _weather_place_corresponds("", "Vilnius", "Lithuania") is True


# ---------------------------------------------------------------------------------------------
# The gate is wired into both lookup paths
# ---------------------------------------------------------------------------------------------


import contextlib
import json
from unittest import mock

import tools.web.web_research as web_research


def _wttr_payload(
    area_name: str, country: str, *, region: str = "", lat: str = "", lon: str = ""
) -> bytes:
    area: dict = {"areaName": [{"value": area_name}], "country": [{"value": country}]}
    if region:
        area["region"] = [{"value": region}]
    if lat:
        area["latitude"] = lat
        area["longitude"] = lon
    return json.dumps(
        {
            "current_condition": [
                {
                    "temp_C": "31",
                    "FeelsLikeC": "30",
                    "humidity": "22",
                    "windspeedKmph": "4",
                    "weatherDesc": [{"value": "Sunny"}],
                    "localObsDateTime": "2026-08-15 08:04 AM",
                }
            ],
            "nearest_area": [area],
            "weather": [{}],
        }
    ).encode()


@contextlib.contextmanager
def _fake_remote(payload: bytes):
    class _Resp:
        def read(self, _n: int = 0) -> bytes:
            return payload

    yield _Resp()


def _always_returns(payload: bytes):
    """A patch value for `_open_remote` that survives being entered more than once.

    `mock.patch.object(..., return_value=_fake_remote(...))` hands back ONE
    `@contextlib.contextmanager` instance on every call, and such an instance is single-use by
    construction: Python 3.11's `__enter__` deletes `self.args/kwds/func` so the generator
    cannot be recreated. The weather lane is a FAILOVER chain -- wttr, then open-meteo's
    geocoder, then its forecast -- so any request that declines re-enters the exhausted object
    and dies with `AttributeError: '_GeneratorContextManager' object has no attribute 'args'`.

    That is why exactly the three DECLINE tests failed while their siblings passed: only a
    decline reaches provider two. The decline invariant they exist to pin was untested, and the
    failures read as a broken guard rather than a broken fixture.

    A factory has no arm ceiling, so a fourth provider does not turn this into a
    StopIteration the day someone adds one.
    """
    return lambda *_args, **_kwargs: _fake_remote(payload)


def test_the_structured_lookup_declines_a_fabricated_place_end_to_end() -> None:
    """EXECUTED, not grepped. A source check for the guard string survived a sabotage that
    dead-coded the branch (`if False and ...`) -- only driving the real function through the real
    gate distinguishes a live guard from a present-but-dead one.

    The provider resolves "Talinn" to Bouma'chouk, Algeria (the measured failure). The lookup must
    return None -- decline -- not a WeatherResult for the wrong city.
    """

    with mock.patch.object(
        web_research,
        "_open_remote",
        side_effect=_always_returns(
            _wttr_payload("Bouma'chouk", "Algeria", region="Tipaza", lat="36.600", lon="2.417")
        ),
    ):
        result = web_research.structured_weather_lookup("Talinn")

    assert result is None


def test_the_structured_lookup_returns_a_corresponding_place_end_to_end() -> None:
    """The control: a real resolution still produces a result, so the guard has not closed the lane."""

    with mock.patch.object(
        web_research, "_open_remote", return_value=_fake_remote(_wttr_payload("Tallinn", "Estonia"))
    ):
        result = web_research.structured_weather_lookup("Tallinn")

    assert result is not None
    assert result.place_label == "Tallinn, Estonia"
    assert result.temperature_c == 31.0


def test_the_prose_fallback_declines_a_fabricated_place_end_to_end() -> None:
    """The sibling builder must decline too -- both paths build a place from the resolution."""

    with mock.patch.object(
        web_research,
        "_open_remote",
        side_effect=_always_returns(
            _wttr_payload("Bouma'chouk", "Algeria", region="Tipaza", lat="36.600", lon="2.417")
        ),
    ):
        summary = web_research._weather_fallback("Talinn", timeout_s=5.0)

    assert summary is None or "bouma" not in summary.lower()


# ---------------------------------------------------------------------------------------------
# The search-snippet fallback must not re-create the fabrication one lane over
# ---------------------------------------------------------------------------------------------


def test_the_snippet_fallback_rejects_a_snippet_naming_another_place() -> None:
    """Measured live AFTER the wttr gate landed: the typo "Villnius" declined at wttr, then the
    web-search fallback pasted a Mount Airy, NC snippet under "Weather in Villnius:". The header
    is a claim of correspondence, and a snippet about somewhere else cannot back it."""

    from core.agent_runtime.fast_live_info_weather_rendering import render_weather_response

    out = render_weather_response(
        query="weather in Villnius now?",
        notes=[{
            "summary": "Hourly weather forecast in Mount Airy, NC. Check current conditions in Mount Airy, NC",
            "result_url": "https://x", "origin_domain": "weather.com",
        }],
    )

    assert "Mount Airy" not in out
    assert "couldn't verify" in out


def test_a_place_free_snippet_still_renders_the_legacy_way() -> None:
    """The discriminator is place-NAMING, not place-absence: "Cloudy." carries no competing place
    and the search that produced it was already scoped to the city. Two release-regression tests
    pin this legacy behaviour; this control keeps the two invariants from fighting."""

    from core.agent_runtime.fast_live_info_weather_rendering import render_weather_response

    out = render_weather_response(
        query="weather in London",
        notes=[{"summary": "Cloudy.", "result_url": "https://example.com", "origin_domain": "example.com"}],
    )

    assert out.startswith("Weather in London:")


def test_a_snippet_naming_the_requested_place_renders() -> None:
    from core.agent_runtime.fast_live_info_weather_rendering import render_weather_response

    out = render_weather_response(
        query="weather in new york now?",
        notes=[{"summary": "Forecast for New York City: 22 C cloudy", "result_url": "", "origin_domain": ""}],
    )

    assert "22" in out


# ---------------------------------------------------------------------------------------------
# REAL RECORDED PAYLOADS -- the lesson this fix was taught by production
# ---------------------------------------------------------------------------------------------
#
# The first version of this gate compared names against `nearest_area` and was tested with mocks
# that returned what the author ASSUMED the provider sends ("Tallinn" -> areaName "Tallinn"). In
# reality nearest_area is the nearest weather STATION, routinely a district sharing no words with
# the city: the gate rejected "Warsaw" (station "Powisle") and "Manchester" (station "Sedgley
# Park") and broke live weather for every big city for ~35 minutes while the mocked suite stayed
# green. The fixtures below are RECORDED from real wttr.in responses on 2026-08-15, so the tests
# now encode the provider's actual behaviour instead of the author's model of it.


def test_warsaw_via_its_real_station_payload_answers() -> None:
    """RECORDED: wttr.in resolves "Warsaw" to station Powisle at 52.233/21.000, region empty.

    No name channel can pass this -- only the tzdb coordinate channel (Europe/Warsaw is
    +5215+02100, ~2 km away) proves the station is Warsaw's."""

    with mock.patch.object(
        web_research,
        "_open_remote",
        side_effect=_always_returns(
            _wttr_payload("Powisle", "Poland", lat="52.233", lon="21.000")
        ),
    ):
        result = web_research.structured_weather_lookup("Warsaw")

    assert result is not None
    assert result.temperature_c == 31.0


def test_manchester_via_its_real_station_payload_answers() -> None:
    """RECORDED: "Manchester" resolves to station Sedgley Park, region "Greater Manchester".

    Manchester is not a tzdb zone city, so the REGION name channel is what carries it."""

    with mock.patch.object(
        web_research,
        "_open_remote",
        side_effect=_always_returns(
            _wttr_payload("Sedgley Park", "United Kingdom", region="Greater Manchester")
        ),
    ):
        result = web_research.structured_weather_lookup("Manchester")

    assert result is not None


def test_the_talinn_mis_geolocation_payload_still_declines() -> None:
    """RECORDED: "Talinn" geolocates to Bouma'chouk, Tipaza, Algeria at 36.600/2.417.

    Tallinn (the tzdb neighbour within the typo budget) is at 59.42/24.75 -- ~2,700 km away.
    The coordinate channel rejects it; so would every name channel."""

    with mock.patch.object(
        web_research,
        "_open_remote",
        side_effect=_always_returns(
            _wttr_payload("Bouma'chouk", "Algeria", region="Tipaza", lat="36.600", lon="2.417")
        ),
    ):
        result = web_research.structured_weather_lookup("Talinn")

    assert result is None


def test_coordinates_overrule_a_name_coincidence() -> None:
    """The authoritative direction: a station NAMED like the request but half a world away is a
    mis-geolocation, whatever it is called. Requested "Paris", station "Paris" -- in Texas."""

    with mock.patch.object(
        web_research,
        "_open_remote",
        side_effect=_always_returns(
            _wttr_payload("Paris", "United States of America", lat="33.662", lon="-95.555")
        ),
    ):
        result = web_research.structured_weather_lookup("Paris")

    assert result is None


def test_a_non_tzdb_city_with_no_coordinate_evidence_still_uses_names() -> None:
    """The fallback order: no provider coordinates -> the name channels decide, as before."""

    with mock.patch.object(
        web_research,
        "_open_remote",
        return_value=_fake_remote(_wttr_payload("Tallinn", "Estonia")),
    ):
        assert web_research.structured_weather_lookup("Tallinn") is not None
