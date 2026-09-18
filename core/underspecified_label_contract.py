"""Deterministic epistemic boundary for explicitly undefined product labels."""

from __future__ import annotations

import re

_UNDEFINED_LABEL_RE = re.compile(
    r"\b(?:catalog|datasheet|inventory|listing)\b[^.!?\n]{0,100}"
    r"\b(?:calls?|labels?|lists?|names?)\b[^.!?\n]{0,40}"
    r"[\"“](?P<label>[^\"”\n]{1,80})[\"”][^.!?\n]{0,100}"
    r"\b(?:no|without)\b[^.!?\n]{0,40}\b(?:composition|definition|description|specification)\b",
    re.IGNORECASE,
)
_EPISTEMIC_QUESTION_RE = re.compile(
    r"\b(?:can|could|cannot|can't)\b[^?\n]{0,100}\b(?:conclude|determine|know|infer)\b"
    r"|\bwhat\b[^?\n]{0,40}\b(?:can|cannot|can't)\b[^?\n]{0,30}\b(?:concluded|determined|known|inferred)\b"
    r"|\bwithout\b[^?\n]{0,40}\b(?:looking\s+it\s+up|context|definition|composition|specification)\b",
    re.IGNORECASE,
)


def underspecified_label_response(user_text: str) -> str | None:
    """Explain why an explicitly undefined label cannot establish product type or composition."""

    text = str(user_text or "").strip()
    match = _UNDEFINED_LABEL_RE.search(text)
    if match is None or _EPISTEMIC_QUESTION_RE.search(text) is None:
        return None
    label = " ".join(match.group("label").split()).strip()
    if not label:
        return None
    return (
        f'The label “{label}” is only a product name, not evidence of composition or physical '
        "type. From that sentence alone, it could name a branded material, powder, liquid, or "
        "something else, so its actual category cannot be determined. A definition, composition, "
        "or product specification is needed; the ordinary-language words in the label are not "
        "enough."
    )


__all__ = ["underspecified_label_response"]
