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


@pytest.mark.parametrize("unhealthy", [False, True])
def test_one_press_restarts_installed_source_and_keeps_data(tmp_path, unhealthy):
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
    if unhealthy:
        (new / "apps/vool_api_server.py").write_text("""import os,sqlite3
from pathlib import Path
home=Path(os.environ['VOOL_HOME'])
with sqlite3.connect(home/'data/seed.db') as connection:
 connection.execute('DELETE FROM memory')
(home/'config/profile.json').write_text('changed')
raise SystemExit(7)
""")
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
    launcher.write_text(f'#!/bin/bash\ncd "$(dirname "$0")"\nexec .venv/bin/python -m apps.vool_api_server --port {port}\n')
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

            def terminal():
                for journal in (home / "data/update_v2/transactions").glob("*/journal.jsonl"):
                    last = json.loads(journal.read_text().splitlines()[-1])["step"]
                    if last in ("finalized", "rolled_back"):
                        return last
                status = json.loads((home / "data/update_v2/status.json").read_text())
                assert status["phase"] != "failed", status
                return None

            assert _wait(terminal, timeout=150) == ("rolled_back" if unhealthy else "finalized")
            after = _wait(lambda: _api(origin, "/healthz"))
            assert after["runtime"]["app_version"] == ("0.7.0" if unhealthy else "0.7.1")
            with sqlite3.connect(database) as connection:
                assert connection.execute("SELECT * FROM memory").fetchall() == [("retained",)]
                assert connection.execute("PRAGMA quick_check").fetchall() == [("ok",)]
            assert profile.read_text() == '{"preference":"brief","wallet":"disposable-watch-only"}'
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
