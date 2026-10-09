"""Retained memory holders have verified erasure or fail-closed readers."""
import json
import sqlite3

import pytest

from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _forget_command as command
from tests.test_forgetting_is_applied_or_never_claimed_20261007 import _ts as ts
from tests.test_question_date_time_leg_20261002 import _hash_backend as _hash_backend
from tests.test_question_date_time_leg_20261002 import _ingest, _profile

pytestmark = pytest.mark.usefixtures("_hash_backend")


@pytest.fixture(autouse=True)
def _holder_runtime_home(tmp_path):
    import core.runtime_paths as paths

    previous = paths._VOOL_HOME_OVERRIDE
    paths.configure_runtime_home(_profile(tmp_path))
    try:
        yield
    finally:
        paths.configure_runtime_home(previous)


def _seed(tmp_path, chat):
    from core.memory.files import memory_entries_path

    profile = _profile(tmp_path)
    assert memory_entries_path().is_relative_to(profile)
    _ingest(profile, chat, [(ts("2026-02-03T09:00:00"), "My locker code is MINT-84.")])
    return profile


def test_profile_files_are_erased_and_unrelated_values_survive(tmp_path):
    from core.context_namespace import grant_context_import
    from core.memory.files import append_jsonl, load_jsonl, operator_dense_profile_path, user_heuristics_path

    chat = "r6-profile-files"
    profile = _seed(tmp_path, chat)
    grant_context_import(chat, scope="user_profile", source_id="profile:confirmed")
    path = user_heuristics_path()
    append_jsonl(path, {"heuristic_id": "locker", "scope": "user_profile", "text": "MINT-84",
                        "origin_chat_id": chat, "status": "active", "provenance": {"kind": "direct_user_observation"}})
    append_jsonl(path, {"heuristic_id": "unrelated", "scope": "user_profile", "text": "blue notebook",
                        "origin_chat_id": chat, "status": "active", "provenance": {"kind": "direct_user_observation"}})
    operator_dense_profile_path().write_text(json.dumps({"memory_facts": ["MINT-84", "blue notebook"],
                                                       "dense_summary": "Locker: MINT-84."}))
    handled, reply = command(profile, chat, "forget MINT-84")
    assert handled and "Forget applied" in reply, reply
    assert "MINT-84" not in str(load_jsonl(path))
    assert "blue notebook" in str(load_jsonl(path))
    dense = json.loads(operator_dense_profile_path().read_text())
    assert "MINT-84" not in str(dense) and "blue notebook" in str(dense)


def test_unscoped_prompt_block_is_named_incomplete_and_withheld(tmp_path):
    from core.vool_memory import VoolMemory

    chat = "r6-block"
    profile = _seed(tmp_path, chat)
    mem = VoolMemory(runtime_home=profile)
    try:
        mem.block_write("user_profile", "The locker code is MINT-84.")
        assert "MINT-84" in mem.block_read("user_profile")
        mem._conn.execute("""
            CREATE TRIGGER reject_block_erasure BEFORE DELETE ON memory_blocks
            BEGIN SELECT RAISE(ABORT, 'block storage unavailable'); END;
        """)
        mem._conn.commit()
        handled, reply = command(profile, chat, "forget MINT-84")
        assert handled and "memory blocks" in reply and "could not be verified" in reply, reply
        assert "MINT-84" in mem._conn.execute("SELECT content FROM memory_blocks").fetchone()[0]
        assert mem.block_read("user_profile") is None
        assert mem.blocks_for_prompt(["user_profile"]) == ""
    finally:
        mem.close()


def test_unreadable_block_revocation_authority_withholds_content(tmp_path):
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    mem = VoolMemory(runtime_home=profile)
    try:
        mem.block_write("user_profile", "The notebook is blue.")

        def deny_gate(action, table, column, database, origin):
            if action == sqlite3.SQLITE_READ and table == "memory_erasure_operations":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        mem._conn.set_authorizer(deny_gate)
        assert mem.block_read("user_profile") is None
    finally:
        mem._conn.set_authorizer(None)
        mem.close()


