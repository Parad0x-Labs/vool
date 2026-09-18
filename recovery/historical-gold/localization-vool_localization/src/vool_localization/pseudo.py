"""Pseudo-localization generator.

Pseudo-localizing BEFORE real translations exist catches three classes of bug
with zero translator effort:

    1. hardcoded/unextractable strings      -> they stay unpseudolocalized ASCII
    2. truncation & layout overflow          -> expansion inflates length ~40%
    3. broken encodings / font gaps          -> accented lookalikes expose them

Transform:
    * wrap every string in accent markers  [!! ... !!]  (spot truncation)
    * map ASCII letters to accented lookalikes (é, ü, ñ, å ...)
    * inflate runs of vowels to simulate German/Brazilian-length UI copy
Format placeholders ({name}, plurals) are preserved untouched so messages
still render. Deterministic: same input, same output, always.
"""
from __future__ import annotations

import re

PLACEHOLDER = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")

_LOOKALIKE = {
    "a": "á", "A": "Á", "b": "ƀ", "B": "Ɓ", "c": "ç", "C": "Ç", "e": "é", "E": "É",
    "i": "ï", "I": "Ï", "o": "ö", "O": "Ö", "n": "ñ", "N": "Ñ", "u": "ü", "U": "Ü",
    "y": "ý", "s": "š", "S": "Š", "g": "ğ", "G": "Ğ", "k": "ķ", "K": "Ķ",
}
_VOWEL_INFLATION = {"a": "aa", "e": "ee", "o": "oo", "u": "uu", "A": "AA", "E": "EE", "O": "OO"}


def pseudolocalize(text: str, *, expand: bool = True) -> str:
    parts: list[str] = []
    last = 0
    for m in PLACEHOLDER.finditer(text):
        parts.append(_convert(text[last:m.start()], expand))
        parts.append(m.group(0))          # placeholder verbatim
        last = m.end()
    parts.append(_convert(text[last:], expand))
    return "[!! " + "".join(parts) + " !!]"


def _convert(chunk: str, expand: bool) -> str:
    out = []
    for ch in chunk:
        rep = _LOOKALIKE.get(ch)
        if rep is not None:
            out.append(rep)
        elif expand and ch in _VOWEL_INFLATION:
            out.append(_VOWEL_INFLATION[ch])
        else:
            out.append(ch)
    return "".join(out)


def expansion_ratio(sample_texts: list[str]) -> float:
    """Mean pseudo/en length ratio — used by the layout-expansion audit."""
    if not sample_texts:
        return 1.0
    ratios = [len(pseudolocalize(t)) / max(len(t), 1) for t in sample_texts]
    return sum(ratios) / len(ratios)
