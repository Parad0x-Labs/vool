"""The audit session capsule: what a follow-up needs so it continues the audit instead of starting one.

"Prove the bug you identified before fixing it." names no file, no model and no finding. The
runtime read it as a fresh request, failed the audit gate (which needs a path in the sentence),
fell into the generic app builder, and burned an 8,192-token reasoning allowance building something
unrelated before reporting the model unreachable.

Nothing was wrong with the sentence. The turn that could answer it had already happened and left
nothing behind. So the audit now leaves a capsule — the minimum state a continuation needs:

* the target file and the identity (hash) of the source that was actually inspected,
* the provider and model the audit was pinned to,
* the execution policy the audit was opened under,
* the candidates nominated, the ones refuted with their counterexamples, and the proof state.

Kept in memory, keyed by session id, bounded. It is a continuation aid, not a record of truth —
the durable record is the runtime event ledger and the tool receipts. Losing a capsule (restart,
eviction) degrades a follow-up to "I need you to name the file again", which is honest; it must
never degrade to answering about a file the operator did not mean.
"""
from __future__ import annotations

import hashlib
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from core.agent_runtime.audit_policy import AuditExecutionPolicy

# Bounded so a long-lived daemon cannot accumulate capsules for every session it ever saw.
_MAX_CAPSULES = 64
_CAPSULE_TTL_SECONDS = 60 * 60 * 6

_LOCK = threading.Lock()
_CAPSULES: dict[str, AuditSessionCapsule] = {}

# A continuation refers BACK to a finding without naming it: "prove it", "prove the bug you
# identified", "reproduce that". These phrasings are meaningless without a prior audit, which is
# why they may only resume a capsule and may never open one.
#
# The object must be a genuine back-reference, and that distinction is the whole gate. A bare
# `that` is not one: "prove that you can write Python" is ordinary English with a conjunction, and
# accepting it would drag unrelated turns into the audit lane — the mirror image of the misroute
# being fixed. So a pronoun counts only where a conjunction cannot stand (end of clause, or before
# a trailing modifier like "before fixing it"), and everything else must name the finding.
_FINDING_NOUN = r"(?:the|that|this|your)\s+(?:bug|finding|issue|defect|problem|report)"
_TRAILING_PRONOUN = r"(?:it|that|this)\s*(?:[.,;!?]|$|\bbefore\b|\bfirst\b|\bnow\b|\bplease\b)"
_CONTINUATION_RE = re.compile(
    rf"\b(?:prove|reproduce|demonstrate|verify|show)\b.{{0,60}}?"
    rf"(?:{_FINDING_NOUN}|\byou\s+(?:identified|found|reported)\b|\b{_TRAILING_PRONOUN})",
    re.IGNORECASE | re.DOTALL,
)
# "prove it" / "prove the bug" with nothing between the verb and the object.
_SHORT_CONTINUATION_RE = re.compile(
    rf"^\s*(?:please\s+)?(?:prove|reproduce|demonstrate)\s+(?:{_FINDING_NOUN}|{_TRAILING_PRONOUN})",
    re.IGNORECASE,
)


@dataclass
class RefutedCandidate:
    """A candidate whose proof test exited 0 — i.e. the claim was disproved by execution."""

    title: str
    claim_key: str
    counterexample: str
    test_command: str = ""
    returncode: int | None = None
    # The semantic token set of the claim. `claim_key` is an exact string and a reword defeats it;
    # the signature is compared by containment, which is what actually stops a disproved claim from
    # coming back with new adjectives. See `continuity_gate.same_claim`.
    signature: frozenset[str] = field(default_factory=frozenset)


