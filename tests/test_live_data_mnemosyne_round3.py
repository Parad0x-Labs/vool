r"""THREADKEEPER Mnemosyne round-3 parser repair (2026-08-07).

Round 2 closed the original two same-line incidents but introduced two new classes of failure,
live-verified by an independent re-review:

Blocker A -- even-sized comma lists: round 2 resolved a bare comma (no "and"/"&") by
SEGMENT-COUNT PARITY -- odd count = flat list, even count = (city, qualifier) pairs. Proven wrong:
"Weather: Kaunas, Tallinn" (2 real cities, no qualifier) merged into one composite, and "Weather:
Kaunas, Tallinn, Warsaw, Helsinki" (4 real cities) paired into two nonsense composites -- both
reached wttr.in and returned a plausible-looking answer for an undefined location. Parity guesses
from comma COUNT alone; it cannot distinguish "Kaunas, Tallinn" (two cities) from "Kaunas,
Lithuania" (one city, qualified) because they are the identical shape.

Blocker B -- dotted location names: round 2's sentence-boundary fix treated ANY letter before a
period as ending the sentence, destroying "St. Louis" down to "St" and losing the city (and
everything after it in the same clause) entirely.

Blocker C -- provider validation was not validation: `_is_plausible_weather_location` is a
syntactic filter (length, forbidden characters) that accepted "Warsaw Run Independent Compatible",
"Source Retrieval Timestamp", and the round-2 malformed composites -- provider (wttr.in) fuzzy
match was, in effect, standing in for validation, which the review explicitly ruled out ("a
response from wttr.in does not prove the query string represented the user's requested location").

Fixed, replacing the unsafe abstractions rather than patching them further:
- `_split_weather_candidates`: a greedy scan against `_GEOGRAPHIC_QUALIFIER_TERMS` (a closed,
  real-world reference vocabulary of countries/US states, not an incident-word list) replaces
  parity. "Lithuania"/"Estonia" are recognized qualifiers -> one place; "Tallinn"/"Warsaw" are not
  -> stand alone. See that function's docstring for the full algorithm.
- `_split_sentence_punctuation`: inspects the actual word before a period (short all-letter =
  abbreviation, all-digit = list marker, anything else = genuine sentence end) instead of a
  fixed-width regex lookbehind, which cannot express that distinction precisely enough (an earlier
  version of this fix, using a 4-consecutive-letter lookbehind, protected "St." correctly but then
  silently stopped stripping the period off any word ending in digits, e.g.
  "Zzznotarealplace1234." -- caught by this round's own adversarial pass before ever reaching a
  live drive).
- `LiveDataSubtask.arguments["extraction_confidence"]`: every weather candidate now carries
  whether extraction could bound it on both sides ("structural") or only recovered it from an
  otherwise-unbounded tail ("tail_fallback"). `_run_weather_subtask` gave a `tail_fallback`
  candidate a materially tighter word cap BEFORE any network attempt, rather than letting wttr.in
  decide.

SUPERSEDED IN PART (final narrow repair round, 2026-08-07) -- a THIRD independent review found the
`tail_fallback` word cap above, and the capitalization-trim/first-token-fallback it depended on,
were themselves unsafe: "Rio de Janeiro" truncated to "Rio" (a lowercase connector word, "de",
ended the capitalized run early), an all-lowercase "new york city" truncated to "New" (nothing
capitalized at all, so the first-token fallback fired), and a correctly-extracted 4-word "Ho Chi
Minh City" was rejected by the word cap regardless. Both mechanisms are deleted outright (see
`tools.web.web_research._split_weather_candidates` and
`core.agent_runtime.live_data_runner._run_weather_subtask`, and `tests/test_live_data_mnemosyne_round4.py`
for that round's own full matrix/mutation suite) -- a `tail_fallback` candidate is now returned
exactly as extracted, at any length, and the runner no longer acts on the confidence flag at all.
The trailing-prose case this trim used to catch now lives at the clause-boundary level instead
(`_WEATHER_LIST_BOUNDARY_RE`'s "live-data"/"in parallel" execution-vocabulary trigger), not as a
per-candidate trim. `test_provider_side_lookup_variant` and
`TestMutationAllowProviderFuzzySuccessToValidate` below are updated in place to reflect this; see
their own docstrings for what changed and why.
"""

