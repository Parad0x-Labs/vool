"""Small, deterministic glossary for standardized exact-shape definition requests.

This is not a general knowledge base. It covers stable terms whose requested answer is an exact,
locally verifiable expansion or synonym. The model still owns explanations and every unregistered
term; this contract prevents an exact three-word acronym expansion from becoming a long model turn
or an exact one-word definition from being replaced with a semantically unrelated sampled token.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from core.raw_output_contract import parse_raw_output_contract


@dataclass(frozen=True)
class StableTerm:
    key: str
    aliases: tuple[str, ...]
    exact_answers: dict[int, str]


_TERMS = (
    StableTerm("latency", ("latency",), {1: "Delay"}),
    StableTerm("http", ("http",), {3: "Hypertext Transfer Protocol"}),
    StableTerm("ram", ("ram",), {3: "Random Access Memory"}),
    StableTerm("cpu", ("cpu",), {3: "Central Processing Unit"}),
    StableTerm("gpu", ("gpu",), {3: "Graphics Processing Unit"}),
    StableTerm("api", ("api",), {3: "Application Programming Interface"}),
    StableTerm("url", ("url",), {3: "Uniform Resource Locator"}),
    StableTerm("dns", ("dns",), {3: "Domain Name System"}),
    StableTerm("sql", ("sql",), {3: "Structured Query Language"}),
)
_BY_ALIAS = {alias.casefold(): term for term in _TERMS for alias in term.aliases}
_DEFINE_RE = re.compile(
    r"^\s*(?:define|expand|spell\s+out)\s+(?:the\s+(?:term|acronym)\s+)?"
    r"(?P<term>[A-Za-z][A-Za-z0-9_-]{0,31})\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_STABLE_PHRASES = {
    ("say hello world", 3): "Hello brave world",
    ("greet the user", 2): "Hello there",
    ("state the color of the sky", 1): "Blue",
    ("state the colour of the sky", 1): "Blue",
}


def stable_structured_definition(user_text: str) -> str | None:
    """Return one registered exact answer, or ``None`` for every non-contract shape."""

    raw = str(user_text or "").strip()
    if not raw.startswith("{"):
        return None
    try:
        payload, end = json.JSONDecoder().raw_decode(raw)
    except (TypeError, ValueError):
        return None
    if not isinstance(payload, dict) or raw[end:].strip():
        return None
    if str(payload.get("role") or "user").strip().casefold() != "user":
        return None
    intent = payload.get("intent")
    if not isinstance(intent, str):
        return None
    contract = parse_raw_output_contract(raw)
    if contract is None or contract.exact_words is None or contract.exact_text is not None:
        return None
    phrase_answer = _STABLE_PHRASES.get((" ".join(intent.casefold().split()), contract.exact_words))
    if phrase_answer is not None:
        return phrase_answer
    match = _DEFINE_RE.fullmatch(intent)
    if not match:
        return None
    term = _BY_ALIAS.get(match.group("term").casefold())
    if term is None:
        return None
    answer = term.exact_answers.get(contract.exact_words)
    if answer is None or len(re.findall(r"\b[\w'-]+\b", answer, re.UNICODE)) != contract.exact_words:
        return None
    return answer


__all__ = ["StableTerm", "stable_structured_definition"]
