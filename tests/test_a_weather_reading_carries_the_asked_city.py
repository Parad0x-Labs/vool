"""A weather reading is labelled with the city that was asked about, and only that city's reading is served.

Measured 2026-11-12 on the v0.8 line (0.8.0-beta, 247124ed), recorded from real wttr.in answers:

    asked "Paris"     -> nearest station "Saint-Merri", France      48.850,   2.350   (district, ~1 km)
    asked "New York"  -> nearest station "Weehawken", New Jersey    40.769, -74.021   (across the river)
    asked "Tokyo"     -> nearest station "Shikinejima", Japan       34.327, 139.218   (island, ~155 km)

The several-part lane rendered the station's name ("Saint-Merri, France: Overcast, 18.0C"), so a
person who asked about Paris read the weather of a place they could not find on a map; and Tokyo's
reading was an island's, because the place check only declines a station 300 km out. Two laws
follow, both pinned here end to end through the real lookup:

* a station within `_SAME_PLACE_KM` of the asked city is that city's reading and carries the
  CITY's name;
* a station farther out is another place's weather: the first provider declines, and the second
  answers for the city instead.

The same sweep found four everyday wordings that named a city and looked up nothing at all ("paris
weather rn", "tokyo weather pls", "how cold is it in paris today", "is it raining in Paris at the
moment"). The place scanner and the live-data classifier now read them from one shared set of
shapes (`core.weather_phrasing`), and prose that only mentions the weather still reads as no place.
"""

from __future__ import annotations

import contextlib
import json
from unittest import mock

import pytest

import tools.web.web_research as web_research
from core.agent_runtime.fast_live_info_mode_classifier import _WEATHER_LIVE_REQUEST_RE
from core.conductor.node import ConductorNode
from core.conductor.operations import _weather_render
from tests.test_weather_substitution_fixtures import FAR_CASES, MEASURED_NEAR_CASES, SubstitutionCase


def _wttr_payload(area_name: str, country: str, lat: float | None, lon: float | None) -> bytes:
    area: dict = {"areaName": [{"value": area_name}], "country": [{"value": country}], "region": [{"value": ""}]}
    if lat is not None and lon is not None:
        area["latitude"] = f"{lat:.3f}"
        area["longitude"] = f"{lon:.3f}"
    return json.dumps(
        {
            "current_condition": [
                {
                    "temp_C": "9",
                    "FeelsLikeC": "7",
                    "humidity": "81",
                    "windspeedKmph": "11",
                    "weatherDesc": [{"value": "Overcast"}],
                    "localObsDateTime": "2026-11-12 05:06 PM",
                }
            ],
            "nearest_area": [area],
            "weather": [{"maxtempC": "11", "mintempC": "6"}],
        }
    ).encode()


@contextlib.contextmanager
def _fake_remote(payload: bytes):
    class _Resp:
        def read(self, _n: int = 0) -> bytes:
            return payload

    yield _Resp()


def _remote_answering(payload: bytes):
    return lambda *_args, **_kwargs: _fake_remote(payload)


def _no_geocoder(*_args, **_kwargs):
    raise AssertionError("an offline-known city must not spend a geocoder call")


# The recorded 2026-11-12 answers, as (asked, station, country, lat, lon, label the person reads).
RECORDED_NEAR = (
    ("Paris", "Saint-Merri", "France", 48.850, 2.350, "Paris, France"),
    ("new york", "Weehawken", "United States of America", 40.769, -74.021, "New York, United States of America"),
    ("Warsaw", "Powisle", "Poland", 52.233, 21.000, "Warsaw, Poland"),
)


@pytest.mark.parametrize(("asked", "station", "country", "lat", "lon", "label"), RECORDED_NEAR)
def test_a_district_station_is_labelled_with_the_city_asked(asked, station, country, lat, lon, label) -> None:
    with mock.patch.object(
        web_research, "_open_remote", side_effect=_remote_answering(_wttr_payload(station, country, lat, lon))
    ), mock.patch.object(web_research, "_open_meteo_geocode", side_effect=_no_geocoder):
        result = web_research._wttr_weather_lookup(asked)

    assert result is not None
    assert result.place_label == label
    assert station not in result.place_label


@pytest.mark.parametrize("case", MEASURED_NEAR_CASES, ids=lambda c: c.name)
def test_every_measured_near_station_reads_as_its_city(case: SubstitutionCase) -> None:
    payload = _wttr_payload(case.resolved_area, case.resolved_country, case.latitude, case.longitude)
    with mock.patch.object(web_research, "_open_remote", side_effect=_remote_answering(payload)):
        result = web_research._wttr_weather_lookup(case.location)

    assert result is not None
    assert result.place_label.casefold().startswith(case.location.casefold())
    assert case.resolved_area not in result.place_label


@pytest.mark.parametrize("case", FAR_CASES, ids=lambda c: c.name)
def test_a_far_station_is_not_the_asked_citys_reading(case: SubstitutionCase) -> None:
    """Tokyo -> Shikinejima, ~155 km out: inside the old 300 km contradiction gate, so it used to be
    served as Tokyo's weather. The first provider now declines it."""

    payload = _wttr_payload(case.resolved_area, case.resolved_country, case.latitude, case.longitude)
    with mock.patch.object(web_research, "_open_remote", side_effect=_remote_answering(payload)):
        assert web_research._wttr_weather_lookup(case.location) is None


