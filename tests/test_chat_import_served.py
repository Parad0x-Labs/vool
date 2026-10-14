"""Moving in: chats from other AI apps brought into VOOL through the served doors.

Every step goes through the real ASGI app (``apps.vool_api_server.create_app``) the browser
talks to: pick a file or a local app, preview, import the ticked chats, then read them back
through the sidebar list and the chat history route. Pinned: the chats arrive with their names,
times and words; nothing reaches the conversation log or any memory store; a second import is a
no-op; the other app's files are never changed; the doors refuse anything not from the owner's
own browser; nothing goes online.
"""
from __future__ import annotations

import hashlib
import io
import json
import socket
import sqlite3
import time
import zipfile
from pathlib import Path

import pytest

from core import runtime_paths
from core.chat_import import flow
from core.chat_import import store as import_store
from tests.asgi_harness import asgi_request

LOCAL = {"Host": "127.0.0.1"}
JSON = {**LOCAL, "Content-Type": "application/json"}
SECRET = "sk-ant-api03-" + "A" * 95


def _drop_import_tables() -> None:
    from storage.db import get_connection

    connection = get_connection()
    try:
        for table in (import_store.MESSAGES_TABLE, import_store.CHATS_TABLE):
            connection.execute(f"DROP TABLE IF EXISTS {table}")
        connection.commit()
    finally:
        connection.close()


