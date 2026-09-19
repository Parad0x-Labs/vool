"""Sandbox journey environment: a REAL daemon + a sandbox VOOL app clone + a local
signed release feed, for driving the updater from the real chat UI in a browser.

 NEVER touches the operator's installed app: the app being updated is a clone under
 the sandbox root, and the daemon runs from THIS worktree against a scratch home.

Usage:
    python tests/updater/sandbox_journey.py setup <sandbox_root> [--port 11880] [--sick]
      → writes <sandbox_root>/journey.json (ports + paths), starts the feed server,
        the sandbox app, and the daemon; blocks until killed.
    The rollback scenario: --sick makes the feed's /healthz report the new version
    as unhealthy, so the restart press ends in Rolled back.

The browser drives: http://127.0.0.1:<port>/chat
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "tests"))

from tests.updater.helpers import generate_publisher_keypair, sha256_hex, sign_bytes
from tests.updater.test_e2e_sandbox import SandboxServer, _make_app, _spawn_app

HELPER = REPO / "installer" / "update" / "mac_update_helper.sh"
PYTHON = sys.executable


def _wait_health(url: str, timeout: float = 60.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=2.0) as response:
                if 200 <= response.status < 300:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("setup",))
    parser.add_argument("root", nargs="?", default="")
    parser.add_argument("--port", type=int, default=11880)
    parser.add_argument("--sick", action="store_true", help="new version reports unhealthy → rollback journey")
    args = parser.parse_args()

    root = Path(args.root or tempfile.mkdtemp(prefix="vool-update-journey-"))
    root.mkdir(parents=True, exist_ok=True)
    apps = root / "Apps"
    apps.mkdir(exist_ok=True)
    app_path = _make_app(apps, "VOOL.app", "old")
    heartbeat = root / "heartbeat.log"
    running = _spawn_app(app_path, heartbeat, "0.5.0")
    pidfile = root / "app.pid"
    pidfile.write_text(str(running.pid))

    private_hex, public_hex = generate_publisher_keypair()
    pin_file = root / "trusted_publisher_keys.json"
    pin_file.write_text(json.dumps({"release-2026-01": public_hex}, indent=2))

    new_bundle = _make_app(root / "zsrc", "VOOL.app", "new")
    artifact = root / "artifact.zip"
    with zipfile.ZipFile(artifact, "w") as archive:
        for file in sorted(new_bundle.rglob("*")):
            if file.is_file():
                archive.write(file, f"VOOL.app/{file.relative_to(new_bundle)}")
    blob = artifact.read_bytes()

    server = SandboxServer(app_path=app_path, installed_version="0.5.0", target_version="0.6.0")
    server.start()
    manifest = {
        "schema": "vool.update.manifest.v1",
        "product": "vool",
        "channel": "stable",
        "version": "0.6.0",
        "sequence": 47,
        "published_at": "2026-09-01T12:00:00Z",
        "minimum_compatible": "0.4.0",
        "notes": "Sandbox journey release\n- Signed manifest check\n- One-press download, one-press restart\n- Automatic rollback if unhealthy",
        "artifacts": {
            "macos-arm64": {
                "url": f"{server.url}/artifact.zip",
                "size": len(blob),
                "sha256": sha256_hex(blob),
                "signature": sign_bytes(private_hex, blob),
            }
        },
    }
    manifest["signature"] = {
        "key_id": "release-2026-01",
        "sig": sign_bytes(
            private_hex,
            json.dumps({k: v for k, v in manifest.items() if k != "signature"}, sort_keys=True, separators=(",", ":")).encode(),
        ),
    }
    server.manifest_bytes = json.dumps(manifest).encode()
    server.artifact_blob = blob
    server.health_sick = bool(args.sick)

    home = root / "vool-home"
    (home / "data" / "config").mkdir(parents=True, exist_ok=True)
    (home / "data" / "config" / "settings.json").write_text(json.dumps({"setting": "original"}))
    # HARD STOP for local-model prewarm (24GB-host rule): the journey daemon must not
    # cold-load any local model — the updater journey needs no model lane.
    (home / "data" / "config" / "local_models_disabled").write_text("sandbox journey\n")

    env = dict(os.environ)
    env.update(
        {
            "VOOL_HOME": str(home),
            "VOOL_UPDATE_MANIFEST_URL": f"{server.url}/manifest.json",
            "VOOL_UPDATE_APP_PATH": str(app_path),
            "VOOL_UPDATE_HEALTH_URL": f"{server.url}/healthz",
            "VOOL_UPDATE_HELPER_SCRIPT": str(HELPER),
            "VOOL_UPDATE_TRUSTED_KEYS_JSON": str(pin_file),
            "VOOL_UPDATE_TARGET_PIDFILE": str(pidfile),
            "VOOL_UPDATE_SANDBOX_WAIVER": "1",
            "PYTHONUNBUFFERED": "1",
        }
    )
    daemon_log = open(root / "daemon.log", "wb")
    daemon = subprocess.Popen(
        [PYTHON, "-m", "apps.vool_api_server", "--port", str(args.port)],
        cwd=str(REPO),
        env=env,
        stdout=daemon_log,
        stderr=subprocess.STDOUT,
    )

    healthy = _wait_health(f"http://127.0.0.1:{args.port}/healthz", timeout=90.0)
    state = {
        "root": str(root),
        "daemon_port": args.port,
        "daemon_pid": daemon.pid,
        "app_path": str(app_path),
        "app_pid": running.pid,
        "feed_port": server.port,
        "sick": bool(args.sick),
        "daemon_healthy": healthy,
        "chat_url": f"http://127.0.0.1:{args.port}/chat",
        "status_url": f"http://127.0.0.1:{args.port}/api/update/status",
    }
    (root / "journey.json").write_text(json.dumps(state, indent=2))
    print(json.dumps(state, indent=2), flush=True)
    if not healthy:
        daemon.terminate()
        server.shutdown_server()
        return 1

    def _stop(*_):
        daemon.terminate()
        server.shutdown_server()
        if running.poll() is None:
            running.terminate()
        # Kill EVERY app this journey started or relaunched (helper launches go
        # through LaunchServices and re-parent to launchd — they never die on
        # their own within the session).
        from tests.updater.test_e2e_sandbox import sweep_sandbox_apps

        time.sleep(0.3)
        sweep_sandbox_apps(root)
        raise SystemExit(0)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    while True:
        if daemon.poll() is not None:
            server.shutdown_server()
            return daemon.returncode or 0
        time.sleep(1)


if __name__ == "__main__":
    raise SystemExit(main())
