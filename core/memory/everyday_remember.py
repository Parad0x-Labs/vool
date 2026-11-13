"""Remember asks in everyday words, the list one opens, and updates to a saved fact.

Measured on the v0.8 head-to-head (f4f048f7, a cloud model and a local one, fresh profile, every task a new chat):

    "hey, few things to remember for me pls. my sister Ilse has her birthday on 14 february, ..."
    "work stuff to keep in mind: our team standup moved to 9:40 every weekday"
    "small update: the cabin dates changed, its now 13-16 march, same booking code"

were each answered "Got it -- noted" and nothing was saved: the memory lane only took a message that STARTS with
remember / note / store / keep / retain / save, and the model never called its memory tool. The facts said on the
following turns of the same list, and the updates said in a later chat, were lost the same way.

This module only reads text and the chat's earlier turns. What is saved, where, and under which law is still the
memory lane's (``core.persistent_memory``): the same admission, scope rule, secret refusal and forget fence as an
explicit "Remember that ...".
"""
from __future__ import annotations

import re
from dataclasses import dataclass

#: An everyday lead that asks VOOL to remember what follows. "Remember to ..." and "don't forget to ..." are
#: reminders (another lane), so the forms here never take "to" after them.
_LEAD_RE = re.compile(
    r"^(?:(?:hey|hi|hello|ok|okay|so|oh|also|and|btw|right)\b[\s,!.]*)*"
    r"(?:(?:can|could|would|will)\s+you\s+(?:please\s+)?|pls\s+|plz\s+|please\s+)?"
    r"(?P<lead>"
    r"(?:a\s+)?(?:few|some|couple(?:\s+of)?|two|three|four|five|\d+)\s+(?:more\s+|quick\s+)?"
    r"(?P<plural1>things|bits|facts|notes|details)\s+(?:for\s+you\s+)?to\s+(?:remember|keep\s+in\s+mind|note(?:\s+down)?)"
    r"|(?:[a-z][\w'-]*\s+){0,3}(?P<plural2>stuff|things|info|details|notes)\s+(?:for\s+you\s+)?to\s+"
    r"(?:remember|keep\s+in\s+mind|note(?:\s+down)?)"
    r"|keep\s+(?:this\s+|that\s+)?in\s+mind(?:\s+that)?"
    r"|remember\s+(?:this\s+)?for\s+me(?:\s+that)?"
    r"|(?:make|take)\s+a\s+note(?:\s+of\s+this)?(?:\s+that)?"
    r"|note\s+(?:this\s+)?down(?:\s+that)?"
    r"|for\s+future\s+reference"
    r"|don'?t\s+forget\s+that"
    r")"
    r"(?:\s+for\s+me)?(?:\s*,?\s*(?:pls|please|plz|thanks|thx))?"
    r"(?:\s*[:.,;!\-]+\s*|\s+|$)(?P<fact>.*)$",
    re.IGNORECASE | re.DOTALL,
)

#: A request or command, not something the person is telling VOOL about their life.
_REQUEST_HEAD_RE = re.compile(
    r"^\s*(?:please\s+|pls\s+|can\s+you\s+|could\s+you\s+)?"
    r"(?:write|make|create|build|draft|show|tell|give|find|search|look|check|open|send|read|summari[sz]e|translate|"
    r"explain|list|help|fix|run|install|buy|order|play|set|add|remove|delete|forget|erase|turn|switch|start|stop|"
    r"cancel|remind|compare|calculate|convert|generate|draw|plan|book|schedule|email|call|text|message|pay|swap|"
    r"remember|note|save|store|keep|retain)\b",
    re.IGNORECASE,
)
_FIRST_PERSON_RE = re.compile(r"\b(?:i|i'm|im|i've|ive|i'd|my|mine|me|we|we're|we've|our|ours|us)\b", re.IGNORECASE)
_CONTINUATION_HEAD_RE = re.compile(r"^(?:(?:oh|also|and|plus|btw|then|one more(?: thing)?)\b[\s,:.-]*)+", re.IGNORECASE)

