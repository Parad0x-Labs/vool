"""Agent teams are wired into the served app, not only importable.

The team commands were a group nobody registered, and the write gate was a hook the runtime tool door never
asked: every unit test passed against a fresh registry and a hand-built door while the served daemon offered
no `agents.*` command and refused every write-mode agent ("that hook is not installed"). These read the
served daemon's own command surface and the server process's own gate state.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.request import Request, urlopen

import pytest

from tests._blackbox_served_rig import ServedDaemon

REPO = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX served daemon")


def _get(base: str, path: str) -> dict:
    with urlopen(f"{base}{path}", timeout=60) as response:
        return json.loads(response.read().decode())


def _post(base: str, path: str, body: dict) -> tuple[int, dict]:
    request = Request(f"{base}{path}", method="POST", data=json.dumps(body).encode(),
                      headers={"Content-Type": "application/json"})
    with urlopen(request, timeout=60) as response:
        return response.status, json.loads(response.read().decode())


def test_the_served_daemon_offers_and_runs_the_team_commands(tmp_path):
    with ServedDaemon(tmp_path / "home") as daemon:
        discovered = json.dumps(_get(daemon.base_url, "/api/commands/chat"))
        for command_id in ("agents.start", "agents.status", "agents.stop"):
            assert command_id in discovered, command_id
        status, envelope = _post(daemon.base_url, "/api/commands/dispatch",
                                 {"command_id": "agents.status", "input": {"session_id": "openclaw:" + "c" * 20}})
        assert status == 200 and envelope.get("ok"), envelope


def test_the_server_process_has_the_write_gate_installed_at_its_tool_door():
    """Fresh interpreter, the server's own import graph: write-mode agents may start only where this is True."""
    probe = ("import apps.vool_api_server, core.agent_team.gate as gate; "
             "print('INSTALLED', gate.INSTALLED)")
    out = subprocess.run([sys.executable, "-c", probe], cwd=REPO, capture_output=True, text=True, timeout=300,
                         env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    assert "INSTALLED True" in out.stdout, (out.stdout[-500:], out.stderr[-2000:])
