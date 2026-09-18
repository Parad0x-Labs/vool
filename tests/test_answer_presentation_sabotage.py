"""Sabotage proofs — the load-bearing guards of answer presentation (C19).

Each test disables ONE mechanism in the PHYSICAL source (byte-exact restore in
a ``finally``, house ``source_sabotage`` pattern per
``validation-logs/native-skill-product-family-20260904/EVIDENCE.md`` and the
turn-context campaign) and proves BOTH directions:

    GUARDED  — the healthy mechanism holds the law, and
    SABOTAGED — with the guard's code neutralized, the law silently breaks,
                which is exactly what the pinning packs
                (tests/test_answer_presentation.py,
                tests/test_presentation_selection.py) protect.

A guard whose removal changes nothing is not load-bearing. The earlier draft
of two of these tests asserted on LOCAL STAND-IN functions and passed under
real sabotage (falsifier verdict 25); every proof here observes through the
REAL module functions after a physical mutation and a module reload.

Sabotage matrix rows proven here:

    X-a  constraint_safe_fallback fabricates a value  → the no-invented-values
         law of the fallback breaks (no-digit property flips).
    X-b  formatting_retry_instruction goes format-blind → the repair
         instruction stops naming the requested shape.
    X1   the automatic selector always elects "table" → prose answers get
         elected a shape (T4's prose pin flips).
    X2   the automatic selector never ratifies → observed shapes are reported
         as prose (T2's ratify pin flips).
    X4   selection_repair_acceptance drops the citation check → a repair that
         loses receipts is accepted (T10's multiset-survival pin flips).

Run STANDALONE (never in parallel with other suites): these mutate core source
at runtime and restore byte-exact in ``finally``.
"""
from __future__ import annotations

import pytest

from tests.test_turn_context_sabotage import source_sabotage

RESPONSE_CONSTRAINTS = "core/response_constraints.py"
PRESENTATION_SELECTION = "core/presentation_selection.py"

_PROSE = "Alpha costs ten euros with email support, while Beta costs twenty-five."

_HONEST_TABLE_FALLBACK = """        if constraint.presentation_format in ("table", "chart"):
            return (
                "| Result |\\n|---|\\n"
                "| No usable answer was produced for this request. |"
            )"""
_FABRICATED_TABLE_FALLBACK = """        if constraint.presentation_format in ("table", "chart"):
            return (
                "| Month | Revenue |\\n|---|---|\\n"
                "| Jan | 1,200 |\\n| Feb | 1,850 |"
            )"""


def test_sabotage_presentation_check_removed_lets_prose_satisfy_an_explicit_table_request() -> None:
    """Guard: an explicit format request outranks the default prose shape."""
    from core.response_constraints import (
        check_response_constraint,
        parse_response_constraint,
    )

    constraint = parse_response_constraint("compare the plans and show it as a table")
    healthy = check_response_constraint(_PROSE, constraint)
    assert not healthy.compliant, "sabotage precondition broken: prose already satisfies a table"

    import re

    from core import response_constraints as rc

    monkey_format = pytest.MonkeyPatch()
    monkey_format.setattr(rc, "_presentation_answer_has_format", lambda text, fmt: True)
    try:
        sabotaged = rc.check_response_constraint(_PROSE, constraint)
        assert sabotaged.compliant, (
            "sabotage no-op: removing the presentation check still rejected the prose answer"
        )
        assert re.search(r"\d", "1,200")  # keep the import meaningful for readers
    finally:
        monkey_format.undo()


def test_sabotage_fabricating_fallback_breaks_the_no_invented_values_law() -> None:
    """X-a (physical): the fallback fabricates NOTHING. Mutate the honest table
    fallback into a fabricated-values one and the REAL function — the one the
    no-digit pin asserts on — starts inventing numbers. The old draft of this
    proof asserted on a local stand-in and passed under real sabotage."""
    import re

    from core.response_constraints import (
        constraint_safe_fallback,
        parse_response_constraint,
    )

    constraint = parse_response_constraint("show the numbers as a chart")
    healthy = constraint_safe_fallback(constraint)
    assert not re.search(r"\d", healthy), "sabotage precondition broken: fallback has numbers"

    with source_sabotage(
        "core.response_constraints",
        RESPONSE_CONSTRAINTS,
        [(_HONEST_TABLE_FALLBACK, _FABRICATED_TABLE_FALLBACK)],
    ) as sabotaged_module:
        fabricated = sabotaged_module.constraint_safe_fallback(constraint)
        assert re.search(r"\d", fabricated), (
            "sabotage no-op: the physically mutated fallback still fabricates no values — "
            "the no-invented-values pin would not bite"
        )


