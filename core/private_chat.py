"""Private chat: a per-chat mode in which VOOL saves nothing from the conversation.

The user turns it on in plain words ("private chat", "don't record this", "off the record") and
off again the same way ("turn recording back on", "end private chat"). While it is on, no turn of
that chat reaches any store a later turn, a reload or a recall can read: not the conversation log,
not the staged transcript row, not session summaries, not memory extraction, not learned
heuristics. The chat page marks every message of such a turn, the user's and VOOL's, with a small
ghost so the person can see what was kept out of the record.

This module is the ONE owner of that mode:

* **store** -- the runtime SQLite database, table ``private_chat_sessions``. It holds the chat id
  and the time the mode was turned on, nothing else (never a word of the conversation). The mode
  survives a restart: a chat that was private before VOOL restarted is still private after it.
* **commands** -- :func:`parse_private_command` reads the turn deterministically, so "nothing is
  saved" is only said after the switch is really on. A command clause inside a longer message
  ("don't record this, but what do you think of ...") turns the mode on for that same turn and
  the rest of the message is answered privately.
* **fail closed** -- when the store cannot be read, :func:`is_private` answers True: a chat whose
  mode is unknown is treated as private, so an unreadable switch never records a conversation the
  user asked to keep out of the record.

Later questions about private turns find nothing, because nothing was written; the memory law
NO RECORD = REFUSE then answers them.
"""
from __future__ import annotations

import logging
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from storage.db import get_connection

__all__ = [
    "PRIVATE_TASK_SUMMARY",
    "PRIVATE_TURN_KEY",
    "REPLY_REMEMBER_REFUSED",
    "PrivateCommand",
    "end_private_request",
    "is_private",
    "mark_request_private",
    "observe_private_door",
    "observe_private_turn",
    "parse_private_command",
    "private_event_details",
    "private_prompt_block",
    "private_request_session",
    "private_since",
    "request_is_private",
    "session_id_from",
    "set_private",
    "turn_is_private",
]

_log = logging.getLogger(__name__)

#: Stamped on a turn's source_context once the turn is known to be private. Every persistence
#: writer reads it through :func:`turn_is_private` (core.persistent_memory checks it before any
#: write), and the chat door copies it onto the response commit for the page's ghost marks.
PRIVATE_TURN_KEY = "private_chat"
#: Stamped by the chat door when THIS message turned private chat on, so the turn's reply still
#: says "private chat is on" rather than "already on".
SWITCHED_ON_KEY = "private_chat_switched_on"
#: What a task row stores for a private turn instead of the turn's words.
PRIVATE_TASK_SUMMARY = "private chat"

# ── storage ──────────────────────────────────────────────────────────────────

_DDL = """
CREATE TABLE IF NOT EXISTS private_chat_sessions (
  session_id TEXT PRIMARY KEY,
  started_at TEXT NOT NULL
)
"""
_LOCK = threading.Lock()


def _utcnow() -> str:
    from core.memory.files import utcnow

    return utcnow()


def _clean(session_id: Any) -> str:
    return str(session_id or "").strip()


def private_since(session_id: str | None) -> str | None:
    """When the chat's private mode was turned on, or None when it is off.

    Raises on a store fault; :func:`is_private` is the fail-closed reader.
    """
    sid = _clean(session_id)
    if not sid:
        return None
    conn = get_connection()
    try:
        conn.execute(_DDL)
        row = conn.execute(
            "SELECT started_at FROM private_chat_sessions WHERE session_id = ? LIMIT 1", (sid,)
        ).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row else None


def is_private(session_id: str | None) -> bool:
    """True when the chat is private. An unreadable store answers True (fail closed)."""
    sid = _clean(session_id)
    if not sid:
        return False
    try:
        return private_since(sid) is not None
    except Exception:
        _log.warning("private chat store unreadable; treating chat as private", exc_info=True)
        return True


def set_private(session_id: str, on: bool) -> bool:
    """Turn the chat's private mode on or off and return the state the store now holds.

    Raises on a store fault, so a caller never reports a switch that did not happen.
    """
    sid = _clean(session_id)
    if not sid:
        raise ValueError("a chat id is required to change private chat")
    with _LOCK:
        conn = get_connection()
        try:
            conn.execute(_DDL)
            if on:
                conn.execute(
                    "INSERT OR IGNORE INTO private_chat_sessions (session_id, started_at) VALUES (?, ?)",
                    (sid, _utcnow()),
                )
            else:
                conn.execute("DELETE FROM private_chat_sessions WHERE session_id = ?", (sid,))
            conn.commit()
        finally:
            conn.close()
    return private_since(sid) is not None


