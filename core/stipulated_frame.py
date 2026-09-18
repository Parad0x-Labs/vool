"""Authoritative boundary for hypothetical and stipulated user frames.

A stipulated future is input supplied by the user, not a claim about the live world.  Treating a
date such as 2035 or a title such as "President Buster" as a freshness signal sends fiction into
retrieval, where an empty index is then incorrectly presented as a reason not to answer.

This module owns the exception as well as the rule: external lookup is appropriate only when the
same request explicitly asks to compare the stipulated frame with real/current facts.
"""

from __future__ import annotations

import re
from typing import Any

from core.hypothetical_frame import detect_hypothetical_frame

_COMPARISON_RE = re.compile(
    r"\b(?:compare|contrast|check|verify|fact[-\s]?check|measure|benchmark)\b",
    re.IGNORECASE,
)

_REAL_CURRENT_FACTS_RE = re.compile(
    r"\b(?:real(?:[-\s]+world)?|reality|actual(?:ly)?|current|present[-\s]+day|today(?:'s)?|"
    r"as\s+of\s+(?:now|today)|live)\s+(?:facts?|reality|world|data|information|events?|situation|record)\b"
    r"|\b(?:what|who)\s+(?:is|are)\s+actually\s+(?:true|current|real)\b"
    r"|\bin\s+(?:the\s+)?real\s+world\b",
    re.IGNORECASE,
)

_FRAME_EXIT_RE = re.compile(
    r"\b(?:end|drop|leave|stop|exit)\s+(?:the\s+|this\s+)?"
    r"(?:scenario|hypothetical|fiction|roleplay|timeline)\b"
    r"|\bback\s+to\s+(?:reality|the\s+real\s+world|real\s+life)\b"
    # Retracting the STIPULATION itself, not the scenario wrapper. Needed once the frame can reach
    # back more than one turn: "forget the assumptions, how long does our build actually take"
    # shares its subject with the stipulation, so without this the retraction would REACTIVATE the
    # very frame it is cancelling. Measured while adding that reach-back; before it, the walk
    # stopped early and got this right by accident.
    r"|\b(?:forget|drop|ignore|discard|scrap|clear)\s+(?:the\s+|those\s+|these\s+|my\s+|our\s+)?"
    r"(?:assumption|assumptions|stipulation|stipulations|premise|premises|numbers|figures)\b",
    re.IGNORECASE,
)

_EXPLICIT_LOOKUP_ACTION_RE = re.compile(
    r"\b(?:look\s+up|browse|google|fetch|retrieve)\b"
    r"|\b(?:search|check)\s+(?:online\b|(?:the\s+)?(?:web|internet)\b|for\b|"
    r"(?:the\s+)?(?:latest|newest|current|recent)\b)",
    re.IGNORECASE,
)

_FRESH_REAL_TARGET_RE = re.compile(
    r"\b(?:latest|newest|most\s+recent|current|currently|today(?:'s)?|right\s+now|live)\b",
    re.IGNORECASE,
)

_SEARCH_SYSTEM_RE = re.compile(
    r"\b(?:web|internet|online)\s+(?:search|lookup)\b"
    r"|\bsearch\s+(?:engine|engines|tools?)\b"
    r"|\b(?:search|lookup|browse|google)\b",
    re.IGNORECASE,
)

_WHY_EXPLANATION_RE = re.compile(
    r"\b(?:why|reason|explain|clarify|describe)\b",
    re.IGNORECASE,
)

_CANNOT_VERIFY_RE = re.compile(
    r"\b(?:can(?:not|'t)|could(?:not|n't)|would(?:not|n't)|will\s+not|won't|"
    r"fail(?:s|ed|ing)?\s+to|unable\s+to)\b.{0,80}"
    r"\b(?:verif(?:y|ied)|confirm(?:ed)?|establish(?:ed)?|prov(?:e|en)|validat(?:e|ed)|find)\b",
    re.IGNORECASE,
)

_FRAME_REFERENCE_RE = re.compile(
    r"\b(?:this|that|these|those)\s+(?:premise|assumption|scenario|story|timeline|world|"
    r"future|stipulation|fact|claim)\b"
    r"|\b(?:the\s+)?(?:premise|scenario|stipulation)\s+(?:above|I\s+gave|we\s+set)\b"
    r"|\b(?:look\s+up|search|check|verify|confirm|find)\s+(?:for\s+)?"
    r"(?:it|that|this|them|those)\b"
    r"|\b(?:about|inside|within)\s+(?:it|that|this|them|those)\b"
    r"|\bwhat\s+happens?\s+(?:there|then|next)\b",
    re.IGNORECASE,
)

_STRONG_FICTION_RE = re.compile(
    r"\b(?:fiction(?:al|ally)?|fictitious|hypothetical(?:ly)?|counterfactual|stipulated|"
    r"make[-\s]believe|imaginary)\b"
    r"|\balternat(?:e|ive)\s+(?:universe|reality|timeline|history)\b"
    r"|\b(?:in|under|for)\s+(?:this|that|the|a)\s+"
    r"(?:imagined|hypothetical|fictional|stipulated|future|story|timeline|world)\b"
    r"|\b(?:scenario|story|timeline|world)\s+(?:where|in\s+which)\b"
    r"|\bpretend(?:ing)?\b",
    re.IGNORECASE,
)