@dataclass
class AuditSessionCapsule:
    session_id: str
    # The isolation boundary. A capsule belongs to one project and one chat; a follow-up asked in
    # another project must MISS, not inherit. Empty ids are the single-project local case and
    # isolate on the chat alone.
    project_id: str = ""
    chat_id: str = ""
    target_path: str = ""
    source_hash: str = ""
    workspace_root: str = ""
    provider_id: str = ""
    model_name: str = ""
    pinned: bool = False
    policy: AuditExecutionPolicy = field(default_factory=AuditExecutionPolicy)
    nominated: list[str] = field(default_factory=list)
    refuted: list[RefutedCandidate] = field(default_factory=list)
    terminal_state: str = ""
    original_request: str = ""
    # How far this audit got. A continuation could not previously ask "resume where?" because the
    # answer was never written down: "Challenge your own audit" re-entered nomination from scratch
    # and spent its whole 3-call ceiling re-nominating (measured 2026-08-07, three nominate calls,
    # terminal `blocked`). One of "", "nominated", "challenged", "proven".
    phase: str = ""
    # Candidates this pass surfaced and never got to challenge — the budget ran out first. They are
    # the honest answer to "challenge your audit": there is work left, and it is THIS work. Stored
    # as the raw survey rows (title / file / line_start / line_end / failure_scenario / harm_class)
    # so a continuation can rebuild a finding without asking a model to nominate one again.
    unreviewed: list[dict[str, Any]] = field(default_factory=list)
    # The candidate the operator was shown and has not yet had proved. A continuation proves THIS,
    # rather than asking the model to nominate again — a second nomination is how "prove the bug
    # you identified" quietly becomes a report about a different bug.
    pending_finding: Any = None
    # The exact evidence the audit read, so a follow-up turn that carries none can still resolve
    # the same file and the same bytes rather than re-reading a workspace that may have changed.
    evidence_blob: dict[str, Any] = field(default_factory=dict)
    updated_at: float = field(default_factory=time.time)

    def banned_claim_keys(self) -> set[str]:
        """Claims already disproved this session. Rule 4: never renominate one of these."""
        return {item.claim_key for item in self.refuted if item.claim_key}

    def banned_signatures(self) -> tuple[frozenset[str], ...]:
        """The same bans, as semantic token sets a reworded claim cannot slip past."""
        from core.agent_runtime.continuity_gate import claim_signature

        return tuple(
            item.signature or claim_signature(item.title) for item in self.refuted if item.title
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_path": self.target_path,
            "source_hash": self.source_hash,
            "provider_id": self.provider_id,
            "model_name": self.model_name,
            "pinned": self.pinned,
            "policy": self.policy.as_dict(),
            "nominated": list(self.nominated),
            "refuted": [
                {
                    "title": item.title,
                    "counterexample": item.counterexample,
                    "test_command": item.test_command,
                    "returncode": item.returncode,
                }
                for item in self.refuted
            ],
            "terminal_state": self.terminal_state,
        }


def claim_key(title: str, scenario: str = "") -> str:
    """A stable identity for "the same semantic claim", so a reword cannot dodge the ban list.

    Content words only, order-independent: "Empty input crashes at startswith" and "startswith
    crashes on empty input" produce the same key, which is the point — the incident's refuted
    hypothesis came back reworded.
    """
    body = f"{title} {scenario}".lower()
    words = {word for word in re.findall(r"[a-z_][a-z0-9_]{2,}", body)}
    stop = {
        "the", "and", "for", "with", "that", "this", "from", "into", "when", "then", "than",
        "not", "but", "are", "was", "were", "has", "have", "had", "its", "it's", "bug", "issue",
        "error", "code", "line", "lines", "file", "can", "will", "may", "any", "all",
    }
    meaningful = sorted(words - stop)[:8]
    return "|".join(meaningful) if meaningful else body.strip()[:64]


def source_identity(body: str) -> str:
    """SHA-256 of the exact bytes that were inspected, so a continuation can prove same-source."""
    return hashlib.sha256(str(body or "").encode("utf-8", "replace")).hexdigest()


def _prune_locked() -> None:
    now = time.time()
    stale = [key for key, capsule in _CAPSULES.items() if now - capsule.updated_at > _CAPSULE_TTL_SECONDS]
    for key in stale:
        _CAPSULES.pop(key, None)
    while len(_CAPSULES) > _MAX_CAPSULES:
        oldest = min(_CAPSULES, key=lambda key: _CAPSULES[key].updated_at)
        _CAPSULES.pop(oldest, None)


def capsule_key(session_id: str, *, project_id: str = "", chat_id: str = "") -> str:
    """Where a capsule lives. Project and chat are part of the address, not a filter.

    Isolation implemented as a key rather than a check: a lookup from another project cannot find
    the row at all, so no future call site can forget to compare ids and leak one project's finding
    into another's chat.
    """
    return f"{str(project_id or '').strip()}\x1f{str(chat_id or '').strip() or str(session_id or '').strip()}"


