"""Cost-tier escalation policy — paid models only on authorized escalation.

The council's cost law mirrors core/cloud_escalation_policy.py: cheap lanes
first; a PAID seat is invocable ONLY while the runtime holds an
:class:`AdjudicationAuthorization` issued by the gate for qualifying dispute
kinds, with a hard per-run budget.

The authorization object cannot be forged from outside this module (its
constructor is guarded by a private sentinel), so a seat or caller cannot
self-authorize an expensive call. The runtime refuses any PAID-tier invocation
without a live authorization covering that phase — the gate is checked at the
invocation boundary, not at planning time.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field

#: Dispute kinds that justify spending a paid adjudication call. Coverage gaps
#: are preference questions, never truth questions — they are settled by voter
#: majority (labelled as preference) and must NOT trigger paid escalation.
QUALIFYING_KINDS = frozenset({
    "factual", "causal", "causal_inversion", "causal_strength",
    "negation", "quantifier", "modality", "temporal", "conditional_scope",
    "value_mismatch",
})


class EscalationRefused(PermissionError):
    """A paid-tier invocation was attempted without gate authorization."""


class _GateSentinel:
    __slots__ = ()
    _instance: _GateSentinel | None = None

    def __new__(cls) -> _GateSentinel:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


_SENTINEL = _GateSentinel()


@dataclass(frozen=True)
class AdjudicationAuthorization:
    """Proof that policy authorizes paid adjudication for these kinds."""

    kinds: frozenset[str]
    token: str
    _sentinel: _GateSentinel = field(repr=False, compare=False, default=_SENTINEL)

    def __post_init__(self) -> None:
        # Forging attempt: constructing one directly outside the gate fails.
        if self._sentinel is not _SENTINEL:
            raise EscalationRefused("authorization objects can only be issued by EscalationGate")


@dataclass(frozen=True)
class EscalationPolicy:
    """User-declared escalation contract for one council."""

    #: dispute kinds that may escalate to the paid judge (default: every
    #: truth-bearing kind; coverage/preference never escalates)
    escalate_kinds: frozenset[str] = None  # type: ignore[assignment]
    #: max paid model invocations per council run (hard ceiling, counts judge
    #: batch calls under one authorization)
    paid_budget: int = 1
    #: disputes per adjudication call — bounded output, not a raised limit
    judge_batch_size: int = 3
    #: SEALED JUDGE LAW: the judge's raw output bytes are the candidate final
    #: answer, hashed and frozen exactly as produced. Downstream code may only
    #: validate/reject and attach metadata — never rewrite. When set, the judge
    #: ALWAYS runs (even under full agreement) so the final bytes always have a
    #: single semantic author.
    sealed_judge: bool = False
    #: if True, the final judge runs even under full agreement (audit mode)
    always_adjudicate: bool = False

    def __post_init__(self) -> None:
        if self.escalate_kinds is None:
            # frozen default = all truth-bearing kinds
            object.__setattr__(self, "escalate_kinds", frozenset(QUALIFYING_KINDS))
        unknown = set(self.escalate_kinds) - QUALIFYING_KINDS
        if unknown:
            raise ValueError(f"unknown escalate kinds: {sorted(unknown)}")


class EscalationGate:
    """Issues paid-invocation authorizations against policy and budget."""

    def __init__(self, policy: EscalationPolicy) -> None:
        self._policy = policy
        self._spent = 0
        self.last_authorized_calls = 0

    @property
    def spent(self) -> int:
        return self._spent

    def needs_authorization(self, tiers: set) -> bool:
        from core.council.seats import Tier

        return Tier.PAID in tiers

    def request(self, dispute_kinds: set[str], *, calls: int = 1) -> AdjudicationAuthorization:
        """Authorize a bounded adjudication session (one or more judge calls).

        The authorization covers ``min(calls, remaining budget)`` calls; the
        caller learns how many via :attr:`last_authorized_calls`. Budget buys
        CALLS, never outcomes — disputes beyond the paid batches stay unresolved.
        """
        qualifying = {k for k in dispute_kinds if k in QUALIFYING_KINDS}
        effective = qualifying & set(self._policy.escalate_kinds)
        if not effective:
            raise EscalationRefused(
                f"no qualifying dispute kind survives ({sorted(dispute_kinds)}); "
                f"policy escalates only {sorted(self._policy.escalate_kinds)}"
            )
        affordable = max(0, min(int(calls), self._policy.paid_budget - self._spent))
        if affordable == 0:
            raise EscalationRefused(
                f"paid budget exhausted ({self._spent}/{self._policy.paid_budget})"
            )
        self._spent += affordable
        self.last_authorized_calls = affordable
        return AdjudicationAuthorization(kinds=frozenset(effective), token=uuid.uuid4().hex)


__all__ = [
    "QUALIFYING_KINDS",
    "AdjudicationAuthorization",
    "EscalationGate",
    "EscalationPolicy",
    "EscalationRefused",
]
