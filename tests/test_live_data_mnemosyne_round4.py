r"""THREADKEEPER Mnemosyne final narrow live-data repair (2026-08-07).

A third independent review found round 3's own two new mechanisms unsafe:

Blocker 1 -- multiword locations truncated: the `tail_fallback` capitalization trim (added in
round 3 to solve trailing-prose contamination) truncated "Rio de Janeiro" to "Rio" (its lowercase
connector word "de" ended the leading capitalized run early) and, via its first-token fallback
when NOTHING was capitalized, truncated all-lowercase "new york city" / "ho chi minh city" (as
users commonly type) to "New" / "Ho".

Blocker 2 -- 4+ word locations rejected: round 3's runner guard rejected any `tail_fallback`
candidate over 3 words, regardless of whether extraction had it right. A correctly-extracted,
untruncated "Ho Chi Minh City" (4 words) was rejected purely for its length.

Blocker 3 -- unknown qualifiers invented a second entity: "Toronto, Ontario" split into two
independent provider calls ("Toronto" and "Ontario") because round 3's qualifier scan only
recognized sovereign countries and US states -- a real region it did not happen to know was
treated exactly like an unrelated second city.

Fixed, replacing the unsafe mechanisms rather than patching them further:
- `_title_case_run` and the capitalization/first-token trim in `_split_weather_candidates` are
  DELETED. A `tail_fallback` candidate (nothing but the clause's own end bounds its right edge) is
  now returned exactly as extracted, at any length, regardless of case.
- `_run_weather_subtask`'s `tail_fallback`-plus->3-word rejection is DELETED. The runner's only
  remaining before-network check is the pre-existing, unchanged `_is_plausible_weather_location`
  syntactic filter.
- `_WEATHER_LIST_BOUNDARY_RE` gains an "live-data"/"in parallel" trigger -- this system's OWN
  recurring execution-instruction vocabulary (verified against this benchmark's actual production
  prompt) -- so a clause like "Kaunas and Tallinn live-data request in parallel" is bounded BEFORE
  `_split_weather_candidates` ever runs, rather than trying to trim the entity back down
  afterward. See `tests/test_live_data_mnemosyne_round3.py::TestTrailingProse::
  test_provider_side_lookup_variant` for the documented, accepted trade-off this creates (a
  trailing continuation this vocabulary does not name is no longer excluded).
- `_GEOGRAPHIC_QUALIFIER_TERMS` gains the first-level administrative subdivisions of Canada,
  Australia, Germany, and Spain (excluding any that double as a common standalone city name, e.g.
  Berlin/Hamburg/Bremen, Madrid/Valencia/Murcia) -- the SAME closed, real-world reference
  vocabulary approach round 3 used for countries/US states, not a new mechanism. "Toronto, Ontario"
  now merges into one qualified place using real evidence ("Ontario" IS a recognized Canadian
  province), the same way "Kaunas, Lithuania" always has.

See `tools.web.web_research._split_weather_candidates`, `._WEATHER_LIST_BOUNDARY_RE`, and
`core.agent_runtime.live_data_runner._run_weather_subtask` for the full docstrings.
"""

from __future__ import annotations

from unittest import mock

from core.agent_runtime.live_data_plan import (
    SubtaskLifecycle,
    build_live_data_plan,
)
from core.agent_runtime.live_data_runner import _run_weather_subtask
from tools.web.web_research import (
    _GEOGRAPHIC_QUALIFIER_TERMS,
)


def _weather(prompt: str) -> list[str]:
    plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
    return [task.entity for task in plan.weather_subtasks()] if plan else []


def _locations(prompt: str) -> list[str]:
    plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
    return [str(t.arguments.get("location")) for t in plan.weather_subtasks()] if plan else []


def _assert_weather(prompt: str, expected: list[str]) -> None:
    actual = _weather(prompt)
    assert sorted(e.lower() for e in actual) == sorted(e.lower() for e in expected), (
        f"{prompt!r} -> {actual!r}, expected {expected!r}"
    )


# ---------------------------------------------------------------------------
# Blocker 1 -- multiword locations, every case, title/UPPER/lowercase.
# ---------------------------------------------------------------------------

_MULTIWORD_LOCATIONS = (
    "New York City", "Los Angeles", "San Francisco", "Buenos Aires", "Rio de Janeiro",
    "Mexico City", "Ho Chi Minh City", "Kuala Lumpur", "Cape Town", "Las Vegas",
    "New Delhi", "São Paulo",
)


