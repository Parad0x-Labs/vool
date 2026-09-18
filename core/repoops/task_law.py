"""TASK_CONTRACT and Gate 0, mechanically enforced on the RepoOps path before any model spend.

`core.council.task_contract` and `core.council.gate0` were built correct and left unwired: the
only code constructing a `TaskContract` or evaluating a `Gate0` was the council's own test file,
so nothing in the product could be refused by them. This module is the wiring for the RepoOps
lane. It does not re-implement either: it builds the typed contract from the session the operator
asked for, observes the FACTS from git, the model-health registry and the spend meter, and hands
both to the real `Gate0`.

The refusal happens at `repo.session.open`, before a model has been asked anything, so a
repository at the wrong SHA, a dirty owned tree, an unavailable provider, an exhausted budget or
an unauthorized mutation costs exactly zero model calls.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: A RepoOps session's default spend envelope. Ceilings are a REFUSAL boundary, not an output
#: budget: they bound how much of an operator's money one repository task may consume before a
#: human is asked again. They are not `max_tokens` and never truncate a reply.
DEFAULT_MAX_CALLS = 40
DEFAULT_MAX_TOKENS = 2_000_000
DEFAULT_MAX_COST_USD = 5.0
DEFAULT_WALL_CLOCK_SECONDS = 3600.0


@dataclass(frozen=True)
class GateOutcome:
    allowed: bool
    reason: str
    detail: str
    checks: tuple[dict[str, Any], ...]
    task_id: str
    contract_error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "allowed": self.allowed,
            "reason": self.reason,
            "detail": self.detail,
            "checks": [dict(c) for c in self.checks],
            "task_id": self.task_id,
            "contract_error": self.contract_error,
        }


def _model_identity(raw: str, *, fallback: str):
    from core.council.model_provenance import ModelIdentity, infer_provider

    text = str(raw or "").strip() or fallback
    provider = infer_provider(text)
    if provider == "unknown":
        provider, _, model = text.partition("/")
        model = model or text
        provider = provider or "local"
    else:
        model = text.split("/", 1)[-1] if "/" in text else text
    return ModelIdentity(provider=str(provider), model=str(model))


def active_model_hint(source_context: dict[str, Any] | None) -> str:
    """The model this turn is actually running on, as the runtime already recorded it.

    Read, never invented: when the runtime has not named a model the seat is declared
    `local/unknown` and Gate 0's provider check decides on that, honestly, rather than on a
    guess that would let an unavailable provider through.
    """

    context = dict(source_context or {})
    for key in ("model_name", "model", "active_model", "selected_model"):
        value = str(context.get(key) or "").strip()
        if value:
            return value
    return ""


def build_contract(
    *,
    task_id: str,
    repo_root: str,
    base_sha: str,
    objective: str,
    authority_owner: str,
    evidence_path: str,
    writable_scope: tuple[str, ...],
    mutating: bool,
    builder_model: str,
    reviewer_model: str = "",
    mutation_grant: Any | None = None,
    max_calls: int = DEFAULT_MAX_CALLS,
    max_cost_usd: float = DEFAULT_MAX_COST_USD,
):
    """The typed TASK_CONTRACT for one RepoOps session. Raises `TaskContractError` when invalid."""

    from core.council.cost_ladder import CostPolicy, SpendCeilings
    from core.council.task_contract import MutationPermission, TaskContract, TaskSeat

    builder = _model_identity(builder_model, fallback="local/unknown")
    reviewer = _model_identity(reviewer_model or builder_model, fallback="local/unknown")
    if mutating:
        # A repository REPAIR seats a writer and an independent reviewer. The contract refuses two
        # seats holding the same model identity, and that refusal is the point: a mutation reviewed
        # by the model that wrote it is not reviewed. When only one model is available the session
        # stays read-only and says so, rather than declaring an independence it does not have.
        seats = (
            TaskSeat(seat_id="repoops-builder", role_id="builder", model=builder),
            TaskSeat(seat_id="repoops-reviewer", role_id="reviewer", model=reviewer),
        )
    else:
        # Reading a repository seats one reviewer. There is no writer, so there is nothing for a
        # second family to be independent OF.
        seats = (TaskSeat(seat_id="repoops-reviewer", role_id="reviewer", model=reviewer),)
    policy = CostPolicy(
        ceilings=SpendCeilings(
            max_calls=int(max_calls),
            max_tokens=DEFAULT_MAX_TOKENS,
            max_cost_usd=float(max_cost_usd),
            wall_clock_seconds=DEFAULT_WALL_CLOCK_SECONDS,
        )
    )
    return TaskContract(
        task_id=task_id,
        repo_root=str(repo_root),
        base_sha=str(base_sha),
        objective=str(objective),
        authority_owner=str(authority_owner),
        done_condition=(
            "the diagnosed cause is repaired, the cumulative suite is green at the repaired SHA, "
            "the review is recorded, and the push is authorized, executed and verified against the remote"
        ),
        verification="the sealed receipt cites an executed test step and a remote SHA read back from the forge",
        counterexample=(
            "the remote ref does not resolve to the pushed SHA after the push, or a cited test "
            "result has no executed step behind it"
        ),
        evidence_path=str(evidence_path),
        cost_policy=policy,
        writable_scope=tuple(writable_scope) if mutating else (),
        mutation=MutationPermission.WORKSPACE_WRITE if mutating else MutationPermission.READ_ONLY,
        seats=seats,
        required_independent_reviewers=1,
    )


def observe_facts(
    *,
    repo_root: str,
    head_sha: str,
    dirty_paths: tuple[str, ...],
    authority_owner: str,
    contract: Any,
    mutation_grant: Any | None = None,
    spend_meter: Any | None = None,
) -> Any:
    """Gate0Facts observed from the machine, not asserted by a caller.

    `provider_availability` comes from the live circuit-breaker registry, so a provider whose
    circuit is open is genuinely unavailable here and the gate refuses before spending on it.
    """

    from core.council.cost_ladder import SpendMeter
    from core.council.gate0 import Gate0Facts
    from core.model_health import circuit_is_open

    providers: dict[str, bool] = {}
    roles: dict[str, frozenset[str]] = {}
    for seat in getattr(contract, "seats", ()):
        provider = str(seat.model.provider)
        try:
            providers[provider] = not bool(circuit_is_open(provider))
        except Exception:
            # An unreadable health registry is not evidence of health.
            providers[provider] = False
        roles.setdefault(seat.model.key, frozenset())
        roles[seat.model.key] = roles[seat.model.key] | {seat.role_id}

    evidence_writable = True
    try:
        target = Path(repo_root) / str(getattr(contract, "evidence_path", "") or ".")
        evidence_writable = os.access(str(target.parent if target.suffix else target), os.W_OK) or not target.exists()
    except Exception:
        evidence_writable = False

    return Gate0Facts(
        repo_root=str(repo_root),
        head_sha=str(head_sha),
        authority_owners=frozenset({str(authority_owner)}),
        spend_meter=spend_meter if spend_meter is not None else SpendMeter(ceilings=contract.cost_policy.ceilings),
        dirty_paths=frozenset(dirty_paths),
        provider_availability=providers,
        role_support=roles,
        evidence_root_writable=bool(evidence_writable),
        mutation_grants=tuple([mutation_grant]) if mutation_grant is not None else (),
    )


def evaluate(
    *,
    repo_root: str,
    head_sha: str,
    dirty_paths: tuple[str, ...],
    objective: str,
    authority_owner: str,
    writable_scope: tuple[str, ...],
    mutating: bool,
    builder_model: str,
    reviewer_model: str = "",
    evidence_path: str = "validation-logs/repoops",
    task_id: str = "",
    spend_meter: Any | None = None,
) -> GateOutcome:
    """Build the contract, observe the facts, and let the REAL Gate 0 decide. No model is called."""

    from core.council.gate0 import Gate0
    from core.council.task_contract import TaskContractError, mint_mutation_grant

    ident = str(task_id or "").strip() or f"repoops-{uuid.uuid4().hex[:12]}"
    grant = mint_mutation_grant(ident, granted_by=str(authority_owner)) if mutating else None
    try:
        contract = build_contract(
            task_id=ident,
            repo_root=repo_root,
            base_sha=head_sha,
            objective=objective,
            authority_owner=authority_owner,
            evidence_path=evidence_path,
            writable_scope=writable_scope,
            mutating=mutating,
            builder_model=builder_model,
            reviewer_model=reviewer_model,
        )
    except TaskContractError as exc:
        return GateOutcome(
            allowed=False,
            reason="invalid_task_contract",
            detail=str(exc),
            checks=(),
            task_id=ident,
            contract_error=type(exc).__name__,
        )
    facts = observe_facts(
        repo_root=repo_root,
        head_sha=head_sha,
        dirty_paths=dirty_paths,
        authority_owner=authority_owner,
        contract=contract,
        mutation_grant=grant,
        spend_meter=spend_meter,
    )
    decision = Gate0(facts).evaluate(contract)
    row = decision.as_row()
    return GateOutcome(
        allowed=bool(row.get("allowed")),
        reason=str(row.get("reason") or ""),
        detail=str(row.get("detail") or ""),
        checks=tuple(dict(c) for c in list(row.get("checks") or [])),
        task_id=ident,
    )


__all__ = ["GateOutcome", "active_model_hint", "build_contract", "evaluate", "observe_facts"]
