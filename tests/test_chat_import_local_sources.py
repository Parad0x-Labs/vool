"""Reading other AI apps' chat stores on this computer, from synthetic stores in a temp home.

Each store is built the way its app writes it (WAL databases with a writer still open, so the
newest rows live only in the write-ahead log), then read through the public readers. The tests
pin what a person would see after a move-in (which chats, which words, which order) and that the
other app's files are never changed, including the ``-wal`` and ``-shm`` files.
"""
from __future__ import annotations

import builtins
import hashlib
import json
import os
import sqlite3
from pathlib import Path

import pytest
import zstandard

from core.chat_import import local_agent_sessions_db as agent_db
from core.chat_import import local_editor_kv as editor_kv
from core.chat_import import local_sources
from core.chat_import import local_transcript_tree as transcript_tree
from core.chat_import.common import Limits
from core.chat_import.sqlite_snapshot import SnapshotError, snapshot_connection

LIMITS = Limits()


def _digest(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def _fingerprint(db: Path) -> dict[str, str | None]:
    return {suffix: _digest(db.with_name(db.name + suffix)) for suffix in ("", "-wal", "-shm")}


def _wal_writer(path: Path) -> sqlite3.Connection:
    """A connection that keeps every new row in the write-ahead log, like a running app."""
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, isolation_level=None)
    connection.execute("PRAGMA journal_mode=WAL")
    connection.execute("PRAGMA wal_autocheckpoint=0")
    return connection


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    path = tmp_path / "work"
    path.mkdir()
    return path


# --------------------------------------------------------------------------- AI agent sessions db


def _agent_store(path: Path, *, version: int = 31) -> sqlite3.Connection:
    connection = _wal_writer(path)
    connection.executescript(
        """
        CREATE TABLE schema_version (version INTEGER NOT NULL);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, parent_session_id TEXT,
            started_at REAL, ended_at REAL, title TEXT, hidden INTEGER DEFAULT 0);
        CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, session_id TEXT, role TEXT,
            content TEXT, tool_calls TEXT, timestamp REAL, active INTEGER DEFAULT 1,
            compacted INTEGER DEFAULT 0, message_uid TEXT);
        """
    )
    connection.execute("INSERT INTO schema_version VALUES (?)", (version,))
    return connection


def _session(connection, sid, started, *, parent=None, title=None, source="cli", hidden=0, ended=None):
    connection.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)", (sid, source, parent, started, ended, title, hidden)
    )


def _message(connection, sid, role, content, ts, *, uid, active=1, compacted=0, tool_calls=None):
    connection.execute(
        "INSERT INTO messages (session_id, role, content, tool_calls, timestamp, active, compacted, message_uid)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (sid, role, content, tool_calls, ts, active, compacted, uid),
    )


def _populated_agent_home(tmp_path: Path) -> tuple[Path, Path, sqlite3.Connection]:
    home = tmp_path / "home"
    db = home / ".hermes" / "state.db"
    c = _agent_store(db)
    # One conversation split by context compression into s1 -> s2; the title was set later.
    _session(c, "s1", 1000.0, title=None, ended=1100.0)
    _session(c, "s2", 1100.0, parent="s1", title="Trip plan", ended=1300.0)
    _message(c, "s1", "system", "SYSTEM PROMPT", 1000.0, uid="m0")
    _message(c, "s1", "user", "plan a trip", 1001.0, uid="m1")
    _message(c, "s1", "assistant", "", 1002.0, uid="m2", tool_calls='[{"name": "search"}]')
    _message(c, "s1", "tool", "TOOL OUTPUT", 1003.0, uid="m3")
    _message(c, "s1", "assistant", "old draft", 1004.0, uid="m4", active=0)  # archived by compaction
    _message(c, "s1", "assistant", "where to?", 1005.0, uid="m5", active=0, compacted=1)
    # The compacted session re-inserts the kept context with the same message ids.
    _message(c, "s2", "user", "plan a trip", 1001.0, uid="m1")
    _message(c, "s2", "user", "to the coast", 1101.0, uid="m6")
    _message(c, "s2", "assistant", "here is a plan", 1102.0, uid="m7")
    # A helper the agent ran by itself, pointing at the user's chat: never a chat, never merged.
    _session(c, "sub", 1150.0, parent="s2", source="subagent", title="helper")
    _message(c, "sub", "user", "HELPER TASK", 1151.0, uid="h1")
    _session(c, "cron1", 1200.0, source="cron", title="nightly")
    _message(c, "cron1", "user", "CRON TASK", 1201.0, uid="c1")
    # A second, separate chat in a named profile database.
    p = _agent_store(home / ".hermes" / "profiles" / "work" / "state.db")
    _session(p, "w1", 2000.0, title="Work chat")
    _message(p, "w1", "user", "status?", 2001.0, uid="w1m1")
    _message(p, "w1", "assistant", "all good", 2002.0, uid="w1m2")
    p.close()
    return home, db, c


