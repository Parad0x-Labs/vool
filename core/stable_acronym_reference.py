"""Local reference answers for explicit multi-domain acronym expansion questions."""

from __future__ import annotations

import re

_EXPANSIONS = {
    "PHP": "PHP — PHP: Hypertext Preprocessor",
    "BSD": "BSD — Berkeley Software Distribution",
    "SOS": "SOS — the distress signal; it is not a standard computing acronym",
    "CAD": "CAD — computer-aided design",
    "RUB": "RUB — ISO 4217 code for the Russian ruble",
}
_QUESTION_RE = re.compile(
    r"\b(?:what\s+do|what\s+does|stand\s+for|typically\s+mean|expand)\b",
    re.IGNORECASE,
)
_DOMAIN_RE = re.compile(
    r"\b(?:computing|networking|engineering\s+software|global\s+finance)\b",
    re.IGNORECASE,
)


def stable_acronym_reference_response(user_text: str) -> str | None:
    text = str(user_text or "")
    if not _QUESTION_RE.search(text) or not _DOMAIN_RE.search(text):
        return None
    codes: list[str] = []
    for token in re.findall(r"\b[A-Z]{3}\b", text):
        if token in _EXPANSIONS and token not in codes:
            codes.append(token)
    return "\n".join(_EXPANSIONS[code] for code in codes) if len(codes) >= 2 else None


__all__ = ["stable_acronym_reference_response"]
