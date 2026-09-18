"""A temporal qualifier is not part of a place name, wherever it sits in the sentence.

Live 0.5.0 evidence: "what is the weather in Vilnius for next week?" built NO live-data plan at
all, even though the request names its city plainly.  `_WEATHER_TRAILING_NOISE` carried "week" but
not "next", so the trailing strip left "... for next", the candidate stopped resolving, and the
whole plan was abandoned into the ordinary chat lane -- which then answered with a fabricated tool
call (see `test_final_answer_is_never_a_tool_call`).  A qualifier standing BEFORE the preposition
that introduces the place ("weather tomorrow in Vilnius") was never removed at all.

The invariant is about sentence shape, not about weather vocabulary or any particular city: the
cases below roam across cities, qualifier forms, and positions the reproduction never used.
"""

from __future__ import annotations

import pytest

from core.agent_runtime.live_data_plan import build_live_data_plan
from tools.web.web_research import _extract_weather_locations_with_confidence


def _planned_locations(user_text: str) -> list[str]:
    plan = build_live_data_plan(user_text, plan_id="test", attempt_id="test")
    if plan is None:
        return []
    return [str(subtask.arguments.get("location") or "") for subtask in plan.weather_subtasks()]


@pytest.mark.parametrize(
    ("user_text", "expected"),
    (
        # The exact live reproduction, and the same qualifier on the other side of the city.
        ("what is the weather in Vilnius for next week?", "vilnius"),
        ("what is the weather for next week in Vilnius?", "vilnius"),
        # The qualifier standing between the weather word and the place.
        ("what is the weather tomorrow in Vilnius?", "vilnius"),
        # Unseen cities and unseen qualifier forms.
        ("weather in Oslo over the weekend", "oslo"),
        ("what is the weather in Reykjavik for the next 3 days?", "reykjavik"),
        ("whats the weather in Porto this weekend", "porto"),
        # Sloppy user-style casing and punctuation.
        ("weather in tallinn for the coming week", "tallinn"),
        ("WEATHER IN BUDAPEST TOMORROW!!", "budapest"),
    ),
)
def test_a_stated_city_survives_its_time_qualifier(user_text: str, expected: str) -> None:
    assert expected in _planned_locations(user_text)


def test_a_misspelled_weather_word_still_finds_the_city() -> None:
    """Was a strict xfail recording the gap; the marker's own instruction was to remove it once
    "the extractor and the classifier agree on spelling tolerance". They now do: every weather-word
    list in `tools/web/web_research.py` reads from `_WEATHER_WORD_RE`.

    Closed 2026-08-18 after the gap reached a user. The classifier accepted "wheather", the
    extractor did not, so the turn was classified LIVE_DATA/multipart/weather and then built NO
    plan -- and the operator was told "no current weather results came back" for three cities.
    """
    assert "tallinn" in _planned_locations("what is the wheather in tallinn?")
    # The shape that actually reached the user: several cities, misspelled weather word.
    assert _planned_locations("wheather in vilnius, berlin and rome") == ["vilnius", "berlin", "rome"]


def test_every_city_in_a_multi_city_request_survives() -> None:
    assert _planned_locations("what is the weather in Kaunas and Riga tomorrow?") == [
        "kaunas",
        "riga",
    ]


@pytest.mark.parametrize(
    "user_text",
    (
        # No city named: the honest outcome is no weather subtask, never an invented location.
        "what is the weather forecast for the next week?",
        "and for the next week?",
        "what is the weather?",
        # A bare temporal follow-up names no place either.
        "and tomorrow?",
    ),
)
def test_a_request_that_names_no_city_never_gets_one(user_text: str) -> None:
    assert _planned_locations(user_text) == []


@pytest.mark.parametrize(
    "user_text",
    (
        # A place name that CONTAINS a time word must not be eaten by the qualifier strip.
        "what is the weather in Sunday River?",
        "what is the weather in Christmas Island?",
        # The same place name alongside a real qualifier, which is the case that could eat it.
        "what is the weather in Sunday River tomorrow?",
        "what is the weather in Christmas Island for next week?",
    ),
)
def test_a_place_name_containing_a_time_word_is_not_stripped(user_text: str) -> None:
    found = {name for name, _confident in _extract_weather_locations_with_confidence(user_text)}
    assert found, f"the place name was stripped away entirely: {user_text!r}"
    assert any("sunday" in name or "christmas" in name for name in found), found


def test_sabotage_restoring_the_trailing_only_strip_loses_the_city(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Prove the qualifier vocabulary is load-bearing, not decoration."""

    import tools.web.web_research as web_research

    monkeypatch.setattr(
        web_research,
        "_WEATHER_TRAILING_NOISE",
        {"today", "tomorrow", "tonight", "now", "currently", "current", "forecast",
         "please", "right", "this", "week", "weekend"},
    )
    found = _extract_weather_locations_with_confidence(
        "what is the weather in Vilnius for next week?"
    )

    assert list(found) == []


def test_sabotage_removing_the_medial_qualifier_rule_loses_the_city(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import re

    import tools.web.web_research as web_research

    monkeypatch.setattr(web_research, "_LEADING_TIME_QUALIFIER_RE", re.compile(r"(?!x)x"))
    found = _extract_weather_locations_with_confidence("what is the weather tomorrow in Vilnius?")

    assert list(found) == []
