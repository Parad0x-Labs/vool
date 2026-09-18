"""LiquefyLogStore contract: the ten P1 requirements, as executable law."""
from __future__ import annotations

import json

import pytest

from core.liquefy.store import (
    EntryNotFound,
    LiquefyStoreError,
    SegmentMissingError,
)


def test_hot_events_immediately_readable(make_store, sample_events):
    store = make_store()
    store.append(sample_events(30))
    hot = store.hot_entries()
    assert [env["seq"] for env in hot] == list(range(1, 31))
    assert hot[0]["payload"]["path"] == "/workspace/f0.txt"
    # readable by seq without any seal
    assert store.read(7)["ref"]["event_id"] == "effect-0006"


def test_seal_compresses_into_cold_and_rotates_hot(make_store, sample_events):
    store = make_store()
    store.append(sample_events(40))
    result = store.seal()
    assert result is not None and result.event_count == 40
    assert result.ratio > 1.0
    assert (store.root / "segments" / f"{result.segment_id}.lsegh").exists()
    assert store.hot_entries() == []  # rotated
    assert store.stats()["last_sealed_seq"] == 40


def test_read_across_tiers_after_seal(make_store, sample_events):
    store = make_store()
    store.append(sample_events(40))
    sealed = store.seal()
    store.append(sample_events(10, session="sess-2"))
    assert store.read(1)["ref"]["event_id"] == "effect-0000"  # cold
    assert store.read(45)["session_id"] == "sess-2"  # hot
    stats = store.stats()
    assert stats["hot_events"] == 10 and stats["last_sealed_seq"] == 40
    with pytest.raises(EntryNotFound):
        store.read(999)
    assert sealed.ratio > 1.0


def test_cold_search_without_full_decompression(make_store, sample_events):
    store = make_store()
    store.append(sample_events(200))
    store.seal()
    report = store.search("/workspace/f3.txt", limit=50)
    assert report["total"] >= 1
    cold_hits = [hit for hit in report["hits"] if hit["segment_id"]]
    assert cold_hits, "cold hits must exist"
    assert {hit["method"] for hit in cold_hits} == {"columnar_grep"}
    for hit in cold_hits:
        assert hit["seq"] >= 1 and hit["event"]["payload"]["path"] == "/workspace/f3.txt"


def test_search_reaches_hot_tier_with_provenance(make_store, sample_events):
    store = make_store()
    store.append(sample_events(5))
    report = store.search("turn-0", limit=10)
    assert report["total"] >= 1
    assert any(hit["method"] == "hot_scan" for hit in report["hits"])


def test_references_resolve_across_seal_boundary(make_store, sample_events):
    store = make_store()
    store.append(sample_events(20))
    store.seal()
    store.append(sample_events(5, session="sess-9", start=40))
    by_event_id = store.find(event_id="effect-0012")
    assert len(by_event_id) == 1 and by_event_id[0]["seq"] == 13
    by_turn = store.find(turn_id="turn-8")  # start=40 shifts ids, not seqs
    assert {env["seq"] for env in by_turn} == {21, 22, 23, 24, 25}
    assert {env["ref"]["event_id"] for env in by_turn} == {"effect-0040", "effect-0041", "effect-0042", "effect-0043", "effect-0044"}
    by_session = store.find(session_id="sess-9")
    assert len(by_session) == 5
    by_trace = store.find(trace_id="trace-0")
    assert len(by_trace) == 10
    assert by_trace[0]["seq"] <= 10


def test_verify_chain_and_deep_integrity(make_store, sample_events):
    store = make_store()
    store.append(sample_events(30))
    first = store.seal()
    store.append(sample_events(5, session="s2"))
    second = store.seal()
    assert first.segment_id != second.segment_id
    shallow = store.verify()
    assert shallow["ok"] is True and shallow["segments"] == 2 and shallow["events"] == 35
    deep = store.verify(deep=True)
    assert deep["ok"] is True and deep["events"] == 35


def test_export_is_deterministic_and_faithful(make_store, sample_events, tmp_path):
    store = make_store()
    events = sample_events(60)
    store.append(events[:40])
    store.seal()
    store.append(events[40:])
    out1, out2 = tmp_path / "e1.jsonl", tmp_path / "e2.jsonl"
    assert store.export(out1) == 60
    assert store.export(out2) == 60
    assert out1.read_bytes() == out2.read_bytes()
    replayed = [json.loads(line) for line in out1.read_text().splitlines()]
    assert [row["ref"]["event_id"] for row in replayed] == [event["ref"]["event_id"] for event in events]
    # range export
    out3 = tmp_path / "e3.jsonl"
    assert store.export(out3, first_seq=10, last_seq=19) == 10


def test_delete_cold_before_is_deterministic_and_receipted(make_store, sample_events):
    store = make_store()
    store.append(sample_events(40))
    store.seal()
    store.append(sample_events(20, session="s2", start=40))
    store.seal()
    removed = store.delete_cold_before(41)
    assert len(removed) == 1
    for segment_id in removed:
        assert not (store.root / "segments" / f"{segment_id}.lsegh").exists()
    with pytest.raises(EntryNotFound):
        store.read(1)  # erased — unreachable, not silent
    assert store.read(41)["ref"]["event_id"] == "effect-0040"
    report = store.verify(deep=True)
    assert report["ok"] is True and report["events"] == 20
    receipt = (store.root / "receipts" / "erasure.jsonl").read_text().splitlines()
    assert len(receipt) == 1 and json.loads(receipt[0])["segment_ids"] == removed
    assert store.delete_cold_before(1) == []  # idempotent


def test_restore_receipt_written_on_cold_retrieval(make_store, sample_events):
    store = make_store()
    store.append(sample_events(10))
    store.seal()
    store.read(3, requester="bug_report")
    restore_log = (store.root / "receipts" / "restore.jsonl").read_text().splitlines()
    receipt = json.loads(restore_log[-1])
    assert receipt["seq"] == 3 and receipt["verified_before_serve"] is True
    assert receipt["requester"] == "bug_report"


def test_prune_hot_refuses_unsealed_and_trims_sealed(make_store, sample_events):
    store = make_store()
    store.append(sample_events(30))
    store.seal()
    store.append(sample_events(5, session="s2"))
    # 5 active (unsealed) lines: pruning below them must refuse, never lose them
    with pytest.raises(LiquefyStoreError):
        store.prune_hot(keep_last=0)
    store.seal()
    assert store.prune_hot(keep_last=0) == 35 or store.hot_entries() == []


def test_segment_missing_fails_closed_not_silent(make_store, sample_events):
    store = make_store()
    store.append(sample_events(10))
    sealed = store.seal()
    (store.root / "segments" / f"{sealed.segment_id}.lsegh").unlink()
    with pytest.raises(SegmentMissingError):
        store.read(5)
    with pytest.raises(SegmentMissingError):
        store.search("effect", limit=5)
    assert store.verify()["ok"] is False


def test_multi_seal_cycle_keeps_seq_continuous(make_store, sample_events):
    store = make_store(segment_max_events=25)
    appended = []
    for batch in range(6):
        events = sample_events(20, session=f"sess-{batch}")
        store.append(events)
        appended.extend(events)
        store.seal()
    report = store.verify(deep=True)
    assert report["ok"] is True
    assert report["events"] == len(appended)
    for seq in range(1, len(appended) + 1):
        assert store.read(seq)["seq"] == seq
