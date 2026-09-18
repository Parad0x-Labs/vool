"""Topic extraction and prose-intent detection for the live-info news fast path.

The news fast path used to feed the user's RAW message to both the web search and the response
title, so "ok, whats the latest on Iran newS?=" searched (and was titled with) that whole string,
and "i am looking for fresh latest news! summary" matched the filler words fresh/latest/news
instead of any topic. extract_news_topic strips the conversational/interrogative lead-in and the
news-request framing down to the actual subject ("Iran"), preserving its casing, and returns "" when
the message carries no real topic so the caller can defer instead of searching filler.

Stripping is conditional so it does not mangle a subject whose own words look like framing: a
framing word that is a load-bearing part of the subject (e.g. "Breaking" in "Breaking Bad", "The" in
"The Who", "Today" in "The Today Show") is kept when it is not adjacent to other framing words.
"""
from __future__ import annotations

import re

# Multi-word lead-ins stripped from the FRONT of the message (longest first, repeated until stable).
_LEAD_PHRASES = (
    "i am looking for", "i'm looking for", "im looking for", "i want to know", "i wanna know",
    "can you tell me", "could you tell me", "can you get me", "can you find", "could you find",
    "can you show me", "can you", "could you", "would you", "will you", "please give me",
    "give me", "tell me", "show me", "get me", "find me", "look up", "search for",
    "what's the latest on", "what is the latest on", "whats the latest on",
    "what's the latest with", "whats the latest with", "what's the latest news",
    "what is the latest news", "whats the latest news", "what's the news", "what is the news",
    "whats the news", "what's new on", "whats new on", "what's new with", "whats new with",
    "what's happening in", "whats happening in", "what is happening in", "what happened in",
    "what happened with", "what happened to", "the latest on", "the latest with",
    "latest news on", "latest news about", "breaking news on", "breaking news about",
    "latest on", "latest about", "news on", "news about", "headlines on", "headlines about",
    "update on", "updates on",
)
_SORTED_LEAD_PHRASES = tuple(sorted(_LEAD_PHRASES, key=len, reverse=True))

# Words always stripped from the front: conversational openers, interrogatives, connectors, and the
# strong news-intent words that are almost never a subject on their own.
_ALWAYS_LEAD = frozenset({
    # openers / interrogatives (verb openers like "give/tell/show me" are handled by the phrase list,
    # not as bare words, so they don't eat subject nouns like "Show" in "The Today Show")
    "ok", "okay", "so", "hey", "hi", "hello", "yo", "well", "um", "uh", "please", "pls", "plz",
    "what", "whats", "hows",
    # connectors ("and" joins two intent words: "news and trends on BTC")
    "on", "about", "for", "in", "with", "of", "from", "and",
    # strong intent (rarely a subject)
    "latest", "newest", "fresh", "freshest", "recently", "currently",
    "headlines", "headline", "update", "updates", "coverage",
})
# Words stripped from the front ONLY when the next token is also framing -- otherwise they start the
# subject: articles and the "soft" intent words that double as real subject words ("News Corp",
# "Breaking Bad", "The Today Show").
_COND_LEAD = frozenset({
    "the", "a", "an", "any", "some", "news", "newz",
    "breaking", "recent", "current", "today", "todays", "story", "stories", "report",
    "reports", "happening", "now",
    # framing words that pair with news ("news and trends on X", "analysis of Y") -- only stripped
    # from the front when the next token is also framing, so a real subject is never truncated.
    "trends", "trend", "analysis", "developments",
})
_FRAMING = _ALWAYS_LEAD | _COND_LEAD
# Words stripped from the END (trailing framing). Deliberately excludes summary/recap (handled by the
# trailing-ask check below) so a subject like "the Mueller summary" is not truncated.
_TRAILING = frozenset({
    "latest", "newest", "news", "newz", "fresh", "freshest", "recent", "recently", "current",
    "currently", "today", "todays", "now", "headlines", "headline", "update", "updates", "coverage",
    "breaking", "story", "stories", "report", "reports", "happening", "trends", "trend", "analysis",
})
_SUMMARY_TAIL = frozenset({"summary", "summaries", "recap", "recaps", "summarize", "summarise", "tldr"})


