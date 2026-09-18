"""Answer presentation (C19): explicit format requests win, defaults stay prose, nothing is invented.

Three laws pinned here, all through the EXISTING constraint authority (`core.response_constraints`)
— this module adds no second parser, no second enforcement seam:

- EXPLICIT WINS: "show it as a table" binds a presentation contract, and a prose answer to it
  is a shape violation that routes into the same bounded repair as any other shape miss;
- ONLY WHEN USEFUL: without an explicit request there is no presentation contract at all, and
  topic nouns ("org chart", "graph database", "tree of life") never bind one;
- NEVER INVENT: the checks are pure observation, the fallback fabricates no values, and a
  compliant answer's facts, citations and receipts pass through enforcement untouched.
"""
from __future__ import annotations

import pytest

from core.response_constraints import (
    PRESENTATION_FORMATS,
    check_response_constraint,
    constraint_safe_fallback,
    enforce_response_constraint,
    formatting_retry_instruction,
    parse_response_constraint,
    response_constraint_from_metadata,
)

_TABLE_ANSWER = """Three options compared:

| Option | Price | Support |
|---|---|---|
| Alpha | €10 [1] | email |
| Beta | €25 | 24/7 |"""

_TIMELINE_ANSWER = """- 1947 — the transistor is demonstrated
- 1958 — the integrated circuit follows
- 1971 — the first microprocessor ships"""

_TREE_ANSWER = """repo
  core/
    runtime
    storage
  apps/
    server"""


@pytest.mark.parametrize(
    ("user_text", "expected_format"),
    [
        ("compare the plans and show it as a table", "table"),
        ("compare the plans in a table please", "table"),
        ("give me a timeline of the company's acquisitions", "timeline"),
        ("make a timeline of the roman republic", "timeline"),
        ("show the module structure as a tree", "tree"),
        ("draw the syntax as a mermaid diagram", "mermaid"),
        ("present the numbers as a bar chart", "chart"),
        ("show the growth as a graph", "chart"),
    ],
)
def test_an_explicit_format_request_binds_the_presentation_contract(
    user_text: str, expected_format: str
) -> None:
    constraint = parse_response_constraint(user_text)
    assert constraint is not None, user_text
    assert constraint.presentation_format == expected_format, user_text


@pytest.mark.parametrize(
    "user_text",
    [
        "the graph database market is growing",
        "what does the org chart at my company look like",
        "read the tree of life document in my workspace",
        "write a poem about a tree",
        "summarise chapter two of the report",
    ],
)
def test_a_topic_noun_never_binds_a_presentation_contract(user_text: str) -> None:
    constraint = parse_response_constraint(user_text)
    assert constraint is None or constraint.presentation_format is None, user_text


def test_no_explicit_request_leaves_the_default_presentation_free() -> None:
    """Only-when-useful: without explicit language there is no shape contract,
    so prose stays the default and no check can fail an answer for its shape."""
    constraint = parse_response_constraint("compare the three insurance plans for me")
    assert constraint is None


def test_a_prose_answer_to_an_explicit_table_request_is_a_shape_violation() -> None:
    """Explicit wins: the default prose shape does not satisfy an explicit request —
    the same violation channel as any other shape miss, so the bounded repair runs."""
    constraint = parse_response_constraint("compare the plans and show it as a table")
    assert constraint is not None
    prose = "Alpha costs ten euros with email support, while Beta costs twenty-five with round-the-clock support."
    check = check_response_constraint(prose, constraint)
    assert not check.compliant
    assert "presentation_format" in check.violations
    # The delivered table satisfies it.
    compliant = check_response_constraint(_TABLE_ANSWER, constraint)
    assert compliant.compliant, compliant.violations


@pytest.mark.parametrize(
    ("answer", "format_name"),
    [
        (_TIMELINE_ANSWER, "timeline"),
        (_TREE_ANSWER, "tree"),
    ],
)
def test_text_renderable_formats_accept_their_honest_shape(answer: str, format_name: str) -> None:
    constraint = parse_response_constraint(f"present it as a {format_name}")
    assert constraint is not None and constraint.presentation_format == format_name
    check = check_response_constraint(answer, constraint)
    assert check.compliant, (format_name, check.violations)


