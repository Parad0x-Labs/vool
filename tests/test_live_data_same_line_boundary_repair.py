"""THREADKEEPER Mnemosyne-FAIL repair (2026-08-07): the exact production incident remained
reproducible through real /api/chat after the first review, in a "compact single-line instruction
style" the mission's own reproduction and the first-round test suite did not cover.

Blocker 1 -- same-line instruction tail: "weather in Kaunas, Tallinn, and Warsaw. Run independent
compatible live-data requests in parallel." has no blank line, and no newline at all, between the
entity list and the instructions that follow it. `_weather_clause_on_line`'s `(.+)$` capture ran
straight through "Run independent..." with nothing to stop it, dropping Warsaw (or, in a nested
bullet-list shape flattened to prose, picking up "- Source, - Retrieval Timestamp" as a bogus
weather entity from "- Markets - Weather For Markets include: ...").

Fixed structurally, not with a blacklist of the words in this one reproduction:
- `_iter_prompt_blocks` now also splits a physical line on sentence-final punctuation and on a
  bullet dash surrounded by whitespace (` - `, the one trace a flattened markdown bullet list
  leaves behind) -- both survive `core.input_normalizer`'s whitespace-only collapsing, unlike a
  blank line. See `_split_sentence_punctuation`'s docstring for the exact abbreviation/list-marker
  rule (round 3 below replaced this round's original letter-only, then 4-letter-floor, version).

Blocker 2 -- comma-delimited weather lists: "Weather: Kaunas, Tallinn, Warsaw" (no "and") was
parsed as ONE entity, since a bare comma was previously never treated as a list separator on its
own (only alongside "and"/"&"/"/", to keep "Kaunas, Lithuania" as one place).

ROUND 2 UPDATE HISTORY (kept for the record; the current behavior is round 3's, described in
`_split_weather_candidates`'s own docstring): this round originally fixed Blocker 2 by
SEGMENT-COUNT PARITY -- an odd number of comma segments is a flat list, an even number pairs as
(city, qualifier) groups of two. Mnemosyne's SECOND review proved that wrong on live evidence: a
plain 2-city list ("Kaunas, Tallinn") merged into one composite, and a plain 4-city list ("Kaunas,
Tallinn, Warsaw, Helsinki") paired into two nonsense composites, both reaching wttr.in and
returning a plausible-looking answer for an undefined location -- parity guesses from comma COUNT
alone and cannot distinguish a 2-city list from a city+qualifier pair, because they have the
identical shape. Round 3 replaced parity with a greedy scan against a real geographic reference
set (`_GEOGRAPHIC_QUALIFIER_TERMS`) -- see `tests/test_live_data_same_line_boundary_repair.py`'s
`TestCommaDisambiguation` class below for the current, corrected behavior, and
`tests/test_live_data_mnemosyne_round3.py` for the full round-3 regression/mutation suite.
"""

from __future__ import annotations

import re

from core.agent_runtime.live_data_plan import (
    LiveDataSubtask,
    SubtaskLifecycle,
    build_live_data_plan,
)
from core.agent_runtime.live_data_runner import _run_weather_subtask
from tools.web.web_research import (
    _WEATHER_LIST_BOUNDARY_RE,
    _is_plausible_weather_location,
    _split_weather_candidates,
)

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


def _weather_entities(plan) -> list[str]:
    return [task.entity for task in plan.weather_subtasks()]


def _market_entities(plan) -> list[str]:
    return [task.entity for task in plan.market_subtasks() if task.operation == "market_quote"]


# ---------------------------------------------------------------------------
# Exact Mnemosyne reproductions.
# ---------------------------------------------------------------------------


