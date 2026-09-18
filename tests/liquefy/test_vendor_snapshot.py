"""Vendored OX-LIQUEFY snapshot regression pack.

Ports the upstream test families (test_codec_roundtrip, test_failclosed,
test_pcc, test_security @ OX-LIQUEFY 2936ecc..822bd0b) onto the vendored
modules, via the VOOL api glue. The snapshot rule is "vendored as a snapshot
WITH its tests" (dossier §36.2) — these are the essence of those tests.
"""
from __future__ import annotations

import pytest

from core.liquefy import api
from core.liquefy.vendor.sealing import NULL_Security_Layer


def _records(count: int) -> list[dict]:
    return [
        {
            "seq": i,
            "kind": ["model_call", "tool_call", "tool_result", "error"][i % 4],
            "path": f"/workspace/f{i % 5}.txt",
            "n": i,
            "ts": f"2026-09-0{i % 9}T10:00:0{i % 10}+03:00",
            "nested": {"a": i % 3, "z": None},
            "uni": "ünïcødé ✓ 日本語",
            "big": 12345 * i,
        }
        for i in range(1, count + 1)
    ]


def test_codec_roundtrip_value_exact_incl_tz_nested_unicode():
    records = _records(500)
    blob = api.compress_records(records)
    recovered = api.decompress_records(blob)
    assert recovered == records


def test_codec_roundtrip_byte_exact_on_canonical_form():
    records = _records(300)
    canonical = api.canonical_jsonl(records)
    blob = api.engine().compress(canonical)
    assert api.canonical_jsonl(api.decompress_records(blob)) == canonical


def test_codec_compression_is_deterministic():
    records = _records(200)
    assert api.compress_records(records) == api.compress_records(records)


def test_failclosed_bad_magic_raises_never_returns_empty():
    with pytest.raises(ValueError):
        api.decompress(b"NOPE-not-a-liquefy-archive")
    with pytest.raises(ValueError):
        api.decompress(b"")


def test_failclosed_truncated_archive_raises():
    blob = api.compress_records(_records(100))
    with pytest.raises(ValueError):
        api.decompress(blob[: len(blob) // 2])


def test_search_without_full_decompression_finds_rows():
    records = _records(400)
    blob = api.compress_records(records)
    result = api.search_blob(blob, "/workspace/f3.txt")
    assert result["found"] is True
    expected = {i for i, record in enumerate(records) if record["path"] == "/workspace/f3.txt"}
    assert expected.issubset(set(result["matches"]))
    assert result["method"] in ("columnar_grep", "fallback_full")


def test_search_corrupt_archive_raises_not_clean_miss():
    blob = api.compress_records(_records(50))
    corrupt = b"COL2" + blob[4:200] + b"garbage"
    with pytest.raises(ValueError):
        api.search_blob(corrupt, "anything")


def test_pcc_commit_and_disclosure_roundtrip():
    from core.liquefy.vendor.pcc import _zone_for, inclusion_proof, verify_disclosure

    records = _records(120)
    commitment = api.commit_records(records)
    assert len(commitment.root) == 32
    column = commitment.column_names()[0]
    values = [record.get(column) for record in records]
    proof = inclusion_proof(commitment, column)
    assert verify_disclosure(commitment.root, column, _zone_for(values), values, proof) is True
    lying = list(values)
    lying[3] = "TAMPERED" if lying[3] != "TAMPERED" else "tampered-2"
    assert verify_disclosure(commitment.root, column, _zone_for(lying), lying, proof) is False


def test_security_seal_roundtrip_and_wrong_key_fails_closed():
    key = b"operator-held-key-0123456789ab"
    layer = NULL_Security_Layer(master_secret=api.domain_sealed_key(key))
    payload = b"compressed-bytes-here"
    sealed = layer.seal(payload, api.VOOL_TENANT, {})
    got, _meta = layer.unseal(sealed, api.VOOL_TENANT)
    assert got == payload
    wrong = NULL_Security_Layer(master_secret=api.domain_sealed_key(b"entirely-different-key-99"))
    with pytest.raises(PermissionError):
        wrong.unseal(sealed, api.VOOL_TENANT)


def test_security_no_default_key_ever():
    with pytest.raises(ValueError):
        NULL_Security_Layer(master_secret=None)
    with pytest.raises(ValueError):
        NULL_Security_Layer(master_secret="")


def test_sealed_blob_refuses_tampering():
    key = b"operator-held-key-0123456789ab"
    sealed = api.seal_blob(api.compress_records(_records(30)), key)
    tampered = sealed[:-3] + bytes([sealed[-3] ^ 0xFF]) + sealed[-2:]
    with pytest.raises(PermissionError):
        api.unseal_blob(tampered, key)
