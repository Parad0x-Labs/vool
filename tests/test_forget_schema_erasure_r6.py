"""Schema evolution and read-return authority remain within the erasure contract."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import core.context_retrieval as retrieval
from core.memory.entries import MemoryErasureError
from core.runtime_paths import configure_runtime_home
from core.vool_memory import VoolMemory


@pytest.fixture
def schema_store(tmp_path: Path, monkeypatch):
    home = tmp_path / "profile"
    configure_runtime_home(home)
    database = tmp_path / "derivatives.sqlite3"

    def connection():
        conn = sqlite3.connect(database)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr("storage.db.get_connection", connection)
    yield database, home
    configure_runtime_home(None)


def seed(database: Path, sql: str) -> None:
    with sqlite3.connect(database) as conn:
        conn.executescript(sql)


def rows(database: Path, table: str):
    with sqlite3.connect(database) as conn:
        return conn.execute(f"SELECT * FROM {table}").fetchall()


def test_absent_optional_stores_are_verified_absent(schema_store):
    _database, _home = schema_store
    status = {}
    retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84", erasures=status)
    for table in (
        "dialogue_topic_archives", "dialogue_turns", "dialogue_sessions",
        "learning_shards", "response_feedback", "context_capsule_versions",
    ):
        assert status["stores"][table]["status"] == "not_applicable"
    assert not any(value for name, value in status.items() if name.endswith("_failures"))


def test_missing_optional_columns_do_not_prevent_present_column_erasure(schema_store):
    database, _home = schema_store
    seed(database, """
        CREATE TABLE dialogue_turns(turn_id TEXT PRIMARY KEY, session_id TEXT, raw_input TEXT);
        INSERT INTO dialogue_turns VALUES ('target', 'chat-a', 'My token is MINT-84.');
        INSERT INTO dialogue_turns VALUES ('sibling', 'chat-a', 'My token is PEAR-42.');
        INSERT INTO dialogue_turns VALUES ('foreign', 'chat-b', 'My token is MINT-84.');
    """)
    status = {}
    retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84", erasures=status)
    assert rows(database, "dialogue_turns") == [
        ('sibling', 'chat-a', 'My token is PEAR-42.'),
        ('foreign', 'chat-b', 'My token is MINT-84.'),
    ]
    assert status["stores"]["dialogue_turns"] == {"status": "verified", "removed": 1}


def test_new_text_and_typed_json_columns_are_scrubbed_from_session_state(schema_store):
    database, _home = schema_store
    seed(database, """
        CREATE TABLE dialogue_sessions(
            session_id TEXT PRIMARY KEY, future_projection JSON, assistant_commitments_json TEXT,
            future_summary TEXT, current_user_goal TEXT
        );
        INSERT INTO dialogue_sessions VALUES
            ('chat-a', '{"code":"MINT-84"}', '["MINT-84"]', 'MINT-84', 'Keep PEAR-42'),
            ('chat-b', '{"code":"MINT-84"}', '["MINT-84"]', 'MINT-84', 'Keep PEAR-42');
    """)
    status = {}
    retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84", erasures=status)
    remaining = rows(database, "dialogue_sessions")
    assert remaining[0] == ('chat-a', '{}', '[]', '', 'Keep PEAR-42')
    assert remaining[1] == ('chat-b', '{"code":"MINT-84"}', '["MINT-84"]', 'MINT-84', 'Keep PEAR-42')
    assert status["stores"]["dialogue_sessions"]["status"] == "verified"


def test_feedback_and_persisted_packets_are_scoped_and_verified(schema_store):
    database, _home = schema_store
    seed(database, """
        CREATE TABLE response_feedback(id INTEGER PRIMARY KEY, session_id TEXT, context_snapshot TEXT, user_correction TEXT);
        CREATE TABLE context_packet_cache(id INTEGER PRIMARY KEY, chat_id TEXT, packet_json JSON);
        INSERT INTO response_feedback VALUES (1, 'chat-a', 'MINT-84', 'correction'), (2, 'chat-b', 'MINT-84', 'foreign');
        INSERT INTO context_packet_cache VALUES (1, 'chat-a', '{"code":"MINT-84"}'), (2, 'chat-b', '{"code":"MINT-84"}');
    """)
    status = {}
    retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84", erasures=status)
    assert rows(database, "response_feedback") == [(2, 'chat-b', 'MINT-84', 'foreign')]
    assert rows(database, "context_packet_cache") == [(2, 'chat-b', '{"code":"MINT-84"}')]
    assert status["stores"]["response_feedback"]["status"] == "verified"
    assert status["stores"]["context_packet_cache"]["status"] == "verified"


def test_store_failure_is_collected_and_other_stores_are_still_swept(schema_store):
    database, _home = schema_store
    seed(database, """
        CREATE TABLE dialogue_topic_archives(id INTEGER PRIMARY KEY, session_id TEXT, summary TEXT);
        CREATE TABLE dialogue_turns(turn_id TEXT PRIMARY KEY, session_id TEXT, raw_input TEXT);
        INSERT INTO dialogue_topic_archives VALUES (1, 'chat-a', 'MINT-84');
        INSERT INTO dialogue_turns VALUES ('target', 'chat-a', 'MINT-84');
        CREATE TRIGGER reject_erasure BEFORE DELETE ON dialogue_topic_archives
        BEGIN SELECT RAISE(ABORT, 'write unavailable'); END;
    """)
    status = {}
    retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84", erasures=status)
    assert status["stores"]["dialogue_topic_archives"]["status"] == "incomplete"
    assert status["stores"]["dialogue_turns"]["status"] == "verified"
    assert rows(database, "dialogue_turns") == []
    assert rows(database, "dialogue_topic_archives") == [(1, 'chat-a', 'MINT-84')]
    with pytest.raises(MemoryErasureError, match="incomplete"):
        retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84")


def test_dialogue_close_failure_invalidates_collected_and_direct_outcomes(schema_store, monkeypatch):
    database, _home = schema_store

    class FailingClose(sqlite3.Connection):
        def close(self):
            super().close()
            raise OSError("connection close failed")

    def connection():
        conn = sqlite3.connect(database, factory=FailingClose)
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr("storage.db.get_connection", connection)
    status = {}
    retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84", erasures=status)
    assert status["stores"]["dialogue_connection"]["status"] == "incomplete"
    with pytest.raises(MemoryErasureError, match="incomplete"):
        retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84")


def test_generation_change_after_authority_read_withholds_built_delivery(tmp_path, monkeypatch):
    home = tmp_path / "profile"
    configure_runtime_home(home)
    context = {"runtime_home": str(home)}
    transcript = [{"role": "user", "content": "What is the code?"}]
    delivery = [{"role": "system", "content": "The code is MINT-84."}, *transcript]
    read = retrieval._chat_revocations
    calls = []

    def revoke_after_read(*args):
        authority = read(*args)
        calls.append(authority)
        mem = VoolMemory(runtime_home=str(home))
        try:
            mem.record_revocation("MINT-84", chat_id="chat-a")
        finally:
            mem.close()
        return authority

    try:
        monkeypatch.setattr(retrieval, "_chat_revocations", revoke_after_read)
        assert retrieval._verify_memory_delivery(delivery, transcript, "chat-a", context) is transcript
        assert calls == [()]
    finally:
        configure_runtime_home(None)


def test_unchanged_generation_preserves_built_delivery(tmp_path):
    home = tmp_path / "profile"
    configure_runtime_home(home)
    transcript = [{"role": "user", "content": "What is the code?"}]
    delivery = [{"role": "system", "content": "The code is PEAR-42."}, *transcript]
    try:
        result = retrieval._verify_memory_delivery(delivery, transcript, "chat-a", {"runtime_home": str(home)})
        assert result is delivery
    finally:
        configure_runtime_home(None)


def test_session_summary_json_checks_absence_and_all_payload_fields(schema_store):
    from core.memory.files import session_summaries_path

    _database, _home = schema_store
    summary_path = session_summaries_path()
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    original = (
        '{"session_id":"chat-a","future_note":"MINT-84","summary":"neutral"}\n'
        '{"session_id":"chat-b","future_note":"MINT-84","summary":"foreign"}\n'
    )
    summary_path.write_text(original, encoding="utf-8")
    absent = {}
    retrieval._sweep_derived_dialogue_surfaces("chat-a", "PLUM-99", erasures=absent)
    assert absent["stores"]["session_summaries"] == {"status": "verified", "removed": 0}
    assert summary_path.read_text(encoding="utf-8") == original
    erased = {}
    retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84", erasures=erased)
    assert erased["stores"]["session_summaries"] == {"status": "verified", "removed": 1}
    from core.memory.entries import _load_erasure_rows

    assert _load_erasure_rows(summary_path) == [
        {"session_id": "chat-b", "future_note": "MINT-84", "summary": "foreign"},
    ]


@pytest.mark.parametrize("column, declaration", [("payload_json", "TEXT"), ("future_payload", "JSON")])
def test_escaped_json_values_are_erased_using_decoded_content(schema_store, column, declaration):
    database, _home = schema_store
    escaped = r'{"nested":[{"text":"M\u0049NT-84","label":"caf\u00e9"}]}'
    with sqlite3.connect(database) as conn:
        conn.execute(f"CREATE TABLE dialogue_sessions(session_id TEXT PRIMARY KEY, {column} {declaration})")
        conn.executemany(
            f"INSERT INTO dialogue_sessions(session_id, {column}) VALUES (?, ?)",
            [("chat-a", escaped), ("chat-b", escaped)],
        )
    status = {}
    retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84", erasures=status)
    assert rows(database, "dialogue_sessions") == [("chat-a", "{}"), ("chat-b", escaped)]
    assert status["stores"]["dialogue_sessions"]["status"] == "verified"


def test_malformed_structured_payload_cannot_be_verified_absent(schema_store):
    database, _home = schema_store
    seed(database, """
        CREATE TABLE dialogue_sessions(session_id TEXT PRIMARY KEY, future_payload JSON);
        INSERT INTO dialogue_sessions VALUES ('chat-a', '{"text":broken}');
    """)
    status = {}
    retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84", erasures=status)
    assert status["stores"]["dialogue_sessions"]["status"] == "incomplete"
    assert rows(database, "dialogue_sessions") == [("chat-a", '{"text":broken}')]
    with pytest.raises(MemoryErasureError, match="incomplete"):
        retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84")


@pytest.mark.parametrize("replacement", [r'{"text":"M\u0049NT-84"}', '{"text":broken}'])
def test_verification_decodes_rewritten_json_and_refuses_invalid_payload(schema_store, replacement):
    database, _home = schema_store
    seed(database, """
        CREATE TABLE dialogue_sessions(session_id TEXT PRIMARY KEY, payload_json TEXT);
        INSERT INTO dialogue_sessions VALUES ('chat-a', '{"text":"MINT-84"}');
    """)
    with sqlite3.connect(database) as conn:
        conn.execute(
            "CREATE TRIGGER replace_scrubbed_json AFTER UPDATE ON dialogue_sessions "
            "WHEN NEW.payload_json = '{}' BEGIN "
            "UPDATE dialogue_sessions SET payload_json = '" + replacement + "' "
            "WHERE session_id = NEW.session_id; END"
        )
    status = {}
    retrieval._sweep_derived_dialogue_surfaces("chat-a", "MINT-84", erasures=status)
    assert status["stores"]["dialogue_sessions"]["status"] == "incomplete"
    assert rows(database, "dialogue_sessions") == [("chat-a", replacement)]
