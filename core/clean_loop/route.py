"""Should this turn take the clean local path? One pure decision, in a fixed order.

1. The flag `VOOL_CLEAN_LOCAL_LOOP` is on (default OFF).
2. The turn is not RED: no pay/send/delete/post/cancel-style verb and no money words. VOOL has no
   text-level risky-action classifier to reuse (its consent gates sit at the effect, which this loop
   never reaches), so this is a small verb floor. RED turns keep the normal ceremony. Nor is it a
   write (create, move, rename, save...), unless the changes flag is on (then the change is shown as
   a card first). A question ("when do i have to pay the plumber by") asks for facts, so its verb
   counts as neither; money words are RED in any form.
3. Nothing else owns the turn: not a planned sub-turn, a resumed checkpoint, a browser workflow, a
   project-bound chat or a chat with an active goal workflow, and it is the owner's own local turn.
4. It is about the owner's own files or computer (or a short follow-up to a clean-loop turn, or it
   shares a distinctive word with the name of one of the owner's files), and not a live/online
   question, which the online lanes own.
5. The brain that would answer is a local model (resolved last: it is the only costly check).
"""
from __future__ import annotations

import os
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

FLAG_ENV = "VOOL_CLEAN_LOCAL_LOOP"
_TRUTHY = {"1", "true", "yes", "on"}

