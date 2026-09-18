"""Sabotage battery: the store's laws must fail LOUD, not silent.

S1 interrupted hot→cold transition (no loss, no duplication)
S2 corruption (bitflip / truncation / header tamper) fails closed + receipted
S3 wrong-key and no-key refusal on sealed segments
S4 seal-verify failure leaves the hot recovery path intact
S5 secrets never reach hot or cold bytes
S6 funnel parity: every event exactly once, across tiers
"""
from __future__ import annotations

import json

import pytest

from core.liquefy import api
from core.liquefy.store import (
    LiquefyLogStore,
    SealVerifyError,
    SegmentCorruptError,
    SegmentKeyError,
    SegmentMissingError,
)

KEY_A = b"operator-key-A-0123456789abcdef"
KEY_B = b"operator-key-B-fedcba9876543210"


def _snapshot_cursor(store: LiquefyLogStore) -> bytes | None:
    return store.cursor_path.read_bytes() if store.cursor_path.exists() else None


def _restore_cursor(store: LiquefyLogStore, snapshot: bytes | None) -> None:
    if snapshot is None:
        store.cursor_path.unlink(missing_ok=True)
    else:
        store.cursor_path.write_bytes(snapshot)
    store._load_cursor()


# ---------------------------------------------------------------- S1 interruption

def test_s1a_crash_before_cursor_advance_serves_hot_then_reseals_idempotently(make_store, sample_events):
    store = make_store()
    store.append(sample_events(20))
    cursor_before = _snapshot_cursor(store)
    hot_before = store.hot_path.read_bytes()  # rotation runs after the cursor write,
    sealed = store.seal()  # so the crash window leaves BOTH the old cursor and the full hot log
    store.hot_path.write_bytes(hot_before)
    _restore_cursor(store, cursor_before)
    assert sealed is not None
    # Every event still readable — from hot, exactly once.
    for seq in range(1, 21):
        assert store.read(seq)["seq"] == seq
    # Re-seal overwrites the orphan under the same content-derived id; no duplication.
    resealed = store.seal()
    assert resealed.segment_id == sealed.segment_id
    exported = []
    store.export(exported_path := store.root / "parity.jsonl")
    exported = [json.loads(line) for line in exported_path.read_text().splitlines()]
    assert [row["seq"] for row in exported] == list(range(1, 21))


def test_s1b_orphan_tmp_files_are_inert(make_store, sample_events):
    store = make_store()
    store.append(sample_events(10))
    tmp = store.segments_dir / ".orphan-segment.tmp-424242"
    tmp.write_bytes(b"VLS1-half-written-garbage")
    sealed = store.seal()
    assert sealed.event_count == 10
    assert store.read(1)["seq"] == 1
    assert tmp.exists()  # gc is retention's job; it never blocks correctness


def test_s1c_crash_after_cursor_before_hot_rotation_no_duplicates(make_store, sample_events):
    store = make_store()
    store.append(sample_events(20))
    store.seal()
    # Simulate the crash: hot still holds the sealed prefix.
    with open(store.hot_path, "a", encoding="utf-8") as handle:
        for seq in range(1, 21):
            env = store.read(seq)
            handle.write(json.dumps(env) + "\n")
    reopened = LiquefyLogStore(store.root)
    assert reopened.read(7)["seq"] == 7  # served from cold, exactly once
    exported = store.root / "parity.jsonl"
    reopened.export(exported)
    seqs = [json.loads(line)["seq"] for line in exported.read_text().splitlines()]
    assert seqs == list(range(1, 21)), f"duplication: {seqs}"
    reopened.seal()  # rotation repairs lazily
    assert reopened.hot_entries() == []


def test_s1d_torn_hot_tail_quarantined_store_survives(make_store, sample_events):
    store = make_store()
    store.append(sample_events(10))
    with open(store.hot_path, "ab") as handle:
        handle.write(b'{"seq": 11, "ts": "2026-09-02T10:00:00+03:00", "kind": "eff')  # torn mid-line
    reopened = LiquefyLogStore(store.root)
    assert [env["seq"] for env in reopened.hot_entries()] == list(range(1, 11))
    quarantine = list((store.root / "receipts").glob("hot-torn-*.jsonl"))
    assert len(quarantine) == 1 and b'"kind": "eff' in quarantine[0].read_bytes()
    seqs = reopened.append([{"kind": "effect_intended", "source": "blackbox", "payload": {"ok": 1}}])
    assert seqs == [11]  # seq continuity preserved