def test_agent_sessions_become_one_chat_per_conversation(tmp_path, work_dir):
    home, db, writer = _populated_agent_home(tmp_path)
    try:
        assert db.with_name("state.db-wal").stat().st_size > 0  # the rows live in the log
        before = _fingerprint(db)
        result = local_sources.read("local_agent", home=home, env={}, platform="linux", work_dir=str(work_dir))
        assert _fingerprint(db) == before
    finally:
        writer.close()
    assert result.source_format == agent_db.FORMAT
    assert result.problems == ("chats_internal_skipped",)
    by_id = {chat.source_chat_id: chat for chat in result.chats}
    assert set(by_id) == {"default/s1", "work/w1"}
    trip = by_id["default/s1"]
    assert trip.title == "Trip plan"
    assert [(m.role, m.text) for m in trip.messages] == [
        ("user", "plan a trip"),
        ("assistant", "where to?"),
        ("user", "to the coast"),
        ("assistant", "here is a plan"),
    ]
    assert trip.created_at == 1000.0 and trip.updated_at == 1300.0
    assert trip.skipped == {"system": 1, "tool": 2}
    assert [m.text for m in by_id["work/w1"].messages] == ["status?", "all good"]
    assert list(work_dir.iterdir()) == []  # the private copy is gone


def test_agent_store_newer_than_verified_is_skipped_not_guessed(tmp_path, work_dir):
    home = tmp_path / "home"
    c = _agent_store(home / ".hermes" / "state.db", version=agent_db.VERIFIED_SCHEMA_VERSION + 1)
    _session(c, "s1", 1.0, title="x")
    _message(c, "s1", "user", "hi", 1.0, uid="u")
    c.close()
    result = local_sources.read("local_agent", home=home, env={}, platform="darwin", work_dir=str(work_dir))
    assert result.chats == () and result.source_format == ""
    assert result.problems == ("store_version_unknown",)


def test_agent_store_without_the_expected_columns_is_skipped(tmp_path, work_dir):
    home = tmp_path / "home"
    c = _wal_writer(home / ".hermes" / "state.db")
    c.executescript(
        "CREATE TABLE schema_version (version INTEGER); INSERT INTO schema_version VALUES (5);"
        "CREATE TABLE sessions (id TEXT, started_at REAL);"
        "CREATE TABLE messages (session_id TEXT, role TEXT, content TEXT, timestamp REAL);"
    )
    c.close()
    result = local_sources.read("local_agent", home=home, env={}, platform="linux", work_dir=str(work_dir))
    assert result.problems == ("store_version_unknown",) and result.chats == ()