def test_scoped_packet_copy_uses_all_schema_columns_and_preserves_foreign_chat(tmp_path):
    from storage.db import get_connection

    chat = "r6-packet-copy"
    profile = _seed(tmp_path, chat)
    conn = get_connection()
    try:
        conn.execute("CREATE TABLE saved_packet_cache (copy_id TEXT PRIMARY KEY, chat_id TEXT, payload_json JSON, extra_at)")
        conn.executemany("INSERT INTO saved_packet_cache VALUES (?, ?, ?, ?)", [
            ("own", chat, "{}", "MINT-84"),
            ("foreign", "other-chat", '{"note":"MINT-84"}', "kept"),
            ("safe", chat, '{"note":"blue notebook"}', "kept"),
        ])
        conn.commit()
        handled, reply = command(profile, chat, "forget MINT-84")
        assert handled and "Forget applied" in reply, reply
        rows = conn.execute("SELECT copy_id FROM saved_packet_cache ORDER BY copy_id").fetchall()
        assert [row[0] for row in rows] == ["foreign", "safe"]
    finally:
        conn.close()


def test_unscoped_tool_cache_is_retained_but_never_returned(tmp_path):
    from core.tool_memo import lookup, record
    from storage.db import get_connection

    chat = "r6-tool-cache"
    profile = _seed(tmp_path, chat)
    record("fixture_tool", "fixture-key", {"value": "MINT-84"})
    assert lookup("fixture_tool", "fixture-key")[0]["value"] == "MINT-84"
    handled, reply = command(profile, chat, "forget MINT-84")
    assert handled and "persisted tool memo cache" in reply and "could not be verified" in reply, reply
    conn = get_connection()
    try:
        assert "MINT-84" in conn.execute("SELECT result_json FROM tool_memo_cache").fetchone()[0]
    finally:
        conn.close()
    assert lookup("fixture_tool", "fixture-key") is None


def _complete_revocation(profile, chat, token):
    from core.vool_memory import VoolMemory

    mem = VoolMemory(runtime_home=profile)
    try:
        operation = mem.begin_erasure(chat, token)
        mem.record_revocation(token, chat_id=chat)
        mem.complete_erasure(operation)
        assert not mem.erasure_incomplete(chat)
    finally:
        mem.close()


@pytest.mark.parametrize("token,encoded", [
    ("Žalias-84", '{"value":"\\u017dalias-84"}'),
    ("MINT-84", '{"value":"M\\u0049NT-84"}'),
])
def test_tool_cache_decodes_escaped_payload_before_completed_revocation_gate(tmp_path, token, encoded):
    from core.tool_memo import lookup, record
    from storage.db import get_connection

    profile = _profile(tmp_path)
    key = "r6-escaped-reader-" + token
    record("fixture_tool", key, {"value": token})
    conn = get_connection()
    try:
        conn.execute("UPDATE tool_memo_cache SET result_json = ? WHERE memo_key = ?", (encoded, key))
        conn.commit()
        assert lookup("fixture_tool", key)[0] == {"value": token}
        _complete_revocation(profile, "r6-unicode-cache", token)
        assert conn.execute("SELECT result_json FROM tool_memo_cache WHERE memo_key = ?", (key,)).fetchone()[0] == encoded
        assert lookup("fixture_tool", key) is None
    finally:
        conn.execute("DELETE FROM tool_memo_cache WHERE memo_key = ?", (key,))
        conn.commit()
        conn.close()


def test_malformed_tool_cache_json_is_withheld_without_counting_a_hit(tmp_path):
    from core.tool_memo import lookup, record
    from storage.db import get_connection

    _profile(tmp_path)
    key = "r6-malformed-reader"
    record("fixture_tool", key, {"value": "blue notebook"})
    conn = get_connection()
    try:
        conn.execute("UPDATE tool_memo_cache SET result_json = ? WHERE memo_key = ?", ('{"value":', key))
        conn.commit()
        assert lookup("fixture_tool", key) is None
        assert conn.execute("SELECT hits FROM tool_memo_cache WHERE memo_key = ?", (key,)).fetchone()[0] == 0
    finally:
        conn.execute("DELETE FROM tool_memo_cache WHERE memo_key = ?", (key,))
        conn.commit()
        conn.close()


