"""Privacy across the actual upload, preview, interrupted import and forget doors."""
from __future__ import annotations

import io
import json
import time
import zipfile

from core import runtime_paths
from core.chat_import import flow, store
from tests.test_chat_import_migration import app as app
from tests.test_chat_import_served import EXPORT, LOCAL, _call, _export_zip, _upload_and_preview
from tests.test_chat_import_served import app as import_app


def test_unread_config_secret_never_reaches_staging_preview_or_crash_residue(app):
    credential = 'sk-' + 'Z' * 48
    source = io.BytesIO()
    with zipfile.ZipFile(source, 'w') as archive:
        archive.writestr('conversations.json', json.dumps(EXPORT))
        archive.writestr('source_settings.json', json.dumps({'provider': {'api_key': credential}}))
    status, upload = _call(app, 'POST', '/api/chat/import/upload', raw=source.getvalue(),
                           headers={**LOCAL, 'Content-Type': 'application/zip'})
    assert status == 201
    incoming = runtime_paths.data_path('chat_import', 'incoming')
    assert len(list(incoming.iterdir())) == 1
    assert all(path.read_bytes() == b'' for path in incoming.iterdir())
    for path in runtime_paths.data_path().rglob('*'):
        if path.is_file():
            assert credential.encode() not in path.read_bytes(), str(path)
    status, preview = _call(app, 'POST', '/api/chat/import/preview', body={'upload_id': upload['upload_id']})
    assert status == 200 and preview['total'] == 2
    assert credential not in json.dumps(preview)
    flow._UPLOADS.clear()
    _call(app, 'GET', '/api/chat/import/sources')
    assert list(incoming.iterdir()) == []
    status, answer = _call(app, 'POST', '/api/chat/import/preview', body={'upload_id': upload['upload_id']})
    assert status == 404 and answer['error'] == 'upload_missing'


def test_forget_erases_interrupted_import_title_and_recovery_cannot_restore_it(app):
    from core.context_namespace import ensure_chat_namespace
    from core.memory.entries import resolve_memory_access_policy
    from core.persistent_memory import maybe_handle_memory_command

    marker = 'violet paper wheel'
    preview = _upload_and_preview(app, _export_zip([{'uuid': 'interrupted-private-title', 'name': marker,
        'chat_messages': [{'sender': 'human', 'text': 'We are folding the ' + marker}]}]))
    connection = store.connect()
    connection.execute(f'''CREATE TRIGGER refuse_complete BEFORE UPDATE OF state ON {store.CHATS_TABLE}
        WHEN NEW.state = 'COMPLETE' BEGIN SELECT RAISE(ABORT, 'synthetic completion fault'); END''')
    connection.commit()
    try:
        status, answer = _call(app, 'POST', '/api/chat/import/start', body={
            'preview_id': preview['preview_id'], 'chats': ['0']})
        assert status == 202, answer
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            _, answer = _call(app, 'GET', '/api/chat/import/status')
            if answer['state'] != 'running':
                break
            time.sleep(.02)
        assert answer['state'] == 'failed', answer
    finally:
        connection.execute('DROP TRIGGER refuse_complete')
        connection.commit()
    sid, pending = connection.execute(f'SELECT session_id,pending_title FROM {store.CHATS_TABLE}').fetchone()
    assert marker in pending
    ensure_chat_namespace(sid, grant_confirmed_profile=True)
    handled, reply = maybe_handle_memory_command('Forget ' + marker + '.', session_id=sid,
        access_policy=resolve_memory_access_policy(chat_id=sid))
    assert handled and 'no erasure step' not in reply, reply
    store_rows = connection.execute(f'SELECT * FROM {store.CHATS_TABLE}').fetchall()
    assert marker not in json.dumps([tuple(row) for row in store_rows])
    assert marker not in json.dumps(store.imported_history_messages(sid))
    flow.reset_for_tests()
    from core.chat_import.importer import recover_pending

    recover_pending()
    _, sessions = _call(app, 'GET', '/api/chat/sessions')
    assert marker not in json.dumps(sessions)
    connection.close()
