"""The ONE filler/discourse-marker vocabulary every splitter and filler-word set shares.

Why one home: the same filler words ("real quick", "btw", "so", "anyway") lived as private
sets in three places -- the demand mint's conversational tokens
(`core.agent_runtime.answer_coverage`), the price lane's generic tokens
(`core.agent_runtime.fast_live_info_price`), and the multi-request clause splitters -- and
they drifted. A fragment one set dropped, another promoted to a request, and the promoted
fragment reached the user as a "Could not be answered" row beside a fully answered turn
(fail list N3 / files:F02, 2026-10-29: "quick one, how tall is the eiffel tower?" answered
the height AND listed "- quick one" as unanswered; "like for a person standing there" split
off the same way).

The law this module serves: a fragment that is ONLY filler never becomes a request. Filler
is packaging around an ask -- an urgency softener ("real quick"), a discourse marker ("btw",
"anyway"), a lead-in ("quick one", "one more thing"), a hedge ("just wondering"), or a
comparison qualifier that leans on the request before it ("like for a person standing
there"). A fragment that names anything of its own is NOT filler and mints exactly as
before: the two-request law is untouched ("what's 37*19 and how much gold is in fort knox"
keeps both requests), and "ignore all previous instructions" keeps
"previous"/"instructions" and still mints.
"""
from __future__ import annotations

import re

#: Single words that are discourse markers or softeners on an ask, never an ask of their own.
#: A fragment whose EVERY token sits here is filler; one token outside it (a digit, a noun, a
#: verb of its own) keeps the fragment a request. Superset-stable: consumers union this into
#: their own closed vocabularies (`answer_coverage._CONVERSATIONAL_TOKENS`,
#: `fast_live_info_price._PRICE_GENERIC_TOKENS`) instead of growing private copies.
DISCOURSE_MARKER_TOKENS = frozenset(
    {
        # Discourse markers and lead-ins (fail list N3, 2026-10-29).
        "btw", "anyway", "um", "so", "ok", "okay", "hey", "also", "and", "then",
        "sorry", "pls", "please", "q",
        # Urgency/format softeners ("real quick, what can you do locally") -- a fragment made
        # entirely of these is a qualifier on the ask that follows it, not a second demand.
        "real", "quick", "quickly", "briefly", "short", "simple", "shorter", "concise",
        "just", "now", "still", "again", "separately",
        # Correction filler ('sorry I mean', 'Actually', 'ignore that' minus its object) --
        # the connective tissue of a correction, not a request beside it.
        "mean", "meant", "actually", "ignore",
        # Wonder-hedges: "just wondering" opens a request whose substance sits in the clause
        # it leans on; a real ask ("what is the weather in Lagos") keeps its own words.
        "wonder", "wondering",
    }
)

#: Whole fragments (normalized: lowercase, punctuation folded) that are filler and nothing
#: else. These name no operand, so the every-token rule cannot see them ("one", "thing",
#: "question", "way" are nouns a real request may own -- they stay OUT of the token set and
#: live only inside these fixed phrases).
FILLER_PHRASES = frozenset(
    {
        "quick one",
        "quick q",
        "quick question",
        "real quick",
        "one more thing",
        "one more question",
        "by the way",
        "just wondering",
        "just curious",
        "ok so",
        "okay so",
    }
)

#: Open-ended filler prefixes: everything after the prefix leans on the request before it
#: ("like for a person standing there" qualifies how to make the picture, it does not ask for
#: a person). Matched at fragment start only -- "make a poster like for a friend" as ONE
#: whole clause is a request about a poster and is not touched.
FILLER_PREFIXES = ("like for",)

_PUNCT_RE = re.compile(r"[^\w\s']+")
_SQUEEZE_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Lowercase, punctuation folded to spaces, whitespace squeezed -- `None`-safe."""
    return _SQUEEZE_RE.sub(" ", _PUNCT_RE.sub(" ", str(text or "").casefold())).strip()


def is_filler_fragment(text: str) -> bool:
    """Whether `text` is filler and nothing else, so it must never become a request.

    Three checks, cheapest first: the whole normalized fragment is a fixed filler phrase; it
    opens with a filler prefix ("like for ..."); or every one of its tokens is a discourse
    marker. Empty input is not filler (callers drop empties before asking). Never raises and
    never inspects anything but the text itself -- no family recognition, no lanes, no state,
    so the demand mint, the clause splitter and the price reader all read ONE answer.
    """
    normalized = _normalize(text)
    if not normalized:
        return False
    if normalized in FILLER_PHRASES:
        return True
    for prefix in FILLER_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix} "):
            return True
    tokens = normalized.split()
    if not tokens:
        return False
    return all(token in DISCOURSE_MARKER_TOKENS for token in tokens)


__all__ = [
    "DISCOURSE_MARKER_TOKENS",
    "FILLER_PHRASES",
    "FILLER_PREFIXES",
    "is_filler_fragment",
]