_RED_RE = re.compile(
    r"\b(?:pay|paying|send|sending|transfer|wire|buy|purchase|sell|trade|swap|donate|tip\s+(?:him|her|them|the)|"
    r"order\s+(?:me|a|an|some)|book\s+(?:me|a|an)|reserve|subscribe|unsubscribe|cancel|"
    r"delete|deleting|remove|removing|erase|wipe|trash|shred|purge|clear\s+out|clean\s+out|clean\s+up|"
    r"empty\s+(?:the\s+)?(?:trash|bin)|get\s+rid|uninstall|install|kill|shut\s*down|reboot|restart\s+(?:it|my|the)|"
    r"format\s+(?:the|my|this|that)\s+(?:drive|disk|usb|computer|laptop|mac|pc)|"
    r"post\s+(?:it|this|that|to|on|a|an|my)|publish|tweet|share|upload|email\s+(?:it|this|that|him|her|them|to)|"
    r"message\s+(?:him|her|them)|sign\s+(?:it|this|that|the|up|in|me)|submit|approve|authori[sz]e)\b",
    # (An amount alone is not RED: "find the €129 invoice" asks about a file. The verbs above still
    # catch "send €50 to anna"; Fable consult 2026-10-28.)
    re.I,
)
# Destructive verbs stay RED in a question too: "is it ok to delete everything in Downloads" is an
# ask to delete in polite form (review 2026-10-28, N4). Only the everyday verbs ("when do i pay the
# plumber", "when did we move the audit") are exempt in a question.
_DESTRUCTIVE_RE = re.compile(
    r"\b(?:delete|deleting|remove|removing|erase|wipe|trash|shred|purge|clear\s+out|clean\s+out|"
    r"empty\s+(?:the\s+)?(?:trash|bin)|get\s+rid|uninstall|kill|shut\s*down|reboot|format)\b",
    re.I,
)
_MONEY_RE = re.compile(r"\b(?:wallet|crypto|bitcoin|btc|eth|solana|usdc|credit\s+card|bank\s+account)\b", re.I)
# A question asks for facts: "when do i have to pay the plumber by", "when did we move the audit to".
# The verb inside it is the subject of the question, not an order (Mac hold-out 2026-10-28: both were
# sent to the old pipeline and answered wrong). The loop only reads; a change still needs its card,
# the owner's yes and VOOL's own approval. Money words stay RED in any form.
_QUESTION_RE = re.compile(
    r"^\s*(?:when|what|what'?s|whats|which|where|who|whose|why|how\s+(?:much|many|long|big|old|often|far)|"
    r"did|was|were|am|is|are|have|has)\b",
    re.I,
)
_WRITE_RE = re.compile(
    r"\b(?:move|moving|rename|renaming|copy|copying|duplicate|create|creating|write|save|saving|put|stick|"
    r"make\s+(?:me\s+)?(?:a|an|new)|set\s+up|organi[sz]e|tidy|sort\s+out|edit|append|add\s+(?:a|an|it|this|that|to))\b",
    re.I,
)
# "where did i put my cv", "what did i write", "it didnt save": the owner recalling, not asking for a change.
_RECALL_RE = re.compile(
    r"\b(?:did\s+(?:i|you)|i|i'?ve|i\s+had|didn'?t|did\s+not|wasn'?t)\s+(?:just\s+|already\s+)?"
    r"(?:move|rename|copy|create|write|save|put|stick|make|edit)\w*",
    re.I,
)
_ONLINE_RE = re.compile(
    r"\b(?:weather|forecast|news|headlines|stock|share\s+price|exchange\s+rate|price\s+of|search\s+(?:the\s+)?(?:web|internet|online)|"
    r"google|online|website|https?://|www\.)\b",
    re.I,
)
_TOPIC_RE = re.compile(
    r"\b(?:files?|folders?|pdfs?|csv|docs?|documents?|downloads?|desktop|photos?|pictures?|pics?|images?|screenshots?|"
    r"videos?|movies?|music|songs?|notes?|to\s*do|list|invoices?|receipts?|bills?|owe|cv|resume|tax|bank|statement|boarding\s+pass|"
    r"tickets?|flights?|boarding|bookings?|reservations?|itinerar(?:y|ies)|meetings?|minutes|reports?|insurance|lease|rent|"
    r"contracts?|payslips?|warrant(?:y|ies)|appointments?|recipes?|disk|drive|storage|space|ram|memory|cpu|processor|specs?|"
    r"screen|monitors?|resolution|process(?:es)?|running|background|services?|logs?|errors?|slow|sluggish|junk|temp|"
    r"privacy|private|left\s+(?:this|my)\s+(?:computer|mac|laptop|machine)|sent\s+to\s+the\s+cloud)\b",
    re.I,
)
# The machine itself counts only when it is the owner's: "my laptop", "this mac", not "mac or windows".
_MACHINE_RE = re.compile(r"\b(?:my|this|the)\s+(?:own\s+)?(?:laptop|computer|mac(?:book)?|pc|machine)\b", re.I)
_OWNED_RE = re.compile(
    r"\b(?:my|mine|our|ours|did\s+(?:i|we)|i\s+(?:wrote|saved|downloaded|took|put|keep|named|made|have|had|got|need|owe))\b",
    re.I,
)
_WORD_RE = re.compile(r"[a-z0-9]+")
_ASK_STOP = frozenset(
    {
        "the", "and", "for", "with", "what", "whats", "when", "where", "which", "who", "whose", "why", "how", "much",
        "many", "long", "does", "did", "was", "were", "are", "have", "has", "had", "this", "that", "these", "those",
        "there", "here", "from", "into", "about", "your", "you", "can", "could", "would", "should", "will", "get",
        "goes", "going", "need", "tell", "show", "find", "file", "files", "folder", "new", "old", "last", "first",
        "next", "some", "any", "all", "one", "two", "best", "better", "good", "make", "made", "know", "like",
        "want", "please", "thing", "things", "stuff", "today", "week", "time", "copy", "final", "untitled", "img",
        "image", "screenshot", "document", "notes", "note", "draft",
    }
)


def _stem(word: str) -> str:
    return word[:-1] if len(word) > 4 and word.endswith("s") else word


def ask_words(text: str) -> set[str]:
    """The distinctive words of an ask (four letters or more, or a word with a digit like "q3")."""
    words = set()
    for word in _WORD_RE.findall(str(text or "").lower()):
        if word in _ASK_STOP or word.isdigit():
            continue
        if len(word) >= 4 or (len(word) >= 2 and any(ch.isdigit() for ch in word)):
            words.add(_stem(word))
    return words


def names_owner_file(text: str, file_words: Callable[[], set[str]] | None) -> bool:
    """True when the ask shares a distinctive word with the name of one of the owner's own files
    ("lisbon" and "Lisbon boarding pass.pdf", "balcony" no, "flat" and "flat viewing notes.md")."""
    if file_words is None:
        return False
    words = ask_words(text)
    if not words:
        return False
    try:
        names = file_words()
    except Exception:
        return False
    return bool(words & names)
