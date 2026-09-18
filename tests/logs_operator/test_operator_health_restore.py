"""Projection health publication and cursor-law restore/recovery.

Health must make a FAILING projection sink VISIBLE (counted failures, receipts)
while the authoritative journal keeps appending — and the sabotage proof shows
what truth is lost when that publication is removed.

Restore must follow the store's cursor laws: torn hot tails quarantined
verbatim with the good prefix preserved, orphan segments re-sealed under the
same content-derived id, chain verified, and a deterministic export digest for
byte-exact before/after comparison.
"""
from __future__ import annotations

import json

import pytest

from core.liquefy.operator import (
    projection_health,
    restore_projection,
    search_events,
    verify_segments,
)
from core.liquefy.store import LiquefyLogStore, SegmentCorruptError, SegmentKeyError
from tests.logs_operator.conftest import journal_entries, write_events


def _store() -> LiquefyLogStore:
    from core.liquefy import hooks

    return hooks.get_default_store()


# --------------------------------------------------------------------------- health


def test_health_reports_tiers_verification_and_sink_state(seeded):
    store = _store()
    write_events(store, journal_entries()[:4])
    store.seal()
    write_events(store, journal_entries()[4:])
    health = projection_health(blackbox=seeded["store"])
    assert health["authority"]["journal_is_authority"] is True
    assert health["authority"]["projection_is_authority"] is False
    assert health["authority"]["journal_chain"]["ok"] is True
    assert health["tiers"]["cold"]["events"] == 4 and health["tiers"]["hot"]["events"] == 4
    assert health["verification"]["status"] == "verified"
    assert health["sink"]["failure_count"] == 0
    assert health["segments"] and health["segments"][0]["segment_id"]


def test_sink_failure_is_visible_and_never_breaks_the_journal_append(seeded, monkeypatch):
    """The C11 health law: the REAL wired sink (record_blackbox_entry) failing
    on every append is COUNTED and published by health, and the journal keeps
    every entry byte-for-byte."""
    import core.liquefy.hooks as hooks_module

    store = _store()
    journal_before = seeded["store"].journal.entries()

    def _boom(entries):
        raise RuntimeError("projection cold path down")

    original_append = store.append
    monkeypatch.setattr(store, "append", _boom, raising=True)
    monkeypatch.setattr(seeded["store"], "liquefy_sink", hooks_module.record_blackbox_entry, raising=True)
    appended = []
    try:
        for entry in journal_entries()[:3]:
            full = seeded["store"].append(entry)  # the journal append must not raise
            appended.append(full)
    finally:
        monkeypatch.setattr(store, "append", original_append, raising=True)
        monkeypatch.setattr(seeded["store"], "liquefy_sink", None, raising=True)

    assert len(appended) == 3, "a failing projection sink broke the authoritative append"
    assert len(seeded["store"].journal.entries()) == len(journal_before) + 3

    write_events(store, journal_entries()[3:])  # the rest reaches the projection normally
    health = projection_health(blackbox=seeded["store"])
    assert health["sink"]["failure_count"] == 3, "sink failures were not published"
    assert health["sink"]["note"], "the publication must say the failures are non-propagating"
    assert health["authority"]["journal_entries"] == len(journal_before) + 3
    assert health["authority"]["journal_chain"]["ok"] is True
    assert search_events(limit=100)["verification"]["status"] == "verified"


def test_sabotage_failure_health_publication_removed_and_named_red_catches_it(seeded, monkeypatch):
    """SABOTAGE: the pre-C11 shape — a sink failure swallowed WITHOUT counting.
    Sink failures then happen and health still reports zero: the publication is
    the load-bearing guard, and with it removed the truth disappears."""
    import core.liquefy.hooks as hooks_module

    failures_seen = {"n": 0}

    def _uncounting_sink(entry):
        """The pre-C11 shape: try the append, swallow the failure, count nothing."""
        store = hooks_module.get_default_store()
        if store is None:
            return None
        try:
            return store.append(
                [{"kind": str(entry.get("kind") or "blackbox_entry"), "source": "blackbox", "payload": entry}]
            )
        except Exception:
            failures_seen["n"] += 1  # swallowed WITHOUT counting — the sabotage
            return None

    store = _store()

    def _boom(entries):
        raise RuntimeError("projection cold path down")

    monkeypatch.setattr(store, "append", _boom, raising=True)
    monkeypatch.setattr(seeded["store"], "liquefy_sink", _uncounting_sink, raising=True)
    for entry in journal_entries()[:3]:
        seeded["store"].append(entry)

    health = projection_health(blackbox=seeded["store"])
    assert failures_seen["n"] == 3, "sabotage was a no-op: no sink failure actually occurred"
    assert health["sink"]["failure_count"] == 0, (
        "with the counting removed health STILL showed the truth — the sabotage proved nothing"
    )
    projected = projection_health(blackbox=seeded["store"])
    lag = projected["authority"]["journal_entries"] - (
        projected["tiers"]["cold"]["events"] + projected["tiers"]["hot"]["events"]
    )
    assert lag >= 3, "the invisible journal/projection lag was not present to expose"


def test_health_names_corruption_receipts(seeded):
    store = _store()
    write_events(store, journal_entries())
    sealed = store.seal()
    path = store.root / "segments" / f"{sealed.segment_id}.lsegh"
    raw = path.read_bytes()
    path.write_bytes(raw[:-1] + bytes([raw[-1] ^ 0xFF]))
    reopened = LiquefyLogStore(store.root)
    # a cold read hits the receipted verify path: typed failure + persisted receipt
    with pytest.raises(SegmentCorruptError):
        reopened.read(1)
    health = projection_health(store=reopened, blackbox=seeded["store"])
    assert health["verification"]["status"] == "failed"
    assert health["receipts"]["corruption"], "corruption receipts are not published by health"
    receipt = health["receipts"]["corruption"][-1]
    assert receipt["segment_id"] == sealed.segment_id and receipt["served"] is False


