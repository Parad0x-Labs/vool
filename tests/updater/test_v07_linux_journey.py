"""Installed-source one-press journey, collected by the ordinary Linux CI lane."""
from __future__ import annotations

import hashlib
import http.server
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

from installer.build_update_payload import write_tree_zip

from .helpers import canonical_manifest_bytes, generate_publisher_keypair, sign_bytes

REPO = Path(__file__).resolve().parents[2]


def _api(origin, path, body=None):
    request = urllib.request.Request(origin + path, data=None if body is None else json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.loads(response.read())


def _wait(callback, *, timeout=90):
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            result = callback()
            if result:
                return result
        except Exception as exc:
            last = str(exc)
        time.sleep(.1)
    raise AssertionError(f"installed-source journey timed out: {last}")


@pytest.mark.parametrize("scenario", ["healthy", "unhealthy", "interrupted", "manual", "manual_offline", "manual_unhealthy", "manual_interrupted"])
def test_one_press_restarts_installed_source_and_keeps_data(tmp_path, scenario):
    unhealthy = scenario == "unhealthy"
    interrupted = scenario == "interrupted"
    home = tmp_path / "home"
    (home / "data").mkdir(parents=True)
    (home / "config").mkdir()
    profile = home / "config/profile.json"
    profile.write_text('{"preference":"brief","wallet":"disposable-watch-only"}')
    database = home / "data/seed.db"
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE memory(value TEXT)")
        connection.execute("INSERT INTO memory VALUES ('retained')")
    target = tmp_path / "Installed Source With Spaces"
    new = tmp_path / "new"
    feed_root = tmp_path / "feed"
    feed_root.mkdir()

    class Handler(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(feed_root), **kwargs)

        def log_message(self, *args):
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    feed = f"http://127.0.0.1:{server.server_port}"
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    private, public = generate_publisher_keypair()
    for code, version in ((target, "0.7.0"), (new, "0.7.1")):
        code.mkdir()
        for package in ("apps", "core", "adapters", "storage", "network", "relay", "retrieval",
                        "sandbox", "tools", "ops", "installer", "config", "skills", "plugins"):
            shutil.copytree(REPO / package, code / package, symlinks=True,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copy2(REPO / "pyproject.toml", code / "pyproject.toml")
        path = code / "core/app_version.py"
        path.write_text(path.read_text().replace('VOOL_VERSION = "0.7.0"', f'VOOL_VERSION = "{version}"'))
        # The same POSIX fixture runs on the development Mac. Ubuntu uses its
        # actual sys.platform/uname, so CI exercises production detection too.
        if sys.platform != "linux":
            path = code / "core/updater/platforms.py"
            path.write_text(path.read_text().replace('raw_os = (platform or sys.platform).lower()',
                                                    'raw_os = (platform or "linux").lower()').replace(
                                                    'raw_machine = (_bundle_manifest_arch() or "").lower()',
                                                    'raw_machine = "x86_64"'))
        (code / "config/build-source.json").write_text(json.dumps({"commit_full": "a" * 40,
                                                                  "source_kind": "git", "dirty_state": True}))
        (code / "config/release/trusted_publisher_keys.json").write_text(json.dumps({"test": public}))
        (code / "config/release/update_feed.json").write_text(json.dumps({
            "manifest_url": feed + "/manifest.json", "channel": "beta", "health_url": origin + "/healthz",
        }))
    if scenario == 'manual_offline':
        entry = new / 'apps/vool_api_server.py'
        text = entry.read_text().replace('from __future__ import annotations', '''from __future__ import annotations
import os as _offline_os
from pathlib import Path as _OfflinePath
if (_OfflinePath(_offline_os.environ['VOOL_HOME'])/'current-runtime-broken').exists():
 raise SystemExit(7)
''')
        entry.write_text(text)
    if scenario == 'manual_unhealthy':
        entry = target / 'apps/vool_api_server.py'
        text = entry.read_text().replace('from __future__ import annotations', '''from __future__ import annotations
import os as _rollback_os, sqlite3 as _rollback_sqlite
from pathlib import Path as _RollbackPath
_rollback_home = _RollbackPath(_rollback_os.environ['VOOL_HOME'])
if (_rollback_home/'manual-trigger').exists():
 with _rollback_sqlite.connect(_rollback_home/'data/seed.db') as _rollback_db:
  _rollback_db.execute('DELETE FROM memory')
 (_rollback_home/'config/profile.json').write_text('manual rollback mutation')
 (_rollback_home/'manual-mutation-proof').write_text('old process actually mutated current data')
 raise SystemExit(7)
''')
        entry.write_text(text)
    if scenario == 'manual_interrupted':
        entry = new / 'core/updater/rollback.py'
        text = entry.read_text().replace("_save(path, state, 'swapped')",
                                         "_save(path, state, 'swapped')\n        os._exit(73)")
        entry.write_text(text)
    if unhealthy:
        (new / "apps/vool_api_server.py").write_text("""import os,sqlite3,json
from pathlib import Path
home=Path(os.environ['VOOL_HOME'])
with sqlite3.connect(home/'data/seed.db') as connection:
 connection.execute('DELETE FROM memory')
(home/'config/profile.json').write_text('changed')
with sqlite3.connect(home/'data/seed.db') as connection:
 (home/'mutation-proof.json').write_text(json.dumps({'rows': connection.execute('SELECT * FROM memory').fetchall(), 'profile': (home/'config/profile.json').read_text()}))
raise SystemExit(7)
""")
    if interrupted:
        # Crash the OLD detached worker immediately after its real swap. On the
        # next launch the new API must recover outside its own process, because
        # rollback stops that API. A mismatched health identity forces recovery.
        transaction = target / "core/updater/transaction.py"
        text = transaction.read_text().replace(
            "self._journal.append(step, payload)",
            "self._journal.append(step, payload)\n        if step is Step.SWAPPED: os._exit(73)",
        )
        transaction.write_text(text)
        (new / "config/build-source.json").write_text(json.dumps({"commit_full": "b" * 40,
                                                                  "source_kind": "git", "dirty_state": True}))
        entry = new / "apps/vool_api_server.py"
        text = entry.read_text().replace("from __future__ import annotations", """from __future__ import annotations
import os as _fixture_os,sqlite3 as _fixture_sqlite,json as _fixture_json
from pathlib import Path as _FixturePath
_fixture_home=_FixturePath(_fixture_os.environ['VOOL_HOME'])
with _fixture_sqlite.connect(_fixture_home/'data/seed.db') as _fixture_connection:
 _fixture_connection.execute('DELETE FROM memory')
(_fixture_home/'config/profile.json').write_text('changed before startup recovery')
with _fixture_sqlite.connect(_fixture_home/'data/seed.db') as _fixture_connection:
 (_fixture_home/'mutation-proof.json').write_text(_fixture_json.dumps({'rows': _fixture_connection.execute('SELECT * FROM memory').fetchall(), 'profile': (_fixture_home/'config/profile.json').read_text()}))
""")
        entry.write_text(text)
    venv = target / ".venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "bin/python").symlink_to(sys.executable)
    (venv / "retained").write_text("environment")
    (target / ".git").mkdir()
    (target / ".git/local-only").write_text("git state")
    (target / "workspace").mkdir()
    (target / "workspace/retained").write_text("workspace")
    (target / "install_receipt.json").write_text(json.dumps({"runtime_home": str(home)}))
    launcher = target / "Start_VOOL.sh"
    launcher.write_text(f'#!/bin/bash\ncd "$(dirname "$0")"\nexec "$(pwd)/.venv/bin/python" -m apps.vool_api_server --port {port}\n')
    launcher.chmod(0o755)
    protected = {name: (target / name).read_bytes() for name in (
        "Start_VOOL.sh", "install_receipt.json", ".venv/retained", ".git/local-only", "workspace/retained"
    )}
    archive = feed_root / "update.zip"
    write_tree_zip(new, archive, prefix="vool-0.7.1-beta")
    blob = archive.read_bytes()
    manifest = {"schema": "vool.update.manifest.v1", "product": "vool", "channel": "beta", "version": "0.7.1",
                "sequence": 1, "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "minimum_compatible": "0.7.0", "notes": "installed-source test", "build_commit": "a" * 40,
                "artifacts": {"linux-x64": {"url": feed + "/update.zip", "size": len(blob),
                    "sha256": hashlib.sha256(blob).hexdigest(), "signature": sign_bytes(private, blob)}}}
    manifest["signature"] = {"key_id": "test", "sig": sign_bytes(private, canonical_manifest_bytes(manifest))}
    (feed_root / "manifest.json").write_text(json.dumps(manifest))
    env = dict(os.environ, VOOL_HOME=str(home), VOOL_TEST_MODE="1", VOOL_UNATTENDED="1",
               VOOL_KEYCHAIN_ALLOWED="0", VOOL_CREDENTIAL_STORE="vault", VOOL_KEY_STORAGE_MODE="file",
               VOOL_KEY_PASSPHRASE="disposable-posix", VOOL_LOCAL_MODELS_ENABLED="0",
               VOOL_DISABLE_COMPUTE_MODE="1", VOOL_DISABLE_MESH_DAEMON="1", PYTHONDONTWRITEBYTECODE="1",
               OLLAMA_HOST="http://127.0.0.1:9")
    for name in ("VOOL_UPDATE_TARGET_PIDFILE", "VOOL_UPDATE_MANIFEST_URL", "VOOL_UPDATE_APP_PATH",
                 "VOOL_UPDATE_HEALTH_URL", "VOOL_UPDATE_PUBLISHER_KEYS", "VOOL_UPDATE_TRUSTED_KEYS_JSON",
                 "VOOL_NATIVE_PARENT_PID", "VOOL_LAUNCHD_SUPERVISOR"):
        env.pop(name, None)
    owned = set()
    with (tmp_path / "api.log").open("wb") as log:
        process = subprocess.Popen(["bash", str(launcher)], env=env, stdout=log, stderr=log, start_new_session=True)
        owned.add(process.pid)
        (tmp_path / "owned-pids.json").write_text(json.dumps(sorted(owned)))
        try:
            _wait(lambda: _api(origin, "/healthz"))
            notice = _wait(lambda: (lambda value: value if value["phase"] == "ready" else None)(
                _api(origin, "/api/update/status")))
            assert notice["target_version"] == "0.7.1"
            response = _api(origin, "/api/update/install", {})
            assert response["accepted"]
            if interrupted:
                from core.updater.macos import _pid_has_exited

                lease_path = home / "data/update_v2/worker.json"

                def crashed():
                    lease = json.loads(lease_path.read_text())
                    return lease if _pid_has_exited(lease["pid"]) else None

                _wait(crashed)
                journals = list((home / "data/update_v2/transactions").glob("*/journal.jsonl"))
                assert json.loads(journals[0].read_text().splitlines()[-1])["step"] == "swapped"
                launched = subprocess.Popen(["bash", str(launcher)], env=env, stdout=log, stderr=log, start_new_session=True)
                owned.add(launched.pid)
                (tmp_path / "owned-pids.json").write_text(json.dumps(sorted(owned)))

            def terminal():
                for journal in (home / "data/update_v2/transactions").glob("*/journal.jsonl"):
                    last = json.loads(journal.read_text().splitlines()[-1])["step"]
                    if last in ("finalized", "rolled_back"):
                        return last
                status = json.loads((home / "data/update_v2/status.json").read_text())
                assert status["phase"] != "failed", status
                return None

            assert _wait(terminal, timeout=150) == ("rolled_back" if unhealthy or interrupted else "finalized")
            after = _wait(lambda: _api(origin, "/healthz"))
            assert after["runtime"]["app_version"] == ("0.7.0" if unhealthy or interrupted else "0.7.1")
            if unhealthy or interrupted:
                proof = json.loads((home / "mutation-proof.json").read_text())
                assert proof == {"rows": [], "profile": "changed before startup recovery" if interrupted else "changed"}
                time.sleep(.5)  # the boot scheduler must not erase recovery truth
                assert _api(origin, "/api/update/status")["phase"] == "rolled_back"
            if interrupted:
                results = list((home / "data/update_v2/workers").glob("recovery-*/recovery-result.json"))
                _wait(lambda: any(path.exists() for path in results) or list(
                    (home / "data/update_v2/workers").glob("recovery-*/recovery-result.json")))
                results = list((home / "data/update_v2/workers").glob("recovery-*/recovery-result.json"))
                assert json.loads(results[0].read_text())["ok"]
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT * FROM memory").fetchall() == [("retained",)]
                assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
            assert profile.read_text() == '{"preference":"brief","wallet":"disposable-watch-only"}'
            if scenario.startswith('manual'):
                with sqlite3.connect(database) as connection:
                    connection.execute("INSERT INTO memory VALUES ('written after update')")
                profile.write_text('settings and disposable key changed after updating')
                status = _wait(lambda: (lambda value: value if value['rollback_available'] else None)(
                    _api(origin, '/api/update/status')))
                assert status['rollback_version'] == '0.7.0'
                import urllib.error

                with pytest.raises(urllib.error.HTTPError) as refusal:
                    _api(origin, '/api/update/rollback', {'expected_data_dir': str(tmp_path / 'other-profile')})
                assert refusal.value.code == 409
                with pytest.raises(urllib.error.HTTPError) as refusal:
                    request = urllib.request.Request(origin + '/api/update/rollback', data=b'{}',
                        headers={'Content-Type': 'application/json', 'Origin': 'https://untrusted.invalid'})
                    urllib.request.urlopen(request, timeout=3)
                assert refusal.value.code == 403
                if scenario == 'manual_unhealthy':
                    (home / 'manual-trigger').touch()
                if scenario == 'manual_offline':
                    # Stop only this fixture's recorded current app, then invoke
                    # the terminal command while its API is unavailable.
                    pid = int((home / 'data/vool_api.pid').read_text())
                    command = subprocess.run(['ps', '-p', str(pid), '-o', 'args='], capture_output=True, text=True).stdout
                    assert str(target) in command
                    owned.add(pid); os.kill(pid, signal.SIGTERM)
                    from core.updater.macos import _pid_has_exited

                    _wait(lambda: _pid_has_exited(pid))
                    (home / 'current-runtime-broken').touch()
                    broken = subprocess.Popen(['bash', str(launcher)], env=env, stdout=log, stderr=log,
                                              start_new_session=True)
                    owned.add(broken.pid)
                    (tmp_path / 'owned-pids.json').write_text(json.dumps(sorted(owned)))
                    assert broken.wait(timeout=30) == 7  # current backend genuinely cannot start
                    cli = subprocess.run([sys.executable, '-m', 'installer.update_cli', 'rollback',
                                          '--i-pressed-rollback', '--wait'], cwd=target, env=env,
                                         capture_output=True, text=True, timeout=200)
                    (tmp_path / 'rollback-cli.log').write_text(cli.stdout + cli.stderr)
                    assert cli.returncode == 0, cli.stdout + cli.stderr
                else:
                    assert _api(origin, '/api/update/rollback', {})['accepted']
                if scenario == 'manual_interrupted':
                    from core.updater.macos import _pid_has_exited

                    lease = home / 'data/update_v2/worker.json'
                    _wait(lambda: (lambda value: value if value['txid'].startswith('rollback-') and
                        _pid_has_exited(value['pid']) else None)(json.loads(lease.read_text())))
                    state = next((home / 'data/update_v2/manual_rollbacks').glob('*/state.json'))
                    assert json.loads(state.read_text())['phase'] == 'swapped'
                    launched = subprocess.Popen(['bash', str(launcher)], env=env, stdout=log, stderr=log,
                                                start_new_session=True)
                    owned.add(launched.pid)
                    (tmp_path / 'owned-pids.json').write_text(json.dumps(sorted(owned)))

                def rollback_terminal():
                    for path in (home / 'data/update_v2/manual_rollbacks').glob('*/state.json'):
                        state = json.loads(path.read_text())
                        if state['phase'] in ('completed', 'restored', 'aborted', 'rollback_failed'):
                            return state['phase']

                assert _wait(rollback_terminal, timeout=160) == ('restored' if scenario == 'manual_unhealthy' else 'completed')
                assert _wait(lambda: _api(origin, '/healthz'))['runtime']['app_version'] == (
                    '0.7.1' if scenario == 'manual_unhealthy' else '0.7.0')
                with sqlite3.connect(database) as connection:
                    assert connection.execute('SELECT * FROM memory').fetchall() == [('retained',), ('written after update',)]
                assert profile.read_text() == 'settings and disposable key changed after updating'
                if scenario == 'manual_unhealthy':
                    assert (home / 'manual-mutation-proof').read_text() == 'old process actually mutated current data'
            assert all((target / name).read_bytes() == body for name, body in protected.items())
            assert not (target / ".vool_updating.lock").exists()
        finally:
            pidfile = home / "data/vool_api.pid"
            if pidfile.exists():
                owned.add(int(pidfile.read_text()))
            (tmp_path / "owned-pids.json").write_text(json.dumps(sorted(owned)))
            for pid in owned:
                command = subprocess.run(["ps", "-p", str(pid), "-o", "args="], capture_output=True, text=True).stdout
                if str(tmp_path) in command:
                    try:
                        os.kill(pid, signal.SIGTERM)
                    except ProcessLookupError:
                        pass
            server.shutdown()
            server.server_close()