def test_agent_home_from_the_environment_is_honoured(tmp_path, work_dir):
    home = tmp_path / "home"
    custom = tmp_path / "elsewhere"
    c = _agent_store(custom / "state.db")
    _session(c, "s1", 1.0, title="Moved home")
    _message(c, "s1", "user", "hello", 1.0, uid="u")
    c.close()
    assert local_sources.detect(home=home, env={}, platform="linux") == []
    env = {"HERMES_HOME": str(custom)}
    assert [s["id"] for s in local_sources.detect(home=home, env=env, platform="linux")] == ["local_agent"]
    result = local_sources.read("local_agent", home=home, env=env, platform="linux", work_dir=str(work_dir))
    assert [chat.title for chat in result.chats] == ["Moved home"]


# --------------------------------------------------------------------------- assistant transcripts


def _event_rows(session_id: str) -> list[dict]:
    return [
        {"type": "session", "version": 4, "id": session_id, "timestamp": "2026-09-01T10:00:00.000Z"},
        {"type": "message", "id": "e1", "parentId": None, "timestamp": "2026-09-01T10:00:01.000Z",
         "message": {"role": "user", "content": "first try", "timestamp": 1788256801000}},
        {"type": "message", "id": "e2", "parentId": "e1",
         "message": {"role": "assistant", "content": [{"type": "text", "text": "ABANDONED ANSWER"}], "timestamp": 1788256802000}},
        # The user went back and asked again from the same point: e3 replaces e2's branch.
        {"type": "message", "id": "e3", "parentId": "e1",
         "message": {"role": "assistant", "content": [
             {"type": "thinking", "thinking": "PRIVATE REASONING"},
             {"type": "toolCall", "name": "search", "arguments": {"q": "TOOL ARGS"}},
             {"type": "text", "text": "the answer"},
         ], "timestamp": 1788256803000}},
        {"type": "message", "id": "e4", "parentId": "e3",
         "message": {"role": "toolResult", "content": [{"type": "text", "text": "TOOL RESULT"}], "timestamp": 1788256804000}},
        {"type": "custom_message", "id": "e5", "parentId": "e4", "content": "INJECTED"},
        {"type": "message", "id": "e6", "parentId": "e5",
         "message": {"role": "user", "content": "<<<BEGIN_OPENCLAW_INTERNAL_CONTEXT>>> RUNTIME", "timestamp": 1788256805000}},
        {"type": "session_info", "id": "e7", "parentId": "e6", "name": "Garden ideas"},
        {"type": "message", "id": "e8", "parentId": "e7",
         "message": {"role": "user", "content": [{"type": "text", "text": "thanks"}], "timestamp": 1788256806000}},
    ]


def _assistant_db(path: Path) -> sqlite3.Connection:
    connection = _wal_writer(path)
    connection.execute(
        "CREATE TABLE transcript_events (session_id TEXT NOT NULL, seq INTEGER NOT NULL, event_json TEXT,"
        " event_zstd BLOB, created_at INTEGER, PRIMARY KEY (session_id, seq))"
    )
    return connection


def _insert_events(connection, session_id: str, events: list[dict], *, compress_from: int | None = None) -> None:
    for seq, event in enumerate(events):
        payload = json.dumps(event)
        if compress_from is not None and seq >= compress_from:
            blob = zstandard.ZstdCompressor().compress(payload.encode())
            connection.execute("INSERT INTO transcript_events VALUES (?, ?, NULL, ?, 0)", (session_id, seq, blob))
        else:
            connection.execute("INSERT INTO transcript_events VALUES (?, ?, ?, NULL, 0)", (session_id, seq, payload))


