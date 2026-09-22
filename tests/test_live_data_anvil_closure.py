r"""THREADKEEPER ANVIL review closure (2026-08-07).

ANVIL classified the previous branch state FAIL. Two in-scope defects and four coverage holes.

DEFECT 1 -- "Sta. Barbara" / "Ste. Genevieve" regressed to "Sta" / "Ste". Replacing the "<=3
letters is an abbreviation" heuristic with `_PLACE_NAME_ABBREVIATIONS` was correct in kind, but
the enumeration missed two real abbreviations the heuristic had covered incidentally. In a
delimited list the false sentence break was worse than a truncation -- "Sta. Barbara / Tallinn"
returned ONLY "sta", losing the sibling city outright. Fixed by naming the missing abbreviations,
NOT by restoring the length heuristic.

DEFECT 2 -- the legacy render reconstruction still turned short instructions into locations:
"Rank these cities", "Which is warmer", "Summarize the weather" all rendered as
"Weather in <instruction>". `_is_plausible_weather_location` is a SHAPE predicate and every one of
those passes all of its arms. Fixed by having the render gate reuse the extraction path's own
instruction vocabulary through the new shared `_has_clause_structure_marker`, plus the two
demonstrative determiners ("these"/"those") that closed class was missing -- no phrase blacklist.

COVERAGE HOLES this file closes (ANVIL proved each was untested, so each could have regressed
silently):
  1. The structure-marker rejection is documented as applying regardless of `confident`, but no
     test drove a CONFIDENT candidate containing a marker. Restricting it to `not confident` would
     have passed the entire previous suite.
  2. Only "st"/"ft"/"mt" of `_PLACE_NAME_ABBREVIATIONS` were exercised. Seven members had no test
     at all -- removing any of them was a silent regression.
  3. Only "it" of the added pronouns/possessives was exercised. Twelve had no test at all.
  4. `_is_plausible_weather_location` had no per-arm regression. Any single arm could be deleted
     with the suite still green.

Every test here is PRODUCTION-FACING: it drives the real functions and asserts on real outputs.
There are deliberately no hand-written "sabotage" classes in this file -- ANVIL correctly ruled
that a locally re-implemented copy of an old algorithm detects nothing about production. Real
source mutations are executed by `scripts/anvil_mutation_matrix.py` against these tests.
"""

from __future__ import annotations

from core.agent_runtime.fast_live_info_weather_rendering import render_weather_response
from core.agent_runtime.live_data_plan import build_live_data_plan
from tools.web.web_research import (
    _CLAUSE_STRUCTURE_MARKERS,
    _PLACE_NAME_ABBREVIATIONS,
    _has_clause_structure_marker,
    _is_plausible_weather_location,
    _split_weather_candidates,
)

_UNTAGGED_NOTES = [{"summary": "blob", "result_url": "https://e.test", "origin_domain": "e.test"}]


def _locations(prompt: str) -> list[str]:
    plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
    return [str(t.arguments.get("location")) for t in plan.weather_subtasks()] if plan else []


def _candidates(clause: str) -> list[str]:
    return [candidate for candidate, _confident in _split_weather_candidates(clause)]


def _renders_a_location(query: str) -> bool:
    """True when the legacy render branch names something as a location for `query`."""
    return "couldn't determine a specific location" not in render_weather_response(
        query=query, notes=_UNTAGGED_NOTES,
    )


# ---------------------------------------------------------------------------
# DEFECT 1 -- Sta./Ste. must survive as complete provider targets.
# ---------------------------------------------------------------------------


