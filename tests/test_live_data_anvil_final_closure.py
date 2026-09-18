r"""THREADKEEPER final ANVIL closure (2026-08-07).

B1 -- real place names collided with the marker vocabulary. Taking every English pronoun into
`_CLAUSE_STRUCTURE_MARKERS` regressed cities the base resolved: "My Tho" and "My Son" (Vietnam)
died on "my", "She Xian" and "He Xian" (China) on "she"/"he", and in a list "My Tho and Tallinn"
silently returned only Tallinn -- a requested sibling vanished. A pronoun now earns a place in the
TOKEN scan only when a required continuation actually depends on it; the collision-prone ones are
rejected instead as a WHOLE bare candidate (`_BARE_PRONOUN_TOKENS`), which cannot shorten a real
two-token city.

B2 -- the render gate's two halves were not independently proven. The gate is
`not _is_plausible_weather_location(...) or _has_clause_structure_marker(...)`; deleting the
plausibility half left the whole focused suite green, because every case being asserted also
carried an instruction marker. This file drives each half with inputs the OTHER half cannot catch.

B3 -- time-qualified real locations. "weather in London at 15:00" resolved to nothing: the clause
captured "London at 15:00" and the plausibility predicate refuses any string with a colon (a
label/header signal). The colon rule is correct; the input is a real city plus a time qualifier,
now stripped by numeric structure before the predicate ever sees it.

B4 -- long legitimate place names. A flat 6-word cap refused "San Carlos de Bariloche Rio Negro
Argentina". The cap is not raised globally; extra room is EARNED by structure -- the span must end
in a recognized real-world geographic qualifier and contain no clause-structure marker.

DELETED COVERAGE -- three removed `TestMutation*` classes were not fake local copies: they drove
the real weather runner and asserted the provider was NOT called. That before-fetch invariant is
re-established here under an honest name, production-facing, not relying on another file.
"""

from __future__ import annotations

from unittest import mock

from core.agent_runtime.fast_live_info_weather_rendering import render_weather_response
from core.agent_runtime.live_data_plan import LiveDataSubtask, SubtaskLifecycle, build_live_data_plan
from core.agent_runtime.live_data_runner import _run_weather_subtask
from tools.web.web_research import (
    _CLAUSE_STRUCTURE_MARKERS,
    _has_clause_structure_marker,
    _is_plausible_weather_location,
    _strip_time_qualifier,
)

_UNTAGGED_NOTES = [{"summary": "blob", "result_url": "https://e.test", "origin_domain": "e.test"}]


def _locations(prompt: str) -> list[str]:
    plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
    return [str(t.arguments.get("location")) for t in plan.weather_subtasks()] if plan else []


def _provider_targets(prompt: str) -> list[str]:
    """Exact strings `structured_weather_lookup` is called with, driving the real plan->runner."""
    plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
    if plan is None:
        return []
    with mock.patch("tools.web.web_research.structured_weather_lookup") as spy:
        spy.return_value = mock.Mock(
            condition="Clear", temperature_c=20, feels_like_c=20, high_c=25, low_c=15,
            place_label="x", source_label="wttr.in", source_url="https://wttr.in/x",
            observed_at="2026-08-07T00:00:00Z",
        )
        for subtask in plan.weather_subtasks():
            _run_weather_subtask(subtask, timeout_s=1.0)
        return [c.args[0] if c.args else c.kwargs.get("location") for c in spy.call_args_list]


def _renders_a_location(query: str) -> bool:
    return "couldn't determine a specific location" not in render_weather_response(
        query=query, notes=_UNTAGGED_NOTES,
    )


# ---------------------------------------------------------------------------
# B1 -- real place names must survive the marker vocabulary.
# ---------------------------------------------------------------------------

_COLLIDING_PLACES = ("My Tho", "My Son", "She Xian", "He Xian")