def test_assistant_database_shows_the_branch_the_user_saw(tmp_path, work_dir):
    home = tmp_path / "home"
    db = home / ".openclaw" / "agents" / "main" / "agent" / "openclaw-agent.sqlite"
    writer = _assistant_db(db)
    _insert_events(writer, "sess-1", _event_rows("sess-1"), compress_from=3)  # newer rows compressed
    try:
        before = _fingerprint(db)
        result = local_sources.read("local_assistant", home=home, env={}, platform="linux", work_dir=str(work_dir))
        assert _fingerprint(db) == before
    finally:
        writer.close()
    assert result.source_format == transcript_tree.FORMAT_DB
    assert result.problems == ()
    (chat,) = result.chats
    assert chat.source_chat_id == "main/sess-1"
    assert chat.title == "Garden ideas"
    assert [(m.role, m.text) for m in chat.messages] == [
        ("user", "first try"),
        ("assistant", "the answer"),
        ("user", "thanks"),
    ]
    text = json.dumps([m.text for m in chat.messages])
    for hidden in ("ABANDONED", "PRIVATE REASONING", "TOOL ARGS", "TOOL RESULT", "INJECTED", "RUNTIME"):
        assert hidden not in text
    assert chat.skipped == {"hidden_branch": 1, "tool": 1, "context": 2}
    assert chat.messages[0].created_at == 1788256801.0
    assert list(work_dir.iterdir()) == []


def test_compressed_rows_without_a_decoder_are_left_out_and_reported(tmp_path, work_dir):
    db = tmp_path / "agents" / "main" / "agent" / "openclaw-agent.sqlite"
    writer = _assistant_db(db)
    _insert_events(writer, "sess-1", _event_rows("sess-1"), compress_from=3)
    _insert_events(writer, "plain", _event_rows("plain"))
    writer.close()
    result = transcript_tree.read_stores([db], [], LIMITS, work_dir=str(work_dir), decoder=None)
    assert result.problems == ("rows_compressed_skipped",)
    # The session with rows nobody can open is left out whole: walking only the readable rows
    # would have shown the thrown-away answer as the current one. The readable session stays.
    assert [chat.source_chat_id for chat in result.chats] == ["main/plain"]
    assert "ABANDONED" not in repr(result)


def test_legacy_transcript_files_and_reset(tmp_path, work_dir):
    home = tmp_path / "home"
    sessions = home / ".clawdbot" / "agents" / "helper" / "sessions"
    sessions.mkdir(parents=True)
    events = [
        {"type": "session", "version": 3, "id": "old", "timestamp": "2026-01-01T00:00:00Z"},
        {"type": "message", "id": "a", "parentId": None, "timestamp": "2026-01-01T00:00:01Z",
         "message": {"role": "user", "content": "BEFORE RESET"}},
        {"type": "message", "id": "b", "parentId": "a", "timestamp": "2026-01-01T00:00:02Z",
         "message": {"role": "assistant", "content": "kept answer"}},
        {"type": "reset", "id": "r", "parentId": "b", "firstKeptEntryId": "b"},
        {"type": "message", "id": "c", "parentId": "r", "timestamp": "2026-01-01T00:00:03Z",
         "message": {"role": "user", "content": "after reset"}},
    ]
    # The app was cut off while appending: a last line without its line end is let go.
    (sessions / "old.jsonl").write_text("\n".join(json.dumps(e) for e in events) + '\n{"type": "mess', encoding="utf-8")
    # A broken line inside a transcript leaves that transcript out whole.
    (sessions / "broken.jsonl").write_text(
        json.dumps(events[0] | {"id": "broken"}) + "\nnot json\n" + json.dumps(events[1]) + "\n", encoding="utf-8"
    )
    (sessions / "gone.jsonl.deleted.1767225600").write_text(json.dumps(events[0]) + "\n", encoding="utf-8")
    newer = dict(events[0], version=9, id="future")
    (sessions / "future.jsonl").write_text(json.dumps(newer) + "\n", encoding="utf-8")
    result = local_sources.read("local_assistant", home=home, env={}, platform="darwin", work_dir=str(work_dir))
    assert result.source_format == transcript_tree.FORMAT_JSONL
    (chat,) = result.chats
    assert chat.source_chat_id == "helper/old"
    assert [(m.role, m.text) for m in chat.messages] == [("assistant", "kept answer"), ("user", "after reset")]
    assert chat.messages[1].created_at == 1767225603.0
    assert set(result.problems) == {"rows_unreadable_skipped", "store_version_unknown"}


