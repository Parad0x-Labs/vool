"""Deterministic competition kernel — designed NOW so racing never requires
rewriting lifeform identity; kept MINIMAL (no netcode, no opponent AI).

race_outcome(participants, track, seed, ruleset) is a pure function:
  participants : [{"lifeform_id", "stats": {"speed","focus","rigor","insight"}}]
  track        : {"segments": [{"kind": "straight|hills|chicane|drag", "length": int}]}
  seed, ruleset: version-pinned

Fixed-point integer math only, seeded from the race seed — the same inputs
reproduce the same finish order anywhere, forever (ghost races need nothing
but the participants' canonical snapshots + track + seed). Stats map through a
soft tanh-like saturation so specialists win their segments and lose others;
"highest number wins everywhere" is structurally impossible.

Competition inputs are SIMULATION-ONLY: results never re-enter the progression
log (law L9 — no pay-to-win loop can form through racing).
"""
from __future__ import annotations

import hashlib
from typing import Any

COMPETITION_RULESET = "voolemon-race-0.1"

_SEGMENT_STAT = {
    "straight": "insight",
    "hills": "rigor",
    "chicane": "focus",
    "drag": "speed",
}
_SATURATION = 60  # tanh knee; soft-caps per-segment dominance


def _saturated(stat: int) -> int:
    # integer tanh surrogate: v = 100 * s / (s + knee), scaled by 100
    s = max(0, int(stat))
    return (100 * s) // (s + _SATURATION)


def _next_rand(state: int) -> tuple[int, int]:
    state = (state + 0x9E3779B97F4A7C15) & ((1 << 64) - 1)
    z = state
    z = ((z ^ (z >> 30)) * 0xBF58476D1CE4E5B9) & ((1 << 64) - 1)
    z = ((z ^ (z >> 27)) * 0x94D049BB133111EB) & ((1 << 64) - 1)
    return state, z ^ (z >> 31)


def race_outcome(participants: list[dict[str, Any]], track: dict[str, Any],
                 seed: str, ruleset: str = COMPETITION_RULESET) -> dict[str, Any]:
    if ruleset != COMPETITION_RULESET:
        raise ValueError(f"unknown competition ruleset {ruleset!r}")
    race_id_basis = f"{seed}|{ruleset}|" + "|".join(
        sorted(str(p.get("lifeform_id")) for p in participants))
    race_id = hashlib.sha256(race_id_basis.encode("utf-8")).hexdigest()[:32]
    rand_state = int(hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16], 16)
    times: dict[str, int] = {}
    for participant in participants:
        stats = dict(participant.get("stats") or {})
        total = 0
        for segment in (track.get("segments") or []):
            stat = int(stats.get(_SEGMENT_STAT.get(segment.get("kind", "straight"),
                                                 "straight"), 0))
            pace = 1000 * 100 // (100 + _saturated(stat))
            rand_state, jitter = _next_rand(rand_state)
            length = max(1, int(segment.get("length", 1)))
            total += (length * pace * (9900 + (jitter % 201))) // 1_000_000
        times[str(participant.get("lifeform_id"))] = total
    order = sorted(times, key=lambda lf: (times[lf], lf))
    return {"race_id": race_id, "ruleset": ruleset, "finish_order": order,
            "times": times,
            "note": "simulation-only; results never enter the progression log"}
