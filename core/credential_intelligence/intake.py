"""The credential intake flow — the ONE state machine a pasted key travels through.

    paste key → local-only classification → likely-provider shortlist → operator selects ONE
    provider → verify ONLY that provider → secure persistence → opaque CredentialBinding →
    provider/capability availability update.

Laws this module enforces end-to-end:

* **No network before selection.** ``paste`` classifies and shortlists; it cannot send
  anything anywhere (the classifier/shortlist are pure, and this module adds no transport).
* **One provider, ever.** ``verify`` consults exactly the selected descriptor; the binding
  step refuses an outcome whose provider is not the operator's selection, so a forged or
  rerouted outcome cannot persist against a different provider.
* **Verified or nothing.** ``complete`` persists only a verified outcome; every distinct bad
  outcome (invalid / exhausted / rate-limited / unauthorized / network-unavailable) raises a
  typed refusal with that status and leaves the store untouched.
* **No secret exposure.** The exact pasted value is registered with the redaction authority
  the moment it is held (so every downstream persistence surface scrubs it), it is dropped
  from the session once persisted, and the session/receipt never embed it — the sabotage test
  proves the receipt stays clean even with the scrubber disabled.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from core.credential_intelligence.availability import apply_verification, provider_availability
from core.credential_intelligence.binding import CredentialBinding
from core.credential_intelligence.format_classifier import classify_format
from core.credential_intelligence.provider_registry import ProviderDescriptor, ProviderRegistry
from core.credential_intelligence.shortlist import ProviderShortlist, build_shortlist
from core.credential_intelligence.store import CredentialStore, IntakeRefusedError
from core.credential_intelligence.verification import (
    VerificationOutcome,
    verify_provider_credential,
)

STAGE_NEW = "new"
STAGE_PASTED = "pasted"
STAGE_SELECTED = "selected"
STAGE_VERIFIED = "verified"
STAGE_DONE = "done"


class StageError(RuntimeError):
    """The flow was driven out of order (verify before select, complete before verify…)."""


class UnknownProviderError(RuntimeError):
    """The operator named a provider this registry does not know."""


@dataclass(frozen=True)
class Selection:
    provider_id: str
    label: str
    #: True when the operator picked a provider the shortlist did NOT offer — allowed (the
    #: operator knows their key), but recorded as their explicit override, never as a guess.
    override: bool


@dataclass(frozen=True)
class IntakeReceipt:
    """Non-secret trace of one intake: stage, provider, outcome status, timestamps."""
    phases: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, object]:
        return {"phases": [dict(p) for p in self.phases]}


@dataclass(frozen=True)
class IntakeResult:
    binding: CredentialBinding
    availability: dict[str, dict[str, object]]
    receipt: IntakeReceipt


def _utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class CredentialIntake:
    def __init__(self, *, registry: ProviderRegistry, store: CredentialStore | None = None,
                 verifier: Callable[..., VerificationOutcome] = verify_provider_credential):
        self._registry = registry
        self._store = store or CredentialStore(registry)
        self._verifier = verifier
        self._stage = STAGE_NEW
        self._shortlist: ProviderShortlist | None = None
        self._secret = ""
        self._selection: Selection | None = None
        self._descriptor: ProviderDescriptor | None = None
        self._outcome: VerificationOutcome | None = None
        self._phases: list[dict[str, str]] = []

    # ------------------------------------------------------------------ stages

    @property
    def stage(self) -> str:
        return self._stage

    def paste(self, secret: str) -> ProviderShortlist:
        """Classify a pasted key locally and return its provider shortlist. Registers the
        exact value with the redaction authority; sends nothing anywhere."""
        from core.secret_redaction import register_exact_secret

        self._secret = str(secret or "").strip()
        fmt = classify_format(self._secret)
        self._shortlist = build_shortlist(fmt, self._registry)
        register_exact_secret(self._secret)
        self._stage = STAGE_PASTED
        self._phases.append({"stage": STAGE_PASTED, "ts": _utcnow(),
                             "unrecognized": str(self._shortlist.unrecognized).lower()})
        return self._shortlist

    def select_provider(self, provider_id: str) -> Selection:
        if self._stage != STAGE_PASTED:
            raise StageError(f"select_provider requires a pasted key (stage is {self._stage!r})")
        descriptor = self._registry.get(provider_id)
        if descriptor is None:
            raise UnknownProviderError(f"{provider_id!r} is not in the provider registry")
        offered = self._shortlist is not None and any(
            e.provider_id == descriptor.provider_id for e in self._shortlist.entries
        )
        self._descriptor = descriptor
        self._selection = Selection(descriptor.provider_id, descriptor.label, override=not offered)
        self._stage = STAGE_SELECTED
        self._phases.append({"stage": STAGE_SELECTED, "provider": descriptor.provider_id,
                             "override": str(self._selection.override).lower(), "ts": _utcnow()})
        return self._selection

    def verify(self) -> VerificationOutcome:
        if self._stage != STAGE_SELECTED or self._descriptor is None:
            raise StageError(f"verify requires an operator selection (stage is {self._stage!r})")
        outcome = self._verifier(self._secret, self._descriptor)
        self._outcome = outcome
        self._stage = STAGE_VERIFIED
        self._phases.append({"stage": STAGE_VERIFIED, "provider": outcome.provider_id,
                             "status": outcome.status, "ts": _utcnow()})
        return outcome

    def complete(self) -> IntakeResult:
        """Persist the verified key, bind it, and update availability from the evidence."""
        if self._stage != STAGE_VERIFIED or self._descriptor is None or self._outcome is None:
            raise StageError(f"complete requires a completed verification (stage is {self._stage!r})")
        binding = self._persist_outcome(self._outcome)
        availability_entry = apply_verification(self._descriptor, self._outcome)
        availability = provider_availability(self._store.bindings())
        availability[self._descriptor.provider_id] = {
            "available": availability_entry["available"],
            "capability_family": availability_entry["capability_family"],
            "status": availability_entry["status"],
            "last_verified_at": availability_entry["last_verified_at"],
        }
        self._stage = STAGE_DONE
        self._phases.append({"stage": STAGE_DONE, "provider": binding.provider_id,
                             "binding": binding.binding_id, "ts": _utcnow()})
        self._secret = ""  # the session no longer needs the raw value; drop it
        return IntakeResult(binding, availability, self.receipt())

    # ------------------------------------------------------------------ guarded seams

    def _persist_outcome(self, outcome: VerificationOutcome) -> CredentialBinding:
        """The seam a saboteur targets: bind the key to whatever provider an outcome claims.
        Refused — the binding is to the OPERATOR'S SELECTION, and the selection only."""
        if self._descriptor is None or self._selection is None:
            raise StageError("no selection to bind against")
        if outcome.provider_id != self._selection.provider_id:
            raise IntakeRefusedError(
                f"outcome claims provider {outcome.provider_id!r} but the operator selected "
                f"{self._selection.provider_id!r}; refusing to bind",
                status="provider_mismatch",
            )
        if outcome.status != "verified":
            raise IntakeRefusedError(
                f"verification ended {outcome.status!r}; nothing persisted", status=outcome.status,
            )
        return self._store.save_verified(self._descriptor, self._secret, outcome)

    # ------------------------------------------------------------------ observability

    def receipt(self) -> IntakeReceipt:
        return IntakeReceipt(phases=tuple(dict(p) for p in self._phases))

    def session_repr_safe(self) -> bool:
        """True when neither the session object nor its receipt embeds the raw secret."""
        import json

        if not self._secret:
            return True
        return (
            self._secret not in repr(self)
            and self._secret not in str(self)
            and self._secret not in json.dumps(self.receipt().to_dict())
        )


__all__ = [
    "CredentialIntake",
    "IntakeReceipt",
    "IntakeResult",
    "Selection",
    "StageError",
    "UnknownProviderError",
]
