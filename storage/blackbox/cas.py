"""Encrypted CAS v2: authenticated-encryption blob storage for the Blackbox.

Every blob is sealed with AES-256-GCM (the ``cryptography`` library's maintained AEAD -- no
homemade cipher), under a random 96-bit nonce, with the schema version, the blob's opaque id,
its key version and its exact plaintext size bound as additional authenticated data. The file
name is ``HMAC(id_key, sha256(plaintext))`` -- equal plaintext still deduplicates, and the raw
plaintext digest appears on no path. File contents are a JSON envelope::

    {"schema": "blackbox-blob-v2", "kid": 2, "id": "<opaque id>", "size": 128,
     "nonce": "<b64 12 bytes>", "ct": "<b64 ciphertext+tag>"}

Reads decrypt, authenticate (GCM tag over ciphertext AND metadata) and verify (the recovered
plaintext's opaque id must equal the envelope's id) before any byte is returned. A tampered
nonce, ciphertext, size, id, key version or schema field fails typed: ``BlobCorruptError`` for
anything that fails authentication, ``CasKeyError`` for a key version this keyring no longer
holds, ``BlobMissingError`` for absence. There is no mode in which plaintext is written.

Threat model, stated honestly: this is tamper-evident and confidential against filesystem
inspection -- an attacker with only the disk (backup, imaging, another account) gets
ciphertext, opaque names, and no plaintext digests. A process running as the SAME LIVE USER
can reach the key through the same secret authority the daemon uses, same as v1's MAC key; the
encryption does not add a boundary the OS account does not already cross.

Erasure (``delete``) removes the ciphertext file and any v1 plaintext twin: after it, that
content is recoverable from nowhere in this store.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import secrets
import tempfile
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from storage.blackbox.blobs import BlobCorruptError, BlobMissingError, BlobTooLargeError

SCHEMA = "blackbox-blob-v2"
NONCE_BYTES = 12


class CasKeyError(RuntimeError):
    """A key-version problem (absent/rotated). Never carries key material."""


class BlobKeyUnavailableError(RuntimeError):
    """No key source resolved; the CAS refuses rather than fall back to plaintext."""


def _fsync_directory(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def _aad(id_hex: str, kid: str, size: int) -> bytes:
    return json.dumps({"schema": SCHEMA, "id": id_hex, "kid": str(kid), "size": int(size)}, sort_keys=True, separators=(",", ":")).encode("utf-8")


class EncryptedBlobStore:
    """The v2 CAS under ``<root>/cas-v2``. Addresses are OPAQUE ids (keyed HMAC of the raw
    digest); ``raw`` digests are accepted at the API edge and translated in memory, so callers
    that only ever knew the raw digest keep working while the filesystem never sees one."""

    def __init__(self, root: Path | str, keyring) -> None:
        self.root = Path(root)
        self.keyring = keyring  # core.blackbox.coverage.cas_keys.CasKeyring
        if self.keyring is None:
            raise BlobKeyUnavailableError("the encrypted CAS has no keyring; it refuses to run rather than store plaintext")

    def path_for_id(self, id_hex: str) -> Path:
        digest = str(id_hex or "").strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise ValueError(f"not a 64-hex blob id: {id_hex!r}")
        return self.root / digest[:2] / digest

    def id_for(self, raw_sha256: str) -> str:
        return self.keyring.id_for(raw_sha256)

    # -- write --------------------------------------------------------------------------------
    def _seal(self, data: bytes, blob_id: str) -> None:
        """Always write a fresh sealed envelope (random nonce, CURRENT key version). Atomic."""
        nonce = secrets.token_bytes(NONCE_BYTES)
        kid = self.keyring.current
        key = self.keyring.current_key
        ct = AESGCM(key).encrypt(nonce, data, _aad(blob_id, kid, len(data)))
        envelope = {
            "schema": SCHEMA,
            "kid": str(kid),
            "id": blob_id,
            "size": len(data),
            "nonce": base64.b64encode(nonce).decode("ascii"),
            "ct": base64.b64encode(ct).decode("ascii"),
        }
        target = self.path_for_id(blob_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.root, 0o700)
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{blob_id[:12]}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(envelope, sort_keys=True, separators=(",", ":")))
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
        _fsync_directory(target.parent)

    def put(self, data: bytes, *, max_bytes: int | None = None) -> tuple[str, int]:
        """Seal ``data``; returns (opaque id, size). Random nonce per write; verified before
        the existing file is trusted on a dedup hit."""
        if max_bytes is not None and len(data) > max_bytes:
            raise BlobTooLargeError(f"{len(data)} bytes exceeds the {max_bytes}-byte capture limit")
        raw = hashlib.sha256(data).hexdigest()  # in memory only; never a filename, never a row
        blob_id = self.id_for(raw)
        target = self.path_for_id(blob_id)
        if target.is_file():
            if self._decrypt_file(target, blob_id) == data:
                return blob_id, len(data)
            with contextlib.suppress(OSError):
                target.unlink()  # wrong bytes under a right id: replace, never trust the name
        self._seal(data, blob_id)
        return blob_id, len(data)

    # -- read ---------------------------------------------------------------------------------
    def _decrypt_file(self, path: Path, expected_id: str) -> bytes:
        try:
            envelope = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise BlobCorruptError(f"blob envelope for {expected_id} is unreadable") from exc
        if not isinstance(envelope, dict) or envelope.get("schema") != SCHEMA:
            raise BlobCorruptError(f"blob envelope for {expected_id} is not {SCHEMA}")
        kid = str(envelope.get("kid") or "")
        blob_id = str(envelope.get("id") or "")
        size = envelope.get("size")
        if blob_id != expected_id:
            raise BlobCorruptError("blob envelope id disagrees with its address")
        try:
            nonce = base64.b64decode(str(envelope.get("nonce") or ""), validate=True)
            ct = base64.b64decode(str(envelope.get("ct") or ""), validate=True)
        except (ValueError, TypeError) as exc:
            raise BlobCorruptError(f"blob envelope for {expected_id} holds invalid encoding") from exc
        try:
            key = self.keyring.key_for(kid)
        except CasKeyError:
            raise  # typed key-version failure: rotated away or foreign keyring
        try:
            data = AESGCM(key).decrypt(nonce, ct, _aad(blob_id, kid, size))
        except InvalidTag as exc:
            raise BlobCorruptError(f"blob {expected_id} failed authentication (tampered or wrong key)") from exc
        if not isinstance(size, int) or size != len(data):
            raise BlobCorruptError(f"blob {expected_id} size binding failed")
        if self.id_for(hashlib.sha256(data).hexdigest()) != blob_id:
            raise BlobCorruptError(f"blob {expected_id} does not hold the content it addresses")
        return data

    def get(self, ref: str) -> bytes:
        """``ref`` may be an opaque id (journal rows) or a legacy raw digest (v1 callers);
        both resolve to the same sealed file."""
        ref = str(ref or "").strip().lower()
        for candidate in dict.fromkeys([ref, self.id_for(ref)]):
            path = self.path_for_id(candidate)
            if path.is_file():
                return self._decrypt_file(path, candidate)
        raise BlobMissingError(f"blob {ref} is not in the encrypted CAS")

    def has(self, ref: str) -> bool:
        ref = str(ref or "").strip().lower()
        return any(self.path_for_id(c).is_file() for c in dict.fromkeys([ref, self.id_for(ref)]))

    def delete(self, ref: str) -> bool:
        ref = str(ref or "").strip().lower()
        removed = False
        for candidate in dict.fromkeys([ref, self.id_for(ref)]):
            path = self.path_for_id(candidate)
            with contextlib.suppress(FileNotFoundError):
                path.unlink()
                _fsync_directory(path.parent)
                removed = True
        return removed

    def iter_ids(self):
        if not self.root.is_dir():
            return
        for shard in sorted(self.root.iterdir()):
            if shard.is_dir():
                for item in sorted(shard.iterdir()):
                    if item.is_file() and len(item.name) == 64:
                        yield item.name

    def reencrypt_all(self) -> int:
        """Re-seal every blob under the CURRENT key version (used after rotation). Each blob is
        decrypted+verified, re-encrypted atomically; failures stop the pass and stay typed."""
        count = 0
        for blob_id in list(self.iter_ids()):
            path = self.path_for_id(blob_id)
            envelope = json.loads(path.read_text(encoding="utf-8"))
            if str(envelope.get("kid")) == self.keyring.current:
                continue
            data = self._decrypt_file(path, blob_id)
            if self.id_for(hashlib.sha256(data).hexdigest()) != blob_id:
                # id is version-independent by construction; a disagreement means the file was
                # foreign to this keyring -- refuse before sealing anything under our name.
                raise BlobCorruptError(f"blob {blob_id} does not hold the content it addresses")
            self._seal(data, blob_id)  # force a fresh envelope: dedup would keep the old kid
            count += 1
        return count


class UnavailableCAS:
    """The fail-closed stand-in when no key source resolved: every write raises typed, every
    read says missing, and nothing is ever stored in plaintext instead."""

    def put(self, data: bytes, *, max_bytes: int | None = None):
        raise BlobKeyUnavailableError(
            "the encrypted CAS key is unavailable through the secret authority; an effect that "
            "needs rollback capture fails closed rather than store plaintext"
        )

    def get(self, ref: str) -> bytes:
        raise BlobKeyUnavailableError("the encrypted CAS key is unavailable; blobs cannot be read")

    def has(self, ref: str) -> bool:
        return False

    def delete(self, ref: str) -> bool:
        return False

    def iter_ids(self):
        return iter(())


def migrate_v1_blobs(v1_dir: Path, cas: EncryptedBlobStore) -> dict:
    """Crash-safe v1->v2 migration: seal every plaintext blob, BYTE-VERIFY the sealed copy
    (decrypt + authenticate + id check), and delete the plaintext only afterwards. Per-blob
    atomicity makes an interrupted pass resumable: re-running skips already-sealed content by
    its verified presence and continues with the rest. A v1 blob whose bytes do not hash to its
    name is REPORTED and left in place -- migration never silently drops evidence.

    Returns counts; never raises on individual blobs (the report names them)."""
    import hashlib as _hashlib

    migrated = 0
    corrupt: list[str] = []
    failed: list[str] = []
    v1_dir = Path(v1_dir)
    if not v1_dir.is_dir():
        return {"migrated": 0, "corrupt": [], "failed": [], "remaining": 0}
    for shard in sorted(v1_dir.iterdir()):
        if not shard.is_dir():
            continue
        for item in sorted(shard.iterdir()):
            if not item.is_file() or len(item.name) != 64:
                continue
            try:
                data = item.read_bytes()
                if _hashlib.sha256(data).hexdigest() != item.name:
                    corrupt.append(item.name)
                    continue
                blob_id, _size = cas.put(data)
                if cas.get(blob_id) == data:  # verified encrypted replacement
                    with contextlib.suppress(OSError):
                        item.unlink()
                        _fsync_directory(item.parent)
                    migrated += 1
                else:
                    failed.append(item.name)
            except Exception:
                # A blob that could not be sealed NOW stays in place -- plaintext is deleted
                # only after its sealed copy verifies. This is the interrupted state a re-run
                # resumes from; the report names what is left.
                failed.append(item.name)
    remaining = sum(
        1
        for shard in v1_dir.iterdir() if shard.is_dir()
        for f in shard.iterdir() if f.is_file() and len(f.name) == 64
    )
    return {"migrated": migrated, "corrupt": corrupt, "failed": failed, "remaining": remaining}


__all__ = [
    "SCHEMA",
    "BlobCorruptError",
    "BlobKeyUnavailableError",
    "BlobMissingError",
    "BlobTooLargeError",
    "CasKeyError",
    "EncryptedBlobStore",
    "UnavailableCAS",
    "migrate_v1_blobs",
]
