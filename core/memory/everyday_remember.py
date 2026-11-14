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


#: A closing or a bare reaction inside a list ("ok that's all", "thanks!"): no fact.
_CLOSING_RE = re.compile(
    r"^(?:ok(?:ay)?|k|thanks?|thank\s+you|thx|ty|cool|great|perfect|nice|awesome|got\s+it|"
    r"that'?s\s+(?:it|all|everything)|nothing\s+else|done|no(?:pe)?|yes|yeah|yep|sure)\b[\s,.!]*"
    r"(?:(?:that'?s\s+(?:it|all|everything)|for\s+now|thanks?|thank\s+you|bye)[\s,.!]*)*$",
    re.IGNORECASE,
)


#: Taking back or correcting what was just said ("never mind, don't keep that one", "actually scratch the last one",
#: "the second one was wrong"): never a fact of the list (independent review of job 19, C2: these were saved and
#: answered "Locked in", with the retracted fact still kept).
_RETRACTION_RE = re.compile(
    r"\b(?:never\s*mind|nvm|scratch\s+(?:that|this|it|the|my)|strike\s+that|take\s+(?:that|it)\s+back|"
    r"(?:don'?t|do\s+not|no\s+need\s+to)\s+(?:keep|save|remember|store|note|bother)|forget\s+(?:that|it|this|the|what|about)|"
    r"ignore\s+(?:that|it|this|the|what|my)|(?:delete|remove|drop|cancel|undo)\s+(?:that|it|this|the\s+last)|"
    r"(?:was|is|'s|’s|were|are)\s+wrong|wrong\s+one|not\s+right|my\s+(?:bad|mistake)|i\s+(?:was|am)\s+wrong|"
    r"i\s+meant|correction|actually\s+no)\b",
    re.IGNORECASE,
)
#: Who a list fact can be about without "I" or "my": someone named or pointed at ("his daughter is called Marta").
_THIRD_PERSON_RE = re.compile(r"\b(?:he|he's|she|she's|his|her|hers|him|they|they're|their|theirs|them)\b", re.IGNORECASE)
#: Words a list's turns share without sharing a subject.
_LIST_FILLER = frozenset(
    ["things", "thing", "remember", "remind", "note", "notes", "list", "couple", "few", "some", "this", "that", "these",
     "those", "with", "from", "have", "has", "had", "about", "just", "also", "what", "when", "where", "there", "here",
     "been", "will", "would", "your", "yours", "for", "the", "and", "but", "then", "than", "very", "really", "today"]
)


def _list_subject_words(text: str) -> set[str]:
    return {w for w in topic_tokens(text) if len(w) >= 4 and w not in _LIST_FILLER}


#: The take-back part of the retractions alone (no correction words such as "wrong" or "i meant", which carry a new
#: value): "never mind, don't keep that one" holds no fact at all.
_TAKE_BACK_RE = re.compile(
    r"\b(?:never\s*mind|nvm|scratch\s+(?:that|this|it|the\s+last)|strike\s+that|take\s+(?:that|it)\s+back|"
    r"(?:don'?t|do\s+not|no\s+need\s+to)\s+(?:keep|save|remember|store|note)\s+(?:that|this|it|the\s+last)|"
    r"forget\s+(?:that|it|this)|ignore\s+(?:that|it|this)|(?:delete|remove|drop|cancel|undo)\s+(?:that|it|this|the\s+last))\b",
    re.IGNORECASE,
)


def takes_back(text: str) -> bool:
    """Whether the statement takes back what was said before ("never mind, don't keep that one")."""
    return bool(_TAKE_BACK_RE.search(" ".join(str(text or "").split())))


def retracts_or_corrects(text: str) -> bool:
    return bool(_RETRACTION_RE.search(" ".join(str(text or "").split())))


