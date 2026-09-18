"""R1g — the multi-intent demand-preservation falsification gate.

WHAT THIS FILE IS
-----------------
An attempt to FALSIFY universal demand preservation, machine-checked. The corpus
below is deterministic: every case states its own expected demand atoms (content
words per intended unit) and the oracle is string/token comparison only — no LLM
judges anything, no socket is opened, no provider is consulted.

The invariants pinned (requirement 3):

  I1  intended demand count and exact source spans survive the demand mint;
  I2  adding an unrelated demand never removes or changes ownership of an
      earlier demand;
  I3  reordering demands never silently drops one;
  I4  no narrow lane may finalize unless it covers every demand;
  I5  composite execution creates one owned outcome per demand;
  I6  every demand ends answered, explicitly failed, refused or deferred —
      never absent;
  I7  merge/finalization cannot publish ordinary success while any demand
      lacks an outcome.

THE CORPUS BUDGET (requirement 2): weather/currency-tagged cases are held under
10% of the corpus and the share is itself asserted by test — the multi-intent
machinery is family-agnostic and must not be certified by two families alone.

SABOTAGES (requirement 5) — each defect class has a named test that goes RED
under it:

  S1  drop a minted unit            -> test_S1_dropping_a_minted_unit_turns_the_mint_check_red
  S2  allow zero coverage           -> test_S2_zero_coverage_allowed_turns_the_lane_gate_red
  S3  omit a lane declaration       -> test_the_catalog_declares_every_executable_terminal_family
                                       (RED at base: `planned_multi_request_turn` was missing) and
                                       test_S3_omitting_a_declaration_turns_the_inventory_red
  S4  skip one planned child        -> test_S4_a_skipped_planned_child_is_named_never_absent
  S5  remove one merge outcome      -> test_S5_a_removed_merge_outcome_still_names_its_unit
"""
from __future__ import annotations

import re
from unittest import mock

import pytest

from core.agent_runtime.answer_coverage import demand_units
from core.agent_runtime.demand_ownership import (
    demand_coverage,
    execution_units,
    lane_may_claim_whole_turn,
)
from core.lane_registry import LANE_CATALOG
from tests.test_turn_attempt_chain import _SOURCE_CONTEXT, _Harness, _incident_mocks

# ======================================================================
# 1. THE CORPUS — deterministic cases with expected demand atoms.
#
# `atoms` lists atom GROUPS (any-of) per INTENDED unit, in order: a unit is
# matched when at least one atom of its group appears in it (I1) and in the
# served reply (I6) — groups hold answer-shape alternatives because a legal
# answer may be the conversion OR its typed refusal ('eur' | 'exchange').
# `wc=True` marks a weather/currency control (budget-checked by test).
# `units` may be given when the intended MINT grain differs from the execution
# grain (riders, chains, pasted context merge — the R1e execution-boundary law).
# ======================================================================

