from __future__ import annotations

import contextlib
import hashlib
import json
import os
import uuid
from pathlib import Path
from typing import Any

from core.runtime_paths import data_path
from storage.cas_integrity import (
    CasCorruptionError,
    CasMissingError,
    atomic_publish,
    canonical_digest,
    resolve_address,
    validate_address,
    verified_read,
)

CAS_DIR = data_path("cas")
CHUNK_SIZE = 2 * 1024 * 1024  # 2MB chunks

def _ensure_cas_dir() -> None:
    CAS_DIR.mkdir(parents=True, exist_ok=True)

def hash_bytes(data: bytes) -> str:
    return canonical_digest(data)

def _chunk_path(chunk_hash: str) -> Path:
    """Address -> path. Resolves the address; never creates directories."""
    chunk_hash = resolve_address(chunk_hash)
    return CAS_DIR / chunk_hash[:2] / chunk_hash[2:4] / chunk_hash

def get_chunk_path(chunk_hash: str) -> Path:
    """Returns the path for a given chunk hash, using a 2-level directory prefix structure."""
    path = _chunk_path(chunk_hash)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

def store_chunk(data: bytes) -> str:
    """
    Hashes the data, publishes it atomically under its address if it is not
    already there, and returns its SHA-256 hash. (Deduplication)
    """
    _ensure_cas_dir()
    chunk_hash = hash_bytes(data)
    atomic_publish(get_chunk_path(chunk_hash), data)
    return chunk_hash

def read_chunk(chunk_hash: str) -> bytes:
    """Verified read. Raises ``CasMissingError`` or ``CasCorruptionError``."""
    return verified_read(_chunk_path(chunk_hash), chunk_hash)

def get_chunk(chunk_hash: str) -> bytes | None:
    """Verified read with ``None`` as the missing outcome; corruption raises."""
    try:
        return read_chunk(chunk_hash)
    except CasMissingError:
        return None

def chunk_file(file_path: str) -> dict[str, Any]:
    """
    Streams a file from disk, chunks it into 2MB pieces, stores them in CAS,
    and returns a manifest dictionary representing the file. The manifest
    declares the whole-file sha256 so reconstruction can verify the result.
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File {file_path} not found")

    file_size = path.stat().st_size
    chunk_hashes = []
    whole = hashlib.sha256()

    with open(path, "rb") as f:
        while True:
            chunk = f.read(CHUNK_SIZE)
            if not chunk:
                break
            whole.update(chunk)
            chunk_hash = store_chunk(chunk)
            chunk_hashes.append(chunk_hash)

    manifest = {
        "filename": path.name,
        "size_bytes": file_size,
        "chunks": chunk_hashes,
        "chunk_size_bytes": CHUNK_SIZE,
        "file_sha256": whole.hexdigest(),
    }

    # Store the manifest itself as a chunk so the whole file can be referenced by one root hash
    manifest_json = json.dumps(manifest, sort_keys=True).encode("utf-8")
    root_hash = store_chunk(manifest_json)

    manifest["root_hash"] = root_hash
    return manifest

def _verified_manifest(root_hash: str) -> dict[str, Any]:
    """Read the manifest chunk (digest-verified) and shape-check it.

    Legacy manifests without ``file_sha256`` are accepted; whatever a manifest
    declares (``size_bytes``, ``file_sha256``) must be well-formed and is
    enforced during reconstruction.
    """
    root_hash = resolve_address(root_hash)
    manifest_bytes = read_chunk(root_hash)
    try:
        manifest = json.loads(manifest_bytes.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise CasCorruptionError(root_hash, reason="manifest_malformed", detail=str(exc)) from exc
    if not isinstance(manifest, dict):
        raise CasCorruptionError(root_hash, reason="manifest_malformed", detail="not an object")
    chunks = manifest.get("chunks")
    if not isinstance(chunks, list):
        raise CasCorruptionError(root_hash, reason="manifest_malformed", detail="chunks is not a list")
    for chunk_hash in chunks:
        validate_address(chunk_hash)
    size = manifest.get("size_bytes")
    if size is not None and (isinstance(size, bool) or not isinstance(size, int) or size < 0):
        raise CasCorruptionError(root_hash, reason="manifest_malformed", detail="size_bytes is not a size")
    declared = manifest.get("file_sha256")
    if declared is not None:
        validate_address(declared)
    return manifest

def reconstruct_object(root_hash: str, output_path: str) -> None:
    """
    Reconstruct a file from its root manifest hash into ``output_path``.

    Every chunk is digest-verified as it is read; the declared size and (when
    declared) the whole-file sha256 are verified before the output is published
    with an atomic rename. On any failure nothing is left at ``output_path``.

    Raises ``CasMissingError`` when the manifest or a chunk is absent and
    ``CasCorruptionError`` when anything present fails verification.
    """
    manifest = _verified_manifest(root_hash)
    out = Path(output_path)
    tmp = out.parent / f".{out.name}.{uuid.uuid4().hex}.tmp"
    whole = hashlib.sha256()
    total = 0
    try:
        with open(tmp, "wb") as f:
            for chunk_hash in manifest["chunks"]:
                data = read_chunk(chunk_hash)
                f.write(data)
                whole.update(data)
                total += len(data)
        size = manifest.get("size_bytes")
        if size is not None and total != size:
            raise CasCorruptionError(
                root_hash, reason="length_mismatch", detail=f"declared {size} bytes, assembled {total}"
            )
        declared = manifest.get("file_sha256")
        if declared is not None and whole.hexdigest() != declared:
            raise CasCorruptionError(
                root_hash, reason="file_digest_mismatch", expected=declared, observed=whole.hexdigest()
            )
        os.replace(tmp, out)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            tmp.unlink()
        raise

def reconstruct_file(root_hash: str, output_path: str) -> bool:
    """
    Reconstructs a file from its root manifest hash into the specified output path.
    Returns True on success, False when the manifest or a chunk is missing.
    Corruption is never mapped to False; it raises ``CasCorruptionError``.
    """
    try:
        reconstruct_object(root_hash, output_path)
    except CasMissingError:
        return False
    return True