class TestMnemosyneReproductionA:
    def test_planned_weather_identities_are_exactly_kaunas_tallinn_warsaw(self) -> None:
        plan = build_live_data_plan(MNEMOSYNE_PROMPT_A, plan_id="p", attempt_id="a")
        assert plan is not None
        assert _weather_entities(plan) == ["Kaunas", "Tallinn", "Warsaw"]

    def test_planned_market_identities_are_exactly_the_four_named_assets(self) -> None:
        plan = build_live_data_plan(MNEMOSYNE_PROMPT_A, plan_id="p", attempt_id="a")
        assert plan is not None
        assert set(_market_entities(plan)) == {"Gold", "Silver", "Bitcoin", "Binancecoin"}

    def test_no_instruction_fragment_or_bullet_list_label_became_an_entity(self) -> None:
        plan = build_live_data_plan(MNEMOSYNE_PROMPT_A, plan_id="p", attempt_id="a")
        assert plan is not None
        all_entities_lower = {task.entity.lower() for task in plan.subtasks}
        banned = {
            "warsaw run independent compatible", "source", "current temperature",
            "- source, - retrieval timestamp", "markets include", "for markets include",
        }
        leaked = all_entities_lower & banned
        assert not leaked, f"instruction fragments leaked as entities: {leaked}"
        for entity in all_entities_lower:
            assert "run independent" not in entity
            assert "retrieval timestamp" not in entity
            assert "current temperature" not in entity

    def test_exactly_seven_subtasks_total(self) -> None:
        plan = build_live_data_plan(MNEMOSYNE_PROMPT_A, plan_id="p", attempt_id="a")
        assert plan is not None
        assert len(plan.subtasks) == 7


class TestMnemosyneReproductionB:
    def test_planned_weather_identities_are_exactly_vilnius_riga_helsinki(self) -> None:
        plan = build_live_data_plan(MNEMOSYNE_PROMPT_B, plan_id="p", attempt_id="a")
        assert plan is not None
        assert _weather_entities(plan) == ["Vilnius", "Riga", "Helsinki"]

    def test_helsinki_does_not_disappear_into_following_instructions(self) -> None:
        plan = build_live_data_plan(MNEMOSYNE_PROMPT_B, plan_id="p", attempt_id="a")
        assert plan is not None
        assert "Helsinki" in _weather_entities(plan)

    def test_planned_market_identities_are_exactly_the_four_named_assets(self) -> None:
        plan = build_live_data_plan(MNEMOSYNE_PROMPT_B, plan_id="p", attempt_id="a")
        assert plan is not None
        assert set(_market_entities(plan)) == {"Ethereum", "Solana", "Gold", "Silver"}


# ---------------------------------------------------------------------------
# Mandatory same-line tests (Requirement 1).
# ---------------------------------------------------------------------------


class TestMandatorySameLineTermination:
    def test_period_terminated_and_list(self) -> None:
        plan = build_live_data_plan(
            "weather in Kaunas, Tallinn, and Warsaw. Run the requests in parallel.",
            plan_id="p", attempt_id="a",
        )
        assert plan is not None
        assert _weather_entities(plan) == ["Kaunas", "Tallinn", "Warsaw"]

    def test_period_terminated_slash_list(self) -> None:
        plan = build_live_data_plan(
            "weather in Kaunas / Tallinn / Warsaw. Return exactly one table.",
            plan_id="p", attempt_id="a",
        )
        assert plan is not None
        assert _weather_entities(plan) == ["Kaunas", "Tallinn", "Warsaw"]

    def test_period_terminated_colon_comma_list(self) -> None:
        plan = build_live_data_plan(
            "Weather: Kaunas, Tallinn, Warsaw. Do not estimate missing fields.",
            plan_id="p", attempt_id="a",
        )
        assert plan is not None
        assert _weather_entities(plan) == ["Kaunas", "Tallinn", "Warsaw"]

    def test_city_country_qualifier_stays_one_entity(self) -> None:
        plan = build_live_data_plan("Weather: Kaunas, Lithuania.", plan_id="p", attempt_id="a")
        assert plan is not None
        assert _weather_entities(plan) == ["Kaunas, Lithuania"]

    def test_slash_separated_qualifier_pairs(self) -> None:
        plan = build_live_data_plan(
            "Weather: Kaunas, Lithuania / Tallinn, Estonia.", plan_id="p", attempt_id="a",
        )
        assert plan is not None
        assert _weather_entities(plan) == ["Kaunas, Lithuania", "Tallinn, Estonia"]


