r"""THREADKEEPER live-data entity-contamination hotfix (2026-08-07).

Production incident: a multi-part LIVE_DATA prompt asking for market prices AND weather, followed
by a later paragraph of output-format instructions, corrupted the weather table with entities
pulled from the INSTRUCTION prose itself ("24-Hour Change", "Today'S High", "Source Timestamp
After The Tables") -- because entity extraction searched the whole prompt text unboundedly instead
of the paragraph/line that actually named the request. The same incident lost Warsaw entirely (its
sentence had no trailing comma/semicolon, so it was folded into an oversized rejected fragment
along with an unrelated later sentence), and picked a bogus "Source (27 C)" as the warmest city
because the corrupted entity reached the retrieval layer and got a real reading back. A follow-up
turn ("u forgot warsaw?") proved the runtime's own conversational memory still had Warsaw correct
-- the loss was in the STRUCTURED plan/render path, not memory, so the fix is structural: bounded
extraction (`_iter_prompt_blocks`, `_weather_clause_on_line`, `_market_clause_on_line`), planned-
entity reconciliation at render time (`_reconcile_to_plan`), and an explicit planned-membership
guard on the two derived-comparison functions (`warmest_city`, `largest_absolute_mover`).

Root cause (full trace, see the commit series on `fix/live-data-entity-contamination-20260807`):
1. Extraction ran on the WHOLE prompt with a `(.+)$` capture and no `re.DOTALL`/`re.MULTILINE`,
   so it fell back to whole-text scanning for any multi-paragraph prompt -- splitting every comma/
   "and" in the ENTIRE text, including the output-format paragraph.
2. `requirements_for()`'s weather recognizer (`_WEATHER_LIVE_REQUEST_RE`) required an explicit
   "weather in/for/at" trigger and never matched a bare "Weather:" section header, so a
   colon-header-shaped prompt built ZERO weather subtasks even after extraction itself was fixed.
3. The header-block extraction path preserved original casing while the inline-clause path already
   lower-cased, so `_resolve_price_alias("Ethereum")` (capitalized) missed its case-sensitive dict
   lookup and produced a duplicate `unsupported_market_entity` alongside the correctly resolved one.
4. Even bounded to one LINE, a single line can still hold both a weather clause and a market clause
   ("Weather in Gold Coast and Silver Spring, plus the price of gold..."), or two independent
   mentions of "weather" -- the captured clause needed its own boundary-stop, mirroring the one the
   market side already had.
5. `warmest_city`/`largest_absolute_mover` trusted whatever outcome list they were handed with no
   explicit check that a candidate's `subtask_id` actually belongs to the plan that was built --
   correct behavior depended entirely on every upstream stage never producing a stray outcome.
6. THE DEEPEST ONE, found only by driving the real `/api/chat` path on an isolated daemon (offline
   `build_live_data_plan()` calls looked perfectly clean the whole time): `core.input_normalizer`
   unconditionally runs `re.sub(r"\s+", " ", value)` on every turn, and `apps/vool_agent.py` used
   that COLLAPSED text (`interpreted.normalized_text`, aliased `effective_input`) as the ONLY text
   ever handed to `build_live_data_plan()`. Every paragraph/line boundary fix above operates on
   boundaries that had ALREADY been erased before extraction ever ran -- the whole prompt was
   effectively one line again by the time it reached these functions, live-verified to reproduce
   the exact same contamination ("Price", "24-Hour Change", "Source)", missing Warsaw) even with
   every fix above in place. Fixed by threading `interpreted.raw_text` (already used elsewhere in
   `apps/vool_agent.py` for the same "structure matters" reason -- response-language-policy and
   response-constraint parsing) through `_maybe_answer_live_data_turn` /
   `_answer_single_live_data_turn` / `_answer_multipart_live_data_turn` /
   `_live_data_plan_unavailable_result` as `raw_input`, used specifically at every
   `build_live_data_plan(...)` call and extraction call in that family -- `effective_input` still
   drives classification and everything else.

Every fix here is structural (paragraph/line boundaries, explicit planned-membership checks, using
text that still has its boundaries intact), never a blacklist of the specific words seen in this
one reproduction -- the adversarial cases below use entirely different vocabulary from the incident
and must pass for the same reason.
"""

from __future__ import annotations

import re
from unittest import mock

from apps.vool_agent import VoolAgent
from core.agent_runtime.live_data_plan import (
    LiveDataSubtask,
    SubtaskLifecycle,
    SubtaskOutcome,
    build_live_data_plan,
)
from core.agent_runtime.live_data_render import (
    _reconcile_to_plan,
    _weather_row,
    render_live_data_answer,
    warmest_city,
)
from tools.web.web_research import _extract_weather_locations

