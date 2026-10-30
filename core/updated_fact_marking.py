"""Updated-fact marking for the memory capsule (v08x, 2026-10-30).

Measured on the DEV-30 probe (missions/v08x-updated-facts-20261030): the capsule
already delivers the newest value of a changed fact beside the older ones
(16/20 newest present, 15/20 older values dated beside it) and nothing tells the
reader which one is current (0/20 marked, 0/20 superseded). The judged misses on
knowledge_update share that cause ("stale value voiced": the newer value IS
retrieved, the reader states the older one or both).

This module marks the delivered record lines of one assembled capsule render:

- newest of a same-subject value chain      -> ``[current value]``
- older members of that chain               -> ``[superseded]``
- same-moment conflicting values (no clear
  later record)                             -> ``[conflicting records]``

Markers are line SUFFIXES (``- user said (stated 2024-05-05): ... [current
value]``): every containment check that compares a delivered line against the
render keeps holding, the record's own date label stays its date, and no record
is dropped — an older value stays visible, marked as history (the forget laws
are untouched: this pass only ever annotates lines other lanes already
delivered).

Chain rules (deliberately conservative; a wrong mark is worse than none):

- only lines the render already delivers as records ("- user said ...",
  "- assistant said ...") participate;
- a pair chains when their subject anchors overlap AND their numeric values
  conflict (both sides carry numbers, the sets disjoint — a salary, a count, a
  target; identical values never mark: an assistant echo of the user's update
  is agreement, not a conflict), or when the newer side is the user's own
  statement carrying an explicit switch cue ("switched to", "no longer", ...)
  over a same-subject older record (a plan or preference change);
- cross-role pairs chain only when the NEWER side is the user's own statement:
  the user's later word supersedes an earlier assistant echo, never the
  reverse (assistant advice is not a fact update);
- order comes from occurrence truth (statement time, then capture sequence)
  where the caller can supply it, else from the line's own date label; a pair
  whose order cannot be resolved does not mark current/superseded — a numeric
  conflict at the same unorderable moment marks BOTH sides
  ``[conflicting records]`` instead.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping, Sequence

#: marker suffixes, appended to the delivered line (probe + tests read these)
CURRENT_MARKER = "[current value]"
SUPERSEDED_MARKER = "[superseded]"
CONFLICT_MARKER = "[conflicting records]"

_RECORD_LINE_RE = re.compile(r"^- (?:user|assistant) said\b")
_DATE_PARENS_RE = re.compile(r"\((?:stated|recorded)[^)]*\)")
_TURN_TAIL_RE = re.compile(r"\s*->->\s*\d+,\d+\s*$")
_DATE_LABEL_RE = re.compile(
    r"stated:?\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})|recorded:?\s*(\d{4})[-/](\d{1,2})[-/](\d{1,2})"
)
#: explicit user-side switch/update cues (categorical value changes)
_SWITCH_CUE_RE = re.compile(
    r"\b(?:switch(?:ed|ing)?\s+to|chang(?:ed|ing)\s+(?:my|the|to)|moved\s+to|"
    r"no\s+longer|instead\s+of|updated\s+(?:my|the)|revised\s+(?:my|the|to)|"
    r"raised\s+(?:my|the|it\s+to)?|increased\s+(?:my|the|it\s+to)?|"
    r"bumped\s+(?:my|the|it\s+to)?|cut\s+(?:my|the|it\s+to)?|"
    r"actually[, ]|scratch\s+that|new\s+(?:plan|schedule|target|goal|number))\b",
    re.IGNORECASE,
)
#: how many lines one render may mark (marker tokens come out of the capsule
#: budget after the packer ran; a chain-heavy render must not overflow it)
_MAX_MARKED_LINES = 12


def _anchors(text: str) -> frozenset[str]:
    try:
        from core.temporal_selection import anchor_terms

        return frozenset(anchor_terms(text))
    except Exception:
        return frozenset()


def _values(text: str) -> frozenset[str]:
    try:
        from core.temporal_selection import value_tokens

        return frozenset(value_tokens(text))
    except Exception:
        return frozenset()


def _line_day(text: str) -> tuple[int, int, int] | None:
    m = _DATE_LABEL_RE.search(text)
    if not m:
        return None
    year, month, day = (m.group(i) for i in (1, 2, 3)) if m.group(1) else (
        m.group(i) for i in (4, 5, 6))
    try:
        return (int(year), int(month), int(day))
    except (TypeError, ValueError):
        return None


def _order_key(entry: dict[str, Any]) -> tuple[float, float] | None:
    """(statement moment, capture sequence) for ordering a chain pair.

    The statement time is the record's own truth; the capture sequence breaks
    same-moment ties (a correction inside one sitting). Missing pieces fall
    back to the line's date label; a record with no time truth at all does not
    order (its pairs degrade to the conflict marker or stay unmarked)."""
    stated = entry.get("statement_at")
    seq = entry.get("source_sequence")
    try:
        stated_f = float(stated) if stated is not None else None
    except (TypeError, ValueError):
        stated_f = None
    try:
        seq_f = float(seq) if seq is not None else None
    except (TypeError, ValueError):
        seq_f = None
    if stated_f is None:
        return None
    return (stated_f, seq_f if seq_f is not None else 0.0)


def _same_moment(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Same calendar day AND no capture-sequence separation (day grain: the
    store's statement clock is the session moment; two records of one sitting
    share it)."""
    da = datetime.fromtimestamp(a[0], tz=timezone.utc).date()
    db = datetime.fromtimestamp(b[0], tz=timezone.utc).date()
    return da == db and a[1] == b[1]