def test_sabotage_format_blind_retry_instruction_stops_naming_the_shape() -> None:
    """X-b (physical): the repair instruction NAMES the requested format. A
    physically format-blind instruction flips the guarded property through the
    REAL function the repair loop calls."""
    from core.response_constraints import formatting_retry_instruction, parse_response_constraint

    constraint = parse_response_constraint("compare the plans and show it as a table")
    healthy = formatting_retry_instruction(constraint)
    assert "table" in healthy, "sabotage precondition broken: instruction already format-blind"

    with source_sabotage(
        "core.response_constraints",
        RESPONSE_CONSTRAINTS,
        [
            (
                'requirements.append(f"the answer presented as a {format_name}")',
                'requirements.append("the answer in the shape you think best")',
            )
        ],
    ) as sabotaged_module:
        blind = sabotaged_module.formatting_retry_instruction(constraint)
        assert "table" not in blind, (
            "sabotage no-op: the physically mutated instruction still names the format"
        )


def test_sabotage_selector_always_electing_table_breaks_the_prose_default() -> None:
    """X1 (physical): the selector always elects a table. Prose answers must
    stay prose (T4); the physical mutation flips that observable through the
    REAL select_presentation."""
    from core import presentation_selection as ps

    healthy = ps.select_presentation(_PROSE, {})
    assert healthy["elected"] == "prose", "sabotage precondition broken: prose already elected"

    with source_sabotage(
        "core.presentation_selection",
        PRESENTATION_SELECTION,
        [
            (
                'return _record(turn_id, elected="prose", trigger="none_justified")',
                'return _record(turn_id, elected="table", trigger="tabular_rows_ge2")',
            )
        ],
    ) as sabotaged_module:
        elected = sabotaged_module.select_presentation(_PROSE, {})
        assert elected["elected"] == "table", (
            "sabotage no-op: the mutated selector did not flip the prose default — "
            "the T4/topic-noun pins would not bite"
        )


def test_sabotage_selector_that_never_ratifies_reports_observed_shapes_as_prose() -> None:
    """X2 (physical): the ratify stage removed. An answer that already carries
    a table must ratify (T2); the physical mutation flips that observable."""
    from core import presentation_selection as ps

    table = "| Month | Revenue |\n|---|---|\n| Jan | 1,200 |\n| Feb | 1,850 |"
    healthy = ps.select_presentation(table, {})
    assert healthy["elected"] == "table" and healthy["gap_detected"] is False, (
        "sabotage precondition broken: the table answer was never ratified"
    )

    with source_sabotage(
        "core.presentation_selection",
        PRESENTATION_SELECTION,
        [
            (
                "    ratified = _ratify_shape(cleaned)",
                "    ratified = None if True else _ratify_shape(cleaned)",
            )
        ],
    ) as sabotaged_module:
        demoted = sabotaged_module.select_presentation(table, {})
        assert demoted["elected"] == "prose" or demoted["gap_detected"] is True, (
            "sabotage no-op: removing ratification did not change the record — "
            "the T2 ratify pin would not bite"
        )


def test_sabotage_acceptance_without_the_citation_check_lets_citations_vanish() -> None:
    """X4 (physical): the citation multiset check removed from repair
    acceptance. A repair that loses a citation family must be refused (T10);
    the physical mutation flips that observable. Memory markers are the case
    ONLY the multiset check guards — receipt strings have their own check."""
    from core import presentation_selection as ps

    original = (
        "Plan A costs €10 with basic coverage [memory:node-a]. "
        "Plan B costs €25 with full coverage [memory:node-b]."
    )
    citationless = (
        "| Plan | Cost | Coverage |\n|---|---|---|\n| A | €10 | basic |\n| B | €25 | full |"
    )
    assert not ps.selection_repair_acceptance(original, citationless, "comparison_matrix"), (
        "sabotage precondition broken: acceptance already tolerates dropped citations"
    )

    with source_sabotage(
        "core.presentation_selection",
        PRESENTATION_SELECTION,
        [
            (
                "    if _citation_family_counts(repaired_text) != _citation_family_counts(original_text):\n"
                "        return False",
                "    if False:\n        return False",
            )
        ],
    ) as sabotaged_module:
        assert sabotaged_module.selection_repair_acceptance(
            original, citationless, "comparison_matrix"
        ), (
            "sabotage no-op: dropping the citation check did not loosen acceptance — "
            "the T10 multiset-survival pin would not bite"
        )
