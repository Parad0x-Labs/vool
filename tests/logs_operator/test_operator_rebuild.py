"""Rebuild of the projection from the authoritative journal.

The laws under test: read-only over the journal (byte witness), idempotent
(no duplicate events on re-run), resumable (state survives interruption and a
restart finishes without duplication), bounded (max_entries caps an invocation)
and cancellable (an operator cancel stops cleanly with a resume point).

Dedup key note: a journal turn has TWO entries (intended + terminal) sharing
one effect_id, so duplication is judged on (event_id, blackbox_seq) pairs —
every journal seq projected exactly once.
SABOTAGE: the dedup boundary carries a named red — with dedup removed (and
resume off), the named invariant fails and the projection shows doubled seqs.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path

from core.liquefy.operator import rebuild_projection, search_events
from tests.logs_operator.conftest import journal_entries, read_jsonl

EXPECTED_PAIRS = sorted((e["effect_id"], i) for i, e in enumerate(journal_entries()))  # journal seqs are 0-based


def _projection_root() -> Path:
    from core.liquefy import hooks

    return Path(hooks.default_root())


def _projected_pairs() -> list[tuple[str, int]]:
    from core.liquefy.operator import _iter_events
    from core.liquefy.store import LiquefyLogStore

    store = LiquefyLogStore(_projection_root())
    pairs = []
    for _tier, _seg, env in _iter_events(store):
        ref = env.get("ref") or {}
        pairs.append((str(ref.get("event_id")), int(ref["blackbox_seq"])))
    return pairs


def test_rebuild_projects_every_journal_event_exactly(seeded):
    report = rebuild_projection(blackbox=seeded["store"])
    assert report["status"] == "complete"
    assert report["appended"] == len(journal_entries())
    assert report["journal_unchanged_witness"] is True
    assert sorted(_projected_pairs()) == EXPECTED_PAIRS
    report_view = search_events(limit=1000)
    assert report_view["verification"]["status"] == "verified"


def test_rebuild_is_idempotent_no_duplicates_on_rerun(seeded):
    first = rebuild_projection(blackbox=seeded["store"])
    assert first["appended"] == len(journal_entries())
    second = rebuild_projection(blackbox=seeded["store"])
    assert second["appended"] == 0
    assert second["skipped_resumed"] == len(journal_entries()), "the resume guard did the skipping"
    # the dedup SET is the second, independent guard: with resume disabled every
    # entry is re-examined and skipped by seq
    third = rebuild_projection(blackbox=seeded["store"], resume=False)
    assert third["appended"] == 0
    assert third["skipped_already_projected"] == len(journal_entries())
    assert len(_projected_pairs()) == len(journal_entries()), "the rebuild duplicated events on re-run"


def test_sabotage_dedup_boundary_removed_duplicates_and_named_red_catches_it(seeded, monkeypatch):
    """SABOTAGE: remove the dedup boundary (treat every journal seq as new) AND
    disable resume so the entries are re-examined. The named invariant — every
    journal seq projected exactly once — must FAIL while the guard is gone."""
    import core.liquefy.operator as operator_module

    monkeypatch.setattr(operator_module, "_existing_blackbox_seqs", lambda store: set(), raising=True)
    rebuild_projection(blackbox=seeded["store"], resume=False)
    rebuild_projection(blackbox=seeded["store"], resume=False)
    pairs = _projected_pairs()
    duplicated = len(pairs) != len(set(pairs))
    assert duplicated, "sabotage was a no-op: dedup removal did not duplicate events"


def test_rebuild_is_bounded_by_max_entries_and_resumes(seeded):
    bounded = rebuild_projection(blackbox=seeded["store"], max_entries=3, batch_size=2)
    assert bounded["status"] == "bounded"
    assert bounded["examined"] == 3 and bounded["appended"] == 3, "everything examined must be projected"
    resumed = rebuild_projection(blackbox=seeded["store"])
    assert resumed["status"] == "complete"
    assert resumed["skipped_resumed"] == 3
    assert resumed["appended"] == len(journal_entries()) - 3
    assert sorted(_projected_pairs()) == EXPECTED_PAIRS


def test_operator_cancellation_stops_cleanly_and_resumes_without_duplicates(seeded):
    """The operator's cancel signal is checked BETWEEN batches: the stop is
    clean, examined work is flushed, and completion adds only the remainder."""
    calls = {"n": 0}

    def _cancel() -> bool:
        calls["n"] += 1
        return True  # cancel right after the FIRST batch lands

    cancelled = rebuild_projection(blackbox=seeded["store"], batch_size=2, cancel=_cancel)
    assert cancelled["status"] == "cancelled", cancelled
    partial = _projected_pairs()
    assert 0 < len(partial) < len(journal_entries()), partial
    assert len(partial) == len(set(partial))

    finished = rebuild_projection(blackbox=seeded["store"])
    assert finished["status"] == "complete" and finished["appended"] > 0
    assert sorted(_projected_pairs()) == EXPECTED_PAIRS, "resume duplicated or lost events after cancellation"


def test_interruption_and_restart_finish_without_duplication(seeded):
    """A rebuild killed MID-RUN (real SIGKILL of a subprocess) leaves a
    consistent projection; a restarted rebuild completes with no duplicates."""
    script = (
        "import sys, time\n"
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[2])!r})\n"
        "from core.blackbox.store import BlackboxStore\n"
        "from core.liquefy.operator import rebuild_projection\n"
        "bb = BlackboxStore(%r)\n"
        "print('rebuilt', rebuild_projection(blackbox=bb, batch_size=1)['appended'])\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n" % str(seeded["store"].root)
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_env(seeded["store"].root),
    )
    try:
        proc.stdout.readline()  # the child got through at least one batch
        proc.kill()
        proc.wait(timeout=10)
    finally:
        if proc.poll() is None:
            proc.kill()

    report = rebuild_projection(blackbox=seeded["store"])
    assert report["status"] == "complete"
    assert sorted(_projected_pairs()) == EXPECTED_PAIRS, "restart after SIGKILL duplicated or lost events"


def _child_env(blackbox_root: Path) -> dict:
    import os

    from core.liquefy import hooks

    env = dict(os.environ)
    env.update(
        {
            "VOOL_BLACKBOX_DIR": str(blackbox_root),
            "VOOL_LIQUEFY_LOGS": "1",
            "VOOL_LIQUEFY_LOGS_HOME": hooks.default_root(),
            "VOOL_KEY_STORAGE_MODE": "file",
            "VOOL_KEY_PASSPHRASE": "logs-operator-test",
        }
    )
    return env


def test_journal_bytes_are_untouched_by_rebuild(seeded):
    journal_path = seeded["store"].root / "journal.jsonl"
    before = journal_path.read_bytes()
    head_before = (seeded["store"].root / "HEAD").read_bytes()
    rebuild_projection(blackbox=seeded["store"])
    rebuild_projection(blackbox=seeded["store"], resume=False)
    assert journal_path.read_bytes() == before, "the rebuild wrote to the authoritative journal"
    assert (seeded["store"].root / "HEAD").read_bytes() == head_before


def test_concurrent_journal_appends_during_rebuild_are_never_duplicated(seeded):
    """A producer keeps journaling WHILE the rebuild scans; a follow-up rebuild
    picks the newcomers up exactly once."""
    stop = threading.Event()

    def _producer():
        i = 0
        while not stop.is_set():
            seeded["store"].append(
                {
                    "schema": "blackbox_effect_v1", "kind": "effect_intended",
                    "effect_id": f"eff-live-{i}", "turn_id": f"turn-live-{i}",
                    "session_id": "sess-live", "ts": f"2026-09-03T12:{i % 60:02d}:00+00:00",
                    "root": "/tmp/ws", "path": f"live-{i}.txt", "operation": "create",
                }
            )
            i += 1

    worker = threading.Thread(target=_producer)
    worker.start()
    try:
        first = rebuild_projection(blackbox=seeded["store"], batch_size=5)
        assert first["journal_unchanged_witness"] is True
    finally:
        stop.set()
        worker.join()

    second = rebuild_projection(blackbox=seeded["store"])
    assert second["status"] == "complete"
    entries_after = seeded["store"].journal.entries()
    journal_seqs = {int(e["seq"]) for e in entries_after}
    pairs = _projected_pairs()
    projected = {seq for _eid, seq in pairs}
    assert projected == journal_seqs, "journal and projection disagree after concurrent rebuild"
    assert len(pairs) == len(set(pairs)), "concurrent appends were served twice"


def test_rebuild_receipts_are_persisted(seeded):
    rebuild_projection(blackbox=seeded["store"])
    rebuild_projection(blackbox=seeded["store"], resume=False)
    receipt_path = _projection_root() / "receipts" / "rebuild.jsonl"
    rows = read_jsonl(receipt_path)
    assert len(rows) == 2
    assert rows[0]["appended"] == len(journal_entries())
    assert rows[1]["appended"] == 0 and rows[1]["skipped_already_projected"] == len(journal_entries())
    state = json.loads((_projection_root() / "rebuild_state.json").read_text())
    assert state["schema"] == "vool.liquefy.rebuild_state.v1"
    assert state["last_blackbox_seq"] == len(journal_entries()) - 1  # seqs are 0-based
