"""TASK_CONTRACT: the typed task law a council run must satisfy to exist.

A contract is not a prompt. Everything the task's authority depends on is a typed
field validated at construction — exact base and candidate SHAs, objective, writable
and forbidden scope, dependencies, authority owner, done condition, verification,
counterexample, evidence destination, cost policy, mutation permission — because a
task that cannot state them is a task whose refusals cannot be checked later.

The seat law: seats are PERMANENT TYPED ROLES with replaceable model identities
(the same law ``core/council/seats.py`` states for the bench). Only the builder
role ever holds write permission, and it holds it because the ROLE'S ENVELOPE is
computed from the role — there is no field anywhere that can name a reviewer as a
writer. Reviewers are mechanically read-only.

Completion law, three certificates that never imply one another:

* ``close_task`` — the done condition, the family-collapsed reviewer majority, and
  the mutation proof. One VERIFIED counterexample refuses the closure regardless
  of how unanimous the vote is: a deterministic counterexample beats a room full
  of agreement, and reviewer votes are collapsed by FAMILY — aliases of one model
  cast one vote, not two;
* ``mark_integration_green`` — the verification command actually passed;
* ``close_obligation`` — the VOOL obligation ledger entry actually closed.

And none of it promotes anything: this module contains no merge, no push, no
promotion — ``CompletionRecord.promote`` exists only to refuse, stating that
promotion is an operator action.
"""

from __future__ import annotations

import enum
import hashlib
import re
import time
import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from core.council.cost_ladder import CostPolicy
from core.council.model_provenance import ModelIdentity
from core.council.roles import ROLE_REGISTRY
from core.council.task_dag import paths_overlap


class TaskContractError(ValueError):
    """The task declaration violates the task law."""


_SHA_RE = re.compile(r"^[0-9a-f]{40}$")

#: Paths no task may ever name as writable, whatever its scope says.
_ALWAYS_FORBIDDEN: tuple[str, ...] = (".git",)

#: The role that may hold write permission. Everyone else is a reviewer.
WRITER_ROLES = frozenset({"builder"})


def is_reviewer_role(role_id: str) -> bool:
    return str(role_id or "") not in WRITER_ROLES


class MutationPermission(str, enum.Enum):
    READ_ONLY = "read_only"
    WORKSPACE_WRITE = "workspace_write"


class _GrantSentinel:
    __slots__ = ()
    _instance: _GrantSentinel | None = None

    def __new__(cls) -> _GrantSentinel:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


_SENTINEL = _GrantSentinel()


@dataclass(frozen=True)
class MutationGrant:
    """The operator's EXPLICIT grant of write permission for ONE task.

    Minted only by :func:`mint_mutation_grant`; direct construction fails on the
    private sentinel, so a contract declaring ``WORKSPACE_WRITE`` grants itself
    nothing — the grant is held by the gate's facts, not by the task.
    """

    task_id: str
    granted_by: str
    granted_at: float
    token: str
    _sentinel: _GrantSentinel = field(repr=False, compare=False, default=_SENTINEL)

    def __post_init__(self) -> None:
        if self._sentinel is not _SENTINEL:
            raise TaskContractError(
                "mutation grants can only be minted by mint_mutation_grant"
            )


def mint_mutation_grant(task_id: str, *, granted_by: str = "operator") -> MutationGrant:
    clean = str(task_id or "").strip()
    if not clean:
        raise TaskContractError("a mutation grant names the task it unlocks")
    return MutationGrant(
        task_id=clean,
        granted_by=str(granted_by or "operator"),
        granted_at=time.time(),
        token=uuid.uuid4().hex,
    )


@dataclass(frozen=True)
class TaskSeat:
    """One seat: a permanent typed role with a replaceable model identity."""

    seat_id: str
    role_id: str
    model: ModelIdentity

    def __post_init__(self) -> None:
        if not str(self.seat_id or "").strip():
            raise TaskContractError("a seat id must be non-empty")
        if self.role_id not in ROLE_REGISTRY:
            raise TaskContractError(
                f"unknown seat role {self.role_id!r} — roles are the closed set "
                f"{sorted(ROLE_REGISTRY)}"
            )
        if not isinstance(self.model, ModelIdentity):
            raise TaskContractError("a seat's model is a typed ModelIdentity")


