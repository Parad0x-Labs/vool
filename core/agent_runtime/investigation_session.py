"""What an ordinary investigation leaves behind, so its follow-up has something to continue.

The audit lane has had a session capsule since the "prove the bug you identified" incident. Ordinary
read-only investigation — the far more common request — had nothing, and on 2026-08-07 that gap
produced its own incident:

    "Inspect this project and identify exactly three files central to cloud provider routing.
     Remember these three because I am going to change models."
    "Continue from the three files we already identified."

The second turn had no record of the first. The runtime re-searched the workspace by keyword and
surfaced `fallback`, `CloudTrail`, `common_zstd` and `github` — real files, none of them the three,
because the three had never been written down anywhere. A later turn in the same chat listed a
different repository altogether.

So an investigation now leaves the same minimum an audit does, and no more:

* the scope it belongs to (project + chat), which is the isolation boundary,
* the turn it started on, so a continuation can say which request it is continuing,
* the workspace it was about,
* the SUBJECT — the operator's own question, verbatim and bounded,
* the paths the turn actually resolved, which is what "the three files we already identified" means.

Deliberately NOT stored: model prose, findings, verdicts, or anything resembling a conclusion. This
is a pointer to work, not a record of truth — the durable record remains the event ledger and the
tool receipts. Generic investigation state must never masquerade as an audit; a capsule here carries
no candidates and no confidence, because an investigation produced none.

Losing a capsule (restart, eviction, TTL) degrades a follow-up to "which three files did you mean?",
which is a fine answer. It must never degrade to answering about a different repository.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

# Same bounds as the audit capsule store, for the same reason: a long-lived daemon must not
# accumulate a row for every chat it has ever seen.
_MAX_CAPSULES = 64
_CAPSULE_TTL_SECONDS = 60 * 60 * 6

_LOCK = threading.Lock()
_CAPSULES: dict[str, InvestigationCapsule] = {}

# How many resolved paths are worth carrying. A continuation refers to a handful of files by
# description ("the three files"); a hundred would be a directory listing, not a subject.
_MAX_RESOLVED_PATHS = 24

INVESTIGATE = "investigate"


@dataclass
class InvestigationCapsule:
    """The subject of one ordinary (non-audit) workspace investigation."""

    session_id: str
    project_id: str = ""
    chat_id: str = ""
    # The turn that opened this investigation, so a continuation can name what it continues rather
    # than implying the whole chat was one subject.
    origin_turn_id: str = ""
    # Always `investigate` today. Present so a reader never has to infer from the absence of audit
    # fields that this is not an audit — the distinction is asserted, not implied.
    mode: str = INVESTIGATE
    workspace_root: str = ""
    # The operator's own words. Bounded, never re-summarized by a model: a paraphrase of the subject
    # is how a continuation quietly changes it.
    subject: str = ""
    # Paths the operator named outright, distinct from paths the runtime found on their behalf.
    explicit_targets: tuple[str, ...] = ()
    # Paths a tool actually opened or matched this turn. This is the set "the three files we
    # already identified" refers to.
    resolved_paths: tuple[str, ...] = ()
    updated_at: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "origin_turn_id": self.origin_turn_id,
            "workspace_root": self.workspace_root,
            "subject": self.subject,
            "explicit_targets": list(self.explicit_targets),
            "resolved_paths": list(self.resolved_paths),
        }


def capsule_key(session_id: str, *, project_id: str = "", chat_id: str = "") -> str:
    """Where a capsule lives. Project and chat are part of the address, not a filter.

    Same construction as the audit capsule store, and for the same reason: isolation implemented as
    a key cannot be forgotten by a future call site the way a comparison can.
    """
    return f"{str(project_id or '').strip()}\x1f{str(chat_id or '').strip() or str(session_id or '').strip()}"


def _prune_locked() -> None:
    now = time.time()
    for key in [k for k, row in _CAPSULES.items() if now - row.updated_at > _CAPSULE_TTL_SECONDS]:
        _CAPSULES.pop(key, None)
    while len(_CAPSULES) > _MAX_CAPSULES:
        oldest = min(_CAPSULES, key=lambda k: _CAPSULES[k].updated_at)
        _CAPSULES.pop(oldest, None)


def save_investigation_capsule(capsule: InvestigationCapsule) -> InvestigationCapsule:
    capsule.updated_at = time.time()
    with _LOCK:
        _CAPSULES[
            capsule_key(capsule.session_id, project_id=capsule.project_id, chat_id=capsule.chat_id)
        ] = capsule
        _prune_locked()
    return capsule


def load_investigation_capsule(
    session_id: str, *, project_id: str = "", chat_id: str = ""
) -> InvestigationCapsule | None:
    key = capsule_key(session_id, project_id=project_id, chat_id=chat_id)
    with _LOCK:
        capsule = _CAPSULES.get(key)
        if capsule is None:
            return None
        if time.time() - capsule.updated_at > _CAPSULE_TTL_SECONDS:
            _CAPSULES.pop(key, None)
            return None
        return capsule


def clear_investigation_capsules() -> None:
    """Test seam and session reset. Never called on an ordinary turn."""
    with _LOCK:
        _CAPSULES.clear()


def record_investigation(
    *,
    session_id: str,
    project_id: str = "",
    chat_id: str = "",
    origin_turn_id: str = "",
    workspace_root: str = "",
    subject: str = "",
    explicit_targets: tuple[str, ...] = (),
    resolved_paths: tuple[str, ...] = (),
) -> InvestigationCapsule | None:
    """Remember this turn as the investigation a follow-up may continue.

    A turn that resolved no paths and named no target investigated nothing, so it records nothing —
    otherwise every greeting in a project-bound chat would become "the investigation" and a later
    "continue" would bind to it.
    """
    if not (resolved_paths or explicit_targets):
        return None
    existing = load_investigation_capsule(session_id, project_id=project_id, chat_id=chat_id)
    # A continuation ADDS to the subject it continues rather than replacing it: the paths the
    # follow-up opened belong to the same investigation, and dropping the originals would lose the
    # very set the operator is pointing at.
    prior_paths = tuple(existing.resolved_paths) if existing is not None else ()
    merged = list(dict.fromkeys([*prior_paths, *resolved_paths]))[:_MAX_RESOLVED_PATHS]
    return save_investigation_capsule(
        InvestigationCapsule(
            session_id=str(session_id or ""),
            project_id=str(project_id or ""),
            chat_id=str(chat_id or ""),
            origin_turn_id=(
                existing.origin_turn_id if existing is not None and existing.origin_turn_id
                else str(origin_turn_id or "")
            ),
            workspace_root=str(workspace_root or ""),
            subject=(existing.subject if existing is not None and existing.subject else str(subject or ""))[:400],
            explicit_targets=tuple(explicit_targets)[:_MAX_RESOLVED_PATHS],
            resolved_paths=tuple(merged),
        )
    )


def resolved_paths_from_observations(observations: Any) -> tuple[str, ...]:
    """Every workspace path this turn's tools actually touched, in the order they were touched.

    Reads the typed observation rows the tool layer already emits (`path` for a single-target read,
    `paths` for a search or listing) rather than parsing the model's prose — the point of the
    capsule is to carry what the RUNTIME did, and prose is exactly the thing that drifts.
    """
    found: list[str] = []
    for row in list(observations or []):
        if not isinstance(row, dict):
            continue
        candidates = [row.get("path"), *(row.get("paths") or [])]
        for candidate in candidates:
            text = str(candidate or "").strip().replace("\\", "/")
            # A bare directory or an empty cell is not a resolved subject.
            if not text or text.endswith("/") or text in found:
                continue
            found.append(text)
    return tuple(found[:_MAX_RESOLVED_PATHS])


def investigation_follow_up_resumes(
    text: str, *, session_id: str, project_id: str = "", chat_id: str = ""
) -> InvestigationCapsule | None:
    """The investigation this follow-up continues, or None when it is not a continuation.

    Both halves are required, exactly as in the audit lane: a continuation phrasing with no capsule
    is an ordinary request that happens to start with the word "continue", and claiming it on the
    strength of a verb would be the mirror image of the bug being fixed.

    Three ways an object may point backwards, and the third needs the capsule: "Keep going on the
    provider health path" carries no pronoun and no investigation noun, and is a continuation
    exactly when the recorded investigation was about provider health. Shape is still consulted
    first — state alone would let any continue-verb re-arm whatever was in scope (ARGUS R1).
    """
    from core.agent_runtime.active_finding import (
        classify_follow_up,
        continuation_refers_to_recorded_work,
    )

    intent = classify_follow_up(text)
    if intent.explicit_retarget:
        return None
    capsule = load_investigation_capsule(session_id, project_id=project_id, chat_id=chat_id)
    if capsule is None or not capsule.resolved_paths:
        return None
    if intent.binds_to_active_finding:
        return capsule
    if continuation_refers_to_recorded_work(
        text, subject=capsule.subject, paths=capsule.resolved_paths
    ):
        return capsule
    return None


def continuation_observation(capsule: InvestigationCapsule) -> dict[str, Any]:
    """The prior subject, shaped as a tool observation so the answering model actually sees it.

    Deliberately delivered through the observation channel the workspace tools already use, rather
    than through a new prompt field: it is the one path that is guaranteed to reach the model on
    every surface, and it keeps the carried subject visibly labelled as runtime state instead of
    letting it blend into the conversation as though the model remembered it.
    """
    return {
        "schema": "tool_observation_v1",
        "intent": "investigation.continuation",
        "tool_surface": "workspace",
        "ok": True,
        "status": "resumed",
        "read_only": True,
        "final_answer": False,
        "subject": capsule.subject,
        "workspace_root": capsule.workspace_root,
        "paths": list(capsule.resolved_paths),
        "instruction": (
            "This turn continues an earlier investigation in this chat. The files listed above are "
            "the ones that investigation actually resolved — they are the subject the operator is "
            "referring to. Continue with these rather than starting a new search, and if they do "
            "not answer the question, say so and name what you looked at."
        ),
    }


__all__ = [
    "INVESTIGATE",
    "InvestigationCapsule",
    "clear_investigation_capsules",
    "continuation_observation",
    "investigation_follow_up_resumes",
    "load_investigation_capsule",
    "record_investigation",
    "resolved_paths_from_observations",
    "save_investigation_capsule",
]
