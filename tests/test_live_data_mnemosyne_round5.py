r"""THREADKEEPER Mnemosyne final mixed-intent containment (2026-08-07).

A fourth independent review confirmed the previous round's three blockers (multiword truncation,
word-count rejection, unknown-qualifier splitting) are closed, and found one more, pre-existing
class of failure: weather extraction did not know where the requested entity LIST ended, so a
weather clause immediately followed by an unrelated instruction clause -- with no comma-count
oddity, no qualifier, nothing round 3-5 already fixed -- absorbed that instruction text as fake
locations and issued real provider calls for them ("Inspect The Provider Retry Implementation",
"Then Tell Me Which", "Run The Independent Lookups").

Blocker A -- right edge of the weather entity list: `_WEATHER_LIST_BOUNDARY_RE` (vocabulary-based,
and explicitly NOT meant to grow into an enumeration of every possible instruction phrase) is not
the whole story. Two STRUCTURAL signals now stop `_split_weather_candidates` from consuming a
neighboring instruction clause as a candidate:
- A semicolon closes a group's entity list outright, rather than being treated as another list
  separator. English uses ";" to join independent clauses, not items in one list.
- `_CLAUSE_STRUCTURE_MARKERS` (see its own docstring in `tools.web.web_research`) rejects a
  candidate OUTRIGHT -- never trims it -- if it contains a closed-class English function word (a
  WH-word, pronoun, auxiliary/modal verb, non-leading determiner, or sequence adverb) anywhere in
  it. This is a small, closed, universal grammatical category that does not grow with each new
  incident's wording -- unlike an enumeration of specific verbs ("inspect", "tell", "run",
  "execute"), which the review explicitly rejected as an approach.

Blocker B -- qualified location followed by another list item: "Toronto, Ontario and Tallinn" used
to split into THREE segments ("Toronto", "Ontario", "Tallinn") because "and" anywhere in the clause
forced every comma into a flat-list separator, bypassing the qualifier scan entirely.
`_split_weather_candidates` now applies the SAME qualifier scan uniformly regardless of whether
segments are joined by ","/";"/"and"/"&" -- "Ontario" is recognized as Toronto's qualifier the same
way it already is in "Toronto, Ontario" alone.

See `tools.web.web_research._split_weather_candidates` and `._CLAUSE_STRUCTURE_MARKERS` for the
full docstrings and rationale.

KNOWN, DOCUMENTED LIMITATION (not silently claimed as solved): a trailing instruction fragment
that contains NEITHER a semicolon boundary NOR any closed-class function word -- e.g. "and restart
nginx", "and update production servers" -- still leaks through as a phantom candidate. Closing this
fully would require either growing a content-word blacklist (explicitly rejected by this round's
review) or real dictionary/NLP disambiguation of "is this word a common English verb" (out of
scope -- that is CONDUCTOR's domain, not this narrow adapter's). See
`TestKnownRemainingLimitation` below, which documents this honestly with a real, currently-failing
characterization rather than hiding it.
"""

from __future__ import annotations

from core.agent_runtime.live_data_plan import build_live_data_plan
from tools.web.web_research import _CLAUSE_STRUCTURE_MARKERS, _split_weather_candidates


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


def _candidates(clause: str) -> list[str]:
    return [candidate for candidate, _confident in _split_weather_candidates(clause)]


# ---------------------------------------------------------------------------
# Mandatory mixed-intent matrix, cases 1-6.
# ---------------------------------------------------------------------------

KILLER_PROMPT = (
    "Check BTC and ETH prices, get weather for Kaunas and Tallinn, inspect the provider retry "
    "implementation, then tell me which market moved most, which city is warmer, and whether the "
    "retry implementation has a circuit breaker. Run independent work in parallel."
)


class TestMandatoryMixedIntentMatrix:
    def test_case_1_cross_domain_killer_prompt(self) -> None:
        _assert_weather(KILLER_PROMPT, ["Kaunas", "Tallinn"])

    def test_case_1_no_phantom_entities_anywhere_in_the_plan(self) -> None:
        plan = build_live_data_plan(KILLER_PROMPT, plan_id="p", attempt_id="a")
        assert plan is not None
        all_entities_lower = {task.entity.lower() for task in plan.subtasks}
        for phantom in (
            "inspect the provider retry implementation", "then tell me which",
            "run independent work", "parallel", "circuit breaker",
        ):
            assert phantom not in all_entities_lower, f"phantom entity leaked: {phantom!r}"

    def test_case_2_rio_de_janeiro_and_tallinn_and_run_the_lookups(self) -> None:
        _assert_weather(
            "Get weather for Rio de Janeiro and Tallinn and run the independent lookups in parallel.",
            ["Rio de Janeiro", "Tallinn"],
        )

    def test_case_3_ho_chi_minh_city_and_kaunas_semicolon_execute(self) -> None:
        _assert_weather(
            "Weather for Ho Chi Minh City and Kaunas; execute independent requests in parallel.",
            ["Ho Chi Minh City", "Kaunas"],
        )

    def test_case_4_toronto_ontario_and_tallinn_then_run_remaining_work(self) -> None:
        _assert_weather(
            "Weather for Toronto, Ontario and Tallinn, then run the remaining work in parallel.",
            ["Toronto, Ontario", "Tallinn"],
        )

    def test_case_5_vancouver_british_columbia_and_tallinn(self) -> None:
        _assert_weather("Weather for Vancouver, British Columbia and Tallinn.", ["Vancouver, British Columbia", "Tallinn"])

    def test_case_6_sydney_new_south_wales_and_kaunas(self) -> None:
        _assert_weather("Weather for Sydney, New South Wales and Kaunas.", ["Sydney, New South Wales", "Kaunas"])