class TestPlaceNamesThatCollideWithPronouns:
    def test_each_colliding_place_resolves(self) -> None:
        for place in _COLLIDING_PLACES:
            assert _provider_targets(f"weather in {place}") == [place.lower()], place

    def test_no_requested_sibling_disappears(self) -> None:
        """The list form was the worse symptom -- Tallinn came back alone, with no signal that a
        requested city had been dropped."""
        assert _provider_targets("Weather: My Tho and Tallinn") == ["my tho", "tallinn"]
        assert _provider_targets("Weather: She Xian and Kaunas") == ["she xian", "kaunas"]
        assert _provider_targets("Weather: He Xian, Tallinn, Warsaw") == ["he xian", "tallinn", "warsaw"]

    def test_colliding_places_in_any_case(self) -> None:
        for place in _COLLIDING_PLACES:
            assert _provider_targets(f"weather in {place.lower()}") == [place.lower()]
            assert _provider_targets(f"Weather: {place.upper()}") == [place.lower()]


class TestContinuationsStayContained:
    """The controls that constrain B1: narrowing the marker set must not re-open LIVE FAILURE 1."""

    _CONTINUATIONS = (
        "summarize it", "describe it", "compare it", "explain it",
        "rank them", "show me", "tell me", "give me",
    )

    def test_every_required_continuation_is_still_contained(self) -> None:
        for continuation in self._CONTINUATIONS:
            got = _provider_targets(f"weather in Kaunas and {continuation}")
            assert got == ["kaunas"], f"{continuation!r} leaked: {got!r}"

    def test_with_terminal_punctuation_too(self) -> None:
        for continuation in self._CONTINUATIONS:
            got = _provider_targets(f"Check the current weather in Kaunas and {continuation}.")
            assert got == ["kaunas"], f"{continuation!r}. leaked: {got!r}"

    def test_a_colliding_place_and_a_continuation_in_one_request(self) -> None:
        """Both behaviours at once: the city survives, the instruction does not."""
        assert _provider_targets("weather in My Tho and summarize it") == ["my tho"]

    def test_a_marker_wrapped_in_quotes_is_still_a_marker(self) -> None:
        """The end-to-end case that makes `_has_clause_structure_marker`'s per-token punctuation
        stripping load-bearing. Sentence punctuation ("it!", "them?") is already removed upstream
        by `_split_sentence_punctuation`, so it cannot demonstrate this; QUOTES survive that far
        and reach the marker scan attached to the token. Without the stripping, "summarize 'it'"
        is not recognized and becomes a second provider target."""
        assert _provider_targets("weather in Kaunas and summarize 'it'") == ["kaunas"]
        assert _provider_targets('weather in Kaunas and rank "them"') == ["kaunas"]


# ---------------------------------------------------------------------------
# B2 -- each half of the render gate is independently load-bearing.
# ---------------------------------------------------------------------------

# Fail plausibility, contain NO clause-structure marker -- only the plausibility half can catch.
_PLAUSIBILITY_ONLY = (
    "weather in [Kaunas]",
    "weather in kaunas gmt+2",
    "weather in 12345",
    "weather in " + " ".join(["zzz"] * 12),
)
# Pass plausibility, contain a marker -- only the marker half can catch.
_MARKER_ONLY = ("Rank these cities", "Which is warmer", "Summarize the weather")


