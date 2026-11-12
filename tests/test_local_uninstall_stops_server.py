"""The Mac/source uninstall script stops a proven-ours VOOL before removing anything.

Runs installer/uninstall_vool_local.sh for real as a subprocess, entirely inside a
temp HOME: every path the script may touch is pinned by its env vars to the
test's temporary folders, so a real home folder and a real VOOL install are never
visible to it. A stand-in server is a plain sleep process whose command line
names the server (``python -c CODE -m apps.vool_api_server`` runs only CODE; the
rest is inert argv that ``ps -ww`` reports), which is exactly what the stop
helper's ownership proof reads: pidfile plus whole command line plus working
folder. No real server is started and no VOOL port is used.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "installer" / "uninstall_vool_local.sh"

SLEEP_CODE = "import time; time.sleep(120)"
SERVER_ARG = "apps.vool_api_server"
LLAMACPP_MODULE = "llama_cpp.server"
LLAMACPP_MODELS_NAME = "llamacpp-models"


@pytest.fixture
def home(tmp_path):
    home = (tmp_path / "home").resolve()
    home.mkdir()
    # A user folder that must survive every uninstall path untouched.
    decoy = home / "NULLA_VIDEO"
    decoy.mkdir()
    (decoy / "notes.txt").write_text("user content")
    return home


@pytest.fixture
def install_root(tmp_path):
    root = (tmp_path / "vool-local").resolve()
    root.mkdir()
    (root / "launch.py").write_text("# stand-in source install\n")
    (root / "README.txt").write_text("stand-in source install\n")
    return root


@pytest.fixture
def runtime_home(home):
    runtime = (home / ".vool_runtime").resolve()
    runtime.mkdir()
    return runtime


def path_stubs(tmp_path: Path) -> Path:
    """A bin dir prepended to PATH so removals stay inside the temp world.

    The platform ships a ``trash`` command that moves what it is given into the
    real home's Trash folder; a failing stub makes the script take its own
    documented ``rm -rf`` fallback instead, so no test file ever lands there.
    """
    stubs = tmp_path / "path-stubs"
    stubs.mkdir()
    trash = stubs / "trash"
    trash.write_text("#!/bin/sh\nexit 1\n")
    trash.chmod(0o755)
    return stubs


def script_env(home: Path, install_root: Path, runtime_home: Path, tmp_path: Path,
               prepend_path: Path | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.update(
        {
            "HOME": str(home),
            "VOOL_HOME": str(runtime_home),
            "NULLA_HOME": str(runtime_home),
            "VOOL_INSTALL_ROOT": str(install_root),
            "OPENCLAW_HOME": str(tmp_path / "openclaw-home"),
            "VOOL_OPENCLAW_AGENT_DIR": str(tmp_path / "openclaw-agent" / "vool"),
            "VOOL_LAUNCH_AGENT_PATH": str(tmp_path / "LaunchAgents" / "ai.vool.runtime.plist"),
            "OLLAMA_MODELS": str(tmp_path / "ollama-models"),
            "VOOL_LLAMACPP_MODELS_DIR": str((tmp_path / LLAMACPP_MODELS_NAME).resolve()),
        }
    )
    # PATH keeps the system directories (the script needs python3 and ps); only a
    # temp bin dir goes in front of it.
    first = path_stubs(tmp_path) if prepend_path is None else prepend_path
    env["PATH"] = str(first) + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin")
    return env


def run_uninstall(env: dict[str, str], *args: str, stdin: str | None = None):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=60,
    )


def start_stand_in(cwd: Path, *name_args: str) -> subprocess.Popen:
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    return subprocess.Popen(
        [str(Path(sys.executable).resolve()), "-c", SLEEP_CODE, *name_args],
        cwd=str(cwd),
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=10)


def wait_for_args(pid: int, needle: str, timeout: float = 5.0) -> None:
    # The command line must be in place (exec done) before the script inspects it.
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        listing = subprocess.run(
            ["ps", "-ww", "-p", str(pid), "-o", "args="],
            capture_output=True, text=True, check=False,
        )
        if listing.returncode == 0 and needle in listing.stdout:
            return
        time.sleep(0.1)
    raise AssertionError(f"stand-in process {pid} never showed {needle!r} on its command line")


def wait_until_exited(process: subprocess.Popen, timeout: float = 10.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process.poll() is not None:
            return
        time.sleep(0.1)
    raise AssertionError(f"stand-in process {process.pid} is still running")


def decoy_intact(home: Path) -> None:
    assert (home / "NULLA_VIDEO" / "notes.txt").read_text() == "user content"


def stop_running_server_and_uninstall(home: Path, install_root: Path, runtime_home: Path,
                                      tmp_path: Path) -> subprocess.CompletedProcess[str]:
    """Case shared by the plain and the long-path stops: a live proven-ours server."""
    data = runtime_home / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "saved-chat.txt").write_text("keep this chat")
    process = start_stand_in(install_root, "-m", SERVER_ARG)
    try:
        wait_for_args(process.pid, SERVER_ARG)
        (data / "vool_api.pid").write_text(str(process.pid))
        result = run_uninstall(script_env(home, install_root, runtime_home, tmp_path), "--yes")
        assert result.returncode == 0, result.stdout + result.stderr
        wait_until_exited(process)
    finally:
        stop_process(process)
    return result


def test_running_server_stopped_chats_kept_and_install_removed(home, install_root, runtime_home, tmp_path):
    result = stop_running_server_and_uninstall(home, install_root, runtime_home, tmp_path)
    assert not install_root.exists()
    assert runtime_home.is_dir()
    assert (runtime_home / "data" / "saved-chat.txt").read_text() == "keep this chat"
    decoy_intact(home)
    assert f"Your chats and settings were kept, in: {runtime_home}" in result.stdout
    assert "A fresh VOOL install will find them there again." in result.stdout
    assert "To delete them too, delete that folder, or run this uninstall again with --remove-data." in result.stdout


def test_process_not_proven_ours_left_running_while_uninstall_completes(home, install_root, runtime_home, tmp_path):
    # A live pidfile number whose command line names the server but whose working
    # folder is not this install: never signalled, and removal still finishes.
    elsewhere = (tmp_path / "not-our-cwd").resolve()
    elsewhere.mkdir()
    data = runtime_home / "data"
    data.mkdir(parents=True)
    process = start_stand_in(elsewhere, "-m", SERVER_ARG)
    try:
        wait_for_args(process.pid, SERVER_ARG)
        (data / "vool_api.pid").write_text(str(process.pid))
        result = run_uninstall(script_env(home, install_root, runtime_home, tmp_path), "--yes")
        assert result.returncode == 0, result.stdout + result.stderr
        assert process.poll() is None
    finally:
        stop_process(process)
    assert not install_root.exists()
    assert runtime_home.is_dir()
    decoy_intact(home)


def test_install_root_path_over_200_chars_is_still_recognised(home, runtime_home, tmp_path):
    deep = tmp_path / ("vool-deep-install-path/" * 12)
    install_root = deep.resolve()
    assert len(str(install_root)) > 200
    install_root.mkdir(parents=True)
    (install_root / "launch.py").write_text("# stand-in source install\n")
    stop_running_server_and_uninstall(home, install_root, runtime_home, tmp_path)
    assert not install_root.exists()
    assert runtime_home.is_dir()


def test_llamacpp_lane_without_pidfile_is_stopped(home, install_root, runtime_home, tmp_path):
    # The llama.cpp lane keeps no pidfile; it is proven by its whole command line
    # (module plus this runtime's model file) and its working folder.
    models = (tmp_path / LLAMACPP_MODELS_NAME).resolve()
    models.mkdir(parents=True)
    (models / "fake.gguf").write_bytes(b"GGUF stand-in model\n")
    process = start_stand_in(
        install_root, "-m", LLAMACPP_MODULE, "--model", str(models / "fake.gguf"),
    )
    try:
        wait_for_args(process.pid, LLAMACPP_MODULE)
        result = run_uninstall(script_env(home, install_root, runtime_home, tmp_path), "--yes")
        assert result.returncode == 0, result.stdout + result.stderr
        wait_until_exited(process)
    finally:
        stop_process(process)
    assert not install_root.exists()
    decoy_intact(home)


def test_remove_data_with_yes_deletes_runtime_home(home, install_root, runtime_home, tmp_path):
    data = runtime_home / "data"
    data.mkdir(parents=True)
    (data / "saved-chat.txt").write_text("keep this chat")
    result = run_uninstall(
        script_env(home, install_root, runtime_home, tmp_path), "--yes", "--remove-data",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not os.path.lexists(runtime_home)
    assert "Deleted your saved chats and settings." in result.stdout
    # The kept-data report runs after the delete step: a run that deleted the chats never also
    # says they were kept, or that nothing was found.
    assert "were kept" not in result.stdout and "A fresh VOOL install will find them" not in result.stdout
    assert "nothing was kept" not in result.stdout
    assert not install_root.exists()
    decoy_intact(home)


def test_remove_data_confirmation_declined_keeps_runtime_home(home, install_root, runtime_home, tmp_path):
    data = runtime_home / "data"
    data.mkdir(parents=True)
    (data / "saved-chat.txt").write_text("keep this chat")
    # "y" accepts the main uninstall question; the empty line takes the data
    # question's default (N).
    result = run_uninstall(
        script_env(home, install_root, runtime_home, tmp_path), "--remove-data", stdin="y\n\n",
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert runtime_home.is_dir()
    assert (runtime_home / "data" / "saved-chat.txt").read_text() == "keep this chat"
    assert "Kept your saved chats and settings." in result.stdout
    assert f"Your chats and settings were kept, in: {runtime_home}" in result.stdout
    assert not install_root.exists()
    decoy_intact(home)


def test_remove_data_refuses_runtime_home_symlink_and_keeps_target(home, install_root, runtime_home, tmp_path):
    runtime_home.rmdir()
    elsewhere = (tmp_path / "elsewhere").resolve()
    elsewhere.mkdir()
    (elsewhere / "notes.txt").write_text("mine")
    runtime_home.symlink_to(elsewhere)
    result = run_uninstall(
        script_env(home, install_root, runtime_home, tmp_path), "--yes", "--remove-data",
    )
    assert result.returncode != 0
    assert "not a plain VOOL data folder" in result.stdout
    assert runtime_home.is_symlink()
    assert (elsewhere / "notes.txt").read_text() == "mine"
    decoy_intact(home)


def test_stop_refusal_removes_nothing(home, install_root, runtime_home, tmp_path):
    # A python3 that answers with the helper's refusal contract (exit 3) stands in
    # for a stop that cannot be completed; the script must then remove nothing.
    refusal_bin = tmp_path / "refusal-bin"
    refusal_bin.mkdir()
    shim = refusal_bin / "python3"
    shim.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' 'VOOL is still running and could not be stopped,"
        " so nothing was removed. Close VOOL and run this uninstall again.'\n"
        "exit 3\n",
    )
    shim.chmod(0o755)
    trash = refusal_bin / "trash"
    trash.write_text("#!/bin/sh\nexit 1\n")
    trash.chmod(0o755)
    data = runtime_home / "data"
    data.mkdir(parents=True)
    process = start_stand_in(install_root, "-m", SERVER_ARG)
    try:
        wait_for_args(process.pid, SERVER_ARG)
        (data / "vool_api.pid").write_text(str(process.pid))
        result = run_uninstall(
            script_env(home, install_root, runtime_home, tmp_path, prepend_path=refusal_bin),
            "--yes",
        )
        assert result.returncode != 0
        assert "VOOL is still running and could not be stopped, so nothing was removed." in result.stdout
        assert "Close VOOL and run this uninstall again." in result.stdout
        assert install_root.is_dir()
        assert runtime_home.is_dir()
        assert process.poll() is None
    finally:
        stop_process(process)
    decoy_intact(home)


def test_script_parses_and_help_documents_remove_data(home, install_root, runtime_home, tmp_path):
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True, timeout=10)
    result = run_uninstall(script_env(home, install_root, runtime_home, tmp_path), "--help")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "--remove-data" in result.stdout