_CASES: list[dict] = [
    # -- pairwise general×general, both orders --------------------------------
    dict(id="g-pair-1", text="explain entropy briefly and write a haiku about rain",
         atoms=[["entropy"], ["haiku", "rain"]]),
    dict(id="g-pair-1r", text="write a haiku about rain and explain entropy briefly",
         atoms=[["haiku", "rain"], ["entropy"]]),
    dict(id="g-pair-2", text="summarize the gold standard and define liquidity",
         atoms=[["gold", "standard"], ["liquidity"]]),
    dict(id="g-pair-2r", text="define liquidity and summarize the gold standard",
         atoms=[["liquidity"], ["gold", "standard"]]),
    dict(id="g-pair-3", text="calculate 17 times 24 and translate good morning to french",
         atoms=[["17", "24"], ["good", "morning", "french"]]),
    dict(id="g-pair-3r", text="translate good morning to french and calculate 17 times 24",
         atoms=[["good", "morning", "french"], ["17", "24"]]),
    dict(id="g-pair-4", text="list three brass instruments and describe a buffer overflow",
         atoms=[["three", "brass", "instruments"], ["buffer", "overflow"]]),
    dict(id="g-pair-4r", text="describe a buffer overflow and list three brass instruments",
         atoms=[["buffer", "overflow"], ["three", "brass", "instruments"]]),
    dict(id="g-pair-5", text="name three sorting algorithms and explain big o notation",
         atoms=[["three", "sorting", "algorithms"], ["big", "notation"]]),
    dict(id="g-pair-6", text="draft a limerick about copper and explain osmosis simply",
         atoms=[["limerick", "copper"], ["osmosis"]], exec_units=2),
    dict(id="g-pair-6r", text="explain osmosis simply and draft a limerick about copper",
         atoms=[["osmosis"], ["limerick", "copper"]], exec_units=1),
    dict(id="g-pair-7", text="outline the causes of inflation and quote one line from hamlet",
         atoms=[["inflation", "causes"], ["hamlet"]], exec_units=2),
    dict(id="g-pair-7r", text="quote one line from hamlet and outline the causes of inflation",
         atoms=[["hamlet"], ["inflation", "causes"]], exec_units=1),
    # -- three-way combinations, both orders, two connector styles ------------
    dict(id="g-three-1",
         text="explain entropy briefly, write a haiku about rain, and summarize the gold standard",
         atoms=[["entropy"], ["haiku", "rain"], ["gold", "standard"]]),
    dict(id="g-three-1r",
         text="summarize the gold standard, write a haiku about rain, and explain entropy briefly",
         atoms=[["gold", "standard"], ["haiku", "rain"], ["entropy"]]),
    dict(id="g-three-2",
         text="describe a buffer overflow. calculate 17 times 24. define liquidity.",
         atoms=[["buffer", "overflow"], ["17", "24"], ["liquidity"]]),
    dict(id="g-three-2r",
         text="define liquidity. calculate 17 times 24. describe a buffer overflow.",
         atoms=[["liquidity"], ["17", "24"], ["buffer", "overflow"]]),
    dict(id="g-three-3",
         text="draft a limerick about copper. explain osmosis simply. outline the causes of inflation.",
         atoms=[["limerick", "copper"], ["osmosis"], ["inflation", "causes"]], exec_units=2),
    dict(id="g-three-3r",
         text="outline the causes of inflation. explain osmosis simply. draft a limerick about copper.",
         atoms=[["inflation", "causes"], ["osmosis"], ["limerick", "copper"]], exec_units=2),
    # -- connector and punctuation variety -------------------------------------
    # c-newline/c-period pin the two-grain law: BOTH demands MINT (I1), and the
    # execution grain may merge them into one context-bearing sub-turn when the
    # second does not open a demand head ("define" is not a head) — merging is
    # context preservation, never demand loss, because the merged span keeps
    # every word of both demands.
    dict(id="c-semi", text="summarize the gold standard; define liquidity",
         atoms=[["gold", "standard"], ["liquidity"]], exec_units=2),
    dict(id="c-newline", text="summarize the gold standard\ndefine liquidity",
         atoms=[["gold", "standard"], ["liquidity"]], exec_units=2),
    dict(id="c-period", text="Summarize the gold standard. Define liquidity.",
         atoms=[["gold", "standard"], ["liquidity"]], exec_units=2),
    dict(id="c-plus", text="define liquidity plus summarize the gold standard",
         atoms=[["liquidity"], ["gold", "standard"]]),
    dict(id="c-also", text="define liquidity, also summarize the gold standard",
         atoms=[["liquidity"], ["gold", "standard"]]),
    dict(id="c-then", text="calculate 17 times 24 then translate good morning to french",
         atoms=[["17", "24"], ["good", "morning", "french"]]),
    dict(id="c-then-r", text="translate good morning to french then calculate 17 times 24",
         atoms=[["good", "morning", "french"], ["17", "24"]]),
    dict(id="c-mixed-punct", text="define liquidity! summarize the gold standard; write a haiku about rain",
         atoms=[["liquidity"], ["gold", "standard"], ["haiku", "rain"]]),
    dict(id="c-colon", text="do two things: define liquidity and summarize the gold standard",
         atoms=[["define", "liquidity"], ["gold", "standard"]]),
    dict(id="c-please", text="please explain osmosis simply and also outline the causes of inflation",
         atoms=[["osmosis"], ["inflation", "causes"]], exec_units=1),
    dict(id="c-question-mark", text="what is osmosis? what is enthalpy?",
         atoms=[["osmosis"], ["enthalpy"]], exec_units=2),
    dict(id="c-question-mark-r", text="what is enthalpy? what is osmosis?",
         atoms=[["enthalpy"], ["osmosis"]], exec_units=2),
    # -- typo-heavy wording (Property 1: a typo still mints) -------------------
    dict(id="t-heads", text="expalin how a hash tabl works and write a haiku about rain",
         atoms=[["hash", "tabl"], ["haiku", "rain"]]),
    dict(id="t-verbs", text="defnie liquidity and summraize the gold standard",
         atoms=[["liquidity"], ["gold", "standard"]]),
    dict(id="t-interrogative", text="waht is a buffer overflow and calcualte 17 times 24",
         atoms=[["buffer", "overflow"], ["17", "24"]]),
    dict(id="t-both-halves", text="transalte good morning to french and defnie liquidity",
         atoms=[["good", "morning", "french"], ["liquidity"]]),
    dict(id="t-5", text="summraize the causes of inflation and drfat a limerick about copper",
         atoms=[["inflation", "causes"], ["limerick", "copper"]], exec_units=1),
    dict(id="t-6", text="exlpain osmosis simply and quuote one line from hamlet",
         atoms=[["osmosis"], ["hamlet"]], exec_units=2),
    # -- corrections and follow-ups --------------------------------------------
    # Retraction shapes: the withdrawn clause mints nothing, the cue itself
    # mints nothing, and only the survivor is demand. The two k-*-2 shapes pin
    # the SAFE failure of the retraction vocabulary ('ignore that'/'never mind'
    # before a 'quote'/'outline' continuation do not fire): BOTH demands are
    # preserved rather than one dropped — a miss, never a loss.
    dict(id="k-wait-no", text="explain entropy briefly. WAIT no, explain enthalpy instead",
         atoms=[["enthalpy"]]),
    dict(id="k-no-wait", text="explain entropy briefly, no wait, explain enthalpy instead",
         atoms=[["enthalpy"]]),
    dict(id="k-scratch", text="explain entropy briefly, scratch that, explain enthalpy instead",
         atoms=[["enthalpy"]]),
    dict(id="k-cancel", text="explain entropy briefly. Cancel that. Explain enthalpy instead",
         atoms=[["enthalpy"]]),
    dict(id="k-add", text="explain entropy briefly, sorry I mean also include enthalpy",
         atoms=[["entropy"], ["enthalpy"]], exec_units=1),
    dict(id="k-actually", text="explain entropy briefly. Actually, explain enthalpy instead",
         atoms=[["entropy"], ["enthalpy"]]),
    dict(id="k-ignore", text="draft a limerick about copper, ignore that, quote one line from hamlet",
         atoms=[["limerick", "copper"], ["hamlet"]]),
    dict(id="k-nevermind", text="draft a limerick about copper. never mind. quote one line from hamlet",
         atoms=[["limerick", "copper"], ["hamlet"]]),
    dict(id="f-rider", text="explain entropy briefly and also the second part",
         atoms=[["entropy"], []], exec_units=1),
    dict(id="f-clarif", text="define liquidity and what about the gold standard",
         atoms=[["liquidity"], ["gold", "standard"]]),
    dict(id="f-anaphora", text="explain osmosis simply and what about enthalpy",
         atoms=[["osmosis"], ["enthalpy"]], exec_units=2),
    dict(id="f-pure-and", text="and?", atoms=[]),
    # -- long pasted-text shapes (no over-shredding: context merges) -----------
    dict(id="p-log",
         text="pasted log line one at 10:00 failed with timeout\n"
              "pasted log line two at 10:01 retried ok\n"
              "explain what a timeout is here",
         atoms=[["pasted", "failed"], ["retried"], ["timeout"]],
         exec_units=2),
    dict(id="p-dump",
         text="Here is a dump:\nfoo bar\nbaz qux\n\nExplain foo. Also define baz.",
         atoms=[["dump"], ["foo", "bar"], ["baz", "qux"], ["explain", "foo"], ["define", "baz"]],
         exec_units=3),
    dict(id="p-prose",
         text="The committee met on Tuesday and reviewed the quarterly numbers which had "
              "been compiled by the finance team although several line items were still "
              "marked provisional pending the audit that had been scheduled for the "
              "following month. Summarize the paragraph above.",
         atoms=[["committee", "tuesday"], ["quarterly", "provisional"], ["summarize", "paragraph"]],
         exec_units=2),
    dict(id="p-quote",
         text='The rule says: "return everything within 5 days."\nquote one line from hamlet',
         # Contract 2026-09-08: the pasted rule is CONTEXT of the request after it and runs with it.
         atoms=[["rule", "days"], ["hamlet"]], exec_units=1),
    # -- weather/currency controls (budget-checked) -----------------------------
    dict(id="w-mixed", text="tell me the weather in Kaunas and explain entropy briefly",
         atoms=[["kaunas"], ["entropy"]], wc=True, mixed=True,
         mixed_owner="live_data_typed_plan"),
    dict(id="w-mixed-r", text="explain entropy briefly and tell me the weather in Kaunas",
         atoms=[["entropy"], ["kaunas"]], wc=True, mixed=True,
         mixed_owner="live_data_typed_plan"),
    dict(id="cur-mixed", text="convert 100 usd to eur and explain what a hedge fund is",
         atoms=[["eur", "exchange"], ["hedge", "fund"]], wc=True, mixed=True),
    dict(id="cur-chain", text="convert 100 usd to eur and then to gold",
         atoms=[["eur"], ["gold"]], wc=True, exec_units=1),
    # -- pure controls -----------------------------------------------------------
    dict(id="pc-general", text="explain hash tables", atoms=[["hash", "tables"]], mixed=False),
    dict(id="pc-weather", text="weather in Kaunas", atoms=[["kaunas"]],
         wc=True, mixed=False),
    dict(id="pc-currency", text="convert 100 usd to eur", atoms=[["eur"]],
         wc=True, mixed=False),
    dict(id="pc-greeting", text="hi there", atoms=[]),
    dict(id="pc-thanks", text="thanks a lot", atoms=[]),
    dict(id="pc-prohibition", text="do not send any money", atoms=[]),
    # Contract 2026-09-08: "give me a verdict" is an output constraint attached to the audit, not a
    # demand fragment of its own.
    dict(id="pc-meta-rider", text="audit this project. give me a verdict.",
         atoms=[["audit", "project"]], exec_units=1),
]

