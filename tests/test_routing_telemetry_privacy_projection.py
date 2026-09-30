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
    finally:
        rdl._LEGACY_MIGRATION_ATTEMPTED = False


def test_a_migration_storage_fault_raises_when_explicit_and_never_costs_the_append(monkeypatch, tmp_path):
    _write_legacy_log()
    real_migrate = rdl.migrate_legacy_decision_log

    def failing(path=None):
        raise OSError("disk full (synthetic fault)")

    # Explicit operator call: the fault is raised, not swallowed.
    monkeypatch.setattr(rdl, "migrate_legacy_decision_log", failing)
    with pytest.raises(OSError):
        rdl.migrate_legacy_decision_log()
    # Lazy in-turn attempt: the fault costs nothing — the append still lands, once.
    rdl._LEGACY_MIGRATION_ATTEMPTED = False
    try:
        rdl.record_decision(session_id="openclaw:" + "d" * 20, user_input="x", family="f", handled=True)
        rows = rdl.recent_decisions(limit=10)
        assert rows and rows[-1]["family"] == "f"
        # and the attempt is not retried per-call (no per-call filesystem work)
        assert rdl._LEGACY_MIGRATION_ATTEMPTED is True
        monkeypatch.setattr(rdl, "migrate_legacy_decision_log", real_migrate)
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
