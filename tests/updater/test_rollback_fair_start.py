"""The rollback health verdict must wait for the previous version to actually run.

The journey fixture slows nothing; a slow RUNNER is the hazard (0.7.6 CI shard 4:
`restored` with the old process's startup never executed, so its mutation proof was
missing). These tests put the slowness where the runner puts it — inside the previous
version's own startup, before it serves or mutates anything — and require the same
ordering the journey asserts: the old process runs and is observed before any verdict
restores the current version. Real launcher, real helper, real probe; only the time
windows are shrunk. Version and slowness travel with the swapped code (apps/), because
the launcher itself is a preserved file the swap never replaces.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.updater import rollback as rollback_module
from core.updater.feed import FeedConfig
from core.updater.linux import LinuxSourceInstaller
from core.updater.rollback import code_digest, rollback_candidate, run_manual_rollback
from core.updater.runtime import UpdateSubsystem
from core.updater.transaction import Step, UpdateJournal

MINI_APP = '''import json, os, sqlite3, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
spec = json.loads((Path(__file__).with_name('version.json')).read_text())
time.sleep(spec.get('start_sleep', 0))
home = Path(os.environ['VOOL_HOME'])
(home / 'data').mkdir(parents=True, exist_ok=True)
(home / 'data/vool_api.pid').write_text(str(os.getpid()))
if spec.get('broken') and (home / 'manual-trigger').exists():
    with sqlite3.connect(home / 'data/seed.db') as db:
        db.execute('DELETE FROM seed')
    (home / 'manual-mutation-proof').write_text('old process actually mutated current data')
    raise SystemExit(7)
time.sleep(spec.get('silent_sleep', 0))
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != '/healthz':
            self.send_response(404); self.end_headers(); return
        body = json.dumps({'runtime': {'app_version': spec['version']}}).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *args):
        pass
HTTPServer(('127.0.0.1', int(os.environ['TEST_APP_PORT'])), Handler).serve_forever()
'''


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def installed(tmp_path, monkeypatch):
    home = tmp_path / 'home'
    data = home / 'data'
    data.mkdir(parents=True)
    (home / 'config').mkdir()
    (home / 'config/profile.json').write_text('{"preference":"brief"}')
    with sqlite3.connect(data / 'seed.db') as db:
        db.execute('CREATE TABLE seed(value TEXT)')
        db.execute("INSERT INTO seed VALUES ('retained')")
    target, new = tmp_path / 'installed', tmp_path / 'new'
    target.mkdir(); new.mkdir()
    for path, version in ((target, '0.7.0'), (new, '0.7.1')):
        (path / 'apps').mkdir()
        (path / 'apps/__init__.py').write_text('')
        (path / 'apps/vool_api_server.py').write_text(MINI_APP)
        (path / 'core').mkdir()
        (path / 'core/version').write_text(version)
    (target / 'install_receipt.json').write_text(json.dumps({'runtime_home': str(home)}))
    (target / '.venv/bin').mkdir(parents=True)
    (target / '.venv/bin/python').symlink_to(sys.executable)
    port = _free_port()
    # The launcher is preserved across swaps, so it stays version-neutral; each code
    # tree carries its own version.json.
    (target / 'Start_VOOL.sh').write_text(
        '#!/bin/bash\n'
        'cd "$(dirname "$0")"\n'
        f'exec env TEST_APP_PORT={port} "$(pwd)/.venv/bin/python" -m apps.vool_api_server\n'
    )
    (target / 'Start_VOOL.sh').chmod(0o755)
    monkeypatch.setenv('VOOL_HOME', str(home))
    yield dict(home=home, data=data, target=target, new=new, port=port,
               monkeypatch=monkeypatch)
    pidfile = home / 'data/vool_api.pid'
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text()), signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    subprocess.run(['pkill', '-f', str(tmp_path / 'installed')], check=False)


def _install(installed, *, health_timeout, old_spec):
    """0.7.1 becomes current through the real protected swap; the retained 0.7.0 is
    then the manual rollback candidate, exactly as the journey leaves it."""
    target, new = installed['target'], installed['new']
    (target / 'apps/version.json').write_text(json.dumps(dict(old_spec, version='0.7.0')))
    (new / 'apps/version.json').write_text(json.dumps({'version': '0.7.1'}))
    swap = LinuxSourceInstaller(runtime_home=installed['home']).atomic_swap(
        new, target, txid='original')
    assert swap.ok
    subsystem = UpdateSubsystem(
        data_dir=installed['data'],
        feed=FeedConfig(app_path=str(target),
                        health_url=f'http://127.0.0.1:{installed["port"]}/healthz'),
        platform='linux-x64', installed_version='0.7.1', health_timeout=health_timeout)
    journal = UpdateJournal(subsystem.paths.transaction_dir('original'))
    journal.append(Step.DECIDED, {'installed_version': '0.7.0', 'target_version': '0.7.1',
                                  'platform': 'linux-x64',
                                  'previous_identity': '0.7.0', 'expected_identity': '0.7.1'})
    journal.append(Step.SWAPPED, {'prior_path': str(swap.prior_path)})
    journal.append(Step.FINALIZED, {'prior_code_sha256': code_digest(swap.prior_path)})
    return subsystem


def _seed_rows(data):
    with sqlite3.connect(data / 'seed.db') as db:
        return db.execute('SELECT * FROM seed').fetchall()


def test_slow_broken_previous_start_runs_before_the_verdict(installed):
    """The journey's manual_unhealthy ordering with the slow runner reproduced: the
    previous version is broken (mutates data, exits 7) and slow to start. A verdict
    reached on launch-time clocks restores the current version while the previous
    version's startup has not run yet — its mutation proof never appears. The verdict
    must wait for the run."""
    installed['monkeypatch'].setattr(rollback_module, 'RELAUNCH_START_BOUND_SECONDS', 30.0,
                                  raising=False)
    subsystem = _install(installed, health_timeout=3.0, old_spec={'start_sleep': 8, 'broken': True})
    (installed['home'] / 'manual-trigger').touch()

    result = run_manual_rollback(subsystem, rollback_candidate(subsystem), 'rollback-slow-broken')

    assert result == {'ok': False, 'restored_current': True}
    proof = installed['home'] / 'manual-mutation-proof'
    assert proof.exists() and proof.read_text() == 'old process actually mutated current data'
    assert _seed_rows(installed['data']) == [('retained',)]
    state = json.loads(next((installed['data'] / 'update_v2/manual_rollbacks')
                            .glob('rollback-slow-broken/state.json')).read_text())
    assert state['phase'] == 'restored'


def test_slow_but_healthy_previous_version_completes(installed):
    """A slow machine must not fail a GOOD rollback: the previous version takes
    longer than the health window to boot, then serves. Launch-time clocks call it
    unhealthy and restore the current version; the observed start completes."""
    installed['monkeypatch'].setattr(rollback_module, 'RELAUNCH_START_BOUND_SECONDS', 30.0,
                                  raising=False)
    subsystem = _install(installed, health_timeout=3.0, old_spec={'start_sleep': 5})

    result = run_manual_rollback(subsystem, rollback_candidate(subsystem), 'rollback-slow-ok')

    assert result['ok'] and result['version'] == '0.7.0'
    assert (installed['target'] / 'core/version').read_text() == '0.7.0'
    assert _seed_rows(installed['data']) == [('retained',)]


def test_never_observed_previous_start_stays_bounded(installed):
    """Fair start is bounded: a previous version that never comes up and never exits
    is still failed within its documented start allowance, and the current version is
    restored."""
    installed['monkeypatch'].setattr(rollback_module, 'RELAUNCH_START_BOUND_SECONDS', 2.0,
                                  raising=False)
    subsystem = _install(installed, health_timeout=3.0, old_spec={'start_sleep': 60})

    started = time.monotonic()
    result = run_manual_rollback(subsystem, rollback_candidate(subsystem), 'rollback-hung-start')
    elapsed = time.monotonic() - started

    assert result == {'ok': False, 'restored_current': True}
    assert elapsed < 30
    assert (installed['target'] / 'core/version').read_text() == '0.7.1'


def test_up_but_silent_previous_version_health_window_from_up(installed):
    """The health window is measured from the moment the previous version is actually
    up (it took the app's pidfile seat), not from launch: up-but-silent is failed
    after one health window, well inside the start allowance."""
    installed['monkeypatch'].setattr(rollback_module, 'RELAUNCH_START_BOUND_SECONDS', 30.0,
                                  raising=False)
    subsystem = _install(installed, health_timeout=2.0, old_spec={'silent_sleep': 600})

    started = time.monotonic()
    result = run_manual_rollback(subsystem, rollback_candidate(subsystem), 'rollback-silent')
    elapsed = time.monotonic() - started

    assert result == {'ok': False, 'restored_current': True}
    assert elapsed < 30
    assert (installed['target'] / 'core/version').read_text() == '0.7.1'


def test_a_previous_version_given_up_on_is_stopped_before_the_restore(installed):
    """A previous version still starting when its start allowance runs out is stopped when
    the current version is restored, not left to finish its start on top of it and change
    the restored data (independent review, 2026-10-30)."""
    installed['monkeypatch'].setattr(rollback_module, 'RELAUNCH_START_BOUND_SECONDS', 2.0,
                                  raising=False)
    subsystem = _install(installed, health_timeout=3.0, old_spec={'start_sleep': 8, 'broken': True})
    (installed['home'] / 'manual-trigger').touch()

    result = run_manual_rollback(subsystem, rollback_candidate(subsystem), 'rollback-abandoned')

    assert result == {'ok': False, 'restored_current': True}
    time.sleep(10)  # past the moment the abandoned previous version would have finished starting
    assert not (installed['home'] / 'manual-mutation-proof').exists()
    assert _seed_rows(installed['data']) == [('retained',)]
    assert (installed['target'] / 'core/version').read_text() == '0.7.1'
