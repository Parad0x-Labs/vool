"""C19 automatic presentation selection: the deterministic authority's unit proofs.

T1-T13 of the specification's RED->GREEN plan, all through the one selector
(``core.presentation_selection.select_presentation``) and its acceptance
function. Pattern of ``tests/test_answer_presentation.py``: pure, stdlib-only,
no served daemon. The selector is prompt-blind by construction; these tests
also pin that construction (T4).
"""
from __future__ import annotations

import json

import pytest

from core.presentation_selection import (
    AUTO_NEVER,
    AUTO_PRESENTATION_FORMATS,
    SELECTION_SCHEMA,
    select_presentation,
    selection_record_json,
    selection_repair_acceptance,
)
from core.response_constraints import SURFACE_RENDERS_PRESENTATION

PLAN_PROSE = (
    "Plan A: deductible €10, coverage basic, price cheap overall.\n"
    "Plan B: deductible €25, coverage full, price premium overall.\n"
    "Plan C: deductible €40, coverage basic, price mid overall."
)
TABLE = "| Month | Revenue |\n|---|---|\n| Jan | 1,200 |\n| Feb | 1,850 |"
TIMELINE_PROSE = "Series A closed in 2019. Series B followed in 2020. The IPO came in 2021."


def _record(**overrides):
    return select_presentation(overrides.pop("bytes", PLAN_PROSE), overrides.pop("context", {}))


@pytest.mark.parametrize("text", [
    "I could not finish checking which station you meant. Please name the city.",
    "| Candidate | Region |\n|---|---|\n| North depot | East |\n| South depot | West |",
])
def test_unresolved_adjudication_is_not_elected_as_an_answer(text):
    result = select_presentation(text, {
        "ambiguity_adjudication": {"state": "unresolved", "mode": "raised", "attempts": 2},
    })
    assert result["disabled_by"] == "refusal_signal"
    assert result["elected"] is None


def test_resolved_adjudication_does_not_disable_a_valid_table():
    result = select_presentation(TABLE, {"ambiguity_adjudication": {"state": "unambiguous"}})
    assert result["disabled_by"] is None


# --------------------------------------------------------------------------- T1


@pytest.mark.parametrize('capable', [False, True])
def test_t1_auto_never_formats_cannot_be_elected_even_with_capability_flipped(capable):
    """mermaid/chart/pie_chart are explicit-request-only forever: election must
    skip them even when someone flips the surface capability to True."""
    flipped = dict(SURFACE_RENDERS_PRESENTATION)
    monkey_capable = {**flipped, "mermaid": capable, "chart": capable}
    # The selector reads the live map, so flip it and try every input that a
    # dishonest selector might read as a diagram or a chart.
    from core import presentation_selection as ps

    original = dict(ps.SURFACE_RENDERS_PRESENTATION)
    try:
        ps.SURFACE_RENDERS_PRESENTATION.clear()
        ps.SURFACE_RENDERS_PRESENTATION.update(monkey_capable)
        fenced_mermaid = "```mermaid\ngraph TD; A-->B;\n```"
        for text in (fenced_mermaid, TABLE, "value 10 and value 20", PLAN_PROSE):
            record = select_presentation(text, {})
            assert record["elected"] not in AUTO_NEVER
            assert record["elected"] in AUTO_PRESENTATION_FORMATS
    finally:
        ps.SURFACE_RENDERS_PRESENTATION.clear()
        ps.SURFACE_RENDERS_PRESENTATION.update(original)


# --------------------------------------------------------------------------- T2


@pytest.mark.parametrize(
    ("text", "elected", "trigger"),
    [
        (TABLE, "table", "tabular_rows_ge2"),
        (
            "| Plan | Deductible | Coverage |\n|---|---|---|\n| A | €10 | basic |\n| B | €25 | full |",
            "comparison_matrix",
            "subjects_ge2_shared_attributes_ge2",
        ),
        ("- 2019: founded\n- 2020: launched\n- 2021: profit", "timeline", "dated_events_ge2"),
        ("CEO\n  CTO\n    Platform\n  CFO", "tree", "containment_depth_ge2"),
        ("1. Open settings\n2. Click security", "numbered_steps", "ordered_steps_ge2"),
        ("- alpha\n- beta\n- gamma", "bullets", "parallel_items_ge3"),
    ],
)
def test_t2_ratify_observed_shapes_without_mutation(text, elected, trigger):
    before = text
    record = select_presentation(text, {})
    assert text == before, "the selector mutated its input"
    assert record["elected"] == elected
    assert record["trigger"] == trigger
    assert record["gap_detected"] is False
    assert record["disabled_by"] is None
    assert record["origin"] == "automatic"
    assert record["schema"] == SELECTION_SCHEMA