def save_audit_capsule(capsule: AuditSessionCapsule) -> AuditSessionCapsule:
    capsule.updated_at = time.time()
    with _LOCK:
        _CAPSULES[
            capsule_key(capsule.session_id, project_id=capsule.project_id, chat_id=capsule.chat_id)
        ] = capsule
        _prune_locked()
    return capsule


def load_audit_capsule(
    session_id: str, *, project_id: str = "", chat_id: str = ""
) -> AuditSessionCapsule | None:
    key = capsule_key(session_id, project_id=project_id, chat_id=chat_id)
    with _LOCK:
        capsule = _CAPSULES.get(key)
        if capsule is None:
            return None
        if time.time() - capsule.updated_at > _CAPSULE_TTL_SECONDS:
            _CAPSULES.pop(key, None)
            return None
        return capsule


def clear_audit_capsules() -> None:
    """Test seam and session reset. Never called on an ordinary turn."""
    with _LOCK:
        _CAPSULES.clear()


def looks_like_audit_continuation(text: str) -> bool:
    """Whether this sentence only makes sense as a continuation of a prior audit.

    Delegates the shape question to `active_finding.classify_follow_up`, which is the general
    referent classifier — the audit is one lane that records findings, not the only one, and two
    copies of this judgement would drift apart. The local regexes stay as the audit-specific
    widening (its own vocabulary) rather than a second definition of "continuation".
    """
    body = " ".join(str(text or "").split())
    if not body:
        return False
    from core.agent_runtime.active_finding import classify_follow_up

    intent = classify_follow_up(body)
    if intent.explicit_retarget:
        # "Ignore that one, check for X instead" is a continuation in form and a change of subject
        # in fact. Binding must be sticky against drift, never against instruction.
        return False
    if intent.is_continuation:
        return True
    return bool(_SHORT_CONTINUATION_RE.search(body) or _CONTINUATION_RE.search(body))


def audit_specific_continuation(text: str) -> bool:
    """Whether this sentence continues an AUDIT specifically, not just "the open work".

    The distinction exists for the no-capsule case. "Challenge your own audit" with nothing to
    challenge deserves to be told so; a bare "continue" in a chat that never audited anything
    deserves ordinary routing, not a sentence about a previous audit it never had.
    """
    from core.agent_runtime.active_finding import CHALLENGE, classify_follow_up

    body = " ".join(str(text or "").split())
    if not body:
        return False
    intent = classify_follow_up(body)
    if intent.explicit_retarget:
        return False
    if intent.action == CHALLENGE:
        return True
    return intent.is_continuation and bool(
        re.search(r"\b(?:audits?|findings?|candidates?)\b", body, re.IGNORECASE)
    )


def audit_follow_up_resumes(
    text: str, *, session_id: str, project_id: str = "", chat_id: str = ""
) -> AuditSessionCapsule | None:
    """The capsule this follow-up continues, or None when it is not a continuation.

    Both halves are required. A continuation phrasing with no capsule is NOT an audit follow-up —
    it is an ordinary request that happens to contain the word "prove", and routing it into the
    audit lane on the strength of a verb is the mirror image of the bug being fixed.

    The capsule is loaded BEFORE the second half is decided, because one of the three ways an object
    can point backwards is only decidable against it: "keep going on liquefy_apache" names no
    pronoun and no audit noun, and is a continuation exactly when that is what was audited. Shape
    first, state second — never state alone, or every sentence starting with a continue-verb would
    re-arm whatever audit happened to be in scope (ARGUS R1).
    """
    capsule = load_audit_capsule(session_id, project_id=project_id, chat_id=chat_id)
    if capsule is None or not capsule.target_path:
        return None
    if looks_like_audit_continuation(text):
        return capsule
    from core.agent_runtime.active_finding import continuation_refers_to_recorded_work

    if continuation_refers_to_recorded_work(
        text,
        subject=capsule.original_request,
        paths=(capsule.target_path, *tuple(capsule.nominated)),
    ):
        return capsule
    return None
