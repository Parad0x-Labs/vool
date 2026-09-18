"""Context understanding rules for short or fragmented user input.

When the user sends an unfinished, vague, or fragmented prompt, VOOL optimizes
it for her own understanding using ONLY:
- The message itself (keywords, phrases)
- Session context (recent topics, reference targets)
- Domain vocabulary and phrase hints

ANTI-HALLUCINATION: Never add requirements, features, or details the user
did not imply. Only expand, connect, and clarify what is already present.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.structured_literal_input import looks_like_structured_literal_input

_WORD_RE = re.compile(r"[a-z0-9_'\-]+")

# Phrase expansions: (pattern_tokens, expansion) - only expand when ALL tokens present
_PHRASE_EXPANSIONS = [
    (("discord", "bot"), "Discord bot"),
    (("tg", "bot"), "Telegram bot"),
    (("telegram", "bot"), "Telegram bot"),
    (("secure", "chat"), "secure the chat"),
    (("ban", "giveaway"), "ban users posting giveaways"),
    (("ban", "airdrop"), "ban airdrop promotions"),
    (("ban", "promo"), "ban promotional posts"),
    (("self", "learn"), "self-learning"),
    (("hive", "task"), "Hive task"),
    (("hive", "mind"), "Hive mind"),
    (("vool", "tool"), "VOOL tooling"),
    (("tool", "pack"), "tooling pack"),
    (("code", "assist"), "coding assistant"),
    (("social", "assist"), "social assistant"),
    (("learn", "create"), "learn and create"),
    (("find", "utilise"), "find and utilise"),
    (("find", "utilize"), "find and utilise"),
    (("knowledge", "internet"), "knowledge available on the internet"),
]

# Connectors to infer between adjacent concepts
_CONNECTOR_RULES = [
    (("bot", "secure"), " with "),
    (("bot", "ban"), " that "),
    (("chat", "ban"), ", ban "),
    (("assist", "tool"), " using "),
]

# Domain terms that should be capitalized or normalized
_DOMAIN_NORMALIZE = {
    "discord": "Discord",
    "telegram": "Telegram",
    "tg": "Telegram",
    "vool": "VOOL",
    "hive": "Hive",
    "git": "Git",
    "github": "GitHub",
}


@dataclass
class WorkingInterpretation:
    """Grounded expansion of short/fragmented input for VOOL's internal use."""

    raw: str
    expanded: str
    explicit_keywords: list[str] = field(default_factory=list)
    context_topics: list[str] = field(default_factory=list)
    grounding_note: str = ""


def _extract_keywords(text: str) -> list[str]:
    tokens = [t for t in _WORD_RE.findall((text or "").lower()) if len(t) > 1]
    return list(dict.fromkeys(tokens))[:16]


def _expand_phrases(text: str) -> str:
    lower = f" {(text or '').lower()} "
    out = text
    for tokens, expansion in _PHRASE_EXPANSIONS:
        if all(f" {t} " in lower for t in tokens) and expansion.lower() not in out.lower():
            phrase = " ".join(tokens)
            out = re.sub(rf"\b{re.escape(phrase)}\b", expansion, out, flags=re.IGNORECASE)
    return " ".join(out.split())


def _apply_connectors(text: str) -> str:
    lower = (text or "").lower()
    for (a, b), _connector in _CONNECTOR_RULES:
        if a in lower and b in lower:
            pass
    return text


def _normalize_domain_terms(text: str) -> str:
    words = text.split()
    out = []
    for w in words:
        low = w.lower()
        if low in _DOMAIN_NORMALIZE:
            out.append(_DOMAIN_NORMALIZE[low])
        else:
            out.append(w)
    return " ".join(out)


def _merge_session_topics(
    recent_turns: list[dict[str, Any]],
    current_topics: list[str],
    reference_targets: list[str],
) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for t in reference_targets + current_topics:
        clean = str(t or "").strip().lower()
        if clean and clean not in seen:
            seen.add(clean)
            merged.append(t)
    for turn in (recent_turns or [])[:4]:
        raw = str(turn.get("raw_input") or turn.get("reconstructed_input") or "")
        for token in _extract_keywords(raw):
            if len(token) > 3 and token not in seen:
                seen.add(token)
                merged.append(token)
    return merged[:8]


