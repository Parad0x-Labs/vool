"""A water-temperature request is never answered with the air reading above it.

Measured live on c6eed761 (2026-08-14), served UI::

    U: what is the watter temperature in Balctic sea?
    A: Balctic Sea: Clear, 15 C (today's high 30 C / low 14 C).
       Source: wttr.in, observed 11:15 AM.        <- AIR, for a geolocated point
                                                     the sea surface was near 18 C

The body of water was taken as a city, wttr.in returned that place's atmosphere, and the runtime
rendered it as the answer with a source line. A wrong value wearing the shape of a correct reply is
worse than no reply: nothing downstream doubts it, and the attribution invites more trust, not less.

These tests pin the MEDIUM distinction -- air is not water -- not the reported sentence. Nothing here
depends on the Baltic, on the "watter" typo, or on wttr.in.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_live_info_mode_classifier import _looks_like_live_weather_request
from core.measurement_medium import requests_a_water_temperature

# ---------------------------------------------------------------------------------------------
# G1 -- the reproduction
# ---------------------------------------------------------------------------------------------


def test_the_reported_request_is_recognised_as_a_water_measurement() -> None:
    assert requests_a_water_temperature("what is the watter temperature in Balctic sea?")


def test_the_air_weather_lane_no_longer_claims_it() -> None:
    """The blocker: this is what let an air reading be rendered as the answer."""

    assert not _looks_like_live_weather_request("what is the watter temperature in Balctic sea?")


# ---------------------------------------------------------------------------------------------
# CLEAN -- other media, other places, no typo
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    (
        "what is the water temperature in the North Sea",
        "sea surface temperature of the Adriatic",
        "how warm is the ocean off Lisbon",
        "lake temperature at Como",
        "river temperature in Kaunas today",
        "what's the seawater temperature at Bondi",
    ),
)
def test_any_water_medium_is_recognised(request_text: str) -> None:
    assert requests_a_water_temperature(request_text), request_text
    assert not _looks_like_live_weather_request(request_text), request_text


# ---------------------------------------------------------------------------------------------
# SLOPPY -- the reproduction was a typo, so typos are the normal case
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    (
        "whats the oceean temp",
        "wather temperature baltic",
        "sea temp rn",
        "how cold is the watter",
        "temprature of the sea pls",
    ),
)
def test_sloppy_water_requests_are_recognised(request_text: str) -> None:
    assert requests_a_water_temperature(request_text), request_text


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- ordinary air weather must be completely untouched
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "request_text",
    (
        "what is the temperature in Vilnius",
        "weather in Vilnius",
        "whats the weather like today",
        "is it cold in Reykjavik",
        "wehather forecast for next 3 days in vilnius pls",
        "current air temperature in Tromso right now",
        "how warm is it in Lisbon",
    ),
)
def test_air_weather_requests_are_untouched(request_text: str) -> None:
    assert not requests_a_water_temperature(request_text), request_text


def test_a_water_word_without_a_temperature_request_is_untouched() -> None:
    """Both halves are required; a sea is not automatically a temperature question."""

    for request_text in ("sea conditions today", "is the lake frozen", "ocean forecast", "river levels"):
        assert not requests_a_water_temperature(request_text), request_text


def test_an_ordinary_weather_request_still_reaches_the_live_lane() -> None:
    """The guard must not cost the air lane a single legitimate turn."""

    assert _looks_like_live_weather_request("what is the current weather in Vilnius right now")


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL NEAR-MISSES
# ---------------------------------------------------------------------------------------------


def test_weather_does_not_collapse_into_water() -> None:
    """The pair that sets the fuzzy floor.

    "weather" scores 0.833 against "water". At a first-cut floor of 0.82 this sentence was read as a
    water request and an ordinary air lookup would have been refused. Measured, then fixed at 0.87.
    """

    assert not requests_a_water_temperature("what is the weather temperature in Vilnius")
    assert not requests_a_water_temperature("weather temp in Oslo")
    assert _looks_like_live_weather_request("what is the weather temperature in Vilnius")


def test_the_typo_shapes_that_must_still_match_do() -> None:
    """The other side of that floor: 0.909 shapes must survive it."""

    assert requests_a_water_temperature("watter temperature baltic")
    assert requests_a_water_temperature("wather temperature baltic")
    assert requests_a_water_temperature("oceean temperature")


def test_a_place_named_after_water_is_not_a_water_measurement() -> None:
    """A medium word must be asked ABOUT, not merely present in a place name.

    Known boundary, stated rather than hidden: this module reads tokens, so a place whose NAME
    contains a medium word and a temperature request together will read as a water request. That is
    why the negative controls above matter, and why the guard only ever declines a lane rather than
    asserting an answer.
    """

    assert not requests_a_water_temperature("weather in Seattle")
    assert not requests_a_water_temperature("forecast for Riverside")


def test_the_research_fallback_also_declines_the_weather_provider() -> None:
    """The second seam, found live AFTER the first was fixed.

    With the typed lane declining, the research fallback reached the same fetcher by another road:
    "balctic sea" went to wttr.in, which geolocated it to SEATTLE and returned Seattle's air
    conditions. Closing one road is not closing the invariant.
    """

    from tools.web.web_research import _weather_fallback

    assert _weather_fallback("what is the watter temperature in Balctic sea?", timeout_s=5.0) is None
    assert _weather_fallback("sea surface temperature of the Adriatic", timeout_s=5.0) is None


def test_the_research_fallback_still_serves_ordinary_air_weather(monkeypatch) -> None:
    """Negative control: the guard must not close the road for a real weather question."""

    import urllib.request

    from tools.web import web_research as wr

    opened: list = []

    class _Response:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def read(self, *args):
            return b"{}"

    def _open(request, timeout=None):
        opened.append(request)
        return _Response()

    monkeypatch.setattr(urllib.request, "urlopen", _open)
    from core.remote_fetch_policy import remote_fetch_policy_scope

    with remote_fetch_policy_scope({"allow_remote_fetch": True}):
        wr._weather_fallback("what is the weather in Vilnius", timeout_s=5.0)

    assert opened, "an ordinary air-weather request must still reach the provider"
