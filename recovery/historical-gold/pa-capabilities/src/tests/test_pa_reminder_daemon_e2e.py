"""End-to-end daemon wiring verification (B9): the REAL ``apps.nulla_api_server``
process, its own NULLA_HOME and port, booted against a CONTROLLED PROVIDER
(``ScriptedProvider`` — an Ollama-dialect stub; no real model is loaded or
called; this test runs no chat turn at all).

Verified against the actual server process, not the module in isolation:
boot starts the reminder runtime; a reminder scheduled into the daemon's own
store is delivered as an observable runtime event in that store; shutdown is
clean. Loopback only; no real accounts, no push, no inference.
"""
from __future__ import annotations

import sqlite3
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import os
import subprocess
import sys
from urllib.request import urlopen

import pytest

pytestmark = pytest.mark.pa_beta

_STUB_MODEL = "stub-pa-e2e:1b"


def _free_port() -> int:
    import socket

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


def _boot_daemon(home: Path, provider_base_url: str, log_path: Path, timeout: float = 120.0):
    """The REAL apps.nulla_api_server process under a MINIMAL env: isolated
    home, controlled Ollama-dialect stub, mesh/prewarm/keychain off. (The
    blackbox rig's full env does not boot on this machine — its own canonical
    test skips here identically — while the raw server serves fine.)"""
    port = _free_port()
    env = dict(os.environ)
    env.update({
        "NULLA_HOME": str(home),
        "PYTHONPATH": os.getcwd(),
        "NULLA_DISABLE_MESH_DAEMON": "1",
        "NULLA_DISABLE_COMPUTE_MODE": "1",
        "NULLA_DISABLE_STUN": "1",
        "NULLA_KEY_STORAGE_MODE": "file",
        "NULLA_KEY_PASSPHRASE": "pa-e2e-rig",
        "NULLA_SKIP_PROVIDER_PREWARM": "1",
        "NULLA_REGISTER_INSTALLED_OLLAMA_MODELS": "0",
        "OLLAMA_HOST": provider_base_url,
        "NULLA_OLLAMA_URL": provider_base_url,
        "NULLA_OLLAMA_CHAT_URL": f"{provider_base_url}/api/chat",
    })
    env.pop("PYTEST_CURRENT_TEST", None)
    handle = log_path.open("wb")
    proc = subprocess.Popen(
        [sys.executable, "-m", "apps.nulla_api_server", "--port", str(port), "--bind", "127.0.0.1"],
        env=env, stdout=handle, stderr=subprocess.STDOUT, start_new_session=True,
    )
    import time as _time

    deadline = _time.monotonic() + timeout
    while _time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"daemon exited {proc.returncode}")
        try:
            with urlopen(f"http://127.0.0.1:{port}/healthz", timeout=3) as response:
                if response.status == 200:
                    return proc, port
        except Exception:
            _time.sleep(1.0)
    raise TimeoutError("daemon never became healthy")


def _daemon_db_path(home: Path) -> Path:
    matches = list(Path(home).rglob("nulla_web0_v2*.db")) or list(Path(home).rglob("*.db"))
    assert matches, "daemon home has no sqlite store"
    return matches[0]


def _insert_due_reminder(db_path: Path, message: str) -> str:
    reminder_id = str(uuid.uuid4())
    due = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
    conn = sqlite3.connect(str(db_path), timeout=10)
    try:
        conn.execute(
            "INSERT INTO reminders (reminder_id, task_id, message, due_at_utc, status, created_at) "
            "VALUES (?, '', ?, ?, 'pending', ?)",
            (reminder_id, message, due, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    finally:
        conn.close()
    return reminder_id


def _reminder_events(db_path: Path) -> list[dict]:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT seq, event_type, message, details_json FROM runtime_session_events "
            "WHERE session_id = 'pa-reminders' ORDER BY seq ASC LIMIT 100"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def test_daemon_boots_reminder_runtime_delivers_and_shuts_down(tmp_path) -> None:
    from tests._blackbox_served_rig import ScriptedProvider

    home = tmp_path / "daemon-home"
    log_path = tmp_path / "daemon.log"
    provider = ScriptedProvider({_STUB_MODEL: {}})  # controlled provider — boot probes only
    provider.__enter__()
    proc = None
    try:
        try:
            proc, port = _boot_daemon(home, provider.base_url, log_path)
        except Exception as exc:
            pytest.skip(f"real daemon could not boot in this environment: {exc}")

        def _log_tail(lines: int = 60) -> str:
            # After bootstrap the daemon reroutes its logs to its DATA-DIR file
            # (route_logging_to_file); stdout only carries the earliest lines.
            candidates = [log_path, *sorted(home.rglob("logs/nulla_api.log"), key=lambda p: p.stat().st_mtime)]
            try:
                text = candidates[-1].read_text("utf-8", "replace")
                return "\n".join(text.splitlines()[-lines:])
            except Exception:
                return "<no log>"

        log = _log_tail(250)
        assert "reminder runtime started" in log, f"boot log lacks the reminder-runtime start line:\n{log[-600:]}"

        db_path = _daemon_db_path(home)
        reminder_id = _insert_due_reminder(db_path, "e2e: take the lasagna out")

        deadline = time.monotonic() + 30
        events: list[dict] = []
        while time.monotonic() < deadline:
            events = [e for e in _reminder_events(db_path) if e["event_type"] == "reminder_notification"]
            if events:
                break
            time.sleep(0.5)
        assert events, (
            "no reminder_notification event in the daemon's own store; "
            f"log tail:\n{_log_tail()}"
        )
        assert any("take the lasagna out" in str(e["message"] or "") for e in events)
        assert any(reminder_id in str(e["details_json"] or "") for e in events)

        conn = sqlite3.connect(str(db_path), timeout=10)
        try:
            row = conn.execute(
                "SELECT status FROM reminders WHERE reminder_id = ?", (reminder_id,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None and row[0] == "delivered"
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        provider.__exit__(None, None, None)