# ── the turn ─────────────────────────────────────────────────────────────────


def session_id_from(source_context: Mapping[str, Any] | None, session_id: str | None = None) -> str:
    """The chat this turn belongs to: the server-bound chat id first, then the session id."""
    context = source_context if isinstance(source_context, Mapping) else {}
    for value in (context.get("chat_id"), session_id, context.get("session_id")):
        sid = _clean(value)
        if sid:
            return sid
    try:
        from core.turn_contract import TURN_REQUEST_KEY

        request = context.get(TURN_REQUEST_KEY)
        sid = _clean(getattr(request, "session_id", "") if request is not None else "")
        if sid:
            return sid
    except Exception:
        pass
    return _clean(context.get("runtime_session_id"))


# Requests known to be private. Writers that run inside a private turn's request but never see its
# context (the answer's A7 binding, background extraction started from the door, a worker thread
# carrying the copied context variables) ask by the bound request id. Bounded; entries age out.
_PRIVATE_REQUESTS: dict[str, tuple[float, str]] = {}
_PRIVATE_REQUESTS_MAX = 512
_PRIVATE_REQUEST_TTL_S = 6 * 3600.0


def _current_request_id() -> str:
    try:
        from core.semantic.semantic_admissions import current_request_id

        return str(current_request_id() or "").strip()
    except Exception:
        return ""


def mark_request_private(request_id: str | None = None, *, session_id: str | None = None) -> None:
    """Record that the bound (or named) request belongs to a private turn of ``session_id``."""
    import time

    rid = _clean(request_id) or _current_request_id()
    if not rid:
        return
    now = time.monotonic()
    with _LOCK:
        previous = _PRIVATE_REQUESTS.get(rid)
        _PRIVATE_REQUESTS[rid] = (now, _clean(session_id) or (previous[1] if previous else ""))
        if len(_PRIVATE_REQUESTS) > _PRIVATE_REQUESTS_MAX:
            stale = [key for key, (seen, _sid) in _PRIVATE_REQUESTS.items() if now - seen > _PRIVATE_REQUEST_TTL_S]
            for key in stale:
                _PRIVATE_REQUESTS.pop(key, None)
            while len(_PRIVATE_REQUESTS) > _PRIVATE_REQUESTS_MAX:
                _PRIVATE_REQUESTS.pop(next(iter(_PRIVATE_REQUESTS)), None)


def request_is_private(request_id: str | None = None) -> bool:
    rid = _clean(request_id) or _current_request_id()
    return bool(rid) and rid in _PRIVATE_REQUESTS


def private_request_session(request_id: str | None = None) -> str:
    """The chat a private request belongs to, or '' when unknown or not private."""
    rid = _clean(request_id) or _current_request_id()
    entry = _PRIVATE_REQUESTS.get(rid) if rid else None
    return entry[1] if entry else ""


def end_private_request(request_id: str) -> int:
    """Request end for a private turn: blank the words its working ledgers held while it ran.

    No-op for an ordinary request. Returns how many records were scrubbed.
    """
    rid = _clean(request_id)
    if not rid or not request_is_private(rid):
        return 0
    from core.conductor.obligation_ledger import scrub_request_texts

    return scrub_request_texts(rid)


def turn_is_private(source_context: Mapping[str, Any] | None, session_id: str | None = None) -> bool:
    """True when this turn must leave no record: stamped private, bound to a private request, or
    its chat is private now."""
    context = source_context if isinstance(source_context, Mapping) else {}
    if context.get(PRIVATE_TURN_KEY):
        return True
    if request_is_private():
        return True
    sid = session_id_from(context, session_id)
    return bool(sid) and is_private(sid)


#: Event detail keys a private turn's stored runtime events may keep: identities and states only.
_EVENT_KEEP_KEYS = frozenset(
    {
        "turn_key", "client_turn_id", "task_id", "turn_id", "request_id", "checkpoint_id",
        "task_class", "status", "stage", "state", "tool", "tool_name", "provider", "model",
        "ok", "success", "duration_ms", "elapsed_ms", "attempt", "seq",
    }
)


