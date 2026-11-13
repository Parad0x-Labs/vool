"""What this chat last asked the runtime to DO, how it ended, and the slots it resolved.

Follow-ups ("try again, I fixed permissions", "that folder", "there") point at that record, not at a phrase and not at
whatever the previous user line happened to say. One record per chat, written when a turn is sealed:

* only an ACTIONABLE turn (a tool ran or was refused, or a lane failed) replaces the request; small talk and plain model
  answers only age it (`turns_since`), so "thanks" between a refusal and "try again" does not lose the refused request;
* the outcome is ``done`` / ``refused`` / ``failed`` / ``needs_answer``;
* the slots are what the turn resolved (folder path, file kind, count, order), so "that folder" has something to mean.

`resolve` is the one decision a turn makes before any deterministic handler reads it: ``retry`` (rerun the recorded
request), ``referent_unresolved`` (the message points at something the record cannot supply: handlers must decline and the
model gets the turn), or ``none``. Retry rests on state: a refused or failed request is retried by a short message that
carries no new task and reports a changed state or says to go; a finished one only by an explicit retry cue.
"""
from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_LOCK = threading.Lock()
_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS conversation_state ("
    "session_id TEXT PRIMARY KEY, request TEXT NOT NULL, outcome TEXT NOT NULL, reason TEXT NOT NULL DEFAULT '', "
    "slots TEXT NOT NULL DEFAULT '{}', turns_since INTEGER NOT NULL DEFAULT 0, updated_at TEXT NOT NULL)"
)

DONE, REFUSED, FAILED, NEEDS_ANSWER = "done", "refused", "failed", "needs_answer"
#: An ask that was answered in words with no tool run and nothing refused (a plain model answer, a clean-loop answer,
#: a recall answer). Only an explicit retry cue reruns it; the cleared-blocker branch is for refused/failed only.
ANSWERED = "answered"
_UNFINISHED = frozenset({REFUSED, FAILED})
#: A bare go-ahead (no retry cue) may only answer a refusal that was the LAST thing said; with turns in between it may be
#: answering something else (VOOL's own proposal), so it goes to the turn as written.
_CUE_MAX_AGE = 4

_REFUSAL_STATUSES = frozenset({"blocked_by_mode", "pending_approval", "cancelled", "denied"})


@dataclass(frozen=True)
class PendingRequest:
    request: str
    outcome: str
    reason: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    turns_since: int = 0


@dataclass(frozen=True)
class Resolution:
    kind: str  # "retry" | "retry_changed" | "pick" | "referent_unresolved" | "none"
    request: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    change: str = ""
    #: A reply the resolver can give itself (a pointer outside the printed list): the turn publishes it, no lane guesses.
    answer: str = ""
    #: The message carries no task (thanks, a dismissal, a shrug): the turn answers `answer` and runs nothing.
    closing: bool = False

    @property
    def text(self) -> str:
        """The text the turn is ROUTED as. The transcript keeps what the user typed (persistent_memory)."""
        if self.kind == "retry_changed":
            return f"{self.request} (change: {self.change})"
        if self.kind == "pick":
            return self.request
        return self.request


def _conn():
    from core.runtime_continuity import _conn as runtime_conn

    conn = runtime_conn()
    conn.execute(_SCHEMA)
    return conn


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def clear_all() -> None:
    """Per-test / full reset: drop every chat's record."""
    try:
        with _LOCK:
            conn = _conn()
            try:
                conn.execute("DELETE FROM conversation_state")
                conn.commit()
            finally:
                conn.close()
    except Exception:
        return


def erase_chat(session_id: str) -> None:
    """Chat delete / forget / private-chat erasure: the chat's request text goes with the chat."""
    sid = str(session_id or "").strip()
    if not sid:
        return
    with _LOCK:
        conn = _conn()
        try:
            conn.execute("DELETE FROM conversation_state WHERE session_id = ?", (sid,))
            conn.commit()
        finally:
            conn.close()


def load(session_id: str) -> PendingRequest | None:
    sid = str(session_id or "").strip()
    if not sid:
        return None
    try:
        with _LOCK:
            conn = _conn()
            try:
                row = conn.execute(
                    "SELECT request, outcome, reason, slots, turns_since FROM conversation_state WHERE session_id = ?",
                    (sid,),
                ).fetchone()
            finally:
                conn.close()
    except Exception:
        return None
    if not row:
        return None
    try:
        slots = json.loads(row[3] or "{}")
    except Exception:
        slots = {}
    return PendingRequest(str(row[0]), str(row[1]), str(row[2]), slots if isinstance(slots, dict) else {}, int(row[4] or 0))


#: Statuses that mean "not done because something is switched off, denied or fenced". Producers seal these structurally
#: (status / details.reason / task_outcome); the reply text is never read.
_REFUSED_REASONS = frozenset({
    "blocked_by_mode", "pending_approval", "cancelled", "denied", "permission_denied", "disabled",
    "owner_local_required", "web_off", "approval_denied", "refused",
})


#: Reason codes the producers seal in ``refused_request.reason`` that mean the web switch is off.
_WEB_OFF_REASONS = frozenset({"web_off", "web_off_lookup_refusal", "offline_lookup_refusal", "live_browser_off"})


def outcome_of(result: dict[str, Any] | None) -> tuple[str, str, bool]:
    """``(outcome, reason, actionable)`` for a sealed turn result, from its structured fields only."""
    result = result or {}
    details = result.get("details") if isinstance(result.get("details"), dict) else {}
    receipts = [r for r in (result.get("tool_receipts") or []) if isinstance(r, dict)]
    status = str(result.get("status") or "").strip().lower()
    task_outcome = str(result.get("task_outcome") or "").strip().lower()
    receipt_status = str((receipts[0].get("execution") or {}).get("status") or "").strip().lower() if receipts else ""
    refused = result.get("refused_request") if isinstance(result.get("refused_request"), dict) else {}
    reasons = [str(refused.get("reason") or "").strip().lower(), status, str(details.get("reason") or "").strip().lower(), receipt_status]
    reasons = ["web_off" if r in _WEB_OFF_REASONS else r for r in reasons]
    missing = str(details.get("missing_right") or "").strip().lower()
    if missing and status in {"refused", "permission_denied"} | _REFUSED_REASONS:
        return REFUSED, f"missing_right:{missing}", True  # a paired phone lacks this right (request_trust.seal_remote_grant_refusal)
    if task_outcome == "refused":
        return REFUSED, next((r for r in reasons if r), "refused"), True
    reason = next((r for r in reasons if r in _REFUSED_REASONS), "") or next((r for r in reasons if r), "")
    if result.get("approval_request") or task_outcome == "pending_approval":
        return NEEDS_ANSWER, reason or "pending_approval", True
    if reason in _REFUSED_REASONS and (result.get("success") is False or receipts or task_outcome in {"failed", "blocked"} or status in _REFUSED_REASONS):
        return REFUSED, reason, True
    failed_receipt = any(not bool((r.get("execution") or {}).get("ok", True)) for r in receipts)
    if result.get("success") is False or task_outcome in {"failed", "failure", "blocked"} or failed_receipt:
        return FAILED, reason, bool(receipts) or task_outcome in {"failed", "failure", "blocked"} or str(result.get("mode") or "").startswith("tool")
    if receipts:
        return DONE, receipt_status, True
    return DONE, "", False