def _collect_entries(
    receipts: Iterable[Mapping[str, Any]],
    lane_records: Iterable[tuple[Any, str]],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []

    def _add(line: str, *, role: str, statement_at: Any, source_sequence: Any) -> None:
        text = str(line or "").rstrip()
        if not _RECORD_LINE_RE.match(text):
            return
        # anchors and values read the RECORD BODY only: the "- user said" role
        # prefix and the "(stated 2024-04-01)" date labels are lane furniture,
        # and their date digits would make every pair look value-disjoint
        body = _RECORD_LINE_RE.sub("", text, count=1)
        body = _TURN_TAIL_RE.sub("", _DATE_PARENS_RE.sub(" ", body))
        entries.append({
            "line": text,
            "role": "assistant" if str(role) == "assistant" else "user",
            "statement_at": statement_at,
            "source_sequence": source_sequence,
            "anchors": _anchors(body),
            "values": _values(body),
        })

    for receipt in receipts or []:
        line = receipt.get("line") if receipt.get("delivered") else None
        if not line:
            continue
        _add(str(line),
             role=str(receipt.get("role") or "user"),
             statement_at=receipt.get("statement_at"),
             source_sequence=receipt.get("source_sequence"))
    for occurrence, line in lane_records or []:
        if not line:
            continue
        _add(str(line),
             role=str(getattr(occurrence, "role", "user") or "user"),
             statement_at=getattr(occurrence, "statement_at", None),
             source_sequence=getattr(occurrence, "source_sequence", None))
    # one entry per distinct line (a line delivered by two lanes marks once)
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for entry in entries:
        if entry["line"] in seen:
            continue
        seen.add(entry["line"])
        unique.append(entry)
    return unique


def _pair_kind(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Why this pair chains: "numeric", "cue", or "none".

    Subject anchors must overlap; the value evidence is either two disjoint
    number sets (a salary, a count, a target) or — weaker, so it demands more
    anchors — an explicit switch cue on the USER side of the pair."""
    if a["line"] == b["line"]:
        return "none"
    shared = a["anchors"] & b["anchors"]
    if not shared:
        return "none"
    if a["values"] and b["values"] and not (a["values"] & b["values"]):
        return "numeric"
    if len(shared) >= 2 and (
            _SWITCH_CUE_RE.search(a["line"]) or _SWITCH_CUE_RE.search(b["line"])):
        return "cue"
    return "none"


def compute_markers(
    receipts: Sequence[Mapping[str, Any]],
    lane_records: Sequence[tuple[Any, str]],
) -> dict[str, str]:
    """Line text -> marker suffix for one assembled capsule render."""
    entries = _collect_entries(receipts, lane_records)
    markers: dict[str, str] = {}

    def _set(line: str, marker: str) -> None:
        # conflict outranks current/superseded: a line that is newest against
        # one record and unorderable against another says both
        if markers.get(line) == CONFLICT_MARKER:
            return
        markers[line] = marker

    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            kind = _pair_kind(a, b)
            if kind == "none":
                continue
            ka, kb = _order_key(a), _order_key(b)
            if ka is None or kb is None:
                # no time truth on one side: line-date labels decide; equal or
                # missing days cannot order, and a numeric conflict with no
                # order is exactly the "neither is clearly later" case
                da, db = _line_day(a["line"]), _line_day(b["line"])
                if da is not None and db is not None and da != db:
                    older, newer = (a, b) if da < db else (b, a)
                elif (kind == "numeric"
                      and ("user" in (a["role"], b["role"]))):
                    # the direction law's floor: a conflict mark too needs a
                    # user side — two same-day assistant lines with different
                    # numbers are different meetings, not a contradiction
                    _set(a["line"], CONFLICT_MARKER)
                    _set(b["line"], CONFLICT_MARKER)
                    continue
                else:
                    continue
            else:
                older, newer = (a, b) if ka < kb else (b, a)
                ko, kn = (ka, kb) if older is a else (kb, ka)
                if _same_moment(ko, kn):
                    # one sitting, no capture order: say both — with a user
                    # side (same floor as above; assistant-vs-assistant
                    # schedule lines never mark)
                    if "user" in (a["role"], b["role"]):
                        _set(a["line"], CONFLICT_MARKER)
                        _set(b["line"], CONFLICT_MARKER)
                    continue
            # the direction law, on the ORDERED pair: only the user's own
            # later statement carries a chain (an assistant line never
            # supersedes the user's earlier word, and assistant-vs-assistant
            # advice churn stays unmarked); a "cue" chain additionally needs
            # the cue on the newer, user side
            if newer["role"] != "user":
                continue
            if kind == "cue" and not _SWITCH_CUE_RE.search(newer["line"]):
                continue
            _set(older["line"], SUPERSEDED_MARKER)
            _set(newer["line"], CURRENT_MARKER)
    if len(markers) > _MAX_MARKED_LINES:
        # keep conflicts first (they change what the reader should say at all),
        # then current, then superseded
        order = {CONFLICT_MARKER: 0, CURRENT_MARKER: 1, SUPERSEDED_MARKER: 2}
        ranked = sorted(markers.items(), key=lambda kv: (order[kv[1]], kv[0]))
        markers = dict(ranked[:_MAX_MARKED_LINES])
    return markers


def mark_updated_fact_lines(
    render_block: str,
    *,
    receipts: Sequence[Mapping[str, Any]] | None = None,
    lane_records: Sequence[tuple[Any, str]] | None = None,
) -> str:
    """The render block with updated-fact markers suffixed to its record lines.

    Only lines inside the block itself are rewritten; every other byte of the
    render (headers, packet rows, notes) passes through untouched. A line the
    render repeats keeps its marker on each copy (same string, same status)."""
    text = str(render_block or "")
    if not text.strip():
        return text
    markers = compute_markers(receipts or [], lane_records or [])
    if not markers:
        return text
    out_lines: list[str] = []
    for line in text.split("\n"):
        marker = markers.get(line.rstrip())
        if marker and marker not in line:
            line = f"{line.rstrip()} {marker}"
        out_lines.append(line)
    return "\n".join(out_lines)


__all__ = [
    "CURRENT_MARKER",
    "SUPERSEDED_MARKER",
    "CONFLICT_MARKER",
    "compute_markers",
    "mark_updated_fact_lines",
]
