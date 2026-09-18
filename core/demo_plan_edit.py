"""Edit a DemoPlan - the accept/modify half of the demo planner.

Structured, pure transformations of a plan (change a shot's subtitle, retime a shot, remove/reorder
a shot, recast the presenter, retime the whole video), plus a light natural-language parser that maps
common phrasings to those ops. The user reviews a plan, says what to change, and gets a new plan back;
nothing renders until they accept. Shot numbers match the plan's displayed index (Shot 0, Shot 1, ...).
"""

from __future__ import annotations

import re
from dataclasses import replace

from core.demo_planner import DemoPlan, Shot, compose_shot_prompt, retime

_TEXT_FIELDS = {
    "subtitle": "subtitle",
    "action": "presenter_action",
    "background": "background",
    "camera": "camera",
}


def _relayout(shots: list[Shot]) -> list[Shot]:
    """Recompute contiguous start/end (each >= 2s) and renumber after a structural change."""
    cursor = 0
    out: list[Shot] = []
    for i, s in enumerate(shots):
        dur = max(2, s.end_s - s.start_s)
        out.append(replace(s, index=i, start_s=cursor, end_s=cursor + dur))
        cursor += dur
    return out


def set_field(plan: DemoPlan, shot_index: int, field: str, value: str) -> DemoPlan:
    attr = _TEXT_FIELDS.get(str(field))
    if attr is None or not (0 <= shot_index < len(plan.shots)):
        return plan
    shots = list(plan.shots)
    updated = replace(shots[shot_index], **{attr: str(value).strip()})
    shots[shot_index] = replace(updated, generation_prompt=compose_shot_prompt(updated, plan.brief))
    return DemoPlan(brief=plan.brief, shots=shots)


def set_duration(plan: DemoPlan, shot_index: int, seconds: int) -> DemoPlan:
    if not (0 <= shot_index < len(plan.shots)):
        return plan
    seconds = max(2, int(seconds))
    shots = list(plan.shots)
    s = shots[shot_index]
    shots[shot_index] = replace(s, end_s=s.start_s + seconds)
    return DemoPlan(brief=plan.brief, shots=_relayout(shots))


def remove_shot(plan: DemoPlan, shot_index: int) -> DemoPlan:
    if not (0 <= shot_index < len(plan.shots)) or len(plan.shots) <= 1:
        return plan
    shots = [s for i, s in enumerate(plan.shots) if i != shot_index]
    return DemoPlan(brief=plan.brief, shots=_relayout(shots))


def move_shot(plan: DemoPlan, shot_index: int, to_index: int) -> DemoPlan:
    n = len(plan.shots)
    if not (0 <= shot_index < n) or not (0 <= to_index < n):
        return plan
    shots = list(plan.shots)
    shots.insert(to_index, shots.pop(shot_index))
    return DemoPlan(brief=plan.brief, shots=_relayout(shots))


def set_presenter(plan: DemoPlan, presenter: str) -> DemoPlan:
    new_brief = replace(plan.brief, presenter=str(presenter).strip().lower())
    shots = [replace(s, generation_prompt=compose_shot_prompt(s, new_brief)) for s in plan.shots]
    return DemoPlan(brief=new_brief, shots=shots)


def apply_op(plan: DemoPlan, op: dict) -> DemoPlan:
    """Apply one structured edit op ({'op': ..., ...}); unknown ops are no-ops."""
    if not isinstance(op, dict):
        return plan
    kind = str(op.get("op") or "")
    try:
        if kind == "set_field":
            return set_field(plan, int(op.get("shot", -1)), str(op.get("field", "")), op.get("value", ""))
        if kind == "set_duration":
            return set_duration(plan, int(op.get("shot", -1)), int(op.get("seconds", 0)))
        if kind == "remove_shot":
            return remove_shot(plan, int(op.get("shot", -1)))
        if kind == "move_shot":
            return move_shot(plan, int(op.get("shot", -1)), int(op.get("to", -1)))
        if kind == "set_presenter":
            return set_presenter(plan, str(op.get("value", "")))
        if kind == "retime":
            return retime(plan, int(op.get("seconds", 0)))
    except (TypeError, ValueError):
        return plan
    return plan


