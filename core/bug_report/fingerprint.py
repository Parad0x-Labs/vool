"""Deterministic issue fingerprint: deduplicate by root cause, not by cosmetic noise.

Fingerprint inputs are only sanitized, typed fields: category, exception type, top frame
(file basename + function, NO line numbers -- they move with every edit), the sorted
component ids, and normalized repro steps. The TITLE is deliberately excluded: it is
presentation, not cause, and two reports of the same crash routinely drift ("...again",
"...still"). Timestamps, request ids, turn numbers and other run-scoped counters are
normalized away, so the same defect reported twice from two different machines at two
different times yields the same fingerprint.
"""
from __future__ import annotations

import hashlib
import json
import re

from core.bug_report.schema import InvolvedComponents, SanitizedError

_NOISE_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}[T ][0-9:.+,Z-]*")
_NOISE_ID_RE = re.compile(r"\b(?:req|sess|turn|run|trace|span)_[A-Za-z0-9]{4,}\b")
# Two-or-more digit runs are run-scoped counters (turn 17, span 42, attempt 03) more often
# than they are the discriminating signal of a defect, so they normalize away for
# dedup purposes; the issue body still carries the exact numbers.
_NOISE_LONG_DIGITS_RE = re.compile(r"\d{2,}")
_WHITESPACE_RE = re.compile(r"\s+")


def _normalize_free_text(text: str) -> str:
    value = str(text or "").lower()
    value = _NOISE_DATE_RE.sub("<date>", value)
    value = _NOISE_ID_RE.sub("<id>", value)
    value = _NOISE_LONG_DIGITS_RE.sub("<n>", value)
    return _WHITESPACE_RE.sub(" ", value).strip()


def compute_fingerprint(
    *,
    category: str,
    error: SanitizedError | None,
    components: InvolvedComponents,
    repro_steps,
    title: str = "",
) -> str:
    frames = [
        {"file": f.file, "function": f.function}
        for f in (error.frames[:5] if error is not None else [])
    ]
    payload = {
        "category": str(category or "").lower().strip(),
        "exc_type": (error.exc_type if error is not None else "").lower(),
        "frames": frames,
        "lanes": sorted(set(components.lanes)),
        "tools": sorted(set(components.tools)),
        "models": sorted(set(components.models)),
        "repro": [_normalize_free_text(step) for step in repro_steps],
    }
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]


__all__ = ["compute_fingerprint"]