class TestStaSteRegression:
    def test_sta_barbara_is_a_complete_target(self) -> None:
        assert _locations("Weather: Sta. Barbara") == ["sta. barbara"]

    def test_ste_genevieve_is_a_complete_target(self) -> None:
        assert _locations("Weather: Ste. Genevieve") == ["ste. genevieve"]

    def test_sta_in_a_slash_list_does_not_swallow_the_sibling(self) -> None:
        """The regression was worse than a truncation: the false sentence break also ended the
        clause, so the city after the delimiter vanished entirely."""
        assert _locations("Weather: Sta. Barbara / Tallinn") == ["sta. barbara", "tallinn"]

    def test_ste_in_an_and_list_does_not_swallow_the_sibling(self) -> None:
        assert _locations("Weather: Ste. Genevieve and Kaunas") == ["ste. genevieve", "kaunas"]

    def test_lowercase_input_too(self) -> None:
        assert _locations("weather in sta. barbara") == ["sta. barbara"]

    def test_a_terminal_period_after_them_still_ends_the_sentence(self) -> None:
        """The abbreviation must stay an abbreviation without disabling real sentence ends."""
        assert _locations("weather in Sta. Barbara. Then summarize.") == ["sta. barbara"]


class TestEveryAbbreviationIsGuarded:
    """COVERAGE HOLE 2. One case per member of `_PLACE_NAME_ABBREVIATIONS`, so removing any single
    entry fails a named test instead of silently regressing a place name."""

    _CASES = {
        "st": "St. Louis",
        "sta": "Sta. Barbara",
        "ste": "Ste. Genevieve",
        "ft": "Ft. Worth",
        "mt": "Mt. Vernon",
        "pt": "Pt. Reyes",
        "dr": "Dr. Phillips",
        "rd": "Rd. Town",
        "ave": "Ave. Maria",
        "blvd": "Blvd. Heights",
        "hwy": "Hwy. Junction",
        "rte": "Rte. Nine",
    }

    def test_every_member_of_the_set_has_a_case_here(self) -> None:
        assert set(self._CASES) == set(_PLACE_NAME_ABBREVIATIONS), (
            "an abbreviation was added or removed without updating this guard"
        )

    def test_each_abbreviation_keeps_its_place_name_whole(self) -> None:
        for abbreviation, place in self._CASES.items():
            got = _locations(f"Weather: {place} / Tallinn")
            assert got == [place.lower(), "tallinn"], f"{abbreviation!r} ({place!r}) -> {got!r}"


# ---------------------------------------------------------------------------
# DEFECT 2 -- short instructions must not render as locations.
# ---------------------------------------------------------------------------


class TestShortInstructionsDoNotRenderAsLocations:
    def test_rank_these_cities(self) -> None:
        assert not _renders_a_location("Rank these cities")

    def test_which_is_warmer(self) -> None:
        assert not _renders_a_location("Which is warmer")

    def test_summarize_the_weather(self) -> None:
        assert not _renders_a_location("Summarize the weather")

    def test_further_short_instruction_shapes(self) -> None:
        """Not copied from ANVIL's list -- proof this generalizes by grammar, not by phrase."""
        for instruction in (
            "Compare these forecasts", "Tell me which one", "Show them to me",
            "Sort those results", "Is it raining", "What is my forecast",
        ):
            assert not _renders_a_location(instruction), f"rendered as a location: {instruction!r}"

    def test_legitimate_short_locations_still_render(self) -> None:
        for query, expected in (
            ("weather in London", "London"),
            ("weather in Paris", "Paris"),
            ("weather in New York", "New York"),
            ("weather in Rio de Janeiro", "Rio de Janeiro"),
            ("weather in Ho Chi Minh City", "Ho Chi Minh City"),
            ("weather in Sta. Barbara", "Sta. Barbara"),
        ):
            rendered = render_weather_response(query=query, notes=_UNTAGGED_NOTES)
            assert expected in rendered, f"{query!r} lost its location: {rendered!r}"


# ---------------------------------------------------------------------------
# COVERAGE HOLE 1 -- the confident flag must not bypass the structure markers.
# ---------------------------------------------------------------------------


