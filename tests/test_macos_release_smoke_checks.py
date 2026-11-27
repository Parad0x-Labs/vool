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
    _drive_release_gates(tmp_path, leak)


def test_the_private_check_starts_after_earlier_chats_extraction_has_started(tmp_path, monkeypatch):
    # The product holds each chat's turns for its quiet window, then a background timer sends them to the extraction
    # model: a call of no private request, recorded as usual. Measured on the Intel smoke of c22a8352, the PIN chat's
    # timer fired 0.5 s into the private turn and its row was counted as the private chat's. The check waits out the
    # window (plus the dispatch margin) from the last chat before it turns private chat on.
    seen, slept = [], []
    monkeypatch.setattr(smoke.time, 'sleep', lambda seconds: slept.append((seconds, len(seen))))
    run = _drive_release_gates(tmp_path, '', quiet_seconds=5, seen=seen)
    assert len(slept) == 1
    seconds, chats_before = slept[0]
    assert 5 < seconds <= 5 + smoke.EXTRACTION_DISPATCH_MARGIN_SECONDS
    messages = [body['messages'][0]['content'] for body in seen]
    # every ordinary chat came before the wait; the message that turns private chat on came after it
    assert chats_before == messages.index('private chat')
    assert all(body['session_id'] == smoke.PRIVATE_SESSION for body in seen[chats_before:])
    private = next(row for row in run.steps if row['step'] == 'private chat leaves no transcript, memory or file')
    assert private['ok'] is True
    assert private['detail']['waited_for_quiet_seconds'] == round(seconds, 1)


def _drive_release_gates(tmp_path, leak, quiet_seconds=0, seen=None):
    home = tmp_path / 'home'
    db = home / 'Library/Application Support/VOOL/runtime/data/vool_web0_v2.db'
    db.parent.mkdir(parents=True)
    with sqlite3.connect(db) as connection:
        connection.execute('create table local_inference_benchmarks(raw_result_json text, created_at text)')
        connection.execute('insert into local_inference_benchmarks values (?, ?)',
                           ('{"tokens": 42}', '2026-11-13T05:00:00+00:00'))
    seen = [] if seen is None else seen
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
        smoke.release_privacy_checks(run, f'http://127.0.0.1:{server.server_port}', home, 'local-model', 10,
                                     quiet_seconds=quiet_seconds)
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
    return run


def test_dmg_with_two_apps_is_refused_and_detached(monkeypatch, tmp_path):
    mount = tmp_path / 'system mount'
    for name in ('First.app', 'Second.app'):
        (mount / name).mkdir(parents=True)
    calls = []

    def run(command, **kwargs):
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout=plistlib.dumps({'system-entities': [{'mount-point': str(mount)}]}))

    monkeypatch.setattr(smoke.subprocess, 'run', run)
    with pytest.raises(smoke.SmokeFailure, match=r'expected one \.app'):
        smoke.stage_app(Path('release.dmg'), tmp_path)
    assert calls[-1] == ['hdiutil', 'detach', str(mount)]


def test_a_failed_step_prints_what_the_app_logged_while_it_ran(tmp_path):
    """Intel smoke on 3dd1243b9: a recall turn ran past the 900 s chat timeout and nothing in the job log said where
    it waited. The failed step now prints the app's own log lines from its time window, stall reports cut short."""
    import json as _json

    support = tmp_path / "VOOL"
    (support / "data/logs").mkdir(parents=True)
    frame = '  File "/x/VOOL.app/Contents/Resources/app/core/plugin_ask.py", line 731, in _judge_turn\n'
    rows = [
        {"ts": "2026-11-25T13:00:00+00:00", "logger": "core.a", "msg": "before the step"},
        {"ts": "2026-11-25T13:10:00+00:00", "logger": "core.turn_stall_watch",
         "msg": "chat turn still running after 20.0 s; it is here:\n" + frame + "    x()\n"},
        {"ts": "2026-11-25T13:11:00+00:00", "logger": "vool.api", "msg": "http_request GET /api/runtime/status"},
        {"ts": "2026-11-25T13:30:00+00:00", "logger": "core.a", "msg": "after the step"},
    ]
    (support / "data/logs/vool_api.log").write_text("\n".join(_json.dumps(r) for r in rows) + "\nnot json\n")
    from datetime import datetime

    start = datetime.fromisoformat("2026-11-25T13:05:00+00:00").timestamp()
    end = datetime.fromisoformat("2026-11-25T13:20:00+00:00").timestamp()
    lines = smoke.turn_log(support, start, end)
    assert len(lines) == 1, lines
    assert "chat turn still running after 20.0 s" in lines[0] and "core/plugin_ask.py" in lines[0]
    assert smoke.turn_log(None, start, end) == []


#: Where the DMG is mounted, as the smoke names it (no real path is touched).
MOUNT = Path("dmg-mount") / "VOOL"


def _detach_runs(monkeypatch, codes):
    """hdiutil detach answering each call with the next exit code; the calls are returned."""
    import subprocess

    calls: list[list[str]] = []

    def run(args, **kwargs):
        calls.append(list(args))
        code = codes.pop(0) if codes else 0
        if code and kwargs.get("check"):
            raise subprocess.CalledProcessError(code, args)
        return subprocess.CompletedProcess(args, code, "", "hdiutil: couldn't eject" if code else "")

    monkeypatch.setattr(smoke.subprocess, "run", run)
    monkeypatch.setattr(smoke.time, "sleep", lambda seconds: None)
    return calls


def test_a_busy_dmg_is_waited_out_before_it_is_unmounted(monkeypatch):
    # The Intel runner for 6a50cd6e5: "couldn't eject disk2 - Resource busy" (exit 16) right after the copy.
    calls = _detach_runs(monkeypatch, [16, 16, 0])
    smoke.detach(MOUNT)
    assert calls == [["hdiutil", "detach", str(MOUNT)]] * 3


def test_a_dmg_busy_to_the_end_is_forced_off(monkeypatch):
    calls = _detach_runs(monkeypatch, [16] * 6 + [0])
    smoke.detach(MOUNT)
    assert calls[-1] == ["hdiutil", "detach", "-force", str(MOUNT)] and len(calls) == 7


def test_any_other_unmount_failure_stands(monkeypatch):
    import subprocess

    calls = _detach_runs(monkeypatch, [1])
    with pytest.raises(subprocess.CalledProcessError):
        smoke.detach(MOUNT)
    assert len(calls) == 1