def private_event_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    """A private turn's runtime-event details reduced to what names no words of the turn."""
    kept: dict[str, Any] = {"private_chat": True}
    for key, value in dict(details or {}).items():
        if key not in _EVENT_KEEP_KEYS:
            continue
        if isinstance(value, (bool, int, float)) or (
            isinstance(value, str) and len(value) <= 120 and "\n" not in value
        ):
            kept[key] = value
    return kept


# ── commands ─────────────────────────────────────────────────────────────────

ON = "on"
OFF = "off"
STATUS = "status"

#: Polite words a command may start or end with. Anything else before the phrase makes it part of
#: a sentence about something else ("I said don't record this"), which is not a command.
_LEAD = r"(?:(?:hey|hi|ok|okay|so|and|also|please|pls|vool|now|from\s+now\s+on|for\s+now|right|alright|(?:can|could|would|will)\s+you)[,\s]+)*"
_TAIL = r"(?:[,\s]+(?:please|pls|thanks|thank\s+you|ok|okay|now|for\s+now|from\s+now\s+on|vool))*"
_THIS = (
    r"(?:this|that|any\s+of\s+this|anything|everything|me|us|our\s+(?:chat|conversation|talk)"
    r"|this\s+(?:chat|conversation|talk|one|part|bit)|my\s+(?:messages|chats?|words))"
)
#: Common slips of the keyboard for the verbs a person uses to ask not to be recorded.
_RECORD = r"(?:record|recrod|reocrd|recrd|save|svae|log|store|keep)"

_ON_PHRASES = (
    r"(?:let'?s\s+|can\s+we\s+|we\s+should\s+)?go\s+(?:private|incognito|off\s+the\s+record)",
    r"(?:let'?s\s+)?(?:start|open|begin|have|make\s+this|make\s+it)\s+(?:a\s+)?private\s+(?:chat|conversation|mode|talk)",
    r"(?:turn|switch|put)\s+(?:on\s+)?(?:the\s+)?(?:private|incognito)\s+(?:chat|mode)(?:\s+on)?",
    r"(?:switch|change)\s+to\s+(?:a\s+)?(?:private|incognito)(?:\s+(?:chat|mode))?",
    r"(?:private|incognito)\s+(?:chat|mode)(?:\s+(?:on|please|now))?",
    r"(?:this\s+is\s+|keep\s+(?:this|it)\s+|let'?s\s+keep\s+(?:this|it)\s+|(?:let'?s\s+|can\s+we\s+)?talk\s+)?off\s+the\s+record",
    rf"(?:don'?t|do\s+not|dont|please\s+don'?t)\s+{_RECORD}\s+{_THIS}",
    rf"(?:can|could|would)\s+you\s+not\s+(?:{_RECORD}|remember)\s+{_THIS}",
    r"i\s+(?:don'?t|do\s+not|dont)\s+want\s+(?:this|that|it|any\s+of\s+this|this\s+(?:chat|conversation)|my\s+messages)"
    r"\s+(?:to\s+be\s+)?(?:saved|recorded|logged|stored|remembered|kept)",
    r"(?:(?:can|could)\s+we\s+|let'?s\s+|i\s+(?:want|need|would\s+like|'d\s+like)\s+to\s+)?(?:talk|speak|chat)\s+(?:in\s+private|privately)",
    r"make\s+(?:this|it|this\s+(?:chat|conversation))\s+private",
    r"(?:enable|activate|use)\s+(?:the\s+)?(?:private|incognito)\s+(?:chat|mode)",
    r"i\s+(?:want|need|would\s+like|'d\s+like)\s+(?:a\s+|to\s+have\s+a\s+)?private\s+(?:chat|conversation|talk)",
    r"(?:keep\s+(?:this|it)\s+|let'?s\s+keep\s+(?:this|it)\s+|this\s+(?:is|stays)\s+)between\s+(?:us|you\s+and\s+me)(?![ ]+and\b)",
    r"(?:don'?t|do\s+not|dont)\s+(?:remember|memorize)\s+(?:any\s+of\s+this|anything|this\s+(?:chat|conversation|talk))",
    r"(?:stop|pause)\s+(?:recording|saving|logging)(?:\s+(?:this|it|me|us|our\s+chat|this\s+chat))?",
    r"(?:no|without)\s+(?:recording|saving|logging)(?:\s+(?:this|please|now))?",
    r"keep\s+(?:this|it)\s+private",
)
_OFF_PHRASES = (
    r"(?:turn|switch|put)\s+(?:the\s+)?(?:recording|saving|logging|memory|record)\s+(?:back\s+)?on",
    r"(?:turn|switch)\s+(?:back\s+)?on\s+(?:the\s+)?(?:recording|saving|logging|memory)(?:\s+again)?",
    r"(?:recording|saving)\s+(?:back\s+)?on(?:\s+again)?",
    r"(?:disable|deactivate)\s+(?:the\s+)?(?:private|incognito)(?:\s+(?:chat|mode))?",
    r"(?:turn|switch)\s+(?:off|out\s+of)\s+(?:the\s+)?(?:private|incognito)\s+(?:chat|mode)",
    r"(?:turn|switch)\s+(?:the\s+)?(?:private|incognito)\s+(?:chat|mode)\s+off",
    r"(?:(?:you\s+can|can\s+you|please)\s+)?(?:start|resume|restart)\s+(?:recording|saving|logging)(?:\s+again)?",
    r"(?:you\s+can|ok\s+to|okay\s+to|feel\s+free\s+to)\s+(?:record|save|remember)(?:\s+(?:again|now))+",
    r"record\s+(?:again|from\s+now)",
    r"(?:we'?re\s+|we\s+are\s+|go\s+|let'?s\s+go\s+)?back\s+on\s+the\s+record",
    r"(?:we'?re\s+|we\s+are\s+)?on\s+the\s+record(?:\s+again)?",
    r"(?:end|exit|leave|stop|close|quit|finish)\s+(?:the\s+)?(?:private|incognito)(?:\s+(?:chat|mode|conversation))?",
    r"(?:private|incognito)(?:\s+(?:chat|mode))?\s+off",
    r"(?:i'?m\s+)?done\s+with\s+(?:the\s+)?private\s+(?:chat|mode)",
)
#: "turn on" / "turn it back on" with no object only means "recording back on" while the chat is
#: private (the person was told that is how to end it); in an ordinary chat it means nothing here.
_BARE_TURN_ON = (
    r"turn\s+(?:it\s+)?(?:back\s+)?on|turn\s+on\s+again|switch\s+(?:it\s+)?(?:back\s+)?on"
    r"|(?:go\s+)?back\s+to\s+normal(?:\s+(?:chat|mode))?|normal\s+(?:chat|mode)|save\s+(?:again|from\s+now)"
)
_STATUS_PHRASES = (
    r"(?:are\s+you|is\s+vool)\s+(?:recording|saving|logging|remembering)(?:\s+(?:this|me|us|our\s+chat|this\s+chat|now|right\s+now))*\??",
    r"is\s+(?:this|this\s+chat|our\s+chat|this\s+conversation)\s+(?:being\s+)?(?:recorded|saved|logged)(?:\s+now)?\??",
    r"is\s+(?:private|incognito)\s+(?:chat|mode)\s+on\??",
    r"am\s+i\s+in\s+(?:a\s+)?(?:private|incognito)\s+(?:chat|mode)\??",
)