class TestStructureMarkersApplyRegardlessOfConfidence:
    def test_a_confident_candidate_containing_a_marker_is_still_rejected(self) -> None:
        """"summarize it" sits BETWEEN two delimiters here, so `_split_weather_candidates` marks it
        confident=True. Being bounded on both sides does not make an instruction a city. Gating the
        marker check on `not confident` passes every other test in the repo -- this is the only one
        that catches it."""
        clause = "Kaunas and summarize it and Tallinn"
        pairs = _split_weather_candidates(clause)
        assert [c for c, _ in pairs] == ["Kaunas", "Tallinn"], f"got {pairs!r}"

    def test_the_rejected_confident_candidate_would_otherwise_be_confident(self) -> None:
        """Proves the test above is actually exercising the confident branch: with the marker word
        swapped for a plain token, the same position yields confident=True."""
        pairs = _split_weather_candidates("Kaunas and Riga and Tallinn")
        by_name = dict(pairs)
        assert by_name["Riga"] is True, f"middle item should be confident: {pairs!r}"

    def test_confident_marker_rejection_end_to_end(self) -> None:
        # The later request owns its tail; it cannot lend a second weather location.
        assert _locations("Weather for Kaunas and summarize it and Tallinn") == ["kaunas"]
        assert _locations("Weather for Kaunas and Tallinn and summarize it") == ["kaunas", "tallinn"]


# ---------------------------------------------------------------------------
# COVERAGE HOLE 3 -- every pronoun/possessive marker guarded.
# ---------------------------------------------------------------------------


class TestEveryPronounAndPossessiveIsGuarded:
    """ANVIL final closure (2026-08-07, B1): this set is no longer "every pronoun". Taking every
    pronoun regressed real cities -- "My Tho"/"My Son" on "my", "She Xian"/"He Xian" on "she"/"he"
    -- so a pronoun is a token-scan marker only when a required continuation actually depends on
    it. See `_CLAUSE_STRUCTURE_MARKERS`'s comment for the full rule."""

    _PRONOUNS = ("it", "they", "them", "me", "you", "him", "her", "its", "their")
    _REMOVED_FOR_COLLISION = ("he", "she", "we", "i", "my", "his", "hers", "our", "your")

    def test_each_pronoun_is_in_the_closed_class(self) -> None:
        for pronoun in self._PRONOUNS:
            assert pronoun in _CLAUSE_STRUCTURE_MARKERS, f"{pronoun!r} missing"

    def test_each_pronoun_rejects_a_candidate_containing_it(self) -> None:
        """Behavioural, not just membership: removing any one from the set fails here."""
        for pronoun in self._PRONOUNS:
            got = _locations(f"Weather for Kaunas and summarize {pronoun} forecast")
            assert got == ["kaunas"], f"{pronoun!r} failed to reject: {got!r}"

    def test_the_collision_prone_pronouns_are_absent_from_the_token_scan(self) -> None:
        """Re-adding any of these silently regresses a real city, so their ABSENCE is the
        invariant -- asserted directly so a well-meaning future addition fails here first."""
        for pronoun in self._REMOVED_FOR_COLLISION:
            assert pronoun not in _CLAUSE_STRUCTURE_MARKERS, (
                f"{pronoun!r} back in the token scan -- this regresses a real place name"
            )

    def test_they_are_still_rejected_as_a_bare_standalone_location(self) -> None:
        """Removed from the TOKEN scan, but a whole candidate that is nothing but one of them is
        still not a place -- the residue the render reconstruction leaves for "What is my
        forecast"."""
        for pronoun in (*self._PRONOUNS, *self._REMOVED_FOR_COLLISION):
            assert not _is_plausible_weather_location(pronoun), f"{pronoun!r} accepted alone"

    def test_us_is_still_usable_as_a_country_lookup(self) -> None:
        assert _is_plausible_weather_location("us")

    def test_us_stays_excluded_so_the_united_states_qualifier_survives(self) -> None:
        assert "us" not in _CLAUSE_STRUCTURE_MARKERS
        assert _locations("Weather: Boston, US") == ["boston, us"]

    def test_demonstratives_are_present_and_behavioural(self) -> None:
        for demonstrative in ("these", "those"):
            assert demonstrative in _CLAUSE_STRUCTURE_MARKERS
            assert not _renders_a_location(f"Rank {demonstrative} cities")

    def test_no_vocabulary_collides_with_another(self) -> None:
        from tools.web.web_research import _GEOGRAPHIC_QUALIFIER_TERMS

        assert not (_GEOGRAPHIC_QUALIFIER_TERMS & _CLAUSE_STRUCTURE_MARKERS)
        assert not (_GEOGRAPHIC_QUALIFIER_TERMS & _PLACE_NAME_ABBREVIATIONS)
        assert not (_CLAUSE_STRUCTURE_MARKERS & _PLACE_NAME_ABBREVIATIONS)


