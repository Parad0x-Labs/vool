"""CAS integrity law: content-addressed names prove nothing — the bytes must.

Confirmed at e821457d (Sonnet-confirmed, synthesis §2): ``storage.cas.get_bytes``
joined chunk files with no hash verification, and ``storage.chunk_store.load_chunk``
returned whatever bytes sat at the address; ``store_chunk`` wrote directly, so a
torn write survived permanently behind ``if not path.exists()``.

This file pins the repaired contract:
- a read serves bytes only if every chunk hashes to its address AND the assembled
  blob hashes to the requested address with the manifest's declared total size
- the manifest must still describe the requested blob (identity binding)
- publication is atomic (temp file + rename); a torn or tampered existing chunk
  is detected and REPLACED by a re-store of the same content — recovery, not
  permanent loss
- corruption is fail-closed: a bad chunk makes the blob unavailable (None),
  never confidently wrong bytes
"""
from __future__ import annotations

import hashlib

import pytest

import storage.chunk_store as chunk_store
from storage.cas import get_bytes, put_bytes
from storage.cas_integrity import CasCorruptionError
from storage.manifest_store import load_manifest, save_manifest


@pytest.fixture
def cas_root(tmp_path, monkeypatch):
    root = tmp_path / "cas_chunks"
    root.mkdir()
    monkeypatch.setattr(chunk_store, "chunk_root", lambda: root)
    return root


def _chunk_path(root, data: bytes) -> "object":
    digest = hashlib.sha256(data).hexdigest()
    return root / digest[:2] / digest[2:4] / digest


# -- positive controls ----------------------------------------------------------------------


def test_single_chunk_round_trip(cas_root):
    payload = b"archive payload: harvest log 2026-09"
    manifest = put_bytes(payload)
    assert manifest["total_bytes"] == len(payload)
    assert get_bytes(manifest["blob_hash"]) == payload


def test_multi_chunk_round_trip(cas_root):
    payload = bytes(range(256)) * 7  # > one 64 KiB chunk boundary? no — force small chunks instead
    manifest = put_bytes(payload, chunk_size=64)
    assert len(manifest["chunk_hashes"]) > 1
    assert get_bytes(manifest["blob_hash"]) == payload


# -- verify-on-read ---------------------------------------------------------------------------


def test_corrupted_chunk_file_is_never_served(cas_root):
    """Bit-rot in place: the blob becomes unavailable loudly, not confidently wrong."""
    payload = b"clinically load-bearing bytes: dosage 4.5mg at 08:00" * 20
    manifest = put_bytes(payload)
    path = _chunk_path(cas_root, payload)
    original = path.read_bytes()
    try:
        path.write_bytes(original + b" tampered tail")
        with pytest.raises(CasCorruptionError):
            get_bytes(manifest["blob_hash"])
    finally:
        path.write_bytes(original)


def test_truncated_chunk_file_is_never_served(cas_root):
    payload = b"ledger row 512: paid invoice Q-3311 in full on 2026-08-30" * 40
    manifest = put_bytes(payload)
    path = _chunk_path(cas_root, payload)
    original = path.read_bytes()
    try:
        path.write_bytes(original[: len(original) // 2])  # a torn write
        with pytest.raises(CasCorruptionError):
            get_bytes(manifest["blob_hash"])
    finally:
        path.write_bytes(original)


def test_load_chunk_verifies_the_address(cas_root):
    data = b"chunk body with its own identity"
    chunk_hash, _ = chunk_store.store_chunk(data)
    path = _chunk_path(cas_root, data)
    original = path.read_bytes()
    try:
        path.write_bytes(b"different body at the same address")
        with pytest.raises(CasCorruptionError):
            chunk_store.load_chunk(chunk_hash)
    finally:
        path.write_bytes(original)
    assert chunk_store.load_chunk(chunk_hash) == data


def test_missing_chunk_returns_none(cas_root):
    payload = b"payload whose chunk will vanish"
    manifest = put_bytes(payload)
    path = _chunk_path(cas_root, payload)
    path.unlink()
    assert get_bytes(manifest["blob_hash"]) is None


# -- manifest identity binding -----------------------------------------------------------------


def test_manifest_describing_a_different_blob_is_rejected(cas_root):
    """blob_index → manifest_id → manifest must still resolve to THIS blob."""
    payload = b"identity-bound payload A"
    manifest = put_bytes(payload)
    meta_manifest_id = manifest["manifest_id"]
    foreign = dict(manifest)
    foreign["blob_hash"] = hashlib.sha256(b"a different blob").hexdigest()
    save_manifest(meta_manifest_id, foreign["blob_hash"], foreign)
    with pytest.raises(CasCorruptionError):
        get_bytes(manifest["blob_hash"])


def test_manifest_total_bytes_mismatch_is_rejected(cas_root):
    payload = b"size-declared payload for the silo audit"
    manifest = put_bytes(payload)
    lying = dict(load_manifest(manifest["manifest_id"]))
    lying["total_bytes"] = len(payload) + 512
    save_manifest(manifest["manifest_id"], manifest["blob_hash"], lying)
    with pytest.raises(CasCorruptionError):
        get_bytes(manifest["blob_hash"])


# -- atomic publication and torn-chunk recovery -------------------------------------------------


def test_restorage_recovers_a_torn_existing_chunk(cas_root):
    """A torn write must not permanently poison the address: storing the same
    content again detects the bad incumbent and republishes atomically."""
    data = b"recoverable chunk: thermostat calibration 21.5C"
    chunk_hash, path = chunk_store.store_chunk(data)
    path.write_bytes(data[:10])  # simulate the interrupted first write
    chunk_hash2, path2 = chunk_store.store_chunk(data)
    assert chunk_hash2 == chunk_hash and path2 == path
    assert chunk_store.load_chunk(chunk_hash) == data


def test_restorage_of_healthy_chunk_is_stable(cas_root):
    data = b"healthy incumbent"
    chunk_hash, path = chunk_store.store_chunk(data)
    before = path.stat().st_mtime_ns
    chunk_store.store_chunk(data)
    assert chunk_store.load_chunk(chunk_hash) == data
    assert path.stat().st_mtime_ns == before  # dedup: no pointless rewrite


def test_full_blob_recovery_after_chunk_corruption(cas_root):
    payload = b"end-to-end recovery corpus: ferry manifest rows 1..90" * 30
    manifest = put_bytes(payload, chunk_size=64)
    first_hash = manifest["chunk_hashes"][0]
    path = cas_root / first_hash[:2] / first_hash[2:4] / first_hash
    path.write_bytes(b"corrupted incumbent bytes")
    with pytest.raises(CasCorruptionError):
        get_bytes(manifest["blob_hash"])
    repaired = put_bytes(payload, chunk_size=64)
    assert get_bytes(repaired["blob_hash"]) == payload
