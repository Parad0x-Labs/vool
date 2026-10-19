"""A cooking or appliance temperature is not the air temperature at a place.

Measured on the 0.7.2 train head (153148e), served daemon: the learning-loop benchmark question
"What oven temperature for roasting potatoes, and how far apart should I space them on the tray?"
was classified LIVE_DATA, planned as a weather lookup for a place called "roasting potatoes"
(`livedata-...:weather:roasting_potatoes`), and answered "I could not obtain a current reading for
this on this turn, so I am not going to state one." The weather recognizer read "temperature for"
as "the temperature for <place>". An oven setting is ordinary cooking knowledge: no live source
answers it, and none is needed.

Both sides are pinned with independently worded phrasings: settings of an appliance or a cooking
process leave the weather lane, and genuine weather asks (including one beside a cooking question
in the same message) keep it.
"""
from __future__ import annotations

import pytest

from core.agent_runtime.answer_coverage import FAMILY_LIVE_INFO, slice_families
from core.agent_runtime.fast_live_info_mode_classifier import live_info_mode
from core.execution_requirements import classify_requirements

COOKING_AND_APPLIANCE_SETTINGS = [
    "What oven temperature for roasting potatoes, and how far apart should I space them on the tray?",
    "what temperature for baking sourdough bread?",
    "best temperature for frying chicken in oil",
    "fridge temperature for storing milk?",
    "what temperature in the oven for lasagna",
    "internal temperature for medium rare steak",
    "air fryer temperature for frozen fries",
    "Which temperature for slow roasting a pork shoulder?",
    "what's the right temperature for brewing green tea",
]

GENUINE_WEATHER_ASKS = [
    "what's the temperature in Berlin right now",
    "current temperature outside",
    "weather tomorrow",
    "temperature in Rome today",
    "what's the temperature for Paris tomorrow",
    "temperature tonight in Oslo",
    "What's the temperature up at the lookout right now?",
    "weather in vilnius, berlin and rome",
]


@pytest.mark.parametrize("text", COOKING_AND_APPLIANCE_SETTINGS)
def test_a_cooking_or_appliance_temperature_needs_no_live_lookup(text):
    requirements = classify_requirements(text)
    assert requirements.answer_mode != "LIVE_DATA", (text, requirements)
    assert "weather" not in requirements.allowed_toolsets, (text, requirements)
    assert not requirements.current_information_required, (text, requirements)
    assert live_info_mode(None, text, interpretation=None) != "weather"


@pytest.mark.parametrize("text", GENUINE_WEATHER_ASKS)
def test_a_genuine_weather_ask_keeps_the_weather_lane(text):
    requirements = classify_requirements(text)
    assert requirements.answer_mode == "LIVE_DATA", (text, requirements)
    assert "weather" in requirements.allowed_toolsets
    assert live_info_mode(None, text, interpretation=None) == "weather"


def test_a_weather_ask_beside_a_cooking_question_keeps_only_its_own_lane():
    text = "What's the weather in Berlin, and what oven temperature for roasting potatoes?"
    requirements = classify_requirements(text)
    assert requirements.answer_mode == "LIVE_DATA"
    assert "weather" in requirements.allowed_toolsets
    from core.live_data_plan import build_live_data_plan

    plan = build_live_data_plan(text, plan_id="p-cooking", attempt_id="a-cooking")
    assert plan is not None
    places = [task.entity for task in plan.weather_subtasks()]
    assert places and all("berlin" in str(place).lower() for place in places), places


def test_the_benchmark_question_is_owed_to_no_live_family():
    text = "What oven temperature for roasting potatoes, and how far apart should I space them on the tray?"
    rows = slice_families(text)
    assert rows
    assert not any(FAMILY_LIVE_INFO in families for _slice, families in rows), rows