# ------------------------------------------------------------------ verify / restore


def test_verify_segments_reports_header_chain_and_disclosure(seeded):
    store = _store()
    write_events(store, journal_entries())
    sealed = store.seal()
    single = verify_segments(segment_id=sealed.segment_id, deep=True, column="kind", store=store)
    assert single["verification"]["status"] == "verified"
    assert single["header"]["segment_id"] == sealed.segment_id
    assert single["chain_contains_segment"] is True
    assert single["deep_read"]["events"] == len(journal_entries())
    assert single["disclosure_verified"] is True, "the claimed column must match the sealed commitment"
    chain = verify_segments(deep=True, store=store)
    assert chain["verification"]["status"] == "verified"
    assert chain["report"]["events"] == len(journal_entries())


def test_verify_fails_closed_on_wrong_key_or_missing_key(seeded):
    store = _store()
    keyed = LiquefyLogStore(store.root, sealing_key=b"k" * 32)
    write_events(keyed, journal_entries())
    sealed = keyed.seal()
    stranger = LiquefyLogStore(store.root, sealing_key=b"z" * 32)
    with pytest.raises(SegmentKeyError):
        verify_segments(segment_id=sealed.segment_id, deep=True, store=stranger)
    keyless = LiquefyLogStore(store.root)
    with pytest.raises(SegmentKeyError):
        verify_segments(segment_id=sealed.segment_id, deep=True, store=keyless)
    # the right key verifies
    owner = LiquefyLogStore(store.root, sealing_key=b"k" * 32)
    assert verify_segments(segment_id=sealed.segment_id, deep=True, store=owner)["verification"]["status"] == "verified"


def test_restore_quarantines_torn_hot_tail_and_keeps_the_good_prefix():
    """A crash mid-append leaves a torn final line: restore quarantines the
    bytes VERBATIM and every complete event survives — byte-exact prefix."""
    store = _store()
    write_events(store, journal_entries()[:4])
    hot_path = store.root / "hot.jsonl"
    good_bytes = hot_path.read_bytes()
    with open(hot_path, "ab") as handle:
        handle.write(b'{"seq": 5, "ts": "2026-09-03T1')  # torn line, no newline

    report = restore_projection(store=store)  # opens a fresh store: constructor repairs
    assert report["repaired_hot_tail"] is True and report["quarantined_tails"]
    quarantine = sorted((store.root / "receipts").glob("hot-torn-*.jsonl"))
    assert quarantine, "the torn bytes were not quarantined as evidence"
    assert good_bytes in quarantine[0].read_bytes(), "quarantine is not verbatim"
    assert report["stats"]["hot_events"] == 4
    assert report["verification"]["status"] == "verified"
    assert (store.root / "hot.jsonl").read_bytes() == good_bytes, "the good prefix was not preserved byte-exact"


def test_restore_reseals_an_orphan_segment_from_still_hot_truth():
    """Crash window: segment file durable, cursor NOT advanced. Restore re-seals
    from the still-hot truth; the content-derived id overwrites the orphan."""
    store = _store()
    write_events(store, journal_entries())
    hot_before_seal = store.hot_entries()
    sealed = store.seal()

    # Rewind the cursor to BEFORE the seal and put the envelopes back in hot:
    # the segment file is now an orphan and hot claims the events — the exact
    # crash window between "segment durable" and "cursor advanced".
    cursor = json.loads((store.root / "cursor.json").read_text())
    cursor["segments"] = []
    cursor["last_sealed_seq"] = 0
    cursor["chain_tip"] = ""
    (store.root / "cursor.json").write_text(json.dumps(cursor, sort_keys=True))
    (store.root / "hot.jsonl").write_text(
        "".join(json.dumps(env, ensure_ascii=False) + "\n" for env in hot_before_seal)
    )

    reopened = LiquefyLogStore(store.root)
    orphan_path = store.root / "segments" / f"{sealed.segment_id}.lsegh"
    assert orphan_path.exists(), "the orphan scenario did not materialise"

    report = restore_projection(store=reopened)
    assert sealed.segment_id in report["orphan_segments"], report["orphan_segments"]
    assert report["resealed"] and report["resealed"]["segment_id"] == sealed.segment_id, (
        "the orphan was not overwritten under its content-derived id"
    )
    assert report["verification"]["status"] == "verified"
    assert report["export"]["events"] == len(journal_entries())


def test_restore_export_digest_is_deterministic_byte_exact():
    """The exported replay is deterministic: two exports of one projection are
    byte-identical, and the same events in a different store root export to the
    same digest — the byte-exact restore property."""
    store = _store()
    write_events(store, journal_entries())
    store.seal()
    write_events(store, journal_entries()[:2])
    first = restore_projection(store=store)
    second = restore_projection(store=store)
    assert first["export"]["sha256"] == second["export"]["sha256"]
    assert first["export"]["events"] == len(journal_entries()) + 2

    twin = LiquefyLogStore(store.root.parent / "twin")
    from core.liquefy.hooks import blackbox_entry_to_event

    twin.append([blackbox_entry_to_event(e) for e in journal_entries()])
    twin.seal()
    twin.append([blackbox_entry_to_event(e) for e in journal_entries()[:2]])
    twin_restore = restore_projection(store=twin)
    assert twin_restore["export"]["sha256"] == first["export"]["sha256"], "restore is not byte-exact across roots"