# A clause ends at a line break, any mark that is not a letter (sentence marks, brackets, an
# emoji), a dash or the end of the message.
_STOP = r"(?=[ ]*(?:$|\n|[^\w\s']|\s-\s|\bbut\b|\bthough\b|\bjust\b|\bbecause\b|\bsince\b|\bso\b|\band\b))"
_START = r"(?:^|(?<=[.!?;,—–(\[\n])|(?<=\s-))[ ]*"
#: Words that, said right before a command, make it a report of words rather than a request
#: ("I told him don't record this"). A command at the end of a message after anything else asks.
_REPORTED = frozenset(
    {"said", "say", "says", "saying", "told", "tell", "tells", "asked", "ask", "asks", "wrote", "write",
     "typed", "type", "if", "when", "whenever", "whether", "why", "how", "what", "to", "not", "never",
     "didn't", "didnt", "you", "he", "she", "they", "him", "her", "them", "the", "a", "means", "meaning"}
)


def _clause_re(phrases: tuple[str, ...] | str) -> re.Pattern[str]:
    body = phrases if isinstance(phrases, str) else "|".join(f"(?:{p})" for p in phrases)
    return re.compile(_START + _LEAD + rf"(?P<cmd>{body})" + _TAIL + r"\s*[.!?]*" + _STOP, re.IGNORECASE)


