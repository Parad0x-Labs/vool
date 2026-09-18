"""Narrow gate: skip curiosity roaming (a live web search) before answering a high-confidence
local/memory/exact-recall/personal question. Scope is deliberately tight — it only decides
whether to skip curiosity for THIS turn; it does not change curiosity itself, research, or the
model. Explicit "search/latest/public" requests, and genuinely-external questions, still roam.
"""
from __future__ import annotations

import re

from core.personal_knowledge import is_personal_knowledge_question
from core.stipulated_frame import stipulated_frame_forbids_retrieval

# Explicit request for external / public / live info — never skip curiosity for these.
_EXPLICIT_RESEARCH_RE = re.compile(
    r"\b(search\b|google\b|bing\b|duckduckgo\b|browse\b|look (?:it |them |that )?up\b|look online\b|"
    r"on the (?:web|internet)\b|\bonline\b|latest\b|newest\b|most recent\b|recent(?:ly)?\b|today\b|"
    r"this (?:week|month|year)\b|up[\s-]?to[\s-]?date\b|\bnews\b|"
    r"current (?:price|news|events|version|status|weather|rate|score))\b",
    re.IGNORECASE,
)
# Explicit "answer from your own knowledge / do not look it up" — answer locally, skip curiosity.
_NO_RESEARCH_RE = re.compile(
    r"\b(from your own knowledge|from memory\b|do not cite|don'?t cite|no sources\b|no web\b|"
    r"without (?:searching|looking|research|the web|external)|don'?t look it up|"
    r"off the top of your head|without external)\b",
    re.IGNORECASE,
)
# A recall about the user's own / current / remembered state that local memory or active mission
# answers ("what is my launch cap?", "what is the current mission?", "what did I say?").
_LOCAL_RECALL_RE = re.compile(
    r"\b(what(?:'s| is| are| was| were| did)?\s+(?:my|our|the current|the remembered|the active|i )|"
    r"current mission\b|active mission\b|remembered mission\b|active constraints\b|"
    r"what did i (?:say|tell)\b|what do you remember\b|recall (?:the|my)\b|"
    r"my (?:launch cap|spend cap|cap\b|wallet|budget|mission|settings|config|preference|deadline))",
    re.IGNORECASE,
)


# A correction / update / latest-instruction recall that local memory answers ("update: cap is now
# X", "the cap is not Y anymore, it is Z", "answer only with the current cap"). Deliberately does
# NOT include a bare "actually"/"correction" alternative: those over-match an external request such
# as "actually, tell me the current SOL price" that the wants_external_research gap would not catch.
_CORRECTION_RECALL_RE = re.compile(
    r"\bupdate:|\bupdated:|"
    r"\bthe (?:cap|value|answer|number|price|domain|setting|mission|budget) is now\b|"
    r"\bis not .{1,40}? (?:anymore|any more|now)\b|"
    r"\bnot .{1,40}? it(?:'s| is)\b|"
    r"\banswer only with the current\b|"
    r"\bwhat is the current (?:cap|mission|value|setting|domain|number|constraint|budget)\b",
    re.IGNORECASE,
)
# A "answer in <style> style: <question>" directive — the task is answerable from the model's own
# knowledge, so it should not roam the web. Applied only AFTER the explicit-research gate, so
# "answer in Telegram style: what is the latest Solana news?" still researches.
_STYLE_TASK_RE = re.compile(r"\banswer in [\w\s-]{0,40}?style\b", re.IGNORECASE)


def wants_external_research(text: str) -> bool:
    return bool(_EXPLICIT_RESEARCH_RE.search(" " + " ".join(str(text or "").lower().split()) + " "))


def wants_no_research(text: str) -> bool:
    return bool(_NO_RESEARCH_RE.search(str(text or "")))


def looks_like_local_recall(text: str) -> bool:
    return bool(_LOCAL_RECALL_RE.search(" " + " ".join(str(text or "").lower().split()) + " "))


def looks_like_correction(text: str) -> bool:
    return bool(_CORRECTION_RECALL_RE.search(" " + " ".join(str(text or "").lower().split()) + " "))


def looks_like_style_task(text: str) -> bool:
    return bool(_STYLE_TASK_RE.search(" " + " ".join(str(text or "").lower().split()) + " "))


def should_skip_curiosity_for_local_answer(
    text: str,
    *,
    active_mission_present: bool = False,
    local_memory_hit: bool = False,
    current_information_required: bool = False,
) -> bool:
    """True when curiosity roaming should be skipped because the answer is high-confidence local.

    Skip when: an explicit "answer from your own knowledge" request; a personal/private (family/
    PII) question; or a "what is my/current/remembered…" recall AND active mission or local memory
    holds the value. Never skip when the user explicitly asks to search/for latest/public info.

    `current_information_required` is the canonical authority's reading of the TURN, and it
    overrides the last arm only. Measured on a served daemon: "what is the current state of X
    right now" reads as a local recall, so once anything about X sat in memory the turn skipped
    retrieval entirely -- no search, no receipt, and no refusal row either -- and the model
    answered a live question from its weights. Memory can make retrieval unnecessary for a
    TIMELESS recall; it cannot make an observation unnecessary for a turn the authority says
    needs a current one.

    Deliberately scoped to that arm. The branches above it are user instructions and privacy
    boundaries -- a stipulated frame, an explicit "don't research", a personal question -- and
    those still win over any requirement, because the user's own instruction is not something a
    classifier may overrule.
    """
    clean = " ".join(str(text or "").split())
    if not clean:
        return False
    if stipulated_frame_forbids_retrieval(clean):
        return True
    if wants_external_research(clean):
        return False
    if wants_no_research(clean):
        return True
    if is_personal_knowledge_question(clean):
        return True
    if looks_like_style_task(clean):
        return True
    if looks_like_correction(clean):
        return True
    if current_information_required:
        return False
    return looks_like_local_recall(clean) and (active_mission_present or local_memory_hit)