_LIST_KEYS = ("entries", "matches", "items")
_LISTED_MAX = 50


def listed_of(details: dict[str, Any]) -> list[dict[str, str]]:
    """The ordered list a tool result printed, as ``[{"name","path"}]`` (structured fields only; the reply text is not read)."""
    for key in _LIST_KEYS:
        raw = details.get(key)
        if not isinstance(raw, list) or not raw:
            continue
        out: list[dict[str, str]] = []
        for item in raw[:_LISTED_MAX]:
            if isinstance(item, str) and item.strip():
                out.append({"name": item.strip().replace("\\", "/").rsplit("/", 1)[-1], "path": item.strip()})
            elif isinstance(item, dict) and (item.get("path") or item.get("name")):
                path = str(item.get("path") or item.get("name"))
                out.append({"name": str(item.get("name") or path.replace("\\", "/").rsplit("/", 1)[-1]), "path": path})
        if out:
            return out
    return []


def slots_of(result: dict[str, Any] | None) -> dict[str, Any]:
    """The folder / kind / count / order a turn resolved, from its tool details."""
    details = dict((result or {}).get("details") or {})
    slots: dict[str, Any] = {}
    for key in ("resolved_target", "path"):
        value = details.get(key)
        if isinstance(value, str) and value.strip():
            slots["path"] = value.strip()
            break
    receipts = [r for r in ((result or {}).get("tool_receipts") or []) if isinstance(r, dict)]
    if receipts and str(receipts[0].get("tool_name") or "").strip():
        slots["tool"] = str(receipts[0]["tool_name"]).strip()
    listed = listed_of(details)
    if listed:
        slots["listed"] = listed
    arguments = details.get("arguments") if isinstance(details.get("arguments"), dict) else {}
    for key in ("path", "kind", "limit", "newest_first"):
        if key in arguments and arguments[key] not in (None, ""):
            slots[key] = arguments[key]
    return slots


def record_sealed_turn(session_id: str, request: str, result: dict[str, Any] | None, *, extra_slots: dict[str, Any] | None = None) -> None:
    """Called once per sealed turn. Actionable turns replace the record; every other turn only ages it."""
    sid = str(session_id or "").strip()
    text = " ".join(str(request or "").split())
    if not sid or not text:
        return
    outcome, reason, actionable = outcome_of(result)
    try:
        with _LOCK:
            conn = _conn()
            try:
                if actionable:
                    slots = {**slots_of(result), **dict(extra_slots or {})}
                    reply = " ".join(str((result or {}).get("response") or (result or {}).get("response_text") or "").split())[:600]
                    if reply and outcome in {REFUSED, FAILED, NEEDS_ANSWER}:
                        slots["_reply"] = reply  # what VOOL showed: it names the blocker; later messages are grounded against it
                    conn.execute(
                        "INSERT OR REPLACE INTO conversation_state (session_id, request, outcome, reason, slots, turns_since, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 0, ?)",
                        (sid, text[:2000], outcome, reason[:200], json.dumps(slots, sort_keys=True, default=str), _now()),
                    )
                elif _is_an_ask(text):
                    # A question or task that was just answered in words: the next "try that again" has it to rerun.
                    # What the chat resolved earlier (folder, printed list) rides along while it is still fresh.
                    old = conn.execute("SELECT slots, turns_since, outcome FROM conversation_state WHERE session_id = ?", (sid,)).fetchone()
                    if old and str(old[2]) in _UNFINISHED and int(old[1] or 0) <= _CUE_MAX_AGE:
                        # A fresh refusal/failure keeps the record: a question asked in between only ages it, as small
                        # talk does, so "try again" still reruns what was refused.
                        conn.execute("UPDATE conversation_state SET turns_since = turns_since + 1 WHERE session_id = ?", (sid,))
                        conn.commit()
                        return
                    carry: dict[str, Any] = {}
                    if old and int(old[1] or 0) <= _PICK_MAX_AGE:
                        try:
                            before = json.loads(old[0] or "{}")
                        except Exception:
                            before = {}
                        carry = {k: v for k, v in before.items() if k in {"listed", "picked", "path"}} if isinstance(before, dict) else {}
                    conn.execute(
                        "INSERT OR REPLACE INTO conversation_state (session_id, request, outcome, reason, slots, turns_since, updated_at) "
                        "VALUES (?, ?, ?, '', ?, 0, ?)",
                        (sid, text[:2000], ANSWERED, json.dumps(carry, sort_keys=True, default=str), _now()),
                    )
                else:
                    conn.execute("UPDATE conversation_state SET turns_since = turns_since + 1 WHERE session_id = ?", (sid,))
                conn.commit()
            finally:
                conn.close()
    except Exception:
        return


_QUESTIONISH_RE = re.compile(
    r"^\s*(?:please\s+|pls\s+)?(?:what|whats|what's|who|whom|whose|why|how|when|where|which|can|could|do|does|did|is|are|was|were|"
    r"will|would|should|has|have|had|am)\b|\?\s*$",
    re.IGNORECASE,
)


