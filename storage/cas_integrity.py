"""Typed read outcomes and verified I/O for the content-addressed stores.

Scope: this is the vocabulary of ``storage.chunk_store``, ``storage.cas`` and
``core.liquefy_cas`` only. It is not a runtime-wide fault catalog.

Contract
--------
* The canonical digest is lowercase hex sha256 of the raw bytes. An address is
  valid only when it is exactly that shape.
* Every read recomputes the digest of what is on disk and compares it to the
  address before any bytes leave this module. A mismatch never returns bytes.
* ``CasMissingError`` (object absent) is distinct from ``CasCorruptionError``
  (object present but wrong: digest mismatch, truncated, malformed manifest,
  malformed address). Both derive from ``CasIntegrityError`` so a caller that
  wants the whole family can catch one type. None of them carry bytes.
* Objects are published with write-to-temp + ``os.replace`` so a concurrent
  reader sees either nothing or the complete object, never a prefix.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import uuid
from pathlib import Path

CANONICAL_DIGEST = "sha256"
_ADDRESS = re.compile(r"^[0-9a-f]{64}$")


class CasIntegrityError(Exception):
    """Base of every typed CAS read outcome. Never carries content bytes."""

    def __init__(
        self,
        address: object,
        *,
        reason: str,
        expected: str | None = None,
        observed: str | None = None,
        detail: str = "",
    ) -> None:
        self.address = str(address)
        self.reason = reason
        self.expected = expected
        self.observed = observed
        self.detail = detail
        shown = self.address if len(self.address) <= 16 else f"{self.address[:12]}..."
        message = f"cas {reason}: {shown!r}"
        if expected and observed:
            message += f" (expected {expected[:12]}..., observed {observed[:12]}...)"
        if detail:
            message += f" - {detail}"
        super().__init__(message)


class CasCorruptionError(CasIntegrityError):
    """The object is present but is not what its address says it is."""


class CasMissingError(CasIntegrityError):
    """The object is absent. Distinct from corruption by construction."""


def canonical_digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_address(address: object) -> str:
    """Return ``address`` when it is a canonical digest; refuse anything else.

    Used for addresses that live inside stored content (a manifest's chunk
    list, a declared file digest): a malformed one means the stored object is
    malformed, so this is a corruption outcome. It is never joined into a
    filesystem path.
    """
    if not isinstance(address, str) or not _ADDRESS.match(address):
        raise CasCorruptionError(address, reason="malformed_address")
    return address


def resolve_address(address: object) -> str:
    """Return a caller-supplied ``address`` when it is a canonical digest.

    A reference that is not a digest cannot resolve to any object, so this is
    the missing outcome (``CasMissingError``, reason ``malformed_address``):
    nothing on disk is touched and no bytes are returned. Callers using the
    lenient readers see ``None``, exactly as they would for an unknown digest.
    """
    if not isinstance(address, str) or not _ADDRESS.match(address):
        raise CasMissingError(address, reason="malformed_address")
    return address


def verified_read(path: Path, address: str) -> bytes:
    """Read ``path`` and return its bytes only if they hash to ``address``."""
    address = validate_address(address)
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise CasMissingError(address, reason="missing") from exc
    except OSError as exc:
        # Unreadable is unverifiable; unverifiable is fail-closed.
        raise CasCorruptionError(address, reason="unreadable", detail=str(exc)) from exc
    observed = canonical_digest(data)
    if observed != address:
        raise CasCorruptionError(
            address, reason="digest_mismatch", expected=address, observed=observed
        )
    return data


def atomic_publish(path: Path, data: bytes) -> None:
    """Publish ``data`` at ``path`` so no reader ever observes a partial object.

    Bytes go to a sibling temp file first and are moved into place with
    ``os.replace``; a crash before the rename leaves nothing under the address
    and no temp residue. An existing object at ``path`` is re-verified: if it
    already matches it is left alone (deduplication), if it does not match the
    correct bytes replace it, since the caller holds content that hashes to the
    address by construction.
    """
    address = validate_address(path.name)
    if canonical_digest(data) != address:
        raise CasCorruptionError(address, reason="publish_digest_mismatch")
    if path.exists():
        try:
            if canonical_digest(path.read_bytes()) == address:
                return
        except OSError:
            pass
    tmp = path.parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    try:
        tmp.write_bytes(data)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise
