"""A short follow-up that repeats the previous task with a new object, or redoes it with a change.

After "how do you say garden in italian and portuguese", the next turns "meadow?", "ok and
butter", "do tunnel too" are the same task for a new word; after a bill split, "one friend can't
come anymore, redo it" is the same task with a change. On the 0.7.8 sweep (2026-11-12, blocks 1c
and 1e) the model read them on their own and defined the word, explained the phrase or asked what
was meant. This module states the carried task explicitly in the turn's text, so every lane and
the model read the continuation as what it is. General shapes, not a list of the sweep's words.
Contributor: sls_0x.
"""
from __future__ import annotations

import re

_LEAD = r"(?:(?:ok(?:ay)?|and|now|then|so|also|alright|right|cool|great|thanks|thx)[,!.\s]+)*"
_NEW_OBJECT_RE = re.compile(
    rf"^\s*{_LEAD}(?:(?:do|try|same\s+for|what\s+about|how\s+about|and\s+for|now\s+for)\s+)?"
    r"(?P<obj>[\w'][\w' -]{0,40}?)"
    r"(?:\s+(?:too|as\s+well|also|next))?\s*[?.!]*\s*$",
    re.IGNORECASE,
)
_REDO_RE = re.compile(
    r"\b(?:redo|re-?do|recalculate|recompute|again|update\s+it|do\s+it\s+again|run\s+it\s+again|try\s+again)\b",
    re.IGNORECASE,
)
_COUNT_CHANGE_RE = re.compile(
    r"\b(?:(?:one|two|three|\d+)\s+(?:more|extra|less|fewer)\b|(?:can'?t|cannot|won'?t)\s+(?:come|make\s+it|join)|"
    r"(?:joined|dropped\s+out|left|cancelled)\b|we\s+are\s+(?:one|two|\d+)\s+(?:more|less|fewer))",
    re.IGNORECASE,
)
_NOT_AN_OBJECT_RE = re.compile(
    r"^(?:yes|no|yeah|yep|nope|ok|okay|thanks|thank\s+you|thx|cool|great|nice|wow|hmm|why|what|how|who|"
    r"when|where|which|really|sure|please|pls|hi|hello|hey|bye|done|stop|cancel|never\s*mind)$",
    re.IGNORECASE,
)
_VERB_RE = re.compile(
    r"\b(?:is|are|was|were|am|be|do|does|did|have|has|had|can|could|will|would|should|make|tell|give|show|"
    r"explain|write|send|open|read|find|remind|remember|forget|delete|i|you|my|me)\b",
    re.IGNORECASE,
)
_TASK_HEAD_RE = re.compile(
    r"^\s*(?:how\s+do\s+(?:you|i)\s+say|translate|what(?:'s|\s+is)\s+\S+\s+in\s|spell|define|convert|"
    r"split|divide|calculate|compute|work\s+out|give\s+me|write|list|summari[sz]e|rewrite|"
    r"what(?:'s|\s+is)\s+the\s+(?:plural|past\s+tense|opposite|synonym)|synonym|antonym)",
    re.IGNORECASE,
)


def last_task_request(session_id: str, *, look_back: int = 4) -> str:
    """The chat's most recent user turn that set a task (a translation, a split, a list...)."""
    try:
        from core.chat_session_identity import canonical_chat_session_id
        from core.memory.entries import recent_conversation_events

        rows = list(recent_conversation_events(canonical_chat_session_id(str(session_id or "")), limit=look_back) or ())
    except Exception:
        return ""
    for row in reversed(rows):
        said = " ".join(str((row or {}).get("user") or "").split())
        if said and _TASK_HEAD_RE.match(said):
            return said
    return ""


def compose_continuation(text: str, previous_request: str) -> str:
    """The turn's text with the carried task stated, or "" when it is not a continuation."""
    current = " ".join(str(text or "").split())
    previous = " ".join(str(previous_request or "").split())
    if not current or not previous or len(previous.split()) < 3 or len(current.split()) > 12:
        return ""
    try:
        from core.model_own_tools import parse_tools_command

        # "use VOOL's tools" is VOOL's own switch, not the last task for a new object: carried, it
        # re-ran the previous web ask and the switch never happened (measured on the v0.8 line).
        if parse_tools_command(current):
            return ""
    except Exception:
        pass
    if not _TASK_HEAD_RE.match(previous):
        return ""
    if _REDO_RE.search(current) or _COUNT_CHANGE_RE.search(current):
        change = re.sub(
            r"\s*\b(?:(?:redo|re-?do|recalculate|recompute|update|do|run|try)\s+(?:it|that|this)(?:\s+again)?|"
            r"redo|re-?do|recalculate|recompute|again)\b",
            "", current, flags=re.IGNORECASE,
        )
        change = re.sub(r"\s*,\s*$|\s{2,}", " ", change).strip(" ,.;:!?-") or current
        return f"Redo my previous request — “{previous}” — with this change: {change}"
    match = _NEW_OBJECT_RE.match(current)
    if not match:
        return ""
    obj = match.group("obj").strip(" '")
    if not obj or len(obj.split()) > 3 or _NOT_AN_OBJECT_RE.match(obj) or _VERB_RE.search(obj):
        return ""
    return f"Same as my previous request — “{previous}” — but for: {obj}"


def continuation_for_chat(text: str, previous_request: str, source_context: dict | None) -> str:
    """`compose_continuation`, except in a chat where the person's database is at work.

    There a short follow-up is its own question: "who joined most recently" was read as the bill-split change
    "someone joined", rewritten into "Redo my previous request ... with this change", pulled off the database
    lane and answered from chat context (Mac run 16i, toolshed 10)."""
    try:
        from core.database_work_state import active_database_work_intents

        if active_database_work_intents(source_context):
            return ""
    except Exception:
        pass
    return compose_continuation(text, previous_request)


__all__ = ["compose_continuation", "continuation_for_chat", "last_task_request"]