class TestMultiwordLocationsNeverTruncated:
    def test_title_case(self) -> None:
        for place in _MULTIWORD_LOCATIONS:
            _assert_weather(f"Weather: {place}", [place])

    def test_upper_case(self) -> None:
        for place in _MULTIWORD_LOCATIONS:
            _assert_weather(f"Weather: {place.upper()}", [place])

    def test_lower_case(self) -> None:
        for place in _MULTIWORD_LOCATIONS:
            _assert_weather(f"weather in {place.lower()}", [place])

    def test_lowercase_connector_word_preserved(self) -> None:
        """"Rio de Janeiro" contains a genuine lowercase connector ("de") -- the exact shape the
        old capitalization trim destroyed by stopping its leading-capitalized-run scan there."""
        locations = _locations("Weather: Rio de Janeiro")
        assert locations == ["rio de janeiro"]

    def test_all_lowercase_first_token_no_longer_wins(self) -> None:
        """All-lowercase input has NOTHING capitalized -- the old first-token fallback kept only
        the first word regardless. "new york city" and "ho chi minh city" must come back whole."""
        assert _locations("weather in new york city") == ["new york city"]
        assert _locations("weather in ho chi minh city") == ["ho chi minh city"]


# ---------------------------------------------------------------------------
# Blocker 2 -- word count is not validation; drive the FULL pipeline including the runner.
# ---------------------------------------------------------------------------


class TestNoWordCountRejection:
    def test_four_word_location_is_not_rejected_by_extraction_confidence(self) -> None:
        plan = build_live_data_plan("Weather: Ho Chi Minh City", plan_id="p", attempt_id="a")
        assert plan is not None
        subtasks = plan.weather_subtasks()
        assert len(subtasks) == 1
        assert subtasks[0].arguments["location"] == "ho chi minh city"
        assert subtasks[0].arguments["extraction_confidence"] == "tail_fallback"

    def test_four_word_location_reaches_the_provider_call_intact(self) -> None:
        """End-to-end through `_run_weather_subtask` (mocked network, not just the plan) -- proves
        the runner does not independently re-reject a long `tail_fallback` candidate before ever
        calling the provider."""
        plan = build_live_data_plan("Weather: Ho Chi Minh City", plan_id="p", attempt_id="a")
        assert plan is not None
        subtask = plan.weather_subtasks()[0]
        with mock.patch("tools.web.web_research.structured_weather_lookup") as fake_lookup:
            fake_lookup.return_value = mock.Mock(
                condition="Clear", source="wttr.in", observed_at="2026-08-07T00:00:00Z",
                as_dict=lambda: {"condition": "Clear", "source": "wttr.in", "observed_at": "2026-08-07T00:00:00Z"},
            )
            outcome = _run_weather_subtask(subtask, timeout_s=1.0)
        fake_lookup.assert_called_once()
        called_location = fake_lookup.call_args.args[0] if fake_lookup.call_args.args else fake_lookup.call_args.kwargs.get("location")
        assert called_location == "ho chi minh city", "the FULL, untruncated location must reach the provider call"
        assert outcome.state == SubtaskLifecycle.SUCCEEDED, outcome.failure_reason


# ---------------------------------------------------------------------------
# Blocker 3 -- unknown/known-subnational qualifiers merge, they do not invent a second entity.
# ---------------------------------------------------------------------------

_QUALIFIED_PAIRS = (
    ("Toronto", "Ontario"),
    ("Vancouver", "British Columbia"),
    ("Sydney", "New South Wales"),
    ("Munich", "Bavaria"),
    ("Barcelona", "Catalonia"),
    ("Dubai", "UAE"),
)


class TestUnknownQualifiersDoNotInventEntities:
    def test_each_named_pair_stays_one_requested_entity(self) -> None:
        for city, region in _QUALIFIED_PAIRS:
            expected = f"{city}, {region}"
            _assert_weather(f"Weather: {expected}", [expected])
            locations = _locations(f"Weather: {expected}")
            assert locations == [f"{city.lower()}, {region.lower()}"], f"{expected!r} -> {locations!r}"
            assert city.lower() not in locations
            assert region.lower() not in locations

    def test_slash_separated_qualified_pair_alongside_a_plain_city(self) -> None:
        _assert_weather(
            "Weather: Vancouver, British Columbia / Tallinn",
            ["Vancouver, British Columbia", "Tallinn"],
        )

    def test_recognizes_the_new_subnational_regions_directly(self) -> None:
        for region in ("ontario", "british columbia", "new south wales", "bavaria", "catalonia", "uae"):
            assert region in _GEOGRAPHIC_QUALIFIER_TERMS, f"{region!r} should be a recognized qualifier"

    def test_still_does_not_recognize_ordinary_cities_as_qualifiers(self) -> None:
        for city in ("toronto", "vancouver", "sydney", "munich", "barcelona", "dubai"):
            assert city not in _GEOGRAPHIC_QUALIFIER_TERMS


