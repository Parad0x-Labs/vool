"""The recovered presentation render lane, live at the finalize seam.

Pins the consolidation laws for ``core.presentation.answer_render``:

- byte-identical passthrough for everything the lane must not touch;
- verbatim-cell normalization with UNKNOWN preserved (never substituted);
- the round-trip guard refuses a renderer that changes the cell matrix;
- fail-open on any internal failure;
- SEAM ORDER: the lane runs at ``finalize_answer`` ABOVE the publication
  conservation verdict, so the sweep always judges the rendered bytes.
"""

from __future__ import annotations

import inspect
import random
import string

import core.finalization as finalization_module
from core.presentation.answer_render import normalize_structured_shape

# ---------------------------------------------------------------------------
# Byte-identical passthrough
# ---------------------------------------------------------------------------

PASSTHROUGH_CASES = {
    "prose_answer": "Paris is the capital of France.",
    "empty": "",
    "no_table_despite_election_context": "One heading\n\n- a\n- b\n- c\n",
    "ragged_table": "Rows:\n\n| A | B | C |\n| --- | --- | --- |\n| 1 | 2 |\n",
    "table_without_divider": "Rows:\n\n| A | B |\n| 1 | 2 |\n| 3 | 4 |\n",
}


def test_passthrough_is_byte_identical() -> None:
    for name, text in PASSTHROUGH_CASES.items():
        rendered, record = normalize_structured_shape(text, {})
        assert rendered == text, name
        assert record["normalized"] == [], name
        assert record["declined"], name


def test_disabled_election_declines() -> None:
    text = "Rows:\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"
    rendered, record = normalize_structured_shape(
        text, {"response_constraint": {"presentation_format": "table"}}
    )
    # An explicit contract stands the automatic authority down; the render lane
    # inherits that stand-down rather than competing with the explicit half.
    assert rendered == text
    assert str(record.get("declined") or "").startswith("disabled_by:")


# ---------------------------------------------------------------------------
# Normalization: verbatim cells, UNKNOWN preserved
# ---------------------------------------------------------------------------

TIGHT_TABLE = (
    "The readings follow.\n\n"
    "|Asset|Price|\n| --- | --- |\n|Gold|2700|\n|Silver|31|\n"
    "\nThat is all."
)


def test_elected_table_is_normalized_well_formed() -> None:
    rendered, record = normalize_structured_shape(TIGHT_TABLE, {})
    assert record["normalized"] == ["table"]
    assert record["cells_before"] == record["cells_after"]
    assert "| Asset | Price |" in rendered
    assert "| Gold | 2700 |" in rendered
    assert "| Silver | 31 |" in rendered
    # Structure around the table is untouched.
    assert rendered.startswith("The readings follow.")
    assert rendered.endswith("That is all.")


def test_unknown_cell_is_never_substituted() -> None:
    text = (
        "X.\n\n|Asset|Price|\n| --- | --- |\n|Gold|2700|\n|Silver|unknown|\n"
    )
    rendered, record = normalize_structured_shape(text, {})
    assert record["normalized"] == ["table"]
    assert "| Silver | unknown |" in rendered
    assert "—" not in rendered and "N/A" not in rendered


def test_random_cells_round_trip_verbatim() -> None:
    rng = random.Random(20260908)
    for _ in range(25):
        rows = [
            ["".join(rng.choice(string.ascii_letters + " 0123456789.,") for _ in range(rng.randint(1, 9)))
             for _ in range(3)]
            for _ in range(rng.randint(1, 5))
        ]
        table = "| A | B | C |\n| --- | --- | --- |\n" + "\n".join(
            "| " + " | ".join(row) + " |" for row in rows
        )
        text = f"Intro.\n\n{table}\n\nOutro."
        rendered, record = normalize_structured_shape(text, {})
        assert record["normalized"] == ["table"]
        # The parser's documented law: cells are stripped at parse, so the
        # verbatim expectation is the stripped cell value, never re-authored.
        for row in rows:
            expected = "| " + " | ".join(cell.strip() for cell in row) + " |"
            assert expected in rendered


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def test_round_trip_guard_refuses_a_renderer_that_drops_a_row(monkeypatch) -> None:
    from core.presentation import answer_render

    def dropping_renderer(doc):  # sabotage: loses the last row
        from core.presentation.model import RenderDocument, Table

        blocks = [
            Table(headers=b.headers, rows=b.rows[:-1]) if isinstance(b, Table) else b
            for b in doc.blocks
        ]
        from core.presentation.render_markdown import MarkdownRenderer

        return MarkdownRenderer().render(RenderDocument(blocks=blocks))

    monkeypatch.setattr(answer_render, "MarkdownRenderer", lambda: type("R", (), {"render": staticmethod(dropping_renderer)}))
    text = "X.\n\n| A | B |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"
    rendered, record = normalize_structured_shape(text, {})
    assert rendered == text
    assert record["declined"] == "round_trip_diverged"


def test_fail_open_when_selection_raises(monkeypatch) -> None:
    import core.presentation_selection as selection_module

    def boom(text, source_context=None):
        raise RuntimeError("selection exploded")

    monkeypatch.setattr(selection_module, "select_presentation", boom)
    text = TIGHT_TABLE
    rendered, record = normalize_structured_shape(text, {})
    assert rendered == text
    assert record["declined"] == "selection_unavailable"


def test_render_lane_never_raises_on_garbage() -> None:
    for garbage in (None, 123, object()):
        _rendered, record = normalize_structured_shape(garbage, None)  # type: ignore[arg-type]
        assert record["declined"]


# ---------------------------------------------------------------------------
# Seam order: the sweep judges the RENDERED bytes
# ---------------------------------------------------------------------------

def test_finalize_seam_orders_render_above_conservation_verdict() -> None:
    source = inspect.getsource(finalization_module.finalize_answer)
    render_at = source.index("normalize_structured_shape(")
    verdict_at = source.index("_semantic_conservation_verdict(")
    assert render_at < verdict_at, (
        "the render lane must run BEFORE the publication-conservation verdict: "
        "the sweep must judge the rendered bytes, never pre-render bytes"
    )


def test_render_lane_record_rides_display_metadata() -> None:
    source = inspect.getsource(finalization_module.finalize_answer)
    assert '"render_lane"' in source, "the render-lane record must ride the commit's display metadata"
