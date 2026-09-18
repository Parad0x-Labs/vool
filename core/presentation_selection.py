"""C19 automatic answer-presentation selection: one deterministic authority.

The explicit half of C19 (a user asking "as a table") is owned by
``core.response_constraints`` and stays untouched. This module is the missing
other half: when NO explicit request exists, decide — from the candidate answer
bytes and the typed turn context alone — whether the answer's own shape
justifies a presentation, and record that decision as provenance.

The laws this module implements (specification
``FREE_FLASH_C19_PRESENTATION_AUTHORITY_SPEC_20260904``):

- **Explicit wins (L1).** Any parsed response contract on the turn stands the
  selection down: ``disabled_by="explicit_request"``.
- **Prompt-blindness (L2/P5).** No function here ever reads user text. The
  triggers are pure predicates over the candidate answer bytes and the typed
  ``source_context`` payloads. A topic noun ("org chart", "tree of life",
  "graph database") cannot elect anything because the prompt is never an
  input.
- **Prose default (L3/P7).** Absent a justified shape the election is
  ``prose`` with ``trigger="none_justified"``.
- **Never invent (L4).** This module never mutates bytes and never fabricates
  a value to satisfy a shape. The only bytes that may change do so in the
  router's single bounded repair, and only after
  :func:`selection_repair_acceptance` holds.
- **Renderer honesty (L5/L6/L10).** ``mermaid``, ``chart`` and ``pie_chart``
  are ``AUTO_NEVER``: never elected, regardless of any capability flag, until
  a renderer lane exists and changes the law itself.
- **Presentation ≠ power (L12).** The selection record is provenance only. It
  travels in ``display_metadata`` beside the receipts, never in answer bytes,
  and grants no tools, permissions or authority.

Zero model calls. One O(n) structural scan. Deterministic: identical inputs
yield an identical record.
"""

from __future__ import annotations

import json
import re
from typing import Any

from core.incomplete_answer import inspect_answer_completeness
from core.response_constraints import (
    CONSTRAINT_ORIGINS,
    SURFACE_RENDERS_PRESENTATION,
    _MARKDOWN_TABLE_RE,
    _presentation_answer_has_format,
    response_constraint_from_metadata,
)
from core.turn_ir import ResponseConstraint

#: The closed automatic vocabulary. Superset of nothing: ``prose`` and the
#: auto-derived shapes exist only here; the explicit vocabulary in
#: ``core.response_constraints.PRESENTATION_FORMATS`` is unchanged.
AUTO_PRESENTATION_FORMATS: tuple[str, ...] = (
    "prose",
    "bullets",
    "numbered_steps",
    "table",
    "comparison_matrix",
    "timeline",
    "tree",
    "mermaid",
    "chart",
    "pie_chart",
)

#: Explicit-request-only formats. These are NEVER auto-elected — not while
#: their render capability is False, and not after some renderer lane flips
#: it. Only an explicit user request (or the law changing) puts them in bytes.
AUTO_NEVER: tuple[str, ...] = ("mermaid", "chart", "pie_chart")

#: Fixed election priority, first match wins; no ties are possible because
#: the predicates below are evaluated in this order.
ELECTION_PRIORITY: tuple[str, ...] = (
    "comparison_matrix",
    "table",
    "timeline",
    "tree",
    "numbered_steps",
    "bullets",
)

#: The closed trigger vocabulary. ``numeric_series_ge2`` and
#: ``part_whole_complete`` justify nothing electable on this surface: numbers
#: alone never make a chart (C1), and a pie needs a renderer lane that does
#: not exist (L5). They are computed so the record can say why nothing fired.
SHAPE_TRIGGERS: tuple[str, ...] = (
    "parallel_items_ge3",
    "ordered_steps_ge2",
    "subjects_ge2_shared_attributes_ge2",
    "dated_events_ge2",
    "containment_depth_ge2",
    "tabular_rows_ge2",
    "numeric_series_ge2",
    "part_whole_complete",
)

