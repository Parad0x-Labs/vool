"""The Skill + Toolbelt + Plugin-Lifecycle composition seam (Mad Scientist).

This module adds NO new authority, NO new permission engine, NO new evidence
store, and NO new effect truth. It is the smallest set of bindings that makes
the frozen experiments compose without any layer gaining authority through the
connection:

    SKILL (a4d07699)      = workflow NEEDS + exact pinned instruction provenance
    TOOLBELT (92fb7dac)   = current implementation/provider EVIDENCE, never authorization
    LIFECYCLE (9f59f2dc)  = current package/grant/budget/trust facts + admission ticket
    EXECUTION BOUNDARY    = the ONE authoritative dispatch decision
    EffectJournal         = the ONE effect truth

Composition laws enforced HERE:

1. SKILL NEED IS NOT A GRANT. ``SkillNeed`` carries strings into the boundary;
   nothing here can mutate a grant set, a handle table, a ticket, or policy.
2. SNAPSHOT PINNING. An invocation binds to the skill's exact
   (package digest, effective digest) at start; ``verify_snapshot`` refuses ANY
   drift - no mid-task widening. New bytes = NEW invocation, normal resolution.
3. TRUST STATE comes from the frozen Skill closure itself (a4d07699): VERIFIED
   vs LEGACY_UNVERIFIED; a legacy skill can never satisfy a verified pin.
4. ONE AUTHORITATIVE TOOL EVIDENCE OWNER: :class:`ToolbeltEvidenceStore` in the
   FROZEN Toolbelt (92fb7dac). Composition references it and adds nothing:
   this module deliberately owns NO evidence store of its own. The earlier
   experimental ``PlanEvidenceStore`` was DELETED once 92fb7dac made the
   binding canonical - keeping it would be a second evidence authority.

Honest scope: deterministic experimental code over the frozen lanes; nothing
touches real GitHub, real credentials, or the production VOOL tree. The
lifecycle admission path here is the experimental plugin path; production must
REBIND it into VOOL's canonical ExecutionGate - never ship two permission gates.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass

from core.kernel.plugin_lifecycle import LifecycleRegistry
from experimental.composition_frozen.skill_a4d07699 import plugin_skills as pskill
from experimental.composition_frozen.skill_a4d07699 import skill_identity as skident
from experimental.composition_frozen.toolbelt_92fb7dac.evidence import (
    EVIDENCE_STORE,
    DriftKind,
    RevalidationResult,
    ToolPlanRef,
    reset_evidence_store,
)
from experimental.composition_frozen.toolbelt_92fb7dac.models import (
    CapabilityNeed,
    ToolPlan,
)
from experimental.composition_frozen.toolbelt_92fb7dac.probes import ProbeRunner

# Trust states come from the FROZEN Skill closure a4d07699 itself.
VERIFIED = skident.VERIFIED
LEGACY_UNVERIFIED = skident.LEGACY_UNVERIFIED


class SnapshotDrift(RuntimeError):
    """The pinned skill package changed under a running invocation."""


# ------------------------------------------------------------------ skill side


@dataclass(frozen=True)
class SkillNeed:
    """A workflow REQUEST from a skill. Carries zero authority by construction:
    there is no method here that touches grants, handles, tickets, or policy."""

    capability: str
    repository: str | None = None
    requested_by_ref: str = ""


def pin_skill(skill: pskill.Skill) -> dict:
    """Freeze EXACTLY what this invocation was told: identity ref, both digests,
    trust state, declared needs from front-matter `allowed-tools`.

    The returned snapshot is immutable evidence of provenance; needs are requests
    only (law 1)."""
    if skill.identity is not None:
        ref = skill.identity.short          # already ref#effective-digest12
        verified = True
        effective = skill.identity.digests.effective_instructions
        package = skill.identity.digests.package
    else:
        # Legacy law (a4d07699): LEGACY_UNVERIFIED structurally cannot satisfy a
        # verified pin - no ref, no hash equality possible.
        name_hash = hashlib.sha256(skill.raw_body.encode()).hexdigest()
        ref = f"legacy-unverified:{skill.name}#{name_hash[:12]}"
        verified = False
        effective = name_hash
        package = name_hash
    return {
        "ref": ref,
        "name": skill.name,
        "verified": verified,
        "effective_digest": effective,
        "package_digest": package,
        "trust": skident.VERIFIED if verified else skident.LEGACY_UNVERIFIED,
        "needs": tuple(SkillNeed(c) for c in skill.allowed_tools),
    }


def verify_snapshot(snapshot: dict, path) -> None:
    """Re-parse the CURRENT file; ANY drift from the pinned snapshot raises.

    A running invocation stays bound to its original bytes: mid-task widening is
    refused, not followed."""
    fresh = pskill.parse_skill(path)
    current = pin_skill(fresh) if fresh is not None else {}
    for key in ("ref", "effective_digest", "package_digest"):
        if snapshot.get(key) != current.get(key):
            raise SnapshotDrift(
                f"pinned skill snapshot drifted: {key} changed "
                f"({str(snapshot.get(key))[:16]}... -> {str(current.get(key))[:16]}...); "
                "the running invocation keeps its original snapshot")


# ------------------------------------------------------------- toolbelt side
#
# DELETED (final freeze): the composition-owned PlanEvidenceStore. Evidence is
# owned canonically by ToolbeltEvidenceStore inside the frozen Toolbelt lane;
# freeze/revalidate are consumed DIRECTLY from
# experimental.composition_frozen.toolbelt_92fb7dac.evidence. Nothing here may
# hold a second authoritative copy.


# ------------------------------------------------------------ composition gate


def resolve_needs_to_plan(need: SkillNeed, executable: str, *,
                          credential_id: str = "gh-store:github",
                          status: str = "READY") -> ToolPlan:
    """Build the Toolbelt plan a need resolves to. Pure assembly - this grants
    nothing; the plan still has to pass canonical freeze/revalidate + lifecycle
    + boundary."""
    return ToolPlan(
        need=CapabilityNeed(capability=need.capability),
        status=status,                       # type: ignore[arg-type]
        executable=executable,
        credential_handle=None,
        evidence=("assembled by composition harness",))


def admit_through_boundary(lifecycle: LifecycleRegistry, plugin_id: str,
                           args_json: str, *, capability: str):
    """The ONE dispatch path used by every composition test: skill need ->
    toolbelt evidence (already validated through the canonical store upstream) ->
    lifecycle admission -> epoch-guarded broker. This function adds no checks of
    its own because adding one here would BE a duplicate authority."""
    return lifecycle.execute(plugin_id, args_json, capability=capability)


def assert_single_evidence_owner() -> None:
    """Architecture invariant (F5): composition must NOT define its own
    authoritative plan-evidence store. Guards against the duplicate authority
    silently returning."""
    import sys
    mod = sys.modules[__name__]
    forbidden = ("PlanEvidenceStore", "_plans", "_records")
    offenders = [n for n in forbidden if hasattr(mod, n)]
    if offenders:
        raise AssertionError(
            f"composition module reintroduced an evidence authority: {offenders}; "
            "ToolbeltEvidenceStore is the ONE owner")


__all__ = [
    "EVIDENCE_STORE",
    "LEGACY_UNVERIFIED",
    "SkillNeed",
    "SkillSnapshot",
    "SnapshotDrift",
    "VERIFIED",
    "admit_through_boundary",
    "assert_single_evidence_owner",
    "pin_skill",
    "reset_evidence_store",
    "resolve_needs_to_plan",
    "verify_snapshot",
]

#: alias kept explicit for report legibility
SkillSnapshot = dict
