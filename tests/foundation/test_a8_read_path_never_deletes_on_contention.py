"""A housekeeping write failure must never become an availability verdict.

``payload_availability_for_hash`` is a READ, and it calls
``_migrate_legacy_digest_tombstones``, which WRITES — on a connection it opens
itself, inside whatever transaction the caller already holds. SQLite does not
exempt a second connection just because it is on the same thread.

That was harmless until a caller held the WAL write lock across the read, which
``storage.useful_output_store`` deliberately does so that its veto and its write
are atomic. Then, on any database upgraded from before pass-002 (one unmigrated
``sha256:`` tombstone is enough):

    the nested UPDATE self-blocks for the full 5 s busy timeout
    -> OperationalError
    -> ``writer_may_persist_text``'s ``except Exception: return False``
    -> the veto REFUSES a legitimate payload
    -> and in ``sync_useful_outputs`` a refusal is a DELETE.

A real useful-output row was destroyed. The tombstone stayed unmigrated, so it
happened again on the next sync, and the one after that.

Measured before the fix: ``dt=5.19s may_persist=False``. After: ``may_persist=True``.
The latency under contention is unchanged and deliberate — see the test at the
bottom, which pins it as a known cost rather than letting it look fixed.
"""
from __future__ import annotations

import time
import uuid

import pytest

PAYLOAD = "a8 read-path probe payload ROMEO-9999"


@pytest.fixture()
def store(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    monkeypatch.setenv("VOOL_KEY_STORAGE_MODE", "file")
    monkeypatch.setenv("VOOL_CREDENTIAL_STORE", "vault")
    from core.runtime_paths import configure_runtime_home
    from storage.db import configure_default_db_path

    configure_runtime_home(tmp_path)
    configure_default_db_path(tmp_path / "a8probe.db")
    from storage.migrations import run_migrations

    run_migrations()
    yield
    configure_default_db_path(None)
    configure_runtime_home(None)


def _plant_legacy_tombstone() -> None:
    """One pre-pass-002 unsalted tombstone — the shape the rewrite exists for."""
    from core.finalization import EVENT_KIND_ERASURE_DIGEST_TOMBSTONE
    from storage.db import get_connection

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO a7_governance_events "
            "(event_id, finalization_id, event_kind, reason, created_at) VALUES (?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                "fid-legacy-probe",
                EVENT_KIND_ERASURE_DIGEST_TOMBSTONE,
                "sha256:" + "ab" * 32,
                time.time(),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def _veto_under_open_write_transaction() -> tuple[float, bool]:
    """Ask the veto while another connection holds the WAL write lock."""
    from core.finalization import writer_may_persist_text
    from storage.db import get_connection

    holder = get_connection()
    holder.execute("BEGIN IMMEDIATE")
    try:
        started = time.monotonic()
        allowed = writer_may_persist_text(PAYLOAD)
        return time.monotonic() - started, allowed
    finally:
        holder.commit()
        holder.close()


def test_a_legitimate_payload_is_permitted_even_with_the_write_lock_held(store) -> None:
    """The data-loss fix, stated as the outcome that matters.

    Before: False, which `sync_useful_outputs` turns into a DELETE of a real row.
    """
    _plant_legacy_tombstone()
    _, allowed = _veto_under_open_write_transaction()
    assert allowed is True, (
        "the veto refused a legitimate payload because a housekeeping rewrite could "
        "not get the write lock; in sync_useful_outputs that refusal deletes a row"
    )


def test_an_uncontended_read_still_closes_the_unsalted_oracle(store) -> None:
    """The rewrite is a privacy hardening and must still happen when it can.

    Only the FAILURE is swallowed. A short busy timeout was tried here and
    reverted: it made the rewrite lose its race under any contention and left the
    unsalted pre-erase hash — a confirmation oracle — in the ledger, which the F11
    served freeze test catches.
    """
    from core.finalization import writer_may_persist_text
    from storage.db import get_connection

    _plant_legacy_tombstone()
    writer_may_persist_text(PAYLOAD)  # an ordinary, uncontended read

    conn = get_connection()
    try:
        left = conn.execute(
            "SELECT COUNT(*) FROM a7_governance_events WHERE reason LIKE 'sha256:%'"
        ).fetchone()[0]
    finally:
        conn.close()
    assert left == 0, "the unsalted tombstone survived an uncontended read"


def test_the_verdict_is_correct_with_no_legacy_tombstone_at_all(store) -> None:
    """Control: the contended path is only interesting because of the rewrite."""
    elapsed, allowed = _veto_under_open_write_transaction()
    assert allowed is True
    assert elapsed < 1.0, f"an uncontended-shaped read took {elapsed:.2f}s"


def test_the_contention_latency_is_a_known_remaining_cost(store) -> None:
    """Named, not hidden.

    The busy timeout is deliberately NOT lowered (see above), so a read that
    collides with an open write transaction while an unmigrated tombstone exists
    still waits. What changed is that it now returns the RIGHT ANSWER instead of
    a refusal. This test exists so the remaining latency is a recorded property
    rather than something a future reader discovers as a surprise.
    """
    _plant_legacy_tombstone()
    elapsed, allowed = _veto_under_open_write_transaction()
    assert allowed is True
    assert elapsed > 1.0, (
        "the contention this documents did not occur, so either the timeout or the "
        "call site changed — re-derive the claim rather than deleting this test"
    )


def test_sabotage_letting_the_rewrite_failure_escape_restores_the_deletion(
    store, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Put the unguarded rewrite back and the veto fails closed again."""
    from core import finalization as fin

    original = fin._migrate_legacy_digest_tombstones

    def unguarded(conn):
        rows = conn.execute(
            "SELECT event_id, reason FROM a7_governance_events "
            "WHERE event_kind = ? AND reason LIKE 'sha256:%'",
            (fin.EVENT_KIND_ERASURE_DIGEST_TOMBSTONE,),
        ).fetchall()
        if not rows:
            return original(conn)
        for row in rows:
            conn.execute(
                "UPDATE a7_governance_events SET reason = ? WHERE event_id = ? AND reason = ?",
                (
                    fin._keyed_tombstone_value(str(row["reason"])),
                    str(row["event_id"]),
                    str(row["reason"]),
                ),
            )
        conn.commit()

    monkeypatch.setattr(fin, "_migrate_legacy_digest_tombstones", unguarded)
    _plant_legacy_tombstone()
    _, allowed = _veto_under_open_write_transaction()
    assert allowed is False, (
        "sabotage no-op: with the rewrite's failure escaping again the veto still "
        "permitted the payload, so swallowing it is not what prevents the deletion"
    )
