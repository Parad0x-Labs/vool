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
    return bool(clean) and "?" not in clean and not _QUESTION_HEAD_RE.match(clean)


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
    r"today|tomorrow|tonight|this\s+(?:week|month|morning|afternoon|evening)|next\s+week|now)\b",
    re.IGNORECASE,
)


#: A question about what the person attached to this turn ("Is anything said in the attached video?")
#: is the attachment reader's: served tests found the recall lane answering it with "I don't have
#: that saved, but you mentioned it in a chat".
_ATTACHMENT_RE = re.compile(
    r"\b(?:attached|attachment|attachments|uploaded|(?:this|that|the)\s+(?:file|image|photo|picture|"
    r"screenshot|video|clip|audio|recording|pdf|document|doc|spreadsheet|archive|zip))\b",
    re.IGNORECASE,
)


def _agent_turn(session_id: str) -> bool:
    """A team agent's own turn never recalls the owner's other chats."""
    try:
        from core.agent_team.gate import is_agent_session

        return is_agent_session(session_id)
    except Exception:
        return False


def _owned_by_another_lane(text: str) -> bool:
    if _LIVE_DOMAIN_RE.search(text) or _ATTACHMENT_RE.search(text):
        return True
    try:
        # Files on this computer ("which pdfs are in my documents", "find my tax folder") are the
        # machine lanes' (real-app check 2026-11-12: answered "I don't have that saved").
        from core.execution.constants import (
            machine_file_search_intent,
            machine_folder_search_intent,
            machine_kind_listing_intent,
        )

        if (
            machine_kind_listing_intent(text)
            or machine_file_search_intent(text)
            or machine_folder_search_intent(text)
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
        for row in search_relevant_memory(question, access_policy=policy, limit=6):
            text = " ".join(str(row.get("text") or "").split())
            if not text:
                continue
            stems = _doc_stems(text)
            held = sum(1 for term in terms if _term_in(term, stems))
            if held * 2 > len(terms):
                covering.append(text)
            elif held >= min(2, len(terms)) and held * 2 >= len(terms) - (len(terms) % 2):
                closest.append((held, text))
        best = max((held for held, _ in closest), default=0)
        return covering, [text for held, text in closest if held == best]
    except Exception:
        return None


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



def _best_owner_statement(result: Any, terms: tuple[str, ...], saved_folded: list[str]):
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
    for row in load_jsonl(conversation_log_path()):
        sid = str(row.get("session_id") or "")
        hit = chats.get(sid)
        if hit is None or str(row.get("artifact_kind") or "").strip():
            continue
        said = _REMEMBER_LEAD_RE.sub("", " ".join(str(row.get("user") or "").split())).strip()
        if not said or not _is_statement(said):
            continue
        found = set(query_terms(said)) & set(terms)
        if not _covers(terms, tuple(found)):
            continue
        if any(saved and saved in said.casefold() for saved in saved_folded):
            continue
        if forget_state is None:
            forget_state = _forget_state()
        if not _not_forgotten(said, str(row.get("ts") or ""), forget_state):
            continue
        rank = (len(found), str(row.get("ts") or ""))
        if best is None or rank > best[0]:
            best = (rank, hit, said, str(row.get("ts") or ""))
    return None if best is None else best[1:]


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
        from core.chat_housekeeping import query_terms, search_chats

        terms = tuple(t for t in query_terms(text) if t not in _QUESTION_WORDS)
        if not terms:
            return False
        rows = _memory_rows(text, terms, session_id, source_context)
        if rows and rows[0]:
            return True
        result = search_chats(text, current_session_id=session_id, limit=8)
        return bool(result.ok and _best_owner_statement(result, terms, []) is not None)
    except Exception:
        return False

def cross_chat_recall(
    question: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> CrossChatRecall | None:
    text = " ".join(str(question or "").split())
    if not text or len(text) > 300:
        return None
    explicit_recall = bool(_EXPLICIT_RECALL_RE.match(text))
    text = as_question(text)
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
    if (
        _CONVERSATION_POINTER_RE.search(_OTHER_CHAT_RE.sub(" ", text))
        and not _OTHER_CHAT_RE.search(text)
        and _chat_has_owner_statements(source_context)
    ):
        return None
    # "where did we leave off?": the conversation's own course, with no fact in it to look for. In a
    # chat that holds an exchange (a continued or imported one) that exchange is the answer.
    if RESUME_RE.search(text) and not _OTHER_CHAT_RE.search(text) and _chat_holds_an_exchange(source_context):
        return None
    try:
        from core.agent_runtime.turn_planner import recall_question_needs_no_planner

        if not (recall_question_needs_no_planner(text) or _personal_question(text) or explicit_recall):
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
    if _owned_by_another_lane(text):
        return None
    if (source_context or {}).get("attachments") or (source_context or {}).get("attachment_ids"):
        return None  # a turn that carries files is about them
    if not explicit_recall and _chat_works_on_a_data_store(source_context):
        return None
    from core.chat_housekeeping import query_terms, search_chats

    terms = tuple(t for t in query_terms(text) if t not in _QUESTION_WORDS)
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
    result = search_chats(text, current_session_id=session_id, limit=12)
    if not result.ok:
        return None
    saved_folded = [row.casefold().rstrip(" .") for row in saved]
    best = _best_owner_statement(result, terms, saved_folded)
    if best is None and saved:
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


__all__ = ["LANE_ID", "CrossChatRecall", "accept_pending_offer", "as_question", "cross_chat_recall"]