# -------------------------------------------------------------------- S2 corruption

@pytest.fixture()
def sealed_store_with_flipped_blob(make_store, sample_events):
    store = make_store()
    store.append(sample_events(10))
    sealed = store.seal()
    path = store.root / "segments" / f"{sealed.segment_id}.lsegh"
    raw = bytearray(path.read_bytes())
    raw[-5] ^= 0x01  # flip one bit inside the blob region
    path.write_bytes(bytes(raw))
    return store, sealed, path


def test_s2a_bitflip_read_fails_closed_with_receipt(sealed_store_with_flipped_blob):
    store, sealed, _path = sealed_store_with_flipped_blob
    with pytest.raises(SegmentCorruptError) as excinfo:
        store.read(3)
    assert excinfo.value.check in ("blob_sha256", "decompress", "original_sha256")
    receipt_path = store.root / "receipts" / f"{sealed.segment_id}.corrupt.json"
    assert receipt_path.exists()
    receipt = json.loads(receipt_path.read_text())
    assert receipt["served"] is False and receipt["action"] == "retained_read_only"


def test_s2b_search_fails_closed_on_corrupt_segment(sealed_store_with_flipped_blob):
    store, _sealed, _path = sealed_store_with_flipped_blob
    with pytest.raises((SegmentCorruptError, SegmentMissingError)):
        store.search("effect", limit=5)
    with pytest.raises((SegmentCorruptError, SegmentMissingError)):
        store.find(event_id="effect-0000")  # reference retrieval refuses too


def test_s2c_deep_verify_detects_bitflip(sealed_store_with_flipped_blob):
    store, _sealed, _path = sealed_store_with_flipped_blob
    report = store.verify(deep=True)
    assert report["ok"] is False and report["first_bad"] is not None


