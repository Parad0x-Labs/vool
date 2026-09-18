"""Typed-signal mapping: witnessed execution-fact dicts -> progression signals.

This module is the ONLY translation layer between the runtime's fact ledger
and the lifeform. It consumes plain dicts shaped like
``core.execution_truth.ExecutionFact.to_dict()`` and produces signal drafts;
it never imports execution_truth and never writes anything (law L5).

Honesty laws encoded here:
- a FAILED or REFUSED fact converts to NOTHING (a broken run is never
  "verified work");
- coarse families only — the raw tool name never becomes a farming axis, and
  families outside the coarse allowlist degrade to OBSERVED-only (zero GAME
  energy);
- long-task credit requires a completed fact of >= LONG_TASK_MIN_S duration;
- the council signal does not exist in this ruleset (single-home co-signing
  would be self-signing).
"""
from __future__ import annotations

from typing import Any

from core.companion.lifeform.progression import TOOL_FAMILIES

LONG_TASK_MIN_S = 3600

_COARSE_BY_NAME = {
    "web.search": "search", "web_fetch": "network", "http": "network",
    "git": "git", "file_write": "file", "file_read": "file",
    "file_edit": "file", "shell": "shell", "terminal": "shell",
    "test": "test", "pytest": "test", "model": "model",
    "retrieval": "retrieval", "effect": "effect",
}

_KIND_FAMILY = {
    "retrieval": ("retrieval",),
    "model": ("model",),
}

_FAMILY_SIGNAL = {
    "shell": "coding_task_verified",
    "file": "coding_task_verified",
    "git": "coding_task_verified",
    "effect": "coding_task_verified",
    "test": "test_suite_passed",
    "network": "research_task_completed",
    "search": "research_task_completed",
    "retrieval": "research_task_completed",
}


def coarse_family(name: str, categories: list[str] | tuple[str, ...]) -> str:
    """Coarse class for a tool/retrieval/model fact. Unknown -> 'unknown'
    (OBSERVED-only: zero GAME energy, red-team F3)."""
    lname = (name or "").strip().lower()
    for key, coarse in _COARSE_BY_NAME.items():
        if key in lname:
            return coarse
    for cat in categories or ():
        if cat in _KIND_FAMILY:
            return cat
    return "unknown"


def is_successful_fact(fact: dict[str, Any]) -> bool:
    """True only for facts whose own status says something actually ran and
    succeeded. Failed and refused facts convert to nothing (law L7)."""
    status = str(fact.get("status") or "").lower()
    if "refus" in status or "cancel" in status:
        return False
    return bool(fact.get("ok")) and "fail" not in status


def fact_to_signals(fact: dict[str, Any]) -> list[dict[str, Any]]:
    """One witnessed fact -> zero or more typed signal drafts. A fact may
    credit multiple signals (a long test run is both a test pass and a long
    task), but every draft carries the SAME (turn_key, fact_id) binding, so
    the reducer's derived event ids keep them dedupable per signal type."""
    if not is_successful_fact(fact):
        return []
    turn_key = str(fact.get("turn_key") or "")
    fact_id = str(fact.get("fact_id") or "")
    if not turn_key or not fact_id:
        return []
    day = str(fact.get("created_at") or "")[:10]
    surface = "cloud" if str(fact.get("session_id", "")).startswith("cloud") else "local"
    kind = str(fact.get("kind") or "")
    name = str(fact.get("name") or "")
    drafts: list[dict[str, Any]] = []

    def _draft(signal_type: str, family: str, verified: bool, degraded: bool = False,
               tokens: int = 0) -> None:
        drafts.append({
            "signal_type": signal_type, "turn_key": turn_key, "fact_id": fact_id,
            "family": family, "surface": surface, "verified": verified,
            "degraded": degraded, "tokens": tokens, "day": day,
        })

    if kind == "model":
        tokens = int((fact.get("detail") or {}).get("tokens_total") or 0)
        _draft("tokens_observed", "model", True, False, max(0, tokens))
        return drafts

    if kind == "retrieval":
        _draft("research_task_completed", "retrieval", True)
        return drafts

    if kind == "tool":
        family = coarse_family(name, fact.get("categories") or ())
        if family == "test":
            _draft("test_suite_passed", "test", True)
        elif family in ("shell", "file", "git", "effect"):
            _draft("coding_task_verified", family, True)
        elif family in ("network", "search"):
            _draft("research_task_completed", family, True)
        else:
            # coarse allowlist miss: OBSERVED-only (zero GAME energy)
            _draft("tool_family_used", "unknown", verified=True, degraded=True)
            return drafts
        if family != "test":
            _draft("tool_family_used", family, True)
        duration_s = float((fact.get("detail") or {}).get("duration_s") or 0.0)
        if duration_s >= LONG_TASK_MIN_S:
            _draft("long_task_survived", "long", True)
        return drafts

    # kinds outside the credited vocabulary (effects with no duration, etc.)
    # still count as presence at the reducer's daily_active_use layer handled
    # by the caller; they mint no energy signal here.
    return drafts


def daily_presence_signal(day: str, turn_key: str, fact_id: str) -> dict[str, Any]:
    """The once-per-UTC-day presence signal (weight-capped in the reducer)."""
    return {
        "signal_type": "daily_active_use", "turn_key": turn_key,
        "fact_id": fact_id, "family": "presence", "surface": "local",
        "verified": True, "degraded": False, "tokens": 0, "day": day,
    }


FAMILIES = tuple(TOOL_FAMILIES) + ("unknown", "long", "presence", "model")
