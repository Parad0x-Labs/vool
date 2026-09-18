"""One wrong letter must not drop a weather turn onto a model.

MEASURED live in the shipped app on 2026-08-18, by the operator, in ordinary use:

    "give me wheather in vilnius, berlin and rome also forecast for next 3 days in each of them"
        -> route=ordinary_plain_text_chat, the model was asked to serve it, the model call FAILED,
           and the user was told "no current weather results came back"

    "weather in vilnius, berlin and rome"
        -> route=live_data_typed_plan, a full sourced table for all three cities

Isolated afterwards: the forecast clause was a red herring. "weather ... also forecast for next
3 days" is served correctly. The single variable was the spelling of one word.

This is the same argument `core.measurement_medium` already makes for its own edit-distance
tolerance -- its live reproduction was "watter" -- and it generalises: a runtime that serves the
correctly-spelled request and drops the typo'd one fails exactly the users who type fastest.

`whether` is a real English word and is deliberately not tolerated; matching it would claim turns
that are not weather requests at all.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.fast_live_info_mode_classifier import _looks_like_live_weather_request

MISSPELLED = [
    "wheather in vilnius, berlin and rome",
    "give me wheather in vilnius, berlin and rome also forecast for next 3 days in each of them",
    "weater in rome",
    "whats the weathr in oslo",
    "wetaher in berlin",
    "weahter today",
    "wather in kaunas",
    "wheter in riga now",
]

CORRECT = [
    "weather in vilnius",
    "weather in vilnius, berlin and rome",
    "what is the current weather in oslo",
    "weather: berlin, rome",
]

# `whether` is ordinary English. Claiming these would send a non-weather turn to a weather provider.
NOT_WEATHER = [
    "I don't know whether it rains tomorrow, write me a poem about it",
    "ask whether he is coming to the meeting",
    "whether the build passes depends on the linter",
    "decide whether to refactor this module",
]


@pytest.mark.parametrize("text", MISSPELLED)
def test_a_common_misspelling_still_reaches_the_weather_lane(text: str) -> None:
    assert _looks_like_live_weather_request(text), (
        f"{text!r}: dropped out of the weather lane, so the turn goes to a model that cannot fetch "
        f"and the user is told no weather came back"
    )


@pytest.mark.parametrize("text", CORRECT)
def test_the_correct_spelling_is_unaffected(text: str) -> None:
    assert _looks_like_live_weather_request(text)


@pytest.mark.parametrize("text", NOT_WEATHER)
def test_whether_is_a_real_word_and_is_not_a_weather_request(text: str) -> None:
    assert not _looks_like_live_weather_request(text), (
        f"{text!r}: 'whether' was read as 'weather' -- this would send an ordinary turn to wttr.in"
    )


def test_a_water_temperature_request_is_still_declined() -> None:
    """The pre-existing carve-out must survive the widening: wttr.in answers AIR, not water."""
    assert not _looks_like_live_weather_request("what is the watter temperature in Balctic sea?")


def test_removing_the_tolerance_reproduces_the_measured_drop() -> None:
    """Sabotage: with only the correct spelling accepted, the operator's turn falls out again."""
    import re

    from core.agent_runtime import fast_live_info_mode_classifier as mod

    original = mod._WEATHER_LIVE_REQUEST_RE
    narrowed = re.compile(
        original.pattern.replace(mod._WEATHER_WORD, "(?:weather)"), original.flags
    )
    assert narrowed.pattern != original.pattern, "the tolerant word is no longer substitutable"

    mod._WEATHER_LIVE_REQUEST_RE = narrowed
    try:
        dropped = [t for t in MISSPELLED if not _looks_like_live_weather_request(t)]
        kept = [t for t in CORRECT if _looks_like_live_weather_request(t)]
    finally:
        mod._WEATHER_LIVE_REQUEST_RE = original

    # One fixture is NOT carried by the tolerance and must not be claimed as such: the operator's
    # full prompt also contains "forecast for next 3 days", which matches the `forecast` trigger on
    # its own. Measured: it reaches the weather lane with or without this fix.
    #
    # That distinction matters for what this repair is allowed to claim. The operator's turn DID
    # reach the weather lane (`route=live_info_fast_path`) and the lane then returned nothing --
    # "no current weather results came back" for three cities. That is a SEPARATE defect in the
    # lane's multi-city/forecast handling, and this fix does not address it. The typo fix is for the
    # shape isolated afterwards: "wheather in vilnius, berlin and rome", which fell out of the lane
    # entirely and landed on a model.
    carried_by_forecast = [t for t in MISSPELLED if "forecast" in t]
    expected_dropped = [t for t in MISSPELLED if t not in carried_by_forecast]
    assert sorted(dropped) == sorted(expected_dropped), (
        "SABOTAGE DID NOT BITE as measured: with only the correct spelling accepted, exactly the "
        "misspellings with no other trigger word must fall out of the lane. Expected "
        f"{len(expected_dropped)}, got {len(dropped)}"
    )
    assert len(kept) == len(CORRECT), "the correct spellings must be unaffected by the sabotage"
    assert _looks_like_live_weather_request(MISSPELLED[0])
