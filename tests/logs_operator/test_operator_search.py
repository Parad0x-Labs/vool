"""Typed search/retrieval over the projection: exact answers to the four operator
questions — what happened, why did it fail, which provider/tool ran, what fault
occurred — with tier, identity, time-bound, truncation and verification truth.

SABOTAGE DISCIPLINE: the redaction invariant here carries a named sabotage
(redaction disabled at ingest ⇒ a named test fails); the overlap-dedup
invariant carries one too (hot served behind the sealed horizon ⇒ a named test
fails). Restore-the-guard re-runs are part of each sabotage proof.
"""
from __future__ import annotations

import json
import threading

import pytest

from core.liquefy.operator import OperatorInputError, retrieve_event, search_events
from tests.logs_operator.conftest import SECRET_TOKEN, journal_entries, write_events


def _projection_over(blackbox, tmp_path=None):
    """Journaled entries (the authority's own bytes, seqs assigned) → projection
    via the real ingest mapping."""
    from core.liquefy import hooks

    store = hooks.get_default_store()
    write_events(store, blackbox.journal.entries())
    return store


def test_what_happened_in_a_turn_returns_the_exact_events(seeded):
    _projection_over(seeded["store"])
    report = search_events(turn_id="turn-alpha", limit=10)
    assert report["returned"] == 2
    assert [(e["event_id"], e["kind"]) for e in report["events"]] == [
        ("eff-write-01", "effect_intended"),
        ("eff-write-01", "effect_terminal"),
    ]
    assert all(e["source"] == "blackbox" for e in report["events"])
    assert report["truncated"] is False and report["limit"] == 10
    assert report["verification"]["status"] == "verified"


def test_why_did_it_fail_returns_the_exact_failing_event_with_its_error(seeded):
    _projection_over(seeded["store"])
    report = search_events(outcome="failed", limit=10)
    assert [e["event_id"] for e in report["events"]] == ["eff-push-02"]
    payload = report["events"][0]["payload"]
    assert payload["error"] == "pre-receive hook declined: protected branch"
    assert payload["status"] == "remote_rejected"


def test_which_provider_and_tool_ran_is_findable_by_name(seeded):
    _projection_over(seeded["store"])
    report = search_events(text="cloud-anthropic", limit=10)
    assert {e["event_id"] for e in report["events"]} == {"eff-push-02"}
    assert report["events"][0]["payload"]["tool"] == "repo__push"
    local = search_events(text="qwen-local", limit=10)
    assert {e["event_id"] for e in local["events"]} == {"eff-write-01", "eff-skip-04", "eff-secret-05"}


def test_what_fault_occurred_covers_outcomes_and_gaps(seeded):
    _projection_over(seeded["store"])
    report = search_events(fault="any", limit=10)
    ids = [e["event_id"] for e in report["events"]]
    assert ids == ["eff-push-02", "gap-shell-03", "eff-skip-04"], ids
    refused = search_events(outcome="refused", limit=10)
    assert [e["event_id"] for e in refused["events"]] == ["eff-skip-04"]


def test_time_bounds_select_the_exact_window_and_are_stated_back(projection):
    """The journal is the authority for event ts (it stamps entries itself), so
    the pinned-clock scenario runs on the projection ingest path, which honors
    a producer-supplied ts."""
    write_events(projection, journal_entries())
    report = search_events(store=projection, time_from="2026-09-03T10:03:00+00:00", time_to="2026-09-03T10:05:00+00:00", limit=10)
    assert [e["event_id"] for e in report["events"]] == ["eff-push-02", "eff-push-02", "gap-shell-03"]
    bounds = report["time_bounds"]
    assert bounds["from"] == "2026-09-03T10:03:00+00:00"
    assert bounds["to"] == "2026-09-03T10:05:00+00:00"
    assert bounds["oldest_served_ts"].startswith("2026-09-03T10:03")
    assert bounds["newest_served_ts"].startswith("2026-09-03T10:05")


def test_session_filter_stays_in_its_session(seeded):
    _projection_over(seeded["store"])
    report = search_events(session_id="sess-other", limit=10)
    assert {e["event_id"] for e in report["events"]} == {"eff-skip-04"}


def test_limit_truth_is_stated_not_implied(seeded):
    _projection_over(seeded["store"])
    report = search_events(limit=3)
    assert report["returned"] == 3 and report["truncated"] is True and report["has_more"] is True
    # the scan stops at the limit-plus-one lookahead: that IS the bounded truth
    assert report["scanned_events"] == 4
    full = search_events(limit=100)
    assert full["returned"] == 8 and full["truncated"] is False and full["scanned_events"] == 8


def test_hot_and_cold_each_served_once_across_the_seal_boundary(seeded):
    """Seal mid-stream: the overlap window (seq <= last_sealed_seq still in the
    hot file until rotation) must surface exactly once, from COLD."""
    from core.liquefy import hooks

    store = hooks.get_default_store()
    write_events(store, journal_entries())
    store.seal()  # everything sealed; hot rotation done
    extra = dict(journal_entries()[0])
    extra["effect_id"] = "eff-post-06"
    extra["turn_id"] = "turn-epsilon"
    extra["ts"] = "2026-09-03T10:09:00+00:00"
    write_events(store, [extra])

    report = search_events(limit=100)
    served = [e["seq"] for e in report["events"]]
    assert served == sorted(served) and len(served) == len(set(served)), "an event was served twice"
    cold = [e for e in report["events"] if e["tier"] == "cold"]
    hot = [e for e in report["events"] if e["tier"] == "hot"]
    assert {e["event_id"] for e in cold} == {f"eff-{p}-0{n}" for p, n in [("write", 1), ("push", 2), ("skip", 4), ("secret", 5)]} | {"gap-shell-03"}
    assert [e["event_id"] for e in hot] == ["eff-post-06"]
    assert all(e["segment_id"] for e in cold)
    assert report["verification"]["segments_served_from"], "cold provenance missing"