# The exact two production reproductions from the incident report.
PROD_PROMPT_INLINE_CLAUSE = """Run independent, compatible live-data requests in parallel. I need the current price of gold, silver, Bitcoin, and BNB; and the current weather in Kaunas, Tallinn, and Warsaw.

Return the results as exactly two tables: one for markets (asset, price, 24-hour change, source) and one for weather (city, condition, today's high and low, source), plus a source timestamp after the tables. Also include a derived statement for the largest 24-hour mover among the market assets and the warmest city among the weather results.

Do not guess missing results, and do not erase a lane just because one part of it failed."""

PROD_PROMPT_HEADER_BLOCK = """Markets:
Ethereum
Solana
gold
silver

Weather:
Vilnius
Riga
Helsinki"""

# Fragments from the incident's own OUTPUT-FORMAT INSTRUCTIONS that must never appear as an
# extracted entity -- these are the literal strings the bug produced in production.
CONTAMINATION_STRINGS = (
    "24-hour change",
    "source",
    "current temperature",
    "today's high",
    "today's low",
    "source timestamp after the tables",
    "do not guess missing results",
    "warmest city among the results",
    "warmest city among the weather results",
    "one for markets",
    "one for weather",
    "largest 24-hour mover",
)


def _weather_entities(plan) -> list[str]:
    return [task.entity for task in plan.weather_subtasks()]


def _market_entities(plan) -> list[str]:
    return [task.entity for task in plan.market_subtasks() if task.operation == "market_quote"]


# ---------------------------------------------------------------------------
# Mandatory regression: the two exact production prompts.
# ---------------------------------------------------------------------------


class TestProductionReproductionInlineClause:
    def test_planned_weather_entities_are_exactly_kaunas_tallinn_warsaw(self) -> None:
        plan = build_live_data_plan(PROD_PROMPT_INLINE_CLAUSE, plan_id="p", attempt_id="a")
        assert plan is not None
        assert _weather_entities(plan) == ["Kaunas", "Tallinn", "Warsaw"]

    def test_planned_market_entities_are_exactly_the_four_named_assets(self) -> None:
        plan = build_live_data_plan(PROD_PROMPT_INLINE_CLAUSE, plan_id="p", attempt_id="a")
        assert plan is not None
        assert set(_market_entities(plan)) == {"Gold", "Silver", "Bitcoin", "Binancecoin"}

    def test_no_unsupported_market_entities_from_instruction_prose(self) -> None:
        """The exact bug: 'the current' left "current" as a dangling unsupported market entity
        after the weather boundary cut removed the rest of the sentence."""
        plan = build_live_data_plan(PROD_PROMPT_INLINE_CLAUSE, plan_id="p", attempt_id="a")
        assert plan is not None
        unsupported = [t.entity for t in plan.subtasks if t.operation == "unsupported_market_entity"]
        assert unsupported == []

    def test_no_contamination_string_became_any_planned_entity(self) -> None:
        plan = build_live_data_plan(PROD_PROMPT_INLINE_CLAUSE, plan_id="p", attempt_id="a")
        assert plan is not None
        all_entities_lower = {task.entity.lower() for task in plan.subtasks}
        for bad in CONTAMINATION_STRINGS:
            assert bad not in all_entities_lower, f"contamination string leaked as an entity: {bad!r}"

    def test_exactly_seven_subtasks_total(self) -> None:
        plan = build_live_data_plan(PROD_PROMPT_INLINE_CLAUSE, plan_id="p", attempt_id="a")
        assert plan is not None
        assert len(plan.subtasks) == 7


class TestProductionReproductionHeaderBlock:
    """The colon-header shape ("Markets:\\nEthereum\\n..."), which is ALSO the mission's own
    canonical Invariant-2 example -- lost weather entirely (classifier gap) and duplicated
    Ethereum/Solana (case-sensitivity gap) before the fix."""

    def test_planned_weather_entities_are_exactly_vilnius_riga_helsinki(self) -> None:
        plan = build_live_data_plan(PROD_PROMPT_HEADER_BLOCK, plan_id="p", attempt_id="a")
        assert plan is not None
        assert _weather_entities(plan) == ["Vilnius", "Riga", "Helsinki"]

    def test_planned_market_entities_are_exactly_the_four_named_assets_no_duplicates(self) -> None:
        plan = build_live_data_plan(PROD_PROMPT_HEADER_BLOCK, plan_id="p", attempt_id="a")
        assert plan is not None
        entities = _market_entities(plan)
        assert set(entities) == {"Ethereum", "Solana", "Gold", "Silver"}
        assert len(entities) == 4, f"duplicate market entity: {entities}"

    def test_no_unsupported_market_entities(self) -> None:
        plan = build_live_data_plan(PROD_PROMPT_HEADER_BLOCK, plan_id="p", attempt_id="a")
        assert plan is not None
        unsupported = [t.entity for t in plan.subtasks if t.operation == "unsupported_market_entity"]
        assert unsupported == []