def _is_an_ask(text: str) -> bool:
    """A question or a task (the existing work-of-its-own reading, or the shape of a question), not a closing, a
    reaction or a bare retry/go-ahead."""
    compact = " ".join(str(text or "").split())
    if not compact or is_social_only(compact):
        return False
    if _CUE_RE.search(compact) and _is_plain_remainder(_split_cue(compact)[1], {}):
        return False
    if _QUESTIONISH_RE.search(compact):
        return True
    try:
        from core.agent_runtime.answer_coverage import asks_for_work_of_its_own

        return bool(asks_for_work_of_its_own(compact))
    except Exception:
        return False


# --- reading a message against the record ---------------------------------------------------------------------------
#: A retry CUE: a retry verb, "again", "once more", "another go/shot/try", "the same". Function words. It is the fast yes;
#: nothing here decides a message WITHOUT a cue (state and the arbiter do).
_CUE_RE = re.compile(
    r"\b(?:re-?try|re-?run|re-?do|re-?send|re-?check|repeat|again|once\s+more|one\s+more\s+(?:time|try|go|shot)|another\s+(?:go|shot|try|attempt|crack)|"
    r"same\s+again|(?:the\s+)?same\s+thing|the\s+same)\b|^(?:ok(?:ay)?\s+|please\s+|pls\s+)?try\b",
    re.IGNORECASE,
)
#: The cue with the do-verb / acknowledgements around it, stripped to leave the CHANGE (if any).
_CUE_STRIP_RE = re.compile(
    r"\b(?:(?:try|run|do|go|give|send|have|check|fetch|get|make|look|take)\s+(?:it\s+|that\s+|this\s+|them\s+)?)?"
    r"(?:re-?try|re-?run|re-?do|re-?send|re-?check|repeat|again|once\s+more|one\s+more\s+(?:time|try|go|shot)|another\s+(?:go|shot|try|attempt|crack)|"
    r"same\s+again|(?:the\s+)?same\s+thing|the\s+same)\b|^(?:ok(?:ay)?\s+|please\s+|pls\s+)?try\b(?:\s+(?:it|that|now))?",
    re.IGNORECASE,
)
_ACK_WORDS = frozenset({
    "now", "please", "pls", "plz", "thanks", "thx", "ty", "thank", "you", "ok", "okay", "k", "kk", "then", "again", "yo", "hey",
    "lol", "haha", "cool", "great", "nice", "so", "and", "it", "that", "this", "them", "go", "on", "ahead", "me", "for", "sure",
    "yes", "yep", "yeah", "alright", "right", "well", "more", "time", "one", "once", "a", "the", "same", "lot", "much", "mate",
    "man", "bro", "dude", "buddy", "repeat", "all", "yours", "mine", "ours", "can", "could", "would", "will", "u", "still", "yet", "nothing", "anything", "something", "give", "done", "try", "shot", "crack",
})
#: The message reports that something changed or says it hopes it works: not a change to WHAT is run.
_STATE_REPORT_RE = re.compile(
    r"\b(?:fixed|enabled|allowed|granted|unlocked|approved|sorted|changed|updated|turned\s+(?:it\s+)?on|switched\s+(?:it\s+)?on|"
    r"is\s+on|are\s+on|its\s+on|it'?s\s+on|works?\s+now)\b|\b(?:should|will|can|does|hope|hopefully)\b.{0,12}\bwork"
    r"|^(?:ok(?:ay)?\s+)?(?:now\s+)?(?:go|go\s+ahead|do\s+it|proceed|try)(?:\s+now)?$",
    re.IGNORECASE,
)
#: Words that change WHAT would be run; a message holding one has a task of its own.
_CHANGE_RE = re.compile(
    r"\b(?:but|instead|only|just|except|also|plus|without|different|another|other|new|more|less|fewer|rather|sorted\s+by|"
    r"newest|oldest|latest|biggest|smallest|largest)\b"
    r"|\b(?:in|on|from|inside|into|under|for|with|to|at|of|about|by)\s+(?:the|my|this|that|those|these|a|an|\d+|~|/)"
    r"|[~/]\S|\b(?:what|who|whom|whose|why|how|when|where|which)\b",
    re.IGNORECASE,
)
_ANAPHORIC_FOLDER_RE = re.compile(
    r"\b(?:that|those|the\s+same|same|previous|other)\s+(?:\w+\s+)?(?:folder|folders|directory|dir)\b|\b(?:in|from|inside)\s+there\b",
    re.IGNORECASE,
)
#: Words that drop the request. "thanks"-class words alone do not (a retry may be thanked).
_NEGATION_RE = re.compile(
    r"\b(?:no|nope|nah|not|never|nvm|cancel|stop|skip|drop|forget|leave|scrap|quit|abort|ignore|without|bye|brb|gtg|whatever|later|"
    r"maybe|tomorrow|tonight|wait|hold\s+on|for\s+now|next\s+(?:week|time)|never\s*mind)\b"
    r"|\b(?:do|does|did|is|are|was|were|has|have|had|can|could|would|should|wo|ca)n'?t\b"
    r"|\b(?:cant|wont|dont|doesnt|didnt|isnt|arent|wasnt|couldnt|wouldnt|shouldnt|havent|hasnt)\b"
    r"|\bchang(?:ed|e|ing)\s+(?:of\s+)?(?:my\s+)?mind\b",
    re.IGNORECASE,
)
#: Exits as a class: a negator, a cancel / postpone verb, or a changed mind. A retry cue in the same message wins (handled before).
_DISMISS_RE = _NEGATION_RE
_THANKS_RE = re.compile(r"\b(?:thanks|thank\s+you|thx|ty|cheers)\b", re.IGNORECASE)
_DISMISS_WHOLE_RE = re.compile(r"^(?:no|nope|nah|nothing|skip\s+it|drop\s+it)[\s.!]*$", re.IGNORECASE)
#: Bare reactions: with the obstacle gone these are still not a "go".
_REACTION_WORDS = frozenset({
    "ok", "okay", "k", "kk", "cool", "lol", "haha", "hehe", "hmm", "hm", "mm", "nice", "right", "good", "great", "yeah", "yep",
    "yes", "sure", "alright", "fine", "wow", "oh", "ah", "ugh", "thanks", "thx", "ty", "noted", "got", "it", "to", "know",
})
#: Go-ahead function words, used only where the blocker is NOT known to be cleared (age 0): a bare go.
_GO_RE = re.compile(r"\b(?:go\s+(?:on|ahead)|proceed|continue|carry\s+on|do\s+it)\b", re.IGNORECASE)
_WH_RE = re.compile(r"\b(?:what|whats|what's|who|whom|whose|why|how|when|where|which)\b", re.IGNORECASE)
#: Function words: pronouns, auxiliaries, determiners, acknowledgements, and the "show me / see / there" of asking to look.
_FUNCTION_WORDS = frozenset(_ACK_WORDS | _REACTION_WORDS | {
    "what", "whats", "what's", "you", "your", "got", "have", "has", "had", "there", "then", "i", "we", "me", "my", "is", "are", "was",
    "do", "does", "did", "see", "show", "look", "its", "it's", "any", "found", "up", "out", "and", "or", "be", "been", "to",
    "let", "lemme", "gimme", "give", "so", "well", "hm", "mm", "mmm", "eh", "um", "uh", "at", "in", "of", "a", "an",
})
_AUX_START = frozenset({"is", "are", "was", "were", "does", "do", "did", "can", "could", "will", "would", "should", "has", "have", "had"})
_PROMPT_WORDS = frozenset({"work", "works", "working", "now", "them", "those", "these", "u", "ya", "ready", "yet"})
_NO_WH_RE = re.compile(_WH_RE.pattern, re.IGNORECASE)


