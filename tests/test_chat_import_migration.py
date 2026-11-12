"""Durable, reversible transcript migration through the served owner-only doors."""
from __future__ import annotations

import time

import pytest

from core.chat_import import flow, store
from tests.test_chat_import_served import (
    EXPORT,
    _call,
    _export_zip,
    _history,
    _run,
    _sessions,
    _upload_and_preview,
)
from tests.test_chat_import_served import app as import_app


@pytest.fixture
def app(import_app):
    # The pre-existing app fixture clears the transcript tables it owns. This module
    # owns the new durable ledgers and must isolate those as well.
    connection = store.connect()
    has_ledger = hasattr(store, "BATCH_SESSIONS_TABLE")
    try:
        if has_ledger:
            connection.execute(f"DELETE FROM {store.BATCH_SESSIONS_TABLE}")
            connection.execute(f"DELETE FROM {store.BATCHES_TABLE}")
        connection.commit()
        yield import_app
    finally:
        if has_ledger:
            connection.execute(f"DELETE FROM {store.BATCH_SESSIONS_TABLE}")
            connection.execute(f"DELETE FROM {store.BATCHES_TABLE}")
        connection.commit()
        connection.close()


def _finish(app, preview, **options):
    status, result = _call(app, "POST", "/api/chat/import/start", body={
        "preview_id": preview["preview_id"], "chats": ["0"], **options})
    assert status == 202, result
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        status, result = _call(app, "GET", "/api/chat/import/status")
        assert status == 200
        if result["state"] != "running":
            assert result["state"] == "done", result
            return result
        time.sleep(.02)
    pytest.fail("import did not finish")


def test_undo_survives_reload_and_preserves_resident_and_duplicate_chats(app):
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    first = _run(app, preview, ["0"])
    first_id = first["first_session_id"]
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    second = _run(app, preview, ["0", "1"])
    second_id = second["first_session_id"]
    assert second["outcome"]["already_here"] == 1
    flow.reset_for_tests()
    status, batches = _call(app, "GET", "/api/chat/import/batches")
    assert status == 200
    assert second["run_id"] in {row["batch_id"] for row in batches["batches"]}
    status, result = _call(app, "POST", "/api/chat/import/undo", body={"batch_id": second["run_id"]})
    assert (status, result["removed"]) == (200, 1)
    assert _history(app, first_id)
    assert second_id not in _sessions(app)
    assert store.imported_history_messages(second_id) == []
    assert store.batch_sessions(second["run_id"]) == []
    status, result = _call(app, "POST", "/api/chat/import/undo", body={"batch_id": second["run_id"]})
    assert (status, result["removed"]) == (200, 0)


def test_undo_refuses_to_delete_new_work(app):
    from core.persistent_memory import append_conversation_event

    preview = _upload_and_preview(app, _export_zip(EXPORT))
    run = _finish(app, preview)
    sid = run["first_session_id"]
    append_conversation_event(session_id=sid, user_input="New work after the move", assistant_output="Keep it")
    status, result = _call(app, "POST", "/api/chat/import/undo", body={"batch_id": run["run_id"]})
    assert status == 409 and result["error"] == "batch_in_use"
    assert _history(app, sid)


def test_continuation_is_explicit_and_uses_only_scrubbed_user_assistant_history(app):
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    run = _finish(app, preview, continuation=True)
    history = store.continuation_history(run["first_session_id"])
    assert [row["role"] for row in history] == ["user", "assistant"]
    assert history[0]["content"] == "how do I bake bread? my key is [redacted-api-key]"
    assert "sk-ant" not in str(history)
    flow.reset_for_tests()
    assert store.continuation_history(run["first_session_id"]) == history


def test_continuation_stays_off_by_default(app):
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    run = _finish(app, preview)
    assert store.continuation_history(run["first_session_id"]) == []


@pytest.mark.parametrize("value", ["yes", 1, None, [], {}])
def test_unknown_continuation_choice_refuses_without_importing(app, value):
    preview = _upload_and_preview(app, _export_zip(EXPORT))
    status, _result = _call(app, "POST", "/api/chat/import/start", body={
        "preview_id": preview["preview_id"], "chats": ["0"], "continuation": value})
    assert status == 400
    assert _sessions(app) == {}


def test_undo_owner_guard_and_unknown_batch(app):
    status, result = _call(app, "POST", "/api/chat/import/undo", body={"batch_id": "missing"})
    assert status == 404 and result["error"] == "batch_missing"
    status, _result = _call(app, "POST", "/api/chat/import/undo", body={"batch_id": "missing"},
                            headers={"Host": "127.0.0.1", "Origin": "https://foreign.example"})
    assert status == 403


