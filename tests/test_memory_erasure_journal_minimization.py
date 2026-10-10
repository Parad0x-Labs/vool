from __future__ import annotations

import contextlib
import sqlite3

import pytest

from core.vool_memory import VoolMemory


def _operation(memory, operation_id):
    with contextlib.closing(sqlite3.connect(memory.db_path)) as connection:
        return connection.execute(
            "SELECT token, status FROM memory_erasure_operations WHERE operation_id = ?",
            (operation_id,),
        ).fetchone()


def test_completed_journal_drops_only_the_redundant_token(tmp_path):
    with VoolMemory(runtime_home=tmp_path) as memory:
        operation = memory.begin_erasure("chat", "MINT84")
        assert _operation(memory, operation) == ("mint84", "pending")
        memory.record_revocation("MINT84", chat_id="chat")
        memory.complete_erasure(operation)
        generation = memory.revocation_generation("chat")
        assert _operation(memory, operation) == ("", "complete")
        assert memory.revoked_tokens("chat", include_pending=True) == ("mint84",)
        memory.complete_erasure(operation)
        assert memory.revocation_generation("chat") == generation
    with VoolMemory(runtime_home=tmp_path) as reopened:
        assert reopened.revoked_tokens("chat", include_pending=True) == ("mint84",)
        assert _operation(reopened, operation) == ("", "complete")


def test_completed_journal_keeps_the_token_without_a_permanent_copy(tmp_path):
    with VoolMemory(runtime_home=tmp_path) as memory:
        operation = memory.begin_erasure("chat", "MINT84")
        memory.complete_erasure(operation)
        assert _operation(memory, operation) == ("mint84", "complete")


def test_repeating_completion_never_completes_a_later_pending_job(tmp_path):
    with VoolMemory(runtime_home=tmp_path) as memory:
        old = memory.begin_erasure("chat", "MINT84")
        memory.record_revocation("MINT84", chat_id="chat")
        memory.complete_erasure(old)
        pending = memory.begin_erasure("chat", "MINT84")
        generation = memory.revocation_generation("chat")
        memory.complete_erasure(old)
        assert memory.revocation_generation("chat") == generation
        assert _operation(memory, pending) == ("mint84", "pending")
        with pytest.raises(RuntimeError, match="incomplete"):
            memory.revoked_tokens("chat", include_pending=True)