def is_social_only(text: str) -> bool:
    """Whether the whole message is a closing or a reaction (thanks, "meh ok whatever", "nvm thanks anyway") and asks for
    nothing: every word is a function word of the closed classes above, and at least one is thanks, a reaction, a filler or
    a dismissal. "go again" / "go on" are acknowledgements only, so they are not social. Read from words, not phrases."""
    toks = _tokens(text)
    if not toks:
        return False
    core = _THANKS_WORDS | _REACTION_WORDS | _FILLER_WORDS | _DISMISS_TOKENS | _SOCIAL_EXTRA
    if any(t in _GO_TOKENS for t in toks):
        return False
    if not all(t in core or t in _THANKS_TAIL or t in _ACK_WORDS for t in toks):
        return False
    return any(t in core for t in toks)


#: What the model is told about a closing remark, so it does not read the chat's last task as still open.
CLOSING_NOTE = ("The person's message is a closing remark (thanks, a dismissal or a reaction). It asks for nothing. "
                "Reply in one short, friendly line. Do not look anything up, run anything, redo or continue the earlier "
                "request, or report on it.")
#: Words that make a short reaction an acceptance of an offer rather than a closing ("yes thanks").
_ACCEPT_WORDS = frozenset({"yes", "yep", "yeah", "yup", "y", "sure", "please", "pls", "plz", "proceed", "confirm", "approve", "go", "ahead", "do"})


#: Words that only pad a closing ("all good", "no worries", "appreciate it", "thanks for trying", "that's all"). They are
#: function words of this class alone: they never make a message a closing without an anchor word below.
_CLOSING_PAD = frozenset({
    "worries", "worry", "problem", "perfect", "good", "fine", "enough", "fair", "awesome", "brilliant", "lovely", "super",
    "appreciate", "appreciated", "trying", "bother", "bothered", "help", "helping", "effort", "time", "i", "i'm", "im", "i've", "ive",
    "its", "it's", "that's", "thats", "that", "it", "all", "for", "the", "of", "is", "are", "be", "ok", "okay", "will", "well",
    "so", "then", "got", "sort", "myself", "own", "np", "ta", "much", "bit", "little", "too", "also", "now", "just", "here",
    "happy", "glad", "no", "nope", "not", "worked", "works", "helped", "helpful", "useful",
})
def is_closing(text: str) -> bool:
    """A social-only message with a thanks word, a filler or a dismissal in it, and no acceptance word."""
    if not is_social_only(text):
        return False
    toks = _tokens(text)
    if any(t in _ACCEPT_WORDS for t in toks):
        return False
    return any(t in _THANKS_WORDS or t in _FILLER_WORDS or t in _DISMISS_TOKENS for t in toks)


def is_plain_closing(text: str) -> bool:
    """A message with no task in it: once the closed classes (thanks, reactions, fillers, acknowledgement padding) are
    removed nothing is left, and at least one anchor (a thanks word, a shrug, "never mind", "forget it") is there. An
    acceptance ("yes thanks"), a go-ahead or a rerun word, a negation of what was said ("not that", "that's not all"),
    a bare "no" / "stop" / "cancel" (an answer or an order, not a goodbye), a forget ask ("forget that", "forget it all")
    or any other word (a noun, a verb, a folder) is a task or a question of its own and keeps the message out. Read from
    structure, not from sentences."""
    toks = _tokens(text)
    if not toks or len(toks) > 12:
        return False
    if "?" in str(text):
        return False
    known = _THANKS_WORDS | _REACTION_WORDS | _FILLER_WORDS | _DISMISS_TOKENS | _SOCIAL_EXTRA | _THANKS_TAIL | _ACK_WORDS | _CLOSING_PAD
    if not all(t in known for t in toks):
        return False
    thanked = any(t in _THANKS_WORDS for t in toks)
    if any(t in _CLOSING_BLOCKERS and not (thanked and t in _THANKS_TAIL) for t in toks):
        return False
    if "not" in toks:
        return False
    for i, t in enumerate(toks):
        nxt = toks[i + 1] if i + 1 < len(toks) else ""
        if t == "forget" and (nxt != "it" or any(w in toks for w in ("all", "that"))):
            return False
        if t == "never" and nxt != "mind":
            return False
        if t == "mind" and (i == 0 or toks[i - 1] != "never"):
            return False
    return any(t in _CLOSING_ANCHOR for t in toks)


def closing_reply(text: str) -> str:
    """One short plain line for a closing, chosen by the class of the message (thanks vs a dismissal), never by its wording."""
    toks = _tokens(text)
    if any(t in _THANKS_WORDS or t in {"appreciate", "appreciated", "ta"} for t in toks):
        return "You're welcome."
    return "Okay. Ask me whenever you want something."