@dataclass(frozen=True)
class TaskContract:
    """The whole task, stated so every later refusal is checkable."""

    task_id: str
    repo_root: str
    base_sha: str
    objective: str
    authority_owner: str
    done_condition: str
    verification: str
    counterexample: str
    evidence_path: str
    cost_policy: CostPolicy | None = None
    candidate_sha: str = ""
    writable_scope: tuple[str, ...] = ()
    forbidden_scope: tuple[str, ...] = ()
    dependencies: tuple[str, ...] = ()
    mutation: MutationPermission = MutationPermission.READ_ONLY
    seats: tuple[TaskSeat, ...] = ()
    required_independent_reviewers: int = 2

    def __post_init__(self) -> None:
        for name in ("task_id", "repo_root", "objective", "authority_owner",
                     "done_condition", "verification", "counterexample",
                     "evidence_path"):
            if not str(getattr(self, name) or "").strip():
                raise TaskContractError(f"task contract field {name!r} is required")
        if not _SHA_RE.match(str(self.base_sha or "")):
            raise TaskContractError("base_sha must be an exact 40-hex commit SHA")
        if str(self.candidate_sha or "") and not _SHA_RE.match(self.candidate_sha):
            raise TaskContractError(
                "candidate_sha is pending ('') or an exact 40-hex commit SHA"
            )
        if self.cost_policy is None or not isinstance(self.cost_policy, CostPolicy):
            raise TaskContractError(
                "a task contract requires a typed CostPolicy — a task without a "
                "budget is a task nobody can refuse"
            )

        writable = _clean_scope(self.writable_scope)
        forbidden = _clean_scope(self.forbidden_scope) + _ALWAYS_FORBIDDEN
        if self.mutation is MutationPermission.WORKSPACE_WRITE and not writable:
            raise TaskContractError(
                "WORKSPACE_WRITE requires a declared writable scope — a task that "
                "writes everywhere owns everything"
            )
        overlap = next(
            (f"{a} ~ {b}" for a in writable for b in forbidden
             if paths_overlap(a, b)),
            "",
        )
        if overlap:
            raise TaskContractError(
                f"writable and forbidden scope overlap ({overlap}) — the forbidden "
                "scope is a fence, not a suggestion"
            )
        object.__setattr__(self, "writable_scope", writable)
        object.__setattr__(self, "forbidden_scope", tuple(dict.fromkeys(forbidden)))
        object.__setattr__(self, "dependencies", _clean_deps(self.dependencies))

        evidence = str(self.evidence_path or "").strip()
        if evidence.startswith("/") or ".." in evidence.split("/"):
            raise TaskContractError(
                "evidence_path is relative to the evidence root and cannot escape it"
            )

        if not isinstance(self.required_independent_reviewers, int) or (
            self.required_independent_reviewers < 1
        ):
            raise TaskContractError("required_independent_reviewers is at least 1")
        self._validate_seats()

    def _validate_seats(self) -> None:
        if not self.seats:
            raise TaskContractError("a task contract seats the council that runs it")
        ids = [seat.seat_id for seat in self.seats]
        if len(ids) != len(set(ids)):
            raise TaskContractError("duplicate seat id — seats are addressable")
        identities = [seat.model for seat in self.seats]
        keys = [identity.key for identity in identities]
        if len(keys) != len(set(keys)):
            raise TaskContractError(
                "duplicate seat: the same model identity appears twice — replace "
                "it with a model from a different family, not a second copy"
            )
        if self.mutation is MutationPermission.WORKSPACE_WRITE and not any(
            seat.role_id in WRITER_ROLES for seat in self.seats
        ):
            raise TaskContractError(
                "a WORKSPACE_WRITE task needs a builder seat to hold the permission"
            )
        reviewer_families = {
            seat.model.family for seat in self.seats if is_reviewer_role(seat.role_id)
        }
        if len(reviewer_families) < self.required_independent_reviewers:
            raise TaskContractError(
                f"the bench holds {len(reviewer_families)} independent reviewer "
                f"families ({sorted(reviewer_families)}) but the contract requires "
                f"{self.required_independent_reviewers} — aliases of one family do "
                "not count as independent reviewers"
            )

    # ------------------------------------------------------------- projections
    def with_candidate(self, candidate_sha: str) -> TaskContract:
        """A new contract with the candidate minted. The old one is untouched."""
        wanted = str(candidate_sha or "").strip()
        if not _SHA_RE.match(wanted):
            raise TaskContractError("candidate_sha must be an exact 40-hex commit SHA")
        return replace(self, candidate_sha=wanted)

    def scope_allows(self, path: str) -> bool:
        """Whether a path is inside the writable scope and outside every fence."""
        if any(paths_overlap(path, fence) for fence in self.forbidden_scope):
            return False
        return any(paths_overlap(path, owned) for owned in self.writable_scope)

    def seat_envelopes(self) -> tuple[SeatEnvelope, ...]:
        """The capability envelope each seat presents at the effect door.

        COMPUTED from the role: the builder seat carries the contract's mutation
        permission and every other seat is read-only, unconditionally. There is no
        input that can make a reviewer a writer.
        """
        return tuple(
            SeatEnvelope(
                seat_id=seat.seat_id,
                role_id=seat.role_id,
                model=seat.model,
                mutation=(
                    self.mutation
                    if seat.role_id in WRITER_ROLES
                    else MutationPermission.READ_ONLY
                ),
            )
            for seat in self.seats
        )