class TestRenderGateHalvesAreSeparatelyLoadBearing:
    def test_plausibility_only_inputs_carry_no_marker(self) -> None:
        """Fixture integrity: if these carried a marker, the marker half would mask a missing
        plausibility half and the mutation would falsely appear caught."""
        for query in _PLAUSIBILITY_ONLY:
            reconstructed = query.replace("weather in ", "")
            assert not _has_clause_structure_marker(reconstructed), query

    def test_plausibility_only_inputs_are_refused(self) -> None:
        for query in _PLAUSIBILITY_ONLY:
            assert not _renders_a_location(query), f"named a location for {query!r}"

    def test_marker_only_inputs_pass_the_plausibility_predicate(self) -> None:
        """Fixture integrity in the other direction: these must be shape-valid, so only the marker
        half can be what refuses them."""
        for query in _MARKER_ONLY:
            assert _is_plausible_weather_location(query), query

    def test_marker_only_inputs_are_refused(self) -> None:
        for query in _MARKER_ONLY:
            assert not _renders_a_location(query), f"named a location for {query!r}"

    def test_legitimate_locations_pass_both_halves(self) -> None:
        for query, expected in (
            ("weather in London", "London"),
            ("weather in My Tho", "My Tho"),
            ("weather in Rio de Janeiro", "Rio de Janeiro"),
        ):
            assert expected in render_weather_response(query=query, notes=_UNTAGGED_NOTES)


# ---------------------------------------------------------------------------
# B3 -- time-qualified real locations.
# ---------------------------------------------------------------------------


class TestTimeQualifiedLocations:
    def test_london_at_a_time(self) -> None:
        assert _provider_targets("weather in London at 15:00") == ["london"]

    def test_paris_at_a_time_with_a_trailing_noise_word(self) -> None:
        """The noise word sits AFTER the time, so a single fixed-order cleanup pass misses it."""
        assert _provider_targets("weather in Paris at 14:30 today") == ["paris"]

    def test_meridiem_form(self) -> None:
        assert _provider_targets("weather in Paris at 9:05 pm") == ["paris"]

    def test_bare_time_without_a_preposition(self) -> None:
        assert _provider_targets("weather in Paris 14:30") == ["paris"]

    def test_time_qualified_city_in_a_list(self) -> None:
        assert _provider_targets("Weather: London at 15:00 / Tallinn") == ["london", "tallinn"]

    def test_the_time_never_becomes_part_of_the_city(self) -> None:
        for target in _provider_targets("weather in London at 15:00"):
            assert ":" not in target and "15" not in target

    def test_a_genuine_label_colon_is_still_refused(self) -> None:
        """The colon rule must survive -- only a numeric CLOCK TIME is stripped."""
        assert not _is_plausible_weather_location("source: retrieval timestamp")
        assert _strip_time_qualifier("source: retrieval timestamp") == "source: retrieval timestamp"

    def test_strip_leaves_a_plain_city_untouched(self) -> None:
        for place in ("London", "Rio de Janeiro", "St. Louis", "My Tho"):
            assert _strip_time_qualifier(place) == place


# ---------------------------------------------------------------------------
# B4 -- long legitimate place names vs long instruction-like strings.
# ---------------------------------------------------------------------------

_LONG_LEGITIMATE = (
    "San Carlos de Bariloche Rio Negro Argentina",
    "Santiago de los Caballeros Dominican Republic",
    "Sao Jose dos Campos Sao Paulo Brazil",
    "Villa Carlos Paz Cordoba Argentina",
)
_LONG_INSTRUCTION_LIKE = (
    "rank all of these cities from warmest to coldest",
    "tell me which of the capitals is currently warmest",
    "compare the forecast and summarize it for me",
    "show the results and then explain their differences",
)