def _cleared_exit(compact: str, pending: PendingRequest | None = None) -> bool:
    """The closed set of messages that are NOT a retry although the blocker is cleared: a dismissal or postponement, thanks
    alone, a question or task with content of its own."""
    if _DISMISS_RE.search(compact) or _DISMISS_WHOLE_RE.match(compact):
        return True
    toks = _tokens(compact)
    if toks and all(t in _THANKS_WORDS or t in _ACK_WORDS or t in _THANKS_TAIL or t in _FILLER_WORDS for t in toks) and any(
        t in _THANKS_WORDS or t in _FILLER_WORDS for t in toks
    ):
        return True
    if _WH_RE.search(compact) and any(t not in _FUNCTION_WORDS for t in toks):
        return True  # a question with content of its own
    if toks and toks[0] in _AUX_START and any(t not in _FUNCTION_WORDS and t not in _PROMPT_WORDS for t in toks):
        return True  # an auxiliary-first question with content of its own ("does it cost anything"); "does it work now" is a prompt
    rest = _WH_RE.sub(" ", compact)
    content = [t for t in toks if t not in _FUNCTION_WORDS]
    if len(content) <= 1 and (not _CHANGE_RE.search(_NEUTRAL_RE.sub(" ", rest)) or _grounded(content, pending)):
        return False  # one bare verb or noun has nothing of its own to do it to
    if len(content) >= 2:
        # Grounding, not a count: every content word must come from what the chat already said (the refusal reply, the
        # request) or from the closed state-change / look-again verbs. One ungrounded word is a task of its own.
        return not _grounded(content, pending)
    return _own_task(rest)


def _stem(token: str) -> str:
    t = token.lower().strip("'")
    for _ in range(2):
        for suffix in ("ing", "ed", "es", "s", "ly", "d"):
            if t.endswith(suffix) and len(t) - len(suffix) >= 3:
                t = t[: -len(suffix)]
                break
    return t


#: Closed classes (verbs of changing a switch; verbs of looking again; hedges), used only to ground a message.
_STATE_VERBS = frozenset(_stem(w) for w in (
    "turn", "turned", "switch", "switched", "enable", "enabled", "allow", "allowed", "grant", "granted", "toggle", "toggled", "tick",
    "ticked", "flip", "flipped", "set", "fix", "fixed", "sort", "sorted", "change", "changed", "update", "updated", "unlock",
    "unlocked", "approve", "approved", "work", "works", "working", "worked", "do", "done", "did", "given",
))
_LOOK_VERBS = frozenset(_stem(w) for w in ("look", "see", "check", "show", "try", "go", "run", "find", "get", "turn", "peek", "take", "have"))
#: Nouns for the controls a person flips to remove a blocker.
_CONTROLS = frozenset(_stem(w) for w in ("box", "checkbox", "button", "switch", "toggle", "option", "setting", "slider", "tab", "menu", "permission", "access", "right"))
_HEDGES = frozenset({"think", "guess", "hope", "believe", "suppose", "reckon", "pretty", "really", "just", "finally", "ur", "thats", "that's", "ill", "im"})


def _grounded(content: list[str], pending: PendingRequest | None) -> bool:
    known: set[str] = set(_STATE_VERBS) | set(_LOOK_VERBS) | set(_CONTROLS)
    if pending is not None:
        for text in (pending.slots.get("_reply"), pending.request):
            known |= {_stem(t) for t in _tokens(str(text or "")) if t not in _FUNCTION_WORDS}
    return all(_stem(t) in known or t in _HEDGES for t in content)


_DISMISS_TOKENS = frozenset({"no", "nope", "not", "cancel", "stop", "skip", "drop", "leave", "scrap", "quit", "abort", "ignore", "later", "nvm"})
_THANKS_TAIL = frozenset({"anyway", "anyways", "though", "tho", "much", "again", "mate", "dude", "man"})
_THANKS_WORDS = frozenset({"thanks", "thank", "thx", "ty", "cheers", "tyvm"})
#: Interjections that carry no request ("meh", "whatever"): function words like the thanks tail.
_FILLER_WORDS = frozenset({"meh", "whatever", "anyway", "anyways", "nah", "nvm", "never", "mind", "forget", "bye"})
#: Further reactions that are social in a closing ("ugh whatever") but still a bare prompt on their own.
_SOCIAL_EXTRA = frozenset({"ugh", "oh", "fine", "eh", "pff", "bah"})
#: Words that ask to run something: a message holding one is never a closing.
_GO_TOKENS = frozenset({"again", "retry", "redo", "rerun", "repeat", "try", "once", "more", "same", "go", "ahead", "proceed", "continue"})
#: At least one of these must be present: it is what makes the message a closing rather than a bare reaction.
_CLOSING_ANCHOR = _THANKS_WORDS | _FILLER_WORDS | frozenset({"appreciate", "appreciated", "worries", "np", "ta", "leave", "skip", "drop"})
#: Tokens that make a message ask for something: an acceptance, a go-ahead, a rerun, or a cue to do work.
_CLOSING_BLOCKERS = _ACCEPT_WORDS | _GO_TOKENS | frozenset({"need", "more", "further", "any", "anything", "something", "also"})


_BARE_GO_MAX_AGE = 0
_ARBITRATE_MAX_AGE = 1


