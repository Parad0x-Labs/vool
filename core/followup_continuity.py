"""A message sent while a task runs must not break, pause or duplicate that task.

Before this module a chat message that arrived while a turn was still working waited behind it:
the page queued it, the per-session turn gate (`core/session_turn_gate.py`) held it, and it ran as
a NEW turn only after the task had finished. A question waited for the whole task. A correction
("also add tests") never reached the work it was about; it started a second task afterwards. A
typed "stop" was queued like any other text and stopped nothing.

This module is the one authority for what a follow-up does while a task is live in its chat:

* ``stop`` -- an explicit stop/cancel. The only thing that ends the task early.
* ``question`` -- answered right away by a side answer that cannot act (no tools, no effects) and
  does not wait on the task's turn gate. The task keeps running.
* ``note`` -- attached to the running task. The task picks it up at its next step.
* ``conflict`` -- a note that would change or undo the task's plan. It is held, and the user is
  asked before the task acts on it. ``yes`` hands it to the task, ``no`` drops it.
* ``passthrough`` -- a whole-message command another authority owns (the x402 brake, a full VOOL
  shutdown). It runs as an ordinary turn.

Nothing a follow-up attaches is lost. A note the task never reached before it ended (it finished
first, or it never took another step) is carried into the chat's persisted message queue and runs
next, in order, in the user's own words.

The state is in-process, like `core.live_turns`: a task lives exactly as long as its worker, and a
restart ends the worker. It is keyed by ``(session_id, turn_id)`` -- the client turn id the cancel
registry already uses -- so a note can never reach another chat's task, or a later task in the same
chat.

Classification is deterministic and deliberately conservative: anything that is not clearly a
question, a stop or a plan change is a note, and a note is always delivered to the task (or run
next). The task's model is also told that a note which contradicts work already done must be
confirmed with the user before it is undone, so a plan change the patterns miss is still asked
about rather than silently applied.
"""

from __future__ import annotations

import re
import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

__all__ = [
    "FOLLOWUP_NOTES_CONTEXT_KEY",
    "SIDE_ANSWER_CONTEXT_KEY",
    "SIDE_ANSWER_SNAPSHOT_KEY",
    "active_task",
    "begin_task",
    "classify_followup",
    "drain_notes_into_context",
    "end_task",
    "handle_followup",
    "note_progress",
    "render_prompt_context",
    "reset_followup_state",
    "task_snapshot",
]

# The running task's delivered notes, cumulative, on its source context (prompt assembly reads it).
FOLLOWUP_NOTES_CONTEXT_KEY = "operator_followup_notes"
# Set by the server (never trusted from a caller) on a side answer to a question asked mid-task.
SIDE_ANSWER_CONTEXT_KEY = "followup_side_answer"
# The running task's request and progress, as the side answer's model sees them.
SIDE_ANSWER_SNAPSHOT_KEY = "followup_active_task_snapshot"

_MAX_PROGRESS = 12
_MAX_NOTES = 50
_MAX_NOTE_CHARS = 4000
_MAX_IDEMPOTENCY_KEYS = 200


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── classification ───────────────────────────────────────────────────────────────────────────────

KIND_STOP = "stop"
KIND_QUESTION = "question"
KIND_NOTE = "note"
KIND_CONFLICT = "conflict"
KIND_CONFIRM = "confirm"
KIND_DECLINE = "decline"
KIND_PASSTHROUGH = "passthrough"

