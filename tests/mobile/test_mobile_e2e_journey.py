"""Headless end-to-end journey: a REAL daemon + the REAL shared phone code.

Launches apps.vool_api_server on a scratch VOOL_HOME and a free port, waits
for /healthz, then runs mobile/e2e/journey.mjs (Node) — the exact TypeScript
modules the iOS/Android app ships — driving pairing, chat, photo attachment,
approval, notification, Proof Chip, and the replay/revocation sabotages over
HTTP. Sabotage of an EXPIRED pairing code is covered with a real clock in
tests/mobile/test_mobile_companion_api.py (time-travelled module clock);
replayed and revoked are proven here against the live wire.

The journey's needs-approval leg consumes two pending approvals minted through
the product's own permission gate BEFORE the daemon boots: the daemon's
pending-approval restore runs once per process (restart recovery, not a live
file watch), so an approval written after the daemon already read the store
would be invisible to it. Seeding first makes the durable path deterministic.

Marked e2e: skipped unless node is on PATH. Evidence (journey log) lands under
the test's tmp_path, and in tests/mobile/evidence/ only when
VOOL_MOBILE_EVIDENCE_DIR opts in (the committed evidence copy is an owner
decision, not a test side effect).
"""
from __future__ import annotations

import json
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

#: The session the seeded approvals belong to (journey.mjs polls its feed).
SEEDED_SESSION = "openclaw:" + "e5" * 10

_SEED_PROGRAM = """
import json
import os
import sys
home, path = sys.argv[1], sys.argv[2]
os.environ["VOOL_HOME"] = home
from core.runtime_paths import configure_runtime_home
configure_runtime_home(home)
from core.mode_permission_policy import decide_tool_call, set_active_mode
session = "openclaw:" + "e5" * 10
set_active_mode(session, "manual", project_id="", client_turn_id="turn-e2e")
decision = decide_tool_call(
    intent="workspace.write_file",
    arguments={"path": path, "content": "from the phone journey"},
    task_id="turn-e2e",
    source_context={"runtime_session_id": session, "operating_mode": "manual", "workspace_root": "/tmp"},
)
assert decision.approval_request is not None, decision.effect
print(decision.approval_request["approval_id"])
"""


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


def _seed_pending_approval(python: str, home: Path, path: str) -> str:
    """Mint one REAL pending approval in a sibling process sharing the daemon's VOOL_HOME.

    Runs BEFORE the daemon boots, so the daemon's once-per-process pending-approval
    restore reads it from the durable store -- the same gate the product itself
    persists through, never a test-only door.
    """
    result = subprocess.run(
        [python, "-c", _SEED_PROGRAM, str(home), path],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().strip('"')


def test_headless_pairing_chat_approval_journey(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not on PATH")
    python = sys.executable
    port = _free_port()
    home = tmp_path / "vool-home"
    home.mkdir()

    # Both approvals the journey decides (one allowed, one refused) exist in the
    # durable store before the daemon reads it once at first use.
    allow_id = _seed_pending_approval(python, home, "notes/journey.txt")
    deny_id = _seed_pending_approval(python, home, "notes/refused-by-phone.txt")

    env = dict(os.environ)
    env.update({
        "VOOL_HOME": str(home),
        # Keep the scratch daemon off anything the operator runs:
        "VOOL_ALLOWED_HOSTS": "127.0.0.1,localhost",
    })
    daemon_log = tmp_path / "daemon.log"
    daemon_log_handle = open(daemon_log, "wb")
    daemon = subprocess.Popen(
        [python, "-m", "apps.vool_api_server", "--port", str(port), "--bind", "127.0.0.1"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=daemon_log_handle,
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
            "VOOL_JOURNEY_APPROVAL_IDS": json.dumps({"allow": allow_id, "deny": deny_id}),
        })
        result = subprocess.run(
            [node, str(JOURNEY)],
            cwd=str(REPO_ROOT / "mobile"),
            env=journey_env,
            capture_output=True,
            text=True,
            timeout=420,
        )
        evidence = f"# healthz: {health}\n# exit: {result.returncode}\n{result.stdout}\n{result.stderr}"
        (tmp_path / "journey.log").write_text(evidence)
        opt_in = os.environ.get("VOOL_MOBILE_EVIDENCE_DIR", "").strip()
        if opt_in:
            target = Path(opt_in)
            target.mkdir(parents=True, exist_ok=True)
            (target / "journey.log").write_text(evidence)
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
        daemon_log_handle.close()
