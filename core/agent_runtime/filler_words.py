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

#: Words that close a message politely: one list, kept in core.closing_words (imports nothing, so the time
#: parser can read it without the agent runtime).

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


_LONE_EXCLAMATION_RE = re.compile(r"(?:^|(?<=[\s,.;:!?]))[¡]?[^\W\d_]+[!¡]+(?=\s|$)", re.UNICODE)


def _without_lone_exclamations(text: str) -> str:
    """``text`` with every single word that stands alone before "!" taken out (by shape, any script)."""
    return _LONE_EXCLAMATION_RE.sub(" ", text).strip(" ,.;:")


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
    exclaimed = _without_lone_exclamations(str(text or ""))
    if exclaimed != str(text or ""):
        # A one-word exclamation ("morning!", "labas!", "¡hola!") is a greeting or an interjection in any language,
        # never a request: the fresh-question audit on 02ff11496 listed "morning! quick one" under "Could not be
        # answered" beside a fully answered question. What is left after it is read on its own.
        return not _normalize(exclaimed) or is_filler_fragment(exclaimed)
    if normalized in FILLER_PHRASES:
        return True
    for prefix in FILLER_PREFIXES:
        if normalized == prefix or normalized.startswith(f"{prefix} "):
            return True
    tokens = normalized.split()
    if not tokens:
        return False
    return all(token in DISCOURSE_MARKER_TOKENS for token in tokens)


#: Words that introduce HOW an ask is to be done: the instrument or source it names ("use the
#: calculator", "using my notes", "with web search", "via the terminal"). The same role in the
#: other chat languages VOOL serves (German, Lithuanian, French, Spanish, Italian, Portuguese).
METHOD_HEADS = frozenset(
    {
        "use", "using", "with", "via", "through", "thru",
        "benutze", "benutz", "nutze", "nutz", "verwende", "mit", "per",
        "naudok", "naudokite", "naudojant", "naudodamas", "naudodama", "su",
        "utilise", "utilisez", "avec", "usa", "usando", "con", "utiliza", "com",
    }
)
#: Words that may sit between a method head and the instrument ("the", "my", "your", "dem", "mano").
_METHOD_DETERMINERS = frozenset(
    {
        "the", "a", "an", "my", "your", "ur", "our", "its", "this", "that",
        "dem", "den", "der", "die", "das", "meinem", "meinen", "meiner", "deinem", "deinen",
        "mano", "savo", "tavo", "le", "la", "les", "mon", "ma", "mes", "el", "los", "las", "mi", "mis",
        "il", "lo", "o", "os", "meu", "minha",
    }
)
#: Words that would make the fragment more than a named instrument: a purpose or a second object.
_METHOD_BREAKERS = frozenset(
    {"to", "for", "and", "or", "from", "about", "on", "in", "into", "that", "which", "who", "if", "then"}
)


def is_method_clause(text: str) -> bool:
    """Whether ``text`` only says HOW the ask beside it is to be done, so it is no ask of its own.

    "what's 4827 times 63, use the calculator please" asks ONE thing; "use the calculator" is the
    method of that ask, the way "with web search" or "using my notes" is. Split off as its own
    request it reached the reader as "Could not be answered: use the calculator please" beside a
    correct product (real-app gate on the v0.8 line, Nov 24). Read by shape: a method head, an
    optional determiner, then one to three words that name the instrument, with no number and no
    word that opens a purpose or a second object ("use the calculator to check the bill" and
    "use 3 eggs" stay requests). Closing courtesy words at the end are packaging. Never raises.
    """
    from core.closing_words import CLOSING_COURTESY_WORDS

    tokens = _normalize(text).split()
    courtesy = {word for word in CLOSING_COURTESY_WORDS if " " not in word} | DISCOURSE_MARKER_TOKENS
    while tokens and tokens[-1] in courtesy:
        tokens.pop()
    while tokens and tokens[0] in DISCOURSE_MARKER_TOKENS and tokens[0] not in METHOD_HEADS:
        tokens.pop(0)
    if len(tokens) < 2 or tokens[0] not in METHOD_HEADS:
        return False
    rest = tokens[1:]
    if rest and rest[0] in _METHOD_DETERMINERS:
        rest = rest[1:]
    if not 1 <= len(rest) <= 3:
        return False
    return not any(any(ch.isdigit() for ch in token) or token in _METHOD_BREAKERS for token in rest)


def without_method_clauses(text: str) -> str:
    """``text`` with every trailing method clause taken out ("4827 times 63, use the calculator please" ->
    "4827 times 63, "; "what's 19 x 7 using my calculator" -> "what's 19 x 7 "). A recognizer that reads the rest of
    a message for a second ask reads this instead, so the way the person wants it done is never taken for one."""
    value = str(text or "")
    out: list[str] = []
    for piece in re.split(r"([,;.!?]+)", value):
        if not piece or re.fullmatch(r"[,;.!?]+", piece):
            out.append(piece)
            continue
        cut = len(piece)
        for match in re.finditer(r"[^\W\d_]+", piece):
            if match.group(0).casefold() in METHOD_HEADS and is_method_clause(piece[match.start():]):
                cut = match.start()
                break
        out.append(piece[:cut])
    return "".join(out)


__all__ = [
    "DISCOURSE_MARKER_TOKENS",
    "FILLER_PHRASES",
    "FILLER_PREFIXES",
    "METHOD_HEADS",
    "is_filler_fragment",
    "is_method_clause",
    "without_method_clauses",
]
