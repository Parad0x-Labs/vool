"""Durable evidence that a chat session is in the middle of email work.

A follow-up such as "open that delivery thread", "add that the kiln is at the north studio" or
"approve it and send it" names no mailbox and no tool, so the wording gates that decide whether a
turn reaches tools cannot see it. Measured 2026-09-14 on a served daemon under shipped routing
(revision-5 evidence served-discovery-shipped-routing-20260914-133440.json): turn 1 executed
email.read against the provider; turn 2 "Open that delivery thread and show me what they wrote."
classified `unknown`, was kept in the tools-less chat lane and was answered with no tool.

The signal is read from typed durable state, never from the words of the turn:

* the execution ledger (core.execution_truth): an email tool this session executed recently;
* the draft store (core.email_drafts): a draft this session owns that is not finished.

Consumers follow the open code-task precedent (core.code_assistant.task_runtime): the chat-lane
policy releases the turn, the planner admits the tool lane, and the tool offer seats the email tools
the session's CURRENT stage needs as explicit seats. The model still decides whether to call any of
them. User-stated constraints -- a tool prohibition, a stipulated hypothetical, a surface that cannot
run tools -- are decided before this signal is consulted and keep their precedence.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# How long a session's recently executed email tools keep its follow-ups in the email lane: the same
# window, for the same question ("is this message a continuation of that conversation"), as the
# follow-up family memory in core.tool_offer_state (30 minutes) -- read from the durable ledger
# instead of process memory, so a restarted daemon decides the same way. A finished draft (sent,
# confirmed or failed) counts for this window too.
RECENT_EMAIL_WORK_SECONDS = 30 * 60

# How long an unfinished draft (being drafted, approved, or with a send still unresolved) keeps its
# session's email work open: an open work item, the same window as an open code task in its journal
# (core.code_assistant.task_runtime, 6 hours).
OPEN_EMAIL_WORK_SECONDS = 6 * 60 * 60

# Seats one stage may take from the bounded offer: 3 reserved seats + at most 4 here leaves a seat of
# the default 8 for the turn's own ranked family, as an open code task does.
MAX_EMAIL_SEATS = 4

_STAGE_SEATS: dict[str, tuple[str, ...]] = {
    "reading": ("email.open", "email.read", "email.draft.save", "email.draft.get"),
    "drafting": ("email.draft.get", "email.draft.save", "email.draft.approve", "email.draft.send"),
    "approved": ("email.draft.send", "email.draft.get", "email.draft.save", "email.draft.approve"),
    "dispatched": ("email.draft.reconcile", "email.draft.get", "email.open", "email.read"),
}


@dataclass(frozen=True)
class EmailWork:
    """What the session's durable state says about its email work right now."""

    stage: str = ""
    seats: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()

    @property
    def active(self) -> bool:
        return bool(self.seats)


def _now() -> float:
    return time.time()


def _fact_age_seconds(created_at: str, now: float) -> float | None:
    try:
        stamp = datetime.fromisoformat(str(created_at))
    except ValueError:
        return None
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return now - stamp.timestamp()


def _stage_for(status: str, unresolved: frozenset[str]) -> str:
    if status in unresolved or status == "sent":
        return "dispatched"
    if status == "approved":
        return "approved"
    if status == "sent_confirmed":
        return "reading"
    return "drafting"  # a draft being written, a stale approval, or a failed send to retry


def _runnable(intents: list[str]) -> list[str]:
    """The intents this runtime can run now, by each tool contract's own ``supported`` flag
    (core.runtime_tool_contracts owns availability): a policy that disables email leaves nothing to
    seat, and so nothing to admit or release."""
    try:
        from core.runtime_tool_contracts import runtime_tool_contract_map

        contracts = runtime_tool_contract_map()
    except Exception:
        return []
    return [intent for intent in intents if getattr(contracts.get(intent), "supported", False)]