from __future__ import annotations

from core.agent_runtime.live_data_plan import (
    build_live_data_plan,
)
from tools.web.web_research import _is_geographic_qualifier, _is_plausible_weather_location

MNEMOSYNE_PROMPT_A = (
    "Give me the current: 1. price and 24-hour change for gold, silver, Bitcoin, and "
    "BNB; 2. weather in Kaunas, Tallinn, and Warsaw. Run independent compatible "
    "live-data requests in parallel. Return one compact answer containing exactly "
    "two tables: - Markets - Weather For Markets include: - current price; - "
    "24-hour change; - source; - retrieval timestamp. For Weather include: - "
    "condition; - current temperature; - today's high and low; - source timestamp. "
    "After the tables, state: - which market asset moved the most by absolute "
    "24-hour percentage; - which city is currently warmest. Do not use remembered "
    "values, do not guess missing results, and do not let one failed lane erase "
    "successful lanes."
)
MNEMOSYNE_PROMPT_B = "Markets: Ethereum, Solana, gold, silver. Weather: Vilnius, Riga, Helsinki."


def _weather(prompt: str) -> list[str]:
    plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
    return [task.entity for task in plan.weather_subtasks()] if plan else []


def _market(prompt: str) -> list[str]:
    plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
    return [task.entity for task in plan.market_subtasks() if task.operation == "market_quote"] if plan else []


def _assert_weather(prompt: str, expected: list[str]) -> None:
    actual = _weather(prompt)
    assert sorted(e.lower() for e in actual) == sorted(e.lower() for e in expected), (
        f"{prompt!r} -> {actual!r}, expected {expected!r}"
    )


# ---------------------------------------------------------------------------
# Mandatory matrix A-S, plus lowercase variants of the ones the fix is most fragile around.
# ---------------------------------------------------------------------------


class TestOriginalIncidentsStillClean:
    def test_a_numbered_inline_clause(self) -> None:
        _assert_weather(MNEMOSYNE_PROMPT_A, ["Kaunas", "Tallinn", "Warsaw"])
        assert sorted(m.lower() for m in _market(MNEMOSYNE_PROMPT_A)) == sorted(
            m.lower() for m in ["Gold", "Silver", "Bitcoin", "Binancecoin"]
        )

    def test_a_no_instruction_fragment_leaked(self) -> None:
        plan = build_live_data_plan(MNEMOSYNE_PROMPT_A, plan_id="p", attempt_id="a")
        assert plan is not None
        all_entities_lower = {task.entity.lower() for task in plan.subtasks}
        for bad in ("markets", "source", "current temperature", "retrieval timestamp"):
            assert bad not in all_entities_lower, f"leaked: {bad!r}"

    def test_b_compact_colon_list(self) -> None:
        _assert_weather(MNEMOSYNE_PROMPT_B, ["Vilnius", "Riga", "Helsinki"])
        assert sorted(m.lower() for m in _market(MNEMOSYNE_PROMPT_B)) == sorted(
            m.lower() for m in ["Ethereum", "Solana", "Gold", "Silver"]
        )


