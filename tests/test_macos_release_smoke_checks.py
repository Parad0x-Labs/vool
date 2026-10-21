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


@pytest.mark.parametrize('leak', [False, True])
def test_release_gates_drive_served_chat_and_detect_private_leaks(tmp_path, leak):
    home = tmp_path / 'home'
    db = home / 'Library/Application Support/VOOL/runtime/data/vool_web0_v2.db'
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as connection:
        connection.execute('create table local_inference_benchmarks(raw_result_json text)')
        connection.execute('insert into local_inference_benchmarks values (?)', ('{"tokens": 42}',))
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            seen.append(body)
            message = body['messages'][0]['content']
            answer = 'Done.'
            if message == 'What is my favourite colour?':
                answer = 'teal'
            if message == 'What is my locker number?':
                answer = 'I do not have that number.'
            data = {'message': {'content': answer}}
            self.reply(data)

        def do_GET(self):
            if '/api/chat/history' in self.path:
                self.reply({'messages': [smoke.SECRET] if leak else []})
            elif smoke.PIN_SESSION in self.path:
                self.reply({'entries': []})
            else:
                self.reply({'entries': [{'text': 'favourite colour is teal'}]})

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
    assert sum(not row['ok'] for row in run.steps) == int(leak)
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
