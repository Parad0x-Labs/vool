"""Writer-shaped payload coverage and verification after the final sweep."""
from __future__ import annotations

import copy
import json

import pytest

from tests.test_question_date_time_leg_20261002 import _hash_backend, _ingest, _profile

pytestmark = pytest.mark.usefixtures("_hash_backend")


def _forget(profile, chat, token):
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command

    handled, reply = maybe_handle_memory_command(
        f"forget {token}", session_id=chat,
        access_policy=resolve_memory_access_policy(chat_id=chat),
        source_context={"chat_id": chat, "runtime_home": str(profile), "client_turn_id": "owned-forget"},
    )
    assert handled
    return reply


def _raw_rows(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _string_leaves(value, prefix=()):
    if isinstance(value, dict):
        for key, item in value.items():
            yield from _string_leaves(item, (*prefix, key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _string_leaves(item, (*prefix, index))
    elif isinstance(value, str):
        yield prefix


def test_every_field_emitted_by_artifact_writer_is_erased(tmp_path, monkeypatch):
    """Adding a writer field automatically adds a checked field here."""
    import core.persistent_memory as memory
    from core.context_namespace import ensure_chat_namespace
    from core.memory.files import conversation_log_path

    profile = _profile(tmp_path)
    ensure_chat_namespace("writer-shape", grant_current_receipts=False)
    captured = []
    real_append = memory.append_sequenced_jsonl

    def capture(path, row, **kwargs):
        captured.append(copy.deepcopy(row))
        return real_append(path, row, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(memory, "append_sequenced_jsonl", capture)
        assert memory.append_assistant_artifact_event(
            session_id="writer-shape", text="The notebook is blue.", artifact_kind="review",
            artifact={"value": "plain", "nested": {"values": ["first", "second"]}},
        )
    row_shape = captured[-1]
    # Owner/lineage fields are storage identities. All other serialized
    # string leaves, including future writer fields, must be swept.
    identities = {"ts", "session_id", "request_id", "project_id"}
    leaves = [path for path in _string_leaves(row_shape) if path[0] not in identities]
    assert ("artifact", "nested", "values", 1) in leaves
    for index, leaf in enumerate(leaves):
        chat = f"writer-field-{index}"
        token = f"FIELDVALUE-{index}-TANGERINE"
        ensure_chat_namespace(chat, grant_current_receipts=False)

        def append_field(path, row, *, leaf=leaf, token=token, **kwargs):
            payload = copy.deepcopy(row)
            parent = payload
            for key in leaf[:-1]:
                parent = parent[key]
            parent[leaf[-1]] = token
            return real_append(path, payload, **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(memory, "append_sequenced_jsonl", append_field)
            assert memory.append_assistant_artifact_event(
                session_id=chat, text="The notebook is blue.", artifact_kind="review",
                artifact={"value": "plain", "nested": {"values": ["first", "second"]}},
            )
        assert any(token in str(row) for row in _raw_rows(conversation_log_path()) if row["session_id"] == chat)
        _forget(profile, chat, token)
        assert all(token not in str(row) for row in _raw_rows(conversation_log_path()) if row["session_id"] == chat), leaf


@pytest.mark.parametrize("field", ["speaker", "source_kind", "import_batch"])
def test_source_writer_metadata_is_erased_with_its_derived_receipt(tmp_path, field):
    from core.context_namespace import ensure_chat_namespace
    from core.memory_receipts import write_receipt
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "source-writer-metadata"
    ensure_chat_namespace(chat, grant_current_receipts=False)
    mem = VoolMemory(runtime_home=profile)
    try:
        occurrence = mem.occurrence_store(
            chat_scope=chat, role="user", body="The notebook is blue.", authority="observed-user-statement",
            **{field: "ORCHID-77"},
        )
        write_receipt(mem, occurrence, chat_scope=chat)
        _forget(profile, chat, "ORCHID-77")
        rows = mem._conn.execute("SELECT * FROM source_occurrences WHERE chat_scope = ?", (chat,)).fetchall()
        receipts = mem._conn.execute("SELECT * FROM memory_receipts WHERE chat_scope = ?", (chat,)).fetchall()
        assert "ORCHID-77" not in str([dict(row) for row in rows])
        assert "ORCHID-77" not in str([dict(row) for row in receipts])
    finally:
        mem.close()


def test_future_metadata_columns_and_nested_receipt_fields_are_swept(tmp_path):
    from core.memory_receipts import write_receipt
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "future-source-columns"
    _ingest(profile, chat, [(1760000000, "The notebook is blue.")])
    mem = VoolMemory(runtime_home=profile)
    try:
        occurrence = mem.occurrence_store(
            chat_scope=chat, role="user", body="The rent is 41 crowns.", authority="observed-user-statement",
        )
        write_receipt(mem, occurrence, chat_scope=chat)
        mem._conn.execute("ALTER TABLE source_occurrences ADD COLUMN future_answer_id JSON")
        mem._conn.execute(
            "UPDATE source_occurrences SET future_answer_id = ? WHERE occurrence_id = ?",
            ('{"nested":["ORCHID-\\u0037\\u0037"]}', occurrence.occurrence_id),
        )
        receipt = mem._conn.execute(
            "SELECT receipt_id, facts_json FROM memory_receipts WHERE occurrence_id = ?", (occurrence.occurrence_id,),
        ).fetchone()
        facts = json.loads(receipt["facts_json"])
        assert facts
        facts[0]["future_annotation"] = {"value": "ORCHID-77"}
        mem._conn.execute("UPDATE memory_receipts SET facts_json = ? WHERE receipt_id = ?", (json.dumps(facts), receipt["receipt_id"]))
        mem._conn.commit()
        _forget(profile, chat, "ORCHID-77")
        remaining = mem._conn.execute("SELECT future_answer_id FROM source_occurrences WHERE occurrence_id = ?", (occurrence.occurrence_id,)).fetchone()
        assert remaining is None or "ORCHID-77" not in str(json.loads(remaining[0] or "null"))
        rows = mem._conn.execute("SELECT * FROM memory_receipts WHERE chat_scope = ?", (chat,)).fetchall()
        assert "ORCHID-77" not in str([dict(row) for row in rows])
    finally:
        mem.close()


def test_final_verifier_detects_raw_append_after_log_sweep(tmp_path, monkeypatch):
    import core.context_retrieval as retrieval
    from core.memory.files import conversation_log_path

    profile = _profile(tmp_path)
    chat = "late-raw-copy"
    _ingest(profile, chat, [(1760000000, "The locker code is ORCHID-77.")])
    original = retrieval.forget_session_memory

    def append_after_sweep(*args, **kwargs):
        removed = original(*args, **kwargs)
        # Bypass the cooperative writer fence to model a legacy/in-flight
        # writer. The final verifier must detect this actual retained copy.
        with conversation_log_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({"session_id": chat, "user": "", "assistant": "",
                                     "artifact": {"future_value": "ORCHID-77"}}) + "\n")
        return removed

    monkeypatch.setattr(retrieval, "forget_session_memory", append_after_sweep)
    reply = _forget(profile, chat, "ORCHID-77")
    assert any("ORCHID-77" in str(row) for row in _raw_rows(conversation_log_path()))
    assert not str(reply).startswith("Forget applied"), reply
    assert "conversation log" in str(reply).lower(), reply


def test_session_metadata_and_backup_are_part_of_verified_erasure(tmp_path):
    from core.memory.entries import set_session_meta
    from core.memory.files import chat_session_meta_path

    profile = _profile(tmp_path)
    chat = 'metadata-carrier'
    _ingest(profile, chat, [(1760000000, 'My locker code is MINT-84.')])
    set_session_meta(chat, title='Locker MINT-84')
    set_session_meta(chat, emoji="🔒")
    reply = _forget(profile, chat, 'MINT-84')
    path = chat_session_meta_path()
    retained = [p.read_text() for p in (path, path.with_name(path.name + '.bak')) if p.exists()]
    assert 'MINT-84' not in str(retained), (str(reply), retained)


def test_session_metadata_writer_cannot_reintroduce_completed_revocation(tmp_path):
    from core.memory.entries import set_session_meta
    from core.memory.files import chat_session_meta_path

    profile = _profile(tmp_path)
    chat = 'metadata-writer'
    _ingest(profile, chat, [(1760000000, 'My locker code is MINT-84.')])
    _forget(profile, chat, 'MINT-84')
    try:
        set_session_meta(chat, title='Locker MINT-84')
    except RuntimeError:
        pass
    path = chat_session_meta_path()
    assert not path.exists() or 'MINT-84' not in path.read_text()