#: Which presentation a fired trigger justifies. ``None`` = the trigger is
#: evidence-only on this surface and never elects.
TRIGGER_JUSTIFIES: dict[str, str | None] = {
    "parallel_items_ge3": "bullets",
    "ordered_steps_ge2": "numbered_steps",
    "subjects_ge2_shared_attributes_ge2": "comparison_matrix",
    "dated_events_ge2": "timeline",
    "containment_depth_ge2": "tree",
    "tabular_rows_ge2": "table",
    "numeric_series_ge2": None,
    "part_whole_complete": None,
}

#: The closed ``disabled_by`` vocabulary. ``elected is None`` if and only if
#: ``disabled_by`` is set.
DISABLE_REASONS: tuple[str, ...] = (
    "explicit_request",
    "exact_contract",
    "refusal_signal",
    "grounding_gate",
    "x_editorial",
    "ordinary_chat_guard",
)

#: Schema tag of the selection record. Sibling of ``vool.turn_proof.v1`` in
#: the commit's ``display_metadata``; never part of answer bytes.
SELECTION_SCHEMA = "vool.presentation_selection.v1"

#: Self-imposed serialized bound on the record (nothing downstream enforces
#: it; the tests do).
MAX_RECORD_JSON_CHARS = 512

#: An automatic election may only flow a repair through the existing explicit
#: contract machinery when the elected shape has an explicit counterpart the
#: checker understands. A comparison matrix IS a table to that machinery;
#: bullets/numbered_steps are ratify-only because marker lines already ARE
#: the elected shape (an election against prose cannot produce them).
EXPLICIT_COUNTERPART: dict[str, str] = {
    "comparison_matrix": "table",
    "table": "table",
    "timeline": "timeline",
    "tree": "tree",
}

# ---------------------------------------------------------------------------
# Kind-aware list detectors. The existing ``core.incomplete_answer`` marker
# regex is deliberately kind-blind; election needs the kinds told apart.
# ---------------------------------------------------------------------------

_ORDERED_MARKER_LINE_RE = re.compile(r"^\s*\(?\d{1,3}[.)]\s+\S")
_UNORDERED_MARKER_LINE_RE = re.compile(r"^\s*[-*•‣▪▫◦]\s+\S")

# A prose "subject segment": "Plan A: deductible €10, coverage basic". Bounded
# subject length so a sentence that merely contains a colon is not a subject.
_SUBJECT_SEGMENT_RE = re.compile(r"^([^:：\n]{1,40}?)\s*[:：]\s+(.+)$")

# A containment edge in prose arrow chains: "CEO → CTO", "CTO -> Platform".
_ARROW_EDGE_RE = re.compile(r"([A-Za-z0-9_ &.'-]{1,40}?)\s*(?:→|->)\s*([A-Za-z0-9_ &.'-]{1,40})")