def test_a_far_station_is_replaced_by_the_station_at_the_citys_own_coordinates() -> None:
    """RECORDED 2026-11-12: wttr.in/Tokyo answers from Shikinejima (20 C); wttr.in/35.6895,139.6917
    answers from Kabukicho, a Tokyo district (12 C). The lookup asks once more, by the city's own
    coordinates, and serves THAT reading under the city's name."""

    urls: list[str] = []

    def _by_url(request, timeout=None):
        urls.append(request.full_url)
        asked_by_coordinates = "," in request.full_url.split("/")[3]
        if asked_by_coordinates:
            return _fake_remote(_wttr_payload("Kabukicho", "Japan", 35.695, 139.702))
        return _fake_remote(_wttr_payload("Shikinejima", "Japan", 34.327, 139.218))

    with mock.patch.object(web_research, "_open_remote", side_effect=_by_url), mock.patch.object(
        web_research, "_open_meteo_geocode", side_effect=_no_geocoder
    ):
        result = web_research._wttr_weather_lookup("tokyo")

    assert result is not None
    assert result.place_label == "Tokyo, Japan"
    assert len(urls) == 2 and urls[1].startswith("https://wttr.in/35.")
    assert result.source_url == urls[1].split("?")[0]


def test_the_conductor_line_names_the_city_not_the_station() -> None:
    """The several-part lane renders `place_label` first: the red-team line 'Saint-Merri, France:
    Overcast, 18.0C (source: wttr.in)' came from here."""

    with mock.patch.object(
        web_research,
        "_open_remote",
        side_effect=_remote_answering(_wttr_payload("Saint-Merri", "France", 48.850, 2.350)),
    ):
        result = web_research._wttr_weather_lookup("paris")
    assert result is not None
    node = ConductorNode(
        node_id="n1", operation="weather_lookup", request_text="paris weather", arguments={"location": "paris"}
    )
    line = _weather_render(node, {**result.to_payload(), "source": "wttr.in"})

    assert line.startswith("Paris, France: Overcast")
    assert "Saint-Merri" not in line


def test_the_research_fallback_names_the_city_too() -> None:
    with mock.patch.object(
        web_research,
        "_open_remote",
        side_effect=_remote_answering(_wttr_payload("Saint-Merri", "France", 48.850, 2.350)),
    ):
        outcome = web_research._weather_fallback("what's the weather in Paris?", timeout_s=5.0)

    assert outcome is not None
    _engine, hits, pages, _notes = outcome
    assert pages[0].text.startswith("Paris, France: Overcast")
    assert "Saint-Merri" not in hits[0].title + pages[0].text


def test_a_city_tzdb_does_not_know_is_measured_with_the_geocoder() -> None:
    """Krakow has no zone of its own; the geocoder says where it is, and the label is the city's."""

    krakow = web_research._PlacePoint("Kraków", "Poland", 50.061, 19.937)
    with mock.patch.object(
        web_research,
        "_open_remote",
        side_effect=_remote_answering(_wttr_payload("Krowodrza", "Poland", 50.078, 19.918)),
    ), mock.patch.object(web_research, "_open_meteo_geocode", return_value=krakow) as geocoder:
        result = web_research._wttr_weather_lookup("Krakow")

    assert result is not None
    assert result.place_label == "Kraków, Poland"
    geocoder.assert_called_once()


def test_a_close_typo_of_a_tzdb_city_is_not_measured_against_it() -> None:
    """Turin is two edits from Tunis. The geocoder reads the exact name first, so a Turin station is
    measured against Turin and kept, not against Tunis (900 km) and declined."""

    turin = web_research._PlacePoint("Turin", "Italy", 45.070, 7.687)
    with mock.patch.object(web_research, "_open_meteo_geocode", return_value=turin):
        label = web_research._weather_reading_label("Turin", "Lingotto", "Italy", "45.030", "7.660")

    assert label == "Turin, Italy"


def test_without_station_coordinates_the_providers_name_stands_and_no_lookup_is_spent() -> None:
    with mock.patch.object(web_research, "_open_meteo_geocode", side_effect=_no_geocoder):
        assert web_research._weather_reading_label("Krakow", "Kleparz", "Poland") == "Kleparz, Poland"


def test_an_unlocatable_place_keeps_the_providers_name() -> None:
    """Nobody can say where the asked place is: the label is never invented."""

    with mock.patch.object(web_research, "_open_meteo_geocode", side_effect=OSError("offline")):
        label = web_research._weather_reading_label("Zzyzx", "Baker", "United States of America", "35.27", "-116.07")

    assert label == "Baker, United States of America"


# -- the wordings that named a city and looked up nothing ------------------------------------------

EVERYDAY_WORDINGS = (
    ("paris weather rn", "paris"),
    ("tokyo weather pls", "tokyo"),
    ("how cold is it in paris today", "paris"),
    ("is it raining in Paris at the moment", "paris"),
    ("paris weather", "paris"),
    ("Paris weather?", "paris"),
    ("how warm is it in tokyo right now", "tokyo"),
    ("paris weather right this minute please", "paris"),
    ("how's paris weather atm", "paris"),
    ("whats the weather like in paris rn", "paris"),
    ("paris weather, gold price, and the time in new york pls", "paris"),
)


@pytest.mark.parametrize(("said", "place"), EVERYDAY_WORDINGS)
def test_everyday_wordings_name_their_place(said: str, place: str) -> None:
    assert web_research._extract_weather_locations(said)[:1] == [place]
    assert web_research._extract_weather_location(said) == place
    assert _WEATHER_LIVE_REQUEST_RE.search(said)


@pytest.mark.parametrize(
    "said",
    (
        "I love this weather",
        "nice weather",
        "the weather",
        "bad weather?",
        "how hot is it in the oven",
        "weather patterns are complex",
    ),
)
def test_prose_about_the_weather_names_no_place(said: str) -> None:
    assert web_research._extract_weather_locations(said) == []
    assert not _WEATHER_LIVE_REQUEST_RE.search(said)