@pytest.mark.parametrize("carrier", ["packet_text", "facts"])
def test_snapshot_unicode_payload_is_withheld_after_completed_revocation(tmp_path, monkeypatch, carrier):
    import core.context_retrieval as retrieval
    from core.evidence_kernel.snapshot import packet_facts_for

    profile = _profile(tmp_path)
    chat = "r6-unicode-snapshot"
    token = "Žalias-84"
    packet_text = token if carrier == "packet_text" else "Scoped packet"
    facts = [{"value": token if carrier == "facts" else "blue notebook"}]
    telemetry = {"evidence_packet_facts": facts,
                 "evidence_packet_snapshot": {"chat_id": chat, "packet_text": packet_text}}
    monkeypatch.setattr(retrieval, "get_last_retrieval_telemetry", lambda: telemetry)
    assert packet_facts_for(packet_text, chat) == facts
    _complete_revocation(profile, chat, token)
    assert packet_facts_for(packet_text, chat) == []


def test_opaque_packet_copy_prevents_verified_outcome(tmp_path):
    from storage.db import get_connection

    chat = "r6-opaque-packet"
    profile = _seed(tmp_path, chat)
    conn = get_connection()
    try:
        conn.execute("CREATE TABLE encoded_packet_cache (copy_id TEXT PRIMARY KEY, chat_id TEXT, payload BLOB)")
        conn.execute("INSERT INTO encoded_packet_cache VALUES (?, ?, ?)", ("opaque", chat, b"MINT-84"))
        conn.commit()
        handled, reply = command(profile, chat, "forget MINT-84")
        assert handled and "encoded packet cache" in reply and "could not be verified" in reply, reply
        assert conn.execute("SELECT payload FROM encoded_packet_cache").fetchone()[0] == b"MINT-84"
    finally:
        conn.close()


def test_retained_receipt_metadata_is_incomplete_and_ledger_reader_withholds(tmp_path):
    from core.evidence_kernel.receipts import issue, ledger_path, read_ledger, verify_chain

    chat = "r6-ledger-copy"
    profile = _seed(tmp_path, chat)
    kind = "vool.memory.packet.v1"
    issue(kind=kind, session_id=chat, subject_type="evidence_packet", subject="fixture",
          metadata={"stored_fact": "MINT-84"})
    assert any("MINT-84" in str(row) for row in read_ledger(kind, chat))
    handled, reply = command(profile, chat, "forget MINT-84")
    assert handled and "packet receipt ledgers" in reply and "could not be verified" in reply, reply
    assert "MINT-84" in ledger_path(kind, chat).read_text()
    assert read_ledger(kind, chat) == []
    assert verify_chain(kind, chat)[0]
    path = ledger_path(kind, chat)
    path.write_text(path.read_text().replace("MINT-84", "MINT-85"))
    assert not verify_chain(kind, chat)[0]


def test_pending_operation_withholds_dense_profile_and_heuristics(tmp_path):
    from core.context_namespace import grant_context_import
    from core.memory.entries import resolve_memory_access_policy, search_user_heuristics
    from core.memory.files import append_jsonl, operator_dense_profile_path, user_heuristics_path
    from core.memory.learning import load_operator_dense_profile
    from core.vool_memory import VoolMemory

    chat = "r6-retained-profile"
    profile = _seed(tmp_path, chat)
    grant_context_import(chat, scope="user_profile", source_id="profile:confirmed")
    append_jsonl(user_heuristics_path(), {"heuristic_id": "locker", "scope": "user_profile", "text": "MINT-84",
                                        "keywords": ["locker"], "origin_chat_id": chat, "status": "active",
                                        "source_id": "profile:confirmed",
                                        "provenance": {"kind": "direct_user_observation", "source_id": "profile:confirmed"}})
    operator_dense_profile_path().write_text('{"dense_summary":"MINT-84"}')
    policy = resolve_memory_access_policy(chat_id=chat)
    assert load_operator_dense_profile()["dense_summary"] == "MINT-84"
    assert search_user_heuristics("locker", access_policy=policy)
    mem = VoolMemory(runtime_home=profile)
    try:
        mem.begin_erasure(chat, "MINT-84")
    finally:
        mem.close()
    assert load_operator_dense_profile() == {}
    assert search_user_heuristics("locker", access_policy=policy) == []


