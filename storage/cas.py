from __future__ import annotations

import uuid
from collections.abc import Iterable

from storage.blob_index import get_blob, upsert_blob
from storage.cas_integrity import (
    CasCorruptionError,
    CasMissingError,
    canonical_digest,
    resolve_address,
    validate_address,
)
from storage.chunk_store import DEFAULT_CHUNK_SIZE, read_chunk, store_chunk
from storage.manifest_store import load_manifest, save_manifest


def _chunk_bytes(data: bytes, chunk_size: int = DEFAULT_CHUNK_SIZE) -> Iterable[bytes]:
    for idx in range(0, len(data), chunk_size):
        yield data[idx : idx + chunk_size]


def put_bytes(data: bytes, *, chunk_size: int = DEFAULT_CHUNK_SIZE) -> dict:
    chunk_hashes: list[str] = []
    for chunk in _chunk_bytes(data, chunk_size=chunk_size):
        chunk_hash, _ = store_chunk(chunk)
        chunk_hashes.append(chunk_hash)

    blob_hash = canonical_digest(data)
    manifest_id = str(uuid.uuid4())
    manifest = {
        "manifest_id": manifest_id,
        "blob_hash": blob_hash,
        "chunk_hashes": chunk_hashes,
        "chunk_size": chunk_size,
        "total_bytes": len(data),
    }
    save_manifest(manifest_id, blob_hash, manifest)
    upsert_blob(blob_hash, len(data), len(chunk_hashes), manifest_id)
    return manifest


def _verified_manifest(blob_hash: str) -> dict:
    """Resolve and shape-check the manifest behind a blob address.

    A caller address that is not a digest, a missing index row or a missing
    manifest row -> ``CasMissingError``. A manifest that
    does not parse, is not a dict, lacks a list of canonical chunk addresses,
    names a different blob, or declares a non-integer size -> ``CasCorruptionError``.
    """
    address = resolve_address(blob_hash)
    meta = get_blob(address)
    if not meta:
        raise CasMissingError(address, reason="blob_unindexed")
    manifest_id = str(meta.get("manifest_id") or "").strip()
    if not manifest_id:
        raise CasCorruptionError(address, reason="manifest_reference_missing")
    try:
        manifest = load_manifest(manifest_id)
    except (ValueError, TypeError) as exc:
        raise CasCorruptionError(address, reason="manifest_malformed", detail=str(exc)) from exc
    if manifest is None:
        raise CasMissingError(address, reason="manifest_missing")
    if not isinstance(manifest, dict):
        raise CasCorruptionError(address, reason="manifest_malformed", detail="not an object")
    chunk_hashes = manifest.get("chunk_hashes")
    if not isinstance(chunk_hashes, list):
        raise CasCorruptionError(address, reason="manifest_malformed", detail="chunk_hashes is not a list")
    for chunk_hash in chunk_hashes:
        validate_address(chunk_hash)
    if str(manifest.get("blob_hash") or "") != address:
        raise CasCorruptionError(address, reason="manifest_address_mismatch")
    total = manifest.get("total_bytes")
    if isinstance(total, bool) or not isinstance(total, int) or total < 0:
        raise CasCorruptionError(address, reason="manifest_malformed", detail="total_bytes is not a size")
    return manifest


def read_bytes(blob_hash: str) -> bytes:
    """Verified blob read: every chunk, the declared length and the blob digest.

    Raises ``CasMissingError`` when the blob, its manifest or any chunk is
    absent; ``CasCorruptionError`` when anything present is not what its
    address says. Never returns bytes that fail either check.
    """
    manifest = _verified_manifest(blob_hash)
    address = manifest["blob_hash"]
    parts = [read_chunk(chunk_hash) for chunk_hash in manifest["chunk_hashes"]]
    data = b"".join(parts)
    if len(data) != manifest["total_bytes"]:
        raise CasCorruptionError(
            address,
            reason="length_mismatch",
            detail=f"declared {manifest['total_bytes']} bytes, assembled {len(data)}",
        )
    observed = canonical_digest(data)
    if observed != address:
        raise CasCorruptionError(address, reason="blob_digest_mismatch", expected=address, observed=observed)
    return data


def get_bytes(blob_hash: str) -> bytes | None:
    """Verified blob read with ``None`` as the missing outcome.

    Corruption is never mapped to ``None``; it raises ``CasCorruptionError``.
    """
    try:
        return read_bytes(blob_hash)
    except CasMissingError:
        return None