def _beat_index(plan: DemoPlan, word: str) -> int:
    word = word.strip().lower()
    if word in ("last", "final", "end"):
        return len(plan.shots) - 1
    if word in ("first", "start", "beginning"):
        return 0
    for i, s in enumerate(plan.shots):
        if s.beat == word:
            return i
    return -1


def parse_edit_instruction(text: str, plan: DemoPlan) -> dict | None:
    """Map a common natural-language edit to a structured op, or None if it can't be parsed.
    Order matters: presenter and remove are explicit; a NAMED shot always beats a whole-video retime;
    a whole-video retime only fires on an absolute request, never a comparative."""
    raw = str(text or "")
    low = " ".join(raw.lower().split())
    if not low:
        return None

    # Presenter recast - gated on the word "presenter" so ordinary content ("boy band theme",
    # "a person's name") is never misread as a destructive recast.
    if "presenter" in low:
        pm = re.search(r"\b(woman|man|girl|boy|person)\b", low)
        if pm:
            return {"op": "set_presenter", "value": pm.group(1)}

    # Remove a shot (by number or beat).
    if re.search(r"\b(remove|delete|drop|cut)\b", low):
        m = re.search(r"shot\s+(\d+)", low)
        if m:
            return {"op": "remove_shot", "shot": int(m.group(1))}
        m = re.search(r"\b(hook|intro|outro|last|first|final)\b", low)
        if m:
            idx = _beat_index(plan, m.group(1))
            if idx >= 0:
                return {"op": "remove_shot", "shot": idx}

    # A named shot takes precedence over a whole-video retime.
    shot_m = re.search(r"\bshot\s+(\d+)", low)
    if shot_m:
        shot = int(shot_m.group(1))
        # subtitle change (case-preserving). `to\b` so "Top"/"Tokyo" are not eaten by an optional "to".
        sub = re.search(r"shot\s+\d+.*?subtitle\s*(?:to\b|:)?\s*(.+)$", raw, re.IGNORECASE)
        if sub:
            return {"op": "set_field", "shot": shot, "field": "subtitle", "value": sub.group(1).strip().strip('"')}
        dur = re.search(r"(\d+)\s*(?:s|sec|secs|seconds)\b", low)
        if dur:
            return {"op": "set_duration", "shot": shot, "seconds": int(dur.group(1))}
        if 0 <= shot < len(plan.shots):
            if "longer" in low:
                return {"op": "set_duration", "shot": shot, "seconds": plan.shots[shot].duration_s + 2}
            if "shorter" in low:
                return {"op": "set_duration", "shot": shot, "seconds": max(2, plan.shots[shot].duration_s - 2)}
        return None

    # Whole-video retime - only an absolute request; a comparative/relative phrasing is not a retime.
    if re.search(r"\b(faster|slower|shorter|longer|too long|too short|ago|earlier|later)\b", low):
        return None
    if re.search(r"\bretime\b|\bmake (?:it|the video)\b|\b(?:whole|entire|total|overall)\b"
                 r"|\b\d+[- ]?seconds?\s+(?:video|demo|clip)\b", low):
        m = re.search(r"(\d+)\s*(?:s|sec|secs|seconds)\b", low)
        if m:
            return {"op": "retime", "seconds": int(m.group(1))}
    return None


def apply_edits(plan: DemoPlan, edits: list) -> tuple[DemoPlan, list]:
    """Apply a mixed list of structured ops (dicts) and NL instructions (strings). Returns the new
    plan and the list of edits that could not be parsed OR produced no change (detected by identity,
    since every successful op builds a new DemoPlan)."""
    current = plan
    unapplied: list = []
    for edit in edits or []:
        if isinstance(edit, dict):
            new = apply_op(current, edit)
        else:
            op = parse_edit_instruction(str(edit), current)
            if op is None:
                unapplied.append(str(edit))
                continue
            new = apply_op(current, op)
        if new is current:                       # no-op (out-of-range / unknown / no change)
            unapplied.append(edit if isinstance(edit, dict) else str(edit))
        else:
            current = new
    return current, unapplied
