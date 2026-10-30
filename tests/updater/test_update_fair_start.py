"""The forward update's health verdict must wait for the new version to actually run.

The rollback flow already measures its verdict from the relaunched process's own
start (tests/updater/test_rollback_fair_start.py); the FORWARD flow (the in-app
"Download and update" press) has the same hazard on a slow machine: its health
verdicts are measured from launch time with fixed windows, so a good new version
that boots slower than the window is rolled back before it ever ran — and a slow
BROKEN one is rolled back before it could mutate anything, which proves nothing.

These tests drive the real UpdateFlow (core/updater/transaction.py) end to end:
real launcher (Start_VOOL.sh), real LinuxProcessHelper, real HttpHealthProbe,
real LinuxSourceInstaller protected swap, real signed manifest + real zip payload
served through the staged downloader. The installed 0.7.0 tree and the offered
0.7.1 tree each carry their own behavior spec (apps/version.json); the launcher
is version-neutral and preserved by the swap, so the spec travels with the code.
Slowness lives inside the new version's own startup, where a slow runner puts it.
"""
from __future__ import annotations

import hashlib
import json
import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

from core.updater.decision import UpdateDecisionReason, decide_update
from core.updater.download import StagedDownloader
from core.updater.health import HttpHealthProbe
from core.updater.linux import LinuxProcessHelper, LinuxSourceInstaller
from core.updater.manifest import parse_and_verify_manifest
from core.updater.migrations import MigrationCatalog
from core.updater.transaction import Step, UpdateFlow
from core.updater.trust import TrustedPublishers
from core.updater.work import WorkCoordinator
from installer.build_update_payload import write_tree_zip

from .helpers import canonical_manifest_bytes, generate_publisher_keypair, manifest_bytes, sign_bytes

# The shared fair-start module lands with the forward-flow fix; until then the
# verdicts run on their launch-time clocks and these tests document that by
# failing on exactly the symptoms that change.
try:
    from core.updater import relaunch_health
except ImportError:  # the fix's shared module does not exist yet on this tree
    relaunch_health = None

KEY_ID = "release-2026-01"

