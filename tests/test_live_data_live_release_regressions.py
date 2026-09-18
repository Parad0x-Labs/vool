r"""THREADKEEPER live-release weather parser regressions (2026-08-07).

The integrated live build (`5d24af39`) exposed two real entity-contamination failures that the
previous acceptance suite missed. Both are covered here with PROVIDER-TARGET SPIES -- every test
asserts on the exact string `structured_weather_lookup` is called with, never on rendered prose,
because rendered prose is what made these look acceptable in the first place.

LIVE FAILURE 1 -- "Check the current weather in Kaunas and summarize it."
Observed provider targets: `kaunas` AND `summarize it.`

Two compounding defects in `tools.web.web_research`, both now fixed:

  1. `_split_sentence_punctuation` protected the terminal period. Its abbreviation rule was a
     SHAPE test -- "any all-letter token of <=3 characters" -- which is not a test for "is an
     abbreviation", it is a test for "is a short word", and English is full of short words that
     end sentences. "it" is 2 letters and all-alpha, so "summarize it." kept its period, the
     sentence never ended, and the weather clause ran on into the instruction. Replaced with
     `_PLACE_NAME_ABBREVIATIONS`, a closed set of REAL abbreviations that occur inside place names
     ("St", "Ft", "Mt", ...), plus the structural single-letter ("D.C.") and all-digit ("1.")
     cases. The question asked is now "is this a known abbreviation", not "is this word short".

  2. `_CLAUSE_STRUCTURE_MARKERS` was missing the third-person and first-person subject pronouns.
     "it" is the single most common way an English instruction refers back to a previous result
     ("...and summarize it", "...and rank them"), so its absence left the most common continuation
     shape entirely uncovered even though the containment mechanism itself was working.

LIVE FAILURE 2 -- "Compare the current weather in 8 European capitals and rank them warmest to
coldest." The instruction itself became a pseudo-location.

The extraction layer was CORRECT here -- it found no explicit city and returned nothing. The
defect was a LATER FALLBACK reconstructing a phantom location, exactly the possibility this
round's review named: `core.agent_runtime.fast_live_info_weather_rendering.render_weather_response`'s
legacy (untagged-notes) branch rebuilds a "location" by regex-stripping a few weather phrases out
of the RAW QUERY. Subtracting a handful of phrases from a sentence leaves a sentence, not a place
name -- live-verified to produce the exact observed string "Compare the 8 European capitals and
rank them warmest to coldest". That reconstruction consulted none of the extraction-layer
containment. It now validates its own output with `_is_plausible_weather_location` (the runtime's
existing, already-tested definition of a usable location -- reused rather than duplicated) and
fails CLOSED when it does not survive. Semantic expansion of "8 European capitals" into actual
cities is CONDUCTOR's job; deferring is correct here, guessing is not.
"""

from __future__ import annotations

from unittest import mock

from core.agent_runtime.fast_live_info_weather_rendering import render_weather_response
from core.agent_runtime.live_data_plan import build_live_data_plan
from core.agent_runtime.live_data_runner import _run_weather_subtask
from tools.web.web_research import (
    _CLAUSE_STRUCTURE_MARKERS,
    _PLACE_NAME_ABBREVIATIONS,
    _split_sentence_punctuation,
)


def _provider_targets(prompt: str) -> list[str]:
    """Every string `structured_weather_lookup` would actually be called with for `prompt`.

    Drives the REAL plan -> runner boundary with the network mocked at the provider seam, so what
    is asserted is the outbound query itself, not a rendered table that could look fine while the
    wrong thing was fetched.
    """
    plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
    if plan is None:
        return []
    targets: list[str] = []
    with mock.patch("tools.web.web_research.structured_weather_lookup") as fake_lookup:
        fake_lookup.return_value = mock.Mock(
            condition="Clear", temperature_c=20, feels_like_c=20, high_c=25, low_c=15,
            place_label="x", source_label="wttr.in", source_url="https://wttr.in/x",
            observed_at="2026-08-07T00:00:00Z",
        )
        for subtask in plan.weather_subtasks():
            _run_weather_subtask(subtask, timeout_s=1.0)
        for call in fake_lookup.call_args_list:
            targets.append(call.args[0] if call.args else call.kwargs.get("location"))
    return targets


def _planned_locations(prompt: str) -> list[str]:
    plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
    return [str(t.arguments.get("location")) for t in plan.weather_subtasks()] if plan else []


# ---------------------------------------------------------------------------
# The four exact live regressions the review specified.
# ---------------------------------------------------------------------------


