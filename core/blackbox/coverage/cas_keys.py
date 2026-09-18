"""The Blackbox CAS key authority: versioned, rotatable keys acquired through the canonical
secret-storage authority -- never a file beside the ciphertext, never plaintext fallbacks.

Resolution order:

1. ``VOOL_BLACKBOX_CAS_KEYS_FILE`` -- an explicit keyring JSON for tests and dev harnesses.
   Still a REAL keyring (same schema, same crypto); it is a key SOURCE, not a weaker cipher.
2. The canonical secret storage (``core.credential_store``) under the name
   ``blackbox.cas.keys``. On first use the keyring is MINTED there and read back; if the
   authority cannot store it, resolution answers UNAVAILABLE and every effect that needs the
   CAS fails closed -- there is no plaintext mode to fall back to.

Keyring schema (JSON at rest inside the secret authority; never on the CAS filesystem)::

    {"id_key": "<64 hex>", "versions": {"1": "<64 hex>"}, "current": "1"}

- ``versions``/``current``: the encryption keys (AES-256-GCM) and the live version. Rotation
  adds a new version and bumps ``current``; an old version is dropped only once nothing it
  sealed is still wanted (see ``drop_version``).
- ``id_key``: the long-lived NAMING key. Blob filenames are ``HMAC(id_key, sha256(plaintext))``
  so equal plaintext still deduplicates while the raw digest never appears on disk. It is
  deliberately NOT versioned: rotating it would orphan every journal reference ever written;
  encrypting it would be circular. It names; it does not decrypt.

Nothing here ever prints, logs, or journals key material or raw plaintext digests.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
from dataclasses import dataclass
from pathlib import Path
from typing import Any

KEYRING_CREDENTIAL_NAME = "blackbox.cas.keys"
KEYS_FILE_ENV = "VOOL_BLACKBOX_CAS_KEYS_FILE"

_ID_PURPOSE = b"blackbox-cas-id-v2"
_SCHEMA_KEYS = ("id_key", "versions", "current")


class CasKeyError(RuntimeError):
    """The CAS keyring is absent, unreadable, or malformed. Never carries key material."""


@dataclass(frozen=True)
class CasKeyring:
    id_key: bytes
    versions: dict[str, bytes]
    current: str

    def __repr__(self) -> str:  # evidence safety: never the material
        return f"<CasKeyring current={self.current!r} versions={sorted(self.versions)}>"

    # -- naming ------------------------------------------------------------------------------
    def id_for(self, raw_sha256: str) -> str:
        """The opaque, version-independent blob id for a content digest (in memory only)."""
        return hmac.new(self.id_key, str(raw_sha256).lower().encode("ascii") + _ID_PURPOSE, hashlib.sha256).hexdigest()

    # -- encryption --------------------------------------------------------------------------
    def key_for(self, version: str | int) -> bytes:
        material = self.versions.get(str(version))
        if material is None:
            raise CasKeyError(f"CAS key version {version} is not in this keyring (rotated away?)")
        return material

    @property
    def current_key(self) -> bytes:
        return self.key_for(self.current)

    # -- persistence -------------------------------------------------------------------------
    def to_json(self) -> str:
        return json.dumps(
            {
                "id_key": self.id_key.hex(),
                "versions": {version: material.hex() for version, material in self.versions.items()},
                "current": str(self.current),
            },
            sort_keys=True,
        )

    @classmethod
    def from_json(cls, payload: str | None) -> CasKeyring:
        try:
            data = json.loads(str(payload or ""))
        except ValueError as exc:
            raise CasKeyError("the CAS keyring payload is not valid JSON") from exc
        if not isinstance(data, dict) or any(key not in data for key in _SCHEMA_KEYS):
            raise CasKeyError("the CAS keyring payload is missing required fields")
        try:
            versions = {str(v): bytes.fromhex(str(m)) for v, m in dict(data["versions"]).items()}
            id_key = bytes.fromhex(str(data["id_key"]))
        except (TypeError, ValueError) as exc:
            raise CasKeyError("the CAS keyring payload is malformed") from exc
        current = str(data["current"])
        if current not in versions or len(id_key) != 32 or any(len(m) != 32 for m in versions.values()):
            raise CasKeyError("the CAS keyring payload is inconsistent")
        return cls(id_key=id_key, versions=versions, current=current)

    @classmethod
    def mint(cls) -> CasKeyring:
        return cls(
            id_key=secrets.token_bytes(32),
            versions={"1": secrets.token_bytes(32)},
            current="1",
        )

    def rotated(self, *, next_version: str | None = None) -> CasKeyring:
        version = str(next_version or (max((int(v) for v in self.versions if v.isdigit()), default=0) + 1))
        if version in self.versions:
            raise CasKeyError(f"CAS key version {version} already exists")
        return CasKeyring(
            id_key=self.id_key,
            versions={**self.versions, version: secrets.token_bytes(32)},
            current=version,
        )

    def without_version(self, version: str) -> CasKeyring:
        version = str(version)
        if version == self.current:
            raise CasKeyError("cannot drop the CURRENT CAS key version")
        versions = {v: m for v, m in self.versions.items() if v != version}
        if len(versions) == len(self.versions):
            raise CasKeyError(f"CAS key version {version} is not in this keyring")
        return CasKeyring(id_key=self.id_key, versions=versions, current=self.current)


def _from_keys_file(path: Path) -> CasKeyring | None:
    try:
        payload = path.read_text(encoding="utf-8")
    except OSError:
        return None  # an unreadable keys file is UNAVAILABLE, never a weaker fallback
    return CasKeyring.from_json(payload)


def resolve_keyring() -> CasKeyring | None:
    """The live CAS keyring, or None when no source can provide one (everything then fails
    closed). Never raises on unavailability -- the typed refusals belong to the callers."""
    override = str(os.environ.get(KEYS_FILE_ENV) or "").strip()
    if override:
        return _from_keys_file(Path(override).expanduser())
    try:
        from core import credential_store

        stored = credential_store.get_credential(KEYRING_CREDENTIAL_NAME)
        if stored:
            return CasKeyring.from_json(stored)
        minted = CasKeyring.mint()
        credential_store.store_credential(KEYRING_CREDENTIAL_NAME, minted.to_json(), label="Blackbox encrypted CAS keys")
        if credential_store.get_credential(KEYRING_CREDENTIAL_NAME) != minted.to_json():
            return None  # read-back failed: do not trust a key we cannot retrieve
        return minted
    except Exception:
        return None


def persist_keyring(keyring: CasKeyring) -> CasKeyring:
    """Persist a rotated keyring through the SAME source it was resolved from."""
    override = str(os.environ.get(KEYS_FILE_ENV) or "").strip()
    if override:
        path = Path(override).expanduser()
        payload = keyring.to_json()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
        return keyring
    from core import credential_store

    credential_store.store_credential(KEYRING_CREDENTIAL_NAME, keyring.to_json(), label="Blackbox encrypted CAS keys")
    if credential_store.get_credential(KEYRING_CREDENTIAL_NAME) != keyring.to_json():
        raise CasKeyError("the rotated CAS keyring could not be read back from the secret authority")
    return keyring


def keyring_state(keyring: CasKeyring | None) -> dict[str, Any]:
    """Diagnostics-safe description: versions and source, never material."""
    if keyring is None:
        return {"available": False, "reason": "no CAS key source resolved; effects needing the CAS fail closed"}
    return {"available": True, "current_version": keyring.current, "versions": sorted(keyring.versions)}


__all__ = [
    "KEYRING_CREDENTIAL_NAME",
    "KEYS_FILE_ENV",
    "CasKeyError",
    "CasKeyring",
    "keyring_state",
    "persist_keyring",
    "resolve_keyring",
]