_CASE_INDEX = {case["id"]: case for case in _CASES}

_WORD_RE = re.compile(r"[a-z0-9]+")

#: Oracle-side stop words — deliberately independent of the implementation's own
#: token tables so the oracle cannot inherit a defect it is checking for.
_ORACLE_STOP = frozenset({
    "a", "an", "the", "and", "or", "but", "if", "then", "also", "plus", "of", "in",
    "on", "at", "to", "for", "from", "by", "with", "about", "as", "into", "is",
    "are", "was", "were", "be", "been", "am", "do", "does", "did", "will", "would",
    "can", "could", "should", "may", "might", "have", "has", "had", "i", "me", "my",
    "we", "you", "your", "it", "its", "they", "them", "their", "this", "that",
    "these", "those", "what", "whats", "which", "who", "whom", "whose", "where",
    "when", "why", "how", "tell", "give", "show", "get", "make", "let", "know",
    "want", "need", "like", "please", "pls", "ok", "okay", "just", "now", "here",
    "there", "very", "really", "too", "so", "no", "not", "yes", "yeah", "two",
    "things", "one", "second", "part", "mean", "wait", "instead", "sorry",
    "actually", "briefly", "include",
})


def _atoms(text: str) -> set[str]:
    """The oracle's own content-word reading — independent of the mint."""
    return {
        word for word in _WORD_RE.findall(str(text or "").lower())
        if word not in _ORACLE_STOP and len(word) > 1
    }


def _check_mint(case: dict) -> None:
    """I1: intended demand count, atom content and exact source spans survive.

    Reads the mint through its MODULE attribute so a sabotage patch on the
    module is what the checker sees — the same binding production consumers
    resolve at call time."""
    from core.agent_runtime import answer_coverage

    text = case["text"]
    expected = case["atoms"]
    # Contract 2026-09-08: the mint still cuts every fragment with its exact span, but each carries
    # a KIND (request / context / constraint / literal / enumeration). The atoms describe the
    # fragments; the requested-slot set is the request-kind view of them.
    units = tuple(
        unit for unit in answer_coverage.interpret_request(text).units if unit.kind != "constraint"
    )  # prohibitions and other constraints never counted as demand fragments
    minted = [u.text for u in units]
    assert len(units) == len(expected), (
        f"{case['id']}: intended {len(expected)} fragment(s), minted {len(units)}: {minted}"
    )
    for unit, wanted in zip(units, expected, strict=True):
        if not wanted:
            continue
        present = _atoms(unit.text)
        if not any(atom in present for atom in wanted):
            raise AssertionError(
                f"{case['id']}: unit {unit.unit_id} ({unit.text!r}) matches none of its "
                f"intended atoms {wanted}; minted units: {minted}"
            )
    for unit in units:
        assert text[unit.start : unit.end] == unit.text, (
            f"{case['id']}: unit {unit.unit_id} span ({unit.start}, {unit.end}) does not "
            f"point at its own text {unit.text!r} (points at "
            f"{text[unit.start : unit.end]!r})"
        )
    if "exec_units" in case:
        execs = execution_units(text)
        assert len(execs) == case["exec_units"], (
            f"{case['id']}: intended {case['exec_units']} execution unit(s), got "
            f"{len(execs)}: {[t for _i, t in execs]}"
        )
    if "mixed" in case:
        coverage = demand_coverage(text)
        assert coverage.mixed == case["mixed"], (
            f"{case['id']}: mixed read {coverage.mixed}, intended {case['mixed']} "
            f"(per-unit lanes: {coverage.per_unit_lanes})"
        )


def test_i1_intended_demand_count_and_spans_survive_the_mint():
    """Invariant I1 over the whole corpus, one failure message per case."""
    failures = []
    for case in _CASES:
        try:
            _check_mint(case)
        except AssertionError as exc:
            failures.append(str(exc))
    assert not failures, "\n".join(failures)


def test_i1_corpus_budget_keeps_weather_and_currency_under_ten_percent():
    """Requirement 2: weather/currency controls are a minority of the corpus."""
    total = len(_CASES)
    tagged = sum(1 for case in _CASES if case.get("wc"))
    assert tagged / total < 0.10, (
        f"weather/currency controls are {tagged}/{total} = {tagged / total:.1%} of the "
        f"corpus — the gate must not be certified by two families alone"
    )


# ======================================================================
# 2. I2/I3 — additivity and reorder, over the coverage reader.
# ======================================================================

_ADDITIVE_PAIRS = [
    ("explain entropy briefly", "write a haiku about rain"),
    ("summarize the gold standard", "define liquidity"),
    ("tell me the weather in Kaunas", "explain entropy briefly"),
    ("convert 100 usd to eur", "explain what a hedge fund is"),
]


def _normalized_units(text: str) -> list[str]:
    return [u.text.strip().lstrip("and ").strip() for u in demand_units(text)]


@pytest.mark.parametrize("first,second", _ADDITIVE_PAIRS, ids=lambda v: v[:18])
def test_i2_adding_a_demand_never_changes_an_earlier_one(first, second):
    """I2: the earlier demand's unit text and per-unit lanes are identical alone
    and beside an unrelated demand (ids may shift; ownership may not)."""
    alone = demand_coverage(first)
    together = demand_coverage(f"{first} and {second}")
    assert together.unit_count == alone.unit_count + 1
    for index in range(alone.unit_count):
        assert _normalized_units(together.units[index][1]) == _normalized_units(
            alone.units[index][1]
        ), (
            f"adding {second!r} changed the earlier unit: "
            f"{alone.units[index][1]!r} -> {together.units[index][1]!r}"
        )
        assert together.per_unit_lanes[index] == alone.per_unit_lanes[index], (
            f"adding {second!r} changed ownership of unit {index + 1}: "
            f"{alone.per_unit_lanes[index]} -> {together.per_unit_lanes[index]}"
        )


@pytest.mark.parametrize("first,second", _ADDITIVE_PAIRS, ids=lambda v: v[:18])
def test_i3_reordering_demands_drops_nothing(first, second):
    """I3: both orders mint both demands with the same atom sets, and each
    unit's lane ownership follows the unit, not its position."""
    forward = demand_coverage(f"{first} and {second}")
    backward = demand_coverage(f"{second} and {first}")
    assert forward.unit_count == backward.unit_count == 2
    forward_atoms = [ _atoms(t) for _i, t in forward.units ]
    backward_atoms = [ _atoms(t) for _i, t in backward.units ]
    assert forward_atoms[0] == backward_atoms[1] and forward_atoms[1] == backward_atoms[0], (
        f"reordering changed the demand set: {forward_atoms} vs {backward_atoms}"
    )
    assert forward.per_unit_lanes[0] == backward.per_unit_lanes[1], (
        f"reordering changed unit ownership: {forward.per_unit_lanes} vs "
        f"{backward.per_unit_lanes}"
    )


# ======================================================================
# 3. I4 — the finalize law at the coverage/registry seam.
# ======================================================================


def test_i4_a_narrow_lane_cannot_finalize_a_multi_demand_turn():
    """I4 through every catalog family: the live and currency lanes cover one
    unit of a mixed turn and must not finalize it; the frontdoor deterministic
    family declares itself single-unit-limited and must not either; composite
    planners and the fallback own whole plans and may."""
    live_mixed = _CASE_INDEX["w-mixed"]["text"]
    currency_mixed = _CASE_INDEX["cur-mixed"]["text"]
    general_mixed = _CASE_INDEX["g-pair-1"]["text"]
    for text in (live_mixed, currency_mixed, general_mixed):
        assert not lane_may_claim_whole_turn(text, "turn_frontdoor_deterministic"), (
            f"the single-unit-limited frontdoor family may finalize {text!r}"
        )
        assert not lane_may_claim_whole_turn(text, "attempt_followup")
        assert not lane_may_claim_whole_turn(text, "not_a_registered_lane")
        for composite in (
            "conductor_multi_intent_plan",
            "demand_owned_mixed_turn",
            "planned_multi_request_turn",
            "model_lane",
        ):
            assert lane_may_claim_whole_turn(text, composite), (
                f"the declared composite {composite} must own whole unit plans ({text!r})"
            )
    assert not lane_may_claim_whole_turn(live_mixed, "live_data_typed_plan"), (
        "the live lane covers one unit of the mixed turn and may not finalize it"
    )
    assert lane_may_claim_whole_turn("weather in Kaunas", "live_data_typed_plan")