# ---------------------------------------------------------------------------
# COVERAGE HOLE 4 -- one meaningful regression per arm of the plausibility predicate.
# ---------------------------------------------------------------------------


class TestEveryPlausibilityArmHasARegression:
    """Each arm asserted both ways -- a rejected input AND an accepted near-neighbour -- so
    deleting the arm flips a real assertion rather than merely widening what passes."""

    def test_arm_empty(self) -> None:
        assert not _is_plausible_weather_location("")
        assert not _is_plausible_weather_location("   ")
        assert _is_plausible_weather_location("Riga")

    def test_arm_structural_characters(self) -> None:
        for bad in ("[Kaunas]", "Kaunas{1}", "Kaunas\nTallinn", "Kaunas\tTallinn", "Kaunas|Tallinn"):
            assert not _is_plausible_weather_location(bad), f"accepted {bad!r}"
        assert _is_plausible_weather_location("Kaunas")

    def test_arm_colon(self) -> None:
        assert not _is_plausible_weather_location("source: retrieval timestamp")
        assert not _is_plausible_weather_location("Kaunas:")
        assert _is_plausible_weather_location("Kaunas, Lithuania")

    def test_arm_gmt(self) -> None:
        assert not _is_plausible_weather_location("kaunas gmt+2")
        assert _is_plausible_weather_location("kaunas")

    def test_arm_queued_user_message(self) -> None:
        assert not _is_plausible_weather_location("queued user message about kaunas")
        assert _is_plausible_weather_location("kaunas")

    def test_arm_max_length(self) -> None:
        assert not _is_plausible_weather_location("a" * 61)
        assert _is_plausible_weather_location("a" * 60)

    def test_arm_max_words(self) -> None:
        assert not _is_plausible_weather_location("one two three four five six seven")
        assert _is_plausible_weather_location("one two three four five six")

    def test_arm_requires_a_letter(self) -> None:
        for bad in ("12345", "---", "42 42"):
            assert not _is_plausible_weather_location(bad), f"accepted {bad!r}"
        assert _is_plausible_weather_location("Ft. Worth")


# ---------------------------------------------------------------------------
# The shared helper itself -- one definition, two call sites.
# ---------------------------------------------------------------------------


class TestSharedInstructionMarkerHelper:
    def test_matches_a_token_with_attached_punctuation(self) -> None:
        """The released build compared the raw token "it." against the set and missed. That exact
        mismatch is what let LIVE FAILURE 1's phantom through the check meant to catch it."""
        assert _has_clause_structure_marker("summarize it.")
        assert _has_clause_structure_marker("tell me,")
        assert _has_clause_structure_marker("(which is warmer)")

    def test_does_not_match_inside_a_longer_word(self) -> None:
        """Whole-token matching, not substring -- otherwise "Ithaca" would match "it"."""
        for place in ("Ithaca", "Iceland", "Wembley", "Theydon", "Ourense", "Myrtle Beach"):
            assert not _has_clause_structure_marker(place), f"false positive on {place!r}"

    def test_real_multiword_places_contain_no_markers(self) -> None:
        for place in (
            "Rio de Janeiro", "Ho Chi Minh City", "Isle of Man", "City of London",
            "District of Columbia", "Frankfurt am Main", "Sta. Barbara", "Ste. Genevieve",
        ):
            assert not _has_clause_structure_marker(place), f"false positive on {place!r}"
