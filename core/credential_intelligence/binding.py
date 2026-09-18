"""Opaque credential bindings — what the rest of the product may ever see about a key.

A ``CredentialBinding`` exposes provider, account, capability, status and last-verified (plus
the ids/timestamps that make them addressable) — and NOTHING else: no secret, no key digest,
no masked suffix. The digest exists only INSIDE the index rows (staleness detection during
reconcile) and never crosses into a binding object or its ``to_dict``.

The index file (``credential_bindings.json`` in the active data dir) is the binding
authority's persistence. Malformed storage is handled HONESTLY: a corrupt file raises
``MalformedBindingIndexError`` — it is never silently treated as "no bindings", which would tell
the operator a working credential does not exist while its key sits in the Keychain.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

BINDING_INDEX_FILE = "credential_bindings.json"

STATUS_VERIFIED = "verified"
STATUS_UNVERIFIED = "unverified"
STATUS_INVALID = "invalid"
STATUS_EXHAUSTED = "exhausted"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_UNAUTHORIZED = "unauthorized"
STATUS_REVOKED = "revoked"
STATUS_MISSING = "missing"
#: A key stored by EXPLICIT operator choice before any verification succeeded. It sits in a
#: quarantine slot no execution consumer reads; the row exists so the operator can see, retry
#: and delete it. Promotion to a real slot happens only through a later verified outcome.
STATUS_QUARANTINED = "unverified_quarantined"

_KNOWN_STATUSES = {
    STATUS_VERIFIED, STATUS_UNVERIFIED, STATUS_INVALID, STATUS_EXHAUSTED,
    STATUS_RATE_LIMITED, STATUS_UNAUTHORIZED, STATUS_REVOKED, STATUS_MISSING,
    STATUS_QUARANTINED,
}

#: Fields a binding may ever expose. The index row carries a few more (digest, slot,
#: storage backend) — those are internal to the store and stripped on the way out.
EXPOSED_FIELDS = (
    "binding_id", "provider_id", "provider_label", "account",
    "capability_family", "status", "last_verified_at", "created_at",
)


class MalformedBindingIndexError(RuntimeError):
    """The binding index exists but cannot be parsed. Surfaced, never papered over."""


@dataclass(frozen=True)
class CredentialBinding:
    binding_id: str
    provider_id: str
    provider_label: str
    account: str
    capability_family: str
    status: str
    last_verified_at: str | None
    created_at: str

    def to_dict(self) -> dict[str, object]:
        return {
            "binding_id": self.binding_id,
            "provider_id": self.provider_id,
            "provider_label": self.provider_label,
            "account": self.account,
            "capability_family": self.capability_family,
            "status": self.status,
            "last_verified_at": self.last_verified_at,
            "created_at": self.created_at,
        }


def binding_id_for(provider_id: str) -> str:
    """Stable across replace and restart: one binding identity per provider slot."""
    return f"cb_{str(provider_id or '').strip().lower()}"


def binding_from_row(row: dict) -> CredentialBinding | None:
    """Project an index row to its opaque binding. A structurally invalid row returns None
    (the store reports it as malformed during reconcile instead of guessing fields)."""
    if not isinstance(row, dict):
        return None
    try:
        status = str(row.get("status") or "")
        if status not in _KNOWN_STATUSES:
            return None
        last_verified = row.get("last_verified_at")
        return CredentialBinding(
            binding_id=str(row["binding_id"]),
            provider_id=str(row["provider_id"]),
            provider_label=str(row.get("provider_label") or ""),
            account=str(row.get("account") or ""),
            capability_family=str(row.get("capability_family") or ""),
            status=status,
            last_verified_at=str(last_verified) if last_verified else None,
            created_at=str(row.get("created_at") or ""),
        )
    except (KeyError, TypeError):
        return None


def index_path() -> Path:
    from core.runtime_paths import active_data_dir

    return active_data_dir() / BINDING_INDEX_FILE


def load_index() -> dict[str, dict]:
    """Provider_id → row. Absent file → {}. Corrupt file → MalformedBindingIndexError (the honest
    refusal — an unreadable index must be DECIDED, not read as empty)."""
    path = index_path()
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MalformedBindingIndexError(f"credential binding index is unreadable: {exc}") from exc
    if not isinstance(raw, dict):
        raise MalformedBindingIndexError("credential binding index is not a mapping")
    return {str(pid): row for pid, row in raw.items() if isinstance(row, dict)}


def save_index(rows: dict[str, dict]) -> None:
    """Atomically and durably (mkstemp + fsync + os.replace). Journal intents are closed only after
    this commit (credential_intelligence.store), so it must survive a crash that already renamed it,
    exactly like the journal's own fsync'd writes (revision-5 review R3)."""
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(rows, sort_keys=True))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp)


def row_is_well_formed(row: dict) -> bool:
    return binding_from_row(row) is not None and bool(row.get("slot"))


__all__ = [
    "BINDING_INDEX_FILE",
    "EXPOSED_FIELDS",
    "CredentialBinding",
    "MalformedBindingIndexError",
    "binding_from_row",
    "binding_id_for",
    "index_path",
    "load_index",
    "row_is_well_formed",
    "save_index",
]
