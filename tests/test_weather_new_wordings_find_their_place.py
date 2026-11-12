"""Three everyday weather wordings are recognized and find their place, in both readers.

App bugs 2026-11-12, the rerun of the Paris weather fix with wordings never asked before, on the
real chat page of the gated build (evidence/proof-380d65c0/weather-380d65c0/table.md):

- "what's it like outside in Paris right now?" and "is it cold in Berlin right now" were not
  recognized as weather asks, reached the model, and came back as a refusal;
- "Paris forecast for today?" was recognized, but the place scanner read "today" after "for" and
  found no place, so the answer was "no current weather results came back".

The classifier (`core.agent_runtime.fast_live_info_mode_classifier`) and the place scanner
(`tools.web.web_research`) read these shapes from one definition (`core.weather_phrasing`), so an
ask that is claimed always has its place. Questions about life in a place stay out.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_live_info_mode_classifier import _looks_like_live_weather_request
from tools.web.web_research import _extract_weather_location, _extract_weather_locations

ASKS = [
    # the three that failed on the gated build
    ("what's it like outside in Paris right now?", "paris"),
    ("is it cold in Berlin right now", "berlin"),
    ("Paris forecast for today?", "paris"),
    # more wordings of the same three shapes
    ("how's it outside in Oslo today?", "oslo"),
    ("what is it like out in Vilnius", "vilnius"),
    ("is it warm in Lisbon today", "lisbon"),
    ("isn't it freezing in Toronto right now?", "toronto"),
    ("is it really windy in Chicago at the moment", "chicago"),
    ("Tokyo forecast for tomorrow", "tokyo"),
    ("london weather for today pls", "london"),
    ("what's it like outside in New York", "new york"),
]

NOT_WEATHER = [
    "is it cold in here",
    "is it hot in the oven",
    "what's it like living in Paris?",
    "what is it like to work in Berlin",
    "I love this weather",
]


@pytest.mark.parametrize("ask,place", ASKS)
def test_the_ask_is_a_weather_lookup_and_names_its_place(ask: str, place: str) -> None:
    assert _looks_like_live_weather_request(ask), ask
    assert [p.lower() for p in _extract_weather_locations(ask)] == [place], ask
    assert _extract_weather_location(ask).lower() == place, ask


@pytest.mark.parametrize("text", NOT_WEATHER)
def test_life_in_a_place_and_prose_stay_out(text: str) -> None:
    assert not _looks_like_live_weather_request(text), text
    assert _extract_weather_locations(text) == [], text


@pytest.mark.parametrize("ask,places", [
    ("weather for today in Riga", ["riga"]),
    ("Riga forecast for tonight", ["riga"]),
    ("forecast for tomorrow in Riga", ["riga"]),
    ("weather for tomorrow in paris and berlin", ["paris", "berlin"]),
    ("what's the weather for today in Rome?", ["rome"]),
])
def test_a_time_word_after_for_is_never_read_as_the_place(ask: str, places: list[str]) -> None:
    assert [p.lower() for p in _extract_weather_locations(ask)] == places, ask