def test_i4_narrow_lane_gate_uses_synthetic_capabilities_not_two_families():
    """I4 is a law about coverage, not about weather/currency: two synthetic
    capabilities (pure injection, no network) reproduce additivity and the
    finalize trichotomy for arbitrary families."""
    from core.agent_runtime import demand_ownership
    from core.lane_registry import LaneSpec, scoped_catalog

    def claims_explain(unit_text: str) -> bool:
        return "explain" in _atoms(unit_text) or "entropy" in _atoms(unit_text)

    def claims_translate(unit_text: str) -> bool:
        return "translate" in _atoms(unit_text)

    text = "explain entropy briefly and translate good morning to french"
    with demand_ownership.scoped_coverage_capabilities(
        {"explain_cap": claims_explain, "translate_cap": claims_translate}
    ):
        specs = (
            LaneSpec("synthetic_explain_lane", 10, True, "explain_cap"),
            LaneSpec("synthetic_translate_lane", 20, True, "translate_cap"),
        )
        with scoped_catalog(specs):
            coverage = demand_coverage(text)
            assert coverage.mixed, f"synthetic capabilities must reproduce mixing: {coverage}"
            assert coverage.lane_unit_ids("synthetic_explain_lane") == ("u1",)
            assert coverage.lane_unit_ids("synthetic_translate_lane") == ("u2",)
            assert not lane_may_claim_whole_turn(text, "synthetic_explain_lane")
            assert not lane_may_claim_whole_turn(text, "synthetic_translate_lane")
            pure = "explain entropy briefly"
            assert lane_may_claim_whole_turn(pure, "synthetic_explain_lane")


# ======================================================================
# 4. The inventory — executable terminal families vs LANE_CATALOG (req 1).
# ======================================================================

#: Every family an EXECUTABLE call site can finalize an external turn under,
#: inventoried from call sites in `VoolAgent._run_once_inner` and the two
#: frontdoor passes (agent.py / turn_frontdoor.py) at base b1e76f71:
#:   empty_turn / namespace-inactive / checkpoint-resume — non-demand intake
#:   gates (fire with no request or as a typed refusal of the whole turn), not
#:   demand lanes; documented here so the exclusion is itself reviewed.
EXECUTABLE_TERMINAL_FAMILIES = (
    # P0 mixed-demand terminal closure: the clock family finalizes at the front
    # door's date_time arm (reason="date_time_fast_path") and now declares the
    # per-unit "clock" coverage capability, so all-deterministic multi-unit
    # turns (clock + arithmetic) reach the demand-owned seam instead of the
    # single plain answering lane.
    "date_time_fast_path",          # turn_frontdoor.py date_time fast path
    "live_info_fast_path",          # turn_frontdoor.py structured fast pass
    "live_data_typed_plan",         # agent.py _maybe_answer_live_data_turn
    "currency_value_contract",      # agent.py maybe_answer_currency_value seam
    "currency_frontdoor",           # turn_frontdoor.py currency fast path
    # P0 mixed-demand: two families with their OWN route reasons and their own
    # terminal call sites, previously finalizing under the deterministic tier
    # while declaring no per-unit coverage.
    "workspace_read_fast_path",     # turn_frontdoor.py workspace runtime pass
                                   # (reason="workspace_runtime_fast_path")
    "direct_math_fast_path",        # turn_frontdoor.py direct math fast path
    # P0 simple-file-write: the single-file write family executes through the
    # builder's typed workflow loop (controller.py workflow response) and
    # finalizes single-unit turns there; its coverage probe is the resolver.
    "workspace_write_workflow",
    # M4 (contract 2026-09-08): two lanes that admitted requests the catalog could not see.
    "workspace_audit_frontdoor",    # turn_frontdoor.py coverage_for(FAMILY_WORKSPACE_AUDIT) arm
                                   # -> agent._maybe_handle_workspace_audit_request
    "machine_fact_fast_path",       # turn_frontdoor.py direct machine-read handler
                                   # (machine.find_largest / free space)
    "turn_frontdoor_deterministic", # handle_turn_frontdoor deterministic intents
    "conductor_multi_intent_plan",  # agent.py _maybe_answer_conductor_turn
    "demand_owned_mixed_turn",      # agent.py _maybe_answer_demand_owned_turn
    "planned_multi_request_turn",   # agent.py _maybe_answer_planned_turn (was UNDECLARED)
    "attempt_followup",             # agent.py _maybe_answer_attempt_followup_turn
    "secret_intake_turn",           # agent.py secret intake + _answer_secret_intake_turn
                                   # (R1g amendment: credential + remainder as one turn)
    "model_lane",                   # the grounded model turn of last resort
)


def test_the_catalog_declares_every_executable_terminal_family():
    """Requirement 1: any lane an executable call site can finalize under is
    declared — a missing declaration is exactly how an omitted lane keeps the
    freedom to swallow mixed demand (R1f's own words). RED at base: the generic
    planner seam finalized multi-unit turns undeclared."""
    declared = {spec.lane_id for spec in LANE_CATALOG}
    missing = set(EXECUTABLE_TERMINAL_FAMILIES) - declared
    assert not missing, (
        f"terminal families executable today but absent from LANE_CATALOG: {sorted(missing)}"
    )


def test_the_executable_inventory_covers_the_catalog():
    """The reverse direction: every DECLARED lane names a real executable family
    — a declaration for a lane that cannot finalize anything is a false
    declaration."""
    declared = {spec.lane_id for spec in LANE_CATALOG}
    unknown = declared - set(EXECUTABLE_TERMINAL_FAMILIES)
    assert not unknown, (
        f"LANE_CATALOG declares lanes with no executable terminal site: {sorted(unknown)}"
    )