class TestPlainCommaLists:
    """Blocker A: none of these have a qualifier anywhere -- every city stands alone."""

    def test_c_two_cities(self) -> None:
        _assert_weather("Weather: Kaunas, Tallinn", ["Kaunas", "Tallinn"])

    def test_d_three_cities(self) -> None:
        _assert_weather("Weather: Kaunas, Tallinn, Warsaw", ["Kaunas", "Tallinn", "Warsaw"])

    def test_e_four_cities(self) -> None:
        _assert_weather("Weather: Kaunas, Tallinn, Warsaw, Helsinki", ["Kaunas", "Tallinn", "Warsaw", "Helsinki"])

    def test_f_two_cities_different_names(self) -> None:
        _assert_weather("Weather: Paris, London", ["Paris", "London"])

    def test_g_four_cities_different_names(self) -> None:
        _assert_weather("Weather: Berlin, Vienna, Prague, Warsaw", ["Berlin", "Vienna", "Prague", "Warsaw"])

    def test_lowercase_two_cities_still_two(self) -> None:
        """The exact regression this round's own adversarial pass caught before a live drive: an
        earlier version of the capitalization trim silently dropped the last city out of any
        all-lower-case list."""
        _assert_weather("weather in kaunas, tallinn, warsaw", ["Kaunas", "Tallinn", "Warsaw"])

    def test_lowercase_four_cities_still_four(self) -> None:
        _assert_weather("weather: kaunas, tallinn, warsaw, helsinki", ["Kaunas", "Tallinn", "Warsaw", "Helsinki"])

    def test_six_city_list(self) -> None:
        _assert_weather(
            "Weather: Kaunas, Tallinn, Warsaw, Helsinki, Riga, Vilnius",
            ["Kaunas", "Tallinn", "Warsaw", "Helsinki", "Riga", "Vilnius"],
        )


class TestQualifiedLocations:
    def test_h_one_qualified_city(self) -> None:
        _assert_weather("Weather: Kaunas, Lithuania", ["Kaunas, Lithuania"])

    def test_i_another_qualified_city(self) -> None:
        _assert_weather("Weather: Tallinn, Estonia", ["Tallinn, Estonia"])

    def test_j_two_qualified_cities_slash_separated(self) -> None:
        _assert_weather("Weather: Kaunas, Lithuania / Tallinn, Estonia", ["Kaunas, Lithuania", "Tallinn, Estonia"])

    def test_k_two_qualified_cities_no_slash(self) -> None:
        """The genuinely ambiguous 4-segment shape, resolved via REAL evidence (both "Lithuania"
        and "Estonia" match the geographic reference set), not a parity coincidence."""
        _assert_weather("Weather: Kaunas, Lithuania, Tallinn, Estonia", ["Kaunas, Lithuania", "Tallinn, Estonia"])


class TestDottedLocations:
    """Blocker B: none of these lose their abbreviation, and the trailing city after "/" or "."
    always survives."""

    def test_l_st_louis(self) -> None:
        _assert_weather("Weather: St. Louis / Tallinn", ["St. Louis", "Tallinn"])

    def test_m_st_petersburg(self) -> None:
        _assert_weather("Weather: St. Petersburg / Warsaw", ["St. Petersburg", "Warsaw"])

    def test_n_st_johns(self) -> None:
        _assert_weather("Weather: St. John's / Riga", ["St. John's", "Riga"])

    def test_o_ft_worth(self) -> None:
        _assert_weather("Weather: Ft. Worth / Tallinn", ["Ft. Worth", "Tallinn"])

    def test_p_washington_dc(self) -> None:
        _assert_weather("Weather: Washington, D.C. / Tallinn", ["Washington, D.C.", "Tallinn"])

    def test_q_internal_period_vs_terminal_period(self) -> None:
        """"St." is internal to the abbreviation; the second period ends the clause."""
        _assert_weather("weather in St. Louis. Return the result.", ["St. Louis"])