def test_mermaid_and_chart_require_the_shipped_renderable_payload() -> None:
    from core.response_constraints import SURFACE_RENDERS_PRESENTATION

    assert SURFACE_RENDERS_PRESENTATION["mermaid"] is True
    assert SURFACE_RENDERS_PRESENTATION["chart"] is True

    mermaid_constraint = parse_response_constraint("draw the flow as a mermaid diagram")
    fenced_source = "```mermaid\ngraph TD; A-->B;\n```"
    assert check_response_constraint(fenced_source, mermaid_constraint).compliant
    assert not check_response_constraint("the flow goes from A to B.", mermaid_constraint).compliant

    chart_constraint = parse_response_constraint("show the numbers as a chart")
    values_table = "| Month | Revenue |\n|---|---|\n| Jan | 1,200 |\n| Feb | 1,850 |"
    assert not check_response_constraint(values_table, chart_constraint).compliant
    chart = '```chart\n{"type":"bar","data":{"labels":["Jan","Feb"],"datasets":[{"label":"Revenue","data":[1200,1850]}]}}\n```'
    assert check_response_constraint(chart, chart_constraint).compliant
    prose_without_values = "Revenue grew nicely over the two months."
    assert not check_response_constraint(prose_without_values, chart_constraint).compliant


def test_the_fallback_never_invents_values_and_lands_in_text_or_table() -> None:
    for format_name in PRESENTATION_FORMATS:
        constraint = parse_response_constraint(f"present it as a {format_name}")
        assert constraint is not None
        fallback = constraint_safe_fallback(constraint)
        assert fallback.strip(), format_name
        # The fallback fabricates nothing: it carries no number that never arrived.
        import re

        assert not re.search(r"\d", fallback), (
            f"{format_name} fallback invented a value: {fallback!r}"
        )
    # The table/chart fallback lands as a table — the readable form this surface supports.
    table_fallback = constraint_safe_fallback(parse_response_constraint("show it as a table"))
    assert table_fallback.startswith("| Result |")


def test_enforcement_preserves_facts_citations_and_receipts_verbatim() -> None:
    """A compliant answer passes through enforcement byte-identical: citations, receipts
    and values are never rewritten by the presentation layer."""
    constraint = parse_response_constraint("compare the plans and show it as a table")
    answer = _TABLE_ANSWER + "\n\nSource: workspace/quote.md [1] (receipt: read 1 file, 0 writes)"
    application = enforce_response_constraint(answer, constraint)
    assert application.compliant, application.violations
    assert application.text == answer, "a compliant answer was rewritten by the shape layer"
    for token in ("[1]", "€10", "€25", "receipt: read 1 file, 0 writes"):
        assert token in application.text


def test_the_retry_instruction_names_the_requested_format() -> None:
    constraint = parse_response_constraint("compare the plans and show it as a table")
    instruction = formatting_retry_instruction(constraint)
    assert "table" in instruction
    mermaid_instruction = formatting_retry_instruction(
        parse_response_constraint("draw the flow as a mermaid diagram")
    )
    assert "mermaid" in mermaid_instruction
    chart_instruction = formatting_retry_instruction(
        parse_response_constraint("show the numbers as a chart")
    )
    assert "fenced chart JSON" in chart_instruction
    assert "never invent" in chart_instruction or "ONLY" in chart_instruction


def test_the_presentation_contract_round_trips_through_turn_metadata() -> None:
    constraint = parse_response_constraint("compare the plans and show it as a table")
    restored = response_constraint_from_metadata({"response_constraint": constraint.to_dict()})
    assert restored is not None
    assert restored.presentation_format == "table"
    # A constraint with NO shape at all still parses to None on the way back.
    assert response_constraint_from_metadata({"response_constraint": {}}) is None


# ---------------------------------------------------------------------------
# The presentation doctrine rides the ONE skill contract
# ---------------------------------------------------------------------------


def test_the_presentation_doctrine_is_an_ordinary_typed_skill() -> None:
    from core.native_skill_library import (
        guidance_for_selection,
        load_skill_contracts,
        select_native_skills,
    )

    library = load_skill_contracts()
    contract = next((c for c in library.contracts if c.id == "answer-presentation"), None)
    assert contract is not None, "the presentation doctrine is not in the ONE library"
    assert contract.risk_class == "read_only"
    assert contract.version == "1.0.0"

    selection = select_native_skills(task_class="general_advisory", user_text="", library=library)
    chosen = {c.id for c in selection.selected}
    assert "answer-presentation" in chosen, chosen

    text, provenance, permitted = guidance_for_selection(
        tuple(c for c in selection.selected if c.id == "answer-presentation")
    )
    assert "explicit user format request" in text or "explicit format" in text.lower()
    assert provenance and provenance[0]["version"] == "1.0.0", (
        "the turn must record WHICH presentation doctrine version influenced it"
    )
    assert not permitted, "presentation doctrine declares no tools and must narrow nothing"

    # Not on turns whose answer is a single fact or a repair.
    unrelated = select_native_skills(task_class="debugging", user_text="", library=library)
    assert "answer-presentation" not in {c.id for c in unrelated.selected}