def test_generation_changes_on_pending_completed_and_revoked_states(tmp_path):
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    mem = VoolMemory(runtime_home=profile)
    try:
        before = mem.revocation_generation("r6-generation")
        operation = mem.begin_erasure("r6-generation", "MINT-84")
        pending = mem.revocation_generation("r6-generation")
        mem.complete_erasure(operation)
        completed = mem.revocation_generation("r6-generation")
        mem.record_revocation("MINT-84", chat_id="r6-generation")
        revoked = mem.revocation_generation("r6-generation")
        assert before != pending != completed != revoked
        assert mem.revocation_generation("another-chat") == ()
    finally:
        mem.close()


def test_persisted_bootstrap_snapshot_is_incomplete_and_reader_withholds(tmp_path):
    from core.bootstrap_adapters import FileTopicAdapter

    chat = "r6-bootstrap-copy"
    profile = _seed(tmp_path, chat)
    adapter = FileTopicAdapter()
    assert adapter.publish_snapshot("fixture", {"records": [{"content": "MINT-84"}]})
    assert adapter.fetch_snapshot("fixture")["records"][0]["content"] == "MINT-84"
    handled, reply = command(profile, chat, "forget MINT-84")
    assert handled and "bootstrap snapshots" in reply and "could not be verified" in reply, reply
    assert "MINT-84" in adapter._topic_path("fixture").read_text()
    assert adapter.fetch_snapshot("fixture") is None


def test_remote_mirror_without_erasure_receipt_is_named_incomplete(tmp_path, monkeypatch):
    chat = "r6-remote-copy"
    profile = _seed(tmp_path, chat)
    monkeypatch.setenv("VOOL_MIRROR_URL", "https://mirror.invalid")
    handled, reply = command(profile, chat, "forget MINT-84")
    assert handled and "remote mirror copies" in reply and "could not be verified" in reply, reply


@pytest.mark.parametrize("carrier", ["keywords", "tags", "context_description"])
def test_semantic_metadata_only_copy_is_erased_with_content_and_foreign_chat_preserved(tmp_path, carrier):
    from core.vool_memory import VoolMemory, _requested_session_scope

    profile = _profile(tmp_path)
    chat = "r6-node-metadata"
    scope = _requested_session_scope(chat)
    mem = VoolMemory(runtime_home=profile)
    try:
        kwargs = {"content": "The notebook is blue.", "keywords": ["notebook"],
                  "tags": ["session:" + scope], "context_description": "session=" + scope,
                  "embedding": []}
        if carrier == "context_description":
            kwargs[carrier] += " Locker code: MINT-84."
        else:
            kwargs[carrier].append("MINT-84")
        node = mem.node_store(**kwargs)
        if carrier in {"keywords", "tags"}:
            encoded = json.dumps(kwargs[carrier]).replace("MINT", "M\\u0049NT")
            mem._conn.execute(f"UPDATE memory_nodes SET {carrier} = ? WHERE node_id = ?", (encoded, node.node_id))
            mem._conn.commit()
        foreign_scope = _requested_session_scope("another-chat")
        foreign = mem.node_store("The foreign notebook is red.", ["MINT-84"], ["session:" + foreign_scope],
                                 "session=" + foreign_scope + " MINT-84", [])
        assert mem.node_invalidate_matching("MINT-84", session_id=chat) == 1
        rows = mem._conn.execute("SELECT * FROM memory_nodes WHERE node_id != ?", (foreign.node_id,)).fetchall()
        assert len(rows) == 1 and rows[0]["content"] == "The notebook is blue."
        retained = {column: json.loads(rows[0][column]) if column in {"keywords", "tags"} else rows[0][column]
                    for column in ("content", "keywords", "tags", "context_description")}
        assert "MINT-84" not in str(retained)
        assert "session:" + scope in retained["tags"]
        assert "MINT-84" in mem._conn.execute("SELECT keywords FROM memory_nodes WHERE node_id = ?",
                                             (foreign.node_id,)).fetchone()[0]
    finally:
        mem.close()


