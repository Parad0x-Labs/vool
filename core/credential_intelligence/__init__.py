"""Credential intelligence without secret exposure (P0, 2026-09-02).

Public surface, one line each:

* :func:`core.credential_intelligence.format_classifier.classify_format` — pure local
  classification of a pasted key (no network, no model, no store).
* :func:`core.credential_intelligence.shortlist.build_shortlist` — the likely-provider
  shortlist the operator chooses from; ambiguous stays ambiguous.
* :class:`core.credential_intelligence.provider_registry.ProviderRegistry` — the typed
  provider table (pinned verification endpoints) the whole flow is injected with;
  :func:`default_registry` derives it from the existing cloud/search provider tables.
* :func:`core.credential_intelligence.verification.verify_provider_credential` — verify the
  key against EXACTLY the selected provider; five distinct failure truths; env-immune.
* :class:`core.credential_intelligence.binding.CredentialBinding` — provider / account /
  capability / status / last-verified, and nothing else.
* :class:`core.credential_intelligence.store.CredentialStore` — verified-only persistence
  over the existing Keychain/vault backend, with intent journal, explicit
  replace/delete/revoke, and shadow/duplicate-free reconciliation.
* :func:`core.credential_intelligence.availability.apply_verification` — capability
  availability from verified evidence only; never flips paid-fallback preferences.
* :class:`core.credential_intelligence.intake.CredentialIntake` — the flow state machine.
"""
from core.credential_intelligence.availability import apply_verification, provider_availability
from core.credential_intelligence.binding import (
    CredentialBinding,
    MalformedBindingIndexError,
    binding_from_row,
)
from core.credential_intelligence.format_classifier import KeyFormat, classify_format
from core.credential_intelligence.intake import (
    CredentialIntake,
    IntakeReceipt,
    IntakeResult,
    Selection,
    StageError,
    UnknownProviderError,
)
from core.credential_intelligence.provider_registry import (
    ProviderDescriptor,
    ProviderRegistry,
    default_registry,
)
from core.credential_intelligence.shortlist import ProviderShortlist, ShortlistEntry, build_shortlist
from core.credential_intelligence.store import (
    CredentialStore,
    DeleteResult,
    IntakeRefusedError,
    ReconcileReport,
    StorageConflictError,
    StorageUnavailableError,
    StoreWriteTimeoutError,
)
from core.credential_intelligence.verification import (
    STATUS_EXHAUSTED,
    STATUS_INVALID,
    STATUS_NETWORK_UNAVAILABLE,
    STATUS_RATE_LIMITED,
    STATUS_REFUSED,
    STATUS_UNAUTHORIZED,
    STATUS_VERIFIED,
    VerificationOutcome,
    verify_provider_credential,
)

__all__ = [
    "STATUS_EXHAUSTED",
    "STATUS_INVALID",
    "STATUS_NETWORK_UNAVAILABLE",
    "STATUS_RATE_LIMITED",
    "STATUS_REFUSED",
    "STATUS_UNAUTHORIZED",
    "STATUS_VERIFIED",
    "CredentialBinding",
    "CredentialIntake",
    "CredentialStore",
    "DeleteResult",
    "IntakeReceipt",
    "IntakeRefusedError",
    "IntakeResult",
    "KeyFormat",
    "MalformedBindingIndexError",
    "ProviderDescriptor",
    "ProviderRegistry",
    "ProviderShortlist",
    "ReconcileReport",
    "Selection",
    "ShortlistEntry",
    "StageError",
    "StorageConflictError",
    "StorageUnavailableError",
    "StoreWriteTimeoutError",
    "UnknownProviderError",
    "VerificationOutcome",
    "apply_verification",
    "binding_from_row",
    "build_shortlist",
    "classify_format",
    "default_registry",
    "provider_availability",
    "verify_provider_credential",
]
