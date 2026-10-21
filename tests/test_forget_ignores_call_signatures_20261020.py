"""A forget is not refused over the random signature a provider call's record carries.

Measured on CI after the 0.7.2 merge (main 89cc1851, shard 6): "Forget 20." on the 20th answered "Forget could not
be verified as fully applied: unclassified provider invocation manifests" because one call's base64 signature held
"20" as its own number. Every model call writes such a record, so a short number or word was refused in a normal
chat, and no retry could clear it. Words in that record are still read, and so is a signature anywhere else: one
the person pasted (a transaction signature in a tool's arguments) is their words.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any

import pytest

from tests.test_forget_scrubs_run_history import _remember, isolated_pages
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command as command
from tests.test_question_date_time_leg_20261002 import _hash_backend

pytestmark = pytest.mark.usefixtures("_hash_backend")

def _call_record(chat: str, request_id: str) -> Any:
    """One provider call's record, canonicalized and signed the way the gateway writes it."""
    from core.provider_invocation_gateway import (
        ProviderInvocationManifest,
        _canonical_manifest_fields,
        _manifest_signature_bytes,
    )
    from network import signer

    unsigned = _canonical_manifest_fields({
        "manifest_id": "provider-manifest-" + request_id, "request_id": request_id, "chat_id": chat,
        "project_id": "", "provider_id": "ollama-local:stub-private:4b", "model_id": "stub-private:4b",
        "operation": "chat", "payload_hash": "0" * 64, "context_manifest_id": "", "context_trace_id": "",
        "prompt_profile": "unknown", "provider_messages": [{"role": "system"}, {"role": "user"}],
        "response_control_state": "none", "selected_sources": [], "excluded_sources": [], "capsule_version": "none",
        "header_names": ["Content-Type"], "created_at": "2026-10-20T06:12:44Z",
    })
    return ProviderInvocationManifest(**unsigned, signature=signer.sign(_manifest_signature_bytes(unsigned)))


def _record_call_whose_signature_carries(chat: str, token: str) -> str:
    """Store real signed call records until one's signature holds ``token`` as its own number; return it."""
    from core.memory.carriers import carries
    from core.provider_invocation_gateway import store_provider_manifest

    for attempt in range(40000):  # a two-digit number stands alone in about one signature in 1,250
        record = _call_record(chat, f"req-{chat}-{attempt}")
        if carries([record.signature], token):
            store_provider_manifest(record)
            return record.signature
    raise AssertionError(f"no signature held {token!r}")


def test_a_number_inside_a_call_signature_does_not_refuse_the_forget(isolated_pages):
    chat = "forget-call-signature"
    _remember(isolated_pages, chat, "my locker is 14.")
    signature = _record_call_whose_signature_carries(chat, "14")

    handled, reply = command(isolated_pages, chat, "forget 14")

    assert handled and reply.startswith("Forget applied"), reply
    conn = sqlite3.connect(isolated_pages / "data" / "vool_web0_v2.db")
    try:  # the record is immutable and stays as written
        stored = conn.execute("SELECT signature, manifest_json FROM provider_invocation_manifests WHERE chat_id = ?",
                              (chat,)).fetchall()
    finally:
        conn.close()
    assert [(row[0], json.loads(row[1])["signature"]) for row in stored] == [(signature, signature)]


def test_words_in_a_call_record_still_refuse_the_forget(isolated_pages):
    chat = "forget-call-words"
    _remember(isolated_pages, chat, "my locker is 14.")
    from core.provider_invocation_gateway import ensure_provider_manifest_schema

    ensure_provider_manifest_schema()
    record = _call_record(chat, "req-words")
    conn = sqlite3.connect(isolated_pages / "data" / "vool_web0_v2.db")
    try:  # a record whose signature field holds words, not a signature
        conn.execute(
            "INSERT INTO provider_invocation_manifests (manifest_id, request_id, chat_id, project_id, provider_id, "
            "model_id, operation, payload_hash, manifest_json, signature, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (record.manifest_id, record.request_id, chat, "", record.provider_id, record.model_id, record.operation,
             record.payload_hash, json.dumps({"signature": "my locker is 14"}), "my locker is 14", record.created_at))
        conn.commit()
    finally:
        conn.close()

    handled, reply = command(isolated_pages, chat, "forget 14")

    assert handled and "could not be verified" in reply and "provider invocation manifests" in reply, reply


def test_a_signature_outside_the_call_records_still_refuses_the_forget(isolated_pages):
    """Only the gateway's own call records are exempt: the same signature in another chat table is read as words."""
    from storage.db import get_connection

    chat = "forget-pasted-signature"
    _remember(isolated_pages, chat, "my locker is 14.")
    signature = _record_call_whose_signature_carries(chat, "14")
    conn = get_connection()
    try:
        conn.execute("CREATE TABLE turn_scratch_notes (note_id TEXT PRIMARY KEY, session_id TEXT, args_json TEXT)")
        conn.execute("INSERT INTO turn_scratch_notes VALUES (?, ?, ?)",
                     ("pasted", chat, json.dumps({"signature": signature})))
        conn.commit()
    finally:
        conn.close()

    handled, reply = command(isolated_pages, chat, "forget 14")

    assert handled and "could not be verified" in reply and "unclassified turn scratch notes" in reply, reply
    assert "provider invocation manifests" not in reply, reply