@dataclass(frozen=True)
class SeatEnvelope:
    """What one seat is, and the mutation permission it holds at the door."""

    seat_id: str
    role_id: str
    model: ModelIdentity
    mutation: MutationPermission


def _clean_scope(scope: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            str(item or "").strip().strip("/") for item in scope or () if str(item or "").strip()
        )
    )


def _clean_deps(deps: Sequence[str]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(str(dep or "").strip() for dep in deps or () if str(dep or "").strip())
    )


# ----------------------------------------------------------------- counterexample


@dataclass(frozen=True)
class CounterexampleClaim:
    """A claimed refutation of the done condition.

    ``verified`` means a tool receipt exists that demonstrates it — the same law
    the orchestrator's tally applies to seat reports. A counterexample without a
    demonstration is an opinion, and opinions trump nothing.
    """

    claim: str
    verified: bool = False
    receipt: str = ""

    def __post_init__(self) -> None:
        if not str(self.claim or "").strip():
            raise TaskContractError("a counterexample states its claim")
        if self.verified and not str(self.receipt or "").strip():
            raise TaskContractError(
                "a VERIFIED counterexample carries the receipt that verified it"
            )


# --------------------------------------------------------------- mutation proof


class MutationProofRefused(RuntimeError):
    """A mutation proof did not prove what it claimed."""


@dataclass(frozen=True)
class MutationProof:
    """Proof that a task's writes happened under authority it actually held.

    The typed primitive a WORKSPACE_WRITE task must present at completion: which
    paths changed, the hash of the exact diff, and the lease (with its fencing
    counter) under which the writes were made.
    """

    task_id: str
    touched_paths: tuple[str, ...]
    diff_sha256: str
    lease_id: str
    fencing_counter: int

    def __post_init__(self) -> None:
        if not str(self.task_id or "").strip():
            raise TaskContractError("a mutation proof names its task")
        paths = tuple(
            dict.fromkeys(
                str(p or "").strip().strip("/") for p in self.touched_paths or () if str(p or "").strip()
            )
        )
        if not paths:
            raise TaskContractError("a mutation proof states the paths it touched")
        if not re.match(r"^[0-9a-f]{64}$", str(self.diff_sha256 or "")):
            raise TaskContractError("diff_sha256 is a 64-hex sha256 of the exact diff")
        if not str(self.lease_id or "").strip():
            raise TaskContractError("a mutation proof carries the lease it was made under")
        object.__setattr__(self, "touched_paths", paths)


@dataclass(frozen=True)
class VerifiedMutation:
    task_id: str
    touched_paths: tuple[str, ...]
    diff_sha256: str
    lease_id: str


