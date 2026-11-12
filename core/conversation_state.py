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
_UNFINISHED = frozenset({REFUSED, FAILED})
#: A bare go-ahead (no retry cue) may only answer a refusal that was the LAST thing said; with turns in between it may be
#: answering something else (VOOL's own proposal), so it goes to the turn as written.
_BARE_GO_MAX_AGE = 0
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
    kind: str  # "retry" | "retry_changed" | "referent_unresolved" | "none"
    request: str = ""
    slots: dict[str, Any] = field(default_factory=dict)
    note: str = ""
    change: str = ""

    @property
    def text(self) -> str:
        """The text the turn is ROUTED as. The transcript keeps what the user typed (persistent_memory)."""
        if self.kind == "retry_changed":
            return f"{self.request} (change: {self.change})"
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


def outcome_of(result: dict[str, Any] | None) -> tuple[str, str, bool]:
    """``(outcome, reason, actionable)`` for a sealed turn result, from its structured fields only."""
    result = result or {}
    details = result.get("details") if isinstance(result.get("details"), dict) else {}
    receipts = [r for r in (result.get("tool_receipts") or []) if isinstance(r, dict)]
    status = str(result.get("status") or "").strip().lower()
    task_outcome = str(result.get("task_outcome") or "").strip().lower()
    receipt_status = str((receipts[0].get("execution") or {}).get("status") or "").strip().lower() if receipts else ""
    reasons = [status, str(details.get("reason") or "").strip().lower(), receipt_status]
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
                    conn.execute(
                        "INSERT OR REPLACE INTO conversation_state (session_id, request, outcome, reason, slots, turns_since, updated_at) "
                        "VALUES (?, ?, ?, ?, ?, 0, ?)",
                        (sid, text[:2000], outcome, reason[:200], json.dumps(slots, sort_keys=True, default=str), _now()),
                    )
                else:
                    conn.execute("UPDATE conversation_state SET turns_since = turns_since + 1 WHERE session_id = ?", (sid,))
                conn.commit()
            finally:
                conn.close()
    except Exception:
        return


# --- reading a message against the record ---------------------------------------------------------------------------
#: A retry cue: a retry verb, or a do-verb with "again" / "once more". Function words, not sentences.
_CUE_RE = re.compile(
    r"\b(?:re-?try|re-?run|re-?do|re-?send|again|once\s+more|one\s+more\s+time)\b", re.IGNORECASE
)
#: The message reports a changed state ("i fixed it", "access is on now", "should work now") or says to go.
_STATE_REPORT_RE = re.compile(
    r"\b(?:fixed|enabled|allowed|granted|unlocked|approved|turned\s+(?:it\s+)?on|switched\s+(?:it\s+)?on|is\s+on|are\s+on)\b"
    r"|\b(?:should|will|can|does)\s+work\b|\bworks?\s+now\b|^(?:ok(?:ay)?\s+)?(?:now\s+)?(?:go|go\s+ahead|do\s+it|proceed|try)(?:\s+now)?$",
    re.IGNORECASE,
)
#: Words that change WHAT would be redone; a message holding one is a request of its own.
_CHANGE_RE = re.compile(
    r"\b(?:but|instead|only|except|also|plus|without|different|another|other|new|more|less|fewer|rather)\b"
    r"|\b(?:in|on|from|inside|into|under|for|with|to|at|of|about)\s+(?:the|my|this|that|those|these|a|an|\d+|~|/)"
    r"|[~/]\S|\b(?:what|who|whom|whose|why|how|when|where|which)\b|\?",
    re.IGNORECASE,
)
_ANAPHORIC_FOLDER_RE = re.compile(
    r"\b(?:that|those|the\s+same|same|previous|other)\s+(?:\w+\s+)?(?:folder|folders|directory|dir)\b|\b(?:in|from|inside)\s+there\b",
    re.IGNORECASE,
)


_CUE_STRIP_RE = re.compile(
    r"\b(?:(?:try|run|do|go|give|send|have)\s+(?:it\s+|that\s+|this\s+|them\s+)?)?"
    r"(?:re-?try|re-?run|re-?do|re-?send|again|once\s+more|one\s+more\s+time)\b|\b(?:ok(?:ay)?|please|pls|so|yes|yep|alright)\b",
    re.IGNORECASE,
)


_CLOSING_RE = re.compile(r"\b(?:thanks|thank\s+you|thx|bye|cheers|never\s*mind|nvm|forget\s+it|leave\s+it|don'?t|do\s+not|stop|cancel|later|for\s+now)\b", re.IGNORECASE)


