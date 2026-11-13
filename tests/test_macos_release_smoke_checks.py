from __future__ import annotations

import json
import plistlib
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from ops import macos_bundle_smoke as smoke


def test_dmg_staging_uses_the_system_mount_point(monkeypatch, tmp_path):
    mount = tmp_path / 'system mount'
    (mount / 'VOOL.app').mkdir(parents=True)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        if command[1] == 'attach':
            assert '-mountpoint' not in command
            assert '-plist' in command
            return SimpleNamespace(stdout=plistlib.dumps({'system-entities': [{'mount-point': str(mount)}]}))
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(smoke.subprocess, 'run', run)
    target = smoke.stage_app(tmp_path / 'release.dmg', tmp_path)
    assert target == tmp_path / 'Applications/VOOL.app'
    assert calls[-1] == ['hdiutil', 'detach', str(mount)]


def _speed_row(db, call_seconds):
    """A speed row as the recorder writes it: when the call ends, with the call's own total duration."""
    from datetime import datetime, timezone

    raw = json.dumps({'eval_count': 3, 'total_duration': int(call_seconds * 1e9)})
    with sqlite3.connect(db) as connection:
        connection.execute('insert into local_inference_benchmarks values (?, ?)',
                           (raw, datetime.now(timezone.utc).isoformat()))


@pytest.mark.parametrize('leak', ['', 'history', 'pin', 'pin on disk', 'speed row'])
def test_release_gates_drive_served_chat_and_detect_private_leaks(tmp_path, leak):
    home = tmp_path / 'home'
    db = home / 'Library/Application Support/VOOL/runtime/data/vool_web0_v2.db'
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as connection:
        connection.execute('create table local_inference_benchmarks(raw_result_json text, created_at text)')
        connection.execute('insert into local_inference_benchmarks values (?, ?)',
                           ('{"tokens": 42}', '2026-11-13T05:00:00+00:00'))
    seen = []
    # every chat's listing shows the profile facts all chats share (cross-chat recall), but only once the chat
    # exists: the served listing of a session with no turn yet is empty (the Intel smoke of d5d044b0 counted
    # the teal fact as the PIN turn's because of that)
    profile = {'record_id': 'memory-teal', 'fact': 'my favourite colour is teal.', 'scope': 'user_profile'}
    pin_rows = [profile]
    chats = set()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            chats.add(body['session_id'])
            message = body['messages'][0]['content']
            answer = 'Done.'
            if smoke.SECRET in message:
                # An earlier chat's background call, begun 500 s ago, ends during the private turn: not its row.
                _speed_row(db, 500)
                if leak == 'speed row':
                    _speed_row(db, 0.2)  # a call that began inside the private turn: its row
            if message == 'What is my favourite colour?':
                answer = 'teal'
            if message == 'What is my locker number?':
                answer = 'I do not have that number.'
            if 'PIN' in message and leak == 'pin':
                pin_rows.append({'record_id': 'memory-pin', 'fact': 'my card PIN is [redacted]', 'scope': 'chat'})
            if 'PIN' in message and leak == 'pin on disk':
                (home / 'transcript.jsonl').write_text(json.dumps({'text': message}) + '\n')
            data = {'message': {'content': answer}}
            self.reply(data)

        def do_GET(self):
            if '/api/chat/history' in self.path:
                self.reply({'messages': [smoke.SECRET] if leak == 'history' else []})
            elif smoke.PIN_SESSION in self.path:
                self.reply({'entries': list(pin_rows) if smoke.PIN_SESSION in chats else []})
            else:
                self.reply({'entries': [profile]})

        def reply(self, data):
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.end_headers()
            self.wfile.write(json.dumps(data).encode())

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    run = smoke.Run(tmp_path)
    try:
        smoke.release_privacy_checks(run, f'http://127.0.0.1:{server.server_port}', home, 'local-model', 10)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    assert len(run.steps) == 5
    failed = [row['step'] for row in run.steps if not row['ok']]
    assert failed == ({'': [], 'history': ['private chat leaves no transcript, memory or file'],
                       'pin': ['PIN not echoed and not stored'],
                       'pin on disk': ['PIN not echoed and not stored'],
                       'speed row': ['private chat leaves no transcript, memory or file']}[leak])
    assert len(seen) == 9
    assert all(body['model'] == 'local-model' and body['model_selection'] == 'pin' for body in seen)
    assert {body['session_id'] for body in seen} == {
        smoke.PIN_SESSION, smoke.PRIVATE_SESSION, 'release-forget-digit-check',
    }


def test_dmg_with_two_apps_is_refused_and_detached(monkeypatch, tmp_path):
    mount = tmp_path / 'system mount'
    for name in ('First.app', 'Second.app'):
        (mount / name).mkdir(parents=True)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(stdout=plistlib.dumps({'system-entities': [{'mount-point': str(mount)}]}))

    monkeypatch.setattr(smoke.subprocess, 'run', run)
    with pytest.raises(smoke.SmokeFailure, match=r'expected one \.app'):
        smoke.stage_app(Path('release.dmg'), tmp_path)
    assert calls[-1] == ['hdiutil', 'detach', str(mount)]