# Date tokens broad enough for prose, in the same families the explicit
# timeline anchor reads. Orderability is decided per token below.
_PROSE_DATE_TOKEN_RE = re.compile(
    r"\b\d{1,4}[-/.]\d{1,2}[-/.]\d{2,4}\b"
    r"|\b(?:19|20)\d{2}\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{1,2}(?:,?\s+(?:19|20)\d{2})?\b"
    r"|\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

_UNIT_TOKEN_RE = re.compile(
    r"(?:(?P<prefix>[€$£])\s*)?"
    r"(?P<number>(?:\d+\.\d+|\d{1,3}(?:,\d{3})+|\d+))"
    r"(?P<attached>%|€|\$|£|°\s?[CF]|[A-Za-z]{1,8}(?:\.[A-Za-z]{1,2})?)?"
    r"(?:\s+(?P<spaced>%|°\s?[CF]|[A-Za-z]{1,12}(?:\.[A-Za-z]{1,2})?))?"
)

#: Spaced unit words a quantity may carry ("5 kg", "3 miles"). A closed set,
#: anchored on the kernel's unit-identity vocabulary
#: (``core.kernel.evidence_types._UNIT_GROUPS``) plus everyday advisory units:
#: an ordinary word after a number ("1420 and") is prose, not a unit.
_SPACED_UNIT_WORDS: frozenset[str] = frozenset(
    {
        "mah", "milliamp-hour", "milliamp-hours", "kwh", "kilowatt-hour",
        "kilowatt-hours", "kw", "kilowatt", "kilowatts", "w", "watt", "watts",
        "wh", "watt-hour", "watt-hours", "hz", "ghz", "gb", "gigabyte",
        "gigabytes", "tb", "terabyte", "terabytes", "kg", "kilogram",
        "kilograms", "g", "gram", "grams", "miles", "mile", "mi", "km",
        "kilometer", "kilometers", "kilometre", "kilometres", "nits", "nit",
        "l", "liter", "liters", "litre", "litres", "inch", "inches",
        "eur", "usd", "gbp", "dollars", "euros", "pounds", "hours", "hour",
        "minutes", "days", "years", "months", "weeks", "users", "seats",
        "requests", "tokens", "items", "words", "percent",
    }
)

# Cells a repaired table may use for a value it does not know (L7; the same
# convention the live-quote renderer ships).
UNKNOWN_CELLS = frozenset({"unknown", "-"})


# ---------------------------------------------------------------------------
# Deterministic shape helpers over answer bytes.
# ---------------------------------------------------------------------------


def _strip_fenced_blocks(text: str) -> str:
    return re.sub(r"```[^\n]*\n.*?\n\s*```", "", str(text or ""), flags=re.DOTALL)


def _prose_lines(text: str) -> list[str]:
    """Non-structural lines: outside fences, not table rows, not list markers."""
    cleaned = _strip_fenced_blocks(text)
    lines: list[str] = []
    for line in cleaned.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("|"):
            continue
        if _ORDERED_MARKER_LINE_RE.match(stripped) or _UNORDERED_MARKER_LINE_RE.match(stripped):
            continue
        lines.append(stripped)
    return lines


def _markdown_table_rows(text: str) -> list[list[str]]:
    """Parse the first markdown table into rows of cells, or return [].

    Line-based on purpose: the shared ``_MARKDOWN_TABLE_RE`` decides whether
    table-shaped bytes exist (single detector seam) but its match span is not
    a row parser.
    """
    rows: list[list[str]] = []
    divider_seen = False
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if not (stripped.startswith("|") and stripped.endswith("|") and len(stripped) >= 2):
            if divider_seen:
                break
            continue
        cells = [cell.strip() for cell in stripped[1:-1].split("|")]
        if not divider_seen:
            if len(rows) == 0:
                rows.append(cells)
                continue
            if len(rows) == 1 and all(
                re.fullmatch(r":?-{2,}:?", cell or "-") for cell in cells
            ):
                divider_seen = True
                continue
            break
        rows.append(cells)
    if not divider_seen:
        return []
    return rows


def count_ordered_marker_lines(text: str) -> int:
    return sum(1 for line in str(text or "").splitlines() if _ORDERED_MARKER_LINE_RE.match(line))


def count_unordered_marker_lines(text: str) -> int:
    return sum(1 for line in str(text or "").splitlines() if _UNORDERED_MARKER_LINE_RE.match(line))


def _answer_has_elected_shape(text: str, elected: str) -> bool:
    """Whether delivered bytes carry an automatic-vocabulary shape.

    Reuses the explicit authority's detectors wherever the shapes coincide
    (single detector seam); the automatic-only shapes get the kind-aware
    list/matrix predicates defined here.
    """
    cleaned = str(text or "")
    if elected == "comparison_matrix":
        return _is_matrix_table(cleaned)
    if elected == "bullets":
        return count_unordered_marker_lines(cleaned) >= 3
    if elected == "numbered_steps":
        return count_ordered_marker_lines(cleaned) >= 2
    if elected in ("table", "timeline", "tree"):
        return _presentation_answer_has_format(cleaned, elected)
    if elected == "prose":
        return True
    return False


def _is_matrix_table(text: str) -> bool:
    """A table where ≥2 row subjects are each described along ≥2 shared
    attribute columns: ≥2 data rows × ≥2 data columns and no subject's
    attribute cell abandoned (fully described rows)."""
    rows = _markdown_table_rows(text)
    if len(rows) < 3:  # header + ≥2 data rows
        return False
    header, data = rows[0], rows[1:]
    if len(header) < 3:  # subject column + ≥2 attribute columns
        return False
    populated = 0
    for row in data:
        if len(row) != len(header):
            return False
        if any(cell == "" for cell in row):
            return False
        populated += 1
    return populated >= 2


# ---------------------------------------------------------------------------
# The closed trigger predicates. Pure functions of the candidate bytes (and,
# for the evidence-only triggers, of typed evidence payloads on the context).
# ---------------------------------------------------------------------------


def _trigger_fired(text: str, trigger: str) -> bool:
    cleaned = str(text or "")
    prose = _prose_lines(cleaned)
    if trigger == "parallel_items_ge3":
        return count_unordered_marker_lines(cleaned) >= 3
    if trigger == "ordered_steps_ge2":
        return count_ordered_marker_lines(cleaned) >= 2
    if trigger == "subjects_ge2_shared_attributes_ge2":
        return _subjects_shared_attributes(prose)
    if trigger == "dated_events_ge2":
        return _dated_events_orderable(prose)
    if trigger == "containment_depth_ge2":
        if _presentation_answer_has_format(cleaned, "tree"):
            return True
        return _arrow_chain_depth_ge2(prose)
    if trigger == "tabular_rows_ge2":
        if _markdown_table_rows(cleaned):
            return True
        return _label_value_rows(prose)
    if trigger == "numeric_series_ge2":
        # Evidence-only: ≥2 grounded numeric tokens. Justifies nothing that
        # elects (C1: two numbers in prose stay prose).
        return len(re.findall(r"\d", cleaned)) >= 2
    if trigger == "part_whole_complete":
        return _part_whole_complete(cleaned)
    return False


def _informative_tokens(segment: str) -> set[str]:
    stopwords = {
        "the", "and", "for", "with", "that", "this", "from", "into", "onto",
        "has", "have", "had", "are", "was", "were", "per", "not", "but",
        "all", "any", "can", "will", "would", "each", "their", "its",
    }
    return {
        token
        for token in re.findall(r"[^\W\d_]{3,}", segment, re.UNICODE)
        if token.casefold() not in stopwords
    }


def _subjects_shared_attributes(prose_lines: list[str]) -> bool:
    """≥2 ``Subject: description`` segments sharing ≥2 informative tokens —
    the prose form of "each described along shared attributes". Values are
    grounded by construction: the segments ARE the answer's own bytes."""
    segments: list[tuple[str, str]] = []
    for line in prose_lines:
        match = _SUBJECT_SEGMENT_RE.match(line)
        if match is not None:
            segments.append((match.group(1), match.group(2)))
    if len(segments) < 2:
        return False
    token_sets = [_informative_tokens(description) for _, description in segments]
    for index, first in enumerate(token_sets[:-1]):
        for second in token_sets[index + 1 :]:
            if len(first & second) >= 2:
                return True
    return False


def _label_value_rows(prose_lines: list[str]) -> bool:
    """≥2 prose lines of the shape ``Label: value`` — grounded rows of a
    table the answer never drew."""
    rows = 0
    for line in prose_lines:
        if _SUBJECT_SEGMENT_RE.match(line) is not None:
            rows += 1
    return rows >= 2


def _orderable_date_keys(line: str) -> set[tuple[int, ...]]:
    """Sortable keys for the date tokens on one line, without invention.

    A token is orderable only when it carries its year (a bare ``2019`` or a
    full date/month-year); ``May 3`` with no year is NOT orderable and never
    guessed.
    """
    keys: set[tuple[int, ...]] = set()
    for match in _PROSE_DATE_TOKEN_RE.finditer(line):
        token = match.group(0)
        year = re.search(r"(?:19|20)\d{2}", token)
        numeric = re.fullmatch(r"\d{1,4}[-/.]\d{1,2}[-/.](\d{2,4})", token)
        month = re.match(r"[A-Za-z]+", token)
        if numeric:
            parts = re.split(r"[-/.]", token)
            if len(parts[2]) == 4:
                keys.add((int(parts[2]), int(parts[1]), int(parts[0])))
            elif parts[2].startswith(("19", "20")):
                keys.add((int(parts[2]), int(parts[1]), int(parts[0])))
        elif year and month and month.group(0).casefold()[:3] in _MONTHS:
            keys.add((int(year.group(0)), _MONTHS[month.group(0).casefold()[:3]], 0))
        elif year and not month:
            keys.add((int(year.group(0)), 0, 0))
    return keys


def _dated_events_orderable(prose_lines: list[str]) -> bool:
    """≥2 sentences each carrying a grounded date token, with ≥2 distinct
    orderable keys among them (so a timeline could be built by sorting, not
    by inventing an order). Sentences, not lines: prose names dates inside
    running text."""
    sentences: list[str] = []
    for line in prose_lines:
        sentences.extend(unit for unit in re.split(r"(?<=[.!?])\s+", line) if unit.strip())
    all_keys: set[tuple[int, ...]] = set()
    dated_units = 0
    for unit in sentences:
        keys = _orderable_date_keys(unit)
        if keys:
            dated_units += 1
            all_keys |= keys
    return dated_units >= 2 and len(all_keys) >= 2


def _arrow_chain_depth_ge2(prose_lines: list[str]) -> bool:
    """Genuine containment depth ≥2 in prose arrow chains: some node is both
    a child ("A → B") and a parent ("B → C"). Arrows inside one line only
    sketch siblings; the chain across edges is what makes depth."""
    edges: list[tuple[str, str]] = []
    for line in prose_lines:
        for match in _ARROW_EDGE_RE.finditer(line):
            parent = match.group(1).strip().casefold()
            child = match.group(2).strip().casefold()
            if parent and child:
                edges.append((parent, child))
    children = {child for _, child in edges}
    parents = {parent for parent, _ in edges}
    return bool(children & parents)


def _part_whole_complete(text: str) -> bool:
    """The pie gate (L5): mutually exclusive parts, a known nonzero whole,
    every value grounded, ≤12 parts. Structural floor only — and it elects
    nothing on this surface (no renderer; ``pie_chart`` is AUTO_NEVER)."""
    rows = _markdown_table_rows(text)
    if not rows or len(rows[0]) != 2 or not (2 <= len(rows) - 1 <= 12):
        return False
    total = 0.0
    for row in rows[1:]:
        if len(row) != 2:
            return False
        try:
            value = float(row[1].replace(",", ""))
        except ValueError:
            return False
        if value < 0:
            return False
        total += value
    return total > 0


def fired_triggers(text: str) -> tuple[str, ...]:
    """Every trigger whose deterministic predicate holds for these bytes."""
    return tuple(trigger for trigger in SHAPE_TRIGGERS if _trigger_fired(text, trigger))


# ---------------------------------------------------------------------------
# Election.
# ---------------------------------------------------------------------------


def _capability_admits(format_name: str) -> bool:
    """Capability truth: never elect into ``AUTO_NEVER``, never elect a
    format the surface cannot render."""
    if format_name in AUTO_NEVER:
        return False
    return bool(SURFACE_RENDERS_PRESENTATION.get(format_name))


def _ratify_shape(text: str) -> tuple[str, str] | None:
    """S1: the bytes ALREADY carry a structure a trigger justifies. Reuses the
    explicit authority's detectors for table/timeline/tree (single detector
    seam) and the kind-aware list/matrix predicates for the rest. Order is
    ELECTION_PRIORITY; first observed shape wins."""
    cleaned = str(text or "")
    observed: list[tuple[str, str]] = [
        entry
        for entry in (
            ("comparison_matrix", "subjects_ge2_shared_attributes_ge2")
            if _is_matrix_table(cleaned)
            else None,
            ("table", "tabular_rows_ge2") if _markdown_table_rows(cleaned) else None,
            ("timeline", "dated_events_ge2")
            if _presentation_answer_has_format(cleaned, "timeline")
            else None,
            ("tree", "containment_depth_ge2")
            if _presentation_answer_has_format(cleaned, "tree")
            else None,
            ("numbered_steps", "ordered_steps_ge2")
            if count_ordered_marker_lines(cleaned) >= 2
            else None,
            ("bullets", "parallel_items_ge3")
            if count_unordered_marker_lines(cleaned) >= 3
            else None,
        )
        if entry is not None
    ]
    for format_name, trigger in observed:
        if _capability_admits(format_name):
            return format_name, trigger
    return None


def _elect_from_prose(text: str) -> tuple[str, str] | None:
    """S2: a trigger fires on the prose answer's own data shape. These are
    the prose-form predicates — the answer carries no table/list/tree it
    could be ratified against, which is exactly what makes it an election
    (``gap_detected=True``) rather than a ratification."""
    prose = _prose_lines(str(text or ""))
    candidates: tuple[tuple[str, str, bool], ...] = (
        ("comparison_matrix", "subjects_ge2_shared_attributes_ge2",
         _subjects_shared_attributes(prose)),
        ("table", "tabular_rows_ge2", _label_value_rows(prose)),
        ("timeline", "dated_events_ge2", _dated_events_orderable(prose)),
        ("tree", "containment_depth_ge2", _arrow_chain_depth_ge2(prose)),
    )
    for format_name, trigger, fired in candidates:
        if fired and _capability_admits(format_name):
            return format_name, trigger
    return None


# ---------------------------------------------------------------------------
# The record.
# ---------------------------------------------------------------------------


def _record(
    turn_id: str,
    *,
    elected: str | None = None,
    trigger: str | None = None,
    disabled_by: str | None = None,
    gap_detected: bool = False,
    fallback: str | None = None,
) -> dict[str, Any]:
    if disabled_by is not None:
        if disabled_by not in DISABLE_REASONS:
            raise ValueError(f"closed vocabulary: unknown disabled_by {disabled_by!r}")
        elected = None
        trigger = None
        gap_detected = False
        fallback = None
    elif elected is None or elected not in AUTO_PRESENTATION_FORMATS:
        raise ValueError(f"closed vocabulary: unknown elected {elected!r}")
    if trigger is not None and trigger != "none_justified" and trigger not in SHAPE_TRIGGERS:
        raise ValueError(f"closed vocabulary: unknown trigger {trigger!r}")
    return {
        "turn_id": str(turn_id or ""),
        "elected": elected,
        "trigger": trigger,
        "disabled_by": disabled_by,
        "gap_detected": bool(gap_detected),
        "fallback": fallback,
        "origin": "automatic",
        "schema": SELECTION_SCHEMA,
    }


def selection_record_json(record: dict[str, Any]) -> str:
    """The record's serialized form; bounded, JSON-safe, transport-ready."""
    payload = json.dumps(record, sort_keys=True, separators=(",", ":"))
    if len(payload) > MAX_RECORD_JSON_CHARS:
        raise ValueError("presentation_selection record exceeds its 512-char bound")
    return payload


def refusal_signal(text: str, source_context: dict[str, Any] | None) -> bool:
    """Pre-finalize S0-a: the turn is already a non-answer, so a refusal is
    never reshaped. Reads only the typed runtime notice and the structural
    completeness inspection — never the prompt."""
    context = dict(source_context or {})
    if bool(context.get("runtime_notice_not_an_answer")):
        return True
    adjudication = context.get("ambiguity_adjudication")
    if isinstance(adjudication, dict) and adjudication.get("state") == "unresolved":
        return True
    completeness = inspect_answer_completeness(str(text or ""))
    return bool(completeness.degenerate)


# ---------------------------------------------------------------------------
# The one pure decision function.
# ---------------------------------------------------------------------------


def select_presentation(
    candidate_bytes: str,
    source_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Decide the automatic presentation for one candidate answer.

    Pure: the same bytes and context always yield the same record. Zero model
    calls, no regex over any prompt text, no byte mutation. First matching
    stage wins:

    - **S0-a** a pre-finalize refusal signal → stand down (``refusal_signal``).
    - **S0-b** an explicit response contract on the turn → stand down
      (``explicit_request``): the existing authority owns the shape.
    - **S0-c** a raw/exact output contract → stand down (``exact_contract``).
    - **S0-d** X-editorial or ordinary-chat-guard neutralization → stand down.
    - **S1** the bytes already carry a justified shape → ratify it
      (``gap_detected=False``).
    - **S2** a trigger fires on a prose answer → elect the shape with
      ``gap_detected=True`` (the router may run the one bounded repair).
    - **S3** nothing fired → prose (``trigger="none_justified"``).
    """
    cleaned = str(candidate_bytes or "")
    context = dict(source_context or {})
    turn_id = str(
        context.get("_canonical_user_turn_id") or context.get("cancel_turn_id") or ""
    )

    if refusal_signal(cleaned, context):
        return _record(turn_id, disabled_by="refusal_signal")

    constraint = response_constraint_from_metadata(context)
    if constraint is not None:
        # L1: an explicit contract (presentation, layout, or a word budget the
        # explicit parser bound) owns this turn's shape and its single repair
        # path. Selection stands down; behavior is byte-identical to base.
        return _record(turn_id, disabled_by="explicit_request")

    if context.get("raw_output_contract"):
        return _record(turn_id, disabled_by="exact_contract")

    if context.get("x_editorial_turn"):
        return _record(turn_id, disabled_by="x_editorial")

    ordinary_policy = context.get("ordinary_chat_output_policy") or {}
    if (
        isinstance(ordinary_policy, dict)
        and str(ordinary_policy.get("mode") or "") == "ordinary_chat"
        and str(ordinary_policy.get("max_words") or "").strip()
    ):
        # C4: the 64-word ordinary-chat lane must stay prose-shaped; a shaped
        # answer would be hard-trimmed downstream. The disable keys on the
        # ceiling actually applying (policy max_words set): a turn whose
        # detail request lifted the ceiling is not trimmable, so election is
        # honest there (falsifier verdict 5 — do not strand the showcase).
        return _record(turn_id, disabled_by="ordinary_chat_guard")

    ratified = _ratify_shape(cleaned)
    if ratified is not None:
        elected, trigger = ratified
        return _record(turn_id, elected=elected, trigger=trigger, gap_detected=False)

    elected_only = _elect_from_prose(cleaned)
    if elected_only is not None:
        elected, trigger = elected_only
        return _record(turn_id, elected=elected, trigger=trigger, gap_detected=True)

    return _record(turn_id, elected="prose", trigger="none_justified")


# ---------------------------------------------------------------------------
# Repair acceptance (slice 2). ALL checks must hold or the original bytes ship.
# ---------------------------------------------------------------------------


def _citation_family_counts(text: str) -> dict[str, int]:
    """Multiset of citation markers by prefix family, using the exact real
    spellings the kernel emits. ``[unverified - model memory]`` is counted
    before the looser ``[unverified`` family so the two never double-count."""
    cleaned = str(text or "")
    unverified_full = cleaned.count("[unverified - model memory]")
    return {
        "[receipt:": cleaned.count("[receipt:"),
        "[memory:": cleaned.count("[memory:"),
        "[stipulated]": cleaned.count("[stipulated]"),
        "[unverified - model memory]": unverified_full,
        "[unverified": cleaned.count("[unverified") - unverified_full,
    }


def _receipt_strings(text: str) -> list[str]:
    return re.findall(r"\[receipt:([^\]]*)\]", str(text or ""))


def _number_unit_pairs(text: str) -> set[tuple[str, str]]:
    """Normalized (quantity, unit) pairs — unit identity is part of quantity
    identity, so a repair that keeps ``10`` but drops ``€10`` fails here."""
    from core.kernel.lexical_spans import quantity_values

    cleaned = str(text or "")
    normalized = quantity_values(cleaned)
    pairs: set[tuple[str, str]] = set()
    for match in _UNIT_TOKEN_RE.finditer(cleaned):
        raw = match.group("number")
        compact = raw.replace(",", "")
        attached = (match.group("attached") or "").strip()
        spaced = (match.group("spaced") or "").strip()
        if spaced.casefold() not in _SPACED_UNIT_WORDS:
            spaced = ""
        unit = (match.group("prefix") or "") + attached + spaced
        for candidate in (raw, compact):
            if candidate in normalized:
                pairs.add((candidate, unit))
                break
    return pairs


def _number_token_values(text: str) -> set[str]:
    from core.kernel.lexical_spans import quantity_values

    return set(quantity_values(str(text or "")))


def selection_repair_acceptance(
    original: str,
    repaired: str,
    elected: str,
) -> bool:
    """Whether a selection-driven repair may replace the original bytes.

    Deterministic and strict: shape, citation multiset, number-token set
    (precision may drop, never appear), verbatim receipts with their units,
    and — for table shapes — complete columns with honest unknown cells. Any
    failure keeps the original answer; a reworded quantity or a dropped
    citation can never buy a prettier shape.
    """
    original_text = str(original or "")
    repaired_text = str(repaired or "")
    if not repaired_text.strip():
        return False
    if not _answer_has_elected_shape(repaired_text, elected):
        return False
    if elected in AUTO_NEVER or not _capability_admits(elected):
        return False
    if _citation_family_counts(repaired_text) != _citation_family_counts(original_text):
        return False
    original_receipts = _receipt_strings(original_text)
    repaired_receipts = _receipt_strings(repaired_text)
    if sorted(original_receipts) != sorted(repaired_receipts):
        return False
    original_numbers = _number_token_values(original_text)
    repaired_numbers = _number_token_values(repaired_text)
    rounding_excused: set[str] = set()
    if not repaired_numbers <= original_numbers:
        # Precision may only DROP: every value the repair added beyond the
        # original's must be an honest rounding of a number the original
        # held. A more precise value asserts digits nobody wrote.
        from core.kernel.evidence_types import _is_rounding_of_any

        for value in repaired_numbers:
            if value in original_numbers:
                continue
            if not _is_rounding_of_any(value, original_numbers):
                return False
            rounding_excused.add(value)
    original_pairs = _number_unit_pairs(original_text)
    for value, unit in _number_unit_pairs(repaired_text):
        if value in rounding_excused:
            continue  # its unit identity travels with the number it rounds
        if (value, unit) not in original_pairs:
            return False
    if elected in ("table", "comparison_matrix"):
        rows = _markdown_table_rows(repaired_text)
        if not rows:
            return False
        width = len(rows[0])
        for row in rows[1:]:
            if len(row) != width:
                return False
            for cell in row:
                # A column any row populates stays populated for every row: a
                # value the answer does not have renders as an honest unknown
                # cell, never as an empty one (L7).
                if not cell.strip():
                    return False
    return True