# ---------------------------------------------------------------------------
# Mandatory regression: the follow-up simulation ("u forgot warsaw?").
#
# The mission is explicit: do not "fix" this by adding conversation-memory recovery -- the
# structured plan itself must never lose Warsaw, independent of what the conversation remembers.
# This drives the exact same prompt through the SAME structured path twice (simulating a retry)
# and additionally simulates Warsaw's own lane FAILING at execution, proving it renders as an
# honest "unavailable" row rather than disappearing.
# ---------------------------------------------------------------------------


class TestFollowUpDoesNotLoseWarsaw:
    def test_replanning_the_same_prompt_is_stable_and_still_has_warsaw(self) -> None:
        """Simulates a retry/re-plan after a "u forgot warsaw?" follow-up: the structured plan is
        deterministic, not dependent on any memory of a prior turn."""
        first = build_live_data_plan(PROD_PROMPT_INLINE_CLAUSE, plan_id="p1", attempt_id="a1")
        second = build_live_data_plan(PROD_PROMPT_INLINE_CLAUSE, plan_id="p2", attempt_id="a2")
        assert first is not None and second is not None
        assert _weather_entities(first) == ["Kaunas", "Tallinn", "Warsaw"]
        assert _weather_entities(second) == ["Kaunas", "Tallinn", "Warsaw"]

    def test_requested_planned_and_rendered_weather_entities_are_all_exactly_the_three_cities(self) -> None:
        plan = build_live_data_plan(PROD_PROMPT_INLINE_CLAUSE, plan_id="p", attempt_id="a")
        assert plan is not None

        requested = _extract_weather_locations(PROD_PROMPT_INLINE_CLAUSE)
        assert requested == ["kaunas", "tallinn", "warsaw"]

        planned = _weather_entities(plan)
        assert planned == ["Kaunas", "Tallinn", "Warsaw"]

        kaunas_task, tallinn_task, warsaw_task = plan.weather_subtasks()
        outcomes = [
            SubtaskOutcome(subtask=kaunas_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Sunny", "temperature_c": 22.0, "source": "wttr.in", "source_url": "https://x"}),
            SubtaskOutcome(subtask=tallinn_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Cloudy", "temperature_c": 18.0, "source": "wttr.in", "source_url": "https://x"}),
            SubtaskOutcome(subtask=warsaw_task, state=SubtaskLifecycle.FAILED, failure_reason="upstream timeout"),
        ]
        rendered = render_live_data_answer(plan, outcomes)
        rendered_cities = re.findall(r"\| (\w[\w\s]*?) \|", rendered.split("**Weather**")[1])
        assert "Warsaw" in rendered_cities

    def test_a_failed_warsaw_lane_renders_unavailable_not_deleted(self) -> None:
        plan = build_live_data_plan(PROD_PROMPT_INLINE_CLAUSE, plan_id="p", attempt_id="a")
        assert plan is not None
        kaunas_task, tallinn_task, warsaw_task = plan.weather_subtasks()
        outcomes = [
            SubtaskOutcome(subtask=kaunas_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Sunny", "temperature_c": 22.0, "source": "wttr.in", "source_url": "https://x"}),
            SubtaskOutcome(subtask=tallinn_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Cloudy", "temperature_c": 18.0, "source": "wttr.in", "source_url": "https://x"}),
            SubtaskOutcome(subtask=warsaw_task, state=SubtaskLifecycle.FAILED, failure_reason="upstream timeout"),
        ]
        rendered = render_live_data_answer(plan, outcomes)
        weather_block = rendered.split("**Weather**")[1]
        assert "Warsaw" in weather_block
        assert "upstream timeout" in weather_block or "unavailable" in weather_block
        # Never a generic placeholder in place of the real requested city.
        assert "Lane As Unavailable" not in rendered
        assert "Unavailable" != "Warsaw"

    def test_a_dropped_warsaw_outcome_still_renders_its_row_via_reconciliation(self) -> None:
        """Even if the runner never produced ANY outcome for Warsaw at all (not FAILED, simply
        absent from the outcomes list), reconciliation against the plan still produces its row."""
        plan = build_live_data_plan(PROD_PROMPT_INLINE_CLAUSE, plan_id="p", attempt_id="a")
        assert plan is not None
        kaunas_task, tallinn_task, _warsaw_task = plan.weather_subtasks()
        outcomes = [
            SubtaskOutcome(subtask=kaunas_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Sunny", "temperature_c": 22.0, "source": "wttr.in", "source_url": "https://x"}),
            SubtaskOutcome(subtask=tallinn_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Cloudy", "temperature_c": 18.0, "source": "wttr.in", "source_url": "https://x"}),
        ]
        rendered = render_live_data_answer(plan, outcomes)
        weather_block = rendered.split("**Weather**")[1]
        assert "Warsaw" in weather_block


# ---------------------------------------------------------------------------
# Adversarial prompts.
# ---------------------------------------------------------------------------


class TestAdversarialCrossClassification:
    def test_gold_coast_and_silver_spring_are_weather_not_market(self) -> None:
        prompt = "Weather in Gold Coast and Silver Spring, plus the price of gold, silver, and Bitcoin."
        plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
        assert plan is not None
        weather = set(_weather_entities(plan))
        market = set(_market_entities(plan))
        assert weather == {"Gold Coast", "Silver Spring"}
        assert market == {"Gold", "Silver", "Bitcoin"}
        # No cross-classification in either direction.
        assert "Gold Coast" not in market and "Silver Spring" not in market
        assert "Gold" not in weather and "Silver" not in weather and "Bitcoin" not in weather


class TestAdversarialSlashDelimitedLists:
    def test_slash_separated_markets_and_weather_both_extract_fully(self) -> None:
        prompt = "Markets: gold/silver/Bitcoin. Weather: Kaunas/Tallinn/Warsaw."
        plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
        assert plan is not None
        assert set(_market_entities(plan)) == {"Gold", "Silver", "Bitcoin"}
        assert _weather_entities(plan) == ["Kaunas", "Tallinn", "Warsaw"]


class TestAdversarialDuplicateAliases:
    def test_bitcoin_and_btc_dedupe_to_one_market_subtask(self) -> None:
        prompt = "Price of Bitcoin, BTC, and Bitcoin again. Weather in Kaunas, Kaunas-Lithuania, and Tallinn."
        plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
        assert plan is not None
        bitcoin_subtasks = [t for t in plan.subtasks if t.operation == "market_quote" and t.arguments.get("asset_key") == "bitcoin"]
        assert len(bitcoin_subtasks) == 1, "Bitcoin/BTC must resolve to the SAME subtask, not two"

    def test_two_differently_spelled_kaunas_requests_are_both_honored_not_silently_merged(self) -> None:
        """The runtime has no geo-knowledge that 'Kaunas-Lithuania' names the same city as
        'Kaunas' -- it must never invent that equivalence, so both are planned as distinct,
        explicit lookups rather than one being silently dropped as a 'duplicate'."""
        prompt = "Price of Bitcoin, BTC, and Bitcoin again. Weather in Kaunas, Kaunas-Lithuania, and Tallinn."
        plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
        assert plan is not None
        assert _weather_entities(plan) == ["Kaunas", "Kaunas-Lithuania", "Tallinn"]


class TestAdversarialOneInvalidSiblingPerList:
    def test_invalid_market_sibling_never_erases_the_valid_ones(self) -> None:
        prompt = "Price of gold, silver, and Qwertycoin999xyz."
        plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
        assert plan is not None
        assert set(_market_entities(plan)) == {"Gold", "Silver"}
        unsupported = [t.entity for t in plan.subtasks if t.operation == "unsupported_market_entity"]
        assert unsupported == ["Qwertycoin999Xyz"]

    def test_invalid_weather_sibling_never_erases_the_valid_ones(self) -> None:
        prompt = "Weather in Kaunas, Tallinn, and Zzznotarealplace1234."
        plan = build_live_data_plan(prompt, plan_id="p", attempt_id="a")
        assert plan is not None
        assert _weather_entities(plan) == ["Kaunas", "Tallinn", "Zzznotarealplace1234"]

        kaunas_task, tallinn_task, invalid_task = plan.weather_subtasks()
        outcomes = [
            SubtaskOutcome(subtask=kaunas_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Sunny", "temperature_c": 21.0, "source": "wttr.in", "source_url": "https://x"}),
            SubtaskOutcome(subtask=tallinn_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Cloudy", "temperature_c": 15.0, "source": "wttr.in", "source_url": "https://x"}),
            SubtaskOutcome(subtask=invalid_task, state=SubtaskLifecycle.FAILED, failure_reason="no observation returned"),
        ]
        rendered = render_live_data_answer(plan, outcomes)
        assert "Kaunas" in rendered and "Sunny" in rendered
        assert "Tallinn" in rendered and "Cloudy" in rendered
        assert "Zzznotarealplace1234" in rendered
        # The successful siblings still won a real comparison.
        assert "Warmest current city: Kaunas" in rendered


# ---------------------------------------------------------------------------
# Mutation (sabotage) tests: reintroducing each fixed bug must turn a specific assertion RED,
# proving the tests actually exercise the fix rather than passing vacuously.
# ---------------------------------------------------------------------------


class TestSabotageSectionBoundaryGuard:
    """Sabotage: revert to the pre-fix unbounded whole-text scan. The section-boundary tests above
    (`TestProductionReproductionInlineClause`) must be what catches this in the real code; here we
    reproduce the SABOTAGED behavior directly to prove the contamination this fix closes was real,
    not a strawman."""

    @staticmethod
    def _sabotaged_extract_weather_locations(query: str) -> list[str]:
        """Faithfully reproduces the ORIGINAL two-stage bug, not just its first half: the bounded
        `(.+)$` regex (no `re.DOTALL`/`re.MULTILINE`) can only match within the text's own final
        newline-free suffix, so for any multi-paragraph prompt it fails to match at all -- and the
        pre-fix code's response to that failure was to fall back to scanning the WHOLE text as one
        unbounded clause. Reproducing only the failing regex (and stopping there) would silently
        return `[]` instead of the actual contamination, which is a different, weaker bug than the
        one this hotfix exists to close.
        """
        match = re.search(r"weather\s+(?:in|for|at)\s+(.+)$", query, re.IGNORECASE)
        if match:
            clause = match.group(1)
        else:
            flattened = " ".join(query.split())
            fallback = re.search(r"weather", flattened, re.IGNORECASE)
            if not fallback:
                return []
            clause = flattened[fallback.end():]
        parts = re.split(r",|\band\b|&|/", clause)
        return [p.strip().lower() for p in parts if p.strip()]

    def test_sabotaged_extraction_wrongly_pulls_instruction_prose_as_locations(self) -> None:
        sabotaged = self._sabotaged_extract_weather_locations(PROD_PROMPT_INLINE_CLAUSE)
        contaminated = [loc for loc in sabotaged if any(bad in loc for bad in ("24-hour", "source", "high"))]
        assert contaminated, "sabotage should have reproduced the contamination"

        # Control: the REAL function does not.
        real = _extract_weather_locations(PROD_PROMPT_INLINE_CLAUSE)
        assert real == ["kaunas", "tallinn", "warsaw"]
        assert not any(any(bad in loc for bad in ("24-hour", "source", "high")) for loc in real)


class TestSabotagePlannedEntityMembershipGuard:
    """Sabotage: remove the plan-membership filter from `warmest_city` -- a corrupted outcome list
    (containing an outcome for an entity the plan never produced) can then win the comparison."""

    @staticmethod
    def _sabotaged_warmest_city(outcomes: list[SubtaskOutcome]):
        candidates = [
            (o.subtask.entity, o.result["temperature_c"])
            for o in outcomes
            if o.subtask.operation == "weather_lookup" and o.ok and o.result is not None
            and isinstance(o.result.get("temperature_c"), (int, float))
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[1])

    def test_sabotage_lets_an_unplanned_bogus_entity_win_the_comparison(self) -> None:
        plan = build_live_data_plan("Weather in Kaunas, Tallinn, and Warsaw.", plan_id="p", attempt_id="a")
        assert plan is not None
        kaunas_task, tallinn_task, warsaw_task = plan.weather_subtasks()
        bogus = LiveDataSubtask(
            subtask_id="p:weather:bogus_source", entity="Source", operation="weather_lookup",
            arguments={"location": "source"}, required_result_fields=("condition", "source", "observed_at"),
            tool="weather", tool_intent="web.research",
        )
        outcomes = [
            SubtaskOutcome(subtask=kaunas_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Sunny", "temperature_c": 22.0, "source": "wttr.in", "source_url": "https://x"}),
            SubtaskOutcome(subtask=tallinn_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Cloudy", "temperature_c": 18.0, "source": "wttr.in", "source_url": "https://x"}),
            SubtaskOutcome(subtask=warsaw_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Clear", "temperature_c": 20.0, "source": "wttr.in", "source_url": "https://x"}),
            SubtaskOutcome(subtask=bogus, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Clear", "temperature_c": 27.0, "source": "wttr.in", "source_url": "https://x"}),
        ]

        sabotaged_winner = self._sabotaged_warmest_city(outcomes)
        assert sabotaged_winner is not None and sabotaged_winner[0] == "Source", (
            "sabotage should have wrongly let the unplanned 'Source' entity win"
        )

        # Control: the REAL, plan-guarded function excludes it.
        real_winner = warmest_city(outcomes, plan)
        assert real_winner is not None
        assert real_winner[0] != "Source"
        assert real_winner[0] == "Kaunas"


class TestSabotageFailureIdentityPreservation:
    """Sabotage: replace the real entity with a generic placeholder in the failure branch."""

    @staticmethod
    def _sabotaged_weather_row(outcome: SubtaskOutcome) -> dict:
        if not outcome.ok or outcome.result is None:
            return {"City": "Lane Unavailable", "Condition": "unavailable"}
        return {"City": outcome.subtask.entity, "Condition": str(outcome.result.get("condition") or "")}

    def test_sabotage_loses_the_real_city_name_on_failure(self) -> None:
        plan = build_live_data_plan("Weather in Helsinki and Oslo.", plan_id="p", attempt_id="a")
        assert plan is not None
        helsinki_task, _oslo_task = plan.weather_subtasks()
        failed_outcome = SubtaskOutcome(subtask=helsinki_task, state=SubtaskLifecycle.FAILED, failure_reason="upstream timeout")

        sabotaged_row = self._sabotaged_weather_row(failed_outcome)
        assert sabotaged_row["City"] != "Helsinki", "sabotage should have lost the real city name"
        assert sabotaged_row["City"] == "Lane Unavailable"

        # Control: the REAL function preserves the requested entity's identity through failure.
        real_row = _weather_row(failed_outcome)
        assert real_row["City"] == "Helsinki"


class TestSabotageWarmestCityEligibilityGuard:
    """Sabotage: drop the `.ok` filter, so a FAILED outcome (whose `result` might still carry a
    stale/leftover payload from a prior stage) can be selected as the winner."""

    @staticmethod
    def _sabotaged_warmest_city_no_ok_check(outcomes: list[SubtaskOutcome]):
        candidates = [
            (o.subtask.entity, o.result["temperature_c"])
            for o in outcomes
            if o.subtask.operation == "weather_lookup" and o.result is not None
            and isinstance(o.result.get("temperature_c"), (int, float))
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda item: item[1])

    def test_sabotage_lets_a_failed_lane_with_a_stale_result_win(self) -> None:
        plan = build_live_data_plan("Weather in Kaunas and Tallinn.", plan_id="p", attempt_id="a")
        assert plan is not None
        kaunas_task, tallinn_task = plan.weather_subtasks()
        outcomes = [
            SubtaskOutcome(subtask=kaunas_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Sunny", "temperature_c": 20.0, "source": "wttr.in", "source_url": "https://x"}),
            # FAILED but carrying a leftover result dict with an implausibly high temperature --
            # simulates a retry/carry-forward bug leaving stale data on a failed outcome.
            SubtaskOutcome(subtask=tallinn_task, state=SubtaskLifecycle.FAILED, failure_reason="stale retry artifact", result={"condition": "stale", "temperature_c": 99.0, "source": "stale", "source_url": ""}),
        ]

        sabotaged_winner = self._sabotaged_warmest_city_no_ok_check(outcomes)
        assert sabotaged_winner is not None and sabotaged_winner[0] == "Tallinn" and sabotaged_winner[1] == 99.0, (
            "sabotage should have wrongly let the failed, stale-result lane win"
        )

        # Control: the REAL function's `.ok` filter excludes any non-succeeded outcome.
        real_winner = warmest_city(outcomes, plan)
        assert real_winner is not None
        assert real_winner[0] == "Kaunas"


# ---------------------------------------------------------------------------
# Direct proof of the render-time reconciliation invariant (subset + completeness).
# ---------------------------------------------------------------------------


class TestReconciliationInvariant:
    def test_an_outcome_for_an_unplanned_subtask_id_is_dropped(self) -> None:
        plan = build_live_data_plan("Weather in Kaunas.", plan_id="p", attempt_id="a")
        assert plan is not None
        (kaunas_task,) = plan.weather_subtasks()
        bogus = LiveDataSubtask(
            subtask_id="p:weather:not_in_plan", entity="Nowhere", operation="weather_lookup",
            arguments={"location": "nowhere"}, required_result_fields=(), tool="weather", tool_intent="web.research",
        )
        outcomes = [
            SubtaskOutcome(subtask=kaunas_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Sunny", "temperature_c": 20.0, "source": "x", "source_url": "https://x"}),
            SubtaskOutcome(subtask=bogus, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Sunny", "temperature_c": 99.0, "source": "x", "source_url": "https://x"}),
        ]
        reconciled = _reconcile_to_plan(plan, outcomes)
        assert [o.subtask.entity for o in reconciled] == ["Kaunas"]

    def test_a_planned_subtask_missing_from_outcomes_still_gets_a_synthesized_entry(self) -> None:
        plan = build_live_data_plan("Weather in Kaunas and Tallinn.", plan_id="p", attempt_id="a")
        assert plan is not None
        kaunas_task, _tallinn_task = plan.weather_subtasks()
        outcomes = [
            SubtaskOutcome(subtask=kaunas_task, state=SubtaskLifecycle.SUCCEEDED, result={"condition": "Sunny", "temperature_c": 20.0, "source": "x", "source_url": "https://x"}),
        ]
        reconciled = _reconcile_to_plan(plan, outcomes)
        assert [o.subtask.entity for o in reconciled] == ["Kaunas", "Tallinn"]
        assert reconciled[1].state == SubtaskLifecycle.FAILED


# ---------------------------------------------------------------------------
# End-to-end: the REAL production entry point, `VoolAgent.run_once()` -- the same method
# `apps/vool_api_server.py`'s `/api/chat` handler calls. This is what actually caught the deepest
# bug: every test above calls `build_live_data_plan()` directly with the raw multi-paragraph text
# and was clean throughout, but the live daemon reproduced the exact incident anyway, because
# `core.input_normalizer` collapses every newline in the text `apps/vool_agent.py` was handing to
# `build_live_data_plan()` before this fix. Network calls are mocked (offline, deterministic); the
# turn-routing, classification, extraction, planning, and rendering are all real.
# ---------------------------------------------------------------------------


def _mock_market_quote(asset_key: str, price: float, change: float):
    from core.live_quote_contract import LiveQuoteResult

    return LiveQuoteResult(
        asset_key=asset_key, asset_name=asset_key.title(), symbol=asset_key.upper(),
        value=price, currency="USD", as_of="2026-08-07 08:00 UTC",
        source_label="test-source", source_url="https://x", change_percent=change,
    )


def _mock_weather(location: str, temp: float, condition: str = "Sunny"):
    from core.weather_result_contract import WeatherResult

    return WeatherResult(
        location=location, place_label=location.title(), condition=condition, temperature_c=temp,
        feels_like_c=None, humidity_pct=None, wind_kmph=None, observed_at="08:00 AM",
        source_label="test-source", source_url="https://x", high_c=temp + 3, low_c=temp - 5,
    )


class TestEndToEndThroughRealAgentEntryPoint:
    def test_production_prompt_renders_clean_through_run_once_despite_whitespace_collapsing(self) -> None:
        """Pins the deepest root cause directly: `run_once` -> `_maybe_answer_live_data_turn` must
        hand `build_live_data_plan` text that still has its paragraph boundaries, not
        `interpreted.normalized_text` (whitespace-collapsed). Sabotage-provable: reverting the
        `raw_input` wiring in `apps/vool_agent.py` reproduces the exact contamination this asserts
        against, live-verified against an isolated daemon on 2026-08-07."""
        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")

        def fake_crypto(asset_keys, **_kwargs):
            prices = {"bitcoin": (64301.0, -0.64), "binancecoin": (586.19, -1.37)}
            return [_mock_market_quote(k, *prices[k]) for k in asset_keys if k in prices]

        def fake_commodity(_query, targets, **_kwargs):
            prices = {"gold": (4352.30, 1.23), "silver": (64.27, 4.32)}
            return [_mock_market_quote(t.asset_key, *prices[t.asset_key]) for t in targets if t.asset_key in prices]

        def fake_weather(location: str, **_kwargs):
            temps = {"kaunas": 19.0, "tallinn": 20.0, "warsaw": 22.0}
            return _mock_weather(location, temps.get(location, 20.0))

        with mock.patch("tools.web.web_research._crypto_price_fallback_multi", side_effect=fake_crypto), \
             mock.patch("tools.web.web_research._market_quote_fallback_multi", side_effect=fake_commodity), \
             mock.patch("tools.web.web_research.structured_weather_lookup", side_effect=fake_weather):
            result = agent.run_once(
                PROD_PROMPT_INLINE_CLAUSE,
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        response = str(result.get("response") or "")
        assert result.get("model_calls") == 0

        # Every planned entity present, exactly once.
        assert response.count("Kaunas") >= 1
        assert response.count("Tallinn") >= 1
        assert response.count("Warsaw") >= 1
        assert response.count("Gold") >= 1
        assert response.count("Silver") >= 1
        assert response.count("Bitcoin") >= 1
        assert response.count("Binancecoin") >= 1

        # Every row in the Weather table names one of the three requested cities -- never a bogus
        # entity pulled from the output-format instructions. Checked structurally (each row's
        # City column) rather than by string search, since "Source" and "24h Change" are
        # themselves legitimate column headers in the correct output.
        weather_block = response.split("**Weather**")[1]
        weather_rows = [
            line for line in weather_block.splitlines()
            if line.startswith("| ") and "---" not in line and "City" not in line
        ]
        assert weather_rows, "no weather rows rendered at all"
        for row in weather_rows:
            city = row.split("|")[1].strip()
            assert city in {"Kaunas", "Tallinn", "Warsaw"}, f"bogus weather entity rendered: {city!r}"

        # The derived comparison names a real requested city, never the bogus one from the incident.
        assert "Warmest current city: Warsaw" in response or "Warmest current city: Kaunas" in response or "Warmest current city: Tallinn" in response
        assert "Warmest current city: -" not in response
        assert "Warmest current city: Source" not in response

    def test_build_live_data_plan_receives_text_with_paragraph_boundaries_intact(self) -> None:
        """Direct proof of the wiring itself: capture exactly what `build_live_data_plan` is
        called with during a real `run_once`, and assert it still has its blank-line paragraph
        boundaries -- not the whitespace-collapsed `effective_input`."""
        import core.agent_runtime.live_data_plan as live_data_plan_module

        agent = VoolAgent(backend_name="test-backend", device="channel-test", persona_id="default")
        captured: list[str] = []
        real_build = live_data_plan_module.build_live_data_plan

        def capturing_build(user_text, **kwargs):
            captured.append(user_text)
            return real_build(user_text, **kwargs)

        with mock.patch("tools.web.web_research._crypto_price_fallback_multi", return_value=[]), \
             mock.patch("tools.web.web_research._market_quote_fallback_multi", return_value=[]), \
             mock.patch("tools.web.web_research.structured_weather_lookup", return_value=None), \
             mock.patch("core.agent_runtime.live_data_plan.build_live_data_plan", side_effect=capturing_build):
            agent.run_once(
                PROD_PROMPT_INLINE_CLAUSE,
                source_context={"surface": "openclaw", "platform": "openclaw"},
            )

        assert captured, "build_live_data_plan was never called"
        assert "\n\n" in captured[0], (
            "build_live_data_plan received whitespace-collapsed text -- paragraph boundaries "
            "were already erased before extraction could use them"
        )


class TestSabotageRawTextWiring:
    """Sabotage: revert to always passing the whitespace-collapsed `effective_input` into
    `build_live_data_plan`, reproducing the deepest root cause directly.

    Mnemosyne review repair (2026-08-07): the exact incident prompt (`PROD_PROMPT_INLINE_CLAUSE`)
    is no longer a good differentiator for THIS specific mechanism on its own -- it has
    sentence-terminal punctuation between its entity list and the instructions that follow, and
    the Blocker-1 sentence-boundary fix (`_iter_prompt_blocks`) now independently protects that
    shape even under whitespace-collapsing, since `core.input_normalizer` only touches whitespace,
    never punctuation. Isolating the raw-text-wiring fix specifically requires a prompt with NO
    sentence-terminal punctuation at all between the entity list and the instructions -- relying
    purely on a blank-line paragraph boundary, which whitespace-collapsing DOES destroy.
    """

    _NO_PERIOD_PROMPT = (
        "weather in Kaunas, Tallinn, and Warsaw\n\n"
        "Return the results as a single compact table with no other commentary"
    )

    def test_sabotage_collapsed_text_reproduces_contamination_with_no_sentence_boundary(self) -> None:
        """Mnemosyne review round 3: the capitalization-based trailing-prose recovery added for
        Blocker A/trailing-prose ("Tallinn" out of "Tallinn live-data request in parallel") means
        Warsaw is no longer lost OUTRIGHT here -- it merges with "Return" instead (the next word
        happens to be sentence-initial-capitalized in the source text, which capitalization alone
        cannot distinguish from a proper noun). Still visibly wrong, and still a fixture proving
        this exact double-collapsed shape (no blank line AND no sentence-ending punctuation, both
        boundaries absent at once) never occurs on the real path: raw-text-wiring keeps the blank
        line intact end to end, and the review's own 20-item mandatory matrix -- which does not
        include this synthetic double-collapse -- is fully green. Recorded as a known, narrow,
        documented limitation rather than silently loosened away.
        """
        collapsed = " ".join(self._NO_PERIOD_PROMPT.split())
        plan = build_live_data_plan(collapsed, plan_id="p", attempt_id="a")
        assert plan is not None
        weather_entities = {t.entity for t in plan.weather_subtasks()}
        assert "Warsaw" not in weather_entities, (
            "sabotage should not produce a clean, correct Warsaw once the only boundary (a blank "
            "line) is collapsed -- it may still merge into a garbled composite"
        )

        # Control: the REAL wiring passes the RAW (paragraph-boundary-intact) text, which stays clean.
        real_plan = build_live_data_plan(self._NO_PERIOD_PROMPT, plan_id="p2", attempt_id="a2")
        assert real_plan is not None
        real_weather_entities = {t.entity for t in real_plan.weather_subtasks()}
        assert real_weather_entities == {"Kaunas", "Tallinn", "Warsaw"}

    def test_the_exact_incident_prompt_also_stays_clean_when_collapsed(self) -> None:
        """Not a sabotage proof (see above) -- a direct demonstration that the sentence-boundary
        fix (Blocker 1) provides a SECOND, independent layer of protection for the exact incident
        prompt specifically, on top of the raw-text-wiring fix. Both must keep holding."""
        collapsed = " ".join(PROD_PROMPT_INLINE_CLAUSE.split())
        plan = build_live_data_plan(collapsed, plan_id="p3", attempt_id="a3")
        assert plan is not None
        weather_entities = {t.entity for t in plan.weather_subtasks()}
        assert weather_entities == {"Kaunas", "Tallinn", "Warsaw"}
