"""VOOL-facing API over the vendored OX-LIQUEFY snapshot.

Boundary law (dossier 2026-08-31 §36.15): compression is storage optimization,
never authority. Everything here is bytes-in/bytes-out plus typed failures; no
permission, routing, or finality decision may ever consult this module.

Key derivation note: the vendored NULL_Security_Layer derives the AES key from
``master_secret + tenant_id`` via PBKDF2 with no domain separation (audit
§22.2). The decision record requires the flaw "fixed on adoption"; rather than
editing the pristine snapshot, callers' keys are domain-separated HERE, before
they reach the vendored layer, so VOOL's tenant string can never collide across
protocol boundaries.
"""
from __future__ import annotations

import hashlib
import time

from core.liquefy.vendor.columnar_gun_v1 import NULL_Json_Columnar_Gun_v1
from core.liquefy.vendor.pcc import (
    Commitment,
    InclusionProof,
    commit_records,
    inclusion_proof,
    verify_disclosure,
    verify_inclusion,
)
from core.liquefy.vendor.sealing import NULL_Security_Layer

CODEC_ID = "LIQUEFY-COL2"
CODEC_VERSION = 1
#: zstd level for cold log segments (engine default 22 is slow; 12 kept a >100x
#: ratio on representative VOOL log classes in the P1 bench).
DEFAULT_LEVEL = 12
VOOL_TENANT = "vool.liquefy.log.v1"
_KEY_DOMAIN = b"vool.liquefy.seal-key.v1\x00"

_ENGINE: NULL_Json_Columnar_Gun_v1 | None = None


def _engine() -> NULL_Json_Columnar_Gun_v1:
    """One shared engine instance. zstd decode is level-independent, and the
    engine holds no per-request state beyond last_skipped_lines, so a single
    instance serves compress/decompress/search for every store."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = NULL_Json_Columnar_Gun_v1(level=DEFAULT_LEVEL)
    return _ENGINE


def engine() -> NULL_Json_Columnar_Gun_v1:
    """Public accessor for the shared vendored engine (store + bench use)."""
    return _engine()


def domain_sealed_key(key: bytes) -> bytes:
    """Domain-separate an operator key before it reaches the vendored KDF."""
    if not isinstance(key, (bytes, bytearray)) or len(key) < 16:
        raise ValueError("liquefy sealing key must be >=16 bytes of entropy")
    return hashlib.sha256(_KEY_DOMAIN + bytes(key)).digest()


def canonical_jsonl(records: list[dict]) -> bytes:
    """The one canonical pre-compression encoding (``vool.liquefy.canon.v1``).

    Measured property (P1, pinned by tests): the COL2 codec reconstructs rows
    with the UNION of keys in first-seen order, None-filling absent keys, and
    preserves every value exactly (incl. TZ-offset timestamps, nested dicts,
    unicode). Canonical form IS that normal form — union projection, first-seen
    key order, compact separators — so a recovered segment re-canonicalizes to
    byte-identical input and the original_bytes hash is a true read gate, not an
    aspiration. Raw caller formatting is deliberately NOT preserved; that is the
    value-exact class law (dossier §36.4), recorded in every segment header.
    """
    import json

    key_order: list[str] = []
    seen: set = set()
    for row in records:
        if not isinstance(row, dict):
            raise TypeError("liquefy log records must be dicts")
        for key in row:
            if key not in seen:
                seen.add(key)
                key_order.append(key)
    projected = [{key: row.get(key) for key in key_order} for row in records]
    return "\n".join(
        json.dumps(record, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
        for record in projected
    ).encode("utf-8")


def compress_records(records: list[dict]) -> bytes:
    return _engine().compress(canonical_jsonl(records))


def decompress_records(blob: bytes) -> list[dict]:
    import json

    raw = decompress(blob)
    return [json.loads(line) for line in raw.decode("utf-8").splitlines() if line]


def decompress(blob: bytes) -> bytes:
    """Fail-closed decompress: every engine failure surfaces as ``ValueError``."""
    try:
        return _engine().decompress(blob)
    except ValueError:
        raise
    except Exception as exc:  # zstd/struct/index errors -> one typed shape
        raise ValueError(f"corrupt or truncated Liquefy archive: {exc}") from exc


def search_blob(blob: bytes, query: str) -> dict:
    """Column-scoped search inside a compressed blob WITHOUT full decompression.

    Returns {found, matches(row indices), latency_ms, method, error?}. Engine
    integrity failures (corrupt column/frame) are surfaced via ``error`` /
    ``ValueError``, never masked as a clean miss.
    """
    if blob[:4] not in (b"COL2", b"COL3"):
        raise ValueError("search: not a Liquefy archive (bad magic)")
    start = time.perf_counter()
    try:
        raw = _engine().grep(blob, query)
    except Exception as exc:
        raise ValueError(f"search failed on corrupt archive: {exc}") from exc
    latency_ms = round((time.perf_counter() - start) * 1000, 3)
    if not isinstance(raw, dict) or raw.get("error") == "Invalid Format":
        raise ValueError("search: not a Liquefy archive (bad magic)")
    return {
        "found": bool(raw.get("matches")),
        "matches": list(raw.get("matches") or []),
        "latency_ms": latency_ms,
        "method": raw.get("method") or "columnar_grep",
        "error": raw.get("error") or None,
    }


def seal_blob(blob: bytes, key: bytes, *, pcc_root: bytes | None = None) -> bytes:
    """AES-256-GCM seal of an already-compressed blob (tenant-isolated KDF)."""
    layer = NULL_Security_Layer(master_secret=domain_sealed_key(key))
    metadata = {}
    if pcc_root is not None:
        metadata["pcc_root"] = bytes(pcc_root).hex()
    return layer.seal(blob, VOOL_TENANT, metadata)


def unseal_blob(sealed: bytes, key: bytes) -> tuple[bytes, dict]:
    """Unseal; wrong key/tenant or any tampering fails closed (PermissionError)."""
    layer = NULL_Security_Layer(master_secret=domain_sealed_key(key))
    return layer.unseal(sealed, VOOL_TENANT)


__all__ = [
    "CODEC_ID",
    "CODEC_VERSION",
    "DEFAULT_LEVEL",
    "VOOL_TENANT",
    "Commitment",
    "InclusionProof",
    "canonical_jsonl",
    "commit_records",
    "compress_records",
    "decompress",
    "decompress_records",
    "domain_sealed_key",
    "engine",
    "inclusion_proof",
    "seal_blob",
    "search_blob",
    "unseal_blob",
    "verify_disclosure",
    "verify_inclusion",
]