class TestExactLiveRegressions:
    def test_1_summarize_it_never_reaches_the_provider(self) -> None:
        prompt = "Check the current weather in Kaunas and summarize it."
        assert _provider_targets(prompt) == ["kaunas"]

    def test_1_summarize_it_is_not_even_planned(self) -> None:
        """Defense in depth: the phantom must not exist as a planned entity either, not merely be
        filtered out before the fetch."""
        prompt = "Check the current weather in Kaunas and summarize it."
        planned = _planned_locations(prompt)
        assert planned == ["kaunas"]
        for target in planned:
            assert "summarize" not in target

    def test_2_eight_european_capitals_sends_no_weather_request_at_all(self) -> None:
        prompt = "Compare the current weather in 8 European capitals and rank them warmest to coldest."
        assert _provider_targets(prompt) == []

    def test_2_eight_european_capitals_never_renders_the_instruction_as_a_location(self) -> None:
        """The actual live defect lived in the RENDER fallback, not extraction -- so it has to be
        asserted there, with the untagged notes that fallback path really receives."""
        prompt = "Compare the current weather in 8 European capitals and rank them warmest to coldest."
        untagged_notes = [{
            "summary": "Some search blob.",
            "result_url": "https://example.com",
            "origin_domain": "example.com",
        }]
        rendered = render_weather_response(query=prompt, notes=untagged_notes)
        for phantom_fragment in ("European capitals", "rank them", "warmest to coldest", "Compare the"):
            assert phantom_fragment not in rendered, f"instruction fragment rendered as a location: {phantom_fragment!r}"
        assert "couldn't determine a specific location" in rendered

    def test_3_then_summarize_the_results(self) -> None:
        prompt = "Get weather for Kaunas and Tallinn, then summarize the results."
        assert _provider_targets(prompt) == ["kaunas", "tallinn"]

    def test_4_then_explain_rsa_encryption(self) -> None:
        prompt = "Weather for Kaunas and then explain RSA encryption."
        assert _provider_targets(prompt) == ["kaunas"]


# ---------------------------------------------------------------------------
# Prior fixed shapes stay green -- asserted at the provider seam, not just the plan.
# ---------------------------------------------------------------------------


class TestPriorLocationShapesStayGreen:
    def test_kaunas_and_tallinn(self) -> None:
        assert _provider_targets("Weather: Kaunas, Tallinn") == ["kaunas", "tallinn"]

    def test_rio_de_janeiro_not_truncated(self) -> None:
        assert _provider_targets("Weather: Rio de Janeiro") == ["rio de janeiro"]

    def test_ho_chi_minh_city_not_truncated_or_rejected(self) -> None:
        assert _provider_targets("Weather: Ho Chi Minh City") == ["ho chi minh city"]

    def test_toronto_ontario_and_tallinn_stays_two_entities(self) -> None:
        targets = _provider_targets("Weather for Toronto, Ontario and Tallinn.")
        assert targets == ["toronto, ontario", "tallinn"]
        assert "ontario" not in targets

    def test_vancouver_british_columbia(self) -> None:
        assert _provider_targets("Weather: Vancouver, British Columbia") == ["vancouver, british columbia"]

    def test_sydney_new_south_wales(self) -> None:
        assert _provider_targets("Weather: Sydney, New South Wales") == ["sydney, new south wales"]

    def test_dotted_abbreviations_survive_the_new_abbreviation_rule(self) -> None:
        """The abbreviation rule changed shape this round -- every dotted city must still work."""
        assert _provider_targets("Weather: St. Louis / Tallinn") == ["st. louis", "tallinn"]
        assert _provider_targets("Weather: St. Petersburg / Warsaw") == ["st. petersburg", "warsaw"]
        assert _provider_targets("Weather: Ft. Worth / Tallinn") == ["ft. worth", "tallinn"]
        assert _provider_targets("Weather: Washington, D.C. / Tallinn") == ["washington, d.c.", "tallinn"]

    def test_internal_period_versus_terminal_period_still_distinguished(self) -> None:
        assert _provider_targets("weather in St. Louis. Return the result.") == ["st. louis"]


# ---------------------------------------------------------------------------
# The two repaired mechanisms, tested directly.
# ---------------------------------------------------------------------------


