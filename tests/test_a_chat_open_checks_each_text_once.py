"""Opening a chat checks each piece of text against erasure once, and still sees a revocation at once.

Owner's iMac, 2026-11-27 (installed 0.8 build): the activity list read 3,154 events of 100 chats
and checked every string in them against the serve gate: 68,982 checks of 5,375 distinct strings,
each check rescanning the governance table twice. One chat open was 45-60 s of CPU, and every other
request the window made waited behind it.

* Inside one projection scope a verdict is reused while no other connection has committed since it
  was read; a commit (an erase, a withhold) clears them, so the next check reads the store again.
* The legacy tombstone rewrite runs once per scope, not once per check.
* A store error is never kept as a verdict.
* The store carries the two indexes the gate reads by.
"""
from __future__ import annotations

import hashlib

import pytest

from core import finalization
from storage.db import get_connection


def _count_reads(monkeypatch) -> list[str]:
    seen: list[str] = []
    original = finalization._availability_verdict
    rewrite = finalization._migrate_legacy_digest_tombstones

    def verdict(conn, clean):
        seen.append("verdict")
        return original(conn, clean)

    def migrate(conn):
        seen.append("rewrite")
        return rewrite(conn)

    monkeypatch.setattr(finalization, "_availability_verdict", verdict)
    monkeypatch.setattr(finalization, "_migrate_legacy_digest_tombstones", migrate)
    return seen


def test_one_scope_reads_each_text_once_until_something_commits(monkeypatch) -> None:
    finalization.reset_governance_readiness_for_tests()
    seen = _count_reads(monkeypatch)
    text = "a line the activity list shows on every row"
    with finalization.availability_read_scope():
        for _ in range(50):
            assert finalization.writer_may_persist_text(text)
        assert seen.count("verdict") == 1
        assert seen.count("rewrite") == 1
        conn = get_connection()
        try:
            conn.execute(
                "INSERT INTO a7_governance_events (event_id, finalization_id, event_kind, reason, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                ("once-scope-tombstone", "no-live-row", finalization.EVENT_KIND_ERASURE_DIGEST_TOMBSTONE,
                 "sha256:" + hashlib.sha256(text.encode()).hexdigest(), "2026-11-27"),
            )
            conn.commit()
        finally:
            conn.close()
        assert not finalization.writer_may_persist_text(text), "a commit midway is seen by the next check"
        assert seen.count("verdict") == 2
        assert seen.count("rewrite") == 1


def test_outside_a_scope_every_check_reads_the_store(monkeypatch) -> None:
    finalization.reset_governance_readiness_for_tests()
    seen = _count_reads(monkeypatch)
    for _ in range(3):
        finalization.writer_may_persist_text("no scope here")
    assert seen.count("verdict") == 3


def test_a_store_error_is_not_kept_as_a_verdict(monkeypatch) -> None:
    finalization.reset_governance_readiness_for_tests()
    original = finalization._availability_verdict
    calls = {"n": 0}

    def flaky(conn, clean):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("store busy")
        return original(conn, clean)

    monkeypatch.setattr(finalization, "_availability_verdict", flaky)
    with finalization.availability_read_scope():
        assert not finalization.writer_may_persist_text("checked twice"), "a failed read fails closed"
        assert finalization.writer_may_persist_text("checked twice"), "the next read asks the store again"
    assert calls["n"] == 2


@pytest.mark.parametrize(("table", "columns"), [
    ("a7_finalizations", ["content_hash"]),
    ("a7_governance_events", ["event_kind", "reason"]),
])
def test_the_store_indexes_what_the_gate_reads_by(table: str, columns: list[str]) -> None:
    conn = get_connection()
    try:
        indexes = [row[1] for row in conn.execute(f"PRAGMA index_list({table})")]
        shapes = [[row[2] for row in conn.execute(f"PRAGMA index_info({name})")] for name in indexes]
    finally:
        conn.close()
    assert columns in shapes, shapes