def verify_mutation_proof(
    proof: MutationProof,
    contract: TaskContract,
    *,
    current_fencing: int,
    lease_scope: Iterable[str],
) -> VerifiedMutation:
    """Mechanically verify a mutation proof against the contract and the lease.

    Every path must be inside the contract's writable scope AND inside the lease's
    scope; the fencing counter must be the one the ledger currently honors. Anything
    else is a typed refusal, never a warning.
    """
    if proof.task_id != contract.task_id:
        raise MutationProofRefused(
            f"proof names task {proof.task_id!r}, contract names {contract.task_id!r}"
        )
    if not re.match(r"^[0-9a-f]{64}$", str(proof.diff_sha256 or "")):
        # Re-checked here as well as at construction: a proof that arrived from
        # anywhere but this module's constructor is untrusted input.
        raise MutationProofRefused("diff_sha256 is a 64-hex sha256 of the exact diff")
    if int(proof.fencing_counter) != int(current_fencing):
        raise MutationProofRefused(
            f"fencing counter {proof.fencing_counter} is not the honored epoch "
            f"{current_fencing} — the lease this proof rides is stale"
        )
    lease_paths = tuple(str(item or "").strip() for item in lease_scope or () if str(item or "").strip())
    for path in proof.touched_paths:
        if not contract.scope_allows(path):
            raise MutationProofRefused(
                f"`{path}` is outside the contract's writable scope (or inside its "
                "forbidden scope)"
            )
        if lease_paths and not any(paths_overlap(item, path) for item in lease_paths):
            raise MutationProofRefused(
                f"`{path}` is outside the writer lease's scope {sorted(lease_paths)}"
            )
    return VerifiedMutation(
        task_id=proof.task_id,
        touched_paths=proof.touched_paths,
        diff_sha256=proof.diff_sha256,
        lease_id=proof.lease_id,
    )


# ------------------------------------------------------------------ completions


class TaskDoneRefused(RuntimeError):
    """The task's done condition cannot be certified."""


class IntegrationGreenRefused(RuntimeError):
    """The verification did not pass; integration cannot be marked green."""


class PromotionRefused(PermissionError):
    """There is no promotion here. Merge, push and promotion are operator actions."""


@dataclass(frozen=True)
class TaskDone:
    """Certificate 1: the task itself is done."""

    task_id: str
    candidate_sha: str
    done_condition: str
    votes_by_family: Mapping[str, str]
    standing_counterexamples: int
    mutation_proof_sha: str | None


@dataclass(frozen=True)
class IntegrationGreen:
    """Certificate 2: the verification command actually passed."""

    task_id: str
    verification_command: str
    output_sha256: str


@dataclass(frozen=True)
class ObligationClosure:
    """Certificate 3: the VOOL obligation ledger entry is closed.

    The obligation ledger itself is another authority's (and out of this module's
    hands); this certificate records the reference, and closing it is a separate
    act from either the task or the integration.
    """

    task_id: str
    obligation_id: str


def close_task(
    contract: TaskContract,
    *,
    mutation_proof: MutationProof | None = None,
    counterexamples: Iterable[CounterexampleClaim] = (),
    reviewer_verdicts: Iterable[tuple[ModelIdentity, str]] = (),
    lease_fencing: int | None = None,
    lease_scope: Iterable[str] | None = None,
) -> TaskDone:
    """Mint the TASK-DONE certificate — or refuse it, naming the law it broke.

    * one VERIFIED counterexample refuses the closure, whatever the vote count;
    * reviewer votes are collapsed by FAMILY: aliases of one model cast ONE vote,
      and a family split across AGREE and DISAGREE counts as DISAGREE;
    * the family majority must be AGREE and the families voting must meet the
      contract's independence floor;
    * a WORKSPACE_WRITE task must present a mechanically verified mutation proof —
      ``lease_fencing`` is the ledger's currently honored epoch when the closer
      holds one (the proof's own counter when it is omitted, which is the case of
      a proof minted straight off a live lease);
    * the candidate SHA must be minted — a task with no artifact cannot be done.
    """

    claims = tuple(counterexamples or ())
    standing = [claim for claim in claims if claim.verified]
    if standing:
        raise TaskDoneRefused(
            "counterexample beats majority: "
            + "; ".join(claim.claim for claim in standing)
        )

    verdicts = tuple(reviewer_verdicts or ())
    by_family: dict[str, list[str]] = {}
    for identity, verdict in verdicts:
        if not isinstance(identity, ModelIdentity):
            raise TaskDoneRefused("a reviewer verdict is cast by a ModelIdentity")
        word = "AGREE" if str(verdict).upper() == "AGREE" else "DISAGREE"
        by_family.setdefault(identity.family, []).append(word)
    votes_by_family: dict[str, str] = {
        family: ("AGREE" if words and all(w == "AGREE" for w in words) else "DISAGREE")
        for family, words in by_family.items()
    }
    if len(votes_by_family) < contract.required_independent_reviewers:
        raise TaskDoneRefused(
            f"only {len(votes_by_family)} independent reviewer families voted "
            f"({sorted(votes_by_family)}); the contract requires "
            f"{contract.required_independent_reviewers} — aliases do not vote twice"
        )
    agree = sum(1 for vote in votes_by_family.values() if vote == "AGREE")
    if agree <= len(votes_by_family) - agree:
        raise TaskDoneRefused(
            f"no family-majority for done: {votes_by_family}"
        )

    if not contract.candidate_sha:
        raise TaskDoneRefused(
            "the candidate SHA is still pending — a task with no artifact is not done"
        )

    proof_sha: str | None = None
    if contract.mutation is MutationPermission.WORKSPACE_WRITE:
        if mutation_proof is None:
            raise TaskDoneRefused(
                "a WORKSPACE_WRITE task closes with a mutation proof or not at all"
            )
        verified = None
        try:
            verified = verify_mutation_proof(
                mutation_proof, contract,
                current_fencing=(
                    mutation_proof.fencing_counter
                    if lease_fencing is None
                    else int(lease_fencing)
                ),
                lease_scope=(
                    contract.writable_scope if lease_scope is None else tuple(lease_scope)
                ),
            )
        except MutationProofRefused as exc:
            raise TaskDoneRefused(
                f"the mutation proof does not hold: {exc}"
            ) from exc
        proof_sha = hashlib.sha256(verified.diff_sha256.encode("utf-8")).hexdigest()

    return TaskDone(
        task_id=contract.task_id,
        candidate_sha=contract.candidate_sha,
        done_condition=contract.done_condition,
        votes_by_family=votes_by_family,
        standing_counterexamples=0,
        mutation_proof_sha=proof_sha,
    )