class TestAbbreviationRuleAsksTheRightQuestion:
    def test_short_ordinary_words_no_longer_masquerade_as_abbreviations(self) -> None:
        """The heart of LIVE FAILURE 1: "it." is short and all-letter but is not an abbreviation,
        so it must end its sentence. Extra short words included to show this is a general rule,
        not a special case for the one word in the incident."""
        for sentence_ender in ("it", "sky", "bay", "us", "go"):
            text = f"weather in Kaunas and summarize {sentence_ender}. Next thing."
            pieces = _split_sentence_punctuation(text)
            assert len(pieces) >= 2, f"{sentence_ender!r} should have ended its sentence: {pieces!r}"

    def test_real_place_abbreviations_are_still_protected(self) -> None:
        for abbreviation in ("St", "Ft", "Mt"):
            pieces = _split_sentence_punctuation(f"weather in {abbreviation}. Louis")
            assert pieces == [f"weather in {abbreviation}. Louis"], f"{abbreviation!r} was wrongly split: {pieces!r}"

    def test_single_letters_and_list_markers_are_still_protected(self) -> None:
        assert _split_sentence_punctuation("Washington, D.C. area") == ["Washington, D.C. area"]
        assert _split_sentence_punctuation("1. weather in Kaunas") == ["1. weather in Kaunas"]

    def test_long_alphanumeric_token_still_ends_its_sentence(self) -> None:
        """Round 3's regression guard -- a word ending in digits is not an abbreviation."""
        pieces = _split_sentence_punctuation("weather in Zzznotarealplace1234. Next")
        assert len(pieces) == 2, pieces


class TestPronounCoverage:
    def test_the_pronouns_that_were_missing_are_present(self) -> None:
        """ANVIL final closure (2026-08-07, B1) narrowed this set: "he"/"she"/"we"/"i"/"my" and the
        remaining possessives were removed because they collide with real romanized toponyms
        ("My Tho", "She Xian", "He Xian"). What LIVE FAILURE 1 actually needs is "it" -- kept, with
        "they"/"their"/"them"/"me" for the other required continuations. See
        `tests/test_live_data_anvil_final_closure.py` for the place-name controls that forced the
        narrowing, and `_CLAUSE_STRUCTURE_MARKERS`'s own comment for the membership rule."""
        for pronoun in ("it", "they", "them", "me", "its", "their"):
            assert pronoun in _CLAUSE_STRUCTURE_MARKERS, f"{pronoun!r} missing from the closed class"

    def test_us_is_still_excluded_so_boston_us_survives(self) -> None:
        """Regression guard from the previous round: "US" is the United States' qualifier short
        form, so it must never be treated as the pronoun "us"."""
        assert "us" not in _CLAUSE_STRUCTURE_MARKERS
        assert _provider_targets("Weather: Boston, US") == ["boston, us"]

    def test_no_vocabulary_collides_with_another(self) -> None:
        from tools.web.web_research import _GEOGRAPHIC_QUALIFIER_TERMS

        assert not (_GEOGRAPHIC_QUALIFIER_TERMS & _CLAUSE_STRUCTURE_MARKERS)
        assert not (_GEOGRAPHIC_QUALIFIER_TERMS & _PLACE_NAME_ABBREVIATIONS)
        assert not (_CLAUSE_STRUCTURE_MARKERS & _PLACE_NAME_ABBREVIATIONS)


class TestRenderFallbackFailsClosed:
    def test_legitimate_single_location_still_renders(self) -> None:
        """The legacy branch must keep working for what it was actually for."""
        notes = [{"summary": "Cloudy.", "result_url": "https://example.com", "origin_domain": "example.com"}]
        assert render_weather_response(query="weather in London", notes=notes).startswith("Weather in London:")

    def test_instruction_shaped_query_defers_instead_of_naming_a_location(self) -> None:
        notes = [{"summary": "Blob.", "result_url": "https://example.com", "origin_domain": "example.com"}]
        rendered = render_weather_response(
            query="Compare the current weather in 8 European capitals and rank them warmest to coldest.",
            notes=notes,
        )
        assert "couldn't determine a specific location" in rendered

    def test_tagged_notes_path_is_untouched(self) -> None:
        """Only the untagged legacy branch changed -- the normal per-city path must be unaffected."""
        notes = [{
            "summary": "Sunny.", "result_url": "https://example.com",
            "origin_domain": "example.com", "_weather_location": "kaunas",
        }]
        rendered = render_weather_response(query="anything at all", notes=notes)
        assert "Kaunas" in rendered


# ---------------------------------------------------------------------------
# Mutation (sabotage) proof -- restore each live defect, named tests must go RED.
# ---------------------------------------------------------------------------


