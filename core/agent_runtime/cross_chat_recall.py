"""Recall of the owner's own facts from their OTHER chats and projects, with where it was found.

Measured 2026-11-11 on the installed 0.7.8 build (sls, real profile): a question about the owner's
own fact that was not saved with "remember" and not said in the current chat was answered "Your
test hotel booking code is not listed in the provided context" -- the model saw only this chat and
the saved memory, never the owner's other conversations, and answered in developer words. Recall
across the owner's chats and projects is the product's promise; the runtime already owns an
exact, governed search over them (``core.chat_housekeeping.search_chats``) that only answered
"which chat did we talk about X in".

This lane answers ONE question about the speaker's own facts when neither saved memory nor the
current chat holds it:

* found in another chat -> the owner's own words from that chat, quoted, with the chat's title,
  project and day, and an offer to remember it. Only a statement the OWNER wrote is used: a
  question, or an assistant reply (which may have been wrong), never grounds an answer.
* found nowhere -> a plain sentence saying it is not saved and not in any of their N chats, and
  that telling VOOL will save it. Never "provided context".

Anything forgotten anywhere stays forgotten: every candidate passes the revocation authority for
all chats (``authorize_payload`` with no chat) before it can be quoted. Only the owner's own local
session reaches here; private chats never search or quote other chats.
"""
from __future__ import annotations

import contextlib
import hashlib
import logging
import math
import re
from dataclasses import dataclass
from typing import Any

from core.context_history_authority import JOINT_DECISION_RE, RESUME_RE

LANE_ID = "cross_chat_recall"
_LOG = logging.getLogger("vool.cross_chat_recall")
_QUESTION_WORDS = frozenset({"my", "me", "i", "what", "which", "who", "whom", "whose", "when", "where", "how", "do", "did", "does", "remember", "know", "tell"})