# These patterns read `classify_followup`'s collapsed text (single spaces, no leading or trailing
# space), so each whitespace run is matched by exactly one quantifier: two quantifiers that could
# both take the same spaces make a long run of them quadratic to reject.
#
# Whole-message only. "stop using pandas" is a plan change, "should I stop it?" is a question, and
# "stop everything" / "stop vool" belong to the shutdown brake -- none of them is this stop.
_STOP_RE = re.compile(
    r"^(?:please\s+)?(?:ok(?:ay)?(?:,\s?|\s))?"
    r"(?:/?stop|/?cancel|abort|halt)"
    r"(?:\s+(?:it|that|this|now|the\s+task|this\s+task|that\s+task|the\s+job|the\s+run|working"
    r"|(?:what|everything)\s+you(?:'re|\s+are)\s+doing))?"
    r"(?:\s+(?:please|now|right\s+now))?(?:\s?[.!]+)?$",
    re.IGNORECASE,
)
_CONFIRM_RE = re.compile(
    r"^(?:yes|yep|yeah|yup|y|ok(?:ay)?|sure|confirm(?:ed)?|go\s+ahead|do\s+it|apply(?:\s+it)?"
    r"|yes(?:,\s?|\s)(?:apply|use|do)\s+(?:it|that|the\s+(?:note|change)))"
    r"(?:\s+please)?(?:\s?[.!]+)?$",
    re.IGNORECASE,
)
_DECLINE_RE = re.compile(
    r"^(?:no|nope|nah|n|don'?t|do\s+not|drop\s+it|discard(?:\s+it)?|ignore\s+(?:it|that)"
    r"|keep\s+going|leave\s+it|no(?:,\s?|\s)(?:drop|ignore|discard)\s+(?:it|that))"
    r"(?:\s+thanks)?(?:\s?[.!]+)?$",
    re.IGNORECASE,
)
_QUESTION_LEAD_RE = re.compile(
    r"^\s*(?:hey[,\s]+|so[,\s]+|and[,\s]+|btw[,\s]+|quick\s+question[,:\s]+)?"
    r"(?:what|what's|whats|why|how|how's|hows|when|where|who|whom|which|whose|is|are|am|was|were"
    r"|do|does|did|have|has|had|will|would|should|shall|could|can|may|might|isn't|aren't|doesn't"
    r"|didn't|won't|wouldn't|shouldn't|can't|couldn't)\b(?!\s+not\b)",
    re.IGNORECASE,
)
# A request phrased as a question is work for the task, not a question for a side answer:
# "can you also add tests?", "could you use the staging bucket instead?".
_REQUEST_AS_QUESTION_RE = re.compile(
    r"^\s*(?:please\s+)?(?:can|could|would|will)\s+you\s+(?:please\s+)?(?:also\s+)?"
    r"(?!tell\b|explain\b|say\b|remind\b|confirm\b|clarify\b|check\s+whether\b)[a-z]",
    re.IGNORECASE,
)
_ADDITIVE_RE = re.compile(r"\b(?:also|too|as\s+well|additionally|in\s+addition)\b", re.IGNORECASE)
# Words that change or undo what the task set out to do. A note carrying one is held and asked about.
_PLAN_CHANGE_RE = re.compile(
    r"\b(?:instead|actually|rather\s+than|never\s*mind|forget\s+(?:it|that|about)|undo|revert|scrap"
    r"|roll\s+back|skip|switch\s+to|change\s+(?:it|that|the|to)|replace|don'?t(?!\s+forget\b)(?!\s+miss\b)"
    r"|do\s+not|stop\s+\w+ing|no\s+longer|not\s+\w+(?:\s?,)?\s+but)\b",
    re.IGNORECASE,
)


def _passthrough_command(text: str) -> bool:
    """A whole-message command another authority owns: the x402 brake and the VOOL shutdown."""
    try:
        from core import vool_agent_brake as brake
    except Exception:  # pragma: no cover - the brake module ships with the runtime
        return False
    for name in ("_STOPALL_RE", "_STOP_RE", "_START_RE"):
        pattern = getattr(brake, name, None)
        if pattern is not None and pattern.match(text):
            return True
    return False


def classify_followup(text: str, *, awaiting_confirmation: bool = False) -> str:
    """What a message sent while a task runs is. Deterministic; see the module docstring."""
    clean = " ".join(str(text or "").split())
    if not clean:
        return KIND_NOTE
    if _passthrough_command(clean):
        return KIND_PASSTHROUGH
    if awaiting_confirmation:
        if _CONFIRM_RE.match(clean):
            return KIND_CONFIRM
        if _DECLINE_RE.match(clean):
            return KIND_DECLINE
    if _STOP_RE.match(clean):
        return KIND_STOP
    request_shaped = bool(_REQUEST_AS_QUESTION_RE.match(clean)) or bool(_ADDITIVE_RE.search(clean))
    question_shaped = clean.endswith("?") or bool(_QUESTION_LEAD_RE.match(clean))
    if question_shaped and not request_shaped:
        return KIND_QUESTION
    if _PLAN_CHANGE_RE.search(clean):
        return KIND_CONFLICT
    return KIND_NOTE


# ── live task registry ───────────────────────────────────────────────────────────────────────────


