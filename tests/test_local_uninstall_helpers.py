"""The local uninstall helpers prove a process is ours before they stop it."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / "installer/uninstall_local_runtime.py"
spec = importlib.util.spec_from_file_location("local_uninstall_runtime_under_test", MODULE)
runtime = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runtime)

SLEEP_60 = "import time; time.sleep(60)"
SERVER_MARKER = "-m apps.vool_api_server"
LLAMACPP_MARKER = "-m llama_cpp.server"

# A stand-in server that traps SIGTERM and keeps sleeping: only SIGKILL ends it.
TERM_IGNORER = '''\
import signal
import time

signal.signal(signal.SIGTERM, signal.SIG_IGN)
while True:
    time.sleep(1)
'''


def pidfile_path(runtime_home: Path) -> Path:
    return runtime_home / "data" / "vool_api.pid"


def write_pidfile(runtime_home: Path, pid_text: str) -> Path:
    pidfile = pidfile_path(runtime_home)
    pidfile.parent.mkdir(parents=True, exist_ok=True)
    pidfile.write_text(pid_text, encoding="utf-8")
    return runtime_home


def start_sleeper(cwd: Path, *extra_args: str) -> subprocess.Popen:
    # The "-c" program ignores the rest of its arguments, so "-m
    # apps.vool_api_server" rides along in the command line (visible to ps)
    # while the process itself is only a plain sleeper.
    return subprocess.Popen(
        [sys.executable, "-c", SLEEP_60, *extra_args],
        cwd=str(cwd),
    )


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=10)


def wait_until_ps_shows(process: subprocess.Popen, needle: str, timeout: float = 5.0) -> None:
    # The child must have exec'd, so ps shows the stand-in's arguments and its
    # own working folder, not the test runner's.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        listing = subprocess.run(["ps", "-ww", "-p", str(process.pid), "-o", "args="],
                                 capture_output=True, text=True, check=False)
        if needle in listing.stdout:
            return
        time.sleep(0.1)
    raise AssertionError(f"stand-in {process.pid} never showed {needle!r} in its command line")


def assert_still_running(process: subprocess.Popen) -> None:
    time.sleep(0.3)
    assert process.poll() is None


@pytest.fixture
def install_root(tmp_path):
    root = tmp_path / "vool-source-install"
    root.mkdir()
    return root


@pytest.fixture
def runtime_home(tmp_path):
    home = tmp_path / "vool-runtime-home"
    home.mkdir()
    return home


@pytest.fixture
def models_dir(tmp_path):
    models = tmp_path / "llamacpp-models"
    models.mkdir()
    (models / "fake.gguf").write_bytes(b"not really a model, only a named file")
    return models


# --- owned_server_pid -----------------------------------------------------------


def test_owned_server_pid_is_zero_without_a_pidfile(runtime_home, install_root):
    assert runtime.owned_server_pid(runtime_home, install_root) == 0


def test_owned_server_pid_is_zero_for_empty_or_garbage_pidfile_text(runtime_home, install_root):
    assert runtime.owned_server_pid(write_pidfile(runtime_home, ""), install_root) == 0
    assert runtime.owned_server_pid(write_pidfile(runtime_home, "   \n"), install_root) == 0
    assert runtime.owned_server_pid(write_pidfile(runtime_home, "not-a-pid"), install_root) == 0


def test_owned_server_pid_is_zero_for_an_already_exited_pid(tmp_path, runtime_home, install_root):
    process = subprocess.Popen([sys.executable, "-c", SLEEP_60], cwd=str(tmp_path))
    wait_until_ps_shows(process, "time.sleep")
    stop_process(process)  # leaves a pidfile naming a pid that is now gone
    assert runtime.owned_server_pid(write_pidfile(runtime_home, str(process.pid)), install_root) == 0


def test_owned_server_pid_is_zero_when_the_command_line_is_not_vools_server(runtime_home, install_root):
    # Live pid, right working folder, but the command line never names the server.
    process = start_sleeper(install_root)
    wait_until_ps_shows(process, "time.sleep")
    try:
        assert runtime.owned_server_pid(write_pidfile(runtime_home, str(process.pid)), install_root) == 0
        assert_still_running(process)
    finally:
        stop_process(process)


def test_owned_server_pid_is_zero_when_the_working_folder_is_not_the_install_root(tmp_path, runtime_home, install_root):
    elsewhere = tmp_path / "somewhere-else"
    elsewhere.mkdir()
    process = start_sleeper(elsewhere, *SERVER_MARKER.split())
    wait_until_ps_shows(process, runtime.SERVER_ARG)
    try:
        assert runtime.owned_server_pid(write_pidfile(runtime_home, str(process.pid)), install_root) == 0
        assert_still_running(process)  # never signalled
    finally:
        stop_process(process)


def test_owned_server_pid_returns_the_pid_when_the_server_runs_from_the_install_root(runtime_home, install_root):
    process = start_sleeper(install_root, *SERVER_MARKER.split())
    wait_until_ps_shows(process, runtime.SERVER_ARG)
    try:
        assert runtime.owned_server_pid(write_pidfile(runtime_home, str(process.pid)), install_root) == process.pid
        assert_still_running(process)  # inspection only, nothing stopped yet
    finally:
        stop_process(process)


def test_owned_server_pid_reads_a_whole_command_line_longer_than_ps_default_width(tmp_path, runtime_home):
    # A checkout path with repeated segments crosses ps's default width; the
    # server marker at the end of the line must still be seen (ps -ww rule).
    segment = "vool-source-checkout-segment-"
    install_root = tmp_path
    while len(str(install_root)) <= 220:
        install_root = install_root / segment
    install_root.mkdir(parents=True)
    assert len(str(install_root)) > 200
    process = start_sleeper(install_root, *SERVER_MARKER.split())
    wait_until_ps_shows(process, runtime.SERVER_ARG)
    try:
        assert runtime.owned_server_pid(write_pidfile(runtime_home, str(process.pid)), install_root) == process.pid
    finally:
        stop_process(process)


def test_owned_server_pid_never_targets_the_uninstaller_process_itself(runtime_home, install_root):
    assert runtime.owned_server_pid(write_pidfile(runtime_home, str(os.getpid())), install_root) == 0
    assert runtime.owned_server_pid(write_pidfile(runtime_home, "1"), install_root) == 0


def test_owned_server_pid_fails_closed_when_the_process_cannot_be_inspected(runtime_home, install_root, monkeypatch):
    process = start_sleeper(install_root, *SERVER_MARKER.split())
    wait_until_ps_shows(process, runtime.SERVER_ARG)
    write_pidfile(runtime_home, str(process.pid))

    def unreachable(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("ps went away")

    monkeypatch.setattr(runtime.subprocess, "run", unreachable)
    try:
        with pytest.raises(OSError, match="could not check whether VOOL is running"):
            runtime.owned_server_pid(runtime_home, install_root)
        assert_still_running(process)
    finally:
        monkeypatch.undo()
        stop_process(process)


def test_owned_server_pid_fails_closed_when_the_working_folder_cannot_be_proven(runtime_home, install_root, monkeypatch):
    process = start_sleeper(install_root, *SERVER_MARKER.split())
    wait_until_ps_shows(process, runtime.SERVER_ARG)
    write_pidfile(runtime_home, str(process.pid))
    monkeypatch.setattr(runtime, "process_cwd", lambda _pid: None)
    try:
        with pytest.raises(OSError, match="could not prove the running VOOL belongs to this install"):
            runtime.owned_server_pid(runtime_home, install_root)
        assert_still_running(process)
    finally:
        monkeypatch.undo()
        stop_process(process)


# --- owned_llamacpp_pids --------------------------------------------------------


def test_owned_llamacpp_pids_includes_a_lane_started_from_this_install(install_root, models_dir):
    model_file = (models_dir / "fake.gguf").resolve()
    process = start_sleeper(install_root, *LLAMACPP_MARKER.split(), "--model", str(model_file))
    wait_until_ps_shows(process, runtime.LLAMACPP_MODULE)
    try:
        assert process.pid in runtime.owned_llamacpp_pids(install_root, models_dir)
    finally:
        stop_process(process)


def test_owned_llamacpp_pids_ignores_a_lane_running_from_elsewhere(tmp_path, install_root, models_dir):
    elsewhere = tmp_path / "someone-elses-folder"
    elsewhere.mkdir()
    assert not Path(sys.executable).resolve().is_relative_to((install_root / ".venv/bin").resolve())
    model_file = (models_dir / "fake.gguf").resolve()
    process = start_sleeper(elsewhere, *LLAMACPP_MARKER.split(), "--model", str(model_file))
    wait_until_ps_shows(process, runtime.LLAMACPP_MODULE)
    try:
        assert process.pid not in runtime.owned_llamacpp_pids(install_root, models_dir)
        assert_still_running(process)  # never signalled
    finally:
        stop_process(process)


def test_owned_llamacpp_pids_ignores_a_lane_serving_a_foreign_model(tmp_path, install_root, models_dir):
    foreign_models = tmp_path / "foreign-models"
    foreign_models.mkdir()
    foreign_model = (foreign_models / "other.gguf").resolve()
    foreign_model.write_bytes(b"a model that is not this runtime's")
    process = start_sleeper(install_root, *LLAMACPP_MARKER.split(), "--model", str(foreign_model))
    wait_until_ps_shows(process, runtime.LLAMACPP_MODULE)
    try:
        assert process.pid not in runtime.owned_llamacpp_pids(install_root, models_dir)
        assert_still_running(process)
    finally:
        stop_process(process)


# --- stop_runtime ---------------------------------------------------------------


def test_stop_runtime_succeeds_when_nothing_is_running(runtime_home, install_root, models_dir):
    assert runtime.stop_runtime(runtime_home, install_root, models_dir,
                                term_wait=1.0, kill_wait=1.0) == (True, None)


def test_stop_runtime_stops_the_owned_server_and_waits_for_exit(runtime_home, install_root, models_dir):
    process = start_sleeper(install_root, *SERVER_MARKER.split())
    wait_until_ps_shows(process, runtime.SERVER_ARG)
    write_pidfile(runtime_home, str(process.pid))
    try:
        assert runtime.stop_runtime(runtime_home, install_root, models_dir,
                                    term_wait=2.0, kill_wait=2.0) == (True, None)
        assert process.poll() is not None
    finally:
        stop_process(process)


def test_stop_runtime_refuses_and_leaves_the_server_running_when_signalling_is_denied(
        runtime_home, install_root, models_dir, monkeypatch):
    process = start_sleeper(install_root, *SERVER_MARKER.split())
    wait_until_ps_shows(process, runtime.SERVER_ARG)
    write_pidfile(runtime_home, str(process.pid))

    def refused(*_args: object, **_kwargs: object) -> None:
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(runtime.os, "kill", refused)
    try:
        assert runtime.stop_runtime(runtime_home, install_root, models_dir,
                                    term_wait=1.0, kill_wait=1.0) == (False, runtime.CANNOT_STOP_MESSAGE)
        assert_still_running(process)  # denied signalling means alive, never "stopped"
    finally:
        monkeypatch.undo()
        stop_process(process)


def test_stop_runtime_escalates_to_kill_when_sigterm_is_ignored(tmp_path, runtime_home, install_root, models_dir):
    script = tmp_path / "term_ignorer.py"
    script.write_text(TERM_IGNORER, encoding="utf-8")
    process = subprocess.Popen(
        [sys.executable, str(script), *SERVER_MARKER.split()],
        cwd=str(install_root),
    )
    wait_until_ps_shows(process, runtime.SERVER_ARG)
    write_pidfile(runtime_home, str(process.pid))
    try:
        assert runtime.stop_runtime(runtime_home, install_root, models_dir,
                                    term_wait=0.2, kill_wait=3.0) == (True, None)
        assert process.poll() is not None
    finally:
        stop_process(process)


# --- main -----------------------------------------------------------------------


def test_main_returns_zero_when_nothing_ours_is_running(runtime_home, install_root, models_dir):
    assert runtime.main(["stop", "--runtime-home", str(runtime_home), "--install-root", str(install_root),
                         "--llamacpp-models-dir", str(models_dir)]) == 0


def test_main_returns_three_and_prints_the_refusal_when_stopping_is_refused(
        runtime_home, install_root, models_dir, monkeypatch, capsys):
    monkeypatch.setattr(runtime, "stop_runtime",
                        lambda *_args, **_kwargs: (False, runtime.CANNOT_STOP_MESSAGE))
    assert runtime.main(["stop", "--runtime-home", str(runtime_home), "--install-root", str(install_root),
                         "--llamacpp-models-dir", str(models_dir)]) == 3
    assert runtime.CANNOT_STOP_MESSAGE in capsys.readouterr().out


def test_main_returns_three_when_the_running_check_fails(runtime_home, install_root, models_dir, monkeypatch):
    def broken(*_args: object, **_kwargs: object) -> None:
        raise OSError("could not check whether VOOL is running")

    monkeypatch.setattr(runtime, "stop_runtime", broken)
    assert runtime.main(["stop", "--runtime-home", str(runtime_home), "--install-root", str(install_root),
                         "--llamacpp-models-dir", str(models_dir)]) == 3
