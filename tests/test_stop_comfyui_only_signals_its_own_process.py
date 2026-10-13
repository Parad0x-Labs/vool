"""stop_comfyui signals only the ComfyUI process VOOL started, never a process found by name.

Before: it ran `pgrep -f "main.py --port 8188"` and sent SIGTERM to every match, so any process
whose command line contained that text (another app's main.py, a user's own ComfyUI) was killed.
Real processes throughout: a decoy that matches the old pattern, and a service started through the
real ensure_service path whose process group must be stopped as a whole.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

import core.local_service_autostart as autostart
import core.resource_governor as gov

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process groups")


def _wait_dead(proc: subprocess.Popen, timeout: float = 5.0) -> bool:
    try:
        proc.wait(timeout=timeout)
        return True
    except subprocess.TimeoutExpired:
        return False


def _pid_alive(pid: int) -> bool:
    import os

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


@pytest.fixture
def clean_registry(monkeypatch):
    monkeypatch.setattr(autostart, "_SPAWNED", {}, raising=False)
    yield


def _decoy() -> subprocess.Popen:
    # Command line contains exactly what the old pgrep pattern looked for.
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)", "main.py", "--port", gov._COMFYUI_PORT],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )


def test_a_process_vool_did_not_start_is_never_signaled(clean_registry) -> None:
    decoy = _decoy()
    try:
        time.sleep(0.3)
        assert gov.stop_comfyui() is False
        time.sleep(0.3)
        assert decoy.poll() is None, "a process VOOL did not start was killed"
    finally:
        decoy.kill()
        decoy.wait(5)


def test_the_started_service_and_its_children_stop_and_nothing_else(clean_registry, tmp_path: Path) -> None:
    ready = tmp_path / "ready"
    grandchild_pid = tmp_path / "grandchild.pid"
    script = (
        "import subprocess, sys, time, pathlib\n"
        "g = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        f"pathlib.Path({str(grandchild_pid)!r}).write_text(str(g.pid))\n"
        f"pathlib.Path({str(ready)!r}).write_text('up')\n"
        "time.sleep(60)\n"
    )
    svc = autostart.LocalService(
        name="ComfyUI",
        reachable=ready.exists,
        argv=[sys.executable, "-c", script, "main.py", "--port", gov._COMFYUI_PORT],
        ready_timeout=15.0,
        poll=0.1,
    )
    decoy = _decoy()
    try:
        ok, msg = autostart.ensure_service(svc)
        assert ok, msg
        started = autostart._SPAWNED["ComfyUI"]
        gpid = int(grandchild_pid.read_text())

        assert gov.stop_comfyui() is True
        assert _wait_dead(started), "the recorded ComfyUI process did not stop"
        deadline = time.monotonic() + 5.0
        while _pid_alive(gpid) and time.monotonic() < deadline:
            time.sleep(0.1)
        assert not _pid_alive(gpid), "a child in the service's own process group survived"
        assert decoy.poll() is None, "a matching process outside the service was killed"

        # Once it has exited, its PID is never signaled again (it may belong to another process now).
        assert gov.stop_comfyui() is False
        assert "ComfyUI" not in autostart._SPAWNED
    finally:
        decoy.kill()
        decoy.wait(5)
        started = autostart._SPAWNED.get("ComfyUI")
        if started is not None and started.poll() is None:
            started.kill()
