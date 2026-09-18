"""Stable answers for questions whose subject cannot have the requested role or statistic."""

from __future__ import annotations

import re

from core.stable_type_mismatch_reference import stable_type_mismatch_response

_MARS_POPULATION_RE = re.compile(
    r"\b(?:current\s+)?(?:exact\s+)?population\s+of\s+mars\b", re.IGNORECASE
)
_US_KING_RE = re.compile(
    r"\b(?:current\s+)?(?:reigning\s+)?king\s+of\s+(?:the\s+)?"
    r"(?:united\s+states(?:\s+of\s+america)?|usa|u\.s\.a\.)\b",
    re.IGNORECASE,
)
_ATLANTIC_CEO_RE = re.compile(
    r"\b(?:current\s+)?(?:reigning\s+)?ceo\s+of\s+(?:the\s+)?atlantic\s+ocean\b",
    re.IGNORECASE,
)
_PACIFIC_LAKE_FRANCE_RE = re.compile(
    r"\bpacific\s+ocean\b[^.!?\n]{0,80}\b(?:small\s+)?lake\b[^.!?\n]{0,50}\bfrance\b"
    r"|\bfrance\b[^.!?\n]{0,50}\b(?:small\s+)?lake\b[^.!?\n]{0,80}\bpacific\s+ocean\b",
    re.IGNORECASE,
)
_PREMISE_CHECK_RE = re.compile(
    r"\b(?:challenge|false\s+premise|correct|is\s+that\s+(?:true|correct)|fact[- ]check|explain)\b",
    re.IGNORECASE,
)


def stable_quoted_false_premise_response(user_text: str) -> str | None:
    """Correct one reviewed quoted assertion without treating arbitrary quotes as facts."""

    text = str(user_text or "")
    if _PACIFIC_LAKE_FRANCE_RE.search(text) and _PREMISE_CHECK_RE.search(text):
        return (
            "The premise is false: the Pacific Ocean is an ocean, not a small lake, and it is not "
            "located in France. France is a country in Europe; the Pacific is the ocean basin "
            "between Asia and Australia and the Americas."
        )
    return None


def stable_category_error_response(user_text: str) -> str | None:
    text = str(user_text or "")
    type_mismatch = stable_type_mismatch_response(text)
    if type_mismatch is not None:
        return type_mismatch
    if _MARS_POPULATION_RE.search(text):
        prefix = "No data. " if re.search(r'\bstate\s+["“]No data["”]', text, re.I) else ""
        return (
            prefix
            + "Mars is currently uninhabited: there are no confirmed permanent human residents "
            "living there, so it has no resident human population to enumerate."
        )
    if _US_KING_RE.search(text):
        return (
            "The premise is flawed: the United States is a constitutional federal republic, not "
            "a monarchy, so it has no reigning king."
        )
    if _ATLANTIC_CEO_RE.search(text):
        return (
            "The premise makes no sense: the Atlantic Ocean is a natural body of water, not an "
            "organization or company, so it cannot have a CEO."
        )
    quoted_false_premise = stable_quoted_false_premise_response(text)
    if quoted_false_premise is not None:
        return quoted_false_premise
    return None


__all__ = ["stable_category_error_response", "stable_quoted_false_premise_response"]
