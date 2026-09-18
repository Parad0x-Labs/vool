"""The one seam where the recovered presentation render lane touches served bytes.

Recovered from ``recovery/historical-gold/presentation-render`` (the lost
presentation family) and wired into ``core.finalization.finalize_answer``
between the RSS closure sweep and the publication-conservation verdict —
ON PURPOSE above the verdict, so the sweep always judges the RENDERED bytes:
a renderer that dropped a slot would record a conservation violation and,
under ``VOOL_SEMANTIC_CONSERVATION=enforce``, refuse the turn.

Laws this seam inherits from the consolidation mandate:

- **ADDITIVE/NORMALIZING ONLY.** The lane may re-form structure the bytes
  already carry (a ragged markdown table becomes a well-formed one); it may
  never add, drop or rewrite a cell. Cell values are copied verbatim, and the
  ``Table`` model law stands: missing data stays as supplied, renderers may
  NOT substitute em-dashes or blanks for UNKNOWN.
- **NEVER INVENT.** No cell, row, value or section is fabricated. A table the
  parser cannot round-trip cell-for-cell is declined and passes through
  byte-identical.
- **ELECTION-GATED, PROMPT-BLIND.** Rendering happens only for a shape the
  C19 authority (``core.presentation_selection.select_presentation``)
  elected from the bytes themselves; the user's prompt text is never read
  here. ``mermaid``/``chart``/``pie_chart`` remain AUTO_NEVER: text-chart
  primitives exist in this package, but no structured-series authority feeds
  them yet, and the C19 law is not weakened to pretend otherwise.
- **FAIL-OPEN, BOUNDED.** Any anomaly returns the original bytes with a
  typed decline record. Presentation can only normalize; a failure must not
  end a working turn.
"""

from __future__ import annotations

import re
from typing import Any

from core.presentation.model import RenderDocument, Table
from core.presentation.render_markdown import MarkdownRenderer

#: Schema tag of the render-lane record (provenance only; never answer bytes).
RENDER_LANE_SCHEMA = "vool.answer_render.v1"

#: Shapes whose delivered structure this lane may normalize. Deliberately
#: excludes every AUTO_NEVER shape and the prose/list shapes (marker lines
#: already ARE the elected shape).
_NORMALIZABLE_SHAPES = frozenset({"table", "comparison_matrix"})

#: The C19 election keys this seam reads. ``elected`` is None exactly when
#: ``disabled_by`` is set; both absent means "not a selection record" -> decline.
_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_DIVIDER_CELL_RE = re.compile(r"^:?-{2,}:?$")


def _first_table_span(text: str) -> tuple[int, int, list[list[str]]] | None:
    """Line span and parsed rows of the first well-shaped markdown table.

    Mirrors ``core.presentation_selection._markdown_table_rows`` (the single
    detector seam) but also returns the span so the caller can replace exactly
    those lines. Returns ``None`` when no table with a header + divider exists.
    """
    lines = str(text or "").splitlines()
    rows: list[list[str]] = []
    start = end = -1
    divider_seen = False
    for index, line in enumerate(lines):
        match = _ROW_RE.match(line)
        if match is None:
            if divider_seen:
                break
            continue
        cells = [cell.strip() for cell in match.group(1).split("|")]
        if not divider_seen:
            if not rows:
                start = index
                rows.append(cells)
                continue
            if len(rows) == 1 and all(_DIVIDER_CELL_RE.match(cell or "-") for cell in cells):
                divider_seen = True
                continue
            return None
        rows.append(cells)
        end = index
    if not divider_seen or end < start:
        return None
    return start, end, rows


def _declined(content: str, reason: str) -> tuple[str, dict[str, Any]]:
    return content, {
        "schema": RENDER_LANE_SCHEMA,
        "normalized": [],
        "declined": reason,
    }


def normalize_structured_shape(
    content: str,
    source_context: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Normalize the elected structure of one candidate answer. Pure; never raises.

    Returns ``(content, record)``. When the C19 election is a normalizable
    table shape and the bytes carry a table, the table's lines are re-formed
    through the vault renderer (aligned pipes, header divider) with every cell
    copied verbatim; all other bytes are untouched. Everything else — prose
    answers, declined elections, ragged or ambiguous tables — returns the
    input byte-identical with a typed decline reason.
    """
    text = str(content or "")
    if not text.strip():
        return _declined(text, "empty")

    # Election comes from the bytes themselves via the C19 authority. Any
    # exception inside selection is a stand-down, never a turn-ending error.
    try:
        from core.presentation_selection import select_presentation

        selection = select_presentation(text, source_context)
    except Exception:
        return _declined(text, "selection_unavailable")

    if not isinstance(selection, dict):
        return _declined(text, "selection_malformed")
    if selection.get("disabled_by"):
        return _declined(text, f"disabled_by:{selection.get('disabled_by')}")
    elected = str(selection.get("elected") or "")
    if elected not in _NORMALIZABLE_SHAPES:
        return _declined(text, f"shape:{elected or 'prose'}")

    span = _first_table_span(text)
    if span is None:
        return _declined(text, "no_table_in_bytes")
    start, end, rows = span
    if len(rows) < 2:
        return _declined(text, "table_too_small")

    headers, body = rows[0], rows[1:]
    width = len(headers)
    if width == 0 or any(len(row) != width for row in body):
        # Ragged tables are declined, never padded: a structural empty cell is
        # indistinguishable from a claimed-blank value, and inventing one is
        # exactly what the never-invent law forbids.
        return _declined(text, "ragged_rows")

    rebuilt = MarkdownRenderer().render(
        RenderDocument(blocks=[Table(headers=list(headers), rows=[list(r) for r in body])])
    ).strip()

    # ROUND-TRIP GUARD: the re-rendered table must parse back to the identical
    # cell matrix. Any drift — escaping, ordering, a dropped row — declines.
    check = _first_table_span(rebuilt)
    if check is None or [cell for cell in rows[0]] != check[2][0] or [list(r) for r in body] != [r for r in check[2][1:]]:
        return _declined(text, "round_trip_diverged")

    lines = text.splitlines()
    # Bytes outside the replaced span are copied exactly, including the text's
    # own trailing newline — normalization must not nibble the neighbourhood.
    trailing = "\n" if text.endswith("\n") else ""
    rendered = "\n".join([*lines[:start], rebuilt, *lines[end + 1:]])
    if trailing and not rendered.endswith("\n"):
        rendered += trailing
    record = {
        "schema": RENDER_LANE_SCHEMA,
        "normalized": ["table"],
        "elected": elected,
        "trigger": str(selection.get("trigger") or ""),
        "rows": len(body),
        "cells_before": sum(len(row) for row in rows),
        "cells_after": width * (len(body) + 1),
    }
    return rendered, record


__all__ = ["RENDER_LANE_SCHEMA", "normalize_structured_shape"]