#: What makes a statement an update to something already saved.
_UPDATE_RE = re.compile(
    r"\b(?:update[ds]?|changed|change of plans?|moved|went back|is now|are now|it'?s now|its now|now (?:on|at|in|is)|"
    r"rescheduled|postponed|brought forward|no longer|not anymore|anymore|instead)\b",
    re.IGNORECASE,
)
_UPDATE_LEAD_RE = re.compile(
    r"^(?:(?:oh|also|and|btw|ok|okay|so)\b[\s,]*)*(?:(?:a\s+)?(?:small|quick|little|tiny)\s+)?(?:update|change)"
    r"(?:\s+(?:on|to)\s+that)?\s*[:,.-]\s*",
    re.IGNORECASE,
)
#: Words an update shares with almost any saved fact; they never decide which fact it updates.
_GENERIC = frozenset(
    [
        "update", "updates", "updated", "small", "quick", "little", "tiny", "change", "changed", "changes",
        "moved", "move", "went", "back", "now", "new", "same", "old", "its", "it's", "is", "are", "was", "were",
        "the", "a", "an", "and", "also", "oh", "to", "on", "at", "in", "of", "for", "my", "our", "we", "i",
        "me", "us", "this", "that", "these", "those", "date", "dates", "time", "times", "day", "days", "week",
        "weeks", "instead", "anymore", "longer", "no", "not"
    ]
)
_MONTHS = frozenset(
    [
        "january", "february", "march", "april", "may", "june", "july", "august", "september", "october",
        "november", "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept", "oct", "nov",
        "dec", "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "weekday",
        "weekdays"
    ]
)

#: How many earlier turns of one chat a list stays open across.
LIST_TURNS = 8


@dataclass(frozen=True)
class EverydayLead:
    """A recognised everyday remember ask: the fact it carries ("" for a lead alone) and whether it opens a list."""

    fact: str
    opens_list: bool


def everyday_lead(text: str) -> EverydayLead | None:
    clean = " ".join(str(text or "").split())
    match = _LEAD_RE.match(clean)
    if match is None:
        return None
    fact = match.group("fact").strip()
    if fact.lower().startswith("to "):
        if not match.group("lead").lower().startswith("keep"):
            return None  # "make a note to call mom" asks for a reminder, another lane
        # "keep in mind to buy milk" keeps "buy milk": the capture lane read "keep" as its verb and saved
        # "in mind to buy milk".
        fact = fact[3:].strip()
        if not fact:
            return None
    plural = bool(match.group("plural1") or match.group("plural2"))
    return EverydayLead(fact=fact, opens_list=plural or not fact)


def is_question(text: str) -> bool:
    """A question or an ask for an answer, also without its question mark ("remind me what my manager is called")."""
    from core.agent_runtime.cross_chat_recall import _is_statement

    clean = " ".join(str(text or "").split())
    return bool(clean) and not _is_statement(clean)


def personal_statement(text: str) -> bool:
    """Something the person tells about their own life: first person, not a question, not a request."""
    clean = _CONTINUATION_HEAD_RE.sub("", " ".join(str(text or "").split()))
    if not clean or is_question(clean) or _REQUEST_HEAD_RE.match(clean):
        return False
    return bool(_FIRST_PERSON_RE.search(clean))


def strip_continuation(text: str) -> str:
    return _CONTINUATION_HEAD_RE.sub("", " ".join(str(text or "").split())).strip()


def list_is_open(earlier_user_turns: list[str]) -> bool:
    """True when this chat's earlier turns, newest first, reach a list-opening remember ask through statements only."""
    for said in earlier_user_turns[:LIST_TURNS]:
        lead = everyday_lead(said)
        if lead is not None:
            return lead.opens_list
        if not (personal_statement(said) or update_shaped(said)):
            return False
    return False


def update_shaped(text: str) -> bool:
    clean = " ".join(str(text or "").split())
    if not clean or is_question(clean) or _REQUEST_HEAD_RE.match(strip_continuation(clean)):
        return False
    return bool(_UPDATE_RE.search(clean) or _UPDATE_LEAD_RE.match(clean))


def update_body(text: str) -> str:
    return strip_continuation(_UPDATE_LEAD_RE.sub("", " ".join(str(text or "").split())))


def topic_tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z][a-z'-]+", str(text or "").casefold())
    return {w for w in words if len(w) > 2 and w not in _GENERIC and w not in _MONTHS}


def updated_entry(text: str, entries: list[dict]) -> dict | None:
    """The one saved entry an update statement replaces: the most shared topic words, newest on a tie."""
    wanted = topic_tokens(update_body(text))
    if not wanted:
        return None
    best, best_score = None, 0
    for row in entries:
        score = len(wanted & topic_tokens(str(row.get("fact") or row.get("text") or "")))
        if score and score >= best_score:
            best, best_score = row, score
    return best