_SELF_RE = re.compile(r"\b(?:my\s+name|call\s+me|who\s+am\s+i|about\s+me)\b|^\s*(?:please\s+)?(?:remember|forget)\b", re.I)
_APPROVAL_RE = re.compile(r"^\s*(?:yes|yep|yeah|ok|okay|sure|go\s+ahead|do\s+it|approve[d]?|confirm(?:ed)?|no|nope|stop)\b", re.I)

# A reply to a change card is a whole short answer, nothing more: "ok, but not the pdfs", "yes?" or
# "yes and also..." are new asks, never an approval (independent review 2026-10-28).
_YES_WORDS = frozenset({
    "yes", "yes please", "yep", "yeah", "yup", "ok", "okay", "sure", "go ahead", "do it", "please do",
    "ok do it", "okay do it", "yes do it", "yes go ahead", "ok go ahead", "sounds good",
})
_NO_WORDS = frozenset({
    "no", "nope", "no thanks", "no thank you", "stop", "don't", "dont", "leave it", "never mind",
    "nevermind", "forget it", "no leave it",
})
_UNDO_RE = re.compile(
    r"(?:please )?(?:undo(?: (?:that|it|this|everything|all of it))?|put (?:them|it|everything|all of it) back|"
    r"revert(?: (?:that|it))?|take (?:that|it) back|change it back)(?: please)?",
)


def _bare(text: str) -> str:
    """Lower case, commas dropped, spaces collapsed, trailing full stops and "!" off. A "?" stays,
    so a question never reads as an answer."""
    return " ".join(str(text or "").lower().replace(",", " ").split()).rstrip(".! ")


_FOLLOW_UP_WORDS = 14
_FOLLOW_UP_TTL_S = 900.0
_RECENT: OrderedDict[str, float] = OrderedDict()
_RECENT_LOCK = threading.Lock()


@dataclass(frozen=True)
class RouteDecision:
    admit: bool
    reason: str
    brain: Any = None


CHANGES_ENV = "VOOL_CLEAN_LOOP_CHANGES"


def changes_enabled(env: Mapping[str, str] | None = None) -> bool:
    """Write asks (new folder, move, rename, new file) come here only with their own flag: the change
    is rehearsed, shown as a card and undoable (core.clean_loop.changes). Default OFF."""
    return str((env if env is not None else os.environ).get(CHANGES_ENV) or "").strip().lower() in _TRUTHY


def flag_on(env: Mapping[str, str] | None = None) -> bool:
    return str((env if env is not None else os.environ).get(FLAG_ENV) or "").strip().lower() in _TRUTHY


def is_question(text: str) -> bool:
    return bool(_QUESTION_RE.search(str(text or "")))


# Sending something out of this computer is RED even asked as a question ("can you upload my cv to
# dropbox?", "is it ok to share these with my landlord"): the owner's things leave. Recalling it ("what
# did i send to anna") is a question about the past and stays a question.
_OUTWARD_RE = re.compile(r"\b(?:upload|send|share|post|publish|tweet|email|forward)\b", re.I)
_OUTWARD_RECALL_RE = re.compile(
    r"\b(?:did|have|had)\s+(?:i|we|you)\s+(?:ever\s+|already\s+|just\s+|last\s+)?"
    r"(?:upload|send|share|post|publish|tweet|email|forward)\b",
    re.I,
)


def is_red(text: str) -> bool:
    value = str(text or "")
    if _MONEY_RE.search(value) or _DESTRUCTIVE_RE.search(value):
        return True
    if _OUTWARD_RE.search(_OUTWARD_RECALL_RE.sub(" ", value)):
        return True
    return bool(_RED_RE.search(value)) and not is_question(value)


def is_write(text: str) -> bool:
    value = str(text or "")
    return bool(_WRITE_RE.search(_RECALL_RE.sub(" ", value))) and not is_question(value)


def change_reply(text: str) -> str:
    """"yes", "no" or "undo" for a bare reply to a change card; "" for anything else."""
    value = _bare(text)
    if _UNDO_RE.fullmatch(value):
        return "undo"
    if value in _NO_WORDS:
        return "no"
    if value in _YES_WORDS:
        return "yes"
    return ""


# "how much of my stuff went to online AI today" asks about this computer, not the web: the privacy
# meter answers it locally (Mac demo 2026-10-28: it was sent to the web lanes and refused).
_PRIVACY_ASK_RE = re.compile(
    r"\b(?:privacy|private|online\s+(?:ai|models?)|cloud\s+(?:ai|models?)|"
    r"(?:went|go|gone|sent|send|left|leave|leaves|shared?)\s+(?:to\s+)?(?:the\s+)?(?:online|cloud|internet|openai|anthropic|chatgpt|claude))\b",
    re.I,
)