def test_a_session_in_both_the_database_and_an_old_file_is_read_once(tmp_path, work_dir):
    agent = tmp_path / "home" / ".openclaw" / "agents" / "main"
    writer = _assistant_db(agent / "agent" / "openclaw-agent.sqlite")
    _insert_events(writer, "same", _event_rows("same"))
    writer.close()
    (agent / "sessions").mkdir(parents=True)
    (agent / "sessions" / "same.jsonl").write_text(
        "\n".join(json.dumps(e) for e in _event_rows("same")), encoding="utf-8"
    )
    result = local_sources.read("local_assistant", home=tmp_path / "home", env={}, platform="linux", work_dir=str(work_dir))
    assert [chat.source_chat_id for chat in result.chats] == ["main/same"]
    assert result.chats[0].source_format == transcript_tree.FORMAT_DB


# --------------------------------------------------------------------------- code editor store


def _editor_db(path: Path) -> sqlite3.Connection:
    connection = _wal_writer(path)
    connection.execute("CREATE TABLE cursorDiskKV (key TEXT UNIQUE ON CONFLICT REPLACE, value BLOB)")
    return connection


def _kv(connection, key: str, value: dict) -> None:
    connection.execute("INSERT INTO cursorDiskKV VALUES (?, ?)", (key, json.dumps(value)))


def test_editor_chats_follow_the_header_list_only(tmp_path, work_dir):
    home = tmp_path / "home"
    db = home / ".config" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    c = _editor_db(db)
    _kv(c, "composerData:abc", {
        "name": "Fix the parser", "createdAt": 1788000000000, "lastUpdatedAt": 1788000600000,
        "fullConversationHeadersOnly": [
            {"bubbleId": "b1", "type": 1}, {"bubbleId": "b2", "type": 2},
            {"bubbleId": "b3", "type": 2}, {"bubbleId": "missing", "type": 2},
        ],
    })
    _kv(c, "bubbleId:abc:b1", {"type": 1, "text": "why does it crash?", "createdAt": "2026-08-30T08:00:01.000Z"})
    _kv(c, "bubbleId:abc:b2", {"type": 2, "text": "", "toolFormerData": {"name": "read_file", "result": "TOOL FILE"}})
    _kv(c, "bubbleId:abc:b3", {"type": 2, "text": "a missing check", "timingInfo": {"clientRpcSendTime": 1788000005000}})
    _kv(c, "bubbleId:abc:old", {"type": 2, "text": "REWOUND ANSWER"})  # no header: an abandoned edit
    _kv(c, "composerData:draft", {"name": "", "fullConversationHeadersOnly": []})
    _kv(c, "composerData:legacy", {"name": "No order"})
    _kv(c, "bubbleId:legacy:x", {"type": 1, "text": "ORDERLESS"})
    c.execute("CREATE TABLE ItemTable (key TEXT, value BLOB)")
    try:
        before = _fingerprint(db)
        result = local_sources.read("local_code_editor", home=home, env={}, platform="linux", work_dir=str(work_dir))
        assert _fingerprint(db) == before
    finally:
        c.close()
    assert result.source_format == editor_kv.FORMAT
    assert result.problems == ("chats_without_order_skipped",)
    (chat,) = result.chats
    assert chat.title == "Fix the parser" and chat.source_chat_id == "abc"
    assert [(m.role, m.text) for m in chat.messages] == [("user", "why does it crash?"), ("assistant", "a missing check")]
    assert chat.messages[1].created_at == 1788000005.0
    assert chat.created_at == 1788000000.0 and chat.updated_at == 1788000600.0
    assert chat.skipped == {"tool": 1, "missing": 1}
    assert "REWOUND" not in repr(result) and "ORDERLESS" not in repr(result) and "TOOL FILE" not in repr(result)