def _carries_new_task(text: str) -> bool:
    """Whether the message brings a task of its own once the retry cue and acknowledgements are set aside."""
    from core.agent_runtime.answer_coverage import asks_for_work_of_its_own

    rest = " ".join(_CUE_STRIP_RE.sub(" ", text).replace(",", " ").split())
    return bool(_CHANGE_RE.search(rest)) or (bool(rest) and asks_for_work_of_its_own(rest))


#: A fresh unfinished request is one the very last turn (or the one before) left; only then is a message with no retry
#: word asked about.
_ARBITRATE_MAX_AGE = 1
_DISMISS_WHOLE_RE = re.compile(r"^(?:no|nope|nah|nothing|skip\s+it|drop\s+it)[\s.!]*$", re.IGNORECASE)


def _read_only(pending: PendingRequest) -> bool:
    """Whether the recorded request only reads. An unknown tool counts as NOT read-only (fail closed)."""
    try:
        from core.capability_graph import capability_for_intent

        capability = capability_for_intent(str(pending.slots.get("tool") or ""))
    except Exception:
        return False
    return bool(capability) and str(capability).endswith(".read")


def resolve(session_id: str, text: str, *, pending: PendingRequest | None = None, arbiter: Any = None) -> Resolution:
    """Decide what a message is against the chat's record: retry / referent_unresolved / none.

    ``arbiter(request, reason, message) -> "again" | "changed" | "neither" | None`` is the model arbitration
    (core.intent_arbiter.arbitrate_followup). It is asked only when the record is refused/failed and fresh, and the message
    is short with no task of its own and no closing word: the cases a retry-word list cannot decide. Its answer is honoured
    (again -> retry, changed -> retry with the change) only for a request that only reads; any other answer, an error or None is "not a retry" (fail closed).
    """
    compact = " ".join(str(text or "").split())
    if not compact:
        return Resolution("none")
    pending = pending if pending is not None else load(session_id)
    # `core.attempt_followup` has already had its turn (it runs before this resolver in the turn spine).
    closing = bool(_CLOSING_RE.search(compact)) or bool(_DISMISS_WHOLE_RE.match(compact))
    short = len(compact.split()) <= 14 and not closing
    if short and pending is not None:
        own_task = _carries_new_task(compact)
        has_cue = bool(_CUE_RE.search(compact)) or bool(re.match(r"^(?:ok(?:ay)?\s+|please\s+)?(?:try|retry)\b", compact, re.IGNORECASE))
        reports = bool(_STATE_REPORT_RE.search(compact))
        if pending.outcome in _UNFINISHED:
            if not own_task and ((has_cue and pending.turns_since <= _CUE_MAX_AGE) or (reports and pending.turns_since <= _BARE_GO_MAX_AGE)):
                return Resolution("retry", pending.request, dict(pending.slots), _note(pending))
            # Whether the rest is a retry cannot be listed. A fresh unfinished request makes the arbiter decide, including
            # for messages that look like a task of their own (the arbiter answers NEW for those).
            if arbiter is not None and pending.turns_since <= _ARBITRATE_MAX_AGE:
                try:
                    verdict = arbiter(pending.request, pending.reason or pending.outcome, compact)
                except Exception:
                    verdict = None
                if verdict in ("again", "changed") and _read_only(pending):
                    if verdict == "again":
                        return Resolution("retry", pending.request, dict(pending.slots), _note(pending))
                    return Resolution("retry_changed", pending.request, apply_change(pending.slots, compact), _note(pending), change=compact)
        elif not own_task and has_cue and pending.turns_since <= _CUE_MAX_AGE and pending.outcome == DONE:
            return Resolution("retry", pending.request, dict(pending.slots), _note(pending))
    if _ANAPHORIC_FOLDER_RE.search(compact) and not (pending and pending.slots.get("path")):
        return Resolution("referent_unresolved", note=("The message points at a folder this chat has not resolved; ask which folder. " + (_note(pending) if pending else "")).strip())
    return Resolution("none", slots=dict(pending.slots) if pending else {}, note=_note(pending) if pending else "")


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


def _note(pending: PendingRequest) -> str:
    where = f" Folder: {pending.slots['path']}." if pending.slots.get("path") else ""
    why = f" ({pending.reason})" if pending.reason else ""
    return f"Earlier in this chat the user asked: “{pending.request}”. It ended {pending.outcome}{why}.{where}"


__all__ = ["PendingRequest", "Resolution", "load", "outcome_of", "record_sealed_turn", "resolve", "slots_of"]