def _tokens(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", str(text or "").lower())


#: Fixed go-ahead / locus phrases whose preposition is not a "where" ("go on and", "done on my side").
_NEUTRAL_RE = re.compile(r"\b(?:go|carry|move)\s+on\b|\bon\s+my\s+(?:side|end)\b|\bat\s+my\s+end\b", re.IGNORECASE)


def _own_task(text: str) -> bool:
    """Whether the message brings a task of its own (a question word, a change/where phrase, or an instruction)."""
    from core.agent_runtime.answer_coverage import asks_for_work_of_its_own

    text = _NEUTRAL_RE.sub(" ", text)

    if _INVERTED_QUESTION_RE.match(text.strip()):
        return True
    return bool(_CHANGE_RE.search(text)) or asks_for_work_of_its_own(text)


_INVERTED_QUESTION_RE = re.compile(
    r"^(?:is|are|was|were|does|do|did|can|could|will|would|should|has|have|had|any|anything)\b[^?]*\?\s*$", re.IGNORECASE
)


def _read_only(pending: PendingRequest) -> bool:
    """Whether the recorded request only reads. An unknown tool counts as NOT read-only (fail closed), except a request
    the web switch refused: that lookup is a read by definition of the switch (no tool is named on that producer)."""
    if pending.outcome == ANSWERED:
        return True  # nothing ran; the request is rerun as text
    if (str(pending.reason or "") == "web_off" or str(pending.reason or "").startswith("missing_right:")) and not pending.slots.get("tool"):
        return True
    try:
        from core.capability_graph import capability_for_intent

        capability = capability_for_intent(str(pending.slots.get("tool") or ""))
    except Exception:
        return False
    return bool(capability) and str(capability).endswith(".read")


_MODE_REASONS = frozenset({"blocked_by_mode", "permission_denied", "disabled", "denied", "approval_denied", "pending_approval"})


def blocker_cleared(pending: PendingRequest, source_context: dict[str, Any] | None = None) -> bool | None:
    """Whether what stopped the recorded request is gone NOW. ``None`` = unknown (never treated as cleared).

    Read from live state per reason code: ``web_off`` -> the web is available; ``owner_local_required`` -> this turn is
    the owner's own session; a mode refusal -> the permission authority no longer denies the recorded tool (an allow, or
    a request for approval, is cleared: the retry then raises the normal approval card).
    """
    reason = str(pending.reason or "").strip().lower()
    try:
        if reason == "web_off":
            from core.vool_browser import live

            return bool(live.available())
        if reason.startswith("missing_right:"):
            if source_context is None:
                return None
            from core.request_trust import remote_turn_has_grant, turn_is_remote

            if not turn_is_remote(source_context):
                return True  # the owner's own session needs no phone right
            return bool(remote_turn_has_grant(source_context, reason.split(":", 1)[1]))  # same live source the producer checks
        if reason == "owner_local_required":
            if source_context is None:
                return None
            from core.request_trust import turn_is_remote

            return not turn_is_remote(source_context)
        if reason in _MODE_REASONS:
            tool = str(pending.slots.get("tool") or "")
            if not tool:
                return None
            from core.authorized_tool_execution import authorize_runtime_tool

            decision = authorize_runtime_tool(tool, {}, source_context=dict(source_context or {}))
            if decision is None:
                return None
            return str(getattr(decision.effect, "value", decision.effect)) != "deny"
    except Exception:
        return None
    return None


def _split_cue(compact: str) -> tuple[bool, str]:
    """``(has_cue, remainder)``: the message with its retry cue (and the do-verb around it) removed."""
    if not _CUE_RE.search(compact):
        return False, compact
    rest = _CUE_STRIP_RE.sub(" ", compact, count=1)
    rest = " ".join(rest.replace(",", " ").split()).strip(" .!?-;:")
    return True, rest


def _is_plain_remainder(rest: str, base_slots: dict[str, Any]) -> bool:
    """Empty, only acknowledgements, or a report about the blocker -- and nothing that changes what runs."""
    toks = _tokens(rest)
    if all(t in _ACK_WORDS for t in toks):
        return True
    if apply_change(base_slots, rest) != dict(base_slots) or _CHANGE_RE.search(_NEUTRAL_RE.sub(" ", rest)) or _own_task(rest):
        return False
    return bool(_STATE_REPORT_RE.search(rest) or _GO_RE.search(rest))


_ORD = {"first": 1, "second": 2, "third": 3, "fourth": 4, "fifth": 5, "sixth": 6, "seventh": 7, "eighth": 8, "ninth": 9, "tenth": 10,
        "1st": 1, "2nd": 2, "3rd": 3, "4th": 4, "5th": 5}
_COUNT = {"two": 2, "three": 3, "four": 4, "five": 5, "2": 2, "3": 3, "4": 4, "5": 5, "6": 6, "7": 7, "8": 8, "9": 9, "10": 10}
_PICK_RE = re.compile(
    r"\b(?:the\s+)?(?P<edge>first|last)\s+(?P<n>two|three|four|five|\d{1,2})(?:\s+(?:ones|items|results|files|folders|pics|pictures))?\b"
    r"|\b(?:the\s+)?(?P<ord>first|second|third|fourth|fifth|sixth|seventh|eighth|ninth|tenth|last|\d{1,2}(?:st|nd|rd|th))(?!\s+(?:time|week|year|night|month|day|few))(?:\s+(?:one|result|item|file|folder|pic|picture))?\b"
    r"|(?:^|\b(?:the|open|show|get|grab|pick|take|whats|what's|is|on|at|with|me)\s+)(?P<nm>(?:number|no\.?)\s*(?P<num>\d{1,2}))\b|#\s*(?P<num2>\d{1,2})\b|\b(?:both|all\s+of\s+them)\b",
    re.IGNORECASE,
)
_PICK_MAX_AGE = 3
#: "newest first", "most recent last", "sorted A to Z first": the word before first/last says how to order.
_ORDER_BEFORE_RE = re.compile(r"\b(?:\w+est|recent|alphabetical(?:ly)?|order(?:ed)?|sorted)\s*$", re.IGNORECASE)
#: A bare yes: the closed class of affirmation words. Used only against a structured offer (an open approval request).
_AFFIRM_WORDS = frozenset({"yes", "yep", "yeah", "yup", "y", "sure", "ok", "okay", "k", "do", "it", "go", "ahead", "please", "pls",
                           "proceed", "confirm", "approve", "approved", "sounds", "good", "fine", "that", "works", "then", "alright"})


#: A pointer relative to the item picked last: "no not that one, the one before it", "the next one".
_RELATIVE_PICK_RE = re.compile(
    r"\b(?:the\s+)?(?:one|file|item|invoice|pic|picture)\s+(?P<before>before|above)\s*(?:it|that|this)?\b"
    r"|\b(?:the\s+)?(?:one|file|item|invoice|pic|picture)\s+(?P<after>after|below)\s*(?:it|that|this)?\b"
    r"|\bthe\s+(?P<prev>previous|prior)\s+(?:one|file|item)\b|\bthe\s+(?P<next>next)\s+(?:one|file|item)\b",
    re.IGNORECASE,
)


def _relative_pick(compact: str, pending: PendingRequest | None) -> Resolution | None:
    """"the one before it" after "the second one": the neighbour, in the printed list, of the item picked last.

    Red team R1-09 (2026-11-12): "no not that one, the one before it" after a pick from a listed folder
    answered "Let me check that for you. One moment!" and ran nothing.
    """
    m = _RELATIVE_PICK_RE.search(compact)
    if not m or pending is None:
        return None
    listed = pending.slots.get("listed") or []
    picked = pending.slots.get("picked") or []
    if not listed or not picked or pending.turns_since > _PICK_MAX_AGE:
        return Resolution("referent_unresolved", note="The message points at a neighbour of an item this chat has not picked from a printed list; ask which item.")
    paths = [str(item.get("path") or "") for item in listed]
    last = str(picked[-1])
    if last not in paths:
        return Resolution("referent_unresolved", note="The item picked last is not in the printed list; ask which item.")
    step = -1 if (m.group("before") or m.group("prev")) else 1
    index = paths.index(last) + step
    if not 0 <= index < len(paths):
        return Resolution("referent_unresolved", note=f"There is no item {'before' if step < 0 else 'after'} {last} in the printed list; ask which one.")
    chosen = listed[index]
    name = f"“{chosen['path']}”"
    a, b = m.span()
    rewritten = (compact[:a] + name + compact[b:]).strip()
    return Resolution("pick", rewritten, {"picked": [chosen["path"]], "listed": listed},
                      f"“{compact}” points at: {name} (next to the item picked last, in the list printed earlier in this chat).")


def _pick(compact: str, pending: PendingRequest | None) -> Resolution | None:
    """An ordinal pointer ("the second one", "the last two", "number 3") against the list the chat printed last."""
    relative = _relative_pick(compact, pending)
    if relative is not None:
        return relative
    m = _PICK_RE.search(compact)
    if not m:
        return None
    if m.group("ord") and m.group("ord").lower() in {"first", "last"} and _ORDER_BEFORE_RE.search(compact[:m.start("ord")]):
        # "list the members, newest first" names an order, not an item: measured on the Mac (Database run 16b,
        # 2026-11-12) it was read as a pointer into an unprinted list, and the reply asked "which list or which item".
        return None
    listed = (pending.slots.get("listed") if pending else None) or []
    if not listed or (pending and pending.turns_since > _PICK_MAX_AGE):
        return Resolution("referent_unresolved", note="The message points at an item of a list this chat has not printed (or printed long ago); ask which list or which item.")
    n = len(listed)
    if m.group("edge"):
        k = _COUNT[m.group("n").lower()]
        picked = listed[:k] if m.group("edge").lower() == "first" else listed[-k:]
    elif m.group("ord"):
        o = m.group("ord").lower()
        k = _ORD.get(o) or (int(re.sub(r"\D", "", o)) if o != "last" else 0)
        picked = [listed[-1]] if o == "last" else ([listed[k - 1]] if 1 <= k <= n else [])
    elif m.group("num") or m.group("num2"):
        i = int(m.group("num") or m.group("num2"))
        picked = [listed[i - 1]] if 1 <= i <= n else []
    elif n == 2:
        picked = list(listed)
    else:
        return None  # "both" / "all of them" only points when the printed list has exactly two items
    if not picked:
        shown = ", ".join(f"`{item['path']}`" for item in listed[:5]) + (" and more" if n > 5 else "")
        asked = k if m.group("ord") else (int(m.group("num") or m.group("num2")) if (m.group("num") or m.group("num2")) else 0)
        reply = (f"That list has only {n} item{'s' if n != 1 else ''} ({shown})"
                 + (f", so there is no item {asked}." if asked else "."))
        return Resolution(
            "referent_unresolved",
            note=f"The list printed earlier has {n} item(s); the pointer is outside it. Ask which one.",
            answer=reply,
        )
    names = ", ".join(f"“{i['path']}”" for i in picked)
    a, b = m.span("nm") if m.group("nm") else m.span()
    rewritten = (compact[:a] + names + compact[b:]).strip()
    # The printed list rides along, so the next pointer ("the one before it") still has it after this
    # pick's own turn replaces the record.
    return Resolution("pick", rewritten, {"picked": [i["path"] for i in picked], "listed": listed},
                      f"“{compact}” points at: {names} (from the list printed earlier in this chat).")


def resolve(
    session_id: str,
    text: str,
    *,
    pending: PendingRequest | None = None,
    arbiter: Any = None,
    source_context: dict[str, Any] | None = None,
    cleared: bool | Any | None = "probe",
) -> Resolution:
    """Decide what a message is against the chat's record: retry / retry_changed / referent_unresolved / none.

    Order (core of the design: state first, wording last):
    1. A retry CUE with a remainder that changes what runs -> ``retry_changed`` (deterministic; recorded request + change,
       slots updated); a cue with nothing but acknowledgements / a report about the blocker -> ``retry``. A cue wins over a
       thanks word ("retry thx"); a dismissal word in the remainder drops it.
    2. No cue, the record unfinished and fresh, the message short, no task of its own, no dismissal:
       the blocker is CLEARED (``blocker_cleared``) -> ``retry`` with no model call, except a bare reaction ("lol ok");
       not cleared / unknown -> a state report or bare go retries (age 0); anything else asks ``arbiter(request, reason,
       message) -> "yes" | "no" | None`` (yes/no: is the user asking to proceed or saying the obstacle is gone). Only "yes"
       on a request that only reads counts; error / None = not a retry.
    3. A no-cue message with a task of its own is a new task (``none``).
    """
    compact = " ".join(str(text or "").split())
    if not compact:
        return Resolution("none")
    pending = pending if pending is not None else load(session_id)
    picked = _pick(compact, pending)
    if picked is not None:
        return picked
    if is_plain_closing(compact):
        # No task in the message: whatever the record says, nothing runs and nothing of the record rides along.
        return Resolution("none", note=CLOSING_NOTE, answer=closing_reply(compact), closing=True)
    if pending is not None and pending.outcome == NEEDS_ANSWER and pending.turns_since == 0 and _read_only(pending):
        toks = _tokens(compact)
        if toks and len(toks) <= 5 and all(t in _AFFIRM_WORDS for t in toks):
            # "yes do it" after VOOL's own open request: the offered read runs again as a fresh turn (approval applies again)
            return Resolution("retry", pending.request, dict(pending.slots), _note(pending))
    # `core.attempt_followup` has already had its turn (it runs before this resolver in the turn spine).
    if pending is not None and len(compact.split()) <= 14:
        has_cue, rest = _split_cue(compact)
        if re.match(r"^(?:what|who|whom|whose|why|how|when|where|which)\b", compact, re.IGNORECASE):
            has_cue = False  # a question ABOUT retrying ("how do i try again?") is not a retry
        unfinished = pending.outcome in _UNFINISHED
        if has_cue and pending.turns_since <= _CUE_MAX_AGE and (unfinished or pending.outcome in {DONE, ANSWERED}):
            if _DISMISS_RE.search(rest) or _DISMISS_WHOLE_RE.match(rest):
                pass
            elif _is_plain_remainder(rest, pending.slots):
                return Resolution("retry", pending.request, dict(pending.slots), _retry_note(pending))
            elif _read_only(pending):
                return Resolution("retry_changed", pending.request, apply_change(pending.slots, rest), _retry_note(pending), change=rest)
        elif not has_cue and unfinished and pending.turns_since <= _ARBITRATE_MAX_AGE:
            state = blocker_cleared(pending, source_context) if cleared == "probe" else cleared
            if state is True:
                # Refused, the person has since removed the blocker (live state), the record is fresh and only reads:
                # any short message retries unless it is an exit (dismissal, a task or question of its own, thanks alone).
                if _read_only(pending) and not _cleared_exit(compact, pending):
                    return Resolution("retry", pending.request, dict(pending.slots), _note(pending))
            elif (
                not _DISMISS_RE.search(compact) and not _THANKS_RE.search(compact) and not _DISMISS_WHOLE_RE.match(compact)
                and not _own_task(compact)
            ):
                if (_GO_RE.search(compact) or _STATE_REPORT_RE.search(compact)) and pending.turns_since <= _BARE_GO_MAX_AGE:
                    return Resolution("retry", pending.request, dict(pending.slots), _note(pending))
                if arbiter is not None:
                    try:
                        verdict = arbiter(pending.request, pending.reason or pending.outcome, compact)
                    except Exception:
                        verdict = None
                    if verdict == "yes" and _read_only(pending):
                        return Resolution("retry", pending.request, dict(pending.slots), _note(pending))
    folder_pointer = _ANAPHORIC_FOLDER_RE.search(compact)
    if folder_pointer and not (pending and pending.slots.get("path")) and (
        _NAMES_A_FOLDER_RE.search(folder_pointer.group(0)) or _record_is_about_files(pending)
    ):
        return Resolution("referent_unresolved", note=("The message points at a folder this chat has not resolved; ask which folder. " + (_note(pending) if pending else "")).strip())
    if is_closing(compact):
        # A thanks or a dismissal is never the previous task again: the record's slots and request stay out of the turn.
        return Resolution("none", note=CLOSING_NOTE)
    note = _note(pending) if pending and _looks_back_at_the_record(compact, pending) else ""
    return Resolution("none", slots=dict(pending.slots) if pending else {}, note=note)


_NAMES_A_FOLDER_RE = re.compile(r"\b(?:folders?|director(?:y|ies)|dir)\b", re.IGNORECASE)
#: Words that point back at what the chat last did, or ask about how it ended.
_LOOKS_BACK_RE = re.compile(
    r"\b(?:it|that|this|those|them|again|why|what\s+happened|went\s+wrong|error|errors|failed|fail|failing|broke|broken|"
    r"stuck|retry|redo|same)\b",
    re.IGNORECASE,
)


def _record_is_about_files(pending: PendingRequest | None) -> bool:
    """Whether "in there" can mean a folder: no record yet, or the record is a folder or file ask.

    In a chat whose last request read a database or the web, "what's the oldest book in there" points at that, not
    at a folder: measured on the Mac (Database run 16b, 2026-11-12), it got "points at a folder ... ask which folder"
    and the reply asked which list the person meant."""
    if pending is None:
        return True
    slots = pending.slots or {}
    if slots.get("listed") or slots.get("kind"):
        return True
    tool = str(slots.get("tool") or "").lower()
    return not tool or tool.startswith("machine.") or "file" in tool or "folder" in tool or "director" in tool


def _looks_back_at_the_record(compact: str, pending: PendingRequest) -> bool:
    """Whether a message that is not a retry still needs to know how the chat's last request ended.

    "why did it fail?" or "ok" right after a refusal does; a new ask of its own does not, and being told "Earlier in this
    chat the user asked: ... It ended failed." beside it led the model to answer the old request or to call the new one
    unsupported (Database run 16b on the Mac, 2026-11-12: "is Ines holding any books at the moment" carried the failed
    "add a notes column" ask)."""
    if pending.turns_since > _CUE_MAX_AGE:
        return False
    return len(compact.split()) <= 3 or bool(_LOOKS_BACK_RE.search(compact))


def apply_change(slots: dict[str, Any], change: str) -> dict[str, Any]:
    """The recorded slots with the user's change applied where it names a folder, a kind of file, a count or an order."""
    from core.execution import constants as c

    out = dict(slots or {})
    folder = c._KNOWN_FOLDER_RE.search(change)
    if folder:
        out["path"] = c._KNOWN_USER_FOLDERS[folder.group(1).lower()]
    kind = c._KIND_WORD_RE.search(change)
    if kind and (not folder or kind.span(1) != folder.span(1)):
        out["kind"] = c._LISTING_KIND_WORDS[kind.group(1).lower()]
    count = c._NEWEST_COUNT_RE.search(change)
    if count:
        out["newest_first"] = True
        digits = count.group(1) or count.group(2)
        if digits:
            out["limit"] = max(1, min(int(digits), 200))
    return out


def _retry_note(pending: PendingRequest) -> str:
    """The note a RERUN carries. An answered ask has nothing to report about how it ended: told "it ended answered" next
    to the same question, a model describes the request or the earlier answer instead of answering it."""
    if pending.outcome != ANSWERED:
        return _note(pending)
    return ("The person asked this again and wants the answer itself, in full, written to them now. "
            "Answer the question directly; do not describe the request, say it was asked before, or refer to the earlier reply.")


def _note(pending: PendingRequest) -> str:
    where = f" Folder: {pending.slots['path']}." if pending.slots.get("path") else ""
    why = f" ({pending.reason})" if pending.reason else ""
    return f"Earlier in this chat the user asked: “{pending.request}”. It ended {pending.outcome}{why}.{where}"


__all__ = ["CLOSING_NOTE", "PendingRequest", "Resolution", "closing_reply", "is_closing", "is_plain_closing", "is_social_only", "load", "outcome_of", "record_sealed_turn", "resolve", "slots_of"]