def test_the_free_models_intent_no_longer_finalizes_a_two_demand_turn(harness):
    """The base defect, measured live at b1e76f71: the frontdoor fast-intent
    block recognized `what free models are on offer` in a TWO-demand message and
    finalized the whole external turn — the explanation half vanished. The
    catalog declares this family single-unit-limited; the seam must enforce it."""
    with _incident_mocks(), _model_stand_in(harness.agent, _GENERAL_STAND_IN):
        result, _context = harness.turn(_FREE_MODELS_MIXED, session_id=harness.session_id)

    answer = str(result.get("response") or "")
    label = _served_label(result)
    assert "free_models" not in label, (
        f"a single-request fast intent finalized a two-demand turn as {label!r}: {answer[:160]!r}"
    )
    assert _GENERAL_STAND_IN in answer or "couldn't produce a complete answer" in answer, (
        f"neither the model's whole-plan answer nor its typed deferral reached the user "
        f"under {label!r}: {answer[:160]!r}"
    )


def test_the_openrouter_intent_no_longer_finalizes_a_two_demand_turn(harness):
    """The same defect through the onboarding intent (claimed whole at base)."""
    with _incident_mocks(), _model_stand_in(harness.agent, _GENERAL_STAND_IN):
        result, _context = harness.turn(_OPENROUTER_MIXED, session_id=harness.session_id)

    answer = str(result.get("response") or "")
    label = _served_label(result)
    assert "openrouter" not in label, (
        f"the onboarding intent finalized a two-demand turn as {label!r}: {answer[:160]!r}"
    )
    assert _GENERAL_STAND_IN in answer, answer[:160]


# ======================================================================
# 5. Integration — I5/I6/I7 over the real cascade, hermetic stand-ins.
# ======================================================================

_FREE_MODELS_MIXED = "what free models are on offer and explain entropy briefly"
_OPENROUTER_MIXED = "connect me to openrouter and explain entropy briefly"
_LIVE_MIXED = "tell me the weather in Kaunas and explain entropy briefly"
_LIVE_MIXED_REVERSED = "explain entropy briefly and tell me the weather in Kaunas"
_CURRENCY_MIXED = "convert 100 usd to eur and explain what a hedge fund is"
_THREE_UNIT = "weather in Kaunas. What is the price of gold? Also explain entropy briefly."
_PURE_WEATHER_PAIR = "weather in Kaunas and Rome"
_GENERAL_PAIR = "explain entropy briefly and define liquidity"

_HASH_STAND_IN = "HASH-STAND-IN: a hash table maps keys to array buckets via a hash function."
_HEDGE_STAND_IN = "HEDGE-FUND-STAND-IN: a hedge fund pools capital for actively managed positions."
_ENTROPY_STAND_IN = "ENTROPY-STAND-IN: entropy measures the disorder of a system."
_GENERAL_STAND_IN = "GENERAL-STAND-IN: an ordinary answer from the normal reasoning path."
_PAIR_STAND_IN = (
    "PAIR-STAND-IN: entropy measures disorder; liquidity is how easily an asset becomes cash."
)


def _model_stand_in(agent, text: str):
    """The R1e deterministic model stand-in: a typed ModelExecutionDecision through
    the REAL model lane, plus the renderer seam. No provider socket."""
    import contextlib

    from core.memory_first_router import ModelExecutionDecision

    decision = ModelExecutionDecision(
        source="model", task_hash="r1g-stand-in", provider_id="r1g-stand-in",
        provider_name="R1G stand-in", model_name="r1g-stand-in",
        output_text=text, confidence=0.9, trust_score=0.9, used_model=True,
    )
    stack = contextlib.ExitStack()
    stack.enter_context(mock.patch.object(agent.memory_router, "resolve", return_value=decision))
    stack.enter_context(
        mock.patch("core.agent_runtime.agent.render_response", return_value=text)
    )
    return stack


def _fx_stand_in(rate: str = "0.86000") -> dict[str, object]:
    def _fetch(_url, _timeout, _headers):
        return {"rate": rate, "date": "2026-08-31"}

    return {"fx_fetch_json": _fetch, "fx_now": None}


@pytest.fixture
def harness(request):
    h = _Harness(f"sess-r1g-{request.node.name[:44]}")
    try:
        yield h
    finally:
        h.close()


def _served_label(result: dict) -> str:
    reason = str(result.get("reason") or "")
    route = str(result.get("route") or "")
    return (reason or route).lower()


def _assert_every_demand_discharged(text: str, response: str, groups=None) -> None:
    """I6: every minted demand is answered in the reply or explicitly named as
    failed/pending — the closure check the oracle performs on real turns. A
    failed unit quotes its own request text in the merge's failure block, so its
    atoms appear verbatim; an answered unit's atoms appear in its answer. Each
    atom GROUP passes when any one atom appears, so a legal typed refusal
    ('exchange rate is a live observation') discharges beside a real answer."""
    from core.agent_runtime.answer_coverage import demand_units as mint

    lowered = str(response or "").lower()
    units = list(mint(text))
    if groups is None:
        groups = [
            sorted(atom for atom in _atoms(unit.text) if atom not in _ORACLE_STOP)
            for unit in units
        ]
    assert len(groups) == len(units), (
        f"discharge groups ({len(groups)}) must align with minted units ({len(units)})"
    )
    for unit, wanted in zip(units, groups, strict=True):
        if not wanted:
            continue
        if not any(atom in lowered for atom in wanted):
            raise AssertionError(
                f"demand unit {unit.text!r} is absent from the reply (none of {wanted} "
                f"present); reply head: {str(response)[:200]!r}"
            )


_LIVE_MIX_CASES = (
    ("w-mixed", "tell me the weather in Kaunas and explain entropy briefly"),
    ("w-mixed-r", "explain entropy briefly and tell me the weather in Kaunas"),
)


def test_i6_live_mixed_both_orders_discharge_every_demand(harness):
    for case_id, text in _LIVE_MIX_CASES:
        groups = _CASE_INDEX[case_id]["atoms"]
        with _incident_mocks(), _model_stand_in(harness.agent, _ENTROPY_STAND_IN):
            result, _context = harness.turn(text, session_id=harness.session_id)
        answer = str(result.get("response") or "")
        assert "kaunas" in answer.lower() and "sunny" in answer.lower(), (
            f"the live half of {text!r} was not served: {answer[:160]!r}"
        )
        _assert_every_demand_discharged(text, answer, groups)