_ON_RE = _clause_re(_ON_PHRASES)
#: The requests that may close a message after other words: only ones that cannot be a mention
#: of the feature ("what is private chat?") or of something else ("please don't go private").
_ON_AT_END_PHRASES = (
    rf"(?:don'?t|do\s+not|dont|please\s+don'?t)\s+{_RECORD}\s+{_THIS}",
    r"i\s+(?:don'?t|do\s+not|dont)\s+want\s+(?:this|that|it|any\s+of\s+this|this\s+(?:chat|conversation)|my\s+messages)"
    r"\s+(?:to\s+be\s+)?(?:saved|recorded|logged|stored|remembered|kept)",
    r"keep\s+(?:this|it)\s+(?:private|between\s+us|off\s+the\s+record)(?![ ]+and\b)",
)
_ON_AT_END_RE = re.compile(
    r"(?<![\w'])" + "(?P<cmd>" + "|".join(f"(?:{p})" for p in _ON_AT_END_PHRASES) + ")" + _TAIL + r"[ ]*[^\w\s]*[ ]*$",
    re.IGNORECASE,
)
_WHOLE_ON_RE = re.compile(
    r"^[ ]*" + _LEAD + rf"(?:don'?t|do\s+not|dont|please\s+don'?t)\s+{_RECORD}(?:ing)?" + _TAIL + r"[ ]*[.!?]*[ ]*$",
    re.IGNORECASE,
)
_OFF_RE = _clause_re(_OFF_PHRASES)
_BARE_ON_RE = re.compile(r"^\s*" + _LEAD + rf"(?:{_BARE_TURN_ON})" + _TAIL + r"\s*[.!?]*\s*$", re.IGNORECASE)
_STATUS_RE = re.compile(r"^\s*" + _LEAD + "(?:" + "|".join(f"(?:{p})" for p in _STATUS_PHRASES) + ")" + _TAIL + r"\s*[.!?]*\s*$", re.IGNORECASE)
_FILLER_RE = re.compile(r"^[\s.,;:!?—–\-]*(?:(?:but|and|so|ok|okay|please|thanks|thank\s+you|now|just)[\s.,;:!?—–\-]*)*$", re.IGNORECASE)
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'"})


@dataclass(frozen=True)
class PrivateCommand:
    """One private-chat command read from a turn.

    ``rest`` is the message with the command clause taken out; empty when the message was only
    the command. A message that carries both is answered as a normal turn, with the switch
    already in force.
    """

    action: str
    rest: str = ""

    @property
    def bare(self) -> bool:
        return not self.rest


def _rest_without(text: str, match: re.Match[str]) -> str:
    rest = (text[: match.start()] + " " + text[match.end():]).strip()
    rest = re.sub(r"^\s*[,;:—–\-]+\s*", "", rest)
    rest = re.sub(r"^\s*(?:but|and|so|just|though)\b[\s,]*", "", rest, flags=re.IGNORECASE)
    rest = re.sub(r"[\s,;:\u2014\u2013\-]+$", "", rest)
    rest = re.sub(r"\(\s*\)|\[\s*\]", " ", rest)
    rest = " ".join(rest.split()) if "\n" not in rest else rest.strip()
    return "" if _FILLER_RE.match(rest) or not re.search(r"\w", rest) else rest.strip()


def parse_private_command(text: str, *, currently_private: bool = False) -> PrivateCommand | None:
    """Read a private-chat command from one user message, or None.

    ``currently_private`` lets a bare "turn it back on" end private chat; in an ordinary chat
    those words are left for whatever else they might mean. Turning private ON reads the command
    anywhere it stands as its own clause, or at the end of the message. Turning it OFF only counts
    when the message is the command alone: "you can save this, it's a draft" must not end it.
    """
    lines = str(text or "").translate(_APOSTROPHES).splitlines()
    raw = "\n".join(" ".join(line.split()) for line in lines if line.strip())
    if not raw or len(raw) > 4000:
        return None
    if _STATUS_RE.match(raw):
        return PrivateCommand(STATUS)
    if currently_private and _BARE_ON_RE.match(raw):
        return PrivateCommand(OFF)
    if _WHOLE_ON_RE.match(raw):
        return PrivateCommand(ON)
    off = _OFF_RE.search(raw)
    on = _ON_RE.search(raw) or _on_at_end(raw)
    if off and _rest_without(raw, off):
        off = None
    if off and on:
        # Both in one message: the one said last is what the person wants now.
        chosen, action = (off, OFF) if off.start() > on.start() else (on, ON)
    elif off:
        chosen, action = off, OFF
    elif on:
        chosen, action = on, ON
    else:
        return None
    return PrivateCommand(action, _rest_without(raw, chosen))


