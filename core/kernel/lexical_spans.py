"""Round-020 canonical lexical span authority.

One function partitions text into ordered, non-overlapping spans. Every numeric
consumer in the kernel reads the SAME partition — no independent regex authority.

Minimum span kinds for Round-020:
    URL         — deterministic: https?://\\S+
    IDENTIFIER  — letter-hyphen-digit, non-unit digit-letter compounds
    QUANTITY    — numeric values outside opaque spans
    OPAQUE      — residual catchall (version members, etc.)

Byte ownership invariant: when an opaque composite span (URL, IDENTIFIER) owns a
range, digits inside it cannot simultaneously emit QUANTITY spans.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = [
    "LexicalSpan",
    "has_quantity",
    "lex_spans",
    "opaque_ranges",
    "quantity_values",
]

# The existing number regex — now a subordinate recognizer used inside lex_spans.
_NUMBER_RE = re.compile(
    r"(?<![0-9A-Za-z.])-\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![0-9A-Za-z.])-\d+(?:\.\d+)?"
    r"|(?<![0-9A-Za-z.])\d{1,3}(?:,\d{3})+(?:\.\d+)?|(?<![0-9A-Za-z.])\d+(?:\.\d+)?"
)

_URL_RE = re.compile(r"https?://\S+")

# Unit suffixes from evidence_types — a digit-led token wearing these keeps its
# quantity ("24GB" is a QUANTITY, "2FA" is not).
_UNIT_SUFFIXES = frozenset((
    "gb", "mb", "kb", "tb", "pb", "ghz", "mhz", "khz", "hz", "pm", "am",
    "km", "kg", "kw", "kwh", "kmh", "mph", "mi", "lb", "lbs", "oz", "ml",
    "mg", "cm", "mm", "nm", "min", "mins", "ms", "hr", "hrs", "sec", "secs",
    "m", "s", "h", "d", "g", "l", "x", "k", "c", "f", "v", "w", "a"))


@dataclass(frozen=True)
class LexicalSpan:
    """One span of canonical lexical authority.

    kind:    "URL", "IDENTIFIER", "QUANTITY", or "OPAQUE"
    start:   byte offset into the source text (inclusive)
    end:     byte offset (exclusive)
    raw:     the exact source bytes this span owns
    """

    kind: str
    start: int
    end: int
    raw: str

    @property
    def normalized(self) -> str:
        """Canonical numeric form for QUANTITY spans; raw bytes for all others."""
        if self.kind == "QUANTITY":
            return self.raw.replace(",", "")
        return self.raw


def _is_identifier_candidate(text: str, match: re.Match) -> bool:
    """True when a _NUMBER_RE match is an identifier member, not a quantity.

    Replicates the identifier-detection logic from evidence_types._number_tokens
    and _number_index so the canonical lexer resolves ownership once.
    """
    start, end = match.span()
    raw = match.group()

    # Letter-hyphen-digit: VX-8042, NOVA-71  (the hyphen before the digit)
    if start >= 2 and text[start - 1] == "-" and text[start - 2].isalpha():
        return True

    # Check letter tail after the digits
    tail = ""
    k = end
    while k < len(text) and text[k].isalpha():
        tail += text[k]
        k += 1
    if tail.lower() == "e" and k < len(text) and (text[k].isdigit() or text[k] in "+-"):
        return False  # scientific notation: 1.5e3 keeps its 1.5
    if tail and k < len(text) and text[k].isalnum():
        return True   # identifier run continues: 0x1F drops its 0
    if tail and tail.lower() not in _UNIT_SUFFIXES:
        return True   # 2FA / 3GPP: identifier, not value+unit

    # Version-dotted member checks from _number_index
    if raw.count(".") >= 2:
        return True   # 3.14.7 is a version member
    if start > 0 and text[start - 1] == "." and start >= 2 and text[start - 2].isdigit():
        return True   # preceded by .<digit>: 14 in 3.14.7
    # followed by .<digit>: 14 in 3.14.7
    return bool(end < len(text) and text[end] == "." and end + 1 < len(text) and text[end + 1].isdigit())


def lex_spans(text: str) -> list[LexicalSpan]:
    """Return ordered, non-overlapping canonical lexical spans for *text*.

    Priority order:
    1. URL spans own their byte ranges unconditionally.
    2. _NUMBER_RE candidates inside URLs → skipped (URL-owned).
    3. _NUMBER_RE candidates that fail identifier checks → QUANTITY.
    4. _NUMBER_RE candidates that pass identifier checks → IDENTIFIER.
    5. Everything else → covered by implicit OPAQUE (no consumer needs iteration).
    """
    spans: list[LexicalSpan] = []

    # Phase 1: URL spans (highest priority)
    url_ranges = [(m.start(), m.end()) for m in _URL_RE.finditer(text)]
    for start, end in url_ranges:
        spans.append(LexicalSpan("URL", start, end, text[start:end]))

    def _in_url(pos: int) -> bool:
        return any(a <= pos < b for a, b in url_ranges)

    # Phase 2: QUANTITY / IDENTIFIER from _NUMBER_RE candidates
    for m in _NUMBER_RE.finditer(text):
        start, end = m.span()
        raw = m.group()

        # URL owns its bytes — skip
        if _in_url(start):
            continue

        if _is_identifier_candidate(text, m):
            spans.append(LexicalSpan("IDENTIFIER", start, end, raw))
        else:
            spans.append(LexicalSpan("QUANTITY", start, end, raw))

    # Sort by start position (URL spans come first from Phase 1, then
    # QUANTITY/IDENTIFIER spans interleaved — sort once at the end).
    spans.sort(key=lambda s: s.start)
    return spans


def has_quantity(text: str) -> bool:
    """True when *text* contains at least one QUANTITY span."""
    return any(s.kind == "QUANTITY" for s in lex_spans(text))


def quantity_values(text: str) -> set[str]:
    """Every canonical QUANTITY value in *text*, with thousands separators normalized."""
    return {s.normalized for s in lex_spans(text) if s.kind == "QUANTITY"}


def opaque_ranges(text: str) -> list[tuple[int, int]]:
    """Every byte range owned by a non-QUANTITY span (URL, IDENTIFIER, OPAQUE).

    Subordinate semantic consumers (unit pairs, temperature, duration) must
    skip regex matches whose number position falls inside these ranges —
    the owning span's digits are never available for quantity semantics.
    """
    return [(s.start, s.end) for s in lex_spans(text) if s.kind != "QUANTITY"]