class TestLongLegitimateVersusLongInstruction:
    def test_long_fully_qualified_places_are_accepted(self) -> None:
        for place in _LONG_LEGITIMATE:
            assert _is_plausible_weather_location(place.lower()), place

    def test_the_flagship_case_reaches_the_provider_whole(self) -> None:
        assert _provider_targets("weather in San Carlos de Bariloche Rio Negro Argentina") == [
            "san carlos de bariloche rio negro argentina"
        ]

    def test_a_long_place_does_not_swallow_its_sibling(self) -> None:
        assert _provider_targets("Weather: San Carlos de Bariloche Rio Negro Argentina and Tallinn") == [
            "san carlos de bariloche rio negro argentina", "tallinn",
        ]

    def test_long_instruction_like_strings_are_still_refused(self) -> None:
        for instruction in _LONG_INSTRUCTION_LIKE:
            assert not _is_plausible_weather_location(instruction), instruction

    def test_extra_room_is_not_granted_without_a_geographic_qualifier(self) -> None:
        """Seven ordinary words with no recognized country/region earn nothing."""
        assert not _is_plausible_weather_location("alpha bravo charlie delta echo foxtrot golf")

    def test_extra_room_is_not_granted_to_an_instruction_that_names_a_country(self) -> None:
        """Both conditions are required -- mentioning a country must not buy room for prose."""
        assert not _is_plausible_weather_location("tell me which of these cities is in argentina")

    def test_the_ordinary_cap_still_applies_to_unqualified_spans(self) -> None:
        assert _is_plausible_weather_location("one two three four five six")
        assert not _is_plausible_weather_location("one two three four five six seven")


# ---------------------------------------------------------------------------
# DELETED COVERAGE -- the before-fetch invariant, re-established honestly.
# ---------------------------------------------------------------------------


class TestProviderIsNotCalledForAnUntrustedEntity:
    """Three removed `TestMutation*` classes really did exercise this: they drove the real runner
    and asserted `structured_weather_lookup` was never called. Restored under an accurate name and
    without relying on any other test file to cover it incidentally."""

    @staticmethod
    def _subtask(location: str, confidence: str = "tail_fallback") -> LiveDataSubtask:
        return LiveDataSubtask(
            subtask_id="p:weather:probe", entity=location.title(), operation="weather_lookup",
            arguments={"location": location, "extraction_confidence": confidence},
            required_result_fields=(), tool="weather", tool_intent="web.research",
        )

    def test_the_provider_is_never_called_for_an_implausible_location(self) -> None:
        for bad in ("source: retrieval timestamp", "[kaunas]", "kaunas gmt+2", "12345", "my"):
            with mock.patch("tools.web.web_research.structured_weather_lookup") as spy:
                outcome = _run_weather_subtask(self._subtask(bad), timeout_s=1.0)
            spy.assert_not_called()
            assert outcome.state == SubtaskLifecycle.FAILED, bad
            assert "rejected implausible location before fetch" in outcome.failure_reason

    def test_a_favourable_confidence_stamp_cannot_smuggle_one_past(self) -> None:
        for confidence in ("structural", "tail_fallback"):
            with mock.patch("tools.web.web_research.structured_weather_lookup") as spy:
                outcome = _run_weather_subtask(self._subtask("[kaunas]", confidence), timeout_s=1.0)
            spy.assert_not_called()
            assert outcome.state == SubtaskLifecycle.FAILED, confidence

    def test_a_valid_location_does_reach_the_provider(self) -> None:
        """Control -- otherwise the assertions above would pass with the runner simply broken."""
        with mock.patch("tools.web.web_research.structured_weather_lookup") as spy:
            spy.return_value = mock.Mock(
                condition="Clear", temperature_c=1, feels_like_c=1, high_c=1, low_c=1,
                place_label="x", source_label="w", source_url="u", observed_at="t",
            )
            outcome = _run_weather_subtask(self._subtask("my tho"), timeout_s=1.0)
        spy.assert_called_once()
        assert outcome.state == SubtaskLifecycle.SUCCEEDED


class TestVocabulariesDoNotCollide:
    def test_no_marker_is_also_an_abbreviation_or_qualifier(self) -> None:
        from tools.web.web_research import _GEOGRAPHIC_QUALIFIER_TERMS, _PLACE_NAME_ABBREVIATIONS

        assert not (_GEOGRAPHIC_QUALIFIER_TERMS & _CLAUSE_STRUCTURE_MARKERS)
        assert not (_GEOGRAPHIC_QUALIFIER_TERMS & _PLACE_NAME_ABBREVIATIONS)
        assert not (_CLAUSE_STRUCTURE_MARKERS & _PLACE_NAME_ABBREVIATIONS)