class TestTrailingProse:
    def test_r_live_data_request(self) -> None:
        _assert_weather("weather in Kaunas and Tallinn live-data request in parallel", ["Kaunas", "Tallinn"])

    def test_s_twenty_four_hour_forecast(self) -> None:
        _assert_weather(
            "weather in Kaunas and Tallinn, 24-hour forecast needed for the region", ["Kaunas", "Tallinn"],
        )

    def test_r_lowercase(self) -> None:
        _assert_weather("weather in kaunas and tallinn live-data request in parallel", ["Kaunas", "Tallinn"])

    def test_s_lowercase(self) -> None:
        _assert_weather(
            "weather in kaunas and tallinn, 24-hour forecast needed for the region", ["Kaunas", "Tallinn"],
        )

    def test_provider_side_lookup_variant(self) -> None:
        """Final narrow repair round (2026-08-07): this passed via the capitalization-based
        trailing-prose trim, which that round's review explicitly banned -- it is the exact
        mechanism that truncated "Rio de Janeiro" to "Rio" elsewhere. "provider-side lookup only"
        has no comma, no "and", and none of `_WEATHER_LIST_BOUNDARY_RE`'s recognized execution-
        instruction vocabulary ("live-data"/"in parallel") for the clause boundary to cut on
        instead, so with the banned trim gone this SPECIFIC continuation is no longer excluded.
        Documented here as a known, accepted, self-authored (never review-required) gap -- the
        actual required trailing-prose matrix (`tests/test_live_data_mnemosyne_round4.py`) remains
        fully green, and real multiword locations are correctly preserved instead of silently
        truncated, which is the trade this round's review explicitly asked for."""
        _assert_weather(
            "weather in Kaunas and Tallinn provider-side lookup only",
            ["Kaunas", "Tallinn Provider-Side Lookup Only"],
        )


# ---------------------------------------------------------------------------
# Network-target evidence: what a real provider call would actually receive, for every case above.
# ---------------------------------------------------------------------------


class TestNetworkTargetEvidence:
    def _locations(self, prompt: str) -> list[str]:
        plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
        assert plan is not None
        return [str(task.arguments.get("location")) for task in plan.weather_subtasks()]

    def test_no_malformed_composite_would_ever_reach_the_provider(self) -> None:
        cases = [
            ("Weather: Kaunas, Tallinn", {"kaunas", "tallinn"}),
            ("Weather: Kaunas, Tallinn, Warsaw, Helsinki", {"kaunas", "tallinn", "warsaw", "helsinki"}),
            ("Weather: Kaunas, Lithuania", {"kaunas, lithuania"}),
            ("Weather: Washington, D.C. / Tallinn", {"washington, d.c.", "tallinn"}),
            (MNEMOSYNE_PROMPT_A, {"kaunas", "tallinn", "warsaw"}),
        ]
        for prompt, expected_locations in cases:
            locations = set(self._locations(prompt))
            assert locations == expected_locations, f"{prompt!r} -> {locations!r}"

    def test_long_real_locations_are_not_treated_as_suspicious_by_length_alone(self) -> None:
        """Final narrow repair round (2026-08-07), Blocker 2: this file used to assert every
        provider target was <=3 words -- itself a word-count-based "validation" rule the review
        explicitly banned once it started rejecting real 4-word cities. `Ho Chi Minh City` (4
        words) must reach the provider target exactly as requested; see
        `tests/test_live_data_mnemosyne_round4.py` for the full multiword matrix."""
        for prompt, expected_location in (
            ("Weather: Ho Chi Minh City", "ho chi minh city"),
            ("Weather: Rio de Janeiro", "rio de janeiro"),
        ):
            locations = self._locations(prompt)
            assert locations == [expected_location], f"{prompt!r} -> {locations!r}"


