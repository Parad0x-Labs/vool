"""One source of truth for the learning loop's bounded rules.

Every threshold the loop enforces lives here so the policy is auditable in one place and tests can
assert the rule, not a magic number buried in a branch. The values are deliberately conservative:
the loop's job is to make *verified* reuse possible, never to promote enthusiasm.

Procedure lifecycle
    candidate --(>= PROMOTION_MIN_INDEPENDENT_VERIFIED_EVENTS independent verified executions)
        --> promoted --(TTL expiry | correction | consecutive unverified reuses)--> demoted/expired

Model sufficiency
    Observations only move the selector once MIN_OBSERVATIONS fresh, task-class-scoped events
    agree, the adjustment is capped on both sides, and it is additive only: it can reorder
    eligible manifests, never rescue one a hard gate excluded.
"""

from __future__ import annotations


class LearningPolicy:
    #: Identity/behavior version of the learning rules themselves. Bound into every lesson
    #: signature and every sufficiency observation: when the policy changes, old lessons and old
    #: feedback do not silently mix with new-rule state.
    POLICY_VERSION = 2
    #: Version of the model-quality feedback record shape (separate: it can evolve alone).
    FEEDBACK_VERSION = 2

    # --- procedure lifecycle -----------------------------------------------------------
    #: How many independently-verified executions (distinct mutation ids, or distinct
    #: session+command-digest witnesses when no mutation was tracked) a candidate needs before it
    #: may be ranked for reuse. One ok=True is never learning.
    PROMOTION_MIN_INDEPENDENT_VERIFIED_EVENTS = 2
    #: A promoted procedure stops being eligible this many days after its last verified evidence.
    PROMOTION_TTL_DAYS = 30
    #: Consecutive unvalidated (ok-but-unvalidated) reuses that demote a promoted procedure.
    DEMOTE_AFTER_CONSECUTIVE_UNVERIFIED_REUSES = 3
    #: Verified reuse failures (the lesson was consumed, its validation ran and FAILED) that
    #: demote a promoted procedure. A single failure is evidence; two is a verdict.
    DEMOTE_AFTER_VERIFIED_REUSE_FAILURES = 2
    #: Hard bounds so one task class cannot grow an unbounded evidence log or store.
    MAX_EVIDENCE_EVENTS_PER_SHARD = 25
    MAX_STORED_PROCEDURES = 200

    #: Shard lifecycle states. ``promoted`` is the only rankable one. ``legacy_unvalidated`` is
    #: the explicit migration state for v1 records: preserved history, never trusted reuse.
    STATUS_CANDIDATE = "candidate"
    STATUS_PROMOTED = "promoted"
    STATUS_DEMOTED = "demoted"
    STATUS_LEGACY_UNVALIDATED = "legacy_unvalidated"

    #: Typed terminal reuse outcomes. Closed vocabulary: transport failures belong to model
    #: health (circuit breaker), not to lesson feedback, and are deliberately absent.
    TERMINAL_SUCCESS = "successful"
    TERMINAL_FAILURE = "failed"
    TERMINAL_CANCELLED = "cancelled"
    TERMINAL_UNVALIDATED = "unvalidated"
    TERMINAL_OUTCOMES = frozenset({TERMINAL_SUCCESS, TERMINAL_FAILURE, TERMINAL_CANCELLED, TERMINAL_UNVALIDATED})

    #: Scope of the store: single-operator local product. No cross-user channel exists; the field
    #: exists so a future sync surface cannot silently widen it.
    OWNER_SCOPE_LOCAL = "local_operator"

    # --- model sufficiency --------------------------------------------------------------
    #: Minimum fresh observations (successes + quality failures) before any adjustment is applied.
    SUFFICIENCY_MIN_OBSERVATIONS = 3
    #: Observations older than this many days stop counting (learning decays; models change).
    SUFFICIENCY_FRESHNESS_DAYS = 7
    #: Bounds of the additive ranking adjustment. Never large enough to outrank a hard gate,
    #: because it is applied only to manifests that already passed every hard exclusion.
    SUFFICIENCY_MAX_BONUS = 0.35
    SUFFICIENCY_MAX_PENALTY = 0.75
    #: Bounded observation history per provider/task-class pair.
    SUFFICIENCY_MAX_OBSERVATIONS = 500

    #: Turn-stage verdicts that indicate the MODEL produced unusable quality. Transport failures
    #: (``provider_error``) are model-health's job (circuit breaker), and retrieval-stage states
    #: are the task's fault, not the model's -- neither is learned quality and neither counts.
    SUFFICIENCY_QUALITY_FAILURE_STATES = frozenset(
        {
            "synthesis_empty",
            "response_extraction_failed",
            "validator_rejected",
        }
    )
    SUFFICIENCY_SUCCESS_STATE = "success"
    #: Recorded outcome vocabulary for sufficiency observations.
    SUFFICIENCY_OUTCOME_VERIFIED = "verified_success"
    SUFFICIENCY_OUTCOME_FAILURE = "quality_failure"
    SUFFICIENCY_OUTCOMES = frozenset({SUFFICIENCY_OUTCOME_VERIFIED, SUFFICIENCY_OUTCOME_FAILURE})
