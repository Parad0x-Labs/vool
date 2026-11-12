"""Operation-specific capability evidence (Pass #3).

Retraction: a repository-level WRITE role does NOT prove that one exact
operation (push branch X, force-push, merge PR #42, change settings) is
permitted. Layers, never flattened:

    AUTHENTICATED IDENTITY            (identities.py)
    REPOSITORY ROLE / viewerPermission(read-only metadata)
    OPERATION-SPECIFIC PROVIDER EVIDENCE  (read-only APIs ONLY; this module)
    PLATFORM AUTHORIZATION                (never here)

PROVEN is returned only when read-only provider evidence genuinely establishes
the exact operation. When GitHub cannot guarantee pushability without attempting
the push, UNKNOWN is the honest answer. A remaining TOCTOU race is stated on
every result: provider rules can change between final probe and dispatch.

Pass #4 closure — LAW: UNOBSERVED GOVERNING POLICY -> CANNOT RETURN PROVEN.
STATUS=PROVEN requires COMPLETE coverage of every provider-side policy surface
that governs the operation; missing policy information is never permission.
Even a PROVEN result means "provider evidence indicates permitted at observation
time" — it is NOT VOOL platform authorization, and the TOCTOU race remains.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

from core.toolbelt.probes import ProbeRunner

TOCTOU_NOTE = ("TOCTOU REMAINDER: provider metadata may change between this "
               "probe and dispatch; the Authoritative Execution Boundary must "
               "consume a fresh ToolPlanRef plus domain preconditions at effect time")


class OperationStatus(str, Enum):
    OPERATION_PROVEN = "OPERATION_PROVEN"
    OPERATION_REFUSED = "OPERATION_REFUSED"
    OPERATION_UNKNOWN = "OPERATION_UNKNOWN"


class RepositoryRole(str, Enum):
    ADMIN = "ADMIN"
    MAINTAIN = "MAINTAIN"
    WRITE = "WRITE"
    TRIAGE = "TRIAGE"
    READ = "READ"
    NONE = "NONE"


_ROLE_ORDER = [RepositoryRole.READ, RepositoryRole.TRIAGE, RepositoryRole.WRITE,
               RepositoryRole.MAINTAIN, RepositoryRole.ADMIN]


def role_at_least(role: RepositoryRole, minimum: RepositoryRole) -> bool:
    if role is RepositoryRole.NONE:
        return False
    return _ROLE_ORDER.index(role) >= _ROLE_ORDER.index(minimum)


@dataclass(frozen=True)
class OperationEvidence:
    operation: str                      # e.g. "github.branch.push"
    resource: str                       # "owner/repo"
    repository_role: RepositoryRole     # what viewerPermission established
    status: OperationStatus
    evidence: tuple[str, ...] = ()      # secretless, read-only probe results
    note: str = ""
    # Pass #4 evidence-coverage law: STATUS=PROVEN requires COMPLETE coverage of
    # every provider-side policy surface that governs the operation.
    observed_policy_surfaces: tuple[str, ...] = ()
    required_policy_surfaces: tuple[str, ...] = ()

    @property
    def coverage_complete(self) -> bool:
        return all(s in self.observed_policy_surfaces
                   for s in self.required_policy_surfaces)

    def __post_init__(self) -> None:
        # Structural enforcement: a PROVEN result with incomplete coverage is a
        # bug, not an opinion — refuse to construct it.
        if self.status is OperationStatus.OPERATION_PROVEN and not self.coverage_complete:
            raise ValueError(
                f"PROVEN requires complete evidence coverage "
                f"(observed={self.observed_policy_surfaces}, "
                f"required={self.required_policy_surfaces}) for {self.operation}")

    def full_note(self) -> str:
        base = self.note or ""
        return f"{base}; {TOCTOU_NOTE}" if base else TOCTOU_NOTE


def _gh_json(runner: ProbeRunner, *args: str) -> tuple[bool, str]:
    r = runner.run(["gh", "api", *args])
    return r.ok, r.stdout


def preflight(operation: str, slug: str, runner: ProbeRunner,
              role: RepositoryRole, *, branch: str | None = None,
              pr_number: int | None = None) -> OperationEvidence:
    """Read-only, operation-specific preflight.

    Supported exact operations (evidence model, not full provider coverage):
      github.repo.read          role alone proves it (any role > NONE)
      github.branch.push        branch existence + protection + ruleset surfaces;
                                PROVEN only when ALL governing surfaces observed
      github.branch.force_push  explicit allow_force_pushes flag only
      github.pr.merge           mergeable flag + WRITE role
      github.repo.settings.write ADMIN role necessary, still UNKNOWN (org rules)
    """
    op = operation.lower()
    if op.startswith("github."):
        op = op[len("github."):]

    if op == "repo.read":
        if role is RepositoryRole.NONE:
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                     evidence=("viewerPermission=NONE",))
        return OperationEvidence(op, slug, role, OperationStatus.OPERATION_PROVEN,
                                 evidence=(f"viewerPermission={role.value}",))

    if op == "branch.push":
        # Pass #4 evidence ceiling: the governing policy surfaces for a direct
        # branch push are classic branch protection AND org/repository rulesets.
        required = ("classic_branch_protection", "rulesets")
        if branch is None:
            return _unknown(op, slug, role, "no branch bound to request")
        ok, body = _gh_json(runner, f"repos/{slug}/branches/{branch}")
        if not ok:
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                     evidence=("branch lookup failed",),
                                     note="branch missing or inaccessible",
                                     required_policy_surfaces=required)
        protected = '"protected":true' in body.replace(" ", "")
        ev = [f"branch {branch} exists", f"protected={protected}",
              f"repository_role={role.value}"]
        observed: tuple[str, ...] = ()
        if protected:
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                     evidence=tuple(ev),
                                     note="protected branch: direct push refused; "
                                          "PR route is a separate operation",
                                     observed_policy_surfaces=("classic_branch_protection",),
                                     required_policy_surfaces=required)
        if not role_at_least(role, RepositoryRole.WRITE):
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                     evidence=tuple(ev), note="role below WRITE",
                                     required_policy_surfaces=required)
        # Role + unprotected is NOT enough (Pass #3 overclaim retracted): rulesets
        # can block pushes invisibly. Observe them or return UNKNOWN.
        rules_ok, rules_body = _gh_json(
            runner, f"repos/{slug}/rules/branches?branch={branch}")
        if not rules_ok:
            return OperationEvidence(
                op, slug, role, OperationStatus.OPERATION_UNKNOWN,
                evidence=(*ev, "ruleset query unavailable"),
                note="provider policy coverage incomplete — unobserved governing "
                     "policy cannot yield PROVEN",
                observed_policy_surfaces=("classic_branch_protection",),
                required_policy_surfaces=required)
        observed = ("classic_branch_protection", "rulesets")
        if '"PUSH_REQUEST"' in rules_body or '"NON_FAST_FORWARD"' in rules_body:
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                     evidence=(*ev, "blocking ruleset observed"),
                                     observed_policy_surfaces=observed,
                                     required_policy_surfaces=required)
        ev.append("rulesets explicitly evaluated: none blocking")
        return OperationEvidence(op, slug, role, OperationStatus.OPERATION_PROVEN,
                                 evidence=tuple(ev),
                                 note="all governing policy surfaces this evidence model "
                                      "requires were observed permissive",
                                 observed_policy_surfaces=observed,
                                 required_policy_surfaces=required)

    if op == "branch.force_push":
        if branch is None:
            return _unknown(op, slug, role, "no branch bound to request")
        ok, body = _gh_json(runner, f"repos/{slug}/branches/{branch}/protection")
        if not ok:
            # No protection record: force-push semantics fall back to provider
            # defaults/org policy we cannot see -> never guess PROVEN.
            return OperationEvidence(
                op, slug, role, OperationStatus.OPERATION_UNKNOWN,
                evidence=("no branch protection record", f"repository_role={role.value}"),
                note="unprotected-branch force-push depends on invisible org defaults")
        if '"allow_force_pushes"' in body.replace(" ", "") and \
                '"enabled":true' in body.replace(" ", ""):
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_PROVEN,
                                     evidence=("protection.allow_force_pushes=true",
                                               f"repository_role={role.value}"))
        return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                 evidence=("protection present without allow_force_pushes",
                                           f"repository_role={role.value}"))

    if op == "pr.merge":
        if pr_number is None:
            return _unknown(op, slug, role, "no PR bound to request")
        ok, body = _gh_json(runner, f"repos/{slug}/pulls/{pr_number}")
        if not ok:
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                     evidence=("PR lookup failed",))
        ev = [f"PR #{pr_number} found"]
        if not role_at_least(role, RepositoryRole.WRITE):
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                     evidence=tuple(ev), note="role below WRITE")
        # mergeable=false is provider-refused; anything else stays UNKNOWN.
        if '"mergeable":false' in body.replace(" ", ""):
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                     evidence=(*ev, "mergeable=false"))
        return OperationEvidence(op, slug, role, OperationStatus.OPERATION_UNKNOWN,
                                 evidence=(*ev, "mergeable state not conclusively true"),
                                 note="WRITE role does not prove merge: required "
                                      "reviews/status checks are not fully visible")

    if op == "repo.settings.write":
        if not role_at_least(role, RepositoryRole.ADMIN):
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                     evidence=(f"repository_role={role.value}",),
                                     note="settings require ADMIN")
        return OperationEvidence(op, slug, role, OperationStatus.OPERATION_UNKNOWN,
                                 evidence=(f"repository_role={role.value}",),
                                 note="even ADMIN is insufficient evidence: org-level "
                                      "restrictions are invisible to read-only probes")

    return _unknown(op, slug, role, f"no evidence model for operation {operation!r}")


def _unknown(op: str, slug: str, role: RepositoryRole, why: str) -> OperationEvidence:
    return OperationEvidence(op, slug, role, OperationStatus.OPERATION_UNKNOWN, note=why)


def parse_viewer_permission(raw: str) -> RepositoryRole:
    m = re.search(r"[A-Z_]+", raw or "")
    try:
        return RepositoryRole(m.group(0)) if m else RepositoryRole.NONE
    except ValueError:
        return RepositoryRole.NONE


__all__ = ["OperationEvidence", "OperationStatus", "RepositoryRole",
           "parse_viewer_permission", "preflight", "role_at_least", "TOCTOU_NOTE"]