# A market/state DIRECTION question ("is oil going up or down and why?", "are rates rising?") is
# about the entity, not the direction words. Extract the subject so the search + title are "oil",
# not the whole clause. Non-greedy subject, capped to a short noun phrase so it never eats a clause.
_DIRECTION_Q_RE = re.compile(
    r"\b(?:is|are|was|were|will|would|has|have|does|do|can|could|should)\s+"
    r"(?:the\s+|a\s+|an\s+)?(?P<subj>.+?)\s+"
    r"(?:going|gonna|trending|heading|likely|expected|projected|set|about)?\s*"
    r"(?:to\s+)?(?:go\s+|move\s+|trend\s+|head\s+)?"
    r"(?:up|down|rise|rising|risen|fall|falling|fallen|higher|lower|"
    r"increas\w*|decreas\w*|surg\w*|drop\w*|climb\w*|plung\w*|rally\w*|crash\w*)\b",
    re.IGNORECASE,
)


def _direction_subject(text: str) -> str:
    match = _DIRECTION_Q_RE.search(str(text or ""))
    if not match:
        return ""
    subject = re.sub(r"^[^\w]+|[^\w]+$", "", match.group("subj").strip()).strip()
    # A subject is a short noun phrase, not a sub-clause; bail out (keep the normal extraction) if long.
    if not subject or len(subject.split()) > 4:
        return ""
    return subject


def _norm(token: str) -> str:
    """Lowercase a token, dropping surrounding punctuation and a trailing possessive/contraction."""
    return re.sub(r"['’]s?$", "", token.strip(",.:;!?\"'’")).lower()


def extract_news_topic(text: str) -> str:
    """Reduce a news request to its subject, casing preserved. Returns "" when there is no subject."""
    raw = " ".join(str(text or "").split())
    raw = re.sub(r"^[^\w]+|[^\w]+$", "", raw).strip()
    direction_subject = _direction_subject(raw)
    if direction_subject:
        return direction_subject
    lowered = raw.lower()
    changed = True
    while changed and raw:
        changed = False
        for phrase in _SORTED_LEAD_PHRASES:
            if lowered == phrase or lowered.startswith(phrase + " "):
                raw = re.sub(r"^[^\w]+", "", raw[len(phrase):]).strip()
                lowered = raw.lower()
                changed = True
                break
        if changed:
            continue
        tokens = raw.split()
        if not tokens:
            break
        head = _norm(tokens[0])
        strip_head = head in _ALWAYS_LEAD or (
            head in _COND_LEAD and len(tokens) > 1 and _norm(tokens[1]) in _FRAMING
        )
        if strip_head:
            raw = re.sub(r"^[^\w]+", "", " ".join(tokens[1:])).strip()
            lowered = raw.lower()
            changed = True
    # A compound message ("latest on BTC? trend up or down?") -- the subject is the first clause;
    # drop a trailing follow-up question after a sentence terminator so the topic is "BTC", not
    # "BTC? trend up or down". Only applied when the first clause still holds a subject.
    head = re.split(r"[?!.]", raw, maxsplit=1)[0].strip()
    if head:
        raw = head
    tokens = raw.split()
    # trailing framing words
    while tokens and _norm(tokens[-1]) in _TRAILING:
        tokens.pop()
    # a trailing "summary"/"recap" is a format ask (drop it) only when it is not part of the subject,
    # i.e. it is the only word left or follows a framing word ("news summary"), never "Mueller summary".
    if tokens and _norm(tokens[-1]) in _SUMMARY_TAIL and (len(tokens) == 1 or _norm(tokens[-2]) in _FRAMING):
        tokens.pop()
        while tokens and _norm(tokens[-1]) in _TRAILING:
            tokens.pop()
    return re.sub(r"^[^\w]+|[^\w]+$", "", " ".join(tokens)).strip()


# Unambiguous "give me prose, not a link list" asks.
_PROSE_ASK_RE = re.compile(
    r"\b(?:summari[sz]e|tl;?dr|sum it up|in a paragraph|in prose|not (?:just )?links|no links|"
    r"without links|give (?:me )?a (?:brief |quick )?summary|in summary|as a summary)\b",
    re.IGNORECASE,
)
# A trailing bare "summary"/"recap" that follows framing/punctuation (an ask), not a subject noun.
_TRAILING_SUMMARY_RE = re.compile(r"(\S+)\s+(summary|recap)\s*[!?.]*$", re.IGNORECASE)
_BARE_TRAILING_SUMMARY_RE = re.compile(r"^\s*(?:summary|recap)\s*[!?.]*$", re.IGNORECASE)


def wants_prose_summary(text: str) -> bool:
    """True only when the user explicitly asked for a prose summary rather than a link list.

    Guards against firing on a subject that merely contains the word ("news on the Mueller summary").
    """
    clean = str(text or "")
    if _PROSE_ASK_RE.search(clean):
        return True
    if _BARE_TRAILING_SUMMARY_RE.match(clean):
        return True
    match = _TRAILING_SUMMARY_RE.search(clean)
    return bool(match and _norm(match.group(1)) in _FRAMING)