# --------------------------------------------------------------------------- T3


def test_t3_prose_over_tabular_shape_elects_and_records_the_gap():
    record = select_presentation(PLAN_PROSE, {})
    assert record["elected"] == "comparison_matrix"
    assert record["trigger"] == "subjects_ge2_shared_attributes_ge2"
    assert record["gap_detected"] is True
    # Slice 1 honesty: the record ships, the prose ships unchanged — no
    # fallback is claimed because no repair ran on this path.
    assert record["fallback"] is None
    assert selection_record_json(record)  # bounded JSON round trip (T9 too)


# --------------------------------------------------------------------------- T4


def test_t4_topic_nouns_never_bind_and_the_selector_never_sees_the_prompt():
    """L2/P5: "org chart", "tree of life", "graph database" are topic nouns. The
    selector's inputs are the answer bytes and the typed context only — the
    user's words are not an input, so a hostile prompt cannot elect a shape."""
    prose = "Graph databases store relationships between records efficiently."
    record = select_presentation(prose, {"user_text": "draw a mermaid diagram of org charts"})
    assert record["elected"] == "prose"
    assert record["trigger"] == "none_justified"
    # A genuinely tabular answer elects because of the ANSWER, with the same
    # hostile prompt present.
    tabular = select_presentation(TABLE, {"user_text": "tell me about graph databases"})
    assert tabular["elected"] == "table"
    assert tabular["trigger"] == "tabular_rows_ge2"
    # P5: selection consumes response_constraint_from_metadata(source_context)
    # only — the exact law agent.py's caller-key pop enforces upstream. A
    # caller-supplied contract dict is read through that one closed door.
    record = select_presentation(
        PLAN_PROSE, {"response_constraint": {"presentation_format": "table"}}
    )
    assert record["disabled_by"] == "explicit_request"


# --------------------------------------------------------------------------- T5


@pytest.mark.parametrize("format_name", ["table", "timeline", "tree", "mermaid", "chart"])
def test_t5_explicit_format_requests_stand_the_selection_down(format_name):
    for constraint in (
        {"presentation_format": format_name},
        {"list_items": 3, "one_item_per_line": True},
    ):
        record = select_presentation(PLAN_PROSE, {"response_constraint": constraint})
        assert record["disabled_by"] == "explicit_request"
        assert record["elected"] is None


# --------------------------------------------------------------------------- T6


def test_t6_exact_contract_stands_the_selection_down():
    record = select_presentation(PLAN_PROSE, {"raw_output_contract": {"exact_text": "OK"}})
    assert record["disabled_by"] == "exact_contract"
    assert record["elected"] is None


# --------------------------------------------------------------------------- T7


def test_t7_x_editorial_turns_stands_the_selection_down():
    record = select_presentation(PLAN_PROSE, {"x_editorial_turn": {"draft_id": "x1"}})
    assert record["disabled_by"] == "x_editorial"


# --------------------------------------------------------------------------- T8


def test_t8_ordinary_chat_policy_stands_the_selection_down():
    """C4: only the lane whose 64-word ceiling actually applies stands the
    selection down — a detail request that lifted the ceiling leaves election
    honest (the trim could never bite there)."""
    ceiling = select_presentation(
        PLAN_PROSE,
        {"ordinary_chat_output_policy": {"mode": "ordinary_chat", "max_words": "64"}},
    )
    assert ceiling["disabled_by"] == "ordinary_chat_guard"
    lifted = select_presentation(
        PLAN_PROSE,
        {"ordinary_chat_output_policy": {"mode": "ordinary_chat", "max_words": ""}},
    )
    assert lifted["disabled_by"] is None
    assert lifted["elected"] == "comparison_matrix"


# --------------------------------------------------------------------------- T9


def test_t9_record_convention_and_bound_json():
    prose = select_presentation("A single plain sentence with no shape.", {})
    assert prose["elected"] == "prose"
    assert prose["trigger"] == "none_justified"
    assert prose["disabled_by"] is None
    gap = select_presentation(PLAN_PROSE, {})
    assert gap["elected"] == "comparison_matrix"
    assert gap["gap_detected"] is True
    for record in (prose, gap, select_presentation(TABLE, {})):
        assert (record["elected"] is None) == (record["disabled_by"] is not None)
        payload = selection_record_json(record)
        assert len(payload) <= 512
        assert json.loads(payload) == record


# --------------------------------------------------------------------------- T10


def _repaired(table_rows: str) -> str:
    return table_rows


