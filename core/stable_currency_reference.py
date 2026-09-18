"""Deterministic answers for explicit ISO-4217 word-identification questions.

Currency identities are closed, stable reference data already owned by ``currency_intent``.  A
model should explain open-ended finance questions, but it should not spend a minute guessing that
``MOP`` means buybacks when the user explicitly asks which words are ISO currency codes.
"""

from __future__ import annotations

import re

from core.currency_intent import ISO_4217, code_candidates

_EXPLICIT_REFERENCE_RE = re.compile(
    r"\b(?:iso\s*4217|currency\s+codes?|global\s+finance|common\s+financially)\b",
    re.IGNORECASE,
)
_IDENTIFICATION_RE = re.compile(
    r"\b(?:identify|which|list|what\s+do|what\s+does|stand\s+for|share)\b",
    re.IGNORECASE,
)
_COUNTRY_LIST_RE = re.compile(
    r"\b(?:list|name|which|what)\b[^.?!]{0,100}\b(?:countries|jurisdictions|issuers)\b"
    r"[^.?!]{0,100}\b(?:use|using|behind|for)\b[^.?!]{0,80}\b(?:those|these|the)\b"
    r"[^.?!]{0,40}\b(?:official\s+)?currency\s+codes?\b",
    re.IGNORECASE,
)
_PLAIN_ENGLISH_PHRASE_RE = re.compile(
    r"\bwhat\s+does\s+[\"']?(?P<first>[A-Z]{3})\s+"
    r"(?P<second>[A-Z]{3})[\"']?\s+mean\s+in\s+plain\s+english\b",
    re.IGNORECASE,
)
_PLAIN_GLOSSES = {
    "TRY": "attempt",
    "ALL": "everything",
}


def stable_currency_reference_response(user_text: str) -> str | None:
    """Map explicitly requested code-like words to stable currency facts, without retrieval."""

    text = str(user_text or "").strip()
    phrase = _PLAIN_ENGLISH_PHRASE_RE.search(text)
    if phrase:
        first_code = phrase.group("first").upper()
        second_code = phrase.group("second").upper()
        first = _PLAIN_GLOSSES.get(first_code)
        second = _PLAIN_GLOSSES.get(second_code)
        if first and second:
            return f'{first_code} {second_code} means "{first} {second}" in plain English.'
    if not text or not _EXPLICIT_REFERENCE_RE.search(text) or not _IDENTIFICATION_RE.search(text):
        return None
    if re.search(r"\b(?:engineering|software|computing|networking)\b", text, re.IGNORECASE):
        return None
    codes: list[str] = []
    for candidate in code_candidates(text):
        code = candidate.code
        if code not in codes:
            codes.append(code)
    if not codes:
        return None

    asks_for_countries = bool(
        re.search(
            r"\b(?:which|what)\s+(?:three\s+|two\s+)?countries\b",
            text,
            re.IGNORECASE,
        )
        or _COUNTRY_LIST_RE.search(text)
    )
    rows: list[str] = []
    for code in codes:
        fact = ISO_4217[code]
        if asks_for_countries:
            rows.append(f"{code} — {fact.region} ({fact.name})")
        else:
            rows.append(f"{code} — {fact.name} ({fact.region})")
    return "ISO 4217 currency codes:\n" + "\n".join(rows)


__all__ = ["stable_currency_reference_response"]