def test_semantic_salvage_removes_carrying_metadata_together_with_body_clause(tmp_path):
    from core.vool_memory import VoolMemory, _requested_session_scope

    profile = _profile(tmp_path)
    chat = "r6-node-salvage"
    scope = _requested_session_scope(chat)
    mem = VoolMemory(runtime_home=profile)
    try:
        mem.node_store("The locker code is MINT-84. The notebook is blue.", ["MINT-84", "notebook"],
                       ["session:" + scope, "code:MINT-84"], "session=" + scope + " Locker: MINT-84.", [])
        assert mem.node_invalidate_matching("MINT-84", session_id=chat) == 1
        rows = mem._conn.execute("SELECT content, keywords, tags, context_description FROM memory_nodes").fetchall()
        assert len(rows) == 1 and "notebook is blue" in rows[0][0]
        assert "MINT-84" not in str(tuple(rows[0]))
        assert "MINT-84" not in str([tuple(row) for row in mem._conn.execute("SELECT content, keywords FROM memory_fts")])
    finally:
        mem.close()


def test_semantic_numeric_erasure_preserves_canonical_scope_digest(tmp_path):
    from core.vool_memory import VoolMemory, _requested_session_scope

    profile = _profile(tmp_path)
    chat = "salv-wall"
    scope = _requested_session_scope(chat)
    assert "4" in scope
    mem = VoolMemory(runtime_home=profile)
    try:
        mem.node_store("My novice lane grade is 4 and my lead wall grade is 6.", ["grade", "lead"],
                       ["session:" + scope], "session=" + scope + " role=user", [])
        assert mem.node_invalidate_matching("4", session_id=chat) == 1
        row = mem._conn.execute("SELECT content, tags, context_description FROM memory_nodes").fetchone()
        assert row["content"] == "my lead wall grade is 6."
        assert "session:" + scope in json.loads(row["tags"])
        assert "session=" + scope in row["context_description"]
        assert mem.node_invalidate_matching("4", session_id=chat) == 0
    finally:
        mem.close()


@pytest.mark.parametrize("scoped", [True, False])
def test_holder_gate_uses_one_snapshot_for_pending_and_revoked_authority(tmp_path, scoped):
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "r6-gate-race"
    reader = VoolMemory(runtime_home=profile)
    writer = VoolMemory(runtime_home=profile)
    started = []

    def start_erasure_before_revocation_read(action, table, column, database, trigger):
        if action == sqlite3.SQLITE_READ and table == "memory_revocations" and not started:
            started.append(writer.begin_erasure(chat, "MINT-84"))
        return sqlite3.SQLITE_OK

    try:
        reader._conn.set_authorizer(start_erasure_before_revocation_read)
        assert not reader.payload_is_current("Locker code MINT-84", chat_id=chat if scoped else None)
        assert started and writer.erasure_incomplete(chat)
    finally:
        reader._conn.set_authorizer(None)
        reader.close()
        writer.close()


@pytest.mark.parametrize("declaration,column", [("JSON", "payload"), ("TEXT", "payload_json")])
def test_schema_owned_json_escapes_are_decoded_before_packet_erasure(tmp_path, declaration, column):
    from core.persistent_memory import _forget_persisted_copies
    from storage.db import get_connection

    _profile(tmp_path)
    conn = get_connection()
    try:
        conn.execute(f"CREATE TABLE escaped_packet_cache (copy_id TEXT PRIMARY KEY, chat_id TEXT, {column} {declaration})")
        conn.execute("INSERT INTO escaped_packet_cache VALUES (?, ?, ?)",
                     ("own", "r6-escaped-packet", '{"value":"M\\u0049NT-84"}'))
        conn.commit()
        outcomes = {}
        _forget_persisted_copies("MINT-84", chat_id="r6-escaped-packet", erasures=outcomes)
        assert conn.execute("SELECT COUNT(*) FROM escaped_packet_cache").fetchone()[0] == 0
        assert outcomes["stores"]["persisted_escaped_packet_cache"]["status"] == "verified"
    finally:
        conn.execute("DROP TABLE escaped_packet_cache")
        conn.commit()
        conn.close()


