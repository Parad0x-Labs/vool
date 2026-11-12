"""Uninstall must stop a running VOOL server first and say plainly what it kept."""
from __future__ import annotations

import hashlib
import http.client
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / "installer/bundle/linux"
SHA = "a" * 40
spec = importlib.util.spec_from_file_location("linux_install_uninstall", LINUX / "install_bundle.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)

# Minimal stand-in for apps/vool_api_server.py, stdlib only: it writes the pidfile at
# <VOOL_HOME>/data/vool_api.pid, answers every request with HTTP 500 (the broken state an
# uninstall used to leave behind) and dies on SIGTERM's default action. No handler is
# installed, so the pidfile simply goes stale after a stop -- which later tests rely on.
STAND_IN_SERVER = '''\
"""Stand-in API server used only by the uninstall tests: pidfile plus plain HTTP 500."""
import argparse
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path


class BrokenHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        self.send_response(500)
        self.end_headers()
        self.wfile.write(b"broken")

    do_POST = do_GET

    def log_message(self, *_args: object) -> None:
        pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    runtime_home = Path(os.environ["VOOL_HOME"])
    data = runtime_home / "data"
    data.mkdir(parents=True, exist_ok=True)
    (data / "vool_api.pid").write_text(str(os.getpid()))
    HTTPServer((args.bind, args.port), BrokenHandler).serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def seal(package: Path) -> None:
    files = sorted(p for p in package.rglob("*") if p.is_file() and p.name != "SHA256SUMS")
    (package / "SHA256SUMS").write_text("".join(f"{hashlib.sha256(p.read_bytes()).hexdigest()}  ./{p.relative_to(package).as_posix()}\n" for p in files))


@pytest.fixture
def package(tmp_path):
    package = tmp_path / "package with spaces"
    shutil.copytree(LINUX, package)
    (package / "python/bin").mkdir(parents=True)
    shutil.copy2(Path(sys.executable).resolve(), package / "python/bin/python3")
    (package / "app/apps").mkdir(parents=True)
    shutil.copy2(ROOT / "apps/vool_api_server.py", package / "app/apps/vool_api_server.py")
    (package / "app/apps/vool_api_server.py").write_text(STAND_IN_SERVER)
    (package / "SOURCE_COMMIT").write_text(SHA + "\n")
    (package / "vool.png").write_bytes((ROOT / "installer/assets/vool.png").read_bytes())
    seal(package)
    return package


@pytest.fixture
def home(tmp_path):
    home = (tmp_path / 'home with spaces and $dollar').resolve()
    home.mkdir()
    # A user folder that must survive every uninstall path untouched.
    decoy = home / "NULLA_VIDEO"
    decoy.mkdir()
    (decoy / "video-notes.txt").write_text("user content")
    return home


@pytest.fixture(autouse=True)
def default_runtime_home_resolution(monkeypatch):
    # runtime_home_for(home) prefers env VOOL_HOME/NULLA_HOME over the HOME-relative default;
    # pin the test process to that default so it matches the server child's VOOL_HOME.
    for name in ("VOOL_HOME", "NULLA_HOME"):
        monkeypatch.delenv(name, raising=False)


def decoy_intact(home: Path) -> None:
    assert (home / "NULLA_VIDEO" / "video-notes.txt").read_text() == "user content"


def free_ephemeral_port() -> int:
    # Bind port 0 to let the OS choose; VOOL's own ports are never used here.
    for _ in range(64):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = int(probe.getsockname()[1])
        if port not in (11434, 11435):
            return port
    raise AssertionError("no free ephemeral port away from 11434/11435")


def start_stand_in_server(home: Path, runtime_home: Path, port: int) -> subprocess.Popen:
    root, _, _ = installer.layout(home)
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "VOOL_HOME": str(runtime_home)}
    # "-B" keeps the installed package tree byte-identical to its checksums.
    return subprocess.Popen(
        [sys.executable, "-B", "-m", "apps.vool_api_server", "--bind", "127.0.0.1", "--port", str(port)],
        cwd=str(root / "versions" / SHA / "app"),
        env=environment,
    )


def stop_process(process: subprocess.Popen) -> None:
    if process.poll() is None:
        process.terminate()
    process.wait(timeout=10)


def wait_until_port_answers_500(port: int, timeout: float = 9.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            connection = http.client.HTTPConnection("127.0.0.1", port, timeout=1)
            connection.request("GET", "/")
            response = connection.getresponse()
            response.read()
            connection.close()
        except (OSError, http.client.HTTPException):
            time.sleep(0.1)
            continue
        assert response.status == 500
        return
    raise AssertionError(f"stand-in server never answered on 127.0.0.1:{port}")


def assert_port_refuses_connections(port: int, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=1):
                pass
        except OSError:
            return
        time.sleep(0.1)
    raise AssertionError(f"127.0.0.1:{port} still accepts connections")


def decline(_question: str) -> str:
    return "n"


def test_uninstall_stops_a_running_server_and_reports_what_was_kept(package, home, capsys):
    runtime_home = home / ".vool_runtime"
    sentinel = runtime_home / "data" / "saved-chat.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("keep this")
    installer.install(package, home)
    port = free_ephemeral_port()
    process = start_stand_in_server(home, runtime_home, port)
    try:
        wait_until_port_answers_500(port)
        installer.uninstall(home, prompt=decline)
        assert process.poll() is not None
        assert_port_refuses_connections(port)
    finally:
        stop_process(process)
    root, binary, desktop = installer.layout(home)
    assert not os.path.lexists(binary)
    assert not desktop.exists()
    assert not (root / "versions").exists()
    assert runtime_home.is_dir()
    assert sentinel.read_text() == "keep this"
    decoy_intact(home)
    out = capsys.readouterr().out
    assert f"Your chats and settings were kept, in: {runtime_home}" in out
    assert "A fresh VOOL install will find them there again." in out
    assert "To delete them too, delete that folder." in out
    # The package (and its uninstall.sh) is gone now, so the line never asks for a second run.
    assert "--remove-data" not in out


def test_uninstall_never_signals_a_process_that_is_not_ours(package, home):
    installer.install(package, home)
    root, _, _ = installer.layout(home)
    # Live pid, working folder inside the install's app tree; only its command line
    # (no "-m apps.vool_api_server") tells it apart from VOOL's own server.
    helper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], cwd=str(root / "versions" / SHA / "app"))
    try:
        data = home / ".vool_runtime" / "data"
        data.mkdir(parents=True)
        (data / "vool_api.pid").write_text(str(helper.pid))
        installer.uninstall(home, prompt=decline)
        assert helper.poll() is None
    finally:
        stop_process(helper)
    assert not (root / "versions").exists()
    decoy_intact(home)


def test_uninstall_removes_nothing_when_the_server_cannot_be_stopped(package, home, monkeypatch):
    installer.install(package, home)
    runtime_home = home / ".vool_runtime"
    port = free_ephemeral_port()
    process = start_stand_in_server(home, runtime_home, port)
    try:
        wait_until_port_answers_500(port)

        def refused(*_args: object, **_kwargs: object) -> None:
            raise PermissionError(1, "Operation not permitted")

        monkeypatch.setattr(installer.os, "kill", refused)
        with pytest.raises(ValueError, match="VOOL is still running and could not be stopped"):
            installer.uninstall(home, prompt=decline)
        root, binary, desktop = installer.layout(home)
        assert (root / "versions").is_dir()
        assert binary.is_symlink()
        assert desktop.exists()
        assert process.poll() is None
        wait_until_port_answers_500(port)
    finally:
        monkeypatch.undo()
        stop_process(process)


def test_remove_data_deletes_only_the_vool_profile_after_confirmation(package, home, capsys):
    installer.install(package, home)
    runtime_home = home / ".vool_runtime"
    sentinel = runtime_home / "data" / "saved-chat.txt"
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("keep this")
    installer.uninstall(home, remove_data=True, prompt=lambda _question: "y")
    assert not runtime_home.exists()
    assert "Deleted your saved chats and settings." in capsys.readouterr().out
    decoy_intact(home)

    installer.install(package, home)
    sentinel.parent.mkdir(parents=True)
    sentinel.write_text("keep this")
    installer.uninstall(home, remove_data=True, prompt=lambda _question: "")
    assert sentinel.read_text() == "keep this"
    decoy_intact(home)

    installer.install(package, home)
    shutil.rmtree(runtime_home)
    elsewhere = home / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "notes.txt").write_text("mine")
    runtime_home.symlink_to(elsewhere)
    with pytest.raises(ValueError):
        installer.uninstall(home, remove_data=True, prompt=lambda _question: "y")
    assert runtime_home.is_symlink()
    assert (elsewhere / "notes.txt").read_text() == "mine"
    decoy_intact(home)


def test_uninstall_proceeds_when_the_pidfile_names_an_exited_process(package, home):
    installer.install(package, home)
    runtime_home = home / ".vool_runtime"
    port = free_ephemeral_port()
    process = start_stand_in_server(home, runtime_home, port)
    wait_until_port_answers_500(port)
    stop_process(process)  # SIGTERM's default action leaves a pidfile whose pid is now gone.
    installer.uninstall(home, prompt=decline)
    root, binary, desktop = installer.layout(home)
    assert not (root / "versions").exists()
    assert not os.path.lexists(binary)
    assert not desktop.exists()
    assert runtime_home.is_dir()
    decoy_intact(home)


def test_uninstall_sh_parses_and_passes_arguments_through():
    script = LINUX / "uninstall.sh"
    subprocess.run(["bash", "-n", str(script)], check=True)
    exec_lines = [line.strip() for line in script.read_text().splitlines() if "install_bundle.py" in line]
    assert exec_lines, "uninstall.sh must run install_bundle.py"
    assert all('"$@"' in line for line in exec_lines)


def test_uninstall_stops_a_server_under_a_long_install_path(package, tmp_path):
    # A deep home pushes the whole command line past 200 characters: ps(1) folds
    # long argument lists unless asked not to, which once hid the server arg and
    # let the removal proceed under a live server.
    home = (tmp_path / ("deep install folder " + "x" * 190)).resolve()
    home.mkdir()
    installer.install(package, home)
    runtime_home = home / ".vool_runtime"
    root, _, _ = installer.layout(home)
    assert len(str(root / "versions" / SHA / "app")) > 200
    port = free_ephemeral_port()
    process = start_stand_in_server(home, runtime_home, port)
    try:
        wait_until_port_answers_500(port)
        installer.uninstall(home, prompt=decline)
        assert process.poll() is not None
        assert_port_refuses_connections(port)
    finally:
        stop_process(process)
    assert not (root / "versions").exists()
    assert runtime_home.is_dir()
