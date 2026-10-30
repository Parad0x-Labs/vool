"""A running VOOL is recognised by its full command line, whatever terminal width ps would assume.

procps `ps` clips `args` to $COLUMNS. A launcher started from a terminal (or a CI runner) can pass
COLUMNS=80 down to VOOL, and a real install path is easily longer than that. When the updater read a
clipped line it stopped recognising its own app: it never sent the stop signal, reported "stopped" at
once and swapped code under a live process.
"""
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from core.updater.linux import LinuxBundleHelper, LinuxProcessHelper
from core.updater.macos import _pid_belongs_to_app
from core.updater.worker import worker_alive

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX process identity")

_SERVER = "import time\nwhile True:\n    time.sleep(.1)\n"


def _long_root(tmp_path: Path) -> Path:
    root = tmp_path / ("Installed Source With Spaces " * 4).strip()
    (root / "apps").mkdir(parents=True)
    (root / "apps/vool_api_server.py").write_text(_SERVER)
    (root / ".venv/bin").mkdir(parents=True)
    (root / ".venv/bin/python").symlink_to(sys.executable)
    return root


def _start(tmp_path: Path, command: list[str], cwd: Path) -> subprocess.Popen:
    process = subprocess.Popen(command, cwd=cwd, env={**os.environ, "PYTHONPATH": str(cwd)},
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    (tmp_path / "owned-pids.json").write_text(json.dumps([process.pid]))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:  # ps reports the interpreter's argv once exec has happened
        shown = subprocess.run(["ps", "-ww", "-p", str(process.pid), "-o", "args="],
                               capture_output=True, text=True, check=False).stdout
        if "apps.vool_api_server" in shown or "core.updater.worker" in shown:
            return process
        time.sleep(.05)
    process.kill()
    raise AssertionError("fixture process never started")


def _stop(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.send_signal(signal.SIGKILL)
    process.wait(timeout=10)


@pytest.fixture
def narrow_terminal(monkeypatch):
    monkeypatch.setenv("COLUMNS", "80")


def test_long_install_path_app_is_recognised_and_really_stopped(tmp_path, narrow_terminal):
    root = _long_root(tmp_path)
    assert len(str(root / ".venv/bin/python")) > 80
    process = _start(tmp_path, [str(root / ".venv/bin/python"), "-m", "apps.vool_api_server"], root)
    pidfile = tmp_path / "vool_api.pid"
    pidfile.write_text(str(process.pid))
    try:
        assert _pid_belongs_to_app(process.pid, root)
        helper = LinuxProcessHelper(app_path=root, pid_file=pidfile, poll_interval=.05)
        assert helper._read_pid() == process.pid
        helper.shutdown_app()
        assert helper.wait_until_stopped(timeout=10)
        # "Stopped" must mean the app is gone, never that it was not recognised.
        assert process.wait(timeout=5) == -signal.SIGTERM
    finally:
        _stop(process)


def test_package_version_server_is_recognised(tmp_path, narrow_terminal):
    app_root = tmp_path / ("VOOL package install " * 4).strip()
    version_app = app_root / "versions" / "0.7.6" / "app"
    (version_app / "apps").mkdir(parents=True)
    (version_app / "apps/vool_api_server.py").write_text(_SERVER)
    python = app_root / "versions" / "0.7.6" / "python/bin/python3"
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    process = _start(tmp_path, [str(python), "-m", "apps.vool_api_server"], version_app)
    pidfile = tmp_path / "vool_api.pid"
    pidfile.write_text(str(process.pid))
    try:
        helper = LinuxBundleHelper(app_path=app_root, pid_file=pidfile, poll_interval=.05)
        assert helper._read_pid() == process.pid
        helper.shutdown_app()
        assert helper.wait_until_stopped(timeout=10)
        assert process.wait(timeout=5) == -signal.SIGTERM
    finally:
        _stop(process)


def test_running_update_worker_with_long_plan_path_is_alive(tmp_path, narrow_terminal):
    data_dir = tmp_path / ("VOOL data folder " * 4).strip() / "data"
    job = data_dir / "update_v2" / "workers" / ("tx-" + "0" * 32)
    code = job / "code"
    (code / "core/updater").mkdir(parents=True)
    (code / "core/__init__.py").write_text("")
    (code / "core/updater/__init__.py").write_text("")
    (code / "core/updater/worker.py").write_text(_SERVER)
    plan = job / "plan.json"
    plan.write_text("{}")
    process = _start(tmp_path, [sys.executable, "-m", "core.updater.worker", str(plan)], code)
    lease = data_dir / "update_v2" / "worker.json"
    lease.write_text(json.dumps({"pid": process.pid, "plan": str(plan)}))
    try:
        assert worker_alive(data_dir)
    finally:
        _stop(process)
    assert not worker_alive(data_dir)