def list_statement(text: str, earlier_user_turns: list[str] | None = None) -> bool:
    """A fact said inside a list the person opened: a statement about the person, the list's people or its subject.

    First person is not needed there. Measured on the real local model (held-out set): after "ok a few things to
    remember for me. my brother Tobiah lives in Quillon", "his daughter is called Marta and she turns 7 on 3 august"
    and "oh and Tobiah is vegetarian" were answered "Got it ... Nothing was saved"; they name the people of the list.
    A question, a request, a closing, a retraction or a correction is no fact, and neither is chatter that is about
    nobody in the list ("the weather is awful today"). `earlier_user_turns` are the list's earlier turns: a statement
    sharing a subject word with them ("tobiah is vegetarian") belongs to the list."""
    clean = _CONTINUATION_HEAD_RE.sub("", " ".join(str(text or "").split()))
    if (len(clean.split()) < 3 or _CLOSING_RE.match(clean) or is_question(clean) or _REQUEST_HEAD_RE.match(clean)
            or _RETRACTION_RE.search(clean)):
        return False
    if _FIRST_PERSON_RE.search(clean) or _THIRD_PERSON_RE.search(clean):
        return True
    earlier = " ".join(str(t or "") for t in (earlier_user_turns or [])[:LIST_TURNS])
    return bool(_list_subject_words(clean) & _list_subject_words(earlier))


def strip_continuation(text: str) -> str:
    return _CONTINUATION_HEAD_RE.sub("", " ".join(str(text or "").split())).strip()


def list_is_open(earlier_user_turns: list[str]) -> bool:
    """True when this chat's earlier turns, newest first, reach a list-opening remember ask through statements only."""
    window = list(earlier_user_turns[:LIST_TURNS])
    for index, said in enumerate(window):
        lead = everyday_lead(said)
        if lead is not None:
            return lead.opens_list
        if not (list_statement(said, window[index + 1:]) or update_shaped(said)):
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


#: A file, a folder or a place on the computer: "I moved the quarterly report pdf to the archive folder" is about where
#: a file is.
_FILE_PLACE_RE = re.compile(
    r"\b(?:files?|folders?|subfolders?|directory|directories|pdfs?|spreadsheets?|screenshots?|downloads|desktop|"
    r"documents\s+folder|icloud\s+drive|dropbox|google\s+drive|onedrive)\b"
    r"|\b[\w-]+\.(?:pdf|docx?|txt|md|csv|xlsx?|pages|numbers|key|png|jpe?g|zip)\b",
    re.IGNORECASE,
)


def _word_form(word: str) -> str:
    return re.sub(r"(?:'s|’s|s)$", "", word)


def updated_entry(text: str, entries: list[dict]) -> dict | None:
    """The one saved entry an update statement replaces: the most shared topic words, newest on a tie.

    A statement about a file or folder replaces only a saved fact about a file or folder: with "the quarterly report
    is due on the 3rd" saved, "I moved the quarterly report pdf to the archive folder" replaced the due date (the clean
    loop hands everyday updates to this lane before it reads files)."""
    body = update_body(text)
    wanted = topic_tokens(body)
    if not wanted:
        return None
    about_files = bool(_FILE_PLACE_RE.search(text))
    # What the update is about: the words before its change word ("the STREET PARTY moved to saturday"). Every one of
    # them must be in the fact it replaces: with "my dentist is dr Okafor on Brill street" saved, the street party
    # replaced the dentist on the one word "street" (independent review of job 19, C6).
    cue = _UPDATE_RE.search(body)
    subject = {_word_form(w) for w in topic_tokens(body[: cue.start()])} if cue else set()
    best, best_score = None, 0
    for row in entries:
        saved = str(row.get("fact") or row.get("text") or "")
        if about_files and not _FILE_PLACE_RE.search(saved):
            continue
        if subject and not subject <= {_word_form(w) for w in topic_tokens(saved)}:
            continue
        score = len(wanted & topic_tokens(saved))
        if score and score >= best_score:
            best, best_score = row, score
    return best
