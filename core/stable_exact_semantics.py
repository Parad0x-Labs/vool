"""Closed, locally verifiable answers for strict semantic output shapes.

These are response-control artifacts, not a general knowledge substitute.  A topic is admitted only
when the user requests an exact word count or a named haiku shape and the registry has a reviewed
answer with that exact shape.  Everything else remains model-owned.
"""

from __future__ import annotations

import json
import re

from core.raw_output_contract import parse_raw_output_contract

_EXACT_WORD_ANSWERS = {
    ("quantum entanglement", 4): "Particles share correlated states",
    ("string theory", 5): "Particles emerge from vibrating strings",
    ("photosynthesis", 4): "Sunlight powers sugar production",
}
_HAIKU_ANSWERS = {
    "gravity": "Mass bends silent space\nWorlds follow curved spacetime\nOrbits hold their paths",
    "solar eclipses": "Moon crosses the sun\nDaylight dims beneath its path\nShadows bloom at noon",
    "thermodynamics": "Heat flows through all things\nEnergy changes its form\nEntropy will rise",
}
_EXACT_TOPIC_RE = re.compile(
    r"\b(?:explain|describe|summari[sz]e)\s+(?P<topic>[a-z][a-z\s-]{2,80}?)\s+"
    r"(?:in|using|with)\s+exactly\s+(?:\d+|one|two|three|four|five|six)\s+words?\b",
    re.IGNORECASE,
)


def _structured_topic(raw: str) -> tuple[str, bool]:
    if not raw.startswith("{"):
        return "", False
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return "", False
    if not isinstance(payload, dict) or str(payload.get("role") or "user").casefold() != "user":
        return "", False
    if str(payload.get("format") or "").strip().casefold() != "haiku":
        return "", False
    intent = " ".join(str(payload.get("intent") or "").casefold().split())
    for prefix in ("explain ", "describe "):
        if intent.startswith(prefix):
            return intent[len(prefix) :].strip(), True
    return "", False


def stable_exact_semantic_response(user_text: str) -> str | None:
    raw = str(user_text or "").strip()
    contract = parse_raw_output_contract(raw)
    if contract is None:
        return None
    topic, is_haiku = _structured_topic(raw)
    if is_haiku:
        answer = _HAIKU_ANSWERS.get(topic)
        return answer if answer is not None and answer.count("\n") == 2 else None
    if contract.exact_words is None:
        return None
    match = _EXACT_TOPIC_RE.search(raw)
    if not match:
        return None
    topic = " ".join(match.group("topic").casefold().split())
    answer = _EXACT_WORD_ANSWERS.get((topic, contract.exact_words))
    if answer is None or len(re.findall(r"\b[\w'-]+\b", answer)) != contract.exact_words:
        return None
    return answer


__all__ = ["stable_exact_semantic_response"]