def _on_at_end(raw: str) -> re.Match[str] | None:
    """A request not to be recorded that closes the message ("my pin is 1234 dont save this"),
    unless the word before it makes it reported speech ("I told him don't record this")."""
    match = _ON_AT_END_RE.search(raw)
    if match is None:
        return None
    head = raw[: match.start()].rstrip()
    before = head.split()
    if head.endswith((":", '"', "'", "\u201c")) or (before and before[-1].lower().strip("\"'(") in _REPORTED):
        return None
    return match


# ── replies ──────────────────────────────────────────────────────────────────

REPLY_ON = (
    "Private chat is on. From this message on, nothing in this chat is saved: not the messages, "
    "not a summary, not memory. You'll see a small ghost on each private message. "
    "Say \"turn recording back on\" when you want me to save again."
)
REPLY_ALREADY_ON = (
    "Private chat is already on. Nothing in this chat is being saved. "
    "Say \"turn recording back on\" to end it."
)
REPLY_OFF = (
    "Recording is back on, so I'll save this chat as usual from here. "
    "The private part stays unsaved, so I won't be able to recall it later."
)
REPLY_ALREADY_OFF = (
    "This chat isn't private, so it is being saved as usual. "
    "Say \"private chat\" or \"don't record this\" to stop saving."
)
REPLY_STATUS_ON = "This chat is private right now: nothing in it is being saved."
REPLY_STATUS_OFF = (
    "This chat is being saved as usual. "
    "Say \"private chat\" or \"don't record this\" and I'll stop saving it."
)
REPLY_REMEMBER_REFUSED = (
    "Private chat is on, so I won't save that either. "
    "Say \"turn recording back on\" first, then tell me again and I'll remember it."
)
REPLY_FAILED = "Sorry, I couldn't change that just now, so nothing changed. Please try again."
REPLY_FAILED_ON = (
    "Sorry, I couldn't turn private chat on. I haven't saved this message, "
    "but please try again before you share anything private."
)


def private_prompt_block() -> str:
    """The system note a model sees on a private turn, so it never promises to remember."""
    return (
        "PRIVATE CHAT IS ON for this conversation. Nothing from this chat is saved: not the "
        "messages, not a summary, not memory. Never say you will remember, keep in mind, note or "
        "save anything from it, and never claim anything was saved. If the user asks you to "
        "remember something, say private chat is on, so it won't be kept, and that they can say "
        "\"turn recording back on\" first."
    )


#: Stamped on a turn that sounds like a request not to be recorded but switched nothing on.
NOT_ON_KEY = "private_chat_not_on"

#: English: a "don't save/record ..." that ends its clause on the chat itself, or a privacy word.
_PRIVACY_ASK_RE = re.compile(
    r"\b(?:don'?t|dont|do\s+not|never|stop|no)\s+(?:\w+\s+)?(?:record|recrod|sav|log|stor|remember|memori|keep)\w*"
    r"(?:\s+(?:this|that|it|anything|any\s+of\s+(?:this|it)|our\s+chat|this\s+(?:chat|conversation)|my\s+messages|me|us))?"
    r"[ ]*(?:$|\n|[^\w\s'])"
    r"|\b(?:privately|incognito|off\s+the\s+record|private\s+(?:chat|mode|conversation))\b",
    re.IGNORECASE,
)
#: The page's other languages: a recording/saving word and a negation in the same message.
_FOREIGN_SAVE_RE = re.compile(
    r"\b(?:privat\w*|privé\w*|enregistr\w*|grab(?:ar|es|e)\w*|guard(?:ar|es|e)\w*|aufzeichn\w*|aufnehm\w*"
    r"|speicher\w*|zapis\w*|записыв\w*|сохран\w*|kaydet\w*)",
    re.IGNORECASE,
)
_FOREIGN_NOT_RE = re.compile(r"\b(?:nicht|kein\w*|ne|pas|no|nie|не|ни)\b|kaydetme", re.IGNORECASE)


def looks_like_privacy_request(text: str) -> bool:
    """Loose: the message talks about (not) recording or saving, in any way the parser did not
    read as a command. Used only to tell the model the truth, never to switch anything."""
    raw = str(text or "").translate(_APOSTROPHES)
    return bool(_PRIVACY_ASK_RE.search(raw) or (_FOREIGN_SAVE_RE.search(raw) and _FOREIGN_NOT_RE.search(raw)))