_PERSONAL_ACTION_SETUP_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|so|now|please|let'?s)\b[\s,]*)*"
    r"(?:assum(?:e|ing)|imagin(?:e|ing)|suppos(?:e|ing))\s+(?:that\s+)?(?:i|we)\s+"
    r"(?!(?:am|are|was|were|'m|'re)\b)",
    re.IGNORECASE,
)

_FIRST_PERSON_LOCATION_SETUP_RE = re.compile(
    r"^\s*(?:(?:ok(?:ay)?|so|now|please|let'?s)\b[\s,]*)*"
    r"(?:assum(?:e|ing)|imagin(?:e|ing)|suppos(?:e|ing))\s+(?:that\s+)?"
    r"(?:i\s+am|i'm|we\s+are|we're)\s+(?:in|at|near|outside|visiting)\b",
    re.IGNORECASE,
)

#: How far back a stipulation may reach when the current turn explicitly RETURNS to it. Bounded so
#: a frame cannot govern a whole session; see stipulated_frame_active for why the immediate
#: predecessor is exempt from the return requirement.
_FRAME_LOOKBACK_USER_TURNS = 6

#: An explicit return to an earlier subject. Subject overlap alone is NOT enough and was tried
#: first: after "Assume it is 2040 and Buster is president" and an intervening haiku request, the
#: question "Who is the current president?" overlaps on "president" and would have been answered
#: from the fiction. That is precisely what the one-turn bound existed to prevent, and
#: tests/test_stipulated_frame_contract.py caught it.
#:
#: A genuine new question shares a subject without ever saying it is coming back to it. Requiring
#: the user to SAY SO is what separates "back to the ci math" from "who is the current president".
_FRAME_RETURN_RE = re.compile(
    r"\b(?:back\s+to|returning\s+to|return\s+to|going\s+back\s+to|as\s+for)\b"
    r"|\b(?:again|earlier|before|previous(?:ly)?)\b",
    re.IGNORECASE,
)

_CLAUSE_BOUNDARY_RE = re.compile(r"[,.!?;:\n]")
_WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_+.#/-]*")
_SCOPE_STOPWORDS = {
    "a", "about", "after", "and", "are", "as", "assume", "assuming", "at", "be", "based",
    "before", "browse", "but", "by", "check", "current", "currently", "do", "find", "for",
    "from", "google", "how", "i", "if", "imagine", "imagining", "in", "internet", "is", "it",
    "latest", "live", "look", "lookup", "me", "most", "my", "newest", "of", "on", "online",
    "or", "please", "recent", "retrieve", "search", "suppose", "supposing", "tell", "than", "that",
    "the", "then", "this", "to", "today", "under", "up", "us", "was", "we", "web", "what",
    "when", "where", "which", "who", "why", "will", "with", "would", "you",
}


def _explanation_only_search_mismatch(text: str) -> bool:
    """Whether search is mentioned only as something unable to prove a supplied premise."""

    return bool(
        _WHY_EXPLANATION_RE.search(text)
        and _SEARCH_SYSTEM_RE.search(text)
        and _CANNOT_VERIFY_RE.search(text)
    )


def _content_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw_token in _WORD_RE.findall(text):
        token = raw_token.lower().strip("._+/-")
        if token.endswith("ies") and len(token) > 4:
            token = f"{token[:-3]}y"
        elif token.endswith("s") and not token.endswith("ss") and len(token) > 4:
            token = token[:-1]
        if len(token) > 2 and token not in _SCOPE_STOPWORDS:
            terms.add(token)
    return terms


def _lookup_scope(text: str) -> tuple[str, str] | None:
    """Split the stipulated setup from a later, explicitly fresh lookup clause."""

    action = _EXPLICIT_LOOKUP_ACTION_RE.search(text)
    freshness_matches = list(_FRESH_REAL_TARGET_RE.finditer(text))
    if not freshness_matches:
        return None
    if action is not None:
        if not any(match.start() >= action.start() for match in freshness_matches):
            return None
        intent_start = action.start()
        boundary_end = max(
            (match.end() for match in _CLAUSE_BOUNDARY_RE.finditer(text, 0, intent_start)),
            default=0,
        )
    else:
        candidates: list[tuple[int, int]] = []
        for freshness in freshness_matches:
            boundary_end = max(
                (
                    match.end()
                    for match in _CLAUSE_BOUNDARY_RE.finditer(text, 0, freshness.start())
                ),
                default=0,
            )
            if boundary_end:
                candidates.append((freshness.start(), boundary_end))
        if not candidates:
            return None
        intent_start, boundary_end = candidates[0]
    return text[:boundary_end or intent_start], text[boundary_end or intent_start:]


