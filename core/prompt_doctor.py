"""Prompt doctor - score a generation prompt and auto-rewrite until it ships.

Implements the doctrine's scoring rubric (see docs/vool_visual_script_intelligence.md) as
executable code: a weighted 1-10 rubric with three hard gates (model_compatibility,
coherence_no_contradictions, safety), a ship threshold, and a bounded rewrite loop that fixes
the weak facets rather than regenerating blindly. Model-agnostic: the caller injects a
``model_client(prompt: str) -> str`` (the local model), so the loop is unit-testable with a stub.

Safety is absolute: a prompt the scorer marks unsafe (minors in a sexual or graphic-violent
context, a nonconsensual real-person likeness, or real-world illegal how-to) is blocked, never
shipped-with-a-warning. Everything else - including explicit adult content between adults - is a
craft question, not a moderation one.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass

SHIP_THRESHOLD = 8.5          # overall must reach this to ship
GATE_SHIP_MIN = 7.0           # every gate dimension must reach this to ship
MIN_DIM = 6.0                 # no single scored dimension may sit below this to ship
GATE_CAP = 6.0               # if any gate scores below this, overall is capped here
SAFETY_BLOCK_AT = 2.0         # safety at/below this is an unsafe hard-block
MAX_REWRITES = 2              # cap the auto-rewrite at 2 passes, then return the best draft


@dataclass(frozen=True)
class Dimension:
    key: str
    weight: float          # weight in the weighted mean for VIDEO (see effective_weight for stills)
    gate: bool = False     # a hard gate: below GATE_CAP caps the overall; below GATE_SHIP_MIN blocks ship
    still_dropped: bool = False  # dropped from the mean for a still image (no motion/sound to score)
    in_mean: bool = True   # safety is a gate but is not averaged into the score


DIMENSIONS: tuple[Dimension, ...] = (
    Dimension("visual_clarity", 1.4),
    Dimension("originality", 1.0),  # re-weighted to 1.3 for stills (see effective_weight)
    Dimension("scene_motion", 1.3, still_dropped=True),
    Dimension("cinematic_strength", 1.1),
    Dimension("emotional_hook", 1.0),
    Dimension("genre_fit", 1.0),
    Dimension("model_compatibility", 1.5, gate=True),
    Dimension("production_realism", 1.0),
    Dimension("sound_voice_usefulness", 1.0, still_dropped=True),
    Dimension("coherence_no_contradictions", 1.5, gate=True),
    Dimension("anti_ai_slop", 1.2),
    Dimension("safety", 0.0, gate=True, in_mean=False),
)
_BY_KEY = {d.key: d for d in DIMENSIONS}
GATE_KEYS = tuple(d.key for d in DIMENSIONS if d.gate)

_SCALE = (
    "Score each dimension 1-10 on one anchor: 1-3 absent/broken (nothing usable or cues conflict); "
    "4-5 present but weak (renders, but generic and under-specified); 6-7 functional (ships, one clear "
    "read, still stock); 8-9 strong and model-ready (specific, front-loaded, nothing left to guess); "
    "10 exemplary (specific AND original AND clean). For safety, 10 = clearly fine, and 0-2 = unsafe "
    "(minors in a sexual or graphic-violent context, a nonconsensual real-person likeness, or real-world "
    "illegal how-to). Explicit adult content between adults is NOT unsafe - score it on craft."
)


def _is_still(medium: str) -> bool:
    return str(medium or "video").strip().lower().startswith(("image", "still", "photo", "pic"))


def effective_weight(dim: Dimension, medium: str) -> float | None:
    """Weight of a dimension in the weighted mean for this medium, or None if it is excluded."""
    if not dim.in_mean:
        return None
    if _is_still(medium) and dim.still_dropped:
        return None
    if dim.key == "originality":
        return 1.3 if _is_still(medium) else 1.0
    return dim.weight


def weighted_overall(scores: dict[str, float], medium: str = "video") -> float:
    """The weighted mean, capped at GATE_CAP if any hard gate is below GATE_CAP."""
    num = den = 0.0
    for dim in DIMENSIONS:
        w = effective_weight(dim, medium)
        if w is None:
            continue
        if dim.key not in scores:
            continue
        num += w * float(scores[dim.key])
        den += w
    overall = (num / den) if den else 0.0
    if any(float(scores.get(g, 10)) < GATE_CAP for g in GATE_KEYS):
        overall = min(overall, GATE_CAP)
    return round(overall, 2)


def _scored_dims(medium: str) -> list[str]:
    return [d.key for d in DIMENSIONS if effective_weight(d, medium) is not None]


def verdict(scores: dict[str, float], medium: str = "video") -> dict:
    """Ship decision + diagnostics for a scored prompt."""
    overall = weighted_overall(scores, medium)
    blocked = float(scores.get("safety", 10)) <= SAFETY_BLOCK_AT
    gates_ok = all(float(scores.get(g, 0)) >= GATE_SHIP_MIN for g in GATE_KEYS)
    scored = _scored_dims(medium)
    min_dim_ok = all(float(scores.get(k, 0)) >= MIN_DIM for k in scored)
    ship = (overall >= SHIP_THRESHOLD) and gates_ok and min_dim_ok and not blocked
    weak = [k for k in scored if float(scores.get(k, 10)) < MIN_DIM]
    weakest = min(scored, key=lambda k: float(scores.get(k, 10))) if scored else None
    if blocked:
        zone = "blocked_unsafe"
    elif overall >= 9.0:
        zone = "ship_as_is"
    elif overall >= SHIP_THRESHOLD:
        zone = "ship_note_weakest"
    elif overall >= 7.0:
        zone = "rewrite_flagged_facets"
    elif overall >= 5.0:
        zone = "rewrite_all_sub6"
    else:
        zone = "rebuild"
    return {
        "overall": overall, "ship": ship, "blocked": blocked, "gates_ok": gates_ok,
        "zone": zone, "weakest": weakest, "weak_dimensions": weak,
    }


def build_scoring_prompt(prompt_text: str, medium: str = "video") -> str:
    dims = ", ".join([*_scored_dims(medium), "safety"])
    return (
        "You are a strict generative-media prompt reviewer. Score the PROMPT below for a "
        f"{'still image' if _is_still(medium) else 'video'} model.\n"
        f"{_SCALE}\n"
        f"Return ONLY a JSON object mapping each of these keys to an integer 1-10: {dims}. "
        "No prose, no code fence.\n\nPROMPT:\n" + str(prompt_text or "")
    )


def build_rewrite_prompt(prompt_text: str, scores: dict[str, float], medium: str = "video") -> str:
    weak = [k for k in _scored_dims(medium) if float(scores.get(k, 10)) < MIN_DIM] or ["overall polish"]
    return (
        "Rewrite the generation PROMPT below so it ships, fixing ONLY these weak facets while keeping "
        f"everything already strong: {', '.join(weak)}.\n"
        "Rules: replace vague adjectives with specific nouns/materials; keep one subject, one primary "
        "action, one setting, one light source, one time-of-day (no contradictions); add a shot size + "
        "lens/motion if camera language is missing; delete quality-tag filler ('stunning', 'masterpiece', "
        "'8k'); tie any feeling to a visible cue. Keep it within a model's parse window, subject and style "
        "front-loaded. Output ONLY the rewritten prompt - no preamble, no explanation.\n\nPROMPT:\n"
        + str(prompt_text or "")
    )


def parse_scores(raw: str) -> dict[str, float]:
    """Extract the {dimension: score} JSON from a model reply; clamp to 1-10, ignore unknown keys."""
    text = str(raw or "").strip()
    if text.startswith("```"):
        text = "\n".join(ln for ln in text.splitlines() if not ln.strip().startswith("```")).strip()
    payload: dict = {}
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            payload = obj
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                obj = json.loads(match.group(0))
                if isinstance(obj, dict):
                    payload = obj
            except json.JSONDecodeError:
                payload = {}
    out: dict[str, float] = {}
    for key, value in payload.items():
        k = str(key).strip().lower()
        if k not in _BY_KEY:
            continue
        try:
            out[k] = max(1.0, min(10.0, float(value)))
        except (TypeError, ValueError):
            continue
    return out


def doctor_prompt(
    prompt_text: str,
    *,
    model_client: Callable[[str], str],
    medium: str = "video",
    max_rewrites: int = MAX_REWRITES,
) -> dict:
    """Score the prompt and auto-rewrite up to ``max_rewrites`` times, returning the best draft.

    Returns {final_prompt, verdict, rounds, blocked, note, history}. The loop stops early when a
    draft ships or is blocked as unsafe; on an unsafe block it does not ship the draft.
    """
    history: list[dict] = []
    current = str(prompt_text or "")
    for attempt in range(int(max_rewrites) + 1):
        scores = parse_scores(model_client(build_scoring_prompt(current, medium)))
        v = verdict(scores, medium)
        history.append({"prompt": current, "scores": scores, "verdict": v})
        if v["ship"] or v["blocked"] or attempt == int(max_rewrites):
            break
        rewritten = str(model_client(build_rewrite_prompt(current, scores, medium)) or "").strip()
        if not rewritten or rewritten == current:
            break
        current = rewritten

    best = max(history, key=lambda h: (not h["verdict"]["blocked"], h["verdict"]["overall"]))
    bv = best["verdict"]
    if bv["blocked"]:
        note = "Blocked: the prompt was scored unsafe; redirect to a safe version rather than shipping."
    elif bv["ship"]:
        note = "" if bv["zone"] == "ship_as_is" else f"Ships; weakest facet: {bv['weakest']}."
    else:
        note = f"Did not reach {SHIP_THRESHOLD} after {len(history) - 1} rewrite(s); weakest: {bv['weakest']}."
    return {
        "final_prompt": "" if bv["blocked"] else best["prompt"],
        "verdict": bv,
        "rounds": len(history),
        "blocked": bv["blocked"],
        "note": note,
        "history": history,
    }
