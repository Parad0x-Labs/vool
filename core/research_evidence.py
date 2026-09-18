"""Small helpers that keep research confidence labels tied to observed evidence."""

from __future__ import annotations

from typing import Any

_EVIDENCE_LEVELS = {"none", "weak", "moderate", "strong"}


def truthful_evidence_strength(result: Any) -> str:
    raw = str(getattr(result, "evidence_strength", "") or "").strip().lower()
    if raw == "strong_evidence":
        raw = "strong"
    if raw not in _EVIDENCE_LEVELS:
        raw = "none"
    notes = [item for item in list(getattr(result, "notes", []) or []) if isinstance(item, dict)]
    domains = {str(item).strip().lower() for item in list(getattr(result, "source_domains", []) or []) if str(item).strip()}
    if raw == "strong" and (len(notes) < 3 or len(domains) < 2):
        return "moderate" if len(notes) >= 2 and len(domains) >= 2 else ("weak" if notes else "none")
    return raw


__all__ = ["truthful_evidence_strength"]