def _fresh_lookup_targets_real_world(text: str) -> bool:
    """True only when a fresh lookup clause targets facts independent of the stipulation."""

    scoped = _lookup_scope(text)
    if scoped is None:
        return False
    premise, lookup = scoped
    if not _content_terms(lookup) or _FRAME_REFERENCE_RE.search(lookup):
        return False

    overlap = _content_terms(premise) & _content_terms(lookup)
    if not overlap:
        return True
    if _STRONG_FICTION_RE.search(premise):
        return False
    return bool(
        _PERSONAL_ACTION_SETUP_RE.search(premise)
        or _FIRST_PERSON_LOCATION_SETUP_RE.search(premise)
    )


def has_stipulated_frame(text: str) -> bool:
    """Return whether the user explicitly established a fictional/counterfactual frame."""

    return detect_hypothetical_frame(text).supplies_premises


def explicitly_compares_frame_to_reality(text: str) -> bool:
    """Return whether the request asks to compare its frame with real/current facts."""

    clean = " ".join(str(text or "").split())
    return bool(_COMPARISON_RE.search(clean) and _REAL_CURRENT_FACTS_RE.search(clean))


def stipulated_frame_forbids_retrieval(text: str) -> bool:
    """True when the request targets its stipulated frame rather than independent live facts."""

    clean = " ".join(str(text or "").split())
    frame = detect_hypothetical_frame(clean)
    if not frame.supplies_premises:
        return False
    if frame.search_refused or frame.frame_only or _explanation_only_search_mismatch(clean):
        return True
    if explicitly_compares_frame_to_reality(clean):
        return False
    return not _fresh_lookup_targets_real_world(clean)


def stipulated_frame_active(
    text: str,
    *,
    source_context: dict[str, Any] | None = None,
) -> bool:
    """Return whether this turn or its immediately preceding user turn owns a stipulated frame.

    Follow-ups are the sharp edge: the current text may only say "Who am I meeting?", while the
    authoritative Buster stipulation lives in the prior user message.  We intentionally inspect
    only the most recent prior user message, preventing an old fictional exercise from disabling
    retrieval for an unrelated conversation later in the same session.
    """

    if _FRAME_EXIT_RE.search(str(text or "")):
        return False
    if stipulated_frame_forbids_retrieval(text):
        return True
    context = dict(source_context or {})
    history = list(
        context.get("client_conversation_history")
        or context.get("conversation_history")
        or []
    )
    current_terms = _content_terms(str(text or ""))
    # Both halves are required past the immediate predecessor: the user has to SAY they are
    # coming back, and the turn has to be about the stipulated subject.
    returns_to_a_subject = bool(_FRAME_RETURN_RE.search(str(text or "")))
    seen_user_turns = 0
    for item in reversed(history):
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or item.get("speaker") or "").strip().lower()
        content = str(item.get("content") or item.get("text") or "").strip()
        if role != "user" or not content:
            continue
        # Augmented histories can include the current user message. Skip it before examining the
        # immediately preceding user turn.
        if " ".join(content.split()) == " ".join(str(text or "").split()):
            continue

        seen_user_turns += 1
        if seen_user_turns == 1:
            # The immediately preceding user turn decides outright -- unchanged behaviour, and the
            # case the original docstring was written for ("Who am I meeting?" after the setup).
            if stipulated_frame_forbids_retrieval(content):
                return True
            continue

        # Beyond the immediate predecessor, a stipulation reaches this turn only when the user
        # explicitly returns to it AND this turn is about it. Measured: T1 stipulated a CI build time, T2 asked an unrelated pre-commit
        # question, and T3 -- "ok back to the ci math, what if we cut runs to 25 a day" -- lost the
        # frame entirely, because the walk stopped at T2 and returned. The user said plainly they
        # were returning to it.
        #
        # Subject overlap is what keeps the original guard intact. Its stated purpose is stopping
        # "an old fictional exercise from disabling retrieval for an unrelated conversation later in
        # the same session" -- an unrelated conversation shares no content terms, so it still gets
        # nothing. Only a turn that names the stipulated subject can reach back past one turn.
        if seen_user_turns > _FRAME_LOOKBACK_USER_TURNS:
            break
        if not returns_to_a_subject:
            break
        if not stipulated_frame_forbids_retrieval(content):
            continue
        if current_terms & _content_terms(content):
            return True
    return False


STIPULATED_FRAME_PROMPT_GUIDANCE = (
    "User-stipulated assumptions, hypothetical or fictional facts, and future rules are "
    "authoritative inside their stated frame. Answer within that frame without searching, "
    "fact-checking, or replacing named people, roles, places, dates, or rules with plausible "
    "real-world substitutes. Keep those stipulated facts active in follow-up answers. Infer "
    "unstated details from the scenario's stated setting and label them as likely when needed. "
    "Only use live/current evidence when the user explicitly asks to compare the frame with "
    "reality or makes a separate fresh lookup request about independent real-world data. If asked "
    "why web search cannot establish stipulated future fiction, explain that it is not a current "
    "indexed fact; do not blame Local Only unless the runtime actually reports that internet "
    "access was disabled."
)
