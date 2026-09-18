"""Asking for two places must plan two lookups, however the sentence repeats itself.

Found by a live drive on 2026-08-14 while proving the Agents panel -- one agent row appeared for a
two-city question. Reproduced offline, no model and no network::

    build_live_data_plan("give me the weather in Paris and the weather in Berlin")
      -> 1 subtask, location 'paris'

The turn then answers Paris and reports "1/1 succeeded". The accounting is truthful and the
PLANNING is incomplete, which is the worst combination: nothing anywhere says a request was dropped,
so neither the user nor any honesty gate can notice.

ROOT CAUSE, and it is not about weather words. `_weather_clause_on_line` returns the FIRST clause on
a line. A line can govern more than once, and the trailing-prose trim -- doing its job correctly --
then cuts the surviving clause down to "Paris and the", so Berlin is never seen at all. `finditer`
cannot fix it either: every clause pattern captures `(.+)$`, so the first match consumes the rest of
the line and leaves nothing to iterate. The scan has to advance past each TRIMMED clause and search
the remainder, which is exactly the text the trim excluded.

THE DISTINCTION THESE TESTS DEFEND:

    a SHARED-verb list  ("weather in Kaunas, Tallinn and Warsaw")  is ONE clause, split by
    `_split_weather_candidates` -- and always worked;
    a REPEATED-verb list ("weather in Paris and the weather in Berlin") is TWO clauses, and was
    losing everything after the first.
"""

from __future__ import annotations

import pytest

from tools.web.web_research import _weather_clause_on_line, _weather_clauses_on_line


def _plan_locations(text: str) -> list[str]:
    from core.agent_runtime.live_data_plan import build_live_data_plan

    plan = build_live_data_plan(text, plan_id="p", attempt_id="a")
    return [str(s.arguments.get("location") or "") for s in (getattr(plan, "subtasks", ()) or ())]


# ---------------------------------------------------------------------------------------------
# G1 -- the measured reproduction, and the same lane in other wording
# ---------------------------------------------------------------------------------------------


def test_the_reported_two_city_request_plans_both() -> None:
    assert _plan_locations("give me the weather in Paris and the weather in Berlin") == ["paris", "berlin"]


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("whats the weather in Vilnius and also the weather in Tokyo", ["vilnius", "tokyo"]),
        ("the weather in Paris and the temperature in Berlin", ["paris", "berlin"]),
        ("what is the weather in Rome and show me the forecast for Cairo", ["rome", "cairo"]),
        ("forecast for Lisbon and forecast for Porto", ["lisbon", "porto"]),
        ("weather in Oslo and rain in Bergen", ["oslo", "bergen"]),
    ),
)
def test_a_repeated_governing_verb_plans_every_place(text: str, expected: list[str]) -> None:
    """None of these is the reported sentence, and none shares its cities."""

    assert _plan_locations(text) == expected, text


def test_three_repeated_clauses_all_survive() -> None:
    text = "weather in Madrid and the weather in Seville and the weather in Valencia"

    assert _plan_locations(text) == ["madrid", "seville", "valencia"]


# ---------------------------------------------------------------------------------------------
# NEGATIVE CONTROLS -- the shapes that already worked must be untouched
# ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    (
        ("weather in Kaunas, Tallinn and Warsaw", ["kaunas", "tallinn", "warsaw"]),
        ("weather in Vilnius and Tokyo", ["vilnius", "tokyo"]),
        ("the weather in Oslo", ["oslo"]),
        ("what is the weather in Reykjavik", ["reykjavik"]),
    ),
)
def test_a_shared_verb_list_is_unchanged(text: str, expected: list[str]) -> None:
    """One clause, split by `_split_weather_candidates`. This path always worked and must not move."""

    assert _plan_locations(text) == expected, text


def test_a_line_that_only_mentions_weather_still_yields_nothing() -> None:
    """The boundary `_weather_clause_on_line` exists to hold: a bare mention with no governing
    preposition is not a location clause, and scanning the line repeatedly must not weaken that."""

    assert _weather_clauses_on_line("the warmest city among the weather results") == []
    # A colon label whose trigger word is not DIRECTLY followed by the colon is not a clause.
    assert _weather_clauses_on_line("For Weather include: - condition - high - low") == []
    assert _plan_locations("summarize the weather report format") == []


def test_the_singular_helper_still_returns_the_first_clause() -> None:
    """Kept as-is deliberately: other callers and an existing sabotage test depend on its shape."""

    assert _weather_clause_on_line("weather in Oslo") == "Oslo"
    assert _weather_clause_on_line("give me the weather in Paris and the weather in Berlin") == "Paris and the"


# ---------------------------------------------------------------------------------------------
# ADVERSARIAL
# ---------------------------------------------------------------------------------------------


def test_the_scan_is_bounded() -> None:
    """A pathological line must not produce unbounded subtasks."""

    from tools.web.web_research import _MAX_WEATHER_CLAUSES_PER_LINE

    text = " and ".join("weather in Oslo" for _ in range(40))

    assert len(_weather_clauses_on_line(text)) <= _MAX_WEATHER_CLAUSES_PER_LINE


def test_the_scan_terminates_on_an_empty_capture() -> None:
    """Forward progress is guaranteed by construction; this pins it rather than trusting it."""

    assert _weather_clauses_on_line("weather in ") == []
    # Degenerate but terminating: it yields the one junk token it can see and stops, rather
    # than looping. `_split_weather_candidates` and the runner reject it downstream.
    assert _weather_clauses_on_line("weather in and weather in") == ["and"]
    assert _plan_locations("weather in and weather in") == []


def test_duplicate_places_are_not_planned_twice() -> None:
    """The extractor already dedupes; scanning a line repeatedly must not defeat that."""

    assert _plan_locations("weather in Oslo and the weather in Oslo") == ["oslo"]


def test_a_second_clause_on_a_LATER_line_still_works() -> None:
    """The per-line boundary is unchanged -- this is the multi-line shape, not the multi-clause one."""

    assert _plan_locations("weather in Paris\nweather in Berlin") == ["paris", "berlin"]