# A turn that introduces its OWN subject — "keep this label: onboarding-context", "remember term:
# local-proof-path", "remember codeword: safe-chat-lane" — must NOT have a stale reference subject
# glued on. Doing so let a prior "lease" reference corrupt a label save ("Context subject: lease"),
# which the model then expanded into an invented apartment-lease record and carried into the summary.
# Pronoun follow-ups ("if that one dies can the other still have it?") do NOT match, so their
# legitimate reference resolution is preserved.
_META_NOUN = r"label|phrase|term|word|codeword|code\s*word|marker|note|tag|name|context|subject|codename|id"
_DECLARES_OWN_SUBJECT_RE = re.compile(
    # 1) memory verb + (determiner) + meta-noun + colon/equals: "keep this label: X", "remember term: X"
    rf"\b(?:keep|remember|save|store|note|record|set)\s+(?:this|the|a|my|our)?\s*(?:{_META_NOUN})\s*[:=]"
    # 2) naming imperative pointing at the current turn: "call it X", "name this X", "label that X".
    # "the" is excluded — it is a definite article in ordinary commands ("call the client first").
    r"|\b(?:call|name|dub|title|label|tag)\s+(?:it|this|that)\b"
    # 3) bare meta-noun label form: "codeword: X", "marker = X"
    rf"|\b(?:{_META_NOUN})\s*[:=]\s*\S+"
    # 4) "remember this: X" / "remember: X" — a value with no meta-noun (tight window avoids prose)
    r"|\b(?:remember|keep|note|record)\b[^:\n]{0,15}?:\s*\S+",
    re.IGNORECASE,
)


def turn_declares_own_subject(text: str) -> bool:
    """True when the turn declares its own subject (a label/term/codeword/marker/name to remember),
    so a stale session reference subject must not be appended to it. A plain pronoun follow-up
    ("use it for the same context") is NOT a declaration — its reference resolution is preserved."""
    return bool(_DECLARES_OWN_SUBJECT_RE.search(str(text or "")))


def expand_unfinished_for_self(
    raw_input: str,
    *,
    normalized_text: str,
    topic_hints: list[str],
    reference_targets: list[str],
    recent_turns: list[dict[str, Any]],
    quality_flags: list[str],
) -> WorkingInterpretation:
    """Expand short/fragmented input for VOOL's internal understanding.

    Uses ONLY: message content, session context, domain vocabulary.
    Never invents requirements or features.
    """
    text = (normalized_text or raw_input or "").strip()
    if not text:
        return WorkingInterpretation(raw=raw_input or "", expanded=text, grounding_note="Empty input.")
    if looks_like_structured_literal_input(text):
        return WorkingInterpretation(
            raw=raw_input or "",
            expanded=text,
            explicit_keywords=_extract_keywords(text),
            context_topics=list(topic_hints or [])[:6],
            grounding_note="",
        )

    is_short = "short_input" in quality_flags or len(text.split()) <= 5
    is_fragmented = "fragmented" in quality_flags or not re.search(r"[.?!]$", text)

    if not (is_short or is_fragmented):
        return WorkingInterpretation(
            raw=raw_input or "",
            expanded=text,
            explicit_keywords=_extract_keywords(text),
            context_topics=list(topic_hints or [])[:6],
            grounding_note="",
        )

    keywords = _extract_keywords(text)
    context_topics = _merge_session_topics(recent_turns, list(topic_hints or []), list(reference_targets or []))

    expanded = _expand_phrases(text)
    expanded = _normalize_domain_terms(expanded)
    expanded = " ".join(expanded.split())

    grounding_note = (
        "Input appears short or fragmented. Working interpretation uses only message keywords and session context. "
        "Do not add requirements the user did not mention."
    )

    return WorkingInterpretation(
        raw=raw_input or "",
        expanded=expanded.strip(),
        explicit_keywords=keywords,
        context_topics=context_topics,
        grounding_note=grounding_note,
    )