def test_private_snapshot_uses_normalized_backup_and_never_changes_wal_source(tmp_path):
    import hashlib
    import sqlite3

    from core.chat_import.sqlite_snapshot import snapshot_connection

    path = tmp_path / 'source.db'
    work = tmp_path / 'work'
    work.mkdir()
    writer = sqlite3.connect(path)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('PRAGMA wal_autocheckpoint=0')
        writer.execute('CREATE TABLE work (name TEXT)')
        writer.execute("INSERT INTO work VALUES ('uncheckpointed work')")
        writer.commit()
        def fingerprint():
            return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                    for p in tmp_path.iterdir() if p.is_file()}
        before = fingerprint()
        with snapshot_connection(path, max_bytes=1 << 20, work_dir=work) as copy:
            assert copy.execute('SELECT * FROM work').fetchall() == [('uncheckpointed work',)]
            assert copy.execute('PRAGMA journal_mode').fetchone() == ('delete',)
            assert copy.execute('PRAGMA integrity_check').fetchone() == ('ok',)
            with pytest.raises(sqlite3.OperationalError):
                copy.execute("INSERT INTO work VALUES ('must refuse')")
        assert fingerprint() == before
        assert list(work.iterdir()) == []
    finally:
        writer.close()


def test_forget_removes_continuation_and_undo_cannot_restore_it(app):
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command

    preview = _upload_and_preview(app, _export_zip(EXPORT))
    run = _finish(app, preview, continuation=True)
    sid = run['first_session_id']
    ensure_chat_namespace(sid, grant_confirmed_profile=True)
    handled, reply = maybe_handle_memory_command(
        'Forget how do I bake bread.', session_id=sid,
        access_policy=resolve_memory_access_policy(chat_id=sid))
    assert handled, reply
    assert 'how do I bake bread' not in str(store.continuation_history(sid))
    status, result = _call(app, 'POST', '/api/chat/import/undo', body={'batch_id': run['run_id']})
    assert status == 200, result
    assert store.continuation_history(sid) == []


def test_lost_forget_authority_refuses_continuation_and_new_import(app):
    import shutil

    from core import runtime_paths
    from core.memory import revocation

    preview = _upload_and_preview(app, _export_zip(EXPORT))
    run = _finish(app, preview, continuation=True)
    assert store.continuation_history(run['first_session_id'])
    revocation.complete(revocation.begin('', 'never return this phrase'))
    # Lose only this test's disposable authority record. The missing record may never be
    # treated as a clean new memory home after an erasure has taken place.
    authority = runtime_paths.active_vool_home() / '.forget-authority'
    assert authority.is_relative_to(runtime_paths.active_vool_home())
    shutil.rmtree(authority)
    assert store.continuation_history(run['first_session_id']) == []
    preview = _upload_and_preview(app, _export_zip([EXPORT[1]]))
    status, result = _call(app, 'POST', '/api/chat/import/start', body={
        'preview_id': preview['preview_id'], 'chats': ['0'], 'continuation': True})
    assert status == 202, result
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        state = flow.status()
        if state['state'] != 'running':
            break
        time.sleep(.02)
    assert state['state'] == 'failed', state
    assert len(_sessions(app)) == 1


def test_partial_batch_failure_is_owned_and_reversible(app):
    from storage.db import get_connection

    preview = _upload_and_preview(app, _export_zip(EXPORT))
    connection = get_connection()
    try:
        connection.execute(f'''CREATE TRIGGER refuse_second_import BEFORE INSERT ON {store.CHATS_TABLE}
            WHEN (SELECT COUNT(*) FROM {store.CHATS_TABLE}) >= 1
            BEGIN SELECT RAISE(FAIL, 'synthetic disk fault'); END''')
        connection.commit()
        run = _run(app, preview, ['0', '1'])
    finally:
        connection.execute('DROP TRIGGER refuse_second_import')
        connection.commit()
        connection.close()
    assert run['outcome']['brought_in'] == 1 and run['outcome']['failed'] == 1
    status, result = _call(app, 'POST', '/api/chat/import/undo', body={'batch_id': run['run_id']})
    assert status == 200 and result['removed'] == 1
    assert _sessions(app) == {}


def test_interrupted_undo_keeps_membership_until_namespace_deletion_finishes(app):
    from core.context_namespace import load_chat_namespace
    from storage.db import get_connection

    preview = _upload_and_preview(app, _export_zip(EXPORT))
    run = _finish(app, preview)
    sid = run['first_session_id']
    connection = get_connection()
    try:
        connection.execute('''CREATE TRIGGER refuse_namespace_delete BEFORE UPDATE OF lifecycle_state
            ON context_namespaces WHEN NEW.lifecycle_state = 'deleted'
            BEGIN SELECT RAISE(ABORT, 'synthetic namespace fault'); END''')
        connection.commit()
        status, result = _call(app, 'POST', '/api/chat/import/undo', body={'batch_id': run['run_id']})
        assert status == 500 and result['error'] == 'undo_failed'
        assert store.imported_history_messages(sid) == []  # the earlier deletion committed
        assert store.batch_sessions(run['run_id']) == [sid]  # retry still owns the orphan
        assert load_chat_namespace(sid).lifecycle_state == 'active'
    finally:
        connection.execute('DROP TRIGGER refuse_namespace_delete')
        connection.commit()
        connection.close()
    flow.reset_for_tests()
    status, result = _call(app, 'POST', '/api/chat/import/undo', body={'batch_id': run['run_id']})
    assert status == 200 and result['removed'] == 1
    assert load_chat_namespace(sid).lifecycle_state == 'deleted'
    assert store.batch_sessions(run['run_id']) == []


