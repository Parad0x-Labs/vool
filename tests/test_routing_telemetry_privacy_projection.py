"""Schema-2 projection + the legacy ``message``-column migration contract (alert 155).

Synthetic values only, disposable homes only. Proves: arbitrary text has no column in new
durable records, ordinary route counting/session correlation still work, the one-time
migration strips legacy plaintext without touching other fields or other sessions' rows,
is idempotent, preserves malformed lines, raises on storage faults when driven explicitly,
and never costs the append when attempted lazily in-turn.
"""
from __future__ import annotations

import json

import pytest

from core import routing_decision_log as rdl
from core import runtime_paths


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    monkeypatch.setattr(rdl, "_SHADOW_STORE", None)
    runtime_paths.configure_runtime_home(tmp_path / "home")
    yield
    runtime_paths.configure_runtime_home(None)


CUSTOM = "my router admin password is Tr0ub4dor&3 never share it"


def _legacy_rows() -> list[dict]:
    return [
        {
            "ts": "2026-09-01T10:00:00+00:00",
            "session_id": "openclaw:" + "a" * 20,
            "message": CUSTOM,
            "family": "weather",
            "handled": True,
        },
        {
            "ts": "2026-09-01T10:01:00+00:00",
            "session_id": "openclaw:" + "b" * 20,
            "message": "[message unavailable: redaction failed]",
            "family": "model_lane",
            "handled": False,
            "claims": ["weather", "time"],
            "arbiter": "timeout",
        },
    ]


def _write_legacy_log() -> None:
    path = rdl.decisions_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(row, ensure_ascii=False) for row in _legacy_rows()]
    lines.append("{not json at all")  # a malformed line that must survive verbatim
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_migration_strips_legacy_message_columns_and_preserves_everything_else():
    _write_legacy_log()
    result = rdl.migrate_legacy_decision_log()
    assert result.ok and result.rewritten is True
    assert result.rows_migrated == 2 and result.rows_malformed_preserved == 1
    rows = rdl.recent_decisions(limit=10)
    assert [row["session_id"] for row in rows] == [
        "openclaw:" + "a" * 20,
        "openclaw:" + "b" * 20,
    ]
    assert all("message" not in row for row in rows)
    assert all(row["record_schema"] == 2 for row in rows)
    # non-content fields preserved exactly
    assert rows[1]["claims"] == ["weather", "time"] and rows[1]["arbiter"] == "timeout"
    assert rows[0]["family"] == "weather" and rows[0]["ts"] == "2026-09-01T10:00:00+00:00"
    # the plaintext is gone from the file bytes, and no backup/debug copy was left behind
    stored = rdl.decisions_path().read_text()
    assert CUSTOM not in stored
    assert sorted(p.name for p in rdl.decisions_path().parent.glob("routing_decisions*")) == [
        "routing_decisions.jsonl"
    ]
    # the malformed line survived byte-identical
    assert "{not json at all" in stored.splitlines()


def test_migration_is_idempotent_and_does_not_rewrite_a_clean_file():
    _write_legacy_log()
    rdl.migrate_legacy_decision_log()
    path = rdl.decisions_path()
    first_bytes = path.read_bytes()
    result = rdl.migrate_legacy_decision_log()
    assert result.rewritten is False and result.rows_migrated == 0
    assert result.rows_already_current == 2 and result.rows_malformed_preserved == 1
    assert path.read_bytes() == first_bytes


def test_first_append_migrates_a_legacy_log_lazily():
    _write_legacy_log()
    rdl._LEGACY_MIGRATION_ATTEMPTED = False
    try:
        rdl.record_decision(session_id="openclaw:" + "c" * 20, user_input="fresh turn", family="f", handled=True)
        stored = rdl.decisions_path().read_text()
        assert CUSTOM not in stored
        assert "fresh turn" not in stored  # the new row is metadata-only too
        rows = rdl.recent_decisions(limit=10)
        assert len(rows) == 3 and rows[-1]["record_schema"] == 2
        assert "{not json at all" in stored.splitlines()  # malformed line untouched
        # the attempt is scoped to THIS runtime log's path — a second configured home is
        # still migrated later, not skipped by a process-global flag
        assert rdl._migration_attempted(rdl.decisions_path())
    finally:
        rdl._LEGACY_MIGRATION_ATTEMPTED = False


def test_lazy_migration_attempts_once_per_log_and_follows_a_second_home(tmp_path):
    """The attempted-marker is keyed to the runtime log, not the process: after the first
    home's log is attempted, a SECOND configured home with its own legacy plaintext is
    still migrated, while the first home's log is never re-scanned per call."""
    _write_legacy_log()
    rdl._LEGACY_MIGRATION_ATTEMPTED = False
    try:
        rdl.record_decision(session_id="openclaw:" + "e" * 20, user_input="first home", family="f", handled=True)
        assert CUSTOM not in rdl.decisions_path().read_text()

        first_home_path = rdl.decisions_path()
        runtime_paths.configure_runtime_home(tmp_path / "second-home")
        rdl._SHADOW_STORE = None  # never let the first home's store serve the second
        second_path = rdl.decisions_path()
        assert second_path != first_home_path
        second_path.parent.mkdir(parents=True, exist_ok=True)
        second_path.write_text(
            json.dumps({"session_id": "openclaw:" + "9" * 20, "message": CUSTOM,
                        "family": "legacy"}) + "\n",
            encoding="utf-8",
        )
        # the first home's log is not re-attempted (cheap set membership, no rescan)
        assert rdl._migration_attempted(first_home_path)
        assert not rdl._migration_attempted(second_path)

        rdl.record_decision(session_id="openclaw:" + "8" * 20, user_input="second home", family="f", handled=True)
        stored = second_path.read_text()
        assert CUSTOM not in stored, "process-global once flag skipped the second runtime log"
        assert [r["family"] for r in rdl.recent_decisions(limit=10)] == ["legacy", "f"]
    finally:
        runtime_paths.configure_runtime_home(None)
        rdl._LEGACY_MIGRATION_ATTEMPTED = False


