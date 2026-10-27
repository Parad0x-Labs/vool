"""A forget is not refused over a runtime-made id nested in a provider call's record.

Measured on the forget-fold review (REVIEW-2, finding 6): ``test_forget_number_after_history_served`` failed
intermittently with "Forget could not be verified ... provider invocation manifests". One call record's
``manifest_json`` held ``context_manifest_id = "0b625409-f27c-426f-8c67-ac3279212f30"``; the segment ``f27c`` has no
other digit, so "27" (today's day of the month) stood as its own number. A random uuid4 does this for a two-digit
number about once in 559, and every served turn writes such ids.

Only that table's ids are left out, only under a key ending in ``_id``, and only when the value is a UUID or plain
hex of 16 or more characters. Any other value under an ``_id`` key, the same UUID under another key, and the same
UUID in another chat table are still read as words.
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

#: The id the review found in the failed run's database.
NESTED_UUID = "0b625409-f27c-426f-8c67-ac3279212f30"
#: Plain hex whose only digits are "27", and an upper-case UUID whose ``F27C`` segment does the same.
PLAIN_HEX = "cafe27abcdefbeadfacedeedfeedbabe"
UPPER_UUID = "0B625409-F27C-426F-8C67-AC3279212F30"
FORGOTTEN = "27"


def _call_record(chat: str, request_id: str, **overrides: Any) -> Any:
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
        "header_names": ["Content-Type"], "created_at": "2026-10-20T06:12:44Z", **overrides,
    })
    return ProviderInvocationManifest(**unsigned, signature=signer.sign(_manifest_signature_bytes(unsigned)))


def _insert_raw_record(profile: Any, chat: str, request_id: str, manifest: dict[str, Any]) -> None:
    """A call record whose ``manifest_json`` is exactly ``manifest`` (its other columns are the gateway's)."""
    from core.provider_invocation_gateway import ensure_provider_manifest_schema

    ensure_provider_manifest_schema()
    record = _call_record(chat, request_id)
    conn = sqlite3.connect(profile / "data" / "vool_web0_v2.db")
    try:
        conn.execute(
            "INSERT INTO provider_invocation_manifests (manifest_id, request_id, chat_id, project_id, provider_id, "
            "model_id, operation, payload_hash, manifest_json, signature, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (record.manifest_id, record.request_id, chat, "", record.provider_id, record.model_id, record.operation,
             record.payload_hash, json.dumps(manifest), record.signature, record.created_at))
        conn.commit()
    finally:
        conn.close()


def _stored_manifests(profile: Any, chat: str) -> list[dict[str, Any]]:
    conn = sqlite3.connect(profile / "data" / "vool_web0_v2.db")
    try:
        rows = conn.execute("SELECT manifest_json FROM provider_invocation_manifests WHERE chat_id = ?",
                            (chat,)).fetchall()
    finally:
        conn.close()
    return [json.loads(row[0]) for row in rows]


def test_the_ids_stand_the_forgotten_number_alone():
    """The hazard is real: the matcher reads "27" as its own number inside each of these ids."""
    from core.memory.carriers import carries

    for value in (NESTED_UUID, PLAIN_HEX, UPPER_UUID):
        assert carries([value], FORGOTTEN), value


def test_a_number_inside_a_nested_call_id_does_not_refuse_the_forget(isolated_pages):
    from core.provider_invocation_gateway import store_provider_manifest

    chat = "forget-nested-call-id"
    _remember(isolated_pages, chat, f"my locker is {FORGOTTEN}.")
    source = {"item_id": UPPER_UUID, "source_type": "memory", "scope": "chat", "source_id": "memory-entry-1",
              "content_hash": "0" * 64, "reason": "selected"}
    store_provider_manifest(_call_record(chat, "req-nested-ids", context_manifest_id=NESTED_UUID,
                                         context_trace_id=PLAIN_HEX, selected_sources=[source]))
    stored = _stored_manifests(isolated_pages, chat)
    assert [m["context_manifest_id"] for m in stored] == [NESTED_UUID]

    handled, reply = command(isolated_pages, chat, f"forget {FORGOTTEN}")

    assert handled and reply.startswith("Forget applied"), reply
    # the record is immutable and keeps its ids as written
    assert _stored_manifests(isolated_pages, chat) == stored


