"""Guard: secrets must not be persisted in cleartext, and must not be served back by the API.

A working redactor was already applied to runtime_checkpoints.request_text and the conversation log,
but skipped on the audit path -- so a key pasted into a chat turn landed verbatim in
runtime_session_events and was returned in cleartext by GET /api/runtime/events, and a
workspace.write_file of a .env body put the key material into the tool receipt.
"""
from __future__ import annotations

import json
import os
import tempfile

import pytest

from storage.migrations import run_migrations

KEY = "sk-live-AUDITPROBE-4477881122334455"
ENV_BODY = "OPENAI_API_KEY=sk-live-SECRET-000111222333\nAWS_SECRET_ACCESS_KEY=wJalrXUtnFEMI/K7MDENG/bPxRfiCY\n"


@pytest.fixture()
def store(tmp_path):
    from core.runtime_continuity import (
        configure_runtime_continuity_db_path,
        reset_runtime_continuity_state,
    )

    db = tmp_path / "continuity.db"
    run_migrations(db_path=db)
    configure_runtime_continuity_db_path(str(db))
    reset_runtime_continuity_state()
    yield
    reset_runtime_continuity_state()
    configure_runtime_continuity_db_path(None)


def test_event_log_never_stores_a_raw_key(store):
    from core.runtime_continuity import append_runtime_event, list_runtime_session_events

    session = "openclaw:aaaabbbbccccdddd0001"
    append_runtime_event(
        session_id=session,
        event_type="task_received",
        message=f"read config.py and use my key {KEY} to check it",
        details={"request_preview": f"key {KEY}", "nested": {"deep": [f"also {KEY}"]}},
    )
    events = list_runtime_session_events(session, after_seq=0, limit=5)
    blob = json.dumps(events)
    assert KEY not in blob, "raw key persisted to runtime_session_events (and served over /api/runtime/events)"
    assert "[redacted-api-key]" in events[0]["message"]
    assert "config.py" in events[0]["message"], "redaction must not destroy the surrounding message"


def test_tool_receipts_never_store_raw_key_material(store):
    from core.runtime_continuity import list_runtime_tool_receipts, store_tool_receipt

    session = "openclaw:aaaabbbbccccdddd0002"
    store_tool_receipt(
        receipt_key="r1", session_id=session, checkpoint_id="c1",
        tool_name="workspace.write_file", idempotency_key="i1",
        arguments={"path": ".env", "content": ENV_BODY},
        execution={"details": {"observation": {"diff_preview": ENV_BODY}}},
    )
    blob = json.dumps(list_runtime_tool_receipts(session))
    assert "sk-live-SECRET-000111222333" not in blob
    assert "wJalrXUtnFEMI/K7MDENG/bPxRfiCY" not in blob
    assert ".env" in blob, "the path must survive -- only the secret VALUE is masked"


def test_mutation_ledger_is_owner_only_but_keeps_bytes_for_rollback(monkeypatch):
    """The ledger holds before_text VERBATIM on purpose: rollback writes those exact bytes back to
    the user's file. Redacting it would restore a placeholder over real content -- data loss. The
    control here is access (0600), not content."""
    monkeypatch.setenv("VOOL_HOME", tempfile.mkdtemp())
    from core.execution import artifacts

    records = [{"path": "x.env", "existed_before": True, "before_text": ENV_BODY, "after_text": "changed\n"}]
    artifacts._store_mutation_records("sess-perm", records)
    path = artifacts._mutation_log_path("sess-perm")
    assert oct(os.stat(path).st_mode & 0o777) == "0o600"
    assert oct(os.stat(path.parent).st_mode & 0o777) == "0o700"
    # Rollback correctness: the bytes must come back exactly, or a restore corrupts the file.
    assert artifacts._load_mutation_records("sess-perm")[0]["before_text"] == ENV_BODY


def test_scrubber_handles_nested_structures_and_non_strings():
    from core.runtime_continuity import _scrub_persisted

    out = _scrub_persisted({"a": KEY, "b": [1, None, {"c": KEY}], "d": 42, "e": True})
    assert KEY not in json.dumps(out)
    assert out["d"] == 42 and out["e"] is True and out["b"][0] == 1


def test_truncated_private_key_is_redacted_before_event_storage(store):
    from core.runtime_continuity import append_runtime_event, list_runtime_session_events

    body = "synthetic-private-key-body"
    session = "pem-redaction-storage"
    append_runtime_event(session_id=session, event_type="task_received",
                         message="prefix -----BEGIN PRIVATE KEY-----\n" + body)
    events = list_runtime_session_events(session, after_seq=0, limit=5)
    assert body not in json.dumps(events)
    assert events[0]["message"] == "prefix [redacted-private-key]"