# "search online for the privacy policy of my bank" asks for the web, not about what left this computer.
_NOT_PRIVACY_RE = re.compile(
    r"\b(?:search|look\s+up|google|browse|polic(?:y|ies)|website|https?://|www\.)\b", re.I
)


def is_privacy_ask(text: str) -> bool:
    value = str(text or "")
    return bool(_PRIVACY_ASK_RE.search(value)) and not _NOT_PRIVACY_RE.search(value)


def is_online_ask(text: str) -> bool:
    value = str(text or "")
    return bool(_ONLINE_RE.search(value)) and not is_privacy_ask(value)


def asks_about_own_files_or_computer(text: str) -> bool:
    value = str(text or "")
    if _SELF_RE.search(value):
        return False
    return bool(_TOPIC_RE.search(value) or _MACHINE_RE.search(value) or _OWNED_RE.search(value))


def note_clean_turn(session_id: str) -> None:
    """Remember that this session's last turn ran here, so a short follow-up stays on the same path."""
    if not session_id:
        return
    with _RECENT_LOCK:
        _RECENT[session_id] = time.monotonic()
        _RECENT.move_to_end(session_id)
        while len(_RECENT) > 256:
            _RECENT.popitem(last=False)


def _is_follow_up(text: str, session_id: str) -> bool:
    value = str(text or "")
    if not session_id or len(value.split()) > _FOLLOW_UP_WORDS or _APPROVAL_RE.search(value):
        return False
    with _RECENT_LOCK:
        seen = _RECENT.get(session_id)
    return seen is not None and time.monotonic() - seen < _FOLLOW_UP_TTL_S


def _owner_context_blocks(source_context: Mapping[str, Any], session_id: str) -> str:
    if source_context.get("planned_subturn"):
        return "planned_subturn"
    if source_context.get("turn_routing_retry") or source_context.get("runtime_checkpoint_resumed"):
        return "resumed_turn"
    if str(source_context.get("_trusted_project_id") or "").strip():
        return "project_owns_turn"
    try:
        from core.goal_workflow import load_active_goal_for_session

        if session_id and load_active_goal_for_session(session_id):
            return "workflow_owns_turn"
    except Exception:
        pass
    try:
        from core.request_trust import request_is_owner_local

        if not request_is_owner_local(dict(source_context)):
            return "not_owner_local"
    except Exception:
        return "not_owner_local"
    return ""


def decide(
    text: str,
    *,
    source_context: Mapping[str, Any] | None,
    session_id: str = "",
    resolve_brain: Callable[[], Any] | None = None,
    browser_turn: bool = False,
    env: Mapping[str, str] | None = None,
    file_words: Callable[[], set[str]] | None = None,
) -> RouteDecision:
    if not flag_on(env):
        return RouteDecision(False, "flag_off")
    if is_red(text):
        return RouteDecision(False, "red_turn")
    if is_write(text) and not changes_enabled(env):
        return RouteDecision(False, "write_turn")
    if browser_turn:
        return RouteDecision(False, "browser_turn")
    blocked = _owner_context_blocks(dict(source_context or {}), session_id)
    if blocked:
        return RouteDecision(False, blocked)
    if is_online_ask(text):
        return RouteDecision(False, "online_ask")
    if not (
        asks_about_own_files_or_computer(text)
        or is_privacy_ask(text)
        or _is_follow_up(text, session_id)
        or names_owner_file(text, file_words)
    ):
        return RouteDecision(False, "not_about_own_files")
    brain = resolve_brain() if resolve_brain is not None else None
    if brain is None:
        return RouteDecision(False, "brain_not_local")
    return RouteDecision(True, "admitted", brain)


__all__ = [
    "CHANGES_ENV",
    "FLAG_ENV",
    "change_reply",
    "changes_enabled",
    "RouteDecision",
    "asks_about_own_files_or_computer",
    "decide",
    "flag_on",
    "ask_words",
    "is_online_ask",
    "is_privacy_ask",
    "is_question",
    "names_owner_file",
    "is_write",
    "is_red",
    "note_clean_turn",
]