@dataclass
class _Note:
    note_id: str
    seq: int
    text: str
    created_at: str
    conflict: bool = False
    # pending -> delivered | carried_over ; awaiting_confirmation -> pending | discarded | carried_over
    status: str = "pending"
    delivered_at: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "note_id": self.note_id,
            "seq": self.seq,
            "text": self.text,
            "conflict": self.conflict,
            "status": self.status,
            "created_at": self.created_at,
            "delivered_at": self.delivered_at,
        }


@dataclass
class _Task:
    session_id: str
    turn_id: str
    request_text: str
    started_at: str
    started_seq: int
    notes: list[_Note] = field(default_factory=list)
    progress: list[str] = field(default_factory=list)
    next_note_seq: int = 1


_LOCK = threading.Lock()
_TASKS: dict[tuple[str, str], _Task] = {}
# Per-session results of handled follow-ups, keyed by the client's idempotency key, so a resend or
# reconnect replay of one follow-up never attaches the same note twice.
_HANDLED: dict[str, dict[str, dict[str, Any]]] = {}
_START_SEQ = 0


def _key(session_id: str, turn_id: str) -> tuple[str, str]:
    return (str(session_id or "").strip(), str(turn_id or "").strip())


def begin_task(session_id: str, turn_id: str, request_text: str) -> None:
    """Record a task that is now live in its chat. Called beside `live_turns.register_turn` for a
    real (non side-answer) turn. Idempotent for the same key."""
    global _START_SEQ
    key = _key(session_id, turn_id)
    if not key[0] or not key[1]:
        return
    with _LOCK:
        if key in _TASKS:
            return
        _START_SEQ += 1
        _TASKS[key] = _Task(
            session_id=key[0],
            turn_id=key[1],
            request_text=str(request_text or "")[:_MAX_NOTE_CHARS],
            started_at=_utcnow(),
            started_seq=_START_SEQ,
        )


def active_task(session_id: str) -> dict[str, Any] | None:
    """The live task of this chat (the most recently started one), or None."""
    clean = str(session_id or "").strip()
    if not clean:
        return None
    with _LOCK:
        task = _latest_task_locked(clean)
        return _task_view(task) if task is not None else None


def _latest_task_locked(session_id: str) -> _Task | None:
    candidates = [task for (sid, _), task in _TASKS.items() if sid == session_id]
    if not candidates:
        return None
    return max(candidates, key=lambda task: task.started_seq)


def _task_view(task: _Task) -> dict[str, Any]:
    return {
        "session_id": task.session_id,
        "turn_id": task.turn_id,
        "request_text": task.request_text,
        "started_at": task.started_at,
        "progress": list(task.progress),
        "notes": [note.public() for note in task.notes],
    }


def note_progress(session_id: str, turn_id: str, summary: str) -> None:
    """Keep the task's latest progress lines, so a side question about the task can be answered."""
    clean = " ".join(str(summary or "").split())[:240]
    if not clean:
        return
    with _LOCK:
        task = _TASKS.get(_key(session_id, turn_id))
        if task is None:
            return
        if task.progress and task.progress[-1] == clean:
            return
        task.progress.append(clean)
        del task.progress[:-_MAX_PROGRESS]


def task_snapshot(session_id: str) -> dict[str, Any] | None:
    """What a side answer may know about the running task: its request, progress and notes."""
    return active_task(session_id)


def end_task(
    session_id: str,
    turn_id: str,
    *,
    enqueue: Callable[..., Any] | None = None,
) -> list[dict[str, Any]]:
    """Release a task whose worker has finished, on every exit path.

    Every note the task never picked up (still pending, or still waiting for the user's yes) is
    carried into the chat's persisted message queue in order, so it runs next as the user's own
    message instead of being lost. Returns the carried notes. Idempotent.
    """
    with _LOCK:
        task = _TASKS.pop(_key(session_id, turn_id), None)
        if task is None:
            return []
        undelivered = [note for note in task.notes if note.status in {"pending", "awaiting_confirmation"}]
        for note in undelivered:
            note.status = "carried_over"
        carried = [note.public() for note in undelivered]
    if not carried:
        return []
    enqueue_fn = enqueue or _default_enqueue
    for note in carried:
        try:
            enqueue_fn(
                session_id=task.session_id,
                payload={"text": note["text"], "followup_carried_from_turn": task.turn_id},
                idempotency_key=f"followup-{note['note_id']}",
            )
        except Exception:
            import logging

            logging.getLogger("vool.followup").exception(
                "could not carry an undelivered follow-up note into the chat queue (session=%s)",
                task.session_id,
            )
    return carried


