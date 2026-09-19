"""The one seam every contact import source implements (Apple Contacts, Google Contacts, Microsoft Outlook).

An adapter only READS what its source shows and says, in typed terms, why it could not. It never writes to the source,
never decides what is saved (core.contacts.importing previews, the owner selects), and never marks anything verified:
imported entries are ``imported_unverified`` with their source and account.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

#: typed reasons an adapter can give, each with the owner's next step
REASON_PERMISSION_NOT_DETERMINED = "permission_not_determined"
REASON_PERMISSION_DENIED = "permission_denied"
REASON_PERMISSION_RESTRICTED = "permission_restricted"
REASON_OS_BINDING_UNAVAILABLE = "os_binding_unavailable"
REASON_NEEDS_RECONNECT = "needs_reconnect"
REASON_SCOPE_MISSING = "scope_missing"
REASON_RATE_LIMITED = "rate_limited"
REASON_PROVIDER_UNAVAILABLE = "provider_unavailable"
REASON_READ_FAILED = "read_failed"
REASON_BINDING_REQUIRED = "credential_binding_required"

RECOVERY: dict[str, str] = {
    REASON_PERMISSION_NOT_DETERMINED: "macOS has not been asked yet. Choose Import again and answer the Contacts permission prompt.",
    REASON_PERMISSION_DENIED: "Contacts access was declined. Allow it for VOOL in System Settings > Privacy & Security > Contacts, then try again.",
    REASON_PERMISSION_RESTRICTED: "A device policy restricts Contacts access on this Mac; it cannot be granted from VOOL.",
    REASON_OS_BINDING_UNAVAILABLE: "This build has no macOS Contacts bridge. The packaged app carries it; saved contacts and other imports still work.",
    REASON_NEEDS_RECONNECT: "The account's sign-in expired or was revoked. Reconnect the account's credential, then preview again.",
    REASON_SCOPE_MISSING: "The account's credential does not include read access to contacts. Reconnect it with contacts read permission.",
    REASON_RATE_LIMITED: "The provider asked VOOL to slow down. Try the preview again later.",
    REASON_PROVIDER_UNAVAILABLE: "The provider could not be reached. Nothing was imported; try again when it is reachable.",
    REASON_READ_FAILED: "The provider's answer could not be read. Nothing was imported.",
    REASON_BINDING_REQUIRED: "Choose the account's stored credential first; VOOL never asks for a password here.",
}


class ImportSourceError(Exception):
    """Why a source could not be read. ``reason`` is one of the REASON_* values."""

    def __init__(self, reason: str, detail: str = "", *, retry_after: int | None = None, partial: tuple[ImportedPerson, ...] = ()) -> None:
        super().__init__(detail or reason)
        self.reason = reason
        self.detail = detail
        self.retry_after = retry_after
        self.partial = partial

    @property
    def recovery(self) -> str:
        return RECOVERY.get(self.reason, RECOVERY[REASON_READ_FAILED])

    def as_dict(self) -> dict[str, Any]:
        out = {"reason": self.reason, "detail": self.detail[:300], "recovery": self.recovery}
        if self.retry_after is not None:
            out["retry_after"] = int(self.retry_after)
        return out


@dataclass(frozen=True)
class ImportedEntry:
    """One entry as the source shows it; core.contacts validates it before anything is proposed."""

    kind: str
    value: str
    label: str = ""
    channel: str = ""
    provider_account: str = ""


@dataclass(frozen=True)
class ImportedPerson:
    source_ref: str
    display_name: str
    entries: tuple[ImportedEntry, ...] = ()
    organization: str = ""


@dataclass(frozen=True)
class SourceAccount:
    """Which account of the provider is read: its label, the identity it names, and the credential binding id (never a secret)."""

    provider: str
    account_label: str = ""
    account_identity: str = ""
    auth_binding: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


class ContactSourceAdapter(Protocol):
    provider: str
    label: str
    needs_binding: bool

    def authorization(self, account: SourceAccount) -> dict[str, Any]:
        """{"state": authorized|not_determined|denied|restricted|unavailable|ready, "recovery": str} without reading contacts."""

    def read(self, account: SourceAccount, *, limit: int) -> tuple[ImportedPerson, ...]:
        """Every contact the source shows, up to ``limit``; raises ImportSourceError."""


__all__ = [
    "REASON_BINDING_REQUIRED",
    "REASON_NEEDS_RECONNECT",
    "REASON_OS_BINDING_UNAVAILABLE",
    "REASON_PERMISSION_DENIED",
    "REASON_PERMISSION_NOT_DETERMINED",
    "REASON_PERMISSION_RESTRICTED",
    "REASON_PROVIDER_UNAVAILABLE",
    "REASON_RATE_LIMITED",
    "REASON_READ_FAILED",
    "REASON_SCOPE_MISSING",
    "RECOVERY",
    "ContactSourceAdapter",
    "ImportSourceError",
    "ImportedEntry",
    "ImportedPerson",
    "SourceAccount",
]