def test_invalid_structured_packet_json_is_named_incomplete(tmp_path):
    from core.persistent_memory import _forget_persisted_copies
    from storage.db import get_connection

    _profile(tmp_path)
    conn = get_connection()
    try:
        conn.execute("CREATE TABLE invalid_packet_cache (copy_id TEXT PRIMARY KEY, chat_id TEXT, payload_json TEXT)")
        conn.execute("INSERT INTO invalid_packet_cache VALUES (?, ?, ?)",
                     ("own", "r6-invalid-packet", '{"value":"MINT-84"'))
        conn.commit()
        outcomes = {}
        _forget_persisted_copies("MINT-84", chat_id="r6-invalid-packet", erasures=outcomes)
        assert conn.execute("SELECT COUNT(*) FROM invalid_packet_cache").fetchone()[0] == 1
        assert outcomes["stores"]["persisted_invalid_packet_cache"]["status"] == "incomplete"
    finally:
        conn.execute("DROP TABLE invalid_packet_cache")
        conn.commit()
        conn.close()


@pytest.mark.parametrize("needle", ["4", "value"])
def test_receipt_erasure_excludes_dates_offsets_and_schema_field_names(tmp_path, needle):
    from core.memory_receipts import write_receipt
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "r6-receipt-metadata"
    mem = VoolMemory(runtime_home=profile)
    try:
        occurrence = mem.occurrence_store(chat_scope=chat, role="user", body="my lead wall grade is 6.",
                                          authority="observed-user-statement", statement_at=ts("2026-04-04T09:00:00"))
        write_receipt(mem, occurrence, chat_scope=chat)
        row = mem._conn.execute("SELECT receipt_id, head_text, facts_json FROM memory_receipts WHERE occurrence_id = ?",
                                (occurrence.occurrence_id,)).fetchone()
        assert row is not None and "2026-04-04" in row["head_text"] and "24" in row["facts_json"]
        assert '"value"' in row["facts_json"]
        assert mem._forget_derived_receipts(needle, chat_scope=chat) == 0
        assert mem._conn.execute("SELECT receipt_id FROM memory_receipts WHERE occurrence_id = ?",
                                 (occurrence.occurrence_id,)).fetchone()[0] == row["receipt_id"]
    finally:
        mem.close()


@pytest.mark.parametrize("carrier", ["head_text", "facts_json"])
def test_stale_receipt_copy_is_rebuilt_from_structured_source(tmp_path, carrier):
    from core.memory_receipts import write_receipt
    from core.vool_memory import VoolMemory

    profile = _profile(tmp_path)
    chat = "r6-receipt-copy"
    mem = VoolMemory(runtime_home=profile)
    try:
        occurrence = mem.occurrence_store(chat_scope=chat, role="user", body="my lead wall grade is 6.",
                                          authority="observed-user-statement")
        write_receipt(mem, occurrence, chat_scope=chat)
        row = mem._conn.execute("SELECT head_text, facts_json FROM memory_receipts WHERE occurrence_id = ?",
                                (occurrence.occurrence_id,)).fetchone()
        if carrier == "head_text":
            stale = row[carrier] + "\nMINT-84"
        else:
            facts = json.loads(row[carrier])
            facts[0]["sentence"] = "MINT-84"
            stale = json.dumps(facts).replace("MINT", "M\\u0049NT")
        mem._conn.execute(f"UPDATE memory_receipts SET {carrier} = ? WHERE occurrence_id = ?",
                          (stale, occurrence.occurrence_id))
        mem._conn.commit()
        assert mem._forget_derived_receipts("MINT-84", chat_scope=chat) == 1
        retained = mem._conn.execute("SELECT head_text, facts_json FROM memory_receipts WHERE occurrence_id = ?",
                                     (occurrence.occurrence_id,)).fetchone()
        assert "MINT-84" not in retained["head_text"]
        assert "MINT-84" not in str(json.loads(retained["facts_json"]))
        assert "lead wall grade is 6" in retained["head_text"]
    finally:
        mem.close()
