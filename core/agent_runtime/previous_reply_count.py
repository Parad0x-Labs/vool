"""Counts and positions asked about the reply just given, computed, never estimated.

"how many words is that sentence?" after VOOL wrote a sentence was answered 12 by the model; the
sentence had 15 (0.7.8 release sweep, 2026-11-12, block 1c). A count of text the runtime holds is
arithmetic, not generation: this lane reads the previous reply from the chat and counts it.
Contributor: sls_0x.
"""
from __future__ import annotations

import re

LANE_ID = "previous_reply_count"

_UNIT = r"(?P<unit>words?|characters?|letters?|sentences?)"
_UNIT2 = r"(?P<unit2>words?|characters?|letters?|sentences?)"
_TARGET = (
    r"(?:that|this|the|your|ur)\s+(?:last\s+|previous\s+)?(?:sentence|reply|answer|response|message|text|line)"
    r"|what\s+(?:you|u)\s+(?:just\s+)?(?:wrote|said|replied|sent)|(?:it|that)\b"
)
_COUNT_RE = re.compile(
    rf"^\s*(?:(?:can|could)\s+(?:you|u)\s+)?(?:how\s+many\s+{_UNIT}\s+(?:(?:is|are|does|do|has|have)(?:\s+(?:there\s+)?in)?|in)\s+(?:{_TARGET})(?:\s+(?:have|has|contain))?"
    rf"|count\s+(?:the\s+)?{_UNIT2}\s+(?:in|of)\s+(?:{_TARGET}))\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_FIRST_LAST_RE = re.compile(
    rf"^\s*what(?:'s|\s+is|\s+was)\s+the\s+(?P<pos>first|last)\s+word\s+(?:of|in)\s+(?:{_TARGET})\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[^\W_]+(?:['’-][^\W_]+)*", re.UNICODE)


def _previous_reply(session_id: str) -> str:
    try:
        from core.chat_session_identity import canonical_chat_session_id
        from core.memory.entries import recent_conversation_events

        rows = list(recent_conversation_events(canonical_chat_session_id(str(session_id or "")), limit=2) or ())
    except Exception:
        return ""
    for row in reversed(rows):
        text = str((row or {}).get("assistant") or "").strip()
        if text:
            from core.response_provenance import strip_provenance_footer

            return strip_provenance_footer(text).strip()
    return ""


def previous_reply_count(question: str, *, session_id: str) -> str | None:
    """The computed answer, or None when the question is not one this lane owns."""
    text = " ".join(str(question or "").split())
    count = _COUNT_RE.match(text)
    position = _FIRST_LAST_RE.match(text)
    if not count and not position:
        return None
    reply = _previous_reply(session_id)
    if not reply:
        return None
    words = _WORD_RE.findall(reply)
    if position:
        if not words:
            return None
        word = words[0] if position.group("pos").lower() == "first" else words[-1]
        return f"The {position.group('pos').lower()} word of my last reply is “{word}”."
    unit = (count.group("unit") or count.group("unit2")).lower().rstrip("s")
    if unit == "word":
        n = len(words)
    elif unit == "sentence":
        n = len([s for s in re.split(r"(?<=[.!?])\s+", reply) if s.strip()])
    elif unit == "letter":
        n = sum(1 for ch in reply if ch.isalpha())
    else:
        n = len(reply)
    label = {"word": "word", "sentence": "sentence", "letter": "letter", "character": "character"}[unit]
    return f"My last reply has {n} {label}{'' if n == 1 else 's'}."


__all__ = ["LANE_ID", "previous_reply_count"]


# --- arithmetic on the number the last reply gave ----------------------------------------------
# "plus 100?", "now add a hundred to the result", "and minus 7", "times 3 please": a continuation
# of a computed answer is arithmetic on that answer. Measured on the 0.7.8 sweep (block 1c): the
# model asked "could you clarify?" and once returned no usable response. Computed here instead.

_NUMBER_WORDS = {
    "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
    "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30,
    "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90,
}
_SCALES = {"hundred": 100, "thousand": 1000, "million": 1_000_000}
_NUM = r"(?P<num>-?\d+(?:[.,]\d+)?|(?:a|an|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred|thousand|million|and|[\s-])+)"
_OP_RE = re.compile(
    rf"^\s*(?:(?:and|now|ok(?:ay)?|then|so)[,\s]+)*(?:what(?:'s|\s+is)\s+(?:that|it)\s+)?"
    rf"(?:(?P<op>plus|add|minus|subtract|take\s+away|times|multiplied\s+by|divided\s+by|over)\s+{_NUM}"
    rf"(?:\s+(?:to|from|by)\s+(?:that|it|the\s+(?:result|answer|total|number)))?"
    rf"|(?P<op2>add|subtract)\s+{_NUM.replace('num', 'num2')}\s+(?:to|from)\s+(?:that|it|the\s+(?:result|answer|total|number)))"
    rf"(?:\s+(?:please|pls))?\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_RESULT_NUMBER_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def _words_to_number(text: str) -> float | None:
    clean = str(text or "").strip().lower().replace("-", " ")
    try:
        return float(clean.replace(",", "."))
    except ValueError:
        pass
    total = current = 0
    seen = False
    for word in clean.split():
        if word in {"and"}:
            continue
        if word in {"a", "an"}:
            current = current or 1
            seen = True
        elif word in _NUMBER_WORDS:
            current += _NUMBER_WORDS[word]
            seen = True
        elif word in _SCALES:
            current = (current or 1) * _SCALES[word]
            if _SCALES[word] >= 1000:
                total, current = total + current, 0
            seen = True
        else:
            return None
    return float(total + current) if seen else None


def _fmt(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.4f}".rstrip("0").rstrip(".")


def previous_result_arithmetic(question: str, *, session_id: str) -> str | None:
    match = _OP_RE.match(" ".join(str(question or "").split()))
    if not match:
        return None
    op = (match.group("op") or match.group("op2") or "").lower()
    operand = _words_to_number(match.group("num") or match.group("num2") or "")
    if operand is None:
        return None
    reply = _previous_reply(session_id)
    if not reply:
        return None
    # The result is unambiguous only when the reply states one (after its last "=") or holds a
    # single number; a reply listing several numbers is left to the model.
    tail = reply.rsplit("=", 1)[1] if "=" in reply else reply
    numbers = _RESULT_NUMBER_RE.findall(tail)
    if not numbers or ("=" not in reply and len(numbers) != 1):
        return None
    base = float(numbers[0].replace(",", ""))
    if op in {"plus", "add"}:
        value, sign = base + operand, "+"
    elif op in {"minus", "subtract", "take away"} or op.startswith("take"):
        value, sign = base - operand, "−"
    elif op in {"times"} or op.startswith("multiplied"):
        value, sign = base * operand, "×"
    else:
        if operand == 0:
            return "That would divide by zero, which has no answer."
        value, sign = base / operand, "÷"
    return f"{_fmt(base)} {sign} {_fmt(operand)} = {_fmt(value)}"


__all__ += ["previous_result_arithmetic"]
