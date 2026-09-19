"""Gate 0: the deterministic stop every task passes before ANY model call.

Gate 0 is not advice, a score, or a prompt. It is a PURE decision over typed facts
evaluated in a FIXED check order: the same facts and the same contract always yield
the same decision and the same reason, because the first failing check in the order
is the reason reported — no coin flips between refusals.

The order (``GATE0_CHECK_ORDER``) is part of the law:

1.  ``repo_root``          — the contract names THIS checkout (WRONG_REPO);
2.  ``base_sha``           — its base SHA is the checkout's HEAD (WRONG_BASE_SHA);
3.  ``owned_tree_clean``   — no dirty path inside the writable scope
                            (DIRTY_OWNED_TREE);
4.  ``task_not_complete``  — the task is not already COMPLETE
                            (TASK_ALREADY_COMPLETE);
5.  ``dependencies_ready`` — every dependency is COMPLETE, missing ones included
                            (DEPENDENCY_BLOCKED);
6.  ``authority_owner_known`` — the named authority owner exists
                            (UNKNOWN_AUTHORITY_OWNER);
7.  ``no_overlapping_writer`` — no live lease overlaps the writable scope
                            (OVERLAPPING_WRITER);
8.  ``providers_available`` — every seat's provider is available; unknown fails
                            closed (PROVIDER_UNAVAILABLE);
9.  ``model_roles_supported`` — every seat's model supports its role; unknown
                            fails closed (UNSUPPORTED_MODEL_ROLE);
10. ``budget_available``   — no spend ceiling is crossed (BUDGET_EXHAUSTED);
11. ``mutation_authorized`` — write permission is operator-granted FOR THIS TASK,
                            and any premium escalation names THIS task
                            (FORBIDDEN_PERMISSION);
12. ``evidence_destination_ready`` — the evidence destination is writable
                            (MISSING_EVIDENCE_DESTINATION).

The orchestrator consults the gate BEFORE it dispatches a single seat, and a
refused task spends ZERO model calls — the dedicated tests prove it by driving the
real state machine with a counting seat turn.

Facts are injected, never fetched: the gate touches no git, no network, no disk.
Whoever builds :class:`Gate0Facts` (the server, a test) owns how the world is
observed; the gate owns only the decision.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from core.council.cost_ladder import SpendMeter
from core.council.task_contract import (
    MutationGrant,
    MutationPermission,
    TaskContract,
)
from core.council.task_dag import (
    TaskStatus,
    WriterLease,
    paths_overlap,
    scopes_overlap,
)


class Gate0Reason(str, enum.Enum):
    """The typed stop codes. Closed set; the ledger stores the value."""

    WRONG_REPO = "wrong_repo"
    WRONG_BASE_SHA = "wrong_base_sha"
    DIRTY_OWNED_TREE = "dirty_owned_tree"
    TASK_ALREADY_COMPLETE = "task_already_complete"
    DEPENDENCY_BLOCKED = "dependency_blocked"
    UNKNOWN_AUTHORITY_OWNER = "unknown_authority_owner"
    OVERLAPPING_WRITER = "overlapping_writer"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    UNSUPPORTED_MODEL_ROLE = "unsupported_model_role"
    BUDGET_EXHAUSTED = "budget_exhausted"
    FORBIDDEN_PERMISSION = "forbidden_permission"
    MISSING_EVIDENCE_DESTINATION = "missing_evidence_destination"


#: The fixed check order — first failure is the reason. Part of the law.
GATE0_CHECK_ORDER: tuple[str, ...] = (
    "repo_root",
    "base_sha",
    "owned_tree_clean",
    "task_not_complete",
    "dependencies_ready",
    "authority_owner_known",
    "no_overlapping_writer",
    "providers_available",
    "model_roles_supported",
    "budget_available",
    "mutation_authorized",
    "evidence_destination_ready",
)


@dataclass(frozen=True)
class Gate0Facts:
    """How the world is, at evaluation time. Injected; never fetched by the gate.

    ``spend_meter`` is the LIVE meter the model door will reserve through — it is
    mutable state by design, and the gate reads its ``exhaustion()`` under the
    meter's own lock.
    """

    repo_root: str
    head_sha: str
    authority_owners: frozenset[str]
    spend_meter: SpendMeter
    dirty_paths: frozenset[str] = frozenset()
    task_status: TaskStatus | None = None
    dependency_statuses: Mapping[str, str] = field(default_factory=dict)
    provider_availability: Mapping[str, bool] = field(default_factory=dict)
    role_support: Mapping[str, frozenset[str]] = field(default_factory=dict)
    live_leases: tuple[WriterLease, ...] = ()
    evidence_root_writable: bool = True
    mutation_grants: tuple[MutationGrant, ...] = ()


@dataclass(frozen=True)
class Gate0Check:
    """One check's outcome. ``reason`` is the stop code when it failed."""

    name: str
    passed: bool
    reason: Gate0Reason | None
    detail: str = ""