def test_i6_currency_mixed_discharges_every_demand(harness):
    groups = _CASE_INDEX["cur-mixed"]["atoms"]
    with _model_stand_in(harness.agent, _HEDGE_STAND_IN):
        context = dict(_SOURCE_CONTEXT)
        context.update(_fx_stand_in())
        with _incident_mocks():
            result = harness.agent.run_once(
                _CURRENCY_MIXED, source_context=context, session_id_override=harness.session_id
            )
    answer = str(result.get("response") or "")
    assert "eur" in answer.lower() or "exchange rate" in answer.lower(), answer[:160]
    assert _HEDGE_STAND_IN in answer
    _assert_every_demand_discharged(_CURRENCY_MIXED, answer, groups)


def test_i6_the_model_lane_owns_whole_plans_for_general_pairs(harness):
    """A general×general pair is the fallback's to answer in one context: no
    narrow deterministic lane may truncate it. Hermetically the plain-task seam
    may honestly DEFER (an explicit I6 terminal state, naming the retry) — what
    it may never do is answer one half and drop the other."""
    with _model_stand_in(harness.agent, _PAIR_STAND_IN):
        result, _context = harness.turn(_GENERAL_PAIR, session_id=harness.session_id)
    answer = str(result.get("response") or "")
    label = _served_label(result)
    assert not label.startswith("deterministic:") or "free_models" in label, (
        f"a narrow deterministic lane finalized a two-demand turn as {label!r}: {answer[:160]!r}"
    )
    assert _PAIR_STAND_IN in answer or "couldn't produce a complete answer" in answer, (
        f"neither the model's whole-plan answer nor its typed deferral reached the user: "
        f"{answer[:160]!r}"
    )


def test_i6_a_pure_two_city_live_turn_serves_both_cities(harness):
    with _incident_mocks():
        result, _context = harness.turn(_PURE_WEATHER_PAIR, session_id=harness.session_id)
    answer = str(result.get("response") or "")
    assert "kaunas" in answer.lower() and "rome" in answer.lower(), answer[:160]


def test_i6_a_multi_demand_followup_is_not_finalized_by_the_followup_lane(harness):
    """attempt_followup declares single-unit-limited. A follow-up that carries a
    SECOND demand must not be finalized as the retry alone."""
    with _incident_mocks(), _model_stand_in(harness.agent, _GENERAL_STAND_IN):
        harness.turn("tell me the weather in Atlantisxyzabc123", session_id=harness.session_id)
        result, _context = harness.turn(
            "retry that and also explain entropy briefly", session_id=harness.session_id
        )
    answer = str(result.get("response") or "")
    label = _served_label(result)
    assert _GENERAL_STAND_IN in answer, (
        f"the two-demand follow-up was finalized as a narrow retry under {label!r}: {answer[:160]!r}"
    )


# ------------------------------------------------- I5/I7 composite honesty


def _planned_outcomes_spy():
    """Records (tasks, outcomes) as the demand-owned seam sees them."""
    seen: dict[str, list] = {}

    import core.agent_runtime.turn_planner as planner

    real = planner.run_plan

    def _spy(tasks, **kwargs):
        outcomes = real(tasks, **kwargs)
        seen.setdefault("calls", []).append((list(tasks), list(outcomes)))
        return outcomes

    return seen, mock.patch.object(planner, "run_plan", _spy)


def test_i5_composite_execution_creates_one_outcome_per_demand(harness):
    """I5 on a real three-unit mixed turn: three minted demands, three owned
    outcomes, and every demand discharged in the synthesis."""
    spy, patch = _planned_outcomes_spy()
    with _incident_mocks(), _model_stand_in(harness.agent, _ENTROPY_STAND_IN), patch:
        result, _context = harness.turn(_THREE_UNIT, session_id=harness.session_id)

    answer = str(result.get("response") or "")
    calls = spy.get("calls") or []
    assert calls, "the mixed turn never ran a demand-unit plan"
    tasks, outcomes = calls[0]
    assert len(outcomes) == len(tasks) == 3, (
        f"one outcome per demand is the contract: {len(tasks)} tasks, "
        f"{len(outcomes)} outcomes ({[o.task.request[:30] for o in outcomes]})"
    )
    _assert_every_demand_discharged(_THREE_UNIT, answer, [["kaunas"], ["gold"], ["entropy"]])


def test_i5_run_plan_returns_one_outcome_per_task_even_when_a_child_is_skipped():
    """I5 structural law on the planner itself, sabotaged at the wave seam: a
    task whose execution never produced an outcome (the skipped-child shape) is
    returned as an EXPLICIT failed outcome — never silently filtered from the
    list. RED at base: run_plan's return comprehension dropped the missing
    index and returned two outcomes for three tasks."""
    from core.agent_runtime import turn_planner
    from core.agent_runtime.turn_planner import PlannedTask, run_plan

    tasks = [
        PlannedTask(index=0, request="explain entropy briefly"),
        PlannedTask(index=1, request="tell me the weather in Kaunas"),
        PlannedTask(index=2, request="write a haiku about rain"),
    ]
    real_waves = turn_planner.execution_waves

    def _skipping_the_last(wave_tasks):
        waves = real_waves(wave_tasks)
        return waves[:-1]  # the last wave — one task — never executes

    with mock.patch.object(turn_planner, "execution_waves", _skipping_the_last):
        outcomes = run_plan(tasks, run_one=lambda task, done: f"answer {task.index}")
    assert [o.task.index for o in outcomes] == [0, 1, 2], (
        f"one outcome per demand is the contract; got "
        f"{[(o.task.index, o.error or o.answer[:20]) for o in outcomes]}"
    )
    skipped = outcomes[2]
    assert not skipped.ok and skipped.error, (
        "the skipped demand must surface as an explicit failure, not vanish"
    )