def _default_enqueue(**kwargs: Any) -> Any:
    from core.runtime_continuity import enqueue_message

    return enqueue_message(**kwargs)


def drain_notes_into_context(source_context: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Hand the running task the notes it has not seen yet, at a step boundary.

    Newly delivered notes are appended (in order) to ``source_context[FOLLOWUP_NOTES_CONTEXT_KEY]``,
    which prompt assembly renders on every later model call of the turn. Returns only the notes
    delivered by THIS call, so the caller can announce them once.
    """
    if not isinstance(source_context, dict) or source_context.get(SIDE_ANSWER_CONTEXT_KEY):
        return []
    session_id = str(source_context.get("runtime_session_id") or source_context.get("session_id") or "").strip()
    turn_id = str(source_context.get("cancel_turn_id") or "").strip()
    if not session_id or not turn_id:
        return []
    with _LOCK:
        task = _TASKS.get(_key(session_id, turn_id))
        if task is None:
            return []
        fresh: list[dict[str, Any]] = []
        now = _utcnow()
        for note in task.notes:
            if note.status == "pending":
                note.status = "delivered"
                note.delivered_at = now
                fresh.append(note.public())
    if fresh:
        existing = [dict(item) for item in list(source_context.get(FOLLOWUP_NOTES_CONTEXT_KEY) or []) if isinstance(item, dict)]
        source_context[FOLLOWUP_NOTES_CONTEXT_KEY] = existing + fresh
    return fresh


# ── the follow-up door ───────────────────────────────────────────────────────────────────────────


def _reply(route: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"ok": True, "route": route, "message": message, **extra}


def handle_followup(
    session_id: str,
    text: str,
    *,
    idempotency_key: str = "",
    cancel: Callable[[str, str], str] | None = None,
) -> dict[str, Any]:
    """Decide what one follow-up does to the chat's live task, and do the part that is not a turn.

    Routes:
      ``run_now``            no task is live: run the message as an ordinary turn.
      ``passthrough``        a command another authority owns: run it as an ordinary turn.
      ``answer``             a question: run a side answer (``followup_mode = side_answer``).
      ``note_added``         attached; the task picks it up at its next step.
      ``needs_confirmation`` a plan change: held until the user says yes or no.
      ``note_confirmed`` / ``note_dropped``   the user's answer to that.
      ``stopped``            the task was asked to stop.
    """
    clean_session = str(session_id or "").strip()
    raw_text = str(text or "").strip()
    idem = str(idempotency_key or "").strip()[:120]
    if not clean_session or not raw_text:
        return {"ok": False, "error": "session_id and text are required"}
    if len(raw_text) > _MAX_NOTE_CHARS:
        return {"ok": False, "error": f"a follow-up is limited to {_MAX_NOTE_CHARS} characters"}
    if idem:
        with _LOCK:
            previous = _HANDLED.get(clean_session, {}).get(idem)
        if previous is not None:
            return {**previous, "replayed": True}
    result = _handle_followup_once(clean_session, raw_text, cancel=cancel)
    if idem and result.get("ok"):
        with _LOCK:
            seen = _HANDLED.setdefault(clean_session, {})
            seen[idem] = dict(result)
            while len(seen) > _MAX_IDEMPOTENCY_KEYS:
                seen.pop(next(iter(seen)))
    return result


def _handle_followup_once(
    session_id: str,
    text: str,
    *,
    cancel: Callable[[str, str], str] | None,
) -> dict[str, Any]:
    with _LOCK:
        task = _latest_task_locked(session_id)
        awaiting = bool(task and any(note.status == "awaiting_confirmation" for note in task.notes))
        turn_id = task.turn_id if task else ""
    if task is None:
        return _reply("run_now", "")
    kind = classify_followup(text, awaiting_confirmation=awaiting)
    if kind == KIND_PASSTHROUGH:
        return _reply("passthrough", "", turn_id=turn_id)
    if kind == KIND_STOP:
        cancel_fn = cancel or _default_cancel
        state = cancel_fn(session_id, turn_id)
        if state != "cancelled":
            return _reply("run_now", "", turn_id=turn_id, cancel_state=state)
        return _reply("stopped", "Stopping the running task.", turn_id=turn_id, cancel_state=state)
    if kind == KIND_QUESTION:
        return _reply("answer", "", turn_id=turn_id)
    if kind in {KIND_CONFIRM, KIND_DECLINE}:
        with _LOCK:
            live = _TASKS.get(_key(session_id, turn_id))
            held = [note for note in (live.notes if live else []) if note.status == "awaiting_confirmation"]
            for note in held:
                note.status = "pending" if kind == KIND_CONFIRM else "discarded"
            changed = [note.public() for note in held]
        if not changed:
            return _add_note(session_id, turn_id, text, conflict=False)
        if kind == KIND_CONFIRM:
            return _reply(
                "note_confirmed",
                "Applying your change to the running task at its next step.",
                turn_id=turn_id,
                notes=changed,
            )
        return _reply("note_dropped", "Dropped that change. The task carries on as it was.", turn_id=turn_id, notes=changed)
    return _add_note(session_id, turn_id, text, conflict=(kind == KIND_CONFLICT))


def _add_note(session_id: str, turn_id: str, text: str, *, conflict: bool) -> dict[str, Any]:
    with _LOCK:
        task = _TASKS.get(_key(session_id, turn_id))
        if task is None:
            return _reply("run_now", "")
        if len(task.notes) >= _MAX_NOTES:
            return {"ok": False, "error": f"this task already holds {_MAX_NOTES} notes"}
        note = _Note(
            note_id=f"note-{uuid.uuid4().hex[:16]}",
            seq=task.next_note_seq,
            text=text,
            created_at=_utcnow(),
            conflict=conflict,
            status="awaiting_confirmation" if conflict else "pending",
        )
        task.next_note_seq += 1
        task.notes.append(note)
        public = note.public()
    if conflict:
        return _reply(
            "needs_confirmation",
            "That would change what the running task is doing, so I have not applied it yet. "
            "Reply yes to apply it or no to drop it. The task keeps going meanwhile.",
            turn_id=turn_id,
            note=public,
        )
    return _reply(
        "note_added",
        "Added to the running task. It picks this up at its next step; if it finishes first, "
        "this runs right after.",
        turn_id=turn_id,
        note=public,
    )


def _default_cancel(session_id: str, turn_id: str) -> str:
    from core.live_turns import request_cancel

    return request_cancel(session_id, turn_id)


# ── prompt context ───────────────────────────────────────────────────────────────────────────────


def render_prompt_context(source_context: dict[str, Any] | None) -> str:
    """The context block a model call sees about follow-ups, or "" when there is none.

    For the running task: the notes the user added while it worked. For a side answer: what the
    running task is and how far it got, and that this message must not start or redo it.
    """
    context = source_context or {}
    if context.get(SIDE_ANSWER_CONTEXT_KEY):
        snapshot = context.get(SIDE_ANSWER_SNAPSHOT_KEY)
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        lines = [
            "A task is still running in this chat. The user's message is a side question asked while "
            "it works. Answer it directly and briefly. Do not start, repeat or redo the running task, "
            "and do not claim it has finished: it continues on its own and reports its own result.",
        ]
        request = str(snapshot.get("request_text") or "").strip()
        if request:
            lines.append(f"The running task was asked: {request[:1200]}")
        progress = [str(item) for item in list(snapshot.get("progress") or []) if str(item).strip()]
        if progress:
            lines.append("Its progress so far (oldest first): " + " | ".join(progress[-_MAX_PROGRESS:]))
        else:
            lines.append("It has not reported a step yet.")
        notes = [
            dict(item) for item in list(snapshot.get("notes") or []) if isinstance(item, dict)
        ]
        if notes:
            lines.append(
                "Notes the user attached to it: "
                + " | ".join(f"{n.get('text', '')} ({n.get('status', '')})" for n in notes)
            )
        return "\n".join(lines)
    notes = [dict(item) for item in list(context.get(FOLLOWUP_NOTES_CONTEXT_KEY) or []) if isinstance(item, dict)]
    if not notes:
        return ""
    rendered = "\n".join(f"{index}. {str(note.get('text') or '').strip()}" for index, note in enumerate(notes, 1))
    return (
        "While you were working on this request, the user added these notes (oldest first). They are "
        "part of the request now: apply them to the remaining work and reflect them in your answer. "
        "If a note contradicts work you already finished, do not undo that work on your own; finish "
        "what the note does not affect and ask the user to confirm before undoing anything.\n"
        f"{rendered}"
    )


def reset_followup_state() -> None:
    """Tests only -- a process with live tasks must never call this."""
    global _START_SEQ
    with _LOCK:
        _TASKS.clear()
        _HANDLED.clear()
        _START_SEQ = 0