@pytest.mark.parametrize("value", [
    f"client {FORGOTTEN}",            # words under an _id key
    FORGOTTEN,                        # a short number
    "f27c",                           # hex, but shorter than 16 characters
    "f27c-0b625409",                  # neither a whole UUID nor plain hex
    NESTED_UUID + " client",          # a UUID with words after it
    PLAIN_HEX + "g",                  # hex-like, one letter outside hex
])
def test_a_value_of_another_shape_under_an_id_key_still_refuses_the_forget(isolated_pages, value):
    chat = "forget-id-key-words"
    _remember(isolated_pages, chat, f"my locker is {FORGOTTEN}.")
    _insert_raw_record(isolated_pages, chat, "req-id-words", {"context_manifest_id": "",
                                                              "selected_sources": [{"item_id": value}]})

    handled, reply = command(isolated_pages, chat, f"forget {FORGOTTEN}")

    assert handled and "could not be verified" in reply and "provider invocation manifests" in reply, reply


@pytest.mark.parametrize("key", ["context_manifest", "context_manifest_ids", "note"])
def test_the_same_uuid_under_another_key_still_refuses_the_forget(isolated_pages, key):
    chat = "forget-uuid-other-key"
    _remember(isolated_pages, chat, f"my locker is {FORGOTTEN}.")
    _insert_raw_record(isolated_pages, chat, "req-uuid-key", {"links": {key: NESTED_UUID}})

    handled, reply = command(isolated_pages, chat, f"forget {FORGOTTEN}")

    assert handled and "could not be verified" in reply and "provider invocation manifests" in reply, reply


def test_the_same_uuid_in_another_table_still_refuses_the_forget(isolated_pages):
    """Only the gateway's own call records are exempt: the same id in another chat table is read as words."""
    from storage.db import get_connection

    chat = "forget-uuid-other-table"
    _remember(isolated_pages, chat, f"my locker is {FORGOTTEN}.")
    conn = get_connection()
    try:
        conn.execute("CREATE TABLE turn_scratch_notes (note_id TEXT PRIMARY KEY, session_id TEXT, args_json TEXT)")
        conn.execute("INSERT INTO turn_scratch_notes VALUES (?, ?, ?)",
                     ("pasted", chat, json.dumps({"context_manifest_id": NESTED_UUID})))
        conn.commit()
    finally:
        conn.close()

    handled, reply = command(isolated_pages, chat, f"forget {FORGOTTEN}")

    assert handled and "could not be verified" in reply and "unclassified turn scratch notes" in reply, reply
    assert "provider invocation manifests" not in reply, reply


def test_control_a_call_record_without_the_number_forgets_clean(isolated_pages):
    from core.provider_invocation_gateway import store_provider_manifest

    chat = "forget-plain-call-record"
    _remember(isolated_pages, chat, f"my locker is {FORGOTTEN}.")
    clean = "0b625409-f3cc-426f-8c61-ac3001212f30"
    store_provider_manifest(_call_record(chat, "req-plain", context_manifest_id=clean))

    handled, reply = command(isolated_pages, chat, f"forget {FORGOTTEN}")

    assert handled and reply.startswith("Forget applied"), reply


def test_a_long_number_under_an_id_key_still_refuses_the_forget(isolated_pages):
    """Sixteen digits are hex-shaped, but an all-digit value may be the person's own number (a card, an account):
    it is read as words, so forgetting it still checks the call records (REVIEW-1 finding 4)."""
    chat = "forget-id-key-long-number"
    number = "4111111111111111"
    _remember(isolated_pages, chat, f"my locker code is {number}.")
    _insert_raw_record(isolated_pages, chat, "req-id-number", {"context_manifest_id": "",
                                                               "selected_sources": [{"item_id": number}]})

    handled, reply = command(isolated_pages, chat, f"forget {number}")

    assert handled and "could not be verified" in reply and "provider invocation manifests" in reply, reply
