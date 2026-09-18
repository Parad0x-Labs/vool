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

    def __post_init__(self) -> None:
        pass  # frozen; TOCTOU note appended via full_note()

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
      github.branch.push        branch existence + protection metadata
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
        if branch is None:
            return _unknown(op, slug, role, "no branch bound to request")
        ok, body = _gh_json(runner, f"repos/{slug}/branches/{branch}")
        if not ok:
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                     evidence=("branch lookup failed",),
                                     note="branch missing or inaccessible")
        protected = '"protected":true' in body.replace(" ", "")
        ev = [f"branch {branch} exists", f"protected={protected}",
              f"repository_role={role.value}"]
        if protected:
            # Protection metadata says direct pushes are restricted; a PR route
            # may exist but that is a different operation with different rules.
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                     evidence=tuple(ev),
                                     note="protected branch: direct push refused; "
                                          "PR route is a separate operation")
        if not role_at_least(role, RepositoryRole.WRITE):
            return OperationEvidence(op, slug, role, OperationStatus.OPERATION_REFUSED,
                                     evidence=tuple(ev), note="role below WRITE")
        return OperationEvidence(op, slug, role, OperationStatus.OPERATION_PROVEN,
                                 evidence=tuple(ev),
                                 note="unprotected branch + WRITE role; "
                                      "org rulesets beyond branch protection are not visible here")

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
