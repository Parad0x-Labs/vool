"""The additive projection: Blackbox hooks + retrieval by retained references.

Laws under test:
  - The Blackbox journal remains byte-for-byte the authority with the sink on
    or off (compression is storage optimization, never authority).
  - A projection failure can never break the recorder.
  - Every consumer-retained reference (blackbox seq, effect_id, turn_id) keeps
    resolving after hot→cold migration.
  - Bug-report/Activity-style reference classes resolve through the same door.
"""
from __future__ import annotations

import json

import pytest

from core.blackbox.store import BlackboxStore, default_store, reset_default_store
from core.liquefy import hooks


@pytest.fixture()
def blackbox_pair(tmp_path, monkeypatch):
    """Two Blackbox stores over identical roots: one raw, one sink-wired."""
    monkeypatch.setenv("VOOL_BLACKBOX_DIR", str(tmp_path / "bb"))
    reset_default_store()
    wired = default_store()
    assert wired.liquefy_sink is not None, "default_store must wire the projection sink"
    raw = BlackboxStore(tmp_path / "bb2")
    assert raw.liquefy_sink is None
    yield wired, raw
    reset_default_store()


def _entry(kind: str, effect_id: str, turn_id: str, path: str) -> dict:
    return {
        "kind": kind,
        "effect_id": effect_id,
        "turn_id": turn_id,
        "path": path,
        "root": "/workspace",
        "operation": "write",
        "before": {"exists": False},
        "after": {"exists": True, "sha256": "ab" * 16, "size": 5},
        "session_id": "sess-bb-1",
        "trace_id": "trace-bb-1",
    }


def test_blackbox_projection_records_and_resolves_references(blackbox_pair):
    wired, _raw = blackbox_pair
    stored = wired.append(_entry("effect_intended", "eff-1", "turn-1", "/w/a.txt"))
    wired.append(_entry("effect_terminal", "eff-1", "turn-1", "/w/a.txt"))
    store = hooks.get_default_store()
    assert store is not None
    hits = store.find(event_id="eff-1")
    assert len(hits) == 2
    assert hits[0]["payload"]["path"] == "/w/a.txt"
    # blackbox journal seq resolves through the projection door
    resolved = hooks.retrieve_reference(turn_id="turn-1")
    assert resolved is not None and resolved["ref"]["blackbox_seq"] == stored["seq"]
    by_bb_seq = hooks.retrieve_reference(event_id="eff-1")
    assert by_bb_seq is not None


def test_sink_observes_only_already_durable_journal_truth(blackbox_pair):
    """Additivity law: the sink runs AFTER the journal line is durable and can
    only observe finalized truth — nothing the projection does can alter what
    the authority recorded (or when)."""
    wired, _raw = blackbox_pair
    observed = {}

    def observing_sink(entry):
        observed["at_sink_time"] = (wired.root / "journal.jsonl").read_text().splitlines()
        observed["entry"] = dict(entry)

    wired.liquefy_sink = observing_sink
    stored = wired.append(_entry("effect_intended", "eff-2", "turn-2", "/w/b.txt"))
    lines = observed["at_sink_time"]
    assert len(lines) == 1  # line was durable BEFORE the sink ran
    on_disk = json.loads(lines[0])
    assert on_disk["entry_hash"] == stored["entry_hash"] == observed["entry"]["entry_hash"]
    assert on_disk["mac"] == stored["mac"]  # finalized, signed truth — not sink-authored
    tail = json.loads((wired.root / "journal.jsonl").read_text().splitlines()[-1])
    assert tail == stored  # and the journal tail is exactly what append returned


def test_projection_failure_never_breaks_recorder(blackbox_pair):
    wired, _raw = blackbox_pair

    def exploding_sink(_entry):
        raise RuntimeError("projection is down")

    wired.liquefy_sink = exploding_sink
    stored = wired.append(_entry("effect_intended", "eff-3", "turn-3", "/w/c.txt"))
    assert stored["kind"] == "effect_intended" and stored["seq"] == 0
    assert (wired.root / "journal.jsonl").exists()


def test_disabled_lane_means_no_projection(blackbox_pair, monkeypatch):
    monkeypatch.setenv("VOOL_LIQUEFY_LOGS", "0")
    hooks.reset_default_store()
    assert hooks.get_default_store() is None
    assert hooks.record_event(kind="k", source="s", payload={}) is None
    assert hooks.retrieve_reference(seq=1) is None


def test_sealed_projection_still_serves_blackbox_references(blackbox_pair):
    wired, _raw = blackbox_pair
    wired.append(_entry("effect_terminal", "eff-9", "turn-9", "/w/z.txt"))
    store = hooks.get_default_store()
    store.seal()
    hit = hooks.retrieve_reference(event_id="eff-9")
    assert hit is not None and hit["payload"]["path"] == "/w/z.txt"
    assert hit["seq"] <= store.stats()["last_sealed_seq"]  # genuinely cold-served


def test_secret_in_blackbox_entry_never_reaches_projection(blackbox_pair):
    wired, _raw = blackbox_pair
    entry = _entry("effect_intended", "eff-secret", "turn-secret", "/w/.env")
    entry["before"] = {"exists": True, "sha256": "cd" * 16, "size": 4, "preview": "AWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI"}
    wired.append(entry)
    store = hooks.get_default_store()
    env = store.hot_entries()[0]
    assert "wJalrXUtnFEMI" not in json.dumps(env)
    assert env["redactions"]  # the admission gate fired and was receipted


def test_bug_report_and_activity_reference_classes_resolve(blackbox_pair):
    """Consumers that hold (trace_id, kind) or (session_id, kind) pairs — the
    bug-report and Activity reference classes — resolve without full scans."""
    wired, _raw = blackbox_pair
    for i in range(4):
        wired.append(_entry("coverage_gap" if i % 2 else "effect_intended", f"eff-{i}", f"turn-{i}", f"/w/{i}.txt"))
    store = hooks.get_default_store()
    gaps = store.find(kind="coverage_gap", session_id="sess-bb-1")
    assert [env["ref"]["event_id"] for env in gaps] == ["eff-1", "eff-3"]
    trace_hits = store.find(trace_id="trace-bb-1", kind="effect_intended")
    assert [env["ref"]["event_id"] for env in trace_hits] == ["eff-0", "eff-2"]