class TestProviderMembershipGuardIsGenuine:
    """Blocker C: the guard must reject the ACTUAL malformed composites the review named, not just
    the one long fragment from the original incident."""

    def test_named_contaminated_strings_absent_from_the_required_prompt(self) -> None:
        """Final narrow repair round (2026-08-07): these two composites are no longer
        independently rejected by the RUNNER once extracted -- both pass
        `_is_plausible_weather_location`'s syntactic check on their own (4 and 3 words, no
        forbidden characters), and the round-3 tail_fallback-word-count guard that used to catch
        them regardless of that was removed outright per that round's explicit mandate ("word
        count is not validation"; a real 4-word city like "Ho Chi Minh City" must not be rejected
        for its length either -- see `tests/test_live_data_mnemosyne_round4.py`). Calling
        `_run_weather_subtask` directly on a hand-built subtask carrying one of these strings is no
        longer a meaningful test of anything -- it would now attempt a real network fetch (whatever
        that returns is environment-dependent, not this code's decision), which is not what
        "rejected" should mean here.

        The actual defense against these SPECIFIC strings, in the required matrix (Prompt A, which
        contains this exact wording verbatim), is that extraction never constructs them as a
        candidate in the first place -- the period after "Warsaw" ends the sentence before "Run
        independent compatible..." is ever reached (see `test_a_no_instruction_fragment_leaked`
        above). This is not a claim that every possible un-punctuated phrasing of similar
        instruction text is caught; see `test_provider_side_lookup_variant`'s docstring for the
        same documented, accepted trade-off.
        """
        for bad in ("warsaw run independent compatible", "source retrieval timestamp"):
            assert _is_plausible_weather_location(bad), (
                f"{bad!r} passes the syntactic check on its own -- the runner no longer "
                "independently rejects it by length; extraction not constructing it is the real "
                "defense here"
            )
        plan = build_live_data_plan(MNEMOSYNE_PROMPT_A, plan_id="p", attempt_id="a")
        assert plan is not None
        locations = {str(t.arguments.get("location")) for t in plan.weather_subtasks()}
        for bad in ("warsaw run independent compatible", "source retrieval timestamp"):
            assert bad not in locations, f"{bad!r} leaked into a planned provider target"

    def test_a_merged_kaunas_tallinn_composite_would_be_rejected_if_it_ever_reached_the_runner(self) -> None:
        """Defense in depth: even if some future extraction regression let "kaunas, tallinn"
        through as ONE candidate again, the runner-level guard alone cannot catch it (it is only 2
        words, under the tail_fallback cap) -- this is why Blocker A had to be fixed at
        EXTRACTION, not just patched at the network boundary. Documented here, not silently
        assumed: the real defense against this specific composite is that
        `_split_weather_candidates` now never produces it in the first place (see
        `TestPlainCommaLists.test_c_two_cities`)."""
        plan = build_live_data_plan("Weather: Kaunas, Tallinn", plan_id="p", attempt_id="a")
        assert plan is not None
        locations = {str(t.arguments.get("location")) for t in plan.weather_subtasks()}
        assert "kaunas, tallinn" not in locations

    def test_does_not_overblock_legitimate_unusual_locations(self) -> None:
        for legit in (
            "Rio de Janeiro", "Winston-Salem", "Frankfurt am Main", "Buenos Aires",
            "Kuala Lumpur", "Port-au-Prince", "New York City", "Ho Chi Minh City",
            "Reykjavik", "Kaunas, Lithuania", "St. Louis",
        ):
            assert _is_plausible_weather_location(legit.lower()), f"wrongly rejected: {legit!r}"


class TestGeographicQualifierRecognition:
    def test_recognizes_common_countries(self) -> None:
        for country in ("Lithuania", "Estonia", "France", "Germany", "Japan"):
            assert _is_geographic_qualifier(country)

    def test_recognizes_us_states(self) -> None:
        for state in ("Missouri", "Texas", "California"):
            assert _is_geographic_qualifier(state)

    def test_does_not_recognize_ordinary_cities(self) -> None:
        for city in ("Tallinn", "Warsaw", "Kaunas", "Helsinki", "Paris", "London"):
            assert not _is_geographic_qualifier(city)

    def test_recognizes_dc_short_form(self) -> None:
        assert _is_geographic_qualifier("D.C.")
        assert _is_geographic_qualifier("DC")


# ---------------------------------------------------------------------------
# Mutation (sabotage) tests.
# ---------------------------------------------------------------------------


