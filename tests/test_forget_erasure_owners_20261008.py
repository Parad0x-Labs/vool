"""Erasure owners verify every removed copy and report retained stores."""
import hashlib
import os
import sqlite3
from pathlib import Path

import pytest

from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command as command
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _ts as ts
from tests.test_question_date_time_leg_20261002 import _capsule, _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures("_hash_backend")


def _deny_replace(monkeypatch, target):
    original = os.replace
    attempts = []

    def deny_one_file(src, dst, *args, **kwargs):
        if Path(dst) == target:
            attempts.append((str(src), str(dst)))
            raise OSError("review fault: real atomic replacement of this synthetic store is denied")
        return original(src, dst, *args, **kwargs)

    monkeypatch.setattr(os, "replace", deny_one_file)
    return attempts


def test_summary_only_erasure_is_not_reported_as_clean_no_match(tmp_path):
    from core.memory.files import append_jsonl, load_jsonl, session_summaries_path

    profile = _profile(tmp_path)
    chat = "review4-summary-only"
    _ingest(profile, chat, [(ts("2026-02-03T09:00:00"), "The garden shed needs roof repairs.")])
    path = session_summaries_path()
    append_jsonl(path, {"session_id": chat, "summary": "My locker code is MINT-84.",
                       "keywords": ["locker", "code", "mint"], "status": "active",
                       "created_at": "2026-02-03T09:00:00+00:00",
                       "provenance": {"kind": "derived_session_summary", "source_id": "review4-summary-only"}})
    assert any("MINT-84" in str(row.get("summary")) for row in load_jsonl(path))
    handled, reply = command(profile, chat, "forget MINT-84")
    assert handled
    assert not any("MINT-84" in str(row.get("summary")) for row in load_jsonl(path))
    assert "Nothing was removed" not in reply and "no memory" not in reply.lower(), reply


def test_receipt_cleanup_select_failure_never_claims_complete_erasure(tmp_path, monkeypatch):
    import core.context_retrieval as cr
    from core.memory_receipts import receipts_for_scope, write_receipt
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "review4-receipt-partial"
    body = "My locker code is MINT-84."
    _ingest(profile, chat, [(ts("2026-02-03T09:00:00"), body)])
    mem = VoolMemory(runtime_home=str(profile))
    row = mem._conn.execute("SELECT occurrence_id FROM source_occurrences WHERE chat_scope = ? AND body LIKE ?",
                            (chat, "%MINT-84%" )).fetchone()
    assert row
    occurrence = mem.occurrence_get(row["occurrence_id"])
    assert occurrence and "MINT-84" in occurrence.body
    write_receipt(mem, occurrence, chat_scope=chat)
    assert any("MINT-84" in str(row) for row in receipts_for_scope(mem, chat))
    denied = []

    def deny_receipt_read(action, first, second, database, origin):
        if action == sqlite3.SQLITE_READ and first == "memory_receipts":
            denied.append((first, second))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    mem._conn.set_authorizer(deny_receipt_read)
    # No fabricated erasure outcome: actual owner instance and SQLite authorization fail at receipt cleanup.
    with monkeypatch.context() as patch:
        patch.setattr(cr, "_open_memory_for_runtime", lambda home: mem)
        patch.setattr(mem, "close", lambda: None)
        handled, reply = command(profile, chat, "forget MINT-84")
    mem._conn.set_authorizer(None)
    retained = receipts_for_scope(mem, chat)
    mem.close()
    assert handled and denied, (handled, reply, denied)
    assert not any("MINT-84" in str(row) for row in retained), (reply, retained)
    assert "Forget applied" not in reply and "could not be verified" in reply, reply


def test_partial_log_scrub_never_claims_complete_erasure(tmp_path, monkeypatch):
    from core.memory.files import conversation_log_path, load_jsonl

    profile = _profile(tmp_path)
    chat = "review4-log-partial"
    body = "My locker code is MINT-84."
    _ingest(profile, chat, [(ts("2026-02-03T09:00:00"), body)])
    path = conversation_log_path()
    assert any("MINT-84" in str(row.get("user")) for row in load_jsonl(path))
    with monkeypatch.context() as patch:
        attempts = _deny_replace(patch, path)
        handled, reply = command(profile, chat, "forget MINT-84")
    assert handled and attempts, (handled, reply, attempts)
    assert not any("MINT-84" in str(row.get("user")) for row in load_jsonl(path)), "public reads must withhold retained plaintext"
    assert b"MINT-84" in path.read_bytes(), "fault must retain real raw plaintext"
    # The ledger gates tested recall, but a claimed complete erasure still leaves the raw log unchanged.
    assert "MINT-84" not in _capsule(profile, chat, "What is my locker code?")[0]
    assert "Forget applied" not in reply and "no memory" not in reply.lower(), reply
    assert "could not be verified" in reply, reply


def test_partial_summary_scrub_never_claims_success_over_a_served_copy(tmp_path, monkeypatch):
    from core.agent_runtime.fast_paths_companion import render_companion_continuation_response
    from core.context_namespace import ensure_chat_namespace, grant_context_import
    from core.memory.entries import resolve_memory_access_policy, search_session_summaries
    from core.memory.files import append_jsonl, load_jsonl, session_summaries_path

    profile = _profile(tmp_path)
    chat = "review4-summary-partial"
    body = "The thermal-pool locker code is MINT-84."
    _ingest(profile, chat, [(ts("2026-02-03T09:00:00"), body)])
    source_id = "session-summary:review4-summary-partial:fixture"
    path = session_summaries_path()
    append_jsonl(path, {"session_id": chat, "summary": body, "keywords": ["thermal", "pool", "locker", "code"],
                       "created_at": "2026-02-03T09:00:00+00:00", "scope": "chat", "source": "session_summary",
                       "source_id": source_id, "status": "active", "origin_chat_id": chat,
                       "provenance": {"kind": "derived_session_summary", "source_id": source_id,
                                      "content_hash": hashlib.sha256(body.encode()).hexdigest(), "origin_chat_id": chat}})
    policy = resolve_memory_access_policy(chat_id=chat)
    assert any("MINT-84" in row["summary"] for row in search_session_summaries("thermal pool locker code", access_policy=policy))
    # The structured sweep uses a literal substring. Only the semantic sweep recognizes the normalized hyphen.
    with monkeypatch.context() as patch:
        attempts = _deny_replace(patch, path)
        handled, reply = command(profile, chat, "forget thermal pool")
    assert handled and attempts, (handled, reply, attempts)
    assert not any("MINT-84" in str(row.get("summary")) for row in load_jsonl(path)), "public reads must withhold retained summary"
    assert b"MINT-84" in path.read_bytes(), "fault must retain actual summary"
    served = search_session_summaries("thermal pool locker code", access_policy=policy)
    foreign = "review4-summary-importer"
    ensure_chat_namespace(foreign, grant_current_receipts=False)
    grant_context_import(foreign, scope="chat", source_id="chat:" + chat)
    carried = render_companion_continuation_response(session_id=foreign, query_text="thermal pool locker code",
                                                     profile={}, access_policy=resolve_memory_access_policy(chat_id=foreign))
    print({"reply": str(reply), "retained_and_served_summaries": served, "companion_continuation_reply": carried})
    assert not any("MINT-84" in row["summary"] for row in served), (reply, served)
    assert "MINT-84" not in carried, (reply, carried)
    assert "Forget applied" not in reply and "could not be verified" in reply, reply
