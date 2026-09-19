"""Platform execution authority — the ONE admission/idempotency/UNKNOWN/receipt seam.

This is the composite adjudicated from the two forge experiments: the broker
owns everything that is NOT repository-specific (admission through kernel Law 3,
idempotency, UNKNOWN truth, journal recording, reconciliation rules), while
domains supply only semantics and a single-use dispatch callable.

Adjudication deltas vs the source design:
- duplicate retries of an APPLIED key REPLAY prior evidence (replayed=True)
  instead of returning "refused" — honest and usable;
- revocation lives here: RolePolicy mints forks, PlatformRevocations records
  mid-turn revocation effective at the next mint.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from core.kernel.capabilities import CapabilitySet, ForkContext, check_tool_call
from core.kernel.effects import EffectJournal, EffectOutcomeUnknown, EffectRunner

EFFECT_STATUSES = ("applied", "refused", "failed", "unknown")

#: Closed translation of DOMAIN result vocabulary -> the ONE canonical effect
#: status set. Domains may speak their own dialect INSIDE their adapters; the
#: moment a result becomes platform effect truth it must be one of
#: EFFECT_STATUSES. Unknown tokens RAISE here — at construction, before any
#: effect truth is committed — instead of being free-form translated later.
_DOMAIN_STATUS_MAP: dict[str, str] = {
    # applied
    "applied": "applied", "succeeded": "applied", "success": "applied",
    "ok": "applied", "done": "applied", "merged": "applied",
    "created": "applied", "pushed": "applied",
    # refused (definitive NO before/at admission — world unchanged)
    "refused": "refused", "rejected": "refused", "not_applied": "refused",
    "denied": "refused",
    # failed (attempted, definitive no-effect answer from the provider)
    "failed": "failed", "error": "failed",
    # unknown (dispatched, outcome unknowable)
    "unknown": "unknown", "timeout": "unknown", "disconnected": "unknown",
}


def canonical_effect_status(domain_token: str) -> str:
    """Map a domain status token to the canonical set; raise on anything else."""
    token = str(domain_token).strip().lower()
    if token not in _DOMAIN_STATUS_MAP:
        raise UntranslatableEffectStatus(
            f"{domain_token!r} is not in the closed domain-status map "
            f"({sorted(_DOMAIN_STATUS_MAP)}); refusing to guess effect truth"
        )
    return _DOMAIN_STATUS_MAP[token]


class UntranslatableEffectStatus(ValueError):
    """A domain result token has no canonical meaning — fail loudly, never guess."""


@dataclass(frozen=True)
class EffectRequest:
    effect_id: str                 # namespaced typed operation, e.g. 'forge.branch.push'
    required_capability: str       # exact token the calling fork must hold
    params: dict                   # JSON-shaped args (taint-walked by the kernel)
    idempotency_key: str


@dataclass(frozen=True)
class EffectOutcome:
    status: str                    # applied / refused / failed / unknown — VALIDATED
    reason: str = ""
    evidence: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        # Mechanical block: an out-of-vocabulary status cannot be CONSTRUCTED,
        # so a domain adapter fails at its own boundary, not at commit time.
        if self.status not in EFFECT_STATUSES:
            raise UntranslatableEffectStatus(
                f"EffectOutcome.status must be one of {EFFECT_STATUSES}, "
                f"got {self.status!r} — use canonical_effect_status() to translate"
            )

    @classmethod
    def from_domain(cls, domain_token: str, *, reason: str = "",
                    evidence: dict | None = None) -> EffectOutcome:
        """The ONLY sanctioned way a domain dialect becomes platform truth."""
        return cls(status=canonical_effect_status(domain_token),
                   reason=reason, evidence=dict(evidence or {}))


@dataclass(frozen=True)
class GenericReceipt:
    """The authoritative record of one admitted attempt (or its refusal)."""

    idempotency_key: str
    effect_id: str
    status: str
    reason: str
    evidence: dict
    authorization_receipt: dict[str, str] | None = None
    replayed: bool = False

    def __post_init__(self) -> None:
        if self.status not in EFFECT_STATUSES:
            raise ValueError(f"receipt status must be one of {EFFECT_STATUSES}, got {self.status!r}")
        if self.status == "applied" and not self.evidence:
            raise ValueError("an applied receipt with no evidence proves nothing and is refused")

    @property
    def happened(self) -> bool:
        return self.status == "applied"


class ExecutionBroker:
    """Single authority for admission, dispatch, idempotency, UNKNOWN, receipts."""

    def __init__(self) -> None:
        self._runner = EffectRunner(mode="record")
        self._receipt_keys: dict[str, GenericReceipt] = {}
        self._pending_unknown: set[str] = set()

    @property
    def journal(self) -> EffectJournal:
        return self._runner.journal

    def receipt_for(self, key: str) -> GenericReceipt | None:
        return self._receipt_keys.get(key)

    def all_receipts(self) -> tuple[GenericReceipt, ...]:
        return tuple(self._receipt_keys.values())

    def execute(
        self,
        fork: ForkContext,
        request: EffectRequest,
        dispatch: Callable[[], EffectOutcome],
    ) -> GenericReceipt:
        """Admit, dispatch and record one effect attempt. Dispatch runs at most once."""
        key = request.idempotency_key
        prior = self._receipt_keys.get(key)
        if prior is not None:
            # Adjudicated semantic: an already-APPLIED key replays its evidence;
            # an unresolved UNKNOWN refuses until reconciled. Neither re-dispatches.
            if prior.status == "applied":
                return self._record(GenericReceipt(
                    idempotency_key=key, effect_id=request.effect_id,
                    status="applied", reason="duplicate_retry_replayed",
                    evidence=dict(prior.evidence),
                    authorization_receipt=prior.authorization_receipt, replayed=True,
                ))
            if prior.status == "unknown":
                return self._record(GenericReceipt(
                    idempotency_key=key, effect_id=request.effect_id,
                    status="refused", reason=f"unreconciled_unknown_key:{key}",
                    evidence={}, replayed=False,
                ))

        try:
            auth = check_tool_call(
                fork,
                tool_name=request.effect_id,
                required=request.required_capability,
                args=request.params,
            )
        except Exception as exc:
            row = getattr(exc, "receipt", {})
            return self._record(GenericReceipt(
                idempotency_key=key, effect_id=request.effect_id,
                status="refused", reason=str(getattr(exc, "reason", "") or exc),
                evidence={"detail": str(row.get("detail", ""))},
                authorization_receipt=row or None,
            ))

        def _dispatch_once() -> dict:
            # The dispatch callable has ALREADY done its real-world work when it
            # returns or throws past its own boundary. From this point on, any
            # failure to INTERPRET the result is ambiguity, not refutation: we
            # can no longer claim applied NOR not-applied, so the only honest
            # status is UNKNOWN (kernel Law 4 inv 11). This is the structural
            # fix for the "real mutation + translation crash -> recorded FAILED"
            # bug found in pass #1.
            try:
                outcome = dispatch()
                if outcome.status == "unknown":
                    raise EffectOutcomeUnknown(outcome.reason, "dispatch_outcome_unknowable")
                return {"status": outcome.status, "reason": outcome.reason,
                        "evidence": dict(outcome.evidence)}
            except EffectOutcomeUnknown:
                raise
            except Exception as exc:
                raise EffectOutcomeUnknown(
                    f"dispatch did not report an interpretable outcome: {exc}",
                    "outcome_uninterpretable_after_dispatch",
                ) from exc

        try:
            recorded = self._runner.run(request.effect_id, _dispatch_once)
        except EffectOutcomeUnknown as exc:
            self._pending_unknown.add(key)
            return self._record(GenericReceipt(
                idempotency_key=key, effect_id=request.effect_id,
                status="unknown", reason=exc.reason,
                evidence={"detail": exc.detail}, authorization_receipt=auth,
            ))
        except Exception as exc:
            return self._record(GenericReceipt(
                idempotency_key=key, effect_id=request.effect_id,
                status="failed", reason=str(exc), evidence={},
                authorization_receipt=auth,
            ))
        return self._record(GenericReceipt(
            idempotency_key=key, effect_id=request.effect_id,
            status=str(recorded["status"]), reason=str(recorded["reason"]),
            evidence=dict(recorded["evidence"]), authorization_receipt=auth,
        ))

    def reconcile(self, key: str, resolve: Callable[[], EffectOutcome]) -> GenericReceipt:
        """Resolve a pending UNKNOWN via a domain-supplied inspector (never re-dispatch)."""
        if key not in self._pending_unknown:
            raise ValueError(f"no unknown-outcome receipt for key {key!r}")
        outcome = resolve()
        self._pending_unknown.discard(key)
        prior = self._receipt_keys[key]
        return self._record(GenericReceipt(
            idempotency_key=key, effect_id=prior.effect_id,
            status=outcome.status,
            reason=outcome.reason or "resolved_by_reconciliation",
            evidence=dict(outcome.evidence),
            authorization_receipt=prior.authorization_receipt,
        ))

    def _record(self, receipt: GenericReceipt) -> GenericReceipt:
        self._receipt_keys[receipt.idempotency_key] = receipt
        return receipt


class PlatformRevocations:
    """Mid-turn revocation: replaces the context at the next mint (kernel Law 3)."""

    def __init__(self) -> None:
        self._revoked: dict[str, frozenset[str]] = {}

    def revoke(self, fork_id: str, token: str) -> None:
        held = self._revoked.setdefault(fork_id, frozenset())
        self._revoked[fork_id] = held | {token}

    def revoked_for(self, fork_id: str) -> frozenset[str]:
        return self._revoked.get(fork_id, frozenset())

    def mint(self, fork_id: str, tokens) -> ForkContext:
        """tokens: any iterable of capability token strings."""
        revoked = self.revoked_for(fork_id)
        return ForkContext(fork_id=fork_id, caps=CapabilitySet(
            t for t in tokens if t not in revoked
        ))


__all__ = [
    "EFFECT_STATUSES", "EffectOutcome", "EffectRequest", "ExecutionBroker",
    "GenericReceipt", "PlatformRevocations", "UntranslatableEffectStatus",
    "canonical_effect_status",
]