@dataclass(frozen=True)
class Gate0Decision:
    """The whole decision: allowed or stopped, plus the audit trail of checks."""

    allowed: bool
    reason: Gate0Reason | None
    detail: str
    checks: tuple[Gate0Check, ...]
    seat_envelopes: tuple[Any, ...] = ()

    def as_row(self) -> dict[str, Any]:
        """The ledger-shaped record: one gate evaluation, every check, verbatim."""
        return {
            "allowed": self.allowed,
            "reason": self.reason.value if self.reason else None,
            "detail": self.detail,
            "checks": [
                {
                    "name": check.name,
                    "passed": check.passed,
                    "reason": check.reason.value if check.reason else None,
                    "detail": check.detail,
                }
                for check in self.checks
            ],
        }


def _norm_repo(path: str) -> str:
    return str(path or "").rstrip("/")


class Gate0:
    """Evaluates one task contract against frozen facts. Pure; owns no state."""

    def __init__(self, facts: Gate0Facts) -> None:
        if not isinstance(facts, Gate0Facts):
            raise TypeError("Gate0 evaluates typed Gate0Facts")
        self._facts = facts

    @property
    def facts(self) -> Gate0Facts:
        return self._facts

    def evaluate(self, contract: TaskContract) -> Gate0Decision:
        if not isinstance(contract, TaskContract):
            raise TypeError("Gate0 evaluates typed TaskContracts")

        checks: list[Gate0Check] = []

        def stop(reason: Gate0Reason, detail: str) -> Gate0Decision:
            return Gate0Decision(
                allowed=False, reason=reason, detail=detail,
                checks=tuple(checks), seat_envelopes=(),
            )

        def pass_check(name: str, detail: str = "") -> None:
            checks.append(Gate0Check(name=name, passed=True, reason=None, detail=detail))

        # 1 — repo identity. A task built for another checkout is not this task.
        if _norm_repo(contract.repo_root) != _norm_repo(self._facts.repo_root):
            checks.append(Gate0Check(
                "repo_root", False, Gate0Reason.WRONG_REPO,
                f"contract names repo {contract.repo_root!r}, this checkout is "
                f"{self._facts.repo_root!r}",
            ))
            return stop(Gate0Reason.WRONG_REPO, checks[-1].detail)
        pass_check("repo_root")

        # 2 — base SHA. The task was planned against an exact commit; anything
        # else means the world moved and the plan is stale.
        if str(contract.base_sha or "") != str(self._facts.head_sha or ""):
            checks.append(Gate0Check(
                "base_sha", False, Gate0Reason.WRONG_BASE_SHA,
                f"contract base {contract.base_sha[:12]}… is not HEAD "
                f"{str(self._facts.head_sha or '')[:12]}…",
            ))
            return stop(Gate0Reason.WRONG_BASE_SHA, checks[-1].detail)
        pass_check("base_sha")

        # 3 — owned tree clean. The task's OWNED paths must be clean, or its
        # baseline for "what did I change" is already a lie.
        dirty_owned = sorted(
            path for path in self._facts.dirty_paths
            if any(paths_overlap(path, owned) for owned in contract.writable_scope)
        )
        if dirty_owned:
            checks.append(Gate0Check(
                "owned_tree_clean", False, Gate0Reason.DIRTY_OWNED_TREE,
                f"owned scope is dirty: {', '.join(dirty_owned[:8])}",
            ))
            return stop(Gate0Reason.DIRTY_OWNED_TREE, checks[-1].detail)
        pass_check("owned_tree_clean")

        # 4 — not already complete. A completed task re-running spends again for
        # work the DAG says exists.
        if self._facts.task_status is TaskStatus.COMPLETE:
            checks.append(Gate0Check(
                "task_not_complete", False, Gate0Reason.TASK_ALREADY_COMPLETE,
                f"task {contract.task_id!r} is already COMPLETE in the DAG",
            ))
            return stop(Gate0Reason.TASK_ALREADY_COMPLETE, checks[-1].detail)
        pass_check("task_not_complete")

        # 5 — dependencies. Every dependency COMPLETE; a missing status is a
        # blocked dependency, never an assumed one.
        blocked: list[str] = []
        for dep in contract.dependencies:
            status = self._facts.dependency_statuses.get(dep)
            if status != TaskStatus.COMPLETE.value:
                blocked.append(dep)
        if blocked:
            checks.append(Gate0Check(
                "dependencies_ready", False, Gate0Reason.DEPENDENCY_BLOCKED,
                f"dependencies not complete: {', '.join(blocked[:8])}",
            ))
            return stop(Gate0Reason.DEPENDENCY_BLOCKED, checks[-1].detail)
        pass_check("dependencies_ready")

        # 6 — authority owner. The contract names WHO owns the authority to run
        # it; an unknown owner is an unowned task.
        if contract.authority_owner not in self._facts.authority_owners:
            checks.append(Gate0Check(
                "authority_owner_known", False, Gate0Reason.UNKNOWN_AUTHORITY_OWNER,
                f"authority owner {contract.authority_owner!r} is not a known owner "
                f"({', '.join(sorted(self._facts.authority_owners)) or 'none registered'})",
            ))
            return stop(Gate0Reason.UNKNOWN_AUTHORITY_OWNER, checks[-1].detail)
        pass_check("authority_owner_known")

        # 7 — one writer per overlapping authority. Only a WRITING task needs the
        # writer lane; read-only tasks ride past other tasks' leases.
        if contract.mutation is MutationPermission.WORKSPACE_WRITE:
            conflicting = next(
                (
                    lease for lease in self._facts.live_leases
                    if lease.task_id != contract.task_id
                    and scopes_overlap(lease.scope, contract.writable_scope)
                ),
                None,
            )
            if conflicting is not None:
                checks.append(Gate0Check(
                    "no_overlapping_writer", False, Gate0Reason.OVERLAPPING_WRITER,
                    f"task {conflicting.task_id!r} holds a live writer lease "
                    f"overlapping {sorted(contract.writable_scope)} "
                    f"(holder {conflicting.holder!r})",
                ))
                return stop(Gate0Reason.OVERLAPPING_WRITER, checks[-1].detail)
        pass_check("no_overlapping_writer")

        # 8 — providers available; an UNKNOWN provider fails closed.
        unavailable = sorted({
            seat.model.provider for seat in contract.seats
            if self._facts.provider_availability.get(seat.model.provider) is not True
        })
        if unavailable:
            checks.append(Gate0Check(
                "providers_available", False, Gate0Reason.PROVIDER_UNAVAILABLE,
                f"providers unavailable or unknown: {', '.join(unavailable)}",
            ))
            return stop(Gate0Reason.PROVIDER_UNAVAILABLE, checks[-1].detail)
        pass_check("providers_available")

        # 9 — model roles supported; a model MISSING from the support map fails
        # closed rather than being assumed capable.
        unsupported = [
            f"{seat.model.key} as {seat.role_id}" for seat in contract.seats
            if seat.role_id not in self._facts.role_support.get(seat.model.key, frozenset())
        ]
        if unsupported:
            checks.append(Gate0Check(
                "model_roles_supported", False, Gate0Reason.UNSUPPORTED_MODEL_ROLE,
                f"model/role not supported: {', '.join(unsupported[:8])}",
            ))
            return stop(Gate0Reason.UNSUPPORTED_MODEL_ROLE, checks[-1].detail)
        pass_check("model_roles_supported")

        # 10 — budget. Any crossed ceiling stops the task before it can spend.
        exhaustion = self._facts.spend_meter.exhaustion()
        if exhaustion is not None:
            detail = f"spend ceiling crossed: {exhaustion.value}"
            checks.append(Gate0Check(
                "budget_available", False, Gate0Reason.BUDGET_EXHAUSTED, detail,
            ))
            return stop(Gate0Reason.BUDGET_EXHAUSTED, detail)
        pass_check("budget_available")

        # 11 — mutation authorized. Write permission is granted by the operator
        # FOR THIS TASK (a grant naming another task authorizes nothing), and a
        # premium escalation carried by the policy must name THIS task too.
        if contract.mutation is MutationPermission.WORKSPACE_WRITE and not any(
            grant.task_id == contract.task_id
            for grant in self._facts.mutation_grants
        ):
            detail = (
                f"task {contract.task_id!r} declares WORKSPACE_WRITE but holds no "
                "operator mutation grant naming it"
            )
            checks.append(Gate0Check(
                "mutation_authorized", False, Gate0Reason.FORBIDDEN_PERMISSION, detail,
            ))
            return stop(Gate0Reason.FORBIDDEN_PERMISSION, detail)
        escalation = contract.cost_policy.escalation
        if escalation is not None and escalation.task_id != contract.task_id:
            detail = (
                f"premium escalation names task {escalation.task_id!r}, not "
                f"{contract.task_id!r} — an escalation for another task authorizes "
                "nothing here"
            )
            checks.append(Gate0Check(
                "mutation_authorized", False, Gate0Reason.FORBIDDEN_PERMISSION, detail,
            ))
            return stop(Gate0Reason.FORBIDDEN_PERMISSION, detail)
        pass_check("mutation_authorized")

        # 12 — evidence destination. Work with nowhere to file its evidence is
        # work that will end unprovable.
        if not self._facts.evidence_root_writable:
            checks.append(Gate0Check(
                "evidence_destination_ready", False,
                Gate0Reason.MISSING_EVIDENCE_DESTINATION,
                "the evidence root is not writable — the task cannot file what "
                "it will produce",
            ))
            return stop(Gate0Reason.MISSING_EVIDENCE_DESTINATION, checks[-1].detail)
        pass_check("evidence_destination_ready")

        return Gate0Decision(
            allowed=True,
            reason=None,
            detail="all checks passed",
            checks=tuple(checks),
            seat_envelopes=contract.seat_envelopes(),
        )


__all__ = [
    "GATE0_CHECK_ORDER",
    "Gate0",
    "Gate0Check",
    "Gate0Decision",
    "Gate0Facts",
    "Gate0Reason",
]
