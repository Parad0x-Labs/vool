"""A forget is not held open by an id the runtime made, and the chat's other memories stay readable.

Measured on the 0.7.2 release smoke (9ef0e1ad, arm64, qwen3:8b; one run in three): "Remember that my favourite colour
is teal.", "Remember that my locker is 14.", "Forget 14." answered "Forget could not be verified as fully applied:
unclassified provider invocation manifests ..." because a call record held the context manifest id
"efd9c38b-8bb9-4148-ba14-f785b7e9d944" and its group "ba14" read as the number 14. The pending erasure then kept every
memory of the chat from being read: the memory list came back empty and "What is my favourite colour?" lost teal,
though teal was still stored. A nested number that may be the person's own is still read.
"""
from __future__ import annotations

import json
import sqlite3

import pytest

from tests.test_forget_scrubs_run_history import _remember, isolated_pages
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command as command
from tests.test_question_date_time_leg_20261002 import _hash_backend

pytestmark = pytest.mark.usefixtures("_hash_backend")

#: The context manifest id from the smoke's call record.
SMOKE_ID = "efd9c38b-8bb9-4148-ba14-f785b7e9d944"


def _record_call(chat: str, context_manifest_id: str) -> None:
    """One provider call's record, canonicalized, signed and stored the way the gateway writes it."""
    from core.provider_invocation_gateway import (
        ProviderInvocationManifest,
        _canonical_manifest_fields,
        _manifest_signature_bytes,
        store_provider_manifest,
    )
    from network import signer

    unsigned = _canonical_manifest_fields({
        "manifest_id": "provider-manifest-c61c5f3c669a4d0e9b1f", "request_id": "req-smoke-call", "chat_id": chat,
        "project_id": "", "provider_id": "ollama-local:qwen3:8b", "model_id": "qwen3:8b", "operation": "chat",
        "payload_hash": "0" * 64, "context_manifest_id": context_manifest_id, "context_trace_id": "",
        "prompt_profile": "unknown", "provider_messages": [{"role": "system"}, {"role": "user"}],
        "response_control_state": "none", "selected_sources": [], "excluded_sources": [], "capsule_version": "none",
        "header_names": ["Content-Type"], "created_at": "2026-10-20T11:38:14.593740+00:00",
    })
    store_provider_manifest(ProviderInvocationManifest(
        **unsigned, signature=signer.sign(_manifest_signature_bytes(unsigned))))


def _facts(chat: str) -> list[str]:
    from core.memory.entries import list_memory_entries

    return [str(entry.get("text") or "") for entry in list_memory_entries(chat_id=chat, limit=20)]


def test_a_runtime_id_holding_the_number_neither_refuses_the_forget_nor_hides_other_memories(isolated_pages):
    chat = "release-forget-digit-check"
    _remember(isolated_pages, chat, "my favourite colour is teal.")
    _remember(isolated_pages, chat, "my locker is 14.")
    _record_call("", SMOKE_ID)  # the smoke's record had no chat: every chat's forget reads it

    handled, reply = command(isolated_pages, chat, "Forget 14.")

    assert handled and reply.startswith("Forget applied"), reply
    assert _facts(chat) == ["my favourite colour is teal."]


def test_a_number_nested_in_an_unclassified_row_still_refuses_the_forget(isolated_pages):
    from storage.db import get_connection

    chat = "forget-nested-number"
    _remember(isolated_pages, chat, "my locker is 14.")
    conn = get_connection()
    try:
        conn.execute("CREATE TABLE turn_scratch_notes (note_id TEXT PRIMARY KEY, session_id TEXT, args_json TEXT)")
        conn.execute("INSERT INTO turn_scratch_notes VALUES (?, ?, ?)",
                     ("locker", chat, json.dumps({"locker": "14", "trace": SMOKE_ID})))
        conn.commit()
    finally:
        conn.close()

    handled, reply = command(isolated_pages, chat, "Forget 14.")

    assert handled and "could not be verified" in reply and "unclassified turn scratch notes" in reply, reply


def test_the_smoke_record_is_kept_as_written(isolated_pages):
    """The call record is immutable: the forget reads past its ids, it does not rewrite them."""
    chat = "forget-record-kept"
    _remember(isolated_pages, chat, "my locker is 14.")
    _record_call(chat, SMOKE_ID)

    handled, reply = command(isolated_pages, chat, "Forget 14.")

    assert handled and reply.startswith("Forget applied"), reply
    conn = sqlite3.connect(isolated_pages / "data" / "vool_web0_v2.db")
    try:
        stored = conn.execute("SELECT manifest_json FROM provider_invocation_manifests WHERE chat_id = ?",
                              (chat,)).fetchall()
    finally:
        conn.close()
    assert [json.loads(row[0])["context_manifest_id"] for row in stored] == [SMOKE_ID]


def test_a_chat_left_dark_by_the_old_check_reads_again_after_the_forget_is_said_again(isolated_pages, monkeypatch):
    """A 0.7.2 pre-release home already stuck on such a record: after the upgrade, saying the forget again completes
    the open erasure, and the chat's other memories are read again."""
    import core.memory.run_history as run_history

    chat = "release-forget-digit-check"
    _remember(isolated_pages, chat, "my favourite colour is teal.")
    _remember(isolated_pages, chat, "my locker is 14.")
    _record_call("", SMOKE_ID)
    with monkeypatch.context() as before_the_fix:  # the check as 9ef0e1ad shipped it: nested ids read as words
        before_the_fix.setattr(run_history, "_without_runtime_ids", lambda value: value)
        handled, reply = command(isolated_pages, chat, "Forget 14.")
        assert handled and "could not be verified" in reply and "provider invocation manifests" in reply, reply
    assert _facts(chat) == []  # the open erasure keeps every memory of the chat from being read

    handled, reply = command(isolated_pages, chat, "Forget 14.")

    assert handled and "could not be verified" not in reply, reply
    assert _facts(chat) == ["my favourite colour is teal."]