_QUESTION_HEAD_RE = re.compile(
    r"^\s*(?:what'?s|whats|wats|what|which|who|whom|whose|when|where|why|how|do|does|did|is|are|was|were|can|could|will|would|should|have|has)\b",
    re.IGNORECASE,
)
_MEMORY_COMMAND_RE = re.compile(
    r"\b(?:memor(?:y|ies)|forg[eo]t|this\s+(?:chat|conversation|thread)|you\s+know\s+about\s+me|about\s+me)\b",
    re.IGNORECASE,
)
#: Words that point back at the conversation ("which hobby did I mention second?", "how old will
#: she be?"). In a chat that already holds the owner's statements they refer to THIS chat and stay
#: with it; in a new chat ("the hotel I mentioned", "my locker code from earlier") they can only
#: mean another chat, which is this lane's to search.
_CONVERSATION_POINTER_RE = re.compile(
    r"\b(?:earlier|before|mention(?:ed)?|said|say|told|tell\s+you|asked|wrote|gave\s+you|"
    r"she|he|her|him|they|them|it|that|those|these)\b",
    re.IGNORECASE,
)
_OTHER_CHAT_RE = re.compile(
    r"\b(?:in|from)\s+(?:a|an|another|other|the\s+other|a\s+different|a\s+previous|my\s+other|an\s+old|a\s+past)\s+"
    r"(?:chats?|conversations?|threads?)\b|\b(?:other|another|previous|past|different)\s+(?:chats?|conversations?)\b",
    re.IGNORECASE,
)
_REMEMBER_LEAD_RE = re.compile(
    r"^\s*(?:(?:by\s+the\s+way|btw|fyi|oh|also|and)\s*[,:]?\s*)?(?:please\s+)?(?:(?:remember|note|keep\s+in\s+mind)(?:\s+that)?\s*[:,]?\s*)?",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CrossChatRecall:
    response: str
    found: bool
    session_id: str = ""
    chats_searched: int = 0



#: Common chat shorthand, read as the words it stands for wherever it appears: an abbreviation
#: must not decide whether a saved fact is found (0.7.8 sweep block 1c: the short forms missed
#: facts the long forms found). General vocabulary, not tied to any one question.
_CHAT_SHORTHAND: tuple[tuple[re.Pattern[str], str], ...] = tuple(
    (re.compile(rf"\b{short}\b", re.IGNORECASE), full)
    for short, full in (
        (r"fav(?:e|ourite|orite)?", "favourite"), (r"nr\.?(?=\s|$|\?)", "number"), (r"num", "number"),
        (r"no\.(?=\s*\w)", "number"), (r"ref", "reference"), (r"refs", "references"), (r"bday", "birthday"),
        (r"b-day", "birthday"), (r"addr", "address"), (r"appt", "appointment"), (r"acct", "account"),
        (r"pwd|pw", "password"), (r"tmrw|tmr|tmw", "tomorrow"), (r"ur", "your"), (r"r(?=\s+(?:my|the|we|you)\b)", "are"),
        (r"msg", "message"), (r"info", "information"), (r"reso|resv", "reservation"), (r"conf", "confirmation"),
    )
)

#: The kinds of thing a person keeps a record of and later asks for by name alone ("car rental
#: reference?", "gym locker code?"). A bare noun phrase ending in one of these, asked as the first
#: message of a chat, is a question about the person's own record.
_RECORD_NOUN_RE = re.compile(
    r"\b(?:code|number|reference|id|pin|password|booking|reservation|confirmation|ticket|address|"
    r"birthday|date|day|time|appointment|account|plate|permit|locker|seat|gate|flight|room)s?\b",
    re.IGNORECASE,
)
_BARE_ASK_RE = re.compile(r"^(?!(?:and|or|plus|what\s+about|how\s+about|but|so|then)\b)[a-z0-9][\w' -]{1,60}\?$", re.IGNORECASE)
_VERBISH_RE = re.compile(
    r"\b(?:is|are|was|were|do|does|did|can|could|will|would|should|have|has|had|be|get|make|go|need|want)\b",
    re.IGNORECASE,
)


def bare_record_ask(text: str) -> str:
    """ "car rental reference?" -> "what's my car rental reference?"; "" when the text is not one."""
    clean = " ".join(str(text or "").split())
    if (not _BARE_ASK_RE.match(clean) or _VERBISH_RE.search(clean) or len(clean.split()) > 7
            or _QUESTION_HEAD_RE.match(clean) or re.match(r"(?:what|which|who|where|when|how|why)\b", clean, re.IGNORECASE)):
        return ""
    head = re.split(r"\s+(?:for|of|from|at|in|on)\s+", clean.rstrip("? "), maxsplit=1)[0]
    if not _RECORD_NOUN_RE.search(head.split()[-1] if head.split() else ""):
        return ""
    return "what's my " + clean.rstrip("? ") + "?"


def as_question(text: str) -> str:
    """The ask as the recall grammar reads it: "whats my gym locker number" -> "what's my gym locker number?".

    People type questions without the mark and with "whats"; a sentence that OPENS like a question
    is read as one, so a missing "?" never turns an own-fact question into a world question."""
    clean = " ".join(str(text or "").split())
    # Chat shorthand reads as the words it stands for (0.7.8 sweep block 1c: "whats my fav
    # bakery" and "parking permit nr for Tallinn?" missed facts that the long forms found).
    for pattern, full in _CHAT_SHORTHAND:
        clean = pattern.sub(full, clean)
    clean = re.sub(r"[\s,]+(?:pls|plz|please|thx|thanks|ty)\s*([?.!]?)$", r"\1", clean, flags=re.IGNORECASE)
    clean = re.sub(r"^(?:whats|wats|whta's|whats')\b", "what's", clean, flags=re.IGNORECASE)
    # "Your test hotel booking code?" -- the person's own thing, asked the way people talk to
    # an assistant about it ("your" from the assistant's side).
    clean = re.sub(r"^your\s+((?!(?:name|model|opinion|view|take|guess)\b)[^?.!]{2,60}?)\s*\?$", r"what's my \1?", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\bu\b", "you", clean, flags=re.IGNORECASE)
    clean = re.sub(
        r"^(?:do|did)\s+you\s+(?:remember|know|recall|have)\s+(?:what\s+)?(?:is\s+)?((?:my|the)\s+.+?)\s*\??$",
        r"what's \1?", clean, flags=re.IGNORECASE,
    )
    clean = re.sub(
        r"^(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?(?:find|get|look\s+up|check|tell\s+me|show\s+me)\s+((?:my|the)\s+[^.!?]+?)\s*[.!?]?$",
        r"what's \1?", clean, flags=re.IGNORECASE,
    ) if not _LIVE_DOMAIN_RE.search(clean) else clean
    clean = re.sub(
        r"^(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?remind\s+me\s+(?:again\s+)?((?:which|what|who|where|when|how)\b[^.!?]+?|the\s+[^.!?]+?)\s*[.!?]?$",
        lambda m: (m.group(1) if re.match(r"(?:which|what|who|where|when|how)\b", m.group(1), re.IGNORECASE)
                   else "what's " + m.group(1)) + "?",
        clean, flags=re.IGNORECASE,
    ) if not re.search(r"\bto\s+\w", clean) else clean
    clean = re.sub(
        r"^(?:(?:can|could|would)\s+you\s+)?(?:please\s+)?remind\s+me\s+(?:again\s+)?(?:of\s+|about\s+|what\s+)?(?:is\s+)?(my\s+[^.!?]+?)(?:\s+is)?\s*[.!?]?$",
        r"what's \1?", clean, flags=re.IGNORECASE,
    ) if not re.search(r"\bto\s+\w", clean) else clean
    if clean and not clean.endswith("?") and _QUESTION_HEAD_RE.match(clean):
        clean = clean.rstrip(" .!") + "?"
    return clean


#: "what bike do I have?", "where did I park?", "which hotel am I in?": a question about the
#: speaker's own situation that the possessive-based recall grammar does not see.
_PERSONAL_Q_RE = re.compile(
    r"^\s*(?:what'?s?|which|where|when|who)\b[^?]{0,80}\b(?:(?:do|did|have|had|am|was|will)\s+i|i\s+(?:mentioned|told\s+you|said|gave\s+you|like|love|prefer|use|have|had|booked|bought|picked|chose))\b[^?]{0,60}\?$",
    re.IGNORECASE,
)


def _personal_question(text: str) -> bool:
    return bool(_PERSONAL_Q_RE.match(text)) and "," not in text and " and " not in text.lower()

def _is_statement(text: str) -> bool:
    clean = " ".join(str(text or "").split())
    if not clean or "?" in clean or _QUESTION_HEAD_RE.match(clean):
        return False
    # A question typed without its mark or with a typo ("waht can you acutally do for me"), or a
    # request to VOOL ("show me the parse_header function"), states no fact. Read as one, the
    # owner's earlier ask in another chat was quoted back as "you mentioned it" when they asked
    # the same thing again (replay corpus on the v0.8 line: 4 repeated asks).
    try:
        from core.input_normalizer import normalize_user_text

        fixed = " ".join(str(normalize_user_text(clean).normalized_text or clean).split())
    except Exception:
        fixed = clean
    return not (_QUESTION_HEAD_RE.match(fixed) or as_question(fixed).endswith("?"))


def _owner_local(source_context: dict[str, Any] | None) -> bool:
    try:
        from core.request_trust import request_is_owner_local

        return request_is_owner_local(source_context or {})
    except Exception:
        return False


def _private(session_id: str, source_context: dict[str, Any] | None) -> bool:
    try:
        from core.private_chat import is_private, request_is_private

        return bool(is_private(session_id) or request_is_private())
    except Exception:
        return bool((source_context or {}).get("private_chat"))


#: Words that make an own-situation question a LIVE assistant question (calendar, reminders, mail,
#: files, weather, money), owned by those lanes even when no recognizer claims the exact phrasing.
#: Measured 2026-11-11: "do I have any meetings this week?" was answered "I don't have that saved".
_LIVE_DOMAIN_RE = re.compile(
    r"\b(?:meetings?|appointments?|calendar|agenda|events?|schedule|reminders?|e-?mails?|mail|inbox|"
    r"messages?|files?|folders?|downloads?|desktop|disk|battery|weather|forecast|balance|wallet|price|"
    # Data the owner keeps in a store a tool reads (database run 2, 2026-11-12: "whats in my orders
    # database?" was answered "I don't have that saved" by this lane).
    r"databases?|databse|datbase|db|tables?|tabel|spreadsheets?|sheets?|repo|repository|repositories|"
    r"today|tomorrow|tonight|this\s+(?:week|month|morning|afternoon|evening)|next\s+week|now|"
    # Free or busy on a day is the calendar's, and space on this computer the disk lane's (skills A/B
    # on the v0.8 head, 2026-11-13: "am I free to meet on Friday?" and, with the loop off, "why is my
    # mac out of space" were answered "I don't have that saved").
    r"busy|(?:free|available)\s+(?:to\s+meet|on|at|for\s+(?:a\s+)?(?:call|meeting|lunch|dinner|coffee)|this|next)|"
    r"mondays?|tuesdays?|wednesdays?|thursdays?|fridays?|saturdays?|sundays?|weekend|"
    r"storage|(?:out\s+of|free|disk|running\s+out\s+of)\s+space|space\s+left|"
    r"(?:on|in)\s+(?:my|this)\s+(?:whole\s+|entire\s+)?(?:computer|mac|macbook|laptop|pc|machine|hard\s+drive))\b",
    re.IGNORECASE,
)
#: Not a saved fact of the person's: a request for an explanation or a cause ("why is my mac out of
#: space?", "how do I reset my router?"), a permission or rule ("am I free to quit my job without
#: notice in Germany?", "am I allowed to park here?"), or what a document says ("what does this csv
#: say about our sales?"). Skills A/B on the v0.8 head (2026-11-13) measured each answered "I don't
#: have that saved, and I looked through your other chats". "Why did I ..." and "how did I ..." ask
#: about the person's own past and stay.
_NOT_A_SAVED_FACT_RE = re.compile(
    # "how many / how much / how long / how old ..." asks for an amount, which may well be a saved fact
    # ("how many tonnes does the yard hold according to my notes?", CI #681); only "how" asking for a
    # method or a cause is left to the model.
    r"^\s*(?:why|how)\b(?!\s+(?:many|much|long|old|often|far|big|tall|large|heavy|soon|late|early)\b)"
    r"(?!\s+(?:did|do|have|had|was|were)\s+(?:i|we)\s+(?:say|tell|mention|decide|agree|choose|pick|name|call|end\s+up)\b)"
    r"|^\s*(?:am|are)\s+(?:i|we)\s+(?:free|allowed|permitted|able|entitled|obliged|required|supposed|legally)\s+to\b"
    r"|\bwhat\s+(?:does|do|did)\s+(?:this|that|these|those|the)\s+(?:[\w'’.-]+\s+){0,2}?(?:say|show|tell\s+(?:me|us))\b",
    re.IGNORECASE,
)
#: Writing the owner keeps (VOOL's notes, a journal, a notes file in a project): "what's in my notes?"
#: reads those, never "I don't have that saved" (v0.8 line 247124ed, 2026-11-12). A fact asked
#: "according to my notes" is still a records question, and this lane answers it: it searches every
#: chat and project and says plainly when nothing is saved (served: "How many tonnes does the yard
#: currently hold according to my notes?" went to a web lookup once the notes words alone sent it away).
_OWN_WRITING_RE = re.compile(
    r"\b(?:notes|notebooks?|journal|diary)\b|\b(?:a|an|any|some)\s+(?:[\w'’-]+\s+)?note\s+(?:about|on|for|regarding)\b",
    re.IGNORECASE,
)
_RECORDS_SOURCE_RE = re.compile(
    r"\b(?:according\s+to|based\s+on|per|going\s+by)\s+(?:my|our|the)\s+(?:notes|notebooks?|journal|diary)\b",
    re.IGNORECASE,
)


#: A question about what the person attached to this turn ("Is anything said in the attached video?")
#: is the attachment reader's: served tests found the recall lane answering it with "I don't have
#: that saved, but you mentioned it in a chat".
_ATTACHMENT_RE = re.compile(
    r"\b(?:attached|attachment|attachments|uploaded|(?:this|that|the)\s+(?:file|image|photo|picture|"
    r"screenshot|video|clip|audio|recording|pdf|document|doc|spreadsheet|archive|zip|csv|xlsx|xls|tsv|json|txt|log|"
    r"sheet|table|chart|report|deck|slides?|presentation|email|message|link|page|article|text))\b",
    re.IGNORECASE,
)


def _agent_turn(session_id: str) -> bool:
    """A team agent's own turn never recalls the owner's other chats."""
    try:
        from core.agent_team.gate import is_agent_session

        return is_agent_session(session_id)
    except Exception:
        return False


def _owned_by_another_lane(text: str, asked: str = "", source_context: dict[str, Any] | None = None) -> bool:
    if _LIVE_DOMAIN_RE.search(text) or _ATTACHMENT_RE.search(text):
        return True
    if _OWN_WRITING_RE.search(_RECORDS_SOURCE_RE.sub(" ", text)):
        return True
    typed = asked or text
    try:
        # A question about the world ("do i need a visa for a layover in doha", "can i legally use a
        # photo from a news site on my blog") is the web's, or the web-off line's: no saved fact or
        # other chat answers it. Red team round 3 (W2/W3, 2026-11-12, head 10): after the clean loop
        # handed such an ask back, this lane answered "I don't have that saved, and I looked through
        # your other chats" before the web switch was read.
        from core.online_asks import requirements_want_a_lookup

        if requirements_want_a_lookup(typed, source_context) or requirements_want_a_lookup(text, source_context):
            return True
    except Exception:
        return True
    try:
        # Read as typed, before the recall grammar's rewrite: "show me my notes" lists the notes
        # (the operator's find_notes), and "find my chat about X" is the saved-chat search. Both
        # were rewritten to "what's my ...?" and answered "I don't have that saved" (v0.8 line
        # 247124ed; in-process on the v0.8 candidate fa4fcc92, 2026-11-12).
        from core.agent_runtime.housekeeping_lane import covers_unit
        from core.operator.parser import parse_operator_action_intent

        if parse_operator_action_intent(typed) is not None or covers_unit(typed):
            return True
    except Exception:
        return True
    try:
        # Files on this computer ("which pdfs are in my documents", "find my tax folder") are the
        # machine lanes' (real-app check 2026-11-12: answered "I don't have that saved"). So is a
        # to-do list ask: "what's on my to do list?" with Desktop/todo.txt there was answered "I
        # don't have that saved" while "read me my to do list" read the file (0.8 to-do list work,
        # 2026-11-12); the file lane, or the list VOOL keeps (core/todo_list_turn.py), answers it.
        from core.execution.constants import (
            machine_file_search_intent,
            machine_folder_search_intent,
            machine_kind_listing_intent,
            machine_todo_list_read_intent,
        )

        if (
            machine_kind_listing_intent(text)
            or machine_file_search_intent(text)
            or machine_folder_search_intent(text)
            or machine_todo_list_read_intent(text)
        ):
            return True
    except Exception:
        pass
    try:
        from core.runtime_lane_truth import runtime_lane_question

        if runtime_lane_question(text):
            return True
    except Exception:
        return True
    try:
        from core.tool_demand_signals import mailbox_retrieval_intents

        if mailbox_retrieval_intents(text):
            return True
    except Exception:
        return True
    try:
        # Any ask a tool family is seated for (database, workspace, web, ...) is that tool's turn.
        from core.tool_demand_signals import resolve_demand_signals

        if resolve_demand_signals(text).required_families:
            return True
    except Exception:
        return True
    try:
        from core.agent_runtime.demand_ownership import registered_lane_for

        if registered_lane_for(text):
            return True
    except Exception:
        return True
    return False


def _operator_store_ask(text: str) -> bool:
    """The person's own notes, reminders and calendar ("show me my notes", "find my notes about the
    boiler") are the operator's stores, read by its own lane. Read on the words as typed: the
    question form this lane builds ("what are my notes?") is not what the operator parser reads.
    On the line merged with the full 0.7.7 train this lane answered "I don't have that saved"
    over the person's notes, where the train listed them (audit N10)."""
    try:
        from core.local_operator_actions import parse_operator_action_intent

        return parse_operator_action_intent(text) is not None
    except Exception:
        return False
def _names_a_project_file(text: str, source_context: dict[str, Any] | None) -> bool:
    """The question names a file in the folder this chat is bound to, so it is that file's question.

    Measured 2026-11-12 on the v0.8 candidate, project chats: "What do my dentist notes say?" with
    dentist_notes.md in the project was answered "I don't have that saved" by this lane. A failed
    check declines too, as every other ownership check here does: the ordinary path still answers.
    """
    try:
        from core.agent_runtime.workspace_question_evidence import question_names_project_file

        return question_names_project_file(text, dict(source_context or {}))
    except Exception:
        return True


def _covers(terms: tuple[str, ...], found: tuple[str, ...] | list[str]) -> bool:
    if not terms:
        return False
    need = max(1, math.ceil(0.6 * len(terms)))
    return len(set(found) & set(terms)) >= need



_DATA_STORE_RE = re.compile(
    r"\b(?:databases?|databse|datbase|db|tables?|tabels?|spreadsheets?|sheets?|csv|workbook|repo|repository)\b",
    re.IGNORECASE,
)


def _chat_works_on_a_data_store(source_context: dict[str, Any] | None) -> bool:
    """Earlier asks in this chat were about a database, table or sheet: a follow-up such as "how many
    titles do we have in total" counts rows there, not something the person told VOOL (database
    run 9, 2026-11-12: it got "I don't have that saved" after five library-database turns)."""
    for item in list((source_context or {}).get("conversation_history") or [])[:-1]:
        if isinstance(item, dict) and str(item.get("role") or "") == "user" and _DATA_STORE_RE.search(str(item.get("content") or "")):
            return True
    return False


def _chat_has_owner_statements(source_context: dict[str, Any] | None) -> bool:
    for item in list((source_context or {}).get("conversation_history") or [])[:-1]:
        if isinstance(item, dict) and str(item.get("role") or "") == "user" and _is_statement(str(item.get("content") or "")):
            return True
    return False


def _chat_holds_an_exchange(source_context: dict[str, Any] | None) -> bool:
    """This chat already holds a reply: it is a conversation in progress (a continued or imported one)."""
    for item in list((source_context or {}).get("conversation_history") or [])[:-1]:
        if isinstance(item, dict) and str(item.get("role") or "") == "assistant" and str(item.get("content") or "").strip():
            return True
    return False




def _current_chat_holds_it(terms: tuple[str, ...], source_context: dict[str, Any] | None, *, joint: bool = False) -> bool:
    from core.chat_housekeeping import query_terms

    for item in list((source_context or {}).get("conversation_history") or []):
        if not isinstance(item, dict):
            continue
        # What the OWNER stated in this chat holds a fact: the question itself (the history carries
        # the current turn) and earlier questions never do. VOOL's own replies -- which may be the
        # very "I don't have that" being repaired -- count only for what "we" worked out together.
        role = str(item.get("role") or "")
        if role != "user" and not (joint and role == "assistant"):
            continue
        content = str(item.get("content") or "")
        if role == "user" and not _is_statement(content):
            continue
        # ANY shared word defers to the conversation: an answer that has to be worked out from what
        # was said here ("I fly out on 14 October and stay 6 nights" -> "What date do I fly back?")
        # shares only "fly" with its question, and the model reading this chat answers it; the
        # recall lane would say "not saved" (acceptance runner CTX-DATE-01, 2026-11-11).
        if content and set(terms) & set(query_terms(content)):
            return True
    return False


#: Stems that name the ANSWER's slot in "what's my manager's name?" / "what is my dentist called?". The saved fact
#: ("my manager is Pieter") holds the value, not the word, so counting them as terms read a saved fact as half a
#: match and answered "I don't have that saved" (served on 066ac524 after "Remember that my manager is Pieter").
_ANSWER_SLOT_STEMS = frozenset({"nam", "call"})


#: Stems of the frame around an intention ("what did i WANT to do for Pickle?", "what was I SUPPOSED to buy?"): the saved
#: fact is the thing itself ("book the vet for Pickle"). Counted as terms, "what did i want to do for Pickle?" held one
#: of two and was answered "I don't have that saved" with that fact saved (held-out set, real local model).
_INTENT_FRAME_STEMS = frozenset({"want", "wanna", "need", "suppos"})
#: The same frame said with "going to", "meant to", "planning to" ("what was i going to cancel?" with "cancel the magazine
#: subscription" saved was answered "I don't have that saved"; Mac, job 19 fold ff3afc71, heldout2 g11). Only before
#: "to": "what's the plan for saturday?" keeps "plan".
_INTENT_TO_RE = re.compile(
    r"\b(?:going|gonna|mean|meant|plan|planned|planning|intend|intended|intending)\s+(?:to\b|on\b)|\bgonna\b", re.IGNORECASE
)
_INTENT_TO_STEMS = frozenset({"going", "gonna", "mean", "meant", "plan", "intend"})


#: The word right after "what" or "which", before the verb, names the KIND of answer wanted ("what colour is my
#: shed?", "which day is the bin collection?", "what size are my boots?"). A saved fact about the thing gives the value
#: ("my shed is the green one by the apple tree") and seldom the word. Counted as a word the fact must hold, "what
#: colour is my shed?" held one of two and was answered "I don't have that saved" on the local route and on the cloud
#: route alike (the head-to-head's scenario S-101, real app). Read by its place in the question, not from a list of
#: such words; a fact that does hold the word ("which vet is my dog registered with?" / "my dogs vet is Dr Rennie")
#: still counts it.
_ASKED_KIND_RE = re.compile(
    r"\b(?:what|which)\s+([a-z]+)\s+(?:is|are|was|were|does|do|did|has|have|had|will|would|should|can|could)\b",
    re.IGNORECASE,
)


def _asked_kind(text: str) -> frozenset[str]:
    from core.chat_housekeeping import query_terms

    return frozenset(term for word in _ASKED_KIND_RE.findall(str(text or "")) for term in query_terms(word))


def _asked_terms(text: str) -> tuple[str, ...]:
    from core.chat_housekeeping import query_terms

    terms = tuple(t for t in query_terms(text) if t not in _QUESTION_WORDS)
    frame = _INTENT_FRAME_STEMS | (_INTENT_TO_STEMS if _INTENT_TO_RE.search(text) else frozenset())
    subject = tuple(t for t in terms if t not in _ANSWER_SLOT_STEMS and t not in frame)
    return subject or terms


def _memory_rows(
    question: str, terms: tuple[str, ...], session_id: str, source_context: dict[str, Any] | None
) -> tuple[list[str], list[str]] | None:
    """Saved memory rows this chat may read, split into (covering, closest); None when unknown.

    A row COVERS the question when it holds more than half of its terms: only then does saved
    memory answer it. One shared word ("test", "code") is not the fact asked for -- deferring on
    it let the model guess a parking level and hand back the bike-lock code for a forgotten hotel
    code (battery v8, 2026-11-11). Rows holding at least half the terms (and at least one) are
    the CLOSEST saved facts, offered as such and never as the answer; one shared word is never
    close (battery v9 offered the hotel and bike-lock codes for "which level is my test parking on").
    """
    try:
        from core.chat_housekeeping import _doc_stems, _term_in
        from core.context_scope import ContextAccessPolicy
        from core.memory.entries import search_relevant_memory

        policy = ContextAccessPolicy.for_request(session_id=session_id, source_context=source_context)
        covering: list[str] = []
        closest: list[tuple[int, str]] = []
        kind = _asked_kind(question)
        for row in search_relevant_memory(question, access_policy=policy, limit=6):
            text = " ".join(str(row.get("text") or "").split())
            if not text:
                continue
            stems = _doc_stems(text)
            # The word for the kind of answer counts for a row that holds it and is not held against one that
            # gives the value without it (see `_ASKED_KIND_RE`).
            wanted = tuple(term for term in terms if term not in kind or _term_in(term, stems)) or terms
            held = sum(1 for term in wanted if _term_in(term, stems))
            if held * 2 > len(wanted):
                covering.append(text)
            elif held >= min(2, len(wanted)) and held * 2 >= len(wanted) - (len(wanted) % 2):
                closest.append((held, text))
        best = max((held for held, _ in closest), default=0)
        return covering, [text for held, text in closest if held == best]
    except Exception:
        return None


#: Rows the recall lane asks the judgement about, per question: the closest in meaning, never the store.
_MEANT_ROWS = 2
#: How near (the saved-row search's own floor) a row must be before the judgement is asked about it at all.
_MEANT_FLOOR = 0.5
#: Judgements already made, by a hash of the question and the row: no text is kept here.
_MEANING_VERDICTS: dict[str, str] = {}
_MEANING_VERDICTS_KEPT = 512


#: What the naming judgement said, by a hash of the question and the facts it was shown: no text is kept here.
_NAMED_FACTS: dict[str, tuple[int, ...]] = {}


def name_the_saved_fact(
    question: str, facts: list[str], *, session_id: str, source_context: dict[str, Any] | None
) -> list[int] | None:
    """For the saved-row search on a machine with no embed model: which of ``facts`` (rows that share no word with
    the question) may be the thing asked for, as places in the list. [] when the judgement names none, None when
    nobody judged (no local model loaded and no judge bound for the turn, a failed call): the search then finds
    what the words find, as before.

    One call for the whole list (`core.intent_arbiter.arbitrate_named_fact`), on the owner's own turn outside a
    private chat, kept for the life of the process so the guards ahead of memory, the recall lane and the context
    loader ask once for a turn. It names a candidate only: every reader still puts the named fact to its own
    yes/no judgement before it is the answer or is shown as a saved fact."""
    try:
        from core.intent_arbiter import arbitrate_named_fact
        from core.request_trust import turn_speaks_for_owner

        if not facts or _private(session_id, source_context) or not turn_speaks_for_owner(
            dict(source_context or {}), str(session_id or "")
        ):
            return None
        key = hashlib.sha256("\x00".join([question, *facts]).encode()).hexdigest()
        kept = _NAMED_FACTS.get(key)
        if kept is not None:
            return list(kept)
        named = arbitrate_named_fact(question, facts, chat_id=str(session_id or ""), source_context=source_context)
        if named is None:
            return None
        places = (named - 1,) if named > 0 else ()
        if len(_NAMED_FACTS) >= _MEANING_VERDICTS_KEPT:
            _NAMED_FACTS.clear()
        _NAMED_FACTS[key] = places
        return list(places)
    except Exception:
        return None


def _rows_that_mean_it(
    question: str, session_id: str, source_context: dict[str, Any] | None
) -> tuple[list[str], bool]:
    """Saved rows that give what the question asks for in OTHER words than its own, and whether that is KNOWN.

    The words decide first (`_memory_rows`); this is asked only when no row covers the question. An embed model
    names the rows nearest the question in meaning, and near is not an answer, so each of the closest few is put to
    the loaded local model as a yes/no (core.intent_arbiter.arbitrate_meaning). ([], True): no embed model, no row
    near, or every near row judged not to be it -- the lane answers as it did. (rows, True): the saved fact asked
    for. ([], False): rows are near and nobody could judge them (no local model loaded, a failed call); the lane
    then cannot say the fact is not saved.

    Only rows that share NO word with the question are put to the judgement. A row that shares a word and does not
    cover the question stays under the covering law of `_memory_rows`: one shared word is not the fact asked for,
    and the judgement does not hold that line (served on the cut before this rule, with "my punting pole is 12
    feet" saved, "what's my pole vault record?" was judged a yes four times of four and answered "12 feet")."""
    try:
        from core.context_scope import ContextAccessPolicy
        from core.intent_arbiter import arbitrate_meaning
        from core.memory.entries import search_relevant_memory
        from core.request_trust import turn_speaks_for_owner

        if _private(session_id, source_context) or not turn_speaks_for_owner(dict(source_context or {}), str(session_id or "")):
            return [], True  # the memory lane's gate: only the owner's own turn, outside a private chat, reads by meaning
        policy = ContextAccessPolicy.for_request(session_id=session_id, source_context=source_context)
        # "Shares no word" means no CONTENT word, and each of the two word measures here has a weak spot of its own,
        # both seen on the real app (2026-11-17). The saved-row search counts "the": "who should mend the cracked
        # teapot?" was never put to the judgement about "Mrs Pellow at the china stall fixes our crockery". The
        # covering law's terms count a weak verb: "where does my ripped jacket need to go?" lost its answer when
        # "the coat with the torn lining goes to the tailor ..." was held to share "go". A word is a shared content
        # word when BOTH count it: "pole" (punting pole / pole vault), "milk", "pitch" are; "the" and "go" are not.
        from core.chat_housekeeping import _doc_stems, _term_in
        from core.memory.entries import _match_stem, keyword_tokens_filtered

        terms = _asked_terms(question)
        asked_words = {_match_stem(token) for token in keyword_tokens_filtered(question)}

        def shares_a_term(text: str) -> bool:
            stems = _doc_stems(text)
            common = asked_words & {_match_stem(token) for token in keyword_tokens_filtered(text)}
            return any(
                _term_in(term, stems) and any(_term_in(term, _doc_stems(word)) for word in common) for term in terms
            )

        near = sorted(
            (
                (float(row.get("meaning") or 0.0), text)
                for row in search_relevant_memory(
                    question, access_policy=policy, limit=24, by_meaning=True,
                    # With no embed model nothing ranks the rows by meaning: the naming judgement names a candidate.
                    name_the_fact=lambda asked, facts: name_the_saved_fact(
                        asked, facts, session_id=session_id, source_context=source_context
                    ),
                )
                for text in [" ".join(str(row.get("text") or "").split())]
                if (
                    row.get("found_by") == "named"
                    or (row.get("meaning") is not None and float(row.get("meaning") or 0.0) >= _MEANT_FLOOR)
                )
                and text and terms and not shares_a_term(text)
            ),
            reverse=True,
        )
        found: list[str] = []
        known = True
        for _, text in near[:_MEANT_ROWS]:
            if not text:
                continue
            # One judgement per question and row for the life of the process: the guards ahead of memory and this
            # lane ask about the same pair within one turn. Only a yes or a no is kept; "nobody judged" is asked again.
            key = hashlib.sha256(f"{question}\x00{text}".encode()).hexdigest()
            verdict = _MEANING_VERDICTS.get(key)
            if verdict is None:
                verdict = arbitrate_meaning(question, text, chat_id=str(session_id or ""), source_context=source_context)
                if verdict in {"yes", "no"}:
                    if len(_MEANING_VERDICTS) >= _MEANING_VERDICTS_KEPT:
                        _MEANING_VERDICTS.clear()
                    _MEANING_VERDICTS[key] = verdict
            if verdict == "yes":
                found.append(text)
            elif verdict != "no":
                known = False
        return found, known or bool(found)
    except Exception:
        return [], True


#: How many saved facts that do not hold a question's subject are put to the judgement for one question, best first.
_JUDGED_NEAR_FACTS = 3


def facts_shown_for_question(
    question: str, facts: list[str], *, may_judge: bool, session_id: str, source_context: dict[str, Any] | None
) -> dict[str, str] | None:
    """For a question, how each saved fact the word search found bears on it: ``"covers"`` (it holds every subject
    word of the question), ``"means"`` (the judgement reads it as the thing asked for, in other words), ``"other"``
    (another thing or person: not to be shown as a fact for this question) or ``"unjudged"`` (nobody could say).
    None when the text is not a question this lane reads: nothing is decided and every fact is shown as before.

    The search finds a fact by ANY word it shares with the question, and every fact found was put in front of the
    answering model as a saved fact. Measured on the real app (2026-11-18), on the start commit and on every commit
    of the fix branch: with "my bread maker is on crust setting 3." saved, "what crust setting is my toaster on?"
    was answered "Your toaster is on crust setting 3."; the exercise bike's level was given as the treadmill's and
    the campervan's key as the way into the boathouse. A shared word ranks a fact; it does not make it the fact
    asked for. So a subject match is a gate for what is shown as a saved fact, not a boost to its score.

    A fact that holds the subject is shown with no call. One that does not is the thing asked for only in other
    words ("the loaf machine" for the bread maker), which is a judgement: the loaded local model is asked whether
    the fact is about the same thing or person (`core.intent_arbiter.arbitrate_same_thing`; the recall lane's own
    question read the toaster and the bread maker as a yes), for at most `_JUDGED_NEAR_FACTS` facts, on the owner's
    own turn outside a private chat. A no keeps the fact out of this question's context; past that
    count a fact that does not hold the subject is "other". When nobody can judge (no local model, a failed call),
    and on a turn that may not ask the judgement (a private chat, a turn that is not the owner's), the fact is
    "unjudged": the caller shows it marked as possibly about another thing, so no answer is lost to a missing
    judge. The first cut of this change hid such a fact from a turn that may not ask, and "how many nights at the
    cabin?" lost the saved dates it is answered from (an existing test of the context loader said so)."""
    try:
        text = as_question(" ".join(str(question or "").split()))
        if not text.endswith("?") or len(text) > 300:
            return None
        terms = _asked_terms(text)
        if not terms:
            return None
        from core.chat_housekeeping import _doc_stems, _term_in

        subject = _subject_terms(text, terms)
        shown: dict[str, str] = {}
        asked = 0
        nobody = False
        for fact in facts:
            clean = " ".join(str(fact or "").split())
            if not clean or clean in shown:
                continue
            if all(_term_in(term, _doc_stems(clean)) for term in subject):
                shown[clean] = "covers"
                continue
            if not may_judge or nobody:
                shown[clean] = "unjudged"
                continue
            if asked >= _JUDGED_NEAR_FACTS:
                shown[clean] = "other"
                continue
            key = hashlib.sha256(f"same thing\x00{text}\x00{clean}".encode()).hexdigest()
            verdict = _MEANING_VERDICTS.get(key)
            if verdict is None:
                from core.intent_arbiter import arbitrate_same_thing

                asked += 1
                verdict = arbitrate_same_thing(text, clean, chat_id=str(session_id or ""), source_context=source_context)
                if verdict in {"yes", "no"}:
                    if len(_MEANING_VERDICTS) >= _MEANING_VERDICTS_KEPT:
                        _MEANING_VERDICTS.clear()
                    _MEANING_VERDICTS[key] = verdict
            if verdict == "yes":
                shown[clean] = "means"
            elif verdict == "no":
                shown[clean] = "other"
            else:
                nobody = True
                shown[clean] = "unjudged"
        return shown
    except Exception:
        return None


#: A question about the value NOW ("what's my balance today?") is not one a saved fact answers: a stored reading is
#: never a current one. "live" counts only as in a live score or feed: "where does Tobiah live?" with "my brother
#: Tobiah lives in Quillon" saved was asked back "the biblical Tobiah or a modern person?" (held-out set, real model).
_NOW_WORD_RE = re.compile(
    r"\b(?:now|today|tonight|currently|current|latest|still|anymore|this\s+(?:week(?:end)?|month|year|morning))\b"
    r"|\blive\s+(?:scores?|results?|prices?|feeds?|streams?|data|updates?|coverage|traffic|status)\b|\b(?:is|are)\s+[\w\s]{0,30}\blive\b(?!\s+in\b)",
    re.IGNORECASE,
)
#: Words that only name the kind of answer asked for ("which DAY does ...", "the booking CODE", "the plate NUMBER"):
#: the saved fact holds the value ("fridays", "KV-3381"), not the word.
_SLOT_STEMS = _ANSWER_SLOT_STEMS | frozenset({"day", "dat", "number", "cod", "many"})


#: A saved fact that records a change ("my dentist moved. new one is dr Okafor", "standup went back to 9:30"): it holds
#: the value as it is NOW, as far as the owner told VOOL.
_CHANGE_RE = re.compile(
    r"\b(?:moved|new|now|changed|switched|went\s+back|instead|no\s+longer|this\s+replaces|rescheduled|postponed)\b",
    re.IGNORECASE,
)
_NOW_ONLY_RE = re.compile(r"^(?:now|currently|current)$", re.IGNORECASE)
#: A saved reading (a price, a balance, a rate, a score): what it was when the owner said it, never what it is now
#: ("what's the gas price now?" with "gas went up, its now 1.89 a litre at the corner station" saved; review of job 19,
#: C3).
_READING_RE = re.compile(
    r"\b(?:prices?|costs?|balance|scores?|rates?|fares?|quotes?)\b|[$€£¥]\s?\d|\d\s?(?:usd|eur|gbp|dollars?|euros?|pounds?)\b"
    r"|\ba\s+(?:litre|liter|gallon|kwh)\b|\bper\s+(?:litre|liter|gallon|kg|kwh|share)\b",
    re.IGNORECASE,
)


#: The measure a "how ..." question asks for: "how BIG is my caravan battery?", "how LONG is the awning pole?".
_HOW_MEASURE_RE = re.compile(r"\bhow\s+([a-z]+)\b", re.IGNORECASE)


def _subject_terms(text: str, terms: tuple[str, ...]) -> tuple[str, ...]:
    """The terms a saved fact must hold to answer the question: its subject, without the words that only name the
    kind of answer (`_SLOT_STEMS`) and without the measure of a "how ..." question.

    The measure is the answer's slot too: the saved fact holds the value ("a 110 amp-hour one", "215 cm"), not the
    word. Measured on the real app: with "my caravan's leisure battery is a 110 amp-hour one." saved, "how big is
    my caravan battery?" was required to find "big" in the row, found no answer in memory, and the lanes ahead of
    memory answered it from this computer's battery state."""
    from core.chat_housekeeping import query_terms

    measure = {term for word in _HOW_MEASURE_RE.findall(text) for term in query_terms(word)}
    # "which saddle rack is MINE?": the pronoun stands for "my", which a saved fact says as "my saddle rack". Read
    # as a subject word it was required of the row, no row held it, and the question was asked back ("Did you mean
    # saddle rack, saddle rack?", measured on the real app).
    measure |= {term for word in ("mine", "ours", "yours") for term in query_terms(word)}
    measure |= _asked_kind(text)
    return tuple(
        term for term in terms if term not in _SLOT_STEMS and term not in measure and term not in {"now", "current"}
    ) or terms


def saved_memory_rows(question: str, *, session_id: str, source_context: dict[str, Any] | None) -> list[str]:
    """The saved facts that answer this question: rows holding every subject word of it. [] when none (or unknown).

    "now" asks for the value as it is; a saved fact answers that only when it records the change ("who's my dentist now
    and where are they?" with "my dentist moved. new one is dr Okafor on Brill street" saved). Any other now-word
    ("today", "latest", "still") keeps a saved fact from answering. Reads only."""
    text = " ".join(str(question or "").split())
    if not text or len(text) > 300:
        return []
    now_words = [m.group(0) for m in _NOW_WORD_RE.finditer(text)]
    if any(not _NOW_ONLY_RE.match(word) for word in now_words):
        return []
    terms = _asked_terms(as_question(text))
    if not terms:
        return []
    # The recall lane's rule (more than half the terms) picks rows to quote as saved; here a saved row takes the turn
    # from the lane that could answer it, so it must hold every word of the subject. With only "we booked the cabin
    # ... booking code KV-3381" saved, "what's the booking code for the boat?" held two of three, the file search
    # stood down and the model was shown the cabin's code; "where is the boat booking letter?" was kept from the
    # files the same way.
    from core.chat_housekeeping import _doc_stems, _term_in

    subject = _subject_terms(text, terms)
    rows = _memory_rows(text, subject, session_id, source_context)
    found = [row for row in (rows[0] if rows else []) if all(_term_in(term, _doc_stems(row)) for term in subject)]
    if now_words:
        # A row that replaced another is the value as it is now, whatever its words ("my pottery class is on
        # Mondays"). The change words also read rows an earlier build wrote with the old value in their text.
        replaced_another = _texts_that_replaced_a_row(session_id, source_context)
        found = [
            row for row in found
            if (_CHANGE_RE.search(row) or row in replaced_another) and not _READING_RE.search(row)
        ]
    return found


def _replaced_rows(session_id: str, source_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """The saved rows a later statement replaced, as this chat may read them (oldest first); [] when unknown."""
    try:
        from core.context_scope import ContextAccessPolicy
        from core.memory.entries import list_replaced_entries

        policy = ContextAccessPolicy.for_request(session_id=session_id, source_context=source_context)
        return list(list_replaced_entries(access_policy=policy))
    except Exception:
        return []


def _texts_that_replaced_a_row(session_id: str, source_context: dict[str, Any] | None) -> set[str]:
    replaced = _replaced_rows(session_id, source_context)
    if not replaced:
        return set()
    try:
        from core.context_scope import ContextAccessPolicy
        from core.memory.entries import list_memory_entries

        policy = ContextAccessPolicy.for_request(session_id=session_id, source_context=source_context)
        successors = {str(row.get("superseded_record_id") or "") for row in replaced}
        return {
            " ".join(str(row.get("text") or "").split())
            for row in list_memory_entries(access_policy=policy, limit=400)
            if str(row.get("record_id") or "") in successors
        }
    except Exception:
        return set()


#: A question about what a saved fact WAS: a past frame and a word that points before the change ("which day was
#: my pottery class before it moved?", "what did the chalet booking used to be?"). It picks the kind of question;
#: which fact it is about is read from the saved rows and their history.
_WAS_BEFORE_RE = re.compile(
    r"\b(?:was|were|did|had)\b.*\b(?:used\s+to|before|previously|originally|formerly|earlier)\b"
    r"|\b(?:used\s+to|previously|originally|formerly)\b.*\b(?:was|were|did|had|be)\b",
    re.IGNORECASE,
)
_HISTORY_POINTER_RE = re.compile(r"\b(?:used\s+to|before|previously|originally|formerly|earlier)\b", re.IGNORECASE)
_HISTORY_WORD_STEMS = frozenset(["use", "used", "before", "previous", "previously", "original", "originally", "former",
                                 "formerly", "earlier", "early"])


def replaced_values_answer(question: str, *, session_id: str, source_context: dict[str, Any] | None) -> str:
    """The answer to "what was it before?" from the history of a saved fact, or "" when no saved fact with a
    history is asked about.

    A replaced row is never read as a current value by any recall path; it is read here only, for the question
    that asks for it. Measured on the served base (94ce6169, a real local model): "Which day was my pottery class
    before it moved to Mondays?" was answered "on Fridays", a day never said, because nothing held the earlier
    values apart from the current one."""
    text = " ".join(str(question or "").split())
    if not text or len(text) > 300 or _is_statement(text) or not _WAS_BEFORE_RE.search(text):
        return ""
    replaced = _replaced_rows(session_id, source_context)
    if not replaced:
        return ""
    try:
        from core.chat_housekeeping import _doc_stems, _term_in
        from core.context_scope import ContextAccessPolicy
        from core.memory.entries import list_memory_entries

        # What the question is about stands before its pointer to the past ("which day was MY POTTERY CLASS before
        # it moved to Mondays?"); what follows describes the change, in words the saved rows need not hold.
        subject_part = _HISTORY_POINTER_RE.split(text, maxsplit=1)[0]
        terms = ()
        for part in (subject_part, text):
            terms = tuple(
                term for term in _asked_terms(as_question(part))
                if term not in _HISTORY_WORD_STEMS and term not in _SLOT_STEMS
            )
            if terms:
                break
        if not terms:
            return ""
        policy = ContextAccessPolicy.for_request(session_id=session_id, source_context=source_context)
        current = list_memory_entries(access_policy=policy, limit=400)
    except Exception:
        return ""
    before: dict[str, list[dict[str, Any]]] = {}
    for row in replaced:
        before.setdefault(str(row.get("superseded_record_id") or ""), []).append(row)
    best: tuple[int, str, list[dict[str, Any]]] | None = None
    for row in current:
        chain: list[dict[str, Any]] = []
        frontier = [str(row.get("record_id") or "")]
        while frontier:
            earlier = before.get(frontier.pop(), [])
            chain.extend(earlier)
            frontier.extend(str(item.get("record_id") or "") for item in earlier)
        if not chain:
            continue
        stems = set()
        for item in (row, *chain):
            stems |= set(_doc_stems(" ".join(str(item.get("text") or "").split())))
        held = sum(1 for term in terms if _term_in(term, stems))
        if held < len(terms):
            continue  # the fact must hold every word of the subject, as a saved row that answers a question does
        rank = (held, str(row.get("created_at") or ""))
        if best is None or rank > best[:2]:
            best = (held, str(row.get("created_at") or ""), chain)
    if best is None:
        return ""
    chain = sorted(best[2], key=lambda item: str(item.get("superseded_at") or item.get("created_at") or ""), reverse=True)
    lines = [" ".join(str(item.get("text") or "").split()) for item in chain]
    response = "Before the last change, I had saved:\n- " + lines[0]
    if len(lines) > 1:
        response += "\n\nEarlier than that:\n" + "\n".join(f"- {line}" for line in lines[1:4])
    return response


def saved_memory_answers(question: str, *, session_id: str, source_context: dict[str, Any] | None) -> bool:
    """Whether a fact the owner saved answers this question ("what's the booking code for the boat?" once the booking
    is saved): `saved_memory_rows` holds one.

    For the lanes ahead of memory that would otherwise answer it from somewhere else: on the Mac (cbc915ec, real
    local model) the clean loop searched the owner's files for it, and the entity check asked "Anselm of Canterbury
    or the actor?" about "what day does Anselm water the plants?" with that fact saved. Reads only.
    """
    if saved_memory_rows(question, session_id=session_id, source_context=source_context):
        return True
    # Asked in other words than the saved fact's own. Measured on the real app (2026-11-17, the fix mission): with
    # "the green suitcase with the broken zip goes to the repair stall at the covered market" saved, "where does my
    # damaged case need to go?" went to the file search first and its file answer was published; "which loaf should
    # I buy?" was asked back by the entity check with the rye bread fact saved; "where's my instrument stored these
    # days?" was answered with a folder of pictures while the cello's row sat in memory. The lanes ahead of memory
    # read saved rows through this guard, and it read them by shared words only.
    return _a_saved_row_means_it(question, session_id, source_context)


def _a_saved_row_means_it(question: str, session_id: str, source_context: dict[str, Any] | None) -> bool:
    """Whether a saved row that shares no word with the question gives what it asks, by the recall lane's own leg
    (`_rows_that_mean_it`: the embed model names the nearest rows, the loaded local model judges each).

    The same preconditions as `saved_memory_rows`: a question this lane could ask about, and no now-word (a stored
    fact is never the value as it is today). No embed model, nobody to judge, or a no: False, and the lanes ahead
    of memory decide as they did. Reads only."""
    text = " ".join(str(question or "").split())
    if not text or len(text) > 300 or _NOW_WORD_RE.search(text):
        return False
    asked = as_question(text)
    if not _asked_terms(asked):
        return False
    rows, _known = _rows_that_mean_it(asked, session_id, source_context)
    return bool(rows)


def saved_list_answers(question: str, *, session_id: str, source_context: dict[str, Any] | None) -> bool:
    """Whether the question asks into a list the owner keeps in memory, by the list's own name: every subject word
    of it is in the name saved rows were kept under ("what's in the flock book?" with "shearing day is 19 june (the
    flock book)" saved). Reads only.

    For the lanes ahead of memory: "what's in ..." reads as a file ask by its words, and the file search answered it
    from documents that share a word with the list's name."""
    try:
        from core.chat_housekeeping import _doc_stems, _term_in
        from core.memory.everyday_remember import row_list_subject

        text = " ".join(str(question or "").split())
        subject = _subject_terms(text, _asked_terms(as_question(text)))
        if not subject:
            return False
        for row in saved_memory_rows(text, session_id=session_id, source_context=source_context):
            name = row_list_subject(row)
            if name and all(_term_in(term, _doc_stems(name)) for term in subject):
                return True
    except Exception:
        return False
    return False


#: A name the owner's saved fact ties to them: "my brother Tobiah lives in Quillon", "my manager is Pieter", "our landlord
#: is called Feodora Lask".
#: Who a person is to the owner: the noun that makes the capitalised word after it a name ("my cousin Leontine",
#: "my manager is Duarte"). Kin come from the one list the runtime already has (core.personal_knowledge); the rest
#: are the roles people hold in someone's daily life.
_RELATION_NOUN = (
    r"(?:sister|brother|mother|father|mom|dad|mum|parent|son|daughter|child|kid|wife|husband|spouse|girlfriend|boyfriend"
    r"|partner|fiance|fiancee|aunt|uncle|cousin|niece|nephew|grand(?:mother|father|ma|pa|son|daughter)|god(?:mother|father"
    r"|son|daughter)|step(?:mother|father|son|daughter|brother|sister)|(?:mother|father|sister|brother|son|daughter)-in-law"
    r"|friend|mate|neighbou?r|colleague|co-?worker|boss|manager|landlord|landlady|tenant|lodger|flatmate|roommate|housemate"
    r"|doctor|gp|dentist|vet|nurse|teacher|tutor|coach|trainer|lawyer|solicitor|accountant|plumber|electrician|builder"
    r"|gardener|cleaner|babysitter|childminder|nanny|hairdresser|barber|mechanic|therapist|physio|optician|pharmacist"
    r"|assistant|client)"
)
#: A name a saved fact ties to the owner, in the three shapes that say so:
#:   "my cousin Leontine ..."            one noun, then the name (any noun: the name stands in apposition to it);
#:   "my allotment neighbour Wim ...", "my manager is Duarte"   a relation noun last before the name or the copula;
#:   "our lock keeper is called Osgood"  "called" / "named" after any one or two words.
#: Measured before this rule (the diagnosis probes of 2026-11-16 and the fix mission's own cases): the pattern took
#: ANY capitalised word after "my <word> <word>" -- "my bus leaves Tuesday", "our last stop is Rotterdam", "my
#: favourite painter is Vermeer" -- and a later question that named it was handled as a question about someone of the
#: owner's. A weekday or a month is never such a name, whatever stands before it (`_owned_names`).
_OWNED_NAME_RE = re.compile(
    r"\b(?:[Mm]y|[Oo]ur|[Hh]is|[Hh]er|[Tt]heir)\s+(?:"
    r"(?:[a-z][\w'-]*\s+){1,2}?(?:is|was)\s+(?:called|named)\s+"
    r"|(?:[a-z][\w'-]*\s+)??" + _RELATION_NOUN + r"s?\s+(?:(?:is|was)\s+)?"
    r"|(?!(?:favou?rite|new|old|next|last|first|other|own)\b)[a-z][\w'-]*\s+"
    r")([A-Z][\w'-]+)"
)
_NOT_A_NAME = frozenset({
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "january", "february", "march",
    "april", "may", "june", "july", "august", "september", "october", "november", "december",
})


def _owned_names(text: str) -> set[str]:
    """The names a saved fact ties to the owner, casefolded; a weekday or a month is none."""
    return {name.casefold() for name in _OWNED_NAME_RE.findall(str(text or ""))} - _NOT_A_NAME


def saved_memory_names(question: str, *, session_id: str, source_context: dict[str, Any] | None) -> bool:
    """Whether the question names someone or something a saved fact ties to the owner ("my brother Tobiah ...").

    For the entity check: "whats Tobiah's phone number?" with "my brother Tobiah lives in Quillon" saved was asked back
    "the biblical Tobiah or the Lion King character?" (held-out set, real local model); the owner means their brother,
    whether or not a saved fact holds the number. Reads only."""
    text = " ".join(str(question or "").split())
    if not text or len(text) > 300:
        return False
    try:
        from core.context_scope import ContextAccessPolicy
        from core.memory.entries import search_relevant_memory

        policy = ContextAccessPolicy.for_request(session_id=session_id, source_context=source_context)
        names = {
            name
            for row in search_relevant_memory(text, access_policy=policy, limit=6)
            for name in _owned_names(" ".join(str(row.get("text") or "").split()))
        }
    except Exception:
        return False
    asked = {word.casefold() for word in re.findall(r"[A-Za-z][\w-]*", re.sub(r"['’]s\b", "", text))}
    return bool(names & asked)


def _iso_utc(value: Any) -> str:
    """A revocation time as the conversation log writes times (ISO-8601 UTC), so they compare."""
    from datetime import datetime, timezone

    text = str(value or "").strip()
    try:
        return datetime.fromtimestamp(float(text), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return text


def _forget_times(tokens: tuple[str, ...]) -> dict[str, str]:
    """When each token was last forgotten, from the memory store's own revocation records.

    A fact said again AFTER a forget is new information the owner gave (measured: "Forget my test
    parking spot", then a later chat "my test parking spot is P-17" was never found again). Forget
    turns are not kept in the conversation log, so the time comes from `memory_revocations`
    (`revoked_at`, every chat scope). A token with no recorded time stays blocking."""
    latest: dict[str, str] = {}
    wanted = {str(token).casefold() for token in tokens if token}
    if not wanted:
        return latest
    from core import context_retrieval as cr

    for opener in (lambda: cr._open_memory_for_runtime(None), cr._open_memory):
        memory = None
        try:
            memory = opener()
            if memory is None:
                continue
            with memory._lock:
                rows = memory._conn.execute("SELECT token, revoked_at FROM memory_revocations").fetchall()
            for token, revoked_at in rows:
                key = str(token or "").casefold()
                when = _iso_utc(revoked_at)
                if key in wanted and when > latest.get(key, ""):
                    latest[key] = when
        except Exception:
            continue
        finally:
            if memory is not None:
                with contextlib.suppress(Exception):
                    memory.close()
    return {token: latest[str(token).casefold()] for token in tokens if str(token).casefold() in latest}


def _forget_state() -> tuple[tuple[str, ...], dict[str, str]] | None:
    """Every forgotten token and when it was forgotten, read once per recall; None when unknown."""
    try:
        from core.memory.revocation import revoked_tokens

        tokens = tuple(revoked_tokens(None))
        return tokens, (_forget_times(tokens) if tokens else {})
    except Exception:
        return None


def _not_forgotten(text: str, said_at: str = "", state: tuple[tuple[str, ...], dict[str, str]] | None = None) -> bool:
    """Whether a statement from another chat may be quoted: no forgotten words, no erased fact.

    Three checks, all fail-closed: (1) no word the owner told VOOL to forget, in ANY chat, occurs in
    it (exact carriers); (2) the fact it states was not erased from memory -- a forgotten saved fact
    must not come back from the chat where it was first said; (3) a subject the owner withdrew
    ("forget the hotel booking code") names this statement's subject. The withdrawal grammar's own
    matcher is chat-local by design and read too broadly here: measured on the installed build,
    "Forget the test code word" withheld "my test parking spot at work is P-17", which shares only
    the word "test" -- so the subject check here requires every content word of the withdrawn
    subject to occur in the statement."""
    try:
        from core.chat_housekeeping import query_terms
        from core.memory.carriers import carries, memory_words
        from core.memory.entries import _text_erased_at
        if state is None:
            state = _forget_state()
        if state is None:
            return False
        tokens, forgot_at = state
        if said_at and tokens:
            # A forget applies to what was said BEFORE it; a later restatement is not forgotten.
            tokens = tuple(t for t in tokens if not forgot_at.get(t) or forgot_at[t] > said_at)
        words = memory_words(text)
        if any(carries(words, token) for token in tokens):
            return False
        fact = _REMEMBER_LEAD_RE.sub("", " ".join(str(text or "").split())).strip()
        for form in (fact, fact.rstrip(".") + ".", fact.rstrip(".")):
            erased_at = _text_erased_at(form)
            # Erased after it was said (or at an unknown time): it stays forgotten. Said again
            # after the forget, it is the owner's again -- battery v9 lost a parking spot restated
            # the day after it was forgotten, because the tombstone was read as "never again".
            if erased_at is not None and (not erased_at or not said_at or erased_at >= said_at):
                return False
        stated = set(query_terms(fact))
        for token in tokens:
            subject = {t for t in query_terms(token) if t not in {"the", "my", "a", "an"}}
            if subject and subject <= stated:
                return False
        return True
    except Exception:
        return False

def _when(ts: str) -> str:
    try:
        from core.agent_runtime.housekeeping_lane import _when as housekeeping_when

        return housekeeping_when(ts)
    except Exception:
        return ""



def _best_owner_statement(result: Any, terms: tuple[str, ...], saved_folded: list[str],
                          replaced_folded: list[str] | None = None):
    """(hit, statement, ts) for the owner's best statement in another matching chat, or None.

    ``search_chats`` ranks chats by their best turn, which is often VOOL's reply; only what the
    OWNER wrote grounds a fact, so each matching chat's own user turns are read and the statement
    covering most of the question's terms wins (newest on a tie)."""
    from core.chat_housekeeping import query_terms
    from core.memory.files import conversation_log_path, load_jsonl

    chats = {hit.session_id: hit for hit in result.hits if not hit.is_current_chat}
    if not chats:
        return None
    best = None
    forget_state = None
    later: list[tuple[int, Any, str, str]] = []  # the owner's statements that share a term, in the order said
    for index, row in enumerate(load_jsonl(conversation_log_path())):
        sid = str(row.get("session_id") or "")
        hit = chats.get(sid)
        if hit is None or str(row.get("artifact_kind") or "").strip():
            continue
        said = _REMEMBER_LEAD_RE.sub("", " ".join(str(row.get("user") or "").split())).strip()
        if not said or not _is_statement(said):
            continue
        found = set(query_terms(said)) & set(terms)
        if not found:
            continue
        if any(saved and saved in said.casefold() for saved in saved_folded):
            continue
        if any(old and old in said.casefold() for old in replaced_folded or ()):
            # The owner's own earlier statement of a fact they have since changed: the saved row that held it is
            # history now, and quoting it under "You also mentioned this" gave the old value beside the current one
            # (served base 94ce6169: Wednesdays quoted under the saved Saturdays).
            continue
        if forget_state is None:
            forget_state = _forget_state()
        if not _not_forgotten(said, str(row.get("ts") or ""), forget_state):
            continue
        later.append((index, hit, said, str(row.get("ts") or "")))
        if not _covers(terms, tuple(found)):
            continue
        rank = (len(found), str(row.get("ts") or ""))
        if best is None or rank > best[0]:
            best = (rank, hit, said, str(row.get("ts") or ""))
    return None if best is None else _still_current(best[1:], later)


def _still_current(best: tuple[Any, str, str], said_in_order: list[tuple[int, Any, str, str]]):
    """The statement that is current for the subject of ``best``, by the capsule's own updated-fact chain rules.

    The best match covers the most question terms, which is often the FIRST time a thing was said ("the gym gave me
    locker 214 this season"); said in passing in a long chat it may have been replaced ("they put me in locker 87")
    and even restored ("I'm back in my old locker"). Served on 0a84ca8f, a new chat asking "what room is my desk in
    at work?" was quoted the old room. Only statements of the best match's own chat take part."""
    from core.updated_fact_marking import CURRENT_MARKER, SUPERSEDED_MARKER, compute_markers

    hit, said, ts = best
    rows = [(i, h, s, t) for i, h, s, t in said_in_order if h is hit]
    if len(rows) < 2:
        return best

    def line(text: str, stamp: str) -> str:
        return f"- user said (recorded {str(stamp)[:10]}): {text}"

    receipts = [{"line": line(s, t), "role": "user", "delivered": True, "statement_at": None, "source_sequence": i}
                for i, _h, s, t in rows]
    try:
        markers = compute_markers(receipts, [])
    except Exception:
        return best
    if markers.get(line(said, ts)) != SUPERSEDED_MARKER:
        return best
    from core.temporal_selection import anchor_terms, value_tokens

    subject = set(anchor_terms(said))
    current = [(i, h, s, t) for i, h, s, t in rows
               if markers.get(line(s, t)) == CURRENT_MARKER and subject & set(anchor_terms(s))]
    if not current:
        return best
    # the line that carries a value first ("locker 214"), then the newest
    current.sort(key=lambda row: (bool(value_tokens(row[2])), row[0]))
    _i, h, s, t = current[-1]
    return (h, s, t)


#: The statement this lane last offered to remember, per chat. A plain "yes" on the next turn
#: saves exactly that statement through the ordinary remember path; anything else drops the offer.
_PENDING_OFFERS: dict[str, str] = {}
_AFFIRMATIVE_RE = re.compile(
    r"^\s*(?:y|yes|yeah|yep|ok|okay|sure|please|yes please|please do|do it|save it|remember it|go ahead)\s*[.!]*\s*$",
    re.IGNORECASE,
)


def accept_pending_offer(text: str, *, session_id: str) -> str:
    """The remember request a "yes" answers, or '' (the offer is consumed either way)."""
    said = _PENDING_OFFERS.pop(str(session_id or ""), "")
    if said and _AFFIRMATIVE_RE.match(str(text or "")):
        return f"Remember that {said.rstrip(' .')}."
    return ""


#: The person asks VOOL to bring back something THEY gave it: "remind me ...", "do you remember /
#: know / recall ...", "can you find my ...", "did I tell you ...".
_EXPLICIT_RECALL_RE = re.compile(
    r"^\s*(?:(?:can|could|would)\s+(?:you|u)\s+)?(?:please\s+)?(?:remind\s+me(?!\s+(?:to|in|at|on|tomorrow|tonight|later|every)\b)|"
    r"(?:do|did)\s+(?:you|u)\s+(?:remember|recall|know|have)\b|(?:find|get|look\s+up)\s+my\b|did\s+i\s+(?:tell|mention|give)\b|"
    r"i\s+(?:saved|stored|kept|noted|wrote\s+down|put)\s+(?:a|an|the|my)\s+[^?.!]{2,60}?\b(?:somewhere|earlier|before)\b|"
    # "what was the wifi password i saved somewhere?", "where's the code i gave you before"
    r"(?:what|where|which)\b[^?.!]{0,60}?\bi\s+(?:saved|stored|gave\s+you|told\s+you|mentioned)\b[^?.!]{0,30}?\b(?:somewhere|earlier|before|last\s+time)\b)",
    re.IGNORECASE,
)


def _short_question_the_owner_answered(text: str, session_id: str, source_context: dict[str, Any] | None) -> bool:
    words = re.findall(r"[\w'-]+", text)
    if not text.endswith("?") or not _QUESTION_HEAD_RE.match(text) or len(words) > 8:
        return False
    if _LIVE_DOMAIN_RE.search(text) or _owned_by_another_lane(text):
        return False
    try:
        from core.chat_housekeeping import search_chats

        terms = _asked_terms(text)
        if not terms:
            return False
        rows = _memory_rows(text, terms, session_id, source_context)
        if rows and rows[0]:
            return True
        result = search_chats(text, current_session_id=session_id, limit=8)
        return bool(result.ok and _best_owner_statement(result, terms, []) is not None)
    except Exception:
        return False

#: The owner's own thing in the question: "what time is OUR standup?", "who's MY dentist now?".
_OWNER_WORD_RE = re.compile(r"\b(?:my|our|mine|ours|i|i'm|i've|we|we're|me|us)\b", re.IGNORECASE)
#: A question for a live reading or a world service (weather, prices, news, scores, opening hours, traffic).
_LIVE_WORLD_ASK_RE = re.compile(
    r"\b(?:weather|forecast|temperature|rain(?:ing)?|snow(?:ing)?|prices?|stocks?|shares?|exchange\s+rate|rates?|news|"
    r"headlines?|scores?|traffic|open|opens|opening\s+hours|hours|flights?\s+status|delayed)\b",
    re.IGNORECASE,
)


def _owners_own_subject(typed: str, rows: list[str]) -> bool:
    """Whether the question asks about the owner's own thing: a possessive, or a name a saved row ties to the owner
    ("my brother Tobiah ..." saved, "where does Tobiah live?")."""
    if _OWNER_WORD_RE.search(typed):
        return True
    owned = {name for row in rows for name in _owned_names(row)}
    asked = {word.casefold() for word in re.findall(r"[A-Za-z][\w'-]+", typed)}
    return bool(owned & asked)


#: A part of a person's name asked for: "whats Pieter's last name?", "what is Feodora's surname?".
_NAME_PART_RE = re.compile(
    r"\b([A-Za-z][\w-]+?)(?:'s|’s|s')?\s+(last\s+name|surname|family\s+name|second\s+name|middle\s+name)\b", re.IGNORECASE
)


def _name_part_never_told(typed: str, saved: list[str]) -> str | None:
    """The reply when the owner asks for a part of a name the saved facts never gave, else None.

    Mac, job 19 fold ff3afc71 (H2H memory set m-p11, real local model): with "my manager is Pieter and the quarterly
    review is on december 2" saved, "whats Pieter's last name?" was answered "Your manager's last name is Pieter."
    Quoting the saved row alone read as an answer (run 8 on 9a156361), so the reply says first that it was never told.
    Only a name a saved row writes with a capital, and only when no saved row gives that name a second capitalised word
    ("Pieter Lask")."""
    match = _NAME_PART_RE.search(" ".join(str(typed or "").split()))
    if match is None or not saved:
        return None
    name, part = match.group(1), " ".join(match.group(2).lower().split())
    named = re.compile(rf"\b{re.escape(name[:1].upper() + name[1:])}\b")
    holders = [row for row in saved if named.search(row)]
    if not holders:
        return None
    if any(re.search(rf"{named.pattern}\s+[A-Z][\w'-]+", row) for row in holders):
        return None  # a saved row may hold it; the ordinary path reads it
    if any(re.search(rf"\b{re.escape(part)}\b", row, re.IGNORECASE) for row in holders):
        return None
    shown = name[:1].upper() + name[1:]
    return (f"You haven't told me {shown}'s {part}. What I have saved about {shown}:\n"
            + "\n".join(f"- {row}" for row in holders[:3]))


def untold_name_part(question: str, *, session_id: str, source_context: dict[str, Any] | None) -> str | None:
    """For a question the recall lane hands on to the model: the reply when it asks for a part of a name the saved
    facts never gave ("whats Pieter's last name?" with only "my manager is Pieter ..." saved), else None. Reads only;
    the same owner, privacy and agent-turn gates as the recall lane."""
    typed = " ".join(str(question or "").split())
    if not typed or len(typed) > 300 or not _NAME_PART_RE.search(typed):
        return None
    if not _owner_local(source_context) or _private(session_id, source_context) or _agent_turn(session_id):
        return None
    try:
        rows = saved_memory_rows(typed, session_id=session_id, source_context=source_context)
    except Exception:
        return None
    return _name_part_never_told(typed, rows)


def _saved_rows_answer_it(typed: str, session_id: str, source_context: dict[str, Any] | None) -> list[str]:
    """Saved facts that answer the question outright, unless it points at files or an attachment.

    These rows take the question from the web, calendar and live lanes, so a world question that only shares the
    fact's words keeps its lane (independent review of job 19, C3): "who is the president?" with "my brother is
    president of the chess club" saved was answered from the saved row. A live-domain question needs the owner's own
    subject; any other question needs that or a subject of two or more words."""
    try:
        from core.clean_loop.route import points_at_files

        if points_at_files(typed) or _ATTACHMENT_RE.search(typed):
            return []
    except Exception:
        return []
    rows = saved_memory_rows(typed, session_id=session_id, source_context=source_context)
    if not rows or _owners_own_subject(typed, rows):
        return rows
    if _LIVE_WORLD_ASK_RE.search(typed):
        return []
    terms = _asked_terms(as_question(typed))
    subject = [term for term in terms if term not in _SLOT_STEMS and term not in {"now", "current"}]
    return rows if len(subject) >= 2 else []


def cross_chat_recall(
    question: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> CrossChatRecall | None:
    text = " ".join(str(question or "").split())
    if not text or len(text) > 300:
        return None
    typed = text
    explicit_recall = bool(_EXPLICIT_RECALL_RE.match(text))
    text = as_question(text)
    if not explicit_recall and (_NOT_A_SAVED_FACT_RE.search(typed) or _NOT_A_SAVED_FACT_RE.search(text)):
        return None
    if not _chat_has_owner_statements(source_context):
        # A first message that names a record by itself ("car rental reference?") asks for the
        # person's own record; later in a chat the same words are a follow-up the model answers.
        bare = bare_record_ask(text)
        if bare:
            text, explicit_recall = bare, True
    # Questions ABOUT memory or this chat ("what do you remember about me?", "what did I say in
    # this chat?") belong to the memory-command and transcript lanes, which answer inventories
    # and same-chat recall exactly.
    if _MEMORY_COMMAND_RE.search(_OTHER_CHAT_RE.sub(" ", text)):
        return None
    # "what did I ask you first" and "what was my previous question" ask to read this chat's own
    # earlier turns, which the transcript reader quotes in order (core.agent_runtime.same_chat_recall).
    # Searched across the other chats instead, it was answered "I don't have that saved" in the
    # served app while the first question sat two turns up (train port real-app run, 2026-11-12).
    # "Which day was my pottery class before it moved?" points at the past of a SAVED fact, not at this chat's
    # earlier turns: when a saved fact with a history holds the subject asked about, its replaced rows answer.
    # Served on the first cut of this change (9f910ead): the word "before" sent the question to the transcript
    # reader, which had nothing, and the model answered "on Fridays", a day never said.
    was_before = replaced_values_answer(typed, session_id=session_id, source_context=source_context)
    if not _OTHER_CHAT_RE.search(typed) and not was_before:
        from core.context_history_authority import is_same_chat_history_recall

        if is_same_chat_history_recall(typed):
            return None
    if (
        _CONVERSATION_POINTER_RE.search(_OTHER_CHAT_RE.sub(" ", text))
        and not _OTHER_CHAT_RE.search(text)
        and _chat_has_owner_statements(source_context)
        and not was_before
    ):
        return None
    # "where did we leave off?": the conversation's own course, with no fact in it to look for. In a
    # chat that holds an exchange (a continued or imported one) that exchange is the answer.
    if RESUME_RE.search(text) and not _OTHER_CHAT_RE.search(text) and _chat_holds_an_exchange(source_context) and not was_before:
        return None
    # A saved fact that answers the question outright is the owner's own record of it, and it answers ahead of a lane
    # that would only search a calendar or the web for it. H2H memory set on the real local model (88b3247e): "what
    # time is our standup?" with "standup went back to 9:30" saved got "No calendars are chosen for sync in Settings
    # yet", and "who's my dentist now and where are they?" with "my dentist moved. new one is dr Okafor on Brill
    # street" saved got "Web lookups are off". A question that points at files or an attachment keeps its own lane.
    answered_by_saved = _saved_rows_answer_it(typed, session_id, source_context)
    try:
        from core.agent_runtime.turn_planner import recall_question_needs_no_planner

        if not (recall_question_needs_no_planner(text) or _personal_question(text) or explicit_recall
                or answered_by_saved or was_before):
            # A short question about something only the owner's own chats know ("what day is
            # piano?" after "my piano lesson is on Tuesdays") is theirs too -- the world question
            # it resembles got a clarifier about a 2013 film (acceptance runner, 2026-11-11).
            if not _short_question_the_owner_answered(text, session_id, source_context):
                return None
        _LOG.info("cross-chat recall considered: %s", text[:80])
    except Exception:
        return None
    if not _owner_local(source_context) or _private(session_id, source_context) or _agent_turn(session_id):
        _LOG.info("cross-chat recall declined: owner_local=%s private=%s", _owner_local(source_context), _private(session_id, source_context))
        return None
    if was_before:
        return CrossChatRecall(response=was_before, found=True)
    claimed_elsewhere = _owned_by_another_lane(text, " ".join(str(question or "").split()), source_context) or (
        _operator_store_ask(typed)
    )
    if claimed_elsewhere and not answered_by_saved:
        return None
    if (source_context or {}).get("attachments") or (source_context or {}).get("attachment_ids"):
        return None  # a turn that carries files is about them
    if _names_a_project_file(text, source_context):
        return None  # "what do my dentist notes say?" with dentist_notes.md in this chat's project
    if not explicit_recall and _chat_works_on_a_data_store(source_context):
        return None
    from core.chat_housekeeping import query_terms, search_chats

    terms = _asked_terms(text)
    if not terms:
        return None
    if _current_chat_holds_it(terms, source_context):
        return None
    # What "we" settled, decided or chose was worked out IN the conversation, often in VOOL's own reply
    # ("We settled it: the tide table first"), which the owner-statement check above never reads. In a
    # chat that holds an exchange the decision alone ("what did we settle last time?") is that
    # exchange's to answer, and one about a subject ("what did we decide about the name?") is when the
    # chat mentions the subject; a continued chat was answered "I don't have that saved" about its own
    # last exchange (chat import run 3, 2026-11-12).
    joint = JOINT_DECISION_RE.search(text)
    if joint is not None and not _OTHER_CHAT_RE.search(text) and _chat_holds_an_exchange(source_context):
        # The words from "we" to the deciding verb ("we finally settle") ask nothing beyond the decision.
        subject = tuple(term for term in terms if term not in set(query_terms(joint.group(0))))
        if not subject or _current_chat_holds_it(subject, source_context, joint=True):
            return None
    rows = _memory_rows(text, terms, session_id, source_context)
    if rows is None:
        return None  # unknown is not "absent": the ordinary path answers
    saved, closest = rows
    if answered_by_saved and not saved:
        saved = list(answered_by_saved)  # the subject rule found them; never report them absent
    meaning_known = True
    if not saved:
        # Asked in other words than the saved fact's own: the rows closest in meaning, each judged to give what is
        # asked, are the saved fact, and the model reads them and answers in words like any other saved row.
        saved, meaning_known = _rows_that_mean_it(text, session_id, source_context)
    result = search_chats(text, current_session_id=session_id, limit=12)
    if not result.ok:
        return None
    from core.memory.everyday_remember import fact_clauses

    # A saved row may hold several facts; a statement of any one of them is the saved fact said again.
    saved_folded = list(dict.fromkeys(
        fact.casefold().rstrip(" .") for row in saved for fact in (row, *fact_clauses(row))
    ))
    replaced_folded = list(dict.fromkeys(
        fact.casefold().rstrip(" .")
        for old in _replaced_rows(session_id, source_context)
        for fact in (" ".join(str(old.get("text") or "").split()), *fact_clauses(str(old.get("text") or "")))
    ))
    best = _best_owner_statement(result, terms, saved_folded, replaced_folded)
    if best is None and saved:
        if answered_by_saved and claimed_elsewhere:
            # Handed on, a calendar or web lane ahead of the model would answer it instead of the saved fact. Any
            # other question goes on to the model, which reads the saved facts and answers in words ("whats Pieter's
            # last name?" is not answered by quoting "my manager is Pieter ...", run 8 on 9a156361).
            return CrossChatRecall(
                response="Here is what I have saved:\n" + "\n".join(f"- {row}" for row in answered_by_saved[:4]),
                found=True, chats_searched=result.chats_searched,
            )
        return None  # saved memory answers; the ordinary path reads it
    if best is not None:
        hit, said, said_ts = best
        title = " ".join(str(hit.title or "").split())
        # A chat titled by the very sentence being quoted is named by its day, not by the quote twice.
        repeats = not title or title.rstrip(" .").casefold() in said.casefold() or said.casefold() in title.casefold()
        where = ("a chat" if repeats else f"your chat “{title}”")
        if hit.project_name:
            where += f" in the {hit.project_name} project"
        when = _when(said_ts or hit.updated_at)
        mention = where + (f" on {when}" if when else "") + f":\n> {said}"
        if saved:
            response = (
                "Here is what I have saved:\n" + "\n".join(f"- {row}" for row in saved[:4])
                + f"\n\nYou also mentioned this in {mention}"
            )
        else:
            response = (
                f"I don't have that saved, but you mentioned it in {mention}"
                "\n\nWant me to remember it so every chat can use it?"
            )
            _PENDING_OFFERS[session_id] = said
        return CrossChatRecall(response=response, found=True, session_id=hit.session_id,
                               chats_searched=result.chats_searched)
    if not meaning_known:
        return None  # rows near in meaning that nobody could judge: unknown is not "absent", the ordinary path answers
    chats = f"{result.chats_searched} chat" + ("" if result.chats_searched == 1 else "s")
    projects = (
        f" or {result.projects_searched} project" + ("" if result.projects_searched == 1 else "s")
        if result.projects_searched else ""
    )
    if closest:
        response = (
            f"I don't have exactly that saved, and I looked through your other chats and projects "
            f"({chats}{projects}) without finding it. The closest thing I have saved:\n" + "\n".join(f"- {row}" for row in closest[:3])
            + "\n\nIf that's not it, tell me and I'll remember it."
        )
    else:
        response = (
            f"I don't have that saved, and I looked through your other chats and projects ({chats}{projects}) "
            "without finding it. If you tell me, I'll remember it for every chat."
        )
    return CrossChatRecall(response=response, found=False, chats_searched=result.chats_searched)


__all__ = [
    "LANE_ID",
    "CrossChatRecall",
    "accept_pending_offer",
    "as_question",
    "cross_chat_recall",
    "saved_memory_answers",
    "saved_memory_names",
    "saved_memory_rows",
    "untold_name_part",
]