def test_s2d_truncated_segment_detected(make_store, sample_events):
    store = make_store()
    store.append(sample_events(10))
    sealed = store.seal()
    path = store.root / "segments" / f"{sealed.segment_id}.lsegh"
    raw = path.read_bytes()
    path.write_bytes(raw[: len(raw) // 3])
    with pytest.raises(SegmentCorruptError):
        store.read(2)


def test_s2e_header_hash_tamper_detected(make_store, sample_events):
    store = make_store()
    store.append(sample_events(10))
    sealed = store.seal()
    path = store.root / "segments" / f"{sealed.segment_id}.lsegh"
    raw = path.read_bytes()
    header_len = int.from_bytes(raw[4:8], "big")
    header = json.loads(raw[8 : 8 + header_len])
    header["original_sha256"] = "0" * 64
    fake = json.dumps(header, ensure_ascii=False).encode()
    tampered_len = len(fake).to_bytes(4, "big")
    # cursor records the old segment_hash, so verify() names it even before a read
    path.write_bytes(raw[:4] + tampered_len + fake + raw[8 + header_len :])
    assert store.verify()["ok"] is False
    with pytest.raises(SegmentCorruptError):
        store.read(1)


# ---------------------------------------------------------------- S3 wrong key

def test_s3a_wrong_key_fails_closed_but_hot_readable(make_store, sample_events):
    store = make_store(sealing_key=KEY_A)
    store.append(sample_events(10))
    store.seal()
    store.append(sample_events(3, session="after"))
    wrong = LiquefyLogStore(store.root, sealing_key=KEY_B)
    with pytest.raises(SegmentKeyError):
        wrong.read(1)
    with pytest.raises(SegmentKeyError):
        wrong.search("effect", limit=3)
    assert [env["seq"] for env in wrong.hot_entries()] == [11, 12, 13]
    with pytest.raises(SegmentKeyError):
        LiquefyLogStore(store.root).read(1)  # no key at all also refused


def test_s3b_correct_key_still_reads(make_store, sample_events):
    store = make_store(sealing_key=KEY_A)
    store.append(sample_events(10))
    store.seal()
    reopened = LiquefyLogStore(store.root, sealing_key=KEY_A)
    assert reopened.read(4)["seq"] == 4
    assert reopened.verify(deep=True)["ok"] is True


# ------------------------------------------------- S4 seal-verify failure keeps hot

def test_s4_failed_seal_verification_preserves_hot_recovery_path(make_store, sample_events, monkeypatch):
    store = make_store()
    store.append(sample_events(10))
    real_engine = api.engine()

    class LyingEngine:
        def __getattr__(self, name):
            return getattr(real_engine, name)

        def compress(self, data):
            blob = real_engine.compress(data)
            return blob[:-1] + bytes([blob[-1] ^ 0x55])  # a blob that will not verify

    monkeypatch.setattr(api, "_ENGINE", LyingEngine())
    with pytest.raises(SealVerifyError):
        store.seal()
    monkeypatch.setattr(api, "_ENGINE", real_engine)

    # Layer 2: an engine that emits a VALID archive of reordered rows — the
    # blob/hash/decompress gates all pass; only the canonical compare refuses it.
    class ReorderingEngine:
        def __getattr__(self, name):
            return getattr(real_engine, name)

        def compress(self, data):
            lines = data.decode("utf-8").split("\n")
            return real_engine.compress(("\n".join(lines[1:] + lines[:1])).encode("utf-8"))

    monkeypatch.setattr(api, "_ENGINE", ReorderingEngine())
    with pytest.raises(SealVerifyError):
        store.seal()
    monkeypatch.setattr(api, "_ENGINE", real_engine)
    assert [env["seq"] for env in store.hot_entries()] == list(range(1, 11))

    # Original recovery path intact: every event still readable from hot.
    assert [env["seq"] for env in store.hot_entries()] == list(range(1, 11))
    for seq in range(1, 11):
        assert store.read(seq)["seq"] == seq
    assert store.stats()["last_sealed_seq"] == 0
    assert list(store.segments_dir.glob("*.lsegh")) == []  # no half segment accepted
    # And the store still seals fine once the fault is gone.
    sealed = store.seal()
    assert sealed.event_count == 10


# -------------------------------------------------------------------- S5 secrets

def test_s5_secrets_never_reach_hot_or_cold_bytes(make_store):
    store = make_store()
    store.append(
        [
            {
                "kind": "tool_result",
                "source": "runtime",
                "payload": {
                    "stdout": "configured AKIAIOSFODNN7EXAMPLE ok",
                    "creds": {"password": "hunter2", "api_key": "sk-ant-live-abcdefghij"},
                    "auth": "Bearer real-secret-token-123456",
                },
            }
        ]
    )
    store.seal()
    hot_bytes = store.hot_path.read_bytes()
    cold_files = list((store.root / "segments").glob("*.lsegh"))
    assert cold_files
    cold_bytes = b"".join(path.read_bytes() for path in cold_files)
    for blob in (hot_bytes, cold_bytes):
        assert b"AKIAIOSFODNN7EXAMPLE" not in blob
        assert b"hunter2" not in blob
        assert b"sk-ant-live-abcdefghij" not in blob
        assert b"real-secret-token-123456" not in blob
    served = store.read(1)  # the served envelope (cold now) carries the redaction receipt
    assert served["redactions"]["password"] == 1
    assert served["payload"]["creds"]["password"] == "[REDACTED:password]"
    assert store.search("AKIAIOSFODNN7EXAMPLE")["total"] == 0


# --------------------------------------------------------------- S6 funnel parity

def test_s6_every_event_exactly_once_across_tiers(make_store, sample_events):
    store = make_store(segment_max_events=30)
    for batch in range(4):
        store.append(sample_events(20, session=f"s{batch}", start=batch * 20))
        if batch % 2:
            store.seal()
    store.seal()
    store.append(sample_events(5, session="tail", start=80))
    seen: list[int] = []
    for seq in range(1, 86):
        env = store.read(seq)
        assert env["seq"] == seq
        seen.append(env["seq"])
    assert seen == list(range(1, 86))
    exported = store.root / "parity.jsonl"
    store.export(exported)
    seqs = [json.loads(line)["seq"] for line in exported.read_text().splitlines()]
    assert seqs == list(range(1, 86))


def test_append_refuses_non_dict_entries(make_store):
    store = make_store()
    with pytest.raises(TypeError):
        store.append(["not-a-dict"])  # type: ignore[list-item]