def test_undo_waits_for_telemetry_erasure_instead_of_claiming_completion(app):
    from core.routing_decision_log import decisions_path

    preview = _upload_and_preview(app, _export_zip(EXPORT))
    run = _finish(app, preview)
    sid = run['first_session_id']
    path = decisions_path()
    assert not path.exists()
    path.mkdir(parents=True)
    try:
        status, result = _call(app, 'POST', '/api/chat/import/undo', body={'batch_id': run['run_id']})
        assert status == 500 and result['error'] == 'undo_failed'
        assert store.batch_sessions(run['run_id']) == [sid]
    finally:
        path.rmdir()
    status, result = _call(app, 'POST', '/api/chat/import/undo', body={'batch_id': run['run_id']})
    assert status == 200 and result['removed'] == 1
    assert store.batch_sessions(run['run_id']) == []


def test_concurrent_legacy_store_upgrade_keeps_resident_history(tmp_path):
    import sqlite3
    import threading
    from concurrent.futures import ThreadPoolExecutor

    path = tmp_path / 'legacy.db'
    connection = sqlite3.connect(path)
    for statement in store._DDL:
        connection.execute(statement)
    connection.execute(f'''INSERT INTO {store.CHATS_TABLE}
        (session_id,source_key,state) VALUES ('resident','resident-key','COMPLETE')''')
    connection.commit()
    connection.close()
    start = threading.Barrier(4)
    def upgrade(_index):
        connection = sqlite3.connect(path, timeout=15)
        try:
            start.wait(timeout=10)
            store.ensure_schema(connection)
        finally:
            connection.close()
    with ThreadPoolExecutor(max_workers=4) as threads:
        list(threads.map(upgrade, range(4)))
    connection = sqlite3.connect(path)
    try:
        assert connection.execute(f'SELECT session_id, continuation_enabled FROM {store.CHATS_TABLE}').fetchall() == [('resident', 0)]
        assert {row[1] for row in connection.execute(f'PRAGMA table_info({store.CHATS_TABLE})')} >= {'batch_id', 'continuation_enabled'}
    finally:
        connection.close()


def test_forgotten_attachment_name_cannot_return_through_import(app):
    from core.memory import revocation

    revocation.complete(revocation.begin('', 'retired_notes.txt'))
    conversation = {
        'uuid': 'attachment-revoked', 'name': 'Project notes',
        'chat_messages': [{'sender': 'human', 'text': 'Please review the document.',
                           'attachments': [{'file_name': 'retired_notes.txt'}]}]}
    preview = _upload_and_preview(app, _export_zip([conversation]))
    run = _run(app, preview, ['0'])
    assert run['outcome']['held_back'] == 1
    assert run['outcome']['brought_in'] == 0
    assert _sessions(app) == {}


def test_locked_rollback_store_exposes_only_committed_rows_without_touching_source(tmp_path):
    import hashlib
    import sqlite3

    from core.chat_import.sqlite_snapshot import snapshot_connection

    path = tmp_path / 'locked.db'
    work = tmp_path / 'work'
    work.mkdir()
    writer = sqlite3.connect(path)
    try:
        writer.execute('PRAGMA journal_mode=DELETE')
        writer.execute('PRAGMA cache_size=5')
        writer.execute('CREATE TABLE tasks (value TEXT)')
        writer.executemany('INSERT INTO tasks VALUES (?)', [('C' + 'x' * 4000,)] * 100)
        writer.commit()
        writer.execute('BEGIN EXCLUSIVE')
        # Update existing disk pages and force cache spill; merely appending can leave
        # the committed root page unchanged and fail to exercise rollback recovery.
        writer.execute('UPDATE tasks SET value = ?', ('U' + 'x' * 4000,))
        journal = path.with_name(path.name + '-journal')
        assert journal.is_file() and journal.stat().st_size > 0
        before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, journal)}
        with snapshot_connection(path, max_bytes=2 << 20, work_dir=work) as copy:
            assert copy.execute('SELECT COUNT(*) FROM tasks').fetchone() == (100,)
            assert copy.execute('SELECT DISTINCT substr(value, 1, 1) FROM tasks').fetchall() == [('C',)]
        assert {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (path, journal)} == before
        assert list(work.iterdir()) == []
    finally:
        writer.rollback()
        writer.close()