# ---------------------------------------------------------------------------
# Blocker B -- qualified location followed by another list item, general form.
# ---------------------------------------------------------------------------


class TestQualifiedLocationFollowedByAnotherListItem:
    def test_qualifier_recognized_even_when_and_joins_the_next_item(self) -> None:
        assert _candidates("Toronto, Ontario and Tallinn") == ["Toronto, Ontario", "Tallinn"]

    def test_qualifier_recognized_with_multiple_and_joined_items_after_it(self) -> None:
        assert _candidates("Toronto, Ontario and Tallinn and Warsaw") == ["Toronto, Ontario", "Tallinn", "Warsaw"]

    def test_qualifier_recognized_when_it_comes_second(self) -> None:
        assert _candidates("Tallinn and Toronto, Ontario") == ["Tallinn", "Toronto, Ontario"]

    def test_two_qualified_pairs_joined_by_and(self) -> None:
        assert _candidates("Kaunas, Lithuania and Tallinn, Estonia") == ["Kaunas, Lithuania", "Tallinn, Estonia"]

    def test_flat_and_list_still_unaffected_no_qualifier_anywhere(self) -> None:
        """The unification must not change the plain flat-list case: no segment here is a
        qualifier, so this must still split into three cities exactly as before."""
        assert _candidates("Kaunas, Tallinn, and Warsaw") == ["Kaunas", "Tallinn", "Warsaw"]


# ---------------------------------------------------------------------------
# Round 3/4 regression matrix, re-confirmed.
# ---------------------------------------------------------------------------


class TestRound4RegressionMatrix:
    def test_multiword_locations_still_preserved(self) -> None:
        for place in (
            "Rio de Janeiro", "New York City", "Ho Chi Minh City", "Buenos Aires", "Kuala Lumpur",
            "Cape Town", "Las Vegas", "New Delhi", "São Paulo",
        ):
            _assert_weather(f"Weather: {place}", [place])

    def test_dotted_locations_still_preserved(self) -> None:
        _assert_weather("Weather: St. Louis", ["St. Louis"])
        _assert_weather("Weather: St. Petersburg", ["St. Petersburg"])
        _assert_weather("Weather: Washington, D.C.", ["Washington, D.C."])

    def test_plain_lists_still_flat(self) -> None:
        _assert_weather("Weather: Kaunas, Tallinn", ["Kaunas", "Tallinn"])
        _assert_weather("Weather: Kaunas, Tallinn, Warsaw, Helsinki", ["Kaunas", "Tallinn", "Warsaw", "Helsinki"])

    def test_qualified_standalone_pairs_still_merge(self) -> None:
        for city, region in (
            ("Toronto", "Ontario"), ("Vancouver", "British Columbia"), ("Sydney", "New South Wales"),
            ("Munich", "Bavaria"), ("Barcelona", "Catalonia"), ("Dubai", "UAE"),
        ):
            _assert_weather(f"Weather: {city}, {region}", [f"{city}, {region}"])


# ---------------------------------------------------------------------------
# Negative controls: legitimate location-shaped inputs an over-broad filter would wrongly reject.
# ---------------------------------------------------------------------------


class TestNegativeControlsAgainstOverBroadFiltering:
    """Every one of these contains a lowercase connector word, a hyphen, or a word that also
    happens to be a common English syllable -- proving `_CLAUSE_STRUCTURE_MARKERS` rejects based
    on genuine closed-class function words, not on "contains a short/common-looking word"."""

    def test_frankfurt_am_main(self) -> None:
        _assert_weather("Weather for Frankfurt am Main and Berlin", ["Frankfurt am Main", "Berlin"])

    def test_havana_not_confused_with_have(self) -> None:
        _assert_weather("Weather for Havana and Kaunas", ["Havana", "Kaunas"])

    def test_cannes_not_confused_with_can(self) -> None:
        _assert_weather("Weather for Cannes and Nice", ["Cannes", "Nice"])

    def test_isle_of_man(self) -> None:
        _assert_weather("Weather for the Isle of Man", ["Isle of Man"])

    def test_stratford_upon_avon(self) -> None:
        _assert_weather("Weather for Stratford-upon-Avon and Tallinn", ["Stratford-upon-Avon", "Tallinn"])

    def test_washington_dc_still_works_alongside_and(self) -> None:
        _assert_weather("Weather for Washington, D.C. and Tallinn", ["Washington, D.C.", "Tallinn"])