def test_editor_store_locations_per_platform(tmp_path):
    home = tmp_path / "home"
    mac = home / "Library" / "Application Support" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    linux_xdg = tmp_path / "xdg" / "Cursor" / "User" / "globalStorage" / "state.vscdb"
    for path in (mac, linux_xdg):
        path.parent.mkdir(parents=True)
        path.write_bytes(b"")
    assert editor_kv.store_paths(home, {}, "darwin") == [mac]
    assert editor_kv.store_paths(home, {}, "linux") == []
    assert editor_kv.store_paths(home, {"XDG_CONFIG_HOME": str(tmp_path / "xdg")}, "linux") == [linux_xdg]


# --------------------------------------------------------------------------- detection and safety


def test_detect_offers_only_installed_apps_without_opening_any_file(tmp_path, monkeypatch):
    home = tmp_path / "home"
    assert local_sources.detect(home=home, env={}, platform="linux") == []
    (home / ".hermes").mkdir(parents=True)
    (home / ".hermes" / "state.db").write_bytes(b"not even a database")
    os.utime(home / ".hermes" / "state.db", (1788000000, 1788000000))

    def refuse(*args, **kwargs):
        raise AssertionError("detect must not open a store")

    monkeypatch.setattr(sqlite3, "connect", refuse)
    monkeypatch.setattr(builtins, "open", refuse)
    found = local_sources.detect(home=home, env={}, platform="linux")
    assert found == [{"id": "local_agent", "kind": "ai_agent", "last_changed": "2026-08-29T10:40:00+00:00"}]
    assert local_sources.detect(home=home, env={}, platform="win32") == []


def test_unknown_or_missing_sources_are_refusals(tmp_path):
    for source_id in ("local_agent", "nope", ""):
        result = local_sources.read(source_id, home=tmp_path, env={}, platform="linux")
        assert result.chats == () and result.problems == ("store_missing",) and result.source_format == ""


def test_snapshot_refusals_and_cleanup(tmp_path, work_dir):
    real = tmp_path / "real.db"
    sqlite3.connect(real).execute("CREATE TABLE t (x)").connection.close()
    link = tmp_path / "link.db"
    link.symlink_to(real)
    junk = tmp_path / "junk.db"
    junk.write_bytes(b"this is not sqlite" * 100)
    for path, limit, code in ((link, 1 << 20, "store_missing"), (real, 10, "store_too_large"), (junk, 1 << 20, "store_unreadable")):
        with pytest.raises(SnapshotError) as caught, snapshot_connection(path, max_bytes=limit, work_dir=work_dir):
            pass
        assert caught.value.code == code
    with snapshot_connection(real, max_bytes=1 << 20, work_dir=work_dir) as connection:
        with pytest.raises(sqlite3.OperationalError):
            connection.execute("INSERT INTO t VALUES (1)")
    assert list(work_dir.iterdir()) == []


def test_a_store_that_is_not_a_database_reads_as_a_plain_problem(tmp_path, work_dir):
    home = tmp_path / "home"
    (home / ".hermes").mkdir(parents=True)
    (home / ".hermes" / "state.db").write_bytes(b"garbage" * 64)
    result = local_sources.read("local_agent", home=home, env={}, platform="linux", work_dir=str(work_dir))
    assert result.chats == () and result.problems == ("store_unreadable",)


def test_a_third_party_state_setting_does_not_move_where_transcripts_are_read(tmp_path, work_dir):
    elsewhere = tmp_path / "state" / "agents" / "main"
    writer = _assistant_db(elsewhere / "agent" / "openclaw-agent.sqlite")
    _insert_events(writer, "elsewhere", _event_rows("elsewhere"))
    writer.close()
    env = {"OPENCLAW_STATE_DIR": str(tmp_path / "state")}
    result = local_sources.read("local_assistant", home=tmp_path / "home", env=env, platform="linux", work_dir=str(work_dir))
    assert list(result.chats) == []