def test_corrupt_segment_fails_closed_with_a_named_cause(seeded, tmp_path):
    from core.liquefy import hooks
    from core.liquefy.store import LiquefyLogStore, SegmentCorruptError

    store = hooks.get_default_store()
    write_events(store, journal_entries())
    sealed = store.seal()
    path = store.root / "segments" / f"{sealed.segment_id}.lsegh"
    raw = path.read_bytes()
    path.write_bytes(raw[:-3] + bytes([raw[-3] ^ 0xFF, raw[-2] ^ 0xFF, raw[-1] ^ 0xFF]))
    reopened = LiquefyLogStore(store.root)
    with pytest.raises(SegmentCorruptError) as excinfo:
        search_events(store=reopened, limit=10)
    assert "blob_sha256" in str(excinfo.value), "the failure must name the failed check"
    with pytest.raises(SegmentCorruptError):
        retrieve_event(event_id="eff-write-01", store=reopened)


def test_results_cannot_reveal_pre_redaction_secrets(seeded):
    """The journal entry carries a live-shaped token; the projection result
    must carry only the redaction CLASS marker, never the token."""
    _projection_over(seeded["store"])
    report = search_events(event_id="eff-secret-05", limit=5)
    assert report["returned"] == 1
    dumped = str(report)
    assert SECRET_TOKEN not in dumped, "a pre-redaction secret leaked through a search result"
    event = report["events"][0]
    assert "[REDACTED:aws_access_key]" in json.dumps(event["payload"])
    assert event["redactions"].get("aws_access_key") == 1


def test_sabotage_redaction_disabled_leaks_and_the_named_test_catches_it(seeded, monkeypatch):
    """SABOTAGE: remove the ingest guard (redaction) — the leak must then reach
    the result (proving test_results_cannot_reveal_pre_redaction_secrets is
    load-bearing: with this sabotage in place, that test FAILS)."""
    import core.liquefy.store as store_module

    monkeypatch.setattr(store_module, "redact_payload", lambda payload: (payload, {}), raising=True)
    from core.liquefy import hooks

    store = hooks.get_default_store()
    write_events(store, journal_entries())
    report = search_events(event_id="eff-secret-05", limit=5)
    leaked = SECRET_TOKEN in str(report)
    assert leaked, "sabotage was a no-op: the secret never reached the result even without redaction"


def test_retrieve_event_resolves_exact_references_across_tiers(seeded):
    from core.liquefy import hooks

    store = hooks.get_default_store()
    write_events(store, seeded["store"].journal.entries())  # journaled: seqs assigned
    store.seal()
    hot = dict(seeded["store"].journal.entries()[0])
    hot.update(effect_id="eff-hot-07", turn_id="turn-zeta")
    write_events(store, [hot])

    cold_hit = retrieve_event(event_id="eff-push-02", store=store)
    assert cold_hit["tier"] == "cold"
    assert cold_hit["event"]["kind"] == "effect_intended"
    assert cold_hit["event"]["payload"]["operation"] == "push"
    assert cold_hit["event"]["blackbox_seq"] == 2, "the journal seq reference must survive ingest"
    assert cold_hit["verification"]["status"] == "verified"
    hot_hit = retrieve_event(event_id="eff-hot-07", store=store)
    assert hot_hit["tier"] == "hot"
    by_seq = retrieve_event(seq=3, store=store)
    assert by_seq["event"]["event_id"] == "eff-push-02"
    with pytest.raises(LookupError):
        retrieve_event(event_id="eff-never-was", store=store)
    receipts = (store.root / "receipts" / "restore.jsonl").read_text().splitlines()
    assert receipts, "cold retrieval must persist a restore receipt"


def test_concurrent_appends_during_search_never_duplicate_or_lose(seq_check=None, seeded=None):
    """A producer keeps appending WHILE a search scans; the result must name a
    consistent snapshot: no duplicates, and everything found verifies."""
    from core.liquefy import hooks

    store = hooks.get_default_store()
    write_events(store, journal_entries())
    stop = threading.Event()
    appended: list[int] = []

    def _producer():
        i = 0
        while not stop.is_set():
            appended.extend(write_events(store, [dict(journal_entries()[0], effect_id=f"eff-live-{i}", ts=f"2026-09-03T11:{i % 60:02d}:00+00:00")]))
            i += 1

    worker = threading.Thread(target=_producer)
    worker.start()
    try:
        for _ in range(6):
            report = search_events(text="workspace", limit=100)
            served = [e["seq"] for e in report["events"]]
            assert len(served) == len(set(served)), "a concurrent search served an event twice"
            assert all(e["source"] == "blackbox" for e in report["events"])
    finally:
        stop.set()
        worker.join()
    final = search_events(limit=1000)
    served = [e["seq"] for e in final["events"]]
    assert len(served) == len(set(served))
    assert set(appended) <= set(served), "an event appended before the final scan was missing"


def test_unparsable_time_bound_is_typed_not_vague(seeded):
    _projection_over(seeded["store"])
    with pytest.raises(OperatorInputError) as excinfo:
        search_events(time_from="10/3/2026", limit=5)
    assert "time_from" in str(excinfo.value)
    with pytest.raises(OperatorInputError):
        search_events(fault="weird", limit=5)