# ---------------------------------------------------------------------------
# Original matrix, still clean.
# ---------------------------------------------------------------------------


class TestOriginalMatrixStillClean:
    def test_original_incident_kaunas_tallinn_warsaw(self) -> None:
        _assert_weather(
            "weather in Kaunas, Tallinn, and Warsaw. Run independent compatible live-data "
            "requests in parallel.",
            ["Kaunas", "Tallinn", "Warsaw"],
        )

    def test_original_incident_vilnius_riga_helsinki(self) -> None:
        _assert_weather("Markets: Ethereum, Solana, gold, silver. Weather: Vilnius, Riga, Helsinki.", ["Vilnius", "Riga", "Helsinki"])

    def test_plain_two_city_list(self) -> None:
        _assert_weather("Weather: Kaunas, Tallinn", ["Kaunas", "Tallinn"])

    def test_plain_four_city_list(self) -> None:
        _assert_weather("Weather: Kaunas, Tallinn, Warsaw, Helsinki", ["Kaunas", "Tallinn", "Warsaw", "Helsinki"])

    def test_dotted_st_louis(self) -> None:
        _assert_weather("Weather: St. Louis", ["St. Louis"])

    def test_dotted_st_petersburg(self) -> None:
        _assert_weather("Weather: St. Petersburg", ["St. Petersburg"])

    def test_dotted_washington_dc(self) -> None:
        _assert_weather("Weather: Washington, D.C.", ["Washington, D.C."])


# ---------------------------------------------------------------------------
# Trailing prose, still bounded.
# ---------------------------------------------------------------------------


class TestTrailingProseStillBounded:
    def test_new_york_city_and_tallinn_live_data_request(self) -> None:
        _assert_weather(
            "weather in new york city and tallinn live-data request in parallel",
            ["New York City", "Tallinn"],
        )

    def test_rio_de_janeiro_and_kaunas_sentence_boundary(self) -> None:
        _assert_weather(
            "weather in rio de janeiro and kaunas. return one table.",
            ["Rio de Janeiro", "Kaunas"],
        )

    def test_ho_chi_minh_city_slash_tallinn(self) -> None:
        _assert_weather("weather in ho chi minh city / tallinn", ["Ho Chi Minh City", "Tallinn"])


# ---------------------------------------------------------------------------
# Network-target evidence: requested / planned / provider-call identity for every mandatory case.
# ---------------------------------------------------------------------------


class TestNetworkTargetEvidence:
    def test_requested_planned_and_provider_identity_agree(self) -> None:
        cases = [
            ("Weather: Rio de Janeiro", "rio de janeiro"),
            ("weather in new york city", "new york city"),
            ("Weather: Ho Chi Minh City", "ho chi minh city"),
            ("Weather: Toronto, Ontario", "toronto, ontario"),
        ]
        for prompt, expected_location in cases:
            plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
            assert plan is not None
            subtasks = plan.weather_subtasks()
            assert len(subtasks) == 1, f"{prompt!r} -> {[t.arguments for t in subtasks]!r}"
            args = subtasks[0].arguments
            assert args["requested_text"] == expected_location, f"{prompt!r} requested_text mismatch"
            assert args["location"] == expected_location, f"{prompt!r} planned location mismatch"

    def test_toronto_ontario_never_generates_two_independent_requested_locations(self) -> None:
        locations = _locations("Weather: Toronto, Ontario")
        assert locations == ["toronto, ontario"]
        assert "toronto" not in locations
        assert "ontario" not in locations

    def test_new_york_city_provider_target_is_not_truncated(self) -> None:
        locations = _locations("weather in new york city")
        assert locations == ["new york city"]
        assert "new" not in locations


# ---------------------------------------------------------------------------
# Mutation (sabotage) tests.
# ---------------------------------------------------------------------------


def _sabotaged_title_case_run(tokens: list[str]) -> list[str]:
    run: list[str] = []
    for token in tokens:
        first_alpha = next((ch for ch in token if ch.isalpha()), "")
        if not first_alpha or not first_alpha.isupper():
            break
        run.append(token)
    return run