def email_work_for(source_context: dict[str, Any] | None) -> EmailWork:
    """The session's email work from the draft store and the ledger; inactive when there is none.

    Never raises: routing must not be able to fail a turn, and an unreadable source reads as no
    evidence from that source.
    """
    context = source_context if isinstance(source_context, dict) else {}
    fact_session = str(context.get("runtime_session_id") or context.get("session_id") or "").strip()
    if not (fact_session or str(context.get("chat_id") or "").strip()):
        return EmailWork()
    now = _now()
    evidence: list[str] = []
    live: list[dict[str, Any]] = []
    unresolved: frozenset[str] = frozenset()
    try:
        from core import email_drafts
        from core.runtime_execution_tools import _email_session_scope

        principal, draft_session = _email_session_scope(context)
        unresolved = frozenset(email_drafts._UNRESOLVED_STATES)
        finished = frozenset(email_drafts._TERMINAL_STATES)
        for draft in email_drafts.session_drafts_snapshot(draft_session, principal=principal or None):
            window = RECENT_EMAIL_WORK_SECONDS if draft["status"] in finished else OPEN_EMAIL_WORK_SECONDS
            if now - float(draft["updated_at"]) <= window:
                live.append(draft)
                evidence.append(f"draft:{draft['draft_id']}:{draft['status']}")
    except Exception:
        live = []
    if fact_session:
        try:
            from core.execution_truth import KIND_TOOL, recent_session_facts

            newest = next((fact for fact in recent_session_facts(fact_session, name_prefix="email.")
                           if fact.in_category(KIND_TOOL)), None)
            age = _fact_age_seconds(newest.created_at, now) if newest is not None else None
            if newest is not None and age is not None and age <= RECENT_EMAIL_WORK_SECONDS:
                evidence.append(f"tool:{newest.name}:{newest.status}")
        except Exception:
            pass
    if not evidence:
        return EmailWork()
    stage = _stage_for(live[0]["status"], unresolved) if live else "reading"
    seats = list(_STAGE_SEATS[stage])
    if stage != "dispatched" and any(draft["status"] in unresolved for draft in live):
        # An uncertain send stays reconcilable while a newer draft in the session is the active one.
        seats.insert(1, "email.draft.reconcile")
    runnable = _runnable(seats)
    if not runnable:
        return EmailWork()
    return EmailWork(stage=stage, seats=tuple(dict.fromkeys(runnable))[:MAX_EMAIL_SEATS], evidence=tuple(evidence))


def email_work_owns_follow_up_references(source_context: dict[str, Any] | None, *, session_id: str = "") -> bool:
    """Whether this session's open email work owns what a follow-up refers to.

    While the session has open email work, a follow-up's references are as plausibly the mailbox's as
    anything else's: "check the sent folder" may be the mailbox's Sent folder rather than a disk folder
    named "sent", and "confirm it's in the Sent folder" or "is this the latest estimate?" asks about the
    mailbox, not the web. No deterministic claimant may act on such a reference before the model, which
    holds the session's email tools, decides. The one owner of that decision for every such claimant --
    the machine-read fast path's folder claims, and the workflow planner's folder search and inferred web
    research. Explicit requests keep their deterministic handling: a typed path, an explicit web lookup.
    Never raises.
    """
    try:
        context = dict(source_context or {})
        if session_id and not (context.get("runtime_session_id") or context.get("session_id")):
            context["session_id"] = session_id
        return email_work_for(context).active
    except Exception:
        return False


def active_email_work_intents(source_context: dict[str, Any] | None) -> tuple[str, ...]:
    """The email tools the session's current email stage needs, or () when it has no email work."""
    return email_work_for(source_context).seats


__all__ = [
    "MAX_EMAIL_SEATS",
    "OPEN_EMAIL_WORK_SECONDS",
    "RECENT_EMAIL_WORK_SECONDS",
    "EmailWork",
    "active_email_work_intents",
    "email_work_for",
    "email_work_owns_follow_up_references",
]