# ---------------------------------------------------------------------------
# Novel prose variants -- not copied verbatim from the review's own incident text.
# ---------------------------------------------------------------------------


class TestNovelProseVariantsGeneralizeByStructure:
    def test_deployment_configuration_and_cache_check(self) -> None:
        _assert_weather(
            "weather for Kaunas and Tallinn, review the deployment configuration, and check "
            "whether the cache is warm.",
            ["Kaunas", "Tallinn"],
        )

    def test_qualified_location_then_server_logs(self) -> None:
        _assert_weather("Weather for Toronto, Ontario, then check server logs.", ["Toronto, Ontario"])

    def test_quarterly_report_and_build_status(self) -> None:
        _assert_weather(
            "weather for Paris and London, summarize the quarterly report, and confirm the build passed.",
            ["Paris", "London"],
        )

    def test_semicolon_database_migration(self) -> None:
        _assert_weather(
            "weather in Warsaw and Helsinki; verify the database migration succeeded.",
            ["Warsaw", "Helsinki"],
        )

    def test_qualified_location_and_churn_analysis(self) -> None:
        _assert_weather(
            "Get weather for Munich, Bavaria and analyze the customer churn data.",
            ["Munich, Bavaria"],
        )

    def test_deployment_pipeline_status(self) -> None:
        _assert_weather(
            "weather for Kaunas and Tallinn and see if the deployment pipeline is green.",
            ["Kaunas", "Tallinn"],
        )


# ---------------------------------------------------------------------------
# Known, documented remaining limitation -- not silently claimed as solved.
# ---------------------------------------------------------------------------


class TestKnownRemainingLimitation:
    """A trailing instruction fragment containing neither a semicolon nor any closed-class
    function word is NOT excluded -- e.g. "and restart nginx" has no WH-word, pronoun, auxiliary
    verb, non-leading determiner, or sequence adverb anywhere in it. Closing this fully would
    require either a growing content-word blacklist (the review explicitly rejected this approach)
    or real semantic/dictionary disambiguation (out of scope for this narrow adapter -- CONDUCTOR's
    eventual domain). Recorded here as a characterization test of the CURRENT, accepted boundary,
    not hidden."""

    def test_content_only_trailing_instruction_is_a_known_gap(self) -> None:
        leaked = [e.lower() for e in _weather("weather for Kaunas and Tallinn and restart nginx")]
        assert "kaunas" in leaked
        assert "tallinn" in leaked
        # Documents the actual current behavior (a phantom entity DOES leak) rather than
        # asserting a false "it's fixed" outcome.
        assert any("restart" in e for e in leaked), (
            "if this now fails, the gap has been closed -- update this test's docstring and "
            "the module docstring's KNOWN LIMITATION section accordingly, do not just delete it"
        )


# ---------------------------------------------------------------------------
# Provider-target evidence.
# ---------------------------------------------------------------------------


class TestProviderTargetEvidence:
    def test_requested_planned_identity_for_every_mandatory_case(self) -> None:
        cases = [
            (KILLER_PROMPT, {"kaunas", "tallinn"}),
            (
                "Get weather for Rio de Janeiro and Tallinn and run the independent lookups in parallel.",
                {"rio de janeiro", "tallinn"},
            ),
            (
                "Weather for Ho Chi Minh City and Kaunas; execute independent requests in parallel.",
                {"ho chi minh city", "kaunas"},
            ),
            (
                "Weather for Toronto, Ontario and Tallinn, then run the remaining work in parallel.",
                {"toronto, ontario", "tallinn"},
            ),
            ("Weather for Vancouver, British Columbia and Tallinn.", {"vancouver, british columbia", "tallinn"}),
            ("Weather for Sydney, New South Wales and Kaunas.", {"sydney, new south wales", "kaunas"}),
        ]
        for prompt, expected in cases:
            locations = set(_locations(prompt))
            assert locations == expected, f"{prompt!r} -> {locations!r}"
            for bad_fragment in (
                "inspect", "implementation", "tell me", "which", "run the", "execute",
                "remaining work", "lookups",
            ):
                for location in locations:
                    assert bad_fragment not in location, f"{bad_fragment!r} leaked into {location!r}"


# ---------------------------------------------------------------------------
# Mutation (sabotage) tests.
# ---------------------------------------------------------------------------


class TestClauseStructureMarkersAreGenuinelyClosedClass:
    """Sanity check on the vocabulary itself: every entry is a function word, not a content word
    that could plausibly be part of a place name."""

    def test_no_geographic_qualifier_overlaps_with_a_clause_structure_marker(self) -> None:
        from tools.web.web_research import _GEOGRAPHIC_QUALIFIER_TERMS

        overlap = _GEOGRAPHIC_QUALIFIER_TERMS & _CLAUSE_STRUCTURE_MARKERS
        assert not overlap, f"a recognized region name collides with a clause-structure marker: {overlap!r}"
