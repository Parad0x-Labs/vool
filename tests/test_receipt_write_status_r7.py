"""Receipt persistence failures are distinct from idempotence and recovered in the turn."""
from __future__ import annotations

import json
import sqlite3

import pytest

import core.context_retrieval as cr
import core.memory_receipts as mr
from core.context_namespace import ensure_chat_namespace
from core.memory.entries import resolve_memory_access_policy
from core.vool_memory import VoolMemory
from tests.test_question_date_time_leg_20261002 import _hash_backend, _profile

pytestmark = pytest.mark.usefixtures("_hash_backend")


@pytest.fixture()
def store(tmp_path, monkeypatch):
    profile = _profile(tmp_path)
    monkeypatch.setenv("VOOL_MEMORY_RECEIPTS", "1")
    mem = VoolMemory(runtime_home=str(profile))
    yield profile, mem
    mem.close()


def _occurrence(mem, chat="receipt-status", body="My bicycle frame cost 740 dollars."):
    return mem.occurrence_store(chat_scope=chat, role="user", authority="observed-user-statement", body=body, statement_at=1770000000)


def _served_store(profile, chat):
    ensure_chat_namespace(chat, grant_current_receipts=False)
    return cr.store_turn(
        chat, "My bicycle frame cost 740 dollars.", "That is the bicycle frame price.",
        access_policy=resolve_memory_access_policy(chat_id=chat),
        source_context={"chat_id": chat, "runtime_home": str(profile)},
    )


def test_written_duplicate_and_failed_receipts_are_distinct(store):
    _, mem = store
    occurrence = _occurrence(mem)
    written = mr.write_receipt(mem, occurrence, chat_scope="receipt-status")
    duplicate = mr.write_receipt(mem, occurrence, chat_scope="receipt-status")
    mem._conn.execute("DROP TABLE memory_receipts")
    mem._conn.set_authorizer(
        lambda action, first, second, database, origin:
        sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TABLE and first == "memory_receipts"
        else sqlite3.SQLITE_OK
    )
    failed = mr.write_receipt(mem, occurrence, chat_scope="receipt-status")
    mem._conn.set_authorizer(None)
    assert getattr(written, "status", None) == "written"
    assert getattr(duplicate, "status", None) == "already_exists"
    assert getattr(failed, "status", None) == "failed"
    assert written.receipt["receipt_id"] == duplicate.receipt["receipt_id"]
    assert failed.receipt is None and failed.reason


@pytest.mark.parametrize("denied_operation", ["read", "insert", "update"])
def test_sqlite_receipt_failure_is_observable_and_rolls_back_partial_index(store, denied_operation):
    _, mem = store
    first = _occurrence(mem, body="I drive a red hatchback.")
    mr.write_receipt(mem, first, chat_scope="receipt-status")
    second = mem.occurrence_store(
        chat_scope="receipt-status", role="user", authority="observed-user-statement", body="I now drive a blue wagon.",
        statement_at=1770001000,
    )
    operation = {"read": sqlite3.SQLITE_READ, "insert": sqlite3.SQLITE_INSERT,
                 "update": sqlite3.SQLITE_UPDATE}[denied_operation]
    denied = []

    def deny(action, table, column, database, origin):
        if action == operation and table == "memory_receipts":
            denied.append((table, column))
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    mem._conn.set_authorizer(deny)
    outcome = mr.write_receipt(mem, second, chat_scope="receipt-status")
    mem._conn.set_authorizer(None)
    rows = mem._conn.execute("SELECT occurrence_id, replaced_by_json FROM memory_receipts").fetchall()
    assert denied
    assert getattr(outcome, "status", None) == "failed"
    assert [(row[0], json.loads(row[1])) for row in rows] == [(first.occurrence_id, [])]


@pytest.mark.parametrize("entry_point", ["turn", "history"])
def test_persistent_receipt_failure_marks_pending_without_losing_source_or_attesting_success(
    store, monkeypatch, entry_point,
):
    profile, mem = store
    assert mr.ensure_schema(mem)
    denied = []

    def deny(action, table, column, database, origin):
        if action == sqlite3.SQLITE_INSERT and table == "memory_receipts":
            denied.append(table)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    chat = "receipt-pending-" + entry_point
    mem._conn.set_authorizer(deny)
    with monkeypatch.context() as patch:
        patch.setattr(cr, "_open_memory_for_runtime", lambda runtime_home: mem)
        patch.setattr(mem, "close", lambda: None)
        if entry_point == "turn":
            out = _served_store(profile, chat)
        else:
            out = cr.import_conversation_history(
                chat, [{"role": "user", "text": "My bicycle frame cost 740 dollars."},
                       {"role": "assistant", "text": "That is the bicycle frame price."}],
                source_context={"chat_id": chat, "runtime_home": str(profile)},
            )
    mem._conn.set_authorizer(None)
    assert out.get("receipt_pending") is True, out
    assert set(out["receipt_pending_occurrence_ids"]) == set(out["occurrence_ids"])
    assert out.get("receipt_rebuild_attempted") is True
    assert "receipt_write_error" in out["index_failures"]
    assert len(out["occurrence_ids"]) == 2 and not out.get("turn_envelopes")
    assert len(denied) >= 4
    assert mem._conn.execute("SELECT COUNT(*) FROM source_occurrences WHERE chat_scope = ?", (chat,)).fetchone()[0] == 2
    assert mem._conn.execute("SELECT COUNT(*) FROM memory_receipts WHERE chat_scope = ?", (chat,)).fetchone()[0] == 0


@pytest.mark.parametrize("initial_failures", [1, 2])
def test_receipt_failure_retries_and_rebuilds_the_existing_backlog_before_return(store, monkeypatch, initial_failures):
    profile, mem = store
    chat = "receipt-rebuild-now"
    orphan = _occurrence(mem, chat=chat, body="The restored luggage scale weighs 2 kilograms.")
    assert mr.ensure_schema(mem)
    denied = []

    def transient(action, table, column, database, origin):
        if action == sqlite3.SQLITE_INSERT and table == "memory_receipts" and len(denied) < initial_failures:
            denied.append(table)
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    mem._conn.set_authorizer(transient)
    with monkeypatch.context() as patch:
        patch.setattr(cr, "_open_memory_for_runtime", lambda runtime_home: mem)
        patch.setattr(mem, "close", lambda: None)
        out = _served_store(profile, chat)
    mem._conn.set_authorizer(None)
    indexed = {row[0] for row in mem._conn.execute("SELECT occurrence_id FROM memory_receipts WHERE chat_scope = ?", (chat,))}
    assert len(denied) == initial_failures
    assert out.get("receipt_rebuild_attempted") is True, out
    assert out.get("receipt_pending") is False, out
    assert "receipt_write_error" in out["index_failures"]
    assert indexed == {orphan.occurrence_id, *out["occurrence_ids"]}
    assert out["occurrence_ids"][0] in out["receipt_rebuilt_occurrence_ids"]


def test_a_missing_receipt_cannot_be_attested_as_recorded(store):
    _, mem = store
    occurrence = _occurrence(mem)
    with pytest.raises(RuntimeError, match="receipt_pending"):
        cr._kernel_turn_envelope("receipt-status", occurrence, occurrence.body, None, "receipt-test")