# ---------------------------------------------------------------------------
# Comma disambiguation (Requirement 2) -- direct unit coverage of
# `_split_weather_candidates`, independent of the full plan-building pipeline.
# ---------------------------------------------------------------------------


def _candidates(clause: str) -> list[str]:
    """Round 3: `_split_weather_candidates` now returns `(candidate, confident)` pairs -- see its
    docstring. Tests in this file that only care about the candidate STRINGS use this helper
    rather than repeating the unwrap everywhere."""
    return [candidate for candidate, _confident in _split_weather_candidates(clause)]


class TestCommaDisambiguation:
    def test_three_bare_comma_segments_is_a_flat_list(self) -> None:
        assert _candidates("kaunas, tallinn, warsaw") == ["kaunas", "tallinn", "warsaw"]

    def test_two_bare_comma_segments_is_one_qualified_place(self) -> None:
        assert _candidates("kaunas, lithuania") == ["kaunas, lithuania"]

    def test_four_bare_comma_segments_pairs_as_two_qualified_places(self) -> None:
        assert _candidates("kaunas, lithuania, tallinn, estonia") == [
            "kaunas, lithuania", "tallinn, estonia",
        ]

    def test_slash_groups_each_comma_disambiguated_independently(self) -> None:
        assert _candidates("kaunas, lithuania / tallinn, estonia") == [
            "kaunas, lithuania", "tallinn, estonia",
        ]

    def test_oxford_and_still_forces_every_comma_as_a_list_separator(self) -> None:
        assert _candidates("kaunas, tallinn, and warsaw") == ["kaunas", "tallinn", "warsaw"]

    def test_five_bare_comma_segments_odd_is_a_flat_list_of_five(self) -> None:
        assert _candidates("alpha, bravo, charlie, delta, echo") == [
            "alpha", "bravo", "charlie", "delta", "echo",
        ]

    def test_two_bare_comma_segments_that_are_both_real_cities_stay_two(self) -> None:
        """Mnemosyne review round 3, Blocker A: neither word is a recognized geographic
        qualifier, so both stand alone -- the exact case round 2's parity heuristic got wrong
        (merged into one "Kaunas, Tallinn" composite that reached wttr.in as one garbled query)."""
        assert _candidates("kaunas, tallinn") == ["kaunas", "tallinn"]

    def test_four_bare_comma_segments_with_no_qualifiers_stay_four(self) -> None:
        """The other round-2 failure: a genuine 4-city list, no qualifiers anywhere, must not be
        paired into two nonsense composites."""
        assert _candidates("kaunas, tallinn, warsaw, helsinki") == ["kaunas", "tallinn", "warsaw", "helsinki"]


# ---------------------------------------------------------------------------
# No contaminated provider calls -- the plan/runner boundary guard.
# ---------------------------------------------------------------------------


class TestNoContaminatedProviderCalls:
    def test_a_planned_entity_never_produces_a_contaminated_provider_argument(self) -> None:
        """Every weather subtask actually built from the Mnemosyne A prompt must carry ONLY one
        of the three real requested cities as its provider-facing `location` argument -- proven
        directly against the plan the runner would actually execute against."""
        plan = build_live_data_plan(MNEMOSYNE_PROMPT_A, plan_id="p", attempt_id="a")
        assert plan is not None
        for task in plan.weather_subtasks():
            location = task.arguments.get("location")
            assert location in {"kaunas", "tallinn", "warsaw"}, (
                f"contaminated provider-call argument would have been sent: {location!r}"
            )

    def test_runner_rejects_a_malformed_location_before_any_network_attempt(self) -> None:
        """Defense in depth at the execution boundary: even a hand-built subtask (simulating a
        future extraction regression) whose location is instruction-tail prose fails fast, never
        reaching `structured_weather_lookup` / the network."""
        bogus = LiveDataSubtask(
            subtask_id="p:weather:bogus", entity="Warsaw Run Independent Compatible",
            operation="weather_lookup",
            arguments={"location": "warsaw run independent compatible live-data requests in parallel"},
            required_result_fields=("condition", "source", "observed_at"),
            tool="weather", tool_intent="web.research",
        )
        outcome = _run_weather_subtask(bogus, timeout_s=1.0)
        assert outcome.state == SubtaskLifecycle.FAILED
        assert "rejected implausible location" in outcome.failure_reason