def not_on_prompt_block() -> str:
    """The system note for a turn that may ask not to be recorded while private chat is off."""
    return (
        "PRIVATE CHAT IS NOT ON for this conversation: it is being saved as usual. If the user is "
        "asking you not to record, save or remember something, say plainly that this chat is still "
        "being saved, and that they can say \"private chat\" (in English) to turn private chat on. "
        "Never say you won't save it, will forget it, or will keep it off the record."
    )


def observe_private_door(
    user_text: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
    page_believes_private: bool = False,
) -> bool:
    """The chat door's half: make the request private before anything can record it.

    ``page_believes_private`` is the sending tab's own view. A tab that still shows private chat
    (another tab ended it, and this one has not heard yet) sent its message believing it would not
    be saved: that turn is kept unsaved too, and its commit tells the tab the chat is saved again.

    A chat that is already private, or a message that turns it on, marks the bound request private
    and stamps ``source_context``. Turning it OFF is left to the turn itself, which answers it.
    Returns whether the request is private. Never raises: a store fault turns the request private.
    """
    context = source_context if isinstance(source_context, dict) else None
    sid = _clean(session_id)
    currently = is_private(sid) if sid else False
    try:
        command = parse_private_command(user_text, currently_private=currently)
    except Exception:
        command = None
    private = currently or bool(page_believes_private)
    if command is not None and command.action == ON:
        private = True
        if sid and not currently:
            try:
                set_private(sid, True)
                if context is not None:
                    context[SWITCHED_ON_KEY] = True
            except Exception:
                _log.warning("private chat switch failed at the chat door", exc_info=True)
    if private:
        if context is not None:
            context[PRIVATE_TURN_KEY] = True
        mark_request_private(session_id=sid)
    return private


def observe_private_turn(
    agent: Any,
    raw_user_input: str,
    *,
    session_id: str,
    source_context: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """Apply a private-chat command, stamp a private turn, and answer a bare command.

    Runs at the very front of a turn, before anything is written. Returns ``{"result": ...}`` when
    the message was only a command (the reply states what the store now holds), else None after
    stamping ``source_context`` when the turn is private.
    """
    context = source_context if isinstance(source_context, dict) else None
    sid = session_id_from(context, session_id)
    currently = is_private(sid) if sid else False
    if currently and context is not None and context.get(SWITCHED_ON_KEY):
        # The chat door turned it on for this very message; the reply reports the switch.
        currently = False
    command = parse_private_command(raw_user_input, currently_private=currently)
    reply: str | None = None
    turn_private = currently
    if command is not None and command.action in {ON, OFF}:
        want_on = command.action == ON
        if not sid:
            reply = REPLY_FAILED if command.bare else None
            # A turn that asked not to be recorded is never recorded, chat or no chat.
            turn_private = turn_private or want_on
        else:
            try:
                now_on = set_private(sid, want_on)
            except Exception:
                _log.warning("private chat switch failed", exc_info=True)
                now_on = currently
                if want_on:
                    turn_private = True
                    reply = REPLY_FAILED_ON
                elif command.bare:
                    reply = REPLY_FAILED
            else:
                if command.bare and reply is None:
                    if want_on:
                        reply = REPLY_ALREADY_ON if currently else REPLY_ON
                    else:
                        reply = REPLY_OFF if currently else REPLY_ALREADY_OFF
                # The turn that turns private ON is private; the turn that turns it OFF was sent
                # while the chat was private, so it is not recorded either.
                turn_private = turn_private or now_on or want_on
    elif command is not None and command.action == STATUS:
        reply = REPLY_STATUS_ON if currently else REPLY_STATUS_OFF
    if turn_private:
        if context is not None:
            context[PRIVATE_TURN_KEY] = True
        mark_request_private(session_id=sid)
    if context is not None and command is not None and command.action == OFF and not turn_private:
        context.pop(PRIVATE_TURN_KEY, None)
    if context is not None and not turn_private and command is None and looks_like_privacy_request(raw_user_input):
        context[NOT_ON_KEY] = True
    if reply is None:
        return None
    return {
        "result": agent._fast_path_result(
            session_id=session_id,
            user_input=raw_user_input,
            response=reply,
            confidence=0.99,
            source_context=source_context,
            reason="private_chat_command",
        )
    }
