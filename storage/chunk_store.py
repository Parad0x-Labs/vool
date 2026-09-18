from __future__ import annotations

from pathlib import Path

from core.runtime_paths import data_path
from storage.cas_integrity import (
    CasMissingError,
    atomic_publish,
    canonical_digest,
    resolve_address,
    verified_read,
)

DEFAULT_CHUNK_SIZE = 64 * 1024


def chunk_root() -> Path:
    """The CAS chunk root, resolved on EVERY call.

    This was a module-level constant evaluated at import time, which meant the
    root froze to whichever runtime home happened to be active when
    ``storage.chunk_store`` was first imported. A later
    ``configure_runtime_home()`` — which is how every test claims an isolated
    home — moved the database and the rest of the data directory but left the
    chunk store pointing at the first home. Because pages are CONTENT-ADDRESSED,
    that silently shared one CAS across every "isolated" test: a test that
    erased a blob's bytes left a poisoned address that any later test admitting
    identical content inherited, surfacing as a ``digest_mismatch`` far from its
    cause.
    """
    return data_path("cas_chunks")


def _chunk_path(chunk_hash: str) -> Path:
    """Address -> path. Resolves the address; never creates directories."""
    chunk_hash = resolve_address(chunk_hash)
    return chunk_root() / chunk_hash[:2] / chunk_hash[2:4] / chunk_hash


def _chunk_dir(chunk_hash: str) -> Path:
    path = _chunk_path(chunk_hash).parent
    path.mkdir(parents=True, exist_ok=True)
    return path


def hash_bytes(data: bytes) -> str:
    return canonical_digest(data)


def store_chunk(data: bytes) -> tuple[str, Path]:
    chunk_hash = hash_bytes(data)
    path = _chunk_dir(chunk_hash) / chunk_hash
    atomic_publish(path, data)
    return chunk_hash, path


def read_chunk(chunk_hash: str) -> bytes:
    """Verified read. Raises ``CasMissingError`` or ``CasCorruptionError``."""
    return verified_read(_chunk_path(chunk_hash), chunk_hash)


def load_chunk(chunk_hash: str) -> bytes | None:
    """Verified read with ``None`` as the missing outcome.

    Corruption (digest mismatch, truncation, malformed address) is never
    mapped to ``None``; it raises ``CasCorruptionError``.
    """
    try:
        return read_chunk(chunk_hash)
    except CasMissingError:
        return None