# ---------------------------------------------------------------------------
# Mutation (sabotage) tests: reverting each repair must turn a specific assertion RED.
# ---------------------------------------------------------------------------


class TestSabotageRestoreEndOfLineCapture:
    """Sabotage 1: revert `_weather_clause_on_line` to capture through the literal end of its
    OWN input line with no boundary-stop cut at all -- the pre-repair shape. Scoped to what THIS
    function is actually responsible for: a market clause on the SAME sentence-unit as a weather
    clause (no period between them for `_iter_prompt_blocks`'s sentence-splitting to have already
    separated) -- distinct from `TestSabotageDisableSameLineInstructionTailBoundary`, which
    targets the sentence/bullet-splitting layer one level up."""

    @staticmethod
    def _sabotaged_weather_clause_on_line(line: str) -> str | None:
        lowered = line.lower()
        for pattern in (
            r"\b(?:weather|forecast|temperature|rain|snow|wind|humidity)\s+(?:only\s+|just\s+|specifically\s+)?(?:in|for|at)\s+(.+)$",
            r"\b(?:weather|forecast)\s*:\s*(.+)$",
        ):
            match = re.search(pattern, lowered)
            if match:
                return match.group(1).strip()  # no _WEATHER_LIST_BOUNDARY_RE cut
        return None

    def test_sabotage_lets_a_same_sentence_market_clause_bleed_into_the_weather_capture(self) -> None:
        line = "weather in gold coast and silver spring, plus the price of gold, silver, and bitcoin"
        sabotaged = self._sabotaged_weather_clause_on_line(line)
        assert sabotaged is not None
        assert "bitcoin" in sabotaged, "sabotage should have captured past the market clause"

        # Control: the real function's boundary-stop cut still applies within one sentence unit.
        from tools.web.web_research import _weather_clause_on_line

        real = _weather_clause_on_line(line)
        assert real is not None
        assert "bitcoin" not in real
        assert "price" not in real


class TestSabotageDisableSameLineInstructionTailBoundary:
    """Sabotage 2: revert `_iter_prompt_blocks` to the pre-repair, blank-line-only boundary (no
    sentence/bullet splitting) -- Warsaw/Helsinki survival must fail for the compact single-line
    instruction style specifically."""

    @staticmethod
    def _sabotaged_iter_prompt_blocks(text: str) -> list[list[str]]:
        lines: list[str] = []
        for raw_line in str(text or "").splitlines():
            cleaned = re.sub(r"[\?\!\.]+", " ", raw_line)
            cleaned = re.sub(r"[ \t]+", " ", cleaned).strip()
            lines.append(cleaned)
        blocks: list[list[str]] = []
        current: list[str] = []
        for line in lines:
            if line:
                current.append(line)
            elif current:
                blocks.append(current)
                current = []
        if current:
            blocks.append(current)
        return blocks

    def test_sabotage_loses_warsaw_from_the_same_line_instruction_tail(self) -> None:
        import tools.web.web_research as web_research_module

        text = "weather in Kaunas, Tallinn, and Warsaw. Run the requests in parallel."
        sabotaged_blocks = self._sabotaged_iter_prompt_blocks(text)
        assert len(sabotaged_blocks) == 1, "sabotage should collapse everything into one block (no sentence split)"
        assert len(sabotaged_blocks[0]) == 1, "sabotage should collapse everything into one line"

        # Control: the real function splits into two units.
        real_blocks = web_research_module._iter_prompt_blocks(text)
        assert len(real_blocks[0]) == 2, "real function must split the sentence-terminated clause from the instruction"

        # And the end-to-end plan built from the REAL function keeps Warsaw.
        plan = build_live_data_plan(text, plan_id="p", attempt_id="a")
        assert plan is not None
        assert "Warsaw" in _weather_entities(plan)