GOOD_REPAIR = (
    "| Plan | Deductible | Coverage |\n|---|---|---|\n"
    "| A | €10 [receipt: read 1 file, 0 writes] | basic |\n| B | €25 | full |"
)


def test_t10_acceptance_holds_only_when_truth_survives():
    original = (
        "Plan A: deductible [receipt: read 1 file, 0 writes] €10, coverage basic. "
        "Plan B: deductible €25, coverage full [unverified - model memory]."
    )
    kept_citation = GOOD_REPAIR.replace(
        "| B | €25 | full |", "| B | €25 | full [unverified - model memory] |"
    )
    assert selection_repair_acceptance(original, kept_citation, "comparison_matrix")
    # Dropped citation prefix family.
    assert not selection_repair_acceptance(original, GOOD_REPAIR, "comparison_matrix")
    # Reworded quantity: 25 became 26 — precision/identity broken (L4).
    changed_number = kept_citation.replace("€25", "€26")
    assert not selection_repair_acceptance(original, changed_number, "comparison_matrix")
    # Thousands separators are formatting: "1,420" == "1420".
    thousands = "| N | V |\n|---|---|\n| a | 1,420 |\n| b | 5 |"
    assert selection_repair_acceptance("N is 1420 and V is 5.", thousands, "table")
    # A decimal never matches digits reordered: "1.75" is not "175".
    decimal = "| N | V |\n|---|---|\n| a | 1.75 |\n| b | 5 |"
    assert not selection_repair_acceptance("N is 175 and V is 5.", decimal, "table")
    # Precision may drop, never appear: 1.98 is an honest rounding of 1.982228298.
    rounding = "| N | V |\n|---|---|\n| a | 1.98 |\n| b | 5 |"
    assert selection_repair_acceptance("N is 1.982228298 and V is 5.", rounding, "table")
    # A unit the original attached cannot disappear (part of quantity identity).
    dropped_unit = kept_citation.replace("€10", "10")
    assert not selection_repair_acceptance(original, dropped_unit, "comparison_matrix")
    # Empty cells are dishonest unknowns (L7).
    empty_cell = (
        "| Plan | Deductible | Coverage |\n|---|---|---|\n| A | €10 |  |\n| B | €25 | full |"
    )
    assert not selection_repair_acceptance(original, empty_cell, "comparison_matrix")
    # A ragged table is not a shape at all.
    ragged = "| Plan | Deductible | Coverage |\n|---|---|---|\n| A | €10 |\n| B | €25 | full |"
    assert not selection_repair_acceptance(original, ragged, "comparison_matrix")
    # The AUTO_NEVER law binds acceptance too.
    assert not selection_repair_acceptance(original, kept_citation, "mermaid")


# --------------------------------------------------------------------------- T11


def test_t11_acceptance_failure_never_adds_content_the_original_lacks():
    """No-invention (L4): a repair that invents a category/number fails, so the
    original bytes — which lack it — are what ships."""
    original = "Rate: 3.5%. Fee: €20."
    invented = (
        "| Rate | Fee | Tier |\n|---|---|---|\n| 3.5% | €20 | premium |\n| 4.0% | €30 | basic |"
    )
    assert not selection_repair_acceptance(original, invented, "table")
    honest = "| Rate | Fee |\n|---|---|\n| 3.5% | €20 |"
    assert selection_repair_acceptance(original, honest, "table")


# --------------------------------------------------------------------------- T12


def test_t12_identical_inputs_yield_identical_records():
    first = select_presentation(PLAN_PROSE, {"cancel_turn_id": "turn-1"})
    second = select_presentation(PLAN_PROSE, {"cancel_turn_id": "turn-1"})
    assert first == second
    assert selection_record_json(first) == selection_record_json(second)


# --------------------------------------------------------------------------- T13


def test_t13_grounding_refused_turn_re_stamps_the_shipped_record():
    """The post-gate authority: on a REFUSED turn the shipped record says the
    grounding gate stood down — never a shape describing refused bytes."""
    from tests.test_unsupported_current_claims_cannot_publish_m3 import (
        RUST_FABRICATION,
        build_turn,
        publish,
    )

    context = build_turn()
    commit = publish(context, RUST_FABRICATION)
    publication = dict((commit.get("grounding_lifecycle") or {}).get("publication") or {})
    assert publication.get("state") == "refused", (
        "this proof requires a really refused turn, not a stubbed gate"
    )
    record = dict((commit.get("display_metadata") or {}).get("presentation_selection") or {})
    assert record["disabled_by"] == "grounding_gate"
    assert record["elected"] is None
    assert record["origin"] == "automatic"
    assert record["schema"] == SELECTION_SCHEMA
