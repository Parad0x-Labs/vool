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

import logging
import math
import re
from dataclasses import dataclass
from typing import Any

LANE_ID = "cross_chat_recall"
_LOG = logging.getLogger("vool.cross_chat_recall")
_QUESTION_WORDS = frozenset({"my", "me", "i", "what", "which", "who", "whom", "whose", "when", "where", "how", "do", "did", "does", "remember", "know", "tell"})

_QUESTION_HEAD_RE = re.compile(
    r"^\s*(?:what'?s|whats|wats|what|which|who|whom|whose|when|where|why|how|do|does|did|is|are|was|were|can|could|will|would|should|have|has)\b",
    re.IGNORECASE,
)
_MEMORY_COMMAND_RE = re.compile(
    r"\b(?:memor(?:y|ies)|forg[eo]t|this\s+(?:chat|conversation|thread)|earlier|"
    r"you\s+know\s+about\s+me|about\s+me|mention(?:ed)?|said|say|told|tell\s+you|asked|wrote|"
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



def as_question(text: str) -> str:
    """The ask as the recall grammar reads it: "whats my gym locker number" -> "what's my gym locker number?".

    People type questions without the mark and with "whats"; a sentence that OPENS like a question
    is read as one, so a missing "?" never turns an own-fact question into a world question."""
    clean = " ".join(str(text or "").split())
    clean = re.sub(r"^(?:whats|wats|whta's|whats')\b", "what's", clean, flags=re.IGNORECASE)
    clean = re.sub(
        r"^(?:do|did)\s+you\s+(?:remember|know|recall)\s+(?:what\s+)?(?:is\s+)?(my\s+.+?)\s*\??$",
        r"what's \1?", clean, flags=re.IGNORECASE,
    )
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
    r"^\s*(?:what|which|where|when|who)\b[^?]{0,80}\b(?:do|did|have|had|am|was|will)\s+i\b[^?]{0,60}\?$",
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
    r"today|tomorrow|tonight|this\s+(?:week|month|morning|afternoon|evening)|next\s+week|now)\b",
    re.IGNORECASE,
)


def _owned_by_another_lane(text: str) -> bool:
    if _LIVE_DOMAIN_RE.search(text):
        return True
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


def _current_chat_holds_it(terms: tuple[str, ...], source_context: dict[str, Any] | None) -> bool:
    from core.chat_housekeeping import query_terms

    for item in list((source_context or {}).get("conversation_history") or []):
        if not isinstance(item, dict):
            continue
        # Only what the OWNER stated in this chat holds a fact: the question itself (the history
        # carries the current turn), earlier questions, and VOOL's own replies -- which may be the
        # very "I don't have that" being repaired -- never do.
        if str(item.get("role") or "") != "user":
            continue
        content = str(item.get("content") or "")
        # ANY shared word defers to the conversation: an answer that has to be worked out from what
        # was said here ("I fly out on 14 October and stay 6 nights" -> "What date do I fly back?")
        # shares only "fly" with its question, and the model reading this chat answers it; the
        # recall lane would say "not saved" (acceptance runner CTX-DATE-01, 2026-11-11).
        if content and _is_statement(content) and set(terms) & set(query_terms(content)):
            return True
    return False


def _memory_rows(
    question: str, terms: tuple[str, ...], session_id: str, source_context: dict[str, Any] | None
) -> list[str] | None:
    """Saved memory rows this chat may read that cover the question, or None when unknown."""
    try:
        from core.chat_housekeeping import query_terms
        from core.context_scope import ContextAccessPolicy
        from core.memory.entries import search_relevant_memory

        policy = ContextAccessPolicy.for_request(session_id=session_id, source_context=source_context)
        return [
            " ".join(str(row.get("text") or "").split())
            for row in search_relevant_memory(question, access_policy=policy, limit=6)
            if _covers(terms, query_terms(str(row.get("text") or "")))
        ]
    except Exception:
        return None


def _forget_times(tokens: tuple[str, ...]) -> dict[str, str]:
    """When the owner last said "forget …" naming each token, read from their own turns.

    The forget ledger records WHAT was forgotten, not when; a fact said again AFTER the forget is new
    information the owner gave (measured: "Forget my test parking spot", then a later chat "my test
    parking spot is P-17" was never found again). A token with no such turn stays blocking."""
    from core.memory.files import conversation_log_path, load_jsonl

    latest: dict[str, str] = {}
    for row in load_jsonl(conversation_log_path()):
        said = " ".join(str(row.get("user") or "").casefold().split())
        if "forget" not in said:
            continue
        ts = str(row.get("ts") or "")
        for token in tokens:
            if token and token.casefold() in said and ts > latest.get(token, ""):
                latest[token] = ts
    return latest


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
        from core.memory.entries import _text_is_erased
        if state is None:
            state = _forget_state()
        if state is None:
            return False
        tokens, forgot_at = state
        if said_at and tokens:
            # Only forgets made BEFORE the statement was said apply to it.
            tokens = tuple(t for t in tokens if not forgot_at.get(t) or forgot_at[t] > said_at)
        words = memory_words(text)
        if any(carries(words, token) for token in tokens):
            return False
        fact = _REMEMBER_LEAD_RE.sub("", " ".join(str(text or "").split())).strip()
        if _text_is_erased(fact) or _text_is_erased(fact.rstrip(".") + ".") or _text_is_erased(fact.rstrip(".")):
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

def cross_chat_recall(
    question: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> CrossChatRecall | None:
    text = " ".join(str(question or "").split())
    if not text or len(text) > 300:
        return None
    text = as_question(text)
    # Questions ABOUT memory or this chat ("what do you remember about me?", "what did I say in
    # this chat?") belong to the memory-command and transcript lanes, which answer inventories
    # and same-chat recall exactly.
    names_other_chat = bool(_OTHER_CHAT_RE.search(text))
    if _MEMORY_COMMAND_RE.search(_OTHER_CHAT_RE.sub(" ", text)) and not (
        names_other_chat and not re.search(r"\b(?:memor(?:y|ies)|forg[eo]t)\b", text, re.IGNORECASE)
    ):
        return None
    try:
        from core.agent_runtime.turn_planner import recall_question_needs_no_planner

        if not (recall_question_needs_no_planner(text) or _personal_question(text)):
            return None
        _LOG.info("cross-chat recall considered: %s", text[:80])
    except Exception:
        return None
    if not _owner_local(source_context) or _private(session_id, source_context):
        _LOG.info("cross-chat recall declined: owner_local=%s private=%s", _owner_local(source_context), _private(session_id, source_context))
        return None
    if _owned_by_another_lane(text):
        return None
    from core.chat_housekeeping import query_terms, search_chats

    terms = tuple(t for t in query_terms(text) if t not in _QUESTION_WORDS)
    if not terms:
        return None
    if _current_chat_holds_it(terms, source_context):
        return None
    saved = _memory_rows(text, terms, session_id, source_context)
    if saved is None:
        return None  # unknown is not "absent": the ordinary path answers
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
    response = (
        f"I don't have that saved, and I couldn't find it in any of your {chats}{projects}. "
        "Tell me and I'll remember it."
    )
    return CrossChatRecall(response=response, found=False, chats_searched=result.chats_searched)


__all__ = ["CrossChatRecall", "LANE_ID", "accept_pending_offer", "as_question", "cross_chat_recall"]