class TestSabotageTreatCommaListAsOneRawLocation:
    """Sabotage 3: revert to the pre-repair rule (only split commas alongside 'and'/'&'/'/') --
    a bare comma list collapses into one nonsense location."""

    @staticmethod
    def _sabotaged_split(clause: str) -> list[str]:
        has_join_word = bool(re.search(r"\band\b|&|;|/", clause))
        parts = re.split(r",|;|\band\b|&|/", clause) if has_join_word else [clause]
        return [p.strip() for p in parts if p.strip()]

    def test_sabotage_merges_a_bare_comma_list_into_one_entity(self) -> None:
        sabotaged = self._sabotaged_split("kaunas, tallinn, warsaw")
        assert sabotaged == ["kaunas, tallinn, warsaw"], "sabotage should have merged the list into one string"

        # Control: the real function returns three.
        real = _candidates("kaunas, tallinn, warsaw")
        assert real == ["kaunas", "tallinn", "warsaw"]


class TestSabotageSplitEveryCommaBlindly:
    """Sabotage 4: the opposite failure mode -- split on every comma unconditionally, breaking
    the "city, qualifier" case."""

    @staticmethod
    def _sabotaged_split(clause: str) -> list[str]:
        return [p.strip() for p in clause.split(",") if p.strip()]

    def test_sabotage_splits_a_qualified_place_into_two(self) -> None:
        sabotaged = self._sabotaged_split("kaunas, lithuania")
        assert sabotaged == ["kaunas", "lithuania"], "sabotage should have wrongly split the qualifier"

        # Control: the real function keeps it as one place.
        real = _candidates("kaunas, lithuania")
        assert real == ["kaunas, lithuania"]


class TestSabotageRemoveProviderCallMembershipValidation:
    """Sabotage 5: remove the plausibility check at the runner boundary -- a malformed location
    would reach `structured_weather_lookup` (and the network) uninspected."""

    @staticmethod
    def _sabotaged_run_weather_subtask_would_call_provider(location: str) -> bool:
        # Mirrors `_run_weather_subtask` with the `_is_plausible_weather_location` guard removed:
        # every location, however malformed, proceeds straight to the fetch attempt.
        return True

    def test_sabotage_would_send_a_contaminated_location_to_the_provider(self) -> None:
        contaminated = "warsaw run independent compatible live-data requests in parallel"
        assert self._sabotaged_run_weather_subtask_would_call_provider(contaminated) is True

        # Control: the real runner rejects it before any fetch attempt.
        bogus = LiveDataSubtask(
            subtask_id="p:weather:bogus2", entity="Contaminated", operation="weather_lookup",
            arguments={"location": contaminated}, required_result_fields=(), tool="weather",
            tool_intent="web.research",
        )
        outcome = _run_weather_subtask(bogus, timeout_s=1.0)
        assert outcome.state == SubtaskLifecycle.FAILED
        assert not _is_plausible_weather_location(contaminated)


# ---------------------------------------------------------------------------
# Regression: the boundary regex itself is still reachable and doing real work (not vacuous).
# ---------------------------------------------------------------------------


def test_weather_list_boundary_regex_matches_a_bullet_flattened_instruction_fragment() -> None:
    assert _WEATHER_LIST_BOUNDARY_RE.search("plus the price of gold") is not None
