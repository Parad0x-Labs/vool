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
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

#: marker suffixes, appended to the delivered line (probe + tests read these)
CURRENT_MARKER = "[current value]"
SUPERSEDED_MARKER = "[superseded]"
CONFLICT_MARKER = "[conflicting records]"

#: the header's answering rule, ONE source for header and post-marking upgrade
_RULE_SENTENCE = (
    "Answer changed facts with the newest value (marked [current value]); "
    "[superseded] marks older values (history questions only); [conflicting records] "
    "disagree with no clear later one — give both."
)


def updated_fact_rule_sentence() -> str:
    """The answering rule the header states beside the markers it names.

    Kept short on purpose: it rides the header line after packing, so every
    token is spent from the delivered block (DEV-30 measured the cost)."""
    return _RULE_SENTENCE

_RECORD_LINE_RE = re.compile(r"^- (?:\[(\d{4}-\d{2}-\d{2})\] )?(?:user|assistant) said\b")
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
    r"actually[, ]|scratch\s+that|new\s+(?:plan|schedule|target|goal|number)|"
    r"instead(?=\s*[.!,;]|\s*$)|after\s+all)\b",
    re.IGNORECASE,
)
#: a user statement that RESTORES an earlier value without repeating it ("I'm back in my old locker", "found the first
#: padlock, using that one again", "we switched back"). Said in passing in a long chat (sls, 2026-11-14: padlock 5555,
#: lost, new 9999, "found my first padlock so we use it again" -> 5555), the newest number in the chain is no longer
#: current; the one before it is.
_REVERT_CUE_RE = re.compile(
    r"\b(?:back\s+(?:in|to|on|at|with|into)\s+(?:my|the|our)\s+(?:old|original|first|previous|former|usual)\b"
    r"|(?:switch|switched|switching|go|going|went|gone|change|changed|changing|move|moved|moving)\s+back\b"
    r"|back\s+to\s+(?:the\s+)?(?:old|original|first|previous|former|usual)\b"
    r"|(?:old|original|first|previous|former)\s+(?:one|\w+)\s+(?:again|back)\b"
    r"|(?:that|the\s+(?:old|first|original|previous))\s+one\s+again\b"
    r"|found\s+(?:my|the|our)\s+(?:first|old|original|previous)\b)",
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


def _render_entries(render_text: str, known_lines: set[str]) -> list[dict[str, Any]]:
    """Record lines of the render itself, for lanes that keep no receipts.

    The distiller/base lane and the evidence packet deliver "- user said ..."
    lines whose occurrence metadata never reaches the delivery seam. Their day
    rides IN the line (a "(stated: ...)" label or the packet's "[date]" prefix),
    so they order by label only — no capture sequence — and the conflict arm
    covers what a label cannot order."""
    entries: list[dict[str, Any]] = []
    for raw in str(render_text or "").split("\n"):
        text = raw.rstrip()
        if not text or text in known_lines:
            continue
        m = _RECORD_LINE_RE.match(text)
        if not m:
            continue
        role = "assistant" if "assistant said" in text[:34] else "user"
        body = _RECORD_LINE_RE.sub("", text, count=1)
        body = _TURN_TAIL_RE.sub("", _DATE_PARENS_RE.sub(" ", body))
        entries.append({
            "line": text,
            "role": role,
            "statement_at": None,
            "source_sequence": None,
            "anchors": _anchors(body),
            "values": _values(body),
        })
    return entries


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
    if _SWITCH_CUE_RE.search(a["line"]) or _SWITCH_CUE_RE.search(b["line"]):
        # one shared subject word: a chain only inside one sitting (see compute_markers)
        return "cue_one_sitting"
    return "none"


def _seq(entry: dict[str, Any]) -> float | None:
    try:
        return float(entry["source_sequence"]) if entry.get("source_sequence") is not None else None
    except (TypeError, ValueError):
        return None


def _same_day_capture_order(
    a: dict[str, Any], b: dict[str, Any], da: tuple[int, int, int] | None, db: tuple[int, int, int] | None,
) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """(older, newer) for two records whose lines show the same day and that both carry a capture order.

    The capture order is the conversation log's own order when both records were found in it ("said_order"), else the
    store's write sequence; inside one day either is the order the person said things in. Records with no day or no
    order, or with the same one, stay unordered."""
    if da is None or db is None or da != db:
        return None
    if a.get("said_order") is not None and b.get("said_order") is not None:
        sa, sb = float(a["said_order"]), float(b["said_order"])
    else:
        sa, sb = _seq(a), _seq(b)
    if sa is None or sb is None or sa == sb:
        return None
    return (a, b) if sa < sb else (b, a)


def _newer_of(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any] | None:
    """The later of two records by the same rules the chain pass uses, or None when they cannot be ordered."""
    ka, kb = _order_key(a), _order_key(b)
    if ka is not None and kb is not None:
        if _same_moment(ka, kb):
            return None
        return b if kb > ka else a
    da, db = _line_day(a["line"]), _line_day(b["line"])
    if da is not None and db is not None and da != db:
        return b if db > da else a
    ordered = _same_day_capture_order(a, b, da, db)
    return ordered[1] if ordered else None


def _mark_restored_values(entries: Sequence[dict[str, Any]], markers: dict[str, str]) -> None:
    """A later user statement that goes back to an earlier value ("I'm back in my old locker") makes the value before
    the current one current again.

    Only for a numeric chain already marked here (a [current value] line and a [superseded] line sharing a subject
    word with it and with the restoring statement), only when the restoring statement is the user's own, carries no
    number of its own and is orderably newer than the current value. Anything less leaves the marks as they are."""
    for current in [e for e in entries if markers.get(e["line"]) == CURRENT_MARKER and e["values"]]:
        older = [e for e in entries
                 if markers.get(e["line"]) == SUPERSEDED_MARKER and e["values"] and e["role"] == "user"
                 and not (e["values"] & current["values"]) and (e["anchors"] & current["anchors"])
                 and _newer_of(e, current) is current]
        if not older:
            continue
        subject = frozenset().union(*(e["anchors"] & current["anchors"] for e in older))
        reverts = [e for e in entries
                   if e["role"] == "user" and not e["values"] and _REVERT_CUE_RE.search(e["line"])
                   and (e["anchors"] & subject) and _newer_of(e, current) is e]
        if not reverts:
            continue
        # the value it goes back to: the newest superseded value before the current one
        restored = older[0]
        for e in older[1:]:
            if _newer_of(restored, e) is e:
                restored = e
        markers[current["line"]] = SUPERSEDED_MARKER
        markers[restored["line"]] = CURRENT_MARKER
        for e in reverts:
            markers.setdefault(e["line"], CURRENT_MARKER)


def compute_markers(
    receipts: Sequence[Mapping[str, Any]],
    lane_records: Sequence[tuple[Any, str]],
    *,
    _entries: Sequence[dict[str, Any]] | None = None,
) -> dict[str, str]:
    """Line text -> marker suffix for one assembled capsule render.

    Callers normally pass receipts + lane records; *_entries* is the
    pre-collected list (mark_updated_fact_lines merges receipt metadata with
    render-only lines)."""
    entries = list(_entries) if _entries is not None else _collect_entries(
        receipts, lane_records)
    markers: dict[str, str] = {}

    def _set(line: str, marker: str) -> None:
        # conflict outranks current/superseded: a line that is newest against
        # one record and unorderable against another says both
        if markers.get(line) == CONFLICT_MARKER:
            return
        if markers.get(line) == SUPERSEDED_MARKER and marker == CURRENT_MARKER:
            return  # a newer record replaced it; another, older pair cannot make it current again
        markers[line] = marker

    for i, a in enumerate(entries):
        for b in entries[i + 1:]:
            kind = _pair_kind(a, b)
            if kind == "none":
                continue
            if kind == "cue_one_sitting":
                # "van's in the garage, so it's the train to the wedding instead" after "we're driving to the
                # wedding in the van": one shared word is enough when both are the user's own lines of one day in
                # capture order (one chat said in passing); never across days or roles
                ordered = (_same_day_capture_order(a, b, _line_day(a["line"]), _line_day(b["line"]))
                           if a["role"] == b["role"] == "user" else None)
                if ordered is None or not _SWITCH_CUE_RE.search(ordered[1]["line"]):
                    continue
                _set(ordered[0]["line"], SUPERSEDED_MARKER)
                _set(ordered[1]["line"], CURRENT_MARKER)
                continue
            ka, kb = _order_key(a), _order_key(b)
            if ka is None or kb is None:
                # no time truth on one side: line-date labels decide; equal or
                # missing days cannot order, and a numeric conflict with no
                # order is exactly the "neither is clearly later" case
                da, db = _line_day(a["line"]), _line_day(b["line"])
                if da is not None and db is not None and da != db:
                    older, newer = (a, b) if da < db else (b, a)
                elif _same_day_capture_order(a, b, da, db) is not None:
                    # one day, both captured with a sequence: the capture order is the order they were said in (a
                    # long chat's turns on one day were all "conflicting records, give both" on 0a84ca8f)
                    older, newer = _same_day_capture_order(a, b, da, db)
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
    _mark_restored_values(entries, markers)
    if len(markers) > _MAX_MARKED_LINES:
        # keep conflicts first (they change what the reader should say at all),
        # then current, then superseded
        order = {CONFLICT_MARKER: 0, CURRENT_MARKER: 1, SUPERSEDED_MARKER: 2}
        ranked = sorted(markers.items(), key=lambda kv: (order[kv[1]], kv[0]))
        markers = dict(ranked[:_MAX_MARKED_LINES])
    return markers


def _record_body(line: str) -> str:
    body = _RECORD_LINE_RE.sub("", str(line or ""), count=1)
    body = _TURN_TAIL_RE.sub("", _DATE_PARENS_RE.sub(" ", body))
    return " ".join(body.lstrip(": ").split()).strip().strip('"').casefold()


def conversation_log_order(bodies: Sequence[str]) -> dict[str, int]:
    """Where each user statement sits in the conversation log (its last occurrence), for the bodies found there.

    Reads only; nothing is delivered from it. A body shorter than 12 characters is not looked up (too common)."""
    wanted = {b for b in bodies if len(b) >= 12}
    if not wanted:
        return {}
    try:
        from core.memory.files import conversation_log_path, load_jsonl

        rows = load_jsonl(conversation_log_path())
    except Exception:
        return {}
    found: dict[str, int] = {}
    for index, row in enumerate(rows):
        said = " ".join(str(row.get("user") or "").split()).casefold()
        if not said:
            continue
        for body in wanted:
            if body == said or body in said:
                found[body] = index
    return found


def _add_said_order(entries: list[dict[str, Any]], said_order: Any) -> None:
    """Give the user's records of one day their order in the conversation log, when two or more of them have no
    statement time: the distilled lines of a long chat carry only their day, and every change said in passing on that
    day was "conflicting records, give both" (served on 0a84ca8f: "my desk moved to room 3B", later "room 2F")."""
    by_day: dict[tuple[int, int, int], list[dict[str, Any]]] = {}
    for entry in entries:
        day = _line_day(entry["line"])
        if entry["role"] == "user" and entry.get("statement_at") is None and day is not None:
            by_day.setdefault(day, []).append(entry)
    pending = [e for group in by_day.values() if len(group) >= 2 for e in group]
    if not pending or said_order is None:
        return
    bodies = {id(e): _record_body(e["line"]) for e in pending}
    try:
        order = said_order(list(set(bodies.values())))
    except Exception:
        return
    for entry in pending:
        position = order.get(bodies[id(entry)])
        if position is not None:
            entry["said_order"] = position


def mark_updated_fact_lines(
    render_block: str,
    *,
    receipts: Sequence[Mapping[str, Any]] | None = None,
    lane_records: Sequence[tuple[Any, str]] | None = None,
    said_order: Any = None,
) -> str:
    """The render block with updated-fact markers suffixed to its record lines.

    Only lines inside the block itself are rewritten; every other byte of the
    render (headers, packet rows, notes) passes through untouched. A line the
    render repeats keeps its marker on each copy (same string, same status)."""
    text = str(render_block or "")
    if not text.strip():
        return text
    receipt_entries = _collect_entries(receipts or [], lane_records or [])
    render_extra = _render_entries(
        text, {entry["line"] for entry in receipt_entries})
    # metadata-bearing entries first: a line both sources know keeps its
    # capture sequence, not its label-only shadow
    entries = receipt_entries + render_extra
    _add_said_order(entries, said_order)
    markers = compute_markers([], [], _entries=entries)
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
    "CONFLICT_MARKER",
    "CURRENT_MARKER",
    "SUPERSEDED_MARKER",
    "compute_markers",
    "conversation_log_order",
    "mark_updated_fact_lines",
    "updated_fact_rule_sentence",
]