def test_explicit_migration_serializes_with_concurrent_appends():
    """Holding the explicit migration across its atomic replace must not overwrite a
    concurrent append: the writer serializes on the same lock and its complete row
    survives alongside the sanitized legacy metadata."""
    import threading
    from unittest.mock import patch

    _write_legacy_log()
    rdl._LEGACY_MIGRATION_ATTEMPTED = True  # keep the lazy attempt out of this probe
    try:
        waiting, release = threading.Event(), threading.Event()
        append_done = threading.Event()
        failures: list[Exception] = []
        real_replace = rdl.os.replace

        def pause_replace(src, dst):
            if str(src).endswith(".migrate.tmp"):
                waiting.set()
                if not release.wait(5):
                    raise TimeoutError("test barrier")
            return real_replace(src, dst)

        def migrate():
            try:
                rdl.migrate_legacy_decision_log()
            except Exception as error:  # pragma: no cover - surfaced by assertion
                failures.append(error)

        def append():
            try:
                rdl.record_decision(session_id="openclaw:" + "7" * 20,
                                    user_input="concurrent new row", family="concurrent_new",
                                    handled=True)
            except Exception as error:  # pragma: no cover - surfaced by assertion
                failures.append(error)
            finally:
                append_done.set()

        with patch.object(rdl.os, "replace", pause_replace):
            thread = threading.Thread(target=migrate)
            thread.start()
            try:
                assert waiting.wait(5)
                writer = threading.Thread(target=append)
                writer.start()
                append_done.wait(0.25)  # a serialized writer may legally still be blocked
            finally:
                release.set()
                thread.join(5)
                writer.join(5)
        assert not thread.is_alive() and not writer.is_alive() and not failures
        stored = rdl.decisions_path().read_text()
        assert CUSTOM not in stored
        assert "concurrent_new" in [r["family"] for r in rdl.recent_decisions(limit=10)], (
            "migration erased a successful concurrent append"
        )
    finally:
        rdl._LEGACY_MIGRATION_ATTEMPTED = False


def test_a_migration_storage_fault_raises_when_explicit_and_never_costs_the_append(monkeypatch, tmp_path):
    _write_legacy_log()

    def failing(_path):
        raise OSError("disk full (synthetic fault)")

    # Explicit operator call: the fault is raised, not swallowed.
    monkeypatch.setattr(rdl, "_migrate_locked", failing)
    with pytest.raises(OSError):
        rdl.migrate_legacy_decision_log()
    # Lazy in-turn attempt: the fault costs nothing — the append still lands, once per log.
    rdl._LEGACY_MIGRATION_ATTEMPTED = False
    try:
        rdl.record_decision(session_id="openclaw:" + "d" * 20, user_input="x", family="f", handled=True)
        rows = rdl.recent_decisions(limit=10)
        assert rows and rows[-1]["family"] == "f"
        # and the attempt is not retried per-call (no per-call filesystem work)
        assert rdl._migration_attempted(rdl.decisions_path())
        # the second call appends without touching the faulting migration again
        def bomb(_path):  # pragma: no cover - must not run
            raise AssertionError("lazy migration retried a faulted attempt")

        monkeypatch.setattr(rdl, "_migrate_locked", bomb)
        rdl.record_decision(session_id="openclaw:" + "d" * 20, user_input="y", family="f2", handled=True)
        assert rdl.recent_decisions(limit=10)[-1]["family"] == "f2"
    finally:
        rdl._LEGACY_MIGRATION_ATTEMPTED = False


def test_projection_keeps_route_counting_and_session_correlation_across_sinks():
    import sqlite3

    a = "openclaw:" + "1" * 20
    b = "openclaw:" + "2" * 20
    rdl.record_decision(session_id=a, user_input="turn one words", family="weather", handled=True)
    rdl.record_decision(session_id=a, user_input="turn two words", family="weather", handled=True)
    rdl.record_decision(session_id=b, user_input="turn three words", family="model_lane",
                        handled=False, claims=["weather", "time"], arbiter="timeout")
    stats = rdl.decision_stats()
    assert stats["total"] == 3 and stats["families"]["weather"] == 2 and stats["ambiguous"] == 1
    rows = rdl.recent_decisions()
    assert [row["session_id"] for row in rows] == [a, a, b]
    with sqlite3.connect(str(rdl.data_path("routing_authority_v2_shadow.sqlite"))) as conn:
        refs = [
            json.loads(bytes(r[0]))["session_ref"]
            for r in conn.execute(
                "SELECT canonical_bytes FROM routing_authority_v2_shadow_records"
                " WHERE record_type = 'RoutingDecisionShadowV2'"
            )
        ]
    assert refs == [a, a, b]  # same correlation key in the shadow twin
