"""The follow-up action gate: a referential command needs a referent before anything runs.

"Prove it" is not a task. It is a pointer, and it is only a task once the thing it points at
exists. The incident this closes had the pointer resolve to nothing and the runtime proceed anyway:
an audit ended `no_finding`, the operator said "Prove the bug you identified before fixing it", and
the runtime nominated a fresh candidate, wrote a regression test for it, ran it and scored the
result. Every step of that was competent. The first one was about nothing.

Two failures were possible there, and both are worse than doing nothing:

* **manufacturing a referent** — calling a model to produce something for the pronoun to mean, which
  is how "prove the bug you identified" became a test for a defect nobody had identified;
* **silent substitution** — proving a different claim under the pronoun the operator used for the
  first one.

So the check runs BEFORE any model call, any tool call and any planning step, and its input is
structured state rather than conversation. If the action needs a finding and
:func:`active_finding_id_for` returns ``None``, the turn stops and says so. A blocked turn costs one
sentence; the incident cost a generated file in a read-only workspace, a command run against it, and
a verdict about a claim that was never made.

The gate deliberately refuses to guess in the other direction too. It blocks only where this scope
has a RECORDED conclusion — an audit that ended with nothing, or a finding that was rejected. With
no record at all it stands aside entirely: "fix it" in an ordinary conversation is about whatever
the operator was just discussing, and claiming that turn would be the same overreach in the opposite
direction.

Nothing here reads a filename, a language, a provider or a bug class.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from core.agent_runtime.active_finding import (
    CONTINUE,
    EXPLAIN,
    FIX,
    NO_FINDING,
    NONE,
    REJECTED,
    REPRODUCE,
    ActiveFinding,
    FindingScope,
    active_finding_for,
    classify_follow_up,
)

# --- why a follow-up was blocked ---------------------------------------------------------------
BLOCKED_NO_FINDING = "no_finding"
BLOCKED_REJECTED_FINDING = "rejected_finding"
# Not a block: there is simply nothing recorded here, so this is not the gate's turn to judge.
NO_RECORDED_CONCLUSION = "no_recorded_conclusion"

# Which actions cannot proceed without a referent. `CONTINUE` is deliberately absent: "keep going"
# asks for more search, and more search is exactly what a turn with no finding should offer.
_ACTIONS_REQUIRING_A_FINDING = frozenset({REPRODUCE, FIX, EXPLAIN})

# How each action is named in the sentence the operator reads. The reproduce wording is the one the
# incident report specifies; the others follow its shape so a fix request is not answered with a
# sentence about reproduction.
_ACTION_NOUN = {
    REPRODUCE: "reproduce",
    FIX: "fix",
    EXPLAIN: "explain",
}

# A sentence that names a concrete file or path is not pointing BACK at anything — it brought its
# own subject. "Audit svc/queue.py and prove the highest-risk bug" contains a reproduce verb and a
# finding noun, and it is a new request; blocking it because an earlier audit in the same chat ended
# with nothing would be the gate refusing work it has no opinion on. Path shape rather than a lane
# lookup, so this reads the same for a Rust crate, a SQL migration or a notebook.
_NAMES_ITS_OWN_SUBJECT_RE = re.compile(
    r"(?:^|[\s`'\"(])[\w.\-]*/[\w./\-]+|\b[\w\-]+\.[A-Za-z][A-Za-z0-9]{0,7}\b(?!\s*$)",
)


@dataclass(frozen=True)
class FollowUpGateDecision:
    """Whether a referential follow-up may proceed, and what to say when it may not."""

    action: str = NONE
    requires_finding: bool = False
    finding_id: str | None = None
    blocked: bool = False
    reason_code: str = ""
    message: str = ""
    # The finding this turn is bound to, when there is one. Every downstream step carries its id.
    finding: ActiveFinding | None = None

    @property
    def bound(self) -> bool:
        """True when this turn has a referent it must stay on."""
        return bool(self.finding_id) and self.finding is not None

    def as_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "requires_finding": self.requires_finding,
            "finding_id": self.finding_id or "",
            "blocked": self.blocked,
            "reason_code": self.reason_code,
        }


def _no_finding_message(action: str) -> str:
    verb = _ACTION_NOUN.get(action, "act on")
    return (
        f"I didn't identify a verified bug in the previous audit, so there is currently nothing to "
        f"{verb}. I can continue investigating for a different defect."
    )


def _rejected_message(action: str, finding: ActiveFinding) -> str:
    verb = _ACTION_NOUN.get(action, "act on")
    named = f" (`{finding.title}`)" if finding.title else ""
    tail = finding.closing_reason or (
        "it did not survive checking, so it was withdrawn rather than reported as a defect"
    )
    return (
        f"The finding I named earlier{named} is no longer standing: {tail}. There is nothing left "
        f"to {verb}. I can continue investigating for a different defect."
    )


def gate_follow_up(text: str, scope: FindingScope) -> FollowUpGateDecision:
    """Resolve a referential command against structured state, before anything executes.

    Returns a decision in every case, including "this is not a follow-up" — a caller that has to
    distinguish "allowed" from "not my turn" reads ``action`` and ``blocked`` rather than a bare
    boolean it can misread in one direction.
    """
    intent = classify_follow_up(text)
    action = intent.action
    if not intent.binds_to_active_finding:
        # No back-reference, or the operator explicitly changed subject. Either way this turn is
        # about something the gate has no opinion on.
        return FollowUpGateDecision(action=action)
    if _NAMES_ITS_OWN_SUBJECT_RE.search(str(text or "")):
        return FollowUpGateDecision(action=action)

    requires_finding = action in _ACTIONS_REQUIRING_A_FINDING
    row = active_finding_for(scope)
    finding_id = row.finding_id if (row is not None and row.is_open) else ""

    if finding_id:
        return FollowUpGateDecision(
            action=action,
            requires_finding=requires_finding,
            finding_id=finding_id,
            finding=row,
        )
    if not requires_finding:
        return FollowUpGateDecision(action=action, requires_finding=False)
    if row is None:
        # Nothing has concluded in this scope. Not a block — see the module docstring: an ordinary
        # "fix it" belongs to whatever the operator was just doing.
        return FollowUpGateDecision(
            action=action, requires_finding=True, reason_code=NO_RECORDED_CONCLUSION
        )

    if row.finding_status == NO_FINDING:
        return FollowUpGateDecision(
            action=action,
            requires_finding=True,
            blocked=True,
            reason_code=BLOCKED_NO_FINDING,
            message=_no_finding_message(action),
        )
    if row.finding_status == REJECTED:
        # A claim that failed reproduction stays failed. Resurrecting it on a second "prove it"
        # would present a disproved claim as a live one, which is the substitution this gate exists
        # to prevent, only slower.
        return FollowUpGateDecision(
            action=action,
            requires_finding=True,
            blocked=True,
            reason_code=BLOCKED_REJECTED_FINDING,
            message=_rejected_message(action, row),
            finding=row,
        )
    return FollowUpGateDecision(
        action=action, requires_finding=True, reason_code=NO_RECORDED_CONCLUSION
    )


def gate_follow_up_conclusion(
    decision: FollowUpGateDecision, *, terminal_state: str = ""
) -> FollowUpGateDecision:
    """Block a follow-up on the strength of a lane's own recorded conclusion.

    The finding row is the authority, and a lane that keeps its own session state can reach the same
    truth first — an audit capsule that ended with no candidate knows there is nothing to prove even
    if its finding row has aged out. This turns that lane fact into the same blocked decision rather
    than letting each lane invent its own refusal sentence.
    """
    state = str(terminal_state or "").strip().lower()
    reason = BLOCKED_REJECTED_FINDING if state in {"refuted", "rejected"} else BLOCKED_NO_FINDING
    if reason == BLOCKED_REJECTED_FINDING:
        verb = _ACTION_NOUN.get(decision.action, "act on")
        message = (
            "The finding I named earlier did not survive its own reproduction, so it was withdrawn "
            f"rather than reported as a defect. There is nothing left to {verb}. I can continue "
            "investigating for a different defect."
        )
    else:
        message = _no_finding_message(decision.action)
    return FollowUpGateDecision(
        action=decision.action,
        requires_finding=True,
        blocked=True,
        reason_code=reason,
        message=message,
    )


def follow_up_blocks_execution(text: str, scope: FindingScope) -> str:
    """The sentence a blocked follow-up must answer with, or ``""`` when it may proceed.

    A convenience for call sites that only need the yes/no and the words — the front door, which
    must not import the whole decision vocabulary to refuse one turn.
    """
    decision = gate_follow_up(text, scope)
    return decision.message if decision.blocked else ""


__all__ = [
    "BLOCKED_NO_FINDING",
    "BLOCKED_REJECTED_FINDING",
    "CONTINUE",
    "NO_RECORDED_CONCLUSION",
    "FollowUpGateDecision",
    "follow_up_blocks_execution",
    "gate_follow_up",
    "gate_follow_up_conclusion",
]
