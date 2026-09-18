"""Verified-procedure promotion: one ok=True is a candidate, never a lesson.

The production caller (``core.runtime_execution_tools._attach_procedure_learning``, an
integration-owned seam) invokes ``promote_verified_procedure`` after a validation-intent tool
result succeeded against a tracked workspace mutation. At base it persisted a fully reusable
procedure from that single event -- the caller itself writes ``validation={"ok": True, ...}``, so
a tool's ok=True manufactured "verified learning" exactly the way PB01 forbids.

Lifecycle (amendment round, policy v2):

* the FIRST verified execution of a lesson signature stages a **candidate** shard (not rankable);
* lesson identity binds the *executable guidance*: task class + receipt intents + validation
  tool + the validation COMMAND DIGEST + normalized steps + declared preconditions + the policy
  version. Two repairs that merely share a task class and toolset are DIFFERENT lessons and can
  no longer accumulate toward one promotion -- mutation ids make two executions independent
  witnesses of the SAME recipe, nothing more;
* promotion requires ``PROMOTION_MIN_INDEPENDENT_VERIFIED_EVENTS`` evidence events with
  independent identities (distinct tracked mutation ids; distinct session+command-digest
  witnesses otherwise), and records its ``promotion_basis`` -- which rule promoted the lesson,
  on how many events, under which policy version. Replays of one execution never count twice;
* v1 ``legacy_unvalidated`` records receive new evidence as CANDIDATES (their unverified
  history is never trusted); only the new evidence counts toward promotion;
* promotion stamps a TTL (``expires_at``); each new verified evidence refreshes it;
* an operator-corrected (demoted) shard never auto-resurrects from new evidence.

Concurrency: the shard id is deterministic in the lesson signature, and the find-or-create plus
append run as ONE signature-scoped transaction (per-shard fcntl lock), so racing processes
converge on a single shard instead of opening duplicates.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from .policy import LearningPolicy
from .procedure_shards import (
    ProcedureShardV1,
    find_or_create_procedure_shard,
    promotion_expiry_stamp,
)
from .reuse_ranker import normalize_guidance_tokens


def lesson_signature(
    *,
    task_class: str,
    preconditions: list[str] | tuple[str, ...],
    steps: list[str] | tuple[str, ...],
    tool_receipts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    validation: dict[str, Any],
) -> str:
    """Stable identity of the lesson: the EXECUTABLE GUIDANCE, not the incident.

    Bound here deliberately: task class, receipt intents, the validation tool, the validation
    command digest (same recipe = same command; an unrelated repair runs a different command),
    the normalized step guidance, the declared preconditions and the learning policy version.
    Titles and file paths are excluded -- they name the incident, not the lesson. Review finding
    (2026-09-03): without the command/guidance terms, two unrelated repairs sharing a task class
    and toolset promoted one another, and two mutation ids were mistaken for holdout validation.
    """
    intents = sorted({str(item.get("intent") or "").strip() for item in tool_receipts if isinstance(item, dict)})
    command = str(validation.get("command") or "").strip()
    command_digest = hashlib.sha256(command.encode("utf-8")).hexdigest() if command else ""
    material = json.dumps(
        {
            "policy_version": LearningPolicy.POLICY_VERSION,
            "task_class": str(task_class or "").strip().lower(),
            "intents": intents,
            "validation_tool": str(validation.get("tool") or "").strip().lower(),
            "validation_command_digest": command_digest,
            "steps": sorted(normalize_guidance_tokens(" ".join(str(item) for item in steps))),
            "preconditions": sorted(normalize_guidance_tokens(" ".join(str(item) for item in preconditions))),
        },
        sort_keys=True,
    )
    return f"lesson-{hashlib.sha256(material.encode('utf-8')).hexdigest()}"


def deterministic_procedure_id(signature: str) -> str:
    return f"procedure-{hashlib.sha256(str(signature or '').encode('utf-8')).hexdigest()[:32]}"


def _evidence_identity(event: dict[str, Any]) -> str:
    """What makes two evidence events independent. A tracked mutation id is the strong form; the
    session + stored command-digest fallback keeps unit/library callers honest without inventing
    independence. (The fallback used to read ``validation_command``, a key nothing stored -- every
    no-mutation witness collapsed onto one identity; it now reads the stored digest.)"""
    mutation_id = str(event.get("mutation_id") or "").strip()
    if mutation_id:
        return f"mutation:{mutation_id}"
    return "digest:" + hashlib.sha256(
        json.dumps(
            {
                "session": str(event.get("session_id") or ""),
                "validation_command_digest": str(event.get("validation_command_digest") or ""),
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def _evidence_event(
    *,
    tool_receipts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    validation: dict[str, Any],
    session_id: str,
    liquefy_bundle_ref: str,
) -> dict[str, Any]:
    mutation_id = ""
    intents: list[str] = []
    for item in tool_receipts:
        if not isinstance(item, dict):
            continue
        intent = str(item.get("intent") or "").strip()
        if intent and intent not in intents:
            intents.append(intent)
        candidate = str(item.get("mutation_id") or "").strip()
        if candidate and not mutation_id:
            mutation_id = candidate
    validation_command = str(validation.get("command") or "").strip()
    validation_digest = ""
    if validation_command:
        validation_digest = "sha256:" + hashlib.sha256(validation_command.encode("utf-8")).hexdigest()
    return {
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "session_id": str(session_id or "").strip(),
        "mutation_id": mutation_id,
        "intents": intents,
        "validation_tool": str(validation.get("tool") or "").strip(),
        "validation_command_digest": validation_digest,
        "validation_returncode": int(validation.get("returncode") or 0),
        "source_digests": {
            "liquefy_bundle_ref": str(liquefy_bundle_ref or "").strip(),
        },
        # Unknown cost stays unknown: no token/cost data reaches this seam today, and a
        # fabricated 0.0 would read as "measured free" (review finding, 2026-09-03).
        "cost": {"estimated_cost_usd": None, "basis": "unknown"},
    }


def _has_real_evidence(
    *,
    tool_receipts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    validation: dict[str, Any],
) -> bool:
    """The honesty gate: refuse to stage anything from ok=True alone.

    A candidate must carry at least one intent-bearing tool receipt AND a verification signal the
    runtime actually observed -- a validation tool or a run command with its return code. Bare
    ``{"ok": True}`` prose from a model or a caller is not evidence.
    """
    if not bool(validation.get("ok")):
        return False
    has_intent_receipt = any(
        isinstance(item, dict) and str(item.get("intent") or "").strip() for item in tool_receipts
    )
    if not has_intent_receipt:
        return False
    has_verification_signal = bool(
        str(validation.get("tool") or "").strip() or str(validation.get("command") or "").strip()
    )
    if not has_verification_signal:
        return False
    returncode = validation.get("returncode")
    if returncode is not None:
        try:
            if int(returncode) != 0:
                return False
        except (TypeError, ValueError):
            return False
    return True


def promote_verified_procedure(
    *,
    task_class: str,
    title: str,
    preconditions: list[str] | tuple[str, ...],
    steps: list[str] | tuple[str, ...],
    tool_receipts: list[dict[str, Any]] | tuple[dict[str, Any], ...],
    validation: dict[str, Any],
    rollback: dict[str, Any],
    privacy_class: str = "local_private",
    shareability: str = "local_only",
    success_signal: str = "verified_success",
    liquefy_bundle_ref: str = "",
    session_id: str = "",
) -> ProcedureShardV1 | None:
    """Stage/extend the candidate for this lesson signature, promoting it when the bounded rule
    is satisfied. Returns the persisted shard (candidate or promoted), or None when the evidence
    is not real enough to stage at all."""
    if not _has_real_evidence(tool_receipts=tool_receipts, validation=validation):
        return None

    signature = lesson_signature(
        task_class=task_class,
        preconditions=preconditions,
        steps=steps,
        tool_receipts=tool_receipts,
        validation=validation,
    )
    event = _evidence_event(
        tool_receipts=tool_receipts,
        validation=validation,
        session_id=session_id,
        liquefy_bundle_ref=liquefy_bundle_ref,
    )
    procedure_id = deterministic_procedure_id(signature)
    validation_payload = {**dict(validation or {}), "lesson_signature": signature}

    def _build() -> ProcedureShardV1:
        return ProcedureShardV1.create(
            task_class=task_class,
            title=title,
            preconditions=preconditions,
            steps=steps,
            tool_receipts=tool_receipts,
            validation=validation_payload,
            rollback=rollback,
            privacy_class=privacy_class,
            shareability=shareability,
            success_signal=success_signal,
            liquefy_bundle_ref=liquefy_bundle_ref,
            status=LearningPolicy.STATUS_CANDIDATE,
            owner_scope=LearningPolicy.OWNER_SCOPE_LOCAL,
            origin_session_id=str(session_id or "").strip(),
            evidence=[event],
            procedure_id=procedure_id,
        )

    shard, _created = find_or_create_procedure_shard(
        procedure_id,
        _build,
        lambda existing: _append_evidence(existing, event, signature=signature),
    )
    return shard


def _append_evidence(
    shard: ProcedureShardV1,
    event: dict[str, Any],
    *,
    signature: str,
) -> ProcedureShardV1:
    from dataclasses import replace

    event_id = _evidence_identity(event)
    known = {_evidence_identity(item) for item in shard.evidence}
    if event_id in known:
        # A replay of the SAME execution (same mutation / same session+command digest) is not a
        # second witness; record nothing new so a retry loop cannot self-promote a candidate.
        return shard

    migrated_from = shard.migrated_from
    prior_evidence = tuple(shard.evidence)
    prior_status = shard.status
    if prior_status == LearningPolicy.STATUS_LEGACY_UNVALIDATED:
        # A v1 record's unverified history is preserved but never trusted: the new evidence
        # starts the lesson over as a candidate, counted from this event only.
        prior_evidence = ()

    evidence = tuple([*prior_evidence, event])[-LearningPolicy.MAX_EVIDENCE_EVENTS_PER_SHARD:]
    validation = dict(shard.validation)
    validation["lesson_signature"] = signature

    status = prior_status
    promoted_at = shard.promoted_at
    expires_at = shard.expires_at
    if prior_status == LearningPolicy.STATUS_LEGACY_UNVALIDATED:
        status = LearningPolicy.STATUS_CANDIDATE
        promoted_at = ""
        expires_at = ""
        if not migrated_from:
            migrated_from = "vool.procedure_shard.v1"
    elif prior_status == LearningPolicy.STATUS_CANDIDATE:
        independent = {_evidence_identity(item) for item in evidence}
        if len(independent) >= LearningPolicy.PROMOTION_MIN_INDEPENDENT_VERIFIED_EVENTS:
            status = LearningPolicy.STATUS_PROMOTED
            promoted_at = event.get("recorded_at") or promoted_at
            expires_at = promotion_expiry_stamp()
            validation["promotion_basis"] = {
                "rule": "independent_validated_evidence",
                "evidence_events": len(independent),
                "policy_version": LearningPolicy.POLICY_VERSION,
            }
    elif prior_status == LearningPolicy.STATUS_PROMOTED:
        # Live lessons stay live while they keep being re-verified; the TTL refresh is the
        # "still true" heartbeat.
        expires_at = promotion_expiry_stamp()

    return replace(
        shard,
        evidence=evidence,
        validation=validation,
        status=status,
        promoted_at=promoted_at,
        expires_at=expires_at,
        migrated_from=migrated_from,
    )