MINI_APP = '''import json, os, sqlite3, time
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
spec = json.loads((Path(__file__).with_name('version.json')).read_text())
time.sleep(spec.get('start_sleep', 0))
home = Path(os.environ['VOOL_HOME'])
(home / 'data').mkdir(parents=True, exist_ok=True)
(home / 'data/vool_api.pid').write_text(str(os.getpid()))
if spec.get('broken') and (home / 'update-trigger').exists():
    with sqlite3.connect(home / 'data/seed.db') as db:
        db.execute('DELETE FROM seed')
    (home / 'update-mutation-proof').write_text('new process actually mutated current data')
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


class FakeServer:
    """Serves the signed payload bytes through the downloader's fetch seam."""

    def __init__(self, blob: bytes):
        self.blob = blob

    def open(self, url, headers=None, timeout=30.0):
        body = self.blob

        class _Resp:
            status = 200
            headers = {"ETag": '"e1"'}

            def __init__(self, payload):
                self._body = payload
                self._offset = 0

            def read(self, n=-1):
                chunk = self._body[self._offset : self._offset + n if n and n > 0 else None]
                self._offset += len(chunk)
                return chunk

            def close(self):
                pass

        return _Resp(body)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def rig(tmp_path, monkeypatch):
    home = tmp_path / "home"
    data = home / "data"
    data.mkdir(parents=True)
    (home / "config").mkdir()
    (home / "config/profile.json").write_text('{"preference":"brief"}')
    with sqlite3.connect(data / "seed.db") as db:
        db.execute("CREATE TABLE seed(value TEXT)")
        db.execute("INSERT INTO seed VALUES ('retained')")
    target, new = tmp_path / "installed", tmp_path / "new"
    target.mkdir()
    new.mkdir()
    for path, version in ((target, "0.7.0"), (new, "0.7.1")):
        (path / "apps").mkdir()
        (path / "apps/__init__.py").write_text("")
        (path / "apps/vool_api_server.py").write_text(MINI_APP)
        (path / "core").mkdir()
        (path / "core/version").write_text(version)
        (path / "pyproject.toml").write_text(f"[project]\nname = \"vool\"\nversion = \"{version}\"\n")
        (path / "installer").mkdir()
        (path / "installer/install_vool.sh").write_text("# linux installer marker\n")
    # The installed tree behaves like a well-behaved prior version: it boots fast.
    (target / "apps/version.json").write_text(json.dumps({"version": "0.7.0"}))
    (target / "install_receipt.json").write_text(json.dumps({"runtime_home": str(home)}))
    (target / ".venv/bin").mkdir(parents=True)
    (target / ".venv/bin/python").symlink_to(sys.executable)
    port = _free_port()
    # The launcher is preserved across swaps, so it stays version-neutral; each
    # code tree carries its own version.json with the per-tree behavior spec.
    (target / "Start_VOOL.sh").write_text(
        "#!/bin/bash\n"
        'cd "$(dirname "$0")"\n'
        f"exec env TEST_APP_PORT={port} \"$(pwd)/.venv/bin/python\" -m apps.vool_api_server\n"
    )
    (target / "Start_VOOL.sh").chmod(0o755)
    monkeypatch.setenv("VOOL_HOME", str(home))
    private_hex, public_hex = generate_publisher_keypair()
    yield dict(tmp=tmp_path, home=home, data=data, target=target, new=new, port=port,
               keys=(private_hex, public_hex),
               trust=TrustedPublishers(pinned_keys={KEY_ID: public_hex}))
    pidfile = home / "data/vool_api.pid"
    if pidfile.exists():
        try:
            os.kill(int(pidfile.read_text()), signal.SIGTERM)
        except (ValueError, ProcessLookupError, PermissionError):
            pass
    subprocess.run(["pkill", "-f", str(tmp_path / "installed")], check=False)


def _shrink_start_bound(monkeypatch, seconds: float) -> None:
    """Shrink the documented start allowance the fair-start wait consults. Before
    the shared module exists, the launch-clock verdict ignores it entirely."""
    if relaunch_health is not None:
        monkeypatch.setattr(relaunch_health, "RELAUNCH_START_BOUND_SECONDS", seconds)


def _offer(rig, new_spec: dict):
    """Publish a signed 0.7.1 offer whose payload is a real zip of the new tree."""
    new = rig["new"]
    (new / "apps/version.json").write_text(json.dumps(dict(new_spec, version="0.7.1")))
    archive = rig["tmp"] / "update.zip"
    archive.unlink(missing_ok=True)
    write_tree_zip(new, archive, prefix="vool-0.7.1-beta")
    blob = archive.read_bytes()
    private_hex, _public_hex = rig["keys"]
    manifest = {
        "schema": "vool.update.manifest.v1",
        "product": "vool",
        "channel": "beta",
        "version": "0.7.1",
        "sequence": 1,
        "published_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "minimum_compatible": "0.7.0",
        "notes": "installed-source fair-start test",
        "artifacts": {
            "linux-x64": {
                "url": "https://updates.example.invalid/vool/0.7.1/linux-x64.zip",
                "size": len(blob),
                "sha256": hashlib.sha256(blob).hexdigest(),
                "signature": sign_bytes(private_hex, blob),
            }
        },
    }
    manifest["signature"] = {"key_id": KEY_ID, "sig": sign_bytes(private_hex, canonical_manifest_bytes(manifest))}
    raw = manifest_bytes(manifest)
    verification = parse_and_verify_manifest(raw, rig["trust"])
    assert verification.ok, verification.reason
    decision = decide_update(
        verification.manifest,
        installed_version="0.7.0",
        channel="beta",
        platform_key="linux-x64",
        high_water=None,
        installer_available=True,
    )
    assert decision.reason is UpdateDecisionReason.UPDATE_AVAILABLE, decision.reason
    return raw, verification.manifest, decision, FakeServer(blob)


def _flow(rig, server, *, health_timeout: float) -> UpdateFlow:
    return UpdateFlow(
        data_dir=rig["data"],
        app_path=rig["target"],
        platform="linux-x64",
        installed_version="0.7.0",
        channel="beta",
        trust=rig["trust"],
        downloader=StagedDownloader(fetch=server.open),
        coordinator=WorkCoordinator(),
        migration_catalog=MigrationCatalog(),
        helper=LinuxProcessHelper(app_path=rig["target"], pid_file=rig["data"] / "vool_api.pid"),
        health_probe=HttpHealthProbe(f"http://127.0.0.1:{rig['port']}/healthz").probe,
        installer=LinuxSourceInstaller(runtime_home=rig["home"]),
        health_timeout=health_timeout,
    )


def _health_version(rig, *, timeout: float = 10.0) -> str:
    """What the app seat currently serves on /healthz (its reported identity)."""
    deadline = time.monotonic() + timeout
    last = "never probed"
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{rig['port']}/healthz", timeout=2) as response:
                return json.loads(response.read())["runtime"]["app_version"]
        except Exception as exc:
            last = str(exc)
            time.sleep(0.25)
    raise AssertionError(f"the app never answered /healthz: {last}")


def _seed_rows(rig) -> list:
    with sqlite3.connect(rig["data"] / "seed.db") as db:
        return db.execute("SELECT * FROM seed").fetchall()


def _app_seat_pids(rig) -> set[int]:
    out = subprocess.run(["pgrep", "-f", str(rig["target"])], capture_output=True, text=True, check=False)
    return {int(token) for token in out.stdout.split()}


def test_fast_healthy_new_version_completes(rig, monkeypatch):
    """Rig sanity: a new version that boots inside the health window installs and
    finalizes through the real flow. This must hold on any tree — if it does not,
    the rig is broken, not the verdict under test."""
    _shrink_start_bound(monkeypatch, 30.0)
    raw, manifest, decision, server = _offer(rig, {"start_sleep": 0})

    result = _flow(rig, server, health_timeout=3.0).run(raw, manifest, decision)

    assert result.ok, result.detail
    assert result.terminal is Step.FINALIZED
    assert (rig["target"] / "core/version").read_text() == "0.7.1"
    assert _health_version(rig) == "0.7.1"


def test_slow_but_healthy_new_version_completes(rig, monkeypatch):
    """A slow machine must not lose a GOOD update: the new version boots slower
    than the health window, then serves the right identity. A verdict measured
    from launch time rolls it back before it ever ran; the verdict must wait for
    the new version's own start."""
    _shrink_start_bound(monkeypatch, 30.0)
    raw, manifest, decision, server = _offer(rig, {"start_sleep": 5})

    result = _flow(rig, server, health_timeout=3.0).run(raw, manifest, decision)

    assert result.ok, result.detail
    assert result.terminal is Step.FINALIZED
    assert not result.rolled_back
    assert (rig["target"] / "core/version").read_text() == "0.7.1"
    assert _health_version(rig) == "0.7.1"


def test_slow_broken_new_version_rolled_back_only_after_it_ran(rig, monkeypatch):
    """A slow BROKEN new version (mutates data, exits 7) is rolled back only after
    it actually ran: its mutation proof must exist. A launch-clock verdict fires
    while it is still booting and rolls back a process that never executed, so
    the proof never appears and nothing was actually tested."""
    _shrink_start_bound(monkeypatch, 30.0)
    raw, manifest, decision, server = _offer(rig, {"start_sleep": 8, "broken": True})
    (rig["home"] / "update-trigger").touch()

    result = _flow(rig, server, health_timeout=3.0).run(raw, manifest, decision)

    assert result.rolled_back
    proof = rig["home"] / "update-mutation-proof"
    assert proof.exists() and proof.read_text() == "new process actually mutated current data"
    assert _seed_rows(rig) == [("retained",)]
    assert (rig["target"] / "core/version").read_text() == "0.7.0"
    assert _health_version(rig) == "0.7.0"
    # no leftover new-version process: only the restored prior version holds the seat
    assert _app_seat_pids(rig) == {int((rig["data"] / "vool_api.pid").read_text())}


def test_never_up_new_version_stays_bounded(rig, monkeypatch):
    """Fair start is bounded: a new version that never comes up and never exits is
    still failed within its (shrunk) start allowance, and the machine is put back
    on the previous version."""
    _shrink_start_bound(monkeypatch, 2.0)
    raw, manifest, decision, server = _offer(rig, {"start_sleep": 60})

    started = time.monotonic()
    result = _flow(rig, server, health_timeout=3.0).run(raw, manifest, decision)
    elapsed = time.monotonic() - started

    assert result.rolled_back
    assert elapsed < 30
    assert (rig["target"] / "core/version").read_text() == "0.7.0"
    assert _health_version(rig) == "0.7.0"


def test_up_but_silent_new_version_health_window_from_up(rig, monkeypatch):
    """The health window is measured from the moment the new version is actually
    up (it took the app's pidfile seat), not from launch: up-but-silent is failed
    after one health window, well inside the start allowance."""
    _shrink_start_bound(monkeypatch, 30.0)
    raw, manifest, decision, server = _offer(rig, {"silent_sleep": 600})

    started = time.monotonic()
    result = _flow(rig, server, health_timeout=2.0).run(raw, manifest, decision)
    elapsed = time.monotonic() - started

    assert result.rolled_back
    assert elapsed < 30
    assert (rig["target"] / "core/version").read_text() == "0.7.0"
    assert _health_version(rig) == "0.7.0"