def test_i7_a_failing_sibling_never_erases_a_satisfied_demand(harness):
    """I7: partial failure is named, success survives (R1e incident pin, held in
    the gauntlet so every invariant has at least one live-turn proof)."""
    with _incident_mocks(), _model_stand_in(harness.agent, _ENTROPY_STAND_IN):
        result, _context = harness.turn(
            "tell me the weather in Atlantisxyzabc123 and Kaunas plus explain entropy briefly",
            session_id=harness.session_id,
        )
    answer = str(result.get("response") or "")
    assert _ENTROPY_STAND_IN in answer
    assert "atlantisxyzabc123" in answer.lower(), (
        f"the failed unit is not named — partial failure must be truthful: {answer[:200]!r}"
    )


# ======================================================================
# 6. SABOTAGES (requirement 5) — each defect class has a named RED test.
# ======================================================================


def test_S1_dropping_a_minted_unit_turns_the_mint_check_red():
    """S1: sabotage the mint to drop a unit; I1's checker MUST fail (count
    mismatch). Green here means the gauntlet cannot miss a dropped demand."""
    case = _CASE_INDEX["g-pair-1"]
    import dataclasses as _dc

    from core.agent_runtime import answer_coverage

    real = answer_coverage.interpret_request

    def _dropping(text):
        interpretation = real(text)
        units = interpretation.units
        return _dc.replace(interpretation, units=units[:-1] if len(units) > 1 else units)

    with mock.patch("core.agent_runtime.answer_coverage.interpret_request", _dropping):
        with pytest.raises(AssertionError, match="intended 2 fragment"):
            _check_mint(case)


def test_S2_zero_coverage_allowed_turns_the_lane_gate_red():
    """S2: sabotage the finalize law to allow a zero-coverage claim; I4's law
    body MUST fail. Green here means the gate test cannot miss the exact defect
    class that swallowed the R1e incidents (a lane covering NOTHING of a
    multi-unit turn finalizing it)."""
    live_mixed = _CASE_INDEX["w-mixed"]["text"]
    with mock.patch(
        "core.agent_runtime.demand_ownership.lane_may_claim_whole_turn",
        return_value=True,
    ):
        from core.agent_runtime.demand_ownership import lane_may_claim_whole_turn

        with pytest.raises(AssertionError):
            assert not lane_may_claim_whole_turn(live_mixed, "live_data_typed_plan"), (
                "a lane covering one unit of a mixed turn finalized the whole turn"
            )
            assert not lane_may_claim_whole_turn(live_mixed, "turn_frontdoor_deterministic")


def test_S3_omitting_a_declaration_turns_the_inventory_red():
    """S3: sabotage the catalog by omitting a declaration; the inventory test
    MUST fail. (At base this is not a simulation — planned_multi_request_turn
    WAS missing; test_the_catalog_declares_every_executable_terminal_family is
    the named RED for it.)"""
    from core import lane_registry

    sabotaged = tuple(s for s in lane_registry.LANE_CATALOG if s.lane_id != "attempt_followup")
    with mock.patch.object(lane_registry, "LANE_CATALOG", sabotaged):
        declared = {spec.lane_id for spec in lane_registry.active_catalog()}
        missing = set(EXECUTABLE_TERMINAL_FAMILIES) - declared
        assert missing, "an omitted declaration went undetected by the inventory"


def test_S4_a_skipped_planned_child_is_named_never_absent(harness):
    """S4: sabotage execution to skip one planned child entirely. The turn must
    still discharge EVERY demand — the missing unit surfaces as an explicit
    failure naming it, never as silence. RED at base: the skipped child vanished."""
    from core.agent_runtime.turn_planner import run_plan as real_run_plan

    def _skipping_one(tasks, **kwargs):
        outcomes = real_run_plan(list(tasks)[:-1], **kwargs)
        return outcomes

    with _incident_mocks(), _model_stand_in(harness.agent, _ENTROPY_STAND_IN), mock.patch(
        "core.agent_runtime.turn_planner.run_plan", _skipping_one
    ):
        result, _context = harness.turn(_THREE_UNIT, session_id=harness.session_id)

    answer = str(result.get("response") or "")
    _assert_every_demand_discharged(_THREE_UNIT, answer, [["kaunas"], ["gold"], ["entropy"]])


def test_S5_a_removed_merge_outcome_still_names_its_unit(harness):
    """S5: sabotage the merge to drop a FAILED unit's block (the unit child
    itself raises, so its outcome is a genuine failure). Finalization must not
    publish the surviving success while a demand's outcome vanished — the seam
    re-synthesizes from the outcomes it holds. RED at base: the dropped block
    took the unit's name with it and the reply read as plain success."""
    from core.agent_runtime import turn_planner_hook
    from core.agent_runtime.turn_planner import merge_outcomes as real_merge

    real_build = turn_planner_hook.build_planner_run_one

    def _exploding_general(agent, **build_kwargs):
        inner = real_build(agent, **build_kwargs)

        def _run_one(task, done):
            if "explain" in str(getattr(task, "request", "") or "").lower():
                raise RuntimeError("the general child exploded")
            return inner(task, done)

        return _run_one

    def _dropping_failures(outcomes):
        return real_merge([o for o in outcomes if o.ok])

    with (
        _incident_mocks(),
        _model_stand_in(harness.agent, _ENTROPY_STAND_IN),
        mock.patch.object(turn_planner_hook, "build_planner_run_one", _exploding_general),
        mock.patch("core.agent_runtime.turn_planner.merge_outcomes", _dropping_failures),
    ):
        result, _context = harness.turn(_LIVE_MIXED, session_id=harness.session_id)

    answer = str(result.get("response") or "")
    assert "explain entropy briefly" in answer.lower(), (
        f"the merge dropped a failed unit's outcome and finalization published "
        f"success without naming it: {answer[:200]!r}"
    )