def mark_integration_green(
    contract: TaskContract,
    *,
    verification_command: str,
    exit_code: int,
    output_sha256: str,
) -> IntegrationGreen:
    """Mint the INTEGRATION-GREEN certificate. Failing verification refuses."""
    if not str(verification_command or "").strip():
        raise IntegrationGreenRefused("integration green states the command that ran")
    if int(exit_code) != 0:
        raise IntegrationGreenRefused(
            f"verification exited {exit_code}: integration is not green"
        )
    if not re.match(r"^[0-9a-f]{64}$", str(output_sha256 or "")):
        raise IntegrationGreenRefused("output_sha256 is a 64-hex sha256 of the run log")
    return IntegrationGreen(
        task_id=contract.task_id,
        verification_command=str(verification_command),
        output_sha256=str(output_sha256),
    )


def close_obligation(contract: TaskContract, *, obligation_id: str) -> ObligationClosure:
    """Mint the OBLIGATION-CLOSURE certificate referencing the VOOL ledger entry."""
    clean = str(obligation_id or "").strip()
    if not clean:
        raise TaskContractError("an obligation closure names its VOOL obligation id")
    return ObligationClosure(task_id=contract.task_id, obligation_id=clean)


@dataclass(frozen=True)
class CompletionRecord:
    """The three closures, held SEPARATELY because they are three facts.

    ``task`` done says the work finished; ``integration`` green says the whole
    tree's verification passed; ``obligation`` closed says the VOOL obligation
    ledger agrees the duty is discharged. None implies another, and the record
    never turns them into a promotion.
    """

    task: TaskDone | None = None
    integration: IntegrationGreen | None = None
    obligation: ObligationClosure | None = None

    @property
    def fully_closed(self) -> bool:
        return None not in (self.task, self.integration, self.obligation)

    def promote(self, *args: Any, **kwargs: Any) -> None:
        """The promotion API: a refusal. There is no code path here that merges,
        pushes, or promotes — those are operator actions, and this method exists
        so that anyone looking for one finds a typed statement instead."""
        raise PromotionRefused(
            "promotion is an operator action; this module neither merges, pushes, "
            "nor promotes"
        )


__all__ = [
    "WRITER_ROLES",
    "CompletionRecord",
    "CounterexampleClaim",
    "IntegrationGreen",
    "IntegrationGreenRefused",
    "MutationGrant",
    "MutationPermission",
    "MutationProof",
    "MutationProofRefused",
    "PromotionRefused",
    "SeatEnvelope",
    "TaskContract",
    "TaskContractError",
    "TaskDone",
    "TaskDoneRefused",
    "TaskSeat",
    "close_obligation",
    "close_task",
    "is_reviewer_role",
    "mark_integration_green",
    "mint_mutation_grant",
    "verify_mutation_proof",
]
