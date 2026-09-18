"""Detect questions about the user's own private/personal information (family, PII).

Such questions are not researchable on the web — the honest behaviour is to answer from
VOOL's own memory if the fact was told to it, and otherwise plainly admit it is not known
(and offer to remember it), instead of running a slow web/research loop. Kept deliberately
tight so it never suppresses ordinary "my project / my repo / my website / my company"
work questions.
"""
from __future__ import annotations

import re

# Private relations / people whose details only the user can supply.
_RELATION_RE = re.compile(
    r"\b(sister|brother|mother|father|mom|dad|mum|parent|parents|son|daughter|child|children|"
    r"kid|kids|wife|husband|spouse|girlfriend|boyfriend|partner|fiance|fiancee|aunt|uncle|cousin|"
    r"grand(?:mother|father|ma|pa|son|daughter)|niece|nephew|in-law)\b",
    re.IGNORECASE,
)
# Personal PII that is not derivable and not on the public web.
_PII_RE = re.compile(
    r"(middle name|maiden name|home address|mailing address|phone number|personal email|"
    r"private email|birth\s?day|date of birth|social security|passport number|personal detail)",
    re.IGNORECASE,
)
# Work/technical subjects that also use the possessive but are NOT private personal info.
_EXCLUDE_RE = re.compile(
    r"\b(project|projects|repo|repos|repository|website|site|company|companies|business|app|apps|"
    r"application|code|codebase|domain|wallet|api|server|config|process|thread|node|container|"
    r"branch|pr|ticket|issue|deadline|task|deploy|build|pipeline|account|team)\b",
    re.IGNORECASE,
)
_POSSESSIVE_RE = re.compile(r"\b(?:my|our)\b", re.IGNORECASE)

PERSONAL_KNOWLEDGE_ADMISSION = (
    "I don't have that in my memory. If you tell me, I can remember it for next time."
)


def is_personal_knowledge_question(text: str) -> bool:
    """True for questions about the user's own family/PII (e.g. "what is my sister's middle
    name?"), False for ordinary possessive work questions ("what is my repo name?")."""
    low = " " + " ".join(str(text or "").lower().split()) + " "
    if not _POSSESSIVE_RE.search(low):
        return False
    if not (_RELATION_RE.search(low) or _PII_RE.search(low)):
        return False
    # A work/technical subject in the same message means it is not a private-personal question
    # (e.g. "my child process", "my sister's website").
    return not _EXCLUDE_RE.search(low)


def memory_has_personal_answer(text: str, memory_search) -> bool:
    """True if VOOL's stored memory already holds the SPECIFIC personal fact asked for, so the
    turn should answer from memory instead of admitting ignorance. Requires the exact relation
    (e.g. "sister") or PII phrase to appear in a retrieved fact — a "brother" question does not
    match a stored "sister" fact even though both share "middle name".
    """
    low = " ".join(str(text or "").lower().split())
    relations = [m.group(1).lower() for m in _RELATION_RE.finditer(low)]
    pii = [m.group(1).lower() for m in _PII_RE.finditer(low)]
    if not relations and not pii:
        return False
    try:
        results = memory_search(text, limit=5) or []
    except Exception:
        return False
    for result in results:
        fact = str((result or {}).get("text") or "").lower()
        if relations and all(rel in fact for rel in relations):
            return True
        if not relations and pii and any(phrase in fact for phrase in pii):
            return True
    return False