@pytest.fixture
def app(tmp_path, monkeypatch):
    home = tmp_path / "vool-home"
    home.mkdir()
    prior = runtime_paths._VOOL_HOME_OVERRIDE
    monkeypatch.setenv("VOOL_HOME", str(home))
    runtime_paths.configure_runtime_home(home)
    # Other apps live in this fake home folder; nothing of the machine's own home is looked at.
    user_home = tmp_path / "user-home"
    user_home.mkdir()
    monkeypatch.setenv("HOME", str(user_home))
    for name in ("HERMES_HOME", "OPENCLAW_STATE_DIR", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(name, raising=False)
    _drop_import_tables()
    flow.reset_for_tests()

    # Nothing in a move-in may go online: any outbound connection fails the test.
    real_connect = socket.socket.connect

    def no_network(sock, address, *args, **kwargs):
        if sock.family in (socket.AF_INET, socket.AF_INET6):
            raise AssertionError(f"chat import tried to connect to {address!r}")
        return real_connect(sock, address, *args, **kwargs)

    monkeypatch.setattr(socket.socket, "connect", no_network)
    from apps.vool_api_server import create_app
    from core.web.api.runtime import RuntimeServices

    try:
        yield create_app(RuntimeServices(display_name="VOOL"))
    finally:
        flow.reset_for_tests()
        _drop_import_tables()
        runtime_paths.configure_runtime_home(prior)


def _call(app, method: str, path: str, *, body: dict | None = None, raw: bytes | None = None, headers=None, query: str = ""):
    sent_headers = dict(headers if headers is not None else (JSON if body is not None else LOCAL))
    payload = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
    status, _headers, content = asgi_request(
        app, method=method, path=path, headers=sent_headers, body=payload, query_string=query.encode()
    )
    try:
        return status, json.loads(content or b"{}")
    except ValueError:
        return status, {"raw": content.decode("utf-8", "replace")}


def _export_zip(conversations: list) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("conversations.json", json.dumps(conversations))
        archive.writestr("users.json", "[]")
    return buffer.getvalue()


def _conversation(uuid: str, name: str, messages: list[tuple[str, str, str]]) -> dict:
    return {
        "uuid": uuid,
        "name": name,
        "created_at": "2025-03-01T09:00:00.000000Z",
        "updated_at": "2025-03-01T09:30:00.000000Z",
        "chat_messages": [
            {"uuid": f"{uuid}-{i}", "sender": sender, "text": "", "created_at": at,
             "content": [{"type": "text", "text": words}]}
            for i, (sender, words, at) in enumerate(messages)
        ],
    }


EXPORT = [
    _conversation("bread", "Baking bread", [
        ("human", f"how do I bake bread? my key is {SECRET}", "2025-03-01T09:00:00Z"),
        ("assistant", "Knead, rest, bake.", "2025-03-01T09:00:05Z"),
    ]),
    _conversation("untitled", "", [
        ("human", "what should I name my cat?\nshe is grey", "2025-03-02T10:00:00Z"),
        ("assistant", "How about Ash?", "2025-03-02T10:00:04Z"),
    ]),
]


def _upload_and_preview(app, data: bytes) -> dict:
    status, uploaded = _call(app, "POST", "/api/chat/import/upload", raw=data,
                             headers={**LOCAL, "Content-Type": "application/zip"})
    assert status == 201, uploaded
    assert set(uploaded) == {"ok", "upload_id", "size_bytes"}  # an opaque id, never a path
    status, preview = _call(app, "POST", "/api/chat/import/preview", body={"upload_id": uploaded["upload_id"]})
    assert status == 200, preview
    return preview


def _run(app, preview: dict, refs: list[str]) -> dict:
    status, started = _call(app, "POST", "/api/chat/import/start", body={"preview_id": preview["preview_id"], "chats": refs})
    assert status == 202, started
    deadline = time.time() + 60
    while time.time() < deadline:
        status, state = _call(app, "GET", "/api/chat/import/status")
        assert status == 200
        if state["state"] != "running":
            return state
        time.sleep(0.05)
    raise AssertionError("the import did not finish")


def _sessions(app) -> dict[str, dict]:
    status, payload = _call(app, "GET", "/api/chat/sessions")
    assert status == 200
    return {row["session_id"]: row for row in payload["sessions"]}


def _history(app, session_id: str) -> list[dict]:
    status, payload = _call(app, "GET", "/api/chat/history", query=f"session={session_id}")
    assert status == 200, payload
    return payload["messages"]


def _memory_bytes() -> dict[str, bytes]:
    from core.memory.files import (
        conversation_log_path,
        memory_entries_path,
        memory_path,
        session_summaries_path,
        user_heuristics_path,
    )

    paths = {
        "conversation_log": conversation_log_path(),
        "memory_entries": memory_entries_path(),
        "memory": memory_path(),
        "session_summaries": session_summaries_path(),
        "user_heuristics": user_heuristics_path(),
    }
    return {name: path.read_bytes() if path.exists() else b"" for name, path in paths.items()}


def test_moving_in_from_an_export_file(app):
    from core.persistent_memory import append_conversation_event

    resident = "openclaw:" + "ab" * 10
    append_conversation_event(session_id=resident, user_input="my own chat", assistant_output="kept",
                              source_context={"surface": "api", "platform": "api"})
    before_memory = _memory_bytes()
    before_sessions = _sessions(app)
    assert resident in before_sessions

    preview = _upload_and_preview(app, _export_zip(EXPORT))
    assert preview["readable"] is True and preview["total"] == 2 and preview["already_here"] == 0
    assert [(c["title"], c["message_count"], c["already_here"]) for c in preview["chats"]] == [
        ("Baking bread", 2, False),
        ("what should I name my cat? she is grey", 2, False),
    ]
    assert SECRET not in json.dumps(preview)  # even the preview list is masked

    state = _run(app, preview, ["0", "1"])
    assert state["state"] == "done"
    assert state["outcome"] == {"brought_in": 2, "already_here": 0, "empty": 0, "held_back": 0,
                                "failed": 0, "secrets_masked": 1, "messages": 4}
    # The picked file is gone once the import finished.
    assert list(runtime_paths.data_path("chat_import", "incoming").iterdir()) == []

    sessions = _sessions(app)
    new_ids = set(sessions) - set(before_sessions)
    assert len(new_ids) == 2 and resident in sessions
    by_title = {sessions[sid]["title"]: sessions[sid] for sid in new_ids}
    bread = by_title["Baking bread"]
    assert bread["session_id"].startswith("openclaw:") and len(bread["session_id"]) == len(resident)
    assert bread["created_at"].startswith("2025-03-01T09:00:00")
    assert bread["updated_at"].startswith("2025-03-01T09:30:00")
    assert bread["imported"] is True and bread["turn_count"] == 1

    messages = _history(app, bread["session_id"])
    assert [(m["role"], m["imported"]) for m in messages] == [("user", True), ("assistant", True)]
    assert messages[0]["content"].startswith("how do I bake bread? my key is ")
    assert SECRET not in messages[0]["content"]
    assert messages[1]["content"] == "Knead, rest, bake."
    assert messages[1]["ts"].startswith("2025-03-01T09:00:05")
    assert _history(app, resident)[0]["content"] == "my own chat"  # the resident chat is untouched

    # History, not memory: no memory store, log or summary got a single byte.
    assert _memory_bytes() == before_memory
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import list_memory_entries, resolve_memory_access_policy, search_relevant_memory
    from core.persistent_memory import recent_conversation_events

    assert recent_conversation_events(bread["session_id"], limit=50) == []
    ensure_chat_namespace(bread["session_id"], grant_confirmed_profile=True)
    policy = resolve_memory_access_policy(chat_id=bread["session_id"])
    assert list_memory_entries(access_policy=policy, limit=100) == []
    assert search_relevant_memory("how do I bake bread", access_policy=policy, limit=10) == []


def test_bringing_the_same_chats_in_again_changes_nothing(app):
    first = _run(app, _upload_and_preview(app, _export_zip(EXPORT)), ["0", "1"])
    assert first["outcome"]["brought_in"] == 2
    sessions = _sessions(app)

    again = _upload_and_preview(app, _export_zip(EXPORT))
    assert again["already_here"] == 2 and all(chat["already_here"] for chat in again["chats"])
    second = _run(app, again, ["0", "1"])
    assert second["outcome"]["brought_in"] == 0 and second["outcome"]["already_here"] == 2
    assert _sessions(app).keys() == sessions.keys()


def test_a_deleted_imported_chat_goes_whole_and_can_be_brought_in_again(app):
    _run(app, _upload_and_preview(app, _export_zip(EXPORT[:1])), ["0"])
    (session_id,) = [sid for sid, row in _sessions(app).items() if row.get("imported")]
    status, _ = _call(app, "POST", "/api/chat/session", body={"session_id": session_id, "delete": True})
    assert status == 200
    assert session_id not in _sessions(app)
    assert import_store.imported_history_messages(session_id) == []

    preview = _upload_and_preview(app, _export_zip(EXPORT[:1]))
    assert preview["already_here"] == 0  # the user removed it; bringing it in again is allowed
    state = _run(app, preview, ["0"])
    assert state["outcome"]["brought_in"] == 1
    assert state["first_session_id"] != session_id


def test_a_run_cut_off_before_titles_is_finished_on_the_next_look(app, monkeypatch):
    from core.memory import entries

    real = entries._set_session_titles
    calls = {"n": 0}

    def crash_once(titles):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk went away")
        return real(titles)

    monkeypatch.setattr(entries, "_set_session_titles", crash_once)
    state = _run(app, _upload_and_preview(app, _export_zip(EXPORT[:1])), ["0"])
    assert state["state"] == "failed" and state["error"] == "store_failed"
    # The chat's messages committed; it is waiting to be finished, not lost and not doubled.
    preview = _upload_and_preview(app, _export_zip(EXPORT[:1]))
    assert preview["already_here"] == 1
    imported = [row for row in _sessions(app).values() if row.get("imported")]
    assert [row["title"] for row in imported] == ["Baking bread"]


def test_reading_a_local_app_never_changes_its_files(app, tmp_path):
    db = tmp_path / "user-home" / ".hermes" / "state.db"
    db.parent.mkdir(parents=True)
    writer = sqlite3.connect(db, isolation_level=None)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("PRAGMA wal_autocheckpoint=0")
    writer.executescript(
        """
        CREATE TABLE schema_version (version INTEGER);
        INSERT INTO schema_version VALUES (31);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, source TEXT, parent_session_id TEXT,
            started_at REAL, ended_at REAL, title TEXT, hidden INTEGER DEFAULT 0);
        CREATE TABLE messages (id INTEGER PRIMARY KEY, session_id TEXT, role TEXT, content TEXT,
            tool_calls TEXT, timestamp REAL, active INTEGER DEFAULT 1, compacted INTEGER DEFAULT 0,
            message_uid TEXT);
        INSERT INTO sessions VALUES ('s1', 'cli', NULL, 1735725600.0, 1735725700.0, 'Garden plan', 0);
        INSERT INTO messages (session_id, role, content, timestamp, message_uid)
            VALUES ('s1', 'user', 'which tomatoes grow fast?', 1735725601.0, 'u1'),
                   ('s1', 'assistant', 'Cherry tomatoes.', 1735725602.0, 'u2');
        """
    )

    def fingerprint() -> dict:
        return {
            suffix: hashlib.sha256(Path(str(db) + suffix).read_bytes()).hexdigest()
            for suffix in ("", "-wal", "-shm") if Path(str(db) + suffix).exists()
        }

    try:
        before = fingerprint()
        assert set(before) == {"", "-wal", "-shm"}
        status, sources = _call(app, "GET", "/api/chat/import/sources")
        assert status == 200 and sources["file"] is True
        assert [s["id"] for s in sources["local"]] == ["local_agent"]
        assert sources["local"][0]["kind"] == "ai_agent"
        status, preview = _call(app, "POST", "/api/chat/import/preview", body={"source": "local_agent"})
        assert status == 200, preview
        assert [c["title"] for c in preview["chats"]] == ["Garden plan"]
        state = _run(app, preview, ["0"])
        assert state["outcome"]["brought_in"] == 1
        assert fingerprint() == before
    finally:
        writer.close()
    assert list(runtime_paths.data_path("chat_import", "work").iterdir()) == []
    messages = _history(app, state["first_session_id"])
    assert [m["content"] for m in messages] == ["which tomatoes grow fast?", "Cherry tomatoes."]


def test_no_app_installed_means_only_the_file_choice(app):
    status, sources = _call(app, "GET", "/api/chat/import/sources")
    assert status == 200 and sources == {"ok": True, "local": [], "file": True}
    status, preview = _call(app, "POST", "/api/chat/import/preview", body={"source": "local_agent"})
    assert status == 200 and preview["chats"] == [] and preview["problems"] == ["store_missing"]
    status, refused = _call(app, "POST", "/api/chat/import/preview", body={"source": "/etc/passwd"})
    assert status == 400 and refused["error"] == "unknown_source"


def test_the_doors_answer_only_the_owners_own_browser(app):
    from core.web.api import service

    runtime = type("R", (), {"runtime_version_stamp": {}})()
    remote = "10.0.0.5"
    response = service.dispatch_get(path="/api/chat/import/sources", query={}, runtime=runtime,
                                    model_name="vool", client_host=remote, headers=LOCAL)
    assert response.status == 403
    response = service.dispatch_upload(path="/api/chat/import/upload", raw_body=b"PK", headers=LOCAL,
                                       runtime=runtime, client_host=remote)
    assert response.status == 403
    for path in ("/api/chat/import/preview", "/api/chat/import/start", "/api/chat/import/discard"):
        status, _ = _call(app, "POST", path, body={}, headers={**JSON, "Sec-Fetch-Site": "cross-site"})
        assert status == 403, path
        status, _ = _call(app, "POST", path, body={}, headers={**JSON, "Origin": "https://elsewhere.example"})
        assert status == 403, path
        status, _ = _call(app, "POST", path, body={}, headers={**LOCAL, "Content-Type": "text/plain"})
        assert status == 415, path
    status, _ = _call(app, "GET", "/api/chat/import/status", headers={**LOCAL, "Sec-Fetch-Site": "cross-site"})
    assert status == 403
    status, _ = _call(app, "POST", "/api/chat/import/upload", raw=b"PK\x03\x04",
                      headers={**LOCAL, "Origin": "https://elsewhere.example"})
    assert status == 403
    status, body = _call(app, "POST", "/api/chat/import/preview", body={"source": "local_agent", "path": "/"})
    assert status == 400 and body["error"] == "unknown_fields"


def test_the_upload_and_start_doors_refuse_what_they_cannot_take(app, monkeypatch):
    status, body = _call(app, "POST", "/api/chat/import/upload", raw=b"", headers=LOCAL)
    assert status == 400 and body["error"] == "empty_file"
    monkeypatch.setattr(flow, "MAX_UPLOAD_BYTES", 1000)
    status, body = _call(app, "POST", "/api/chat/import/upload", raw=b"x" * 2000, headers=LOCAL)
    assert status == 413 and body["error"] == "file_too_large"
    status, body = _call(app, "POST", "/api/chat/import/upload", raw=b"x" * (200 * 1024), headers=LOCAL)
    assert status == 413  # over the door's ceiling: refused before the body is held
    monkeypatch.setattr(flow, "MAX_UPLOAD_BYTES", 512 << 20)

    preview = _upload_and_preview(app, b"this is not a zip file")
    assert preview["readable"] is False and preview["chats"] == [] and preview["problems"] == ["not_a_zip"]
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    for refs, code in ((["7"], "bad_selection"), (["0", "0"], "bad_selection"), ([], "nothing_selected"), ([0], "bad_selection")):
        status, body = _call(app, "POST", "/api/chat/import/start", body={"preview_id": preview["preview_id"], "chats": refs})
        assert status == 400 and body["error"] == code, refs
    status, body = _call(app, "POST", "/api/chat/import/start", body={"preview_id": "f" * 32, "chats": ["0"]})
    assert status == 404 and body["error"] == "preview_missing"
    status, body = _call(app, "POST", "/api/chat/import/discard", body={})
    assert status == 200
    status, body = _call(app, "POST", "/api/chat/import/start", body={"preview_id": preview["preview_id"], "chats": ["0"]})
    assert status == 404
    assert list(runtime_paths.data_path("chat_import", "incoming").iterdir()) == []


def test_forgotten_words_stay_forgotten(app):
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command

    _run(app, _upload_and_preview(app, _export_zip(EXPORT)), ["0", "1"])
    cat_chat = next(sid for sid, row in _sessions(app).items() if row.get("title", "").startswith("what should I name"))
    ensure_chat_namespace(cat_chat, grant_confirmed_profile=True)
    handled, reply = maybe_handle_memory_command(
        "Forget How about Ash.", session_id=cat_chat, access_policy=resolve_memory_access_policy(chat_id=cat_chat)
    )
    assert handled, reply
    # The forget in that chat reached the imported words; the rest of the chat stays.
    assert [m["content"] for m in _history(app, cat_chat)] == ["what should I name my cat?\nshe is grey"]


def test_a_chat_carrying_words_forgotten_everywhere_is_held_back(app):
    from core.memory import revocation

    # A forget recorded for every chat (no chat scope) in the one revocation authority.
    revocation.complete(revocation.begin("", "flourstone bakehouse"))
    carrying = _conversation("bakery", "Bakery names", [
        ("human", "my sister's bakery is called Flourstone Bakehouse", "2025-03-03T10:00:00Z"),
        ("assistant", "Lovely name.", "2025-03-03T10:00:02Z"),
    ])
    state = _run(app, _upload_and_preview(app, _export_zip([carrying, EXPORT[0]])), ["0", "1"])
    assert state["outcome"]["held_back"] == 1 and state["outcome"]["brought_in"] == 1
    titles = [row["title"] for row in _sessions(app).values() if row.get("imported")]
    assert titles == ["Baking bread"]
    from storage.db import get_connection

    connection = get_connection()
    try:
        texts = [row[0] for row in connection.execute(f"SELECT text FROM {import_store.MESSAGES_TABLE}")]
    finally:
        connection.close()
    assert all("flourstone" not in text.lower() for text in texts)


def test_a_picked_file_left_behind_by_a_restart_is_removed(app):
    _upload_and_preview(app, _export_zip(EXPORT))
    incoming = runtime_paths.data_path("chat_import", "incoming")
    assert len(list(incoming.iterdir())) == 1
    status, _ = _call(app, "GET", "/api/chat/import/sources")
    assert status == 200 and len(list(incoming.iterdir())) == 1  # still in use by the open preview
    flow._UPLOADS.clear()  # what a server restart forgets
    _call(app, "GET", "/api/chat/import/sources")
    assert list(incoming.iterdir()) == []
