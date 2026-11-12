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
    "man", "bro", "dude", "buddy", "repeat", "it", "all", "yours", "mine", "ours", "can", "could", "would", "will", "u", "still", "yet", "nothing", "anything", "something", "give", "done", "try", "shot", "crack", "go",
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
    "do", "does", "did", "see", "show", "look", "its", "it's", "any", "got", "found", "up", "out", "and", "or", "be", "been", "to",
    "let", "lemme", "gimme", "give", "so", "well", "hm", "mm", "mmm", "eh", "um", "uh", "at", "in", "of", "have", "a", "an",
})
_AUX_START = frozenset({"is", "are", "was", "were", "does", "do", "did", "can", "could", "will", "would", "should", "has", "have", "had"})
_PROMPT_WORDS = frozenset({"work", "works", "working", "now", "them", "those", "these", "u", "ya", "ready", "yet"})
_NO_WH_RE = re.compile(_WH_RE.pattern, re.IGNORECASE)


def _cleared_exit(compact: str) -> bool:
    """The closed set of messages that are NOT a retry although the blocker is cleared: a dismissal or postponement, thanks
    alone, a question or task with content of its own."""
    if _DISMISS_RE.search(compact) or _DISMISS_WHOLE_RE.match(compact):
        return True
    toks = _tokens(compact)
    if toks and all(t in _THANKS_WORDS or t in _ACK_WORDS or t in _THANKS_TAIL for t in toks) and any(t in _THANKS_WORDS for t in toks):
        return True
    if _WH_RE.search(compact) and any(t not in _FUNCTION_WORDS for t in toks):
        return True  # a question with content of its own
    if toks and toks[0] in _AUX_START and any(t not in _FUNCTION_WORDS and t not in _PROMPT_WORDS for t in toks):
        return True  # an auxiliary-first question with content of its own ("does it cost anything"); "does it work now" is a prompt
    rest = _WH_RE.sub(" ", compact)
    content = [t for t in toks if t not in _FUNCTION_WORDS]
    if len(content) <= 1 and not _CHANGE_RE.search(_NEUTRAL_RE.sub(" ", rest)):
        return False  # one bare verb or noun has nothing of its own to do it to
    if len(content) >= 3:
        return True  # three words of content: it says something of its own
    if len(content) == 2:
        return True  # two content words: it says something of its own (no past-tense guess; "i ticked the box" is a miss)
    return _own_task(rest)


_THANKS_TAIL = frozenset({"anyway", "anyways", "though", "tho", "much", "again", "mate", "dude", "man"})
_THANKS_WORDS = frozenset({"thanks", "thank", "thx", "ty", "cheers", "tyvm"})
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
    if str(pending.reason or "") == "web_off" and not pending.slots.get("tool"):
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


def resolve(
    session_id: str,
    text: str,
    *,
    pending: PendingRequest | None = None,
    arbiter: Any = None,
    source_context: dict[str, Any] | None = None,
    cleared: bool | None | Any = "probe",
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
    # `core.attempt_followup` has already had its turn (it runs before this resolver in the turn spine).
    if pending is not None and len(compact.split()) <= 14:
        has_cue, rest = _split_cue(compact)
        if re.match(r"^(?:what|who|whom|whose|why|how|when|where|which)\b", compact, re.IGNORECASE):
            has_cue = False  # a question ABOUT retrying ("how do i try again?") is not a retry
        unfinished = pending.outcome in _UNFINISHED
        if has_cue and pending.turns_since <= _CUE_MAX_AGE and (unfinished or pending.outcome == DONE):
            if _DISMISS_RE.search(rest) or _DISMISS_WHOLE_RE.match(rest):
                pass
            elif _is_plain_remainder(rest, pending.slots):
                return Resolution("retry", pending.request, dict(pending.slots), _note(pending))
            elif _read_only(pending):
                return Resolution("retry_changed", pending.request, apply_change(pending.slots, rest), _note(pending), change=rest)
        elif not has_cue and unfinished and pending.turns_since <= _ARBITRATE_MAX_AGE:
            state = blocker_cleared(pending, source_context) if cleared == "probe" else cleared
            if state is True:
                # Refused, the person has since removed the blocker (live state), the record is fresh and only reads:
                # any short message retries unless it is an exit (dismissal, a task or question of its own, thanks alone).
                if _read_only(pending) and not _cleared_exit(compact):
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


__all__ = ["PendingRequest", "Resolution", "load", "record_sealed_turn", "resolve", "outcome_of", "slots_of"]
