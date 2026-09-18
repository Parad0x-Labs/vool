"""Verified content-addressed blob store for Blackbox before/after bytes.

Bytes are addressed by their SHA-256. A blob is written to a temp file in the same directory,
fsynced, renamed over its final name, and the directory is fsynced -- so a crash never leaves a
half-written blob under a valid name. Every read re-hashes the bytes and refuses a mismatch as
``BlobCorruptError``; every write of an already-present hash re-verifies the existing bytes instead of
trusting the name. Nothing here depends on the shared SQLite database: a rollback must work when
the runtime database is unavailable, and a store that other lanes are rewriting is not a place
to keep the only copy of a user's file.

Limits, stated: the store is same-user writable. A process running as the same user can delete or
replace blobs; the journal's recorded SHA-256 exposes a replacement (``BlobCorruptError``) and a
deletion (``BlobMissingError``) at rollback time, but cannot recover the bytes.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple


class BlobError(RuntimeError):
    """Base class for every typed blob-store failure."""


class BlobMissingError(BlobError):
    pass


class BlobCorruptError(BlobError):
    pass


class BlobTooLargeError(BlobError):
    pass


class BlobRef(NamedTuple):
    """What a put returns. ``sha256`` is the content's raw digest (in memory, for callers that
    compare content); ``ref`` is the CAS ADDRESS -- the opaque keyed id under the encrypted CAS
    v2, the raw digest under legacy v1. Empty ``ref`` means the address IS ``sha256``."""

    sha256: str
    size: int
    ref: str = ""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fsync_directory(path: Path) -> None:
    with contextlib.suppress(OSError):
        fd = os.open(str(path), os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


class BlobStore:
    """The CAS facade. With ``cas`` (an ``EncryptedBlobStore``, or the fail-closed
    ``UnavailableCAS``) every WRITE is sealed v2 and every ref is an OPAQUE id, with legacy
    v1 plaintext files still READ during the migration window. Without ``cas`` -- the legacy
    direct constructor used by migration tooling and pre-amendment callers -- the store behaves
    exactly as v1 did. Under v2 the ``BlobRef.sha256`` field carries the opaque id: equal
    plaintext still deduplicates, and no raw digest reaches the filesystem or a filename."""

    def __init__(self, root: Path | str, *, cas=None) -> None:
        self.root = Path(root)
        self.blob_dir = self.root / "blobs"
        self.cas = cas

    def path_for(self, sha256: str) -> Path:
        digest = str(sha256 or "").strip().lower()
        if len(digest) != 64 or any(ch not in "0123456789abcdef" for ch in digest):
            raise BlobError(f"not a sha256 digest: {sha256!r}")
        if self.cas is not None:
            from storage.blackbox.cas import UnavailableCAS

            if not isinstance(self.cas, UnavailableCAS):
                return self.cas.path_for_id(self.cas.id_for(digest))
        return self.blob_dir / digest[:2] / digest

    def has(self, sha256: str) -> bool:
        if self.cas is not None:
            if self.cas.has(str(sha256)):
                return True
        try:
            return self.path_for(sha256).is_file()
        except BlobError:
            return False

    def put(self, data: bytes, *, max_bytes: int | None = None) -> BlobRef:
        if self.cas is not None:
            raw = sha256_bytes(data)
            blob_id, size = self.cas.put(data, max_bytes=max_bytes)
            return BlobRef(raw, size, blob_id)
        if max_bytes is not None and len(data) > max_bytes:
            raise BlobTooLargeError(f"{len(data)} bytes exceeds the {max_bytes}-byte capture limit")
        digest = sha256_bytes(data)
        target = self.path_for(digest)
        if target.is_file():
            # Verified CAS: the NAME is not trusted; the bytes under it are re-hashed. A wrong
            # blob under a right name is replaced by the right bytes, never left in place.
            if sha256_bytes(target.read_bytes()) == digest:
                return BlobRef(digest, len(data), digest)
        target.parent.mkdir(parents=True, exist_ok=True)
        with contextlib.suppress(OSError):
            os.chmod(self.blob_dir, 0o700)
        fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=f".{digest[:12]}.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, target)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp_name)
            raise
        _fsync_directory(target.parent)
        return BlobRef(digest, len(data), digest)

    def get(self, sha256: str) -> bytes:
        if self.cas is not None:
            # Sealed copy first; a miss falls through to legacy v1 files so the migration
            # window never loses read access to not-yet-migrated blobs.
            try:
                return self.cas.get(str(sha256))
            except BlobMissingError:
                pass
        target = self.path_for(sha256)
        try:
            data = target.read_bytes()
        except FileNotFoundError as exc:
            raise BlobMissingError(f"blob {sha256} is not in the store") from exc
        actual = sha256_bytes(data)
        if actual != str(sha256).lower():
            raise BlobCorruptError(f"blob {sha256} holds bytes hashing to {actual}")
        return data

    def delete(self, sha256: str) -> bool:
        """Erasure: remove the sealed ciphertext AND any legacy plaintext twin."""
        removed = self.cas.delete(str(sha256)) if self.cas is not None else False
        try:
            legacy = self.blob_dir / str(sha256)[:2] / str(sha256)
        except BlobError:
            return removed
        try:
            legacy.unlink()
            _fsync_directory(legacy.parent)
            removed = True
        except FileNotFoundError:
            pass
        return removed

    def iter_sha256(self) -> Iterator[str]:
        """Opaque ids (v2) then legacy digests (v1) -- the store's full population, for status
        and retention. Under v2 these names are NOT raw digests; nothing should treat them as
        content hashes."""
        if self.cas is not None:
            yield from self.cas.iter_ids()
        if not self.blob_dir.is_dir():
            return
        for shard in sorted(self.blob_dir.iterdir()):
            if not shard.is_dir():
                continue
            for item in sorted(shard.iterdir()):
                if item.is_file() and len(item.name) == 64:
                    yield item.name

    def total_bytes(self) -> int:
        total = 0
        if self.cas is not None:
            for blob_id in self.cas.iter_ids():
                with contextlib.suppress(OSError, ValueError):
                    total += self.cas.path_for_id(blob_id).stat().st_size
        if self.blob_dir.is_dir():
            for shard in self.blob_dir.iterdir():
                if not shard.is_dir():
                    continue
                for item in shard.iterdir():
                    if item.is_file() and len(item.name) == 64:
                        with contextlib.suppress(OSError):
                            total += item.stat().st_size
        return total


__all__ = ["BlobCorruptError", "BlobError", "BlobMissingError", "BlobRef", "BlobStore", "BlobTooLargeError", "sha256_bytes"]
