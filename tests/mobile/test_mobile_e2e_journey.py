"""Headless end-to-end journey: a REAL daemon + the REAL shared phone code.

Launches apps.vool_api_server on a scratch VOOL_HOME and a free port, waits
for /healthz, then runs mobile/e2e/journey.mjs (Node) — the exact TypeScript
modules the iOS/Android app ships — driving pairing, chat, photo attachment,
approval, notification, Proof Chip, and the replay/revocation sabotages over
HTTP. Sabotage of an EXPIRED pairing code is covered with a real clock in
tests/mobile/test_mobile_companion_api.py (time-travelled module clock);
replayed and revoked are proven here against the live wire.

Marked e2e: skipped unless node is on PATH. Evidence (journey log) lands in
tests/mobile/evidence/ for the evidence commit.
"""
from __future__ import annotations

import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
JOURNEY = REPO_ROOT / "mobile" / "e2e" / "journey.mjs"
EVIDENCE_DIR = Path(__file__).resolve().parent / "evidence"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _wait_health(port: int, timeout: float = 180.0) -> dict:
    deadline = time.time() + timeout
    last = ""
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as resp:
                import json

                return json.loads(resp.read().decode())
        except Exception as exc:
            last = str(exc)
            time.sleep(1.0)
    raise RuntimeError(f"daemon never became healthy: {last}")


def test_headless_pairing_chat_approval_journey(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    python = sys.executable
    port = _free_port()
    home = tmp_path / "vool-home"
    home.mkdir()

    env = dict(os.environ)
    env.update({
        "VOOL_HOME": str(home),
        # Keep the scratch daemon off anything the operator runs:
        "VOOL_ALLOWED_HOSTS": "127.0.0.1,localhost",
    })
    daemon = subprocess.Popen(
        [python, "-m", "apps.vool_api_server", "--port", str(port), "--bind", "127.0.0.1"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        health = _wait_health(port)
        assert health.get("ok") is True, health
        journey_env = dict(os.environ)
        journey_env.update({
            "VOOL_BASE_URL": f"http://127.0.0.1:{port}",
            "VOOL_HOME": str(home),
            "VOOL_PYTHON": python,
        })
        result = subprocess.run(
            [node, str(JOURNEY)],
            cwd=str(REPO_ROOT / "mobile"),
            env=journey_env,
            capture_output=True,
            text=True,
            timeout=420,
        )
        EVIDENCE_DIR.mkdir(exist_ok=True)
        (EVIDENCE_DIR / "journey.log").write_text(
            f"# healthz: {health}\n# exit: {result.returncode}\n{result.stdout}\n{result.stderr}"
        )
        assert result.returncode == 0, (
            f"journey failed (exit {result.returncode}):\n{result.stdout}\n{result.stderr}"
        )
        assert "[journey] COMPLETE" in result.stdout
    finally:
        daemon.terminate()
        try:
            daemon.wait(timeout=15)
        except subprocess.TimeoutExpired:
            daemon.kill()
            daemon.wait(timeout=10)
