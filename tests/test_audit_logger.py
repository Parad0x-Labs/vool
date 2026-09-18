from __future__ import annotations

import json
import threading

import pytest

from core import audit_logger
from storage.db import get_connection


@pytest.fixture(autouse=True)
def _reset_schema_flag():
    # Each test starts from a clean once-flag so schema-setup counting is
    # deterministic regardless of test ordering.
    audit_logger.reset_schema_ready_flag()
    yield
    audit_logger.reset_schema_ready_flag()


def _audit_rows(event_type: str) -> list[dict]:
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT target_id, details_json FROM audit_log WHERE event_type = ?",
            (event_type,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# --- #21 schema setup once-flag -------------------------------------------


def test_schema_setup_runs_once_across_many_log_calls(monkeypatch):
    calls = {"n": 0}
    real = audit_logger._create_audit_log_schema

    def counting():
        calls["n"] += 1
        real()

    monkeypatch.setattr(audit_logger, "_create_audit_log_schema", counting)
    audit_logger.reset_schema_ready_flag()

    for i in range(25):
        audit_logger.log("test_once_flag", target_id=f"t{i}", details={"i": i})

    assert calls["n"] == 1


def test_schema_setup_reruns_when_db_path_changes(monkeypatch):
    calls = {"n": 0}
    real = audit_logger._create_audit_log_schema

    def counting():
        calls["n"] += 1
        real()

    monkeypatch.setattr(audit_logger, "_create_audit_log_schema", counting)

    paths = iter(["/tmp/path-a.db", "/tmp/path-a.db", "/tmp/path-b.db", "/tmp/path-b.db"])
    monkeypatch.setattr(audit_logger, "active_default_db_path", lambda: next(paths))
    audit_logger.reset_schema_ready_flag()

    for _ in range(4):
        audit_logger._ensure_audit_log_table()

    # Two distinct paths => exactly two setup runs.
    assert calls["n"] == 2


def test_schema_setup_thread_safe_runs_once(monkeypatch):
    calls = {"n": 0}
    real = audit_logger._create_audit_log_schema
    barrier = threading.Barrier(8)

    def counting():
        calls["n"] += 1
        real()

    monkeypatch.setattr(audit_logger, "_create_audit_log_schema", counting)
    audit_logger.reset_schema_ready_flag()

    def worker():
        barrier.wait()
        audit_logger._ensure_audit_log_table()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert calls["n"] == 1


# --- #28 ip:port redaction ------------------------------------------------


def test_redact_addr_stable_short_token_never_raw_ip():
    raw = "198.51.100.10:49110"
    token = audit_logger.redact_addr(raw)

    assert token.startswith("addr:")
    assert "198.51.100.10" not in token
    assert "49110" not in token
    # 'addr:' + 12 hex chars
    assert len(token) == len("addr:") + 12
    # Deterministic within the process.
    assert token == audit_logger.redact_addr(raw)
    # Distinct addresses map to distinct tokens.
    assert token != audit_logger.redact_addr("198.51.100.10:49111")


def test_redact_addrs_in_compound_text():
    text = "10.0.0.5:8080 (public 203.0.113.7:9000) stream=192.168.1.2:7000"
    out = audit_logger.redact_addrs_in_text(text)

    for raw in ("10.0.0.5", "203.0.113.7", "192.168.1.2", ":8080", ":9000", ":7000"):
        assert raw not in out
    assert out.count("addr:") == 3
    # Non-address scaffolding text is preserved.
    assert "public" in out and "stream=" in out


def test_log_redacts_ip_port_target_id_in_durable_audit_log():
    audit_logger.log(
        "transport_started",
        target_id="10.0.0.5:8080 (public 203.0.113.7:9000)",
        target_type="transport",
        details={},
    )
    rows = _audit_rows("transport_started")
    assert rows, "expected an audit_log row"
    target_id = rows[0]["target_id"]
    assert "10.0.0.5" not in target_id
    assert "203.0.113.7" not in target_id
    assert "addr:" in target_id


def test_log_redacts_address_detail_key():
    audit_logger.log(
        "stream_transport_start_failed",
        target_id="node-x",
        details={"endpoint": "198.51.100.42:5555", "error": "boom"},
    )
    rows = _audit_rows("stream_transport_start_failed")
    details = json.loads(rows[0]["details_json"])
    assert "198.51.100.42" not in details["endpoint"]
    assert details["endpoint"].startswith("addr:")
    # Non-address free-text is left untouched (no over-redaction).
    assert details["error"] == "boom"


# --- #30 long peer-id truncation ------------------------------------------


def test_truncate_peer_id_truncates_long_keeps_short():
    short = "peer-abc"
    assert audit_logger.truncate_peer_id(short) == short

    long_id = "p" * 64
    out = audit_logger.truncate_peer_id(long_id)
    assert out != long_id
    assert long_id not in out
    assert out.startswith("p" * 12)
    assert "(64)" in out
    # Deterministic.
    assert out == audit_logger.truncate_peer_id(long_id)
    # Distinct long ids that share a prefix stay distinguishable via length.
    other = "p" * 12 + "q" * 70
    assert audit_logger.truncate_peer_id(other) != out


def test_log_truncates_peer_id_detail_key():
    long_id = "helper-" + ("z" * 80)
    audit_logger.log(
        "assist_claim",
        target_id="task-1",
        details={"helper_peer_id": long_id, "assignment_mode": "auto"},
    )
    rows = _audit_rows("assist_claim")
    details = json.loads(rows[0]["details_json"])
    assert details["helper_peer_id"] != long_id
    assert long_id not in details["helper_peer_id"]
    # Unrelated keys untouched.
    assert details["assignment_mode"] == "auto"
