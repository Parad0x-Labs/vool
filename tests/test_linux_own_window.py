"""VOOL's own window on Linux: the package launcher, the window host on the system python3, and the
window through an in-app update.

The real-app proof (the installed package on Ubuntu 24.04 and 22.04, a real display, the update pressed
in the window) is release evidence; these tests hold the contracts that proof depends on, with real
processes, sockets and files wherever the behavior is about them.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import socket
import stat
import subprocess
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from unittest import mock

import pytest
import tomllib

from core.runtime_paths import runtime_home_identity
from installer.bundle import linux_desktop, linux_window_lib, vool_window
from installer.bundle.native_runtime_supervisor import NativeRuntimeError, NativeRuntimeSupervisor

ROOT = Path(__file__).resolve().parents[1]
LINUX = ROOT / "installer/bundle/linux"
SHA = "c" * 40
NEW_SHA = "d" * 40
linux_only = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="the Linux package's window")

# Everything the launcher writes into its own environment; restored after each test.
_LAUNCHER_ENV = ("VOOL_PROJECT_ROOT", "VOOL_WORKSPACE_ROOT", "PYTHONDONTWRITEBYTECODE", "PYTHONHOME", "PYTHONPATH",
                 "VOOL_RUNTIME_MODE", "VOOL_RUNTIME_PYTHON", "VOOL_EXPECTED_COMMIT", "VOOL_NATIVE_API_URL",
                 "VOOL_NATIVE_REQUIRE_OWNED_RUNTIME", "DISPLAY", "WAYLAND_DISPLAY")


class _Exec(Exception):
    def __init__(self, path, argv):
        super().__init__(path)
        self.path, self.argv = path, list(argv)


def _installed_launcher(tmp_path, monkeypatch, *, display: bool):
    package = tmp_path / "package"
    package.mkdir()
    shutil.copy2(LINUX / "launch.py", package / "launch.py")
    (package / "app").symlink_to(ROOT, target_is_directory=True)
    (package / "SOURCE_COMMIT").write_text(SHA + "\n")
    spec = importlib.util.spec_from_file_location("linux_launch_window", package / "launch.py")
    launch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(launch)
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool-home"))
    for name in _LAUNCHER_ENV:
        monkeypatch.setenv(name, "")
        monkeypatch.delenv(name)
    if display:
        monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(launch.os, "chdir", lambda path: None)

    def execv(path, argv):
        raise _Exec(path, argv)

    monkeypatch.setattr(launch.os, "execv", execv)
    monkeypatch.setattr(sys, "argv", ["launch.py"])
    return package.resolve(), launch


# -- the launcher ------------------------------------------------------------------------------------


def test_on_a_desktop_vool_opens_its_window_on_the_system_python(tmp_path, monkeypatch):
    package, launch = _installed_launcher(tmp_path, monkeypatch, display=True)
    monkeypatch.setattr(linux_desktop, "find_window_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(launch, "_health", lambda origin: {})
    with pytest.raises(_Exec) as started:
        launch.main()
    assert started.value.path == "/usr/bin/python3"
    assert started.value.argv == ["/usr/bin/python3", "-I", "-B", str(package / "window_host.py")]
    # The window starts and owns the server, on the package's own interpreter and this exact build.
    assert os.environ["VOOL_RUNTIME_MODE"] == "self-contained"
    assert os.environ["VOOL_RUNTIME_PYTHON"] == sys.executable
    assert os.environ["VOOL_EXPECTED_COMMIT"] == SHA
    assert os.environ["VOOL_NATIVE_API_URL"] == "http://127.0.0.1:11435"
    assert "VOOL_NATIVE_REQUIRE_OWNED_RUNTIME" not in os.environ


@pytest.mark.parametrize("serving, joins", [(SHA, True), (NEW_SHA, False)])
def test_the_window_joins_only_this_exact_package_already_serving(tmp_path, monkeypatch, serving, joins):
    _package, launch = _installed_launcher(tmp_path, monkeypatch, display=True)
    monkeypatch.setattr(linux_desktop, "find_window_python", lambda: "/usr/bin/python3")
    monkeypatch.setattr(launch, "_health", lambda origin: {"ok": True, "runtime": {"commit_full": serving}})
    with pytest.raises(_Exec):
        launch.main()
    # Anything else on the address is refused by the window, which then stops nothing it did not start.
    assert os.environ.get("VOOL_NATIVE_REQUIRE_OWNED_RUNTIME") == ("0" if joins else None)


def test_a_desktop_without_the_window_parts_is_told_what_to_install(tmp_path, monkeypatch):
    _package, launch = _installed_launcher(tmp_path, monkeypatch, display=True)
    told = []
    monkeypatch.setattr(linux_desktop, "find_window_python", lambda: None)
    monkeypatch.setattr(linux_desktop, "tell_user", lambda title, body, **kw: told.append((title, body)))
    assert launch.main() == 1
    ((title, body),) = told
    assert title == "VOOL cannot open its window"
    assert "sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1" in body


def test_headless_serves_even_on_a_desktop(tmp_path, monkeypatch):
    _package, launch = _installed_launcher(tmp_path, monkeypatch, display=True)
    monkeypatch.setattr(sys, "argv", ["launch.py", "--headless"])
    with pytest.raises(_Exec) as started:
        launch.main()
    assert started.value.argv == [sys.executable, "-B", "-E", "-s", "-m", "apps.vool_api_server",
                                  "--bind", "127.0.0.1", "--port", "11435"]


def test_without_a_display_opening_vool_again_reaches_the_running_app_in_the_browser(tmp_path, monkeypatch):
    # The CI update journeys run `vool` with no display after the update: it must open the chat of the
    # package already serving, and never start a second server.
    _package, launch = _installed_launcher(tmp_path, monkeypatch, display=False)
    opened = []
    monkeypatch.setattr(launch, "_port_is_free", lambda port: False)
    monkeypatch.setattr(launch, "_health", lambda origin: {"ok": True, "runtime": {"commit_full": SHA}})
    monkeypatch.setattr(launch.webbrowser, "open", lambda url: opened.append(url) or True)
    monkeypatch.setattr(launch.subprocess, "Popen", lambda *a, **k: pytest.fail("started a second server"))
    assert launch.main() == 0
    assert opened == ["http://127.0.0.1:11435/chat"]


# -- which python3 shows the window, and what a person is told -------------------------------------


def _fake_python(path: Path, exit_code: int, calls: Path) -> None:
    path.write_text(f"#!/bin/sh\necho \"$0 $1 $2 $3\" >> '{calls}'\nexit {exit_code}\n")
    path.chmod(0o755)


@linux_only
def test_the_window_runs_on_the_first_system_python_that_can_show_it(tmp_path):
    calls = tmp_path / "calls.log"
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _fake_python(bin_dir / "python3", 1, calls)       # a newer default python3 without python3-gi
    _fake_python(bin_dir / "python3.12", 0, calls)    # the distribution's, with its bindings
    (bin_dir / "python3.13").symlink_to(bin_dir / "python3")
    _fake_python(bin_dir / "python3.12-config", 0, calls)
    candidates = linux_desktop.window_python_candidates(bin_dir)
    assert candidates == [str(bin_dir / "python3"), str(bin_dir / "python3.13"), str(bin_dir / "python3.12")]
    assert linux_desktop.find_window_python(candidates) == str(bin_dir / "python3.12")
    # Each interpreter is probed once, isolated and without writing bytecode.
    assert calls.read_text().splitlines() == [f"{bin_dir}/python3 -I -B -c", f"{bin_dir}/python3.12 -I -B -c"]
    _fake_python(bin_dir / "python3.12", 1, calls)
    assert linux_desktop.find_window_python(candidates) is None


def test_what_a_person_is_told_when_the_window_cannot_open():
    no_display = linux_desktop.window_guidance({})
    assert "vool --headless" in no_display and "http://127.0.0.1:11435/chat" in no_display
    missing = linux_desktop.window_guidance({"WAYLAND_DISPLAY": "wayland-0"})
    assert "sudo apt install python3-gi gir1.2-gtk-3.0 gir1.2-webkit2-4.1" in missing
    assert linux_desktop.has_display({"DISPLAY": ":1"}) and not linux_desktop.has_display({"DISPLAY": " "})


def test_an_app_menu_launch_gets_a_desktop_notification_not_only_standard_error(tmp_path, monkeypatch, capsys):
    icon = tmp_path / "vool.png"
    icon.write_bytes(b"png")
    shown = []
    monkeypatch.setenv("DISPLAY", ":0")
    monkeypatch.setattr(linux_desktop.shutil, "which", lambda name: "/usr/bin/notify-send" if name == "notify-send" else None)
    monkeypatch.setattr(linux_desktop.subprocess, "Popen", lambda command, **kw: shown.append(command))
    linux_desktop.tell_user("VOOL could not start", "Close it, then open VOOL again.", icon=icon)
    assert shown == [["/usr/bin/notify-send", "--app-name=VOOL", f"--icon={icon}", "VOOL could not start",
                      "Close it, then open VOOL again."]]
    assert "VOOL could not start" in capsys.readouterr().err


def test_the_window_helpers_need_only_the_standard_library():
    # linux_desktop runs on the system python3 and in the launcher before anything else; linux_window_lib
    # runs in the bundle build before the package's dependencies exist; window_host and launch start them.
    for path in (ROOT / "installer/bundle/linux_desktop.py", ROOT / "installer/bundle/linux_window_lib.py",
                 LINUX / "window_host.py", LINUX / "launch.py"):
        for node in ast.parse(path.read_text()).body:
            names = [a.name for a in node.names] if isinstance(node, ast.Import) else (
                [node.module] if isinstance(node, ast.ImportFrom) and node.module != "__future__" else [])
            for name in names:
                assert name.split(".")[0] in sys.stdlib_module_names, f"{path.name} imports {name}"


# -- the window's own library folder: exactly uv.lock's pins, pure Python ---------------------------


def test_window_lib_pins_are_uv_locks_pure_python_wheels_by_digest():
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    built = json.loads((ROOT / "installer/bundle/built_wheels.json").read_text())["wheels"]
    pins = linux_window_lib.locked_pins(lock, built)
    assert [name for name, _version, _digests in pins] == list(linux_window_lib.WINDOW_PACKAGES)
    for name, version, digests in pins:
        (entry,) = [p for p in lock["package"] if linux_window_lib.norm(p["name"]) == name]
        assert version == entry["version"] and digests and all(len(d) == 64 for d in digests)
    proxy = {name: digests for name, _v, digests in pins}["proxy-tools"]
    assert proxy == [r["sha256"] for r in built if linux_window_lib.norm(r["package"]) == "proxy-tools"]


def _lock(**wheels):
    return {"package": [{"name": name, "version": "1.0", "wheels": wheels.get(name.replace("-", "_"), [
        {"url": f"https://files.example/{name}-1.0-py3-none-any.whl", "hash": "sha256:" + "a" * 64}])}
        for name in linux_window_lib.WINDOW_PACKAGES]}


def test_a_window_package_without_a_pure_python_wheel_needs_this_projects_recorded_build():
    lock = _lock(proxy_tools=[{"url": "https://files.example/proxy_tools-1.0-cp312-cp312-linux_x86_64.whl",
                               "hash": "sha256:" + "b" * 64}])
    with pytest.raises(ValueError, match="publishes no pure-Python wheel"):
        linux_window_lib.locked_pins(lock, [])
    record = {"package": "proxy-tools", "target": "any", "version": "0.9", "file": "proxy_tools-0.9-py3-none-any.whl",
              "sha256": "c" * 64}
    with pytest.raises(ValueError, match=r"uv\.lock pins 1\.0"):
        linux_window_lib.locked_pins(lock, [record])
    record.update(version="1.0", file="proxy_tools-1.0-py3-none-any.whl")
    assert linux_window_lib.locked_pins(lock, [record])[2] == ("proxy-tools", "1.0", ["c" * 64])


def _dist(target: Path, name: str, version: str, *requires: str) -> None:
    info = target / f"{name.replace('-', '_')}-{version}.dist-info"
    info.mkdir(parents=True)
    lines = ["Metadata-Version: 2.1", f"Name: {name}", f"Version: {version}", *(f"Requires-Dist: {r}" for r in requires)]
    (info / "METADATA").write_text("\n".join(lines) + "\n")


PINS = [("pywebview", "6.2.1", []), ("bottle", "0.13.4", []), ("proxy-tools", "0.1.0", []),
        ("typing-extensions", "4.15.0", [])]


def _window_lib(tmp_path: Path) -> Path:
    target = tmp_path / "window-lib"
    _dist(target, "pywebview", "6.2.1", "bottle", "proxy_tools", "typing_extensions",
          'pyobjc-core; sys_platform == "darwin"', 'pythonnet; sys_platform == "win32"', 'QtPy; extra == "qt"',
          'importlib_resources; python_version < "3.7"')
    _dist(target, "bottle", "0.13.4")
    _dist(target, "proxy-tools", "0.1.0")
    _dist(target, "typing-extensions", "4.15.0")
    return target


def test_the_installed_window_lib_is_exactly_the_pins(tmp_path):
    assert linux_window_lib.check(_window_lib(tmp_path), PINS) == []


@pytest.mark.parametrize("damage, problem", [
    (lambda t: (t / "webview/lib").mkdir(parents=True) or (t / "webview/lib/WebView2Loader.dll").write_bytes(b"MZ"),
     "compiled file in window-lib: webview/lib/WebView2Loader.dll"),
    (lambda t: (t / "linked.py").symlink_to("/etc/hostname"), "symlink in window-lib: linked.py"),
    (lambda t: _dist(t, "cffi", "2.0.0"), "window-lib holds"),
    (lambda t: shutil.rmtree(t / "bottle-0.13.4.dist-info") or _dist(t, "bottle", "0.13.5"), "bottle 0.13.5 installed"),
    (lambda t: _dist(t, "typing-extensions-extra", "1.0", "certifi") or None, "requires certifi"),
    (lambda t: _dist(t, "evil", "1.0", 'certifi; platform_machine == "x86_64"'), "unrecognised requirement condition"),
])
def test_anything_else_in_window_lib_is_refused(tmp_path, damage, problem):
    target = _window_lib(tmp_path)
    damage(target)
    assert any(problem in p for p in linux_window_lib.check(target, PINS)), linux_window_lib.check(target, PINS)


def test_the_build_writes_hashed_requirements_and_checks_the_folder_against_them(tmp_path):
    (tmp_path / "uv.lock").write_text(
        "".join(f'[[package]]\nname = "{name}"\nversion = "{version}"\nwheels = [\n'
                f'  {{ url = "https://files.example/{name}-{version}-py3-none-any.whl", hash = "sha256:{"e" * 64}" }},\n]\n\n'
                for name, version, _d in PINS))
    (tmp_path / "built.json").write_text(json.dumps({"wheels": []}))
    requirements = tmp_path / "requirements-window.txt"
    assert linux_window_lib.main(["requirements", "--lock", str(tmp_path / "uv.lock"), "--built",
                                  str(tmp_path / "built.json"), "--out", str(requirements)]) == 0
    assert requirements.read_text().startswith("pywebview==6.2.1 \\\n    --hash=sha256:" + "e" * 64)
    target = _window_lib(tmp_path)
    assert linux_window_lib.main(["check", "--target", str(target), "--requirements", str(requirements)]) == 0
    _dist(target, "cffi", "2.0.0")
    assert linux_window_lib.main(["check", "--target", str(target), "--requirements", str(requirements)]) == 1


def test_the_package_build_installs_window_lib_by_digest_and_checks_it():
    build = (ROOT / "installer/bundle/build_linux_bundle.sh").read_text()
    install = build[build.index('--target "${BUNDLE}/window-lib"') - 200:build.index("requirements-window.txt\"", build.index('--target "${BUNDLE}/window-lib"'))]
    for flag in ("--no-deps", "--no-build", "--require-hashes", "--no-config"):
        assert flag in install
    assert 'linux_window_lib.py" check' in build
    assert "from installer.bundle import vool_window" in build  # the window's imports, on the system python3


# -- the runtime the window starts, joins, or takes over --------------------------------------------


def _health(sha: str = SHA, *, pid: int, home: Path) -> dict:
    return {"ok": True, "agent": "VOOL", "daemon": True,
            "runtime": {"commit": sha[:12], "commit_full": sha, "pid": pid, "build_id": f"test+{sha[:12]}",
                        "protocol_version": 1, "release_version": "0.8.0-beta",
                        "workstation_version": "vool-workstation-test-v1",
                        "runtime_home_id": runtime_home_identity({"VOOL_HOME": str(home)})}}


def _joined(tmp_path: Path, pid: int, *, clock=None) -> NativeRuntimeSupervisor:
    home = tmp_path / "home"
    supervisor = NativeRuntimeSupervisor(tmp_path, ["true"], expected_sha=SHA, env={"VOOL_HOME": str(home)},
                                         require_owned=False, startup_timeout=1, poll_interval=0, unhealthy_grace=30,
                                         popen=mock.Mock(), sleep=lambda _: None,
                                         monotonic=clock or time.monotonic)
    supervisor._health = mock.Mock(return_value=_health(pid=pid, home=home))
    supervisor.ensure_ready()
    assert supervisor.attached_existing
    return supervisor


def test_the_runtime_keeps_the_packages_own_python_isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("VOOL_RUNTIME_MODE", "self-contained")
    monkeypatch.setenv("VOOL_RUNTIME_PYTHON", "/opt/vool/python/bin/python3")
    monkeypatch.setenv("VOOL_EXPECTED_COMMIT", SHA)
    monkeypatch.setenv("VOOL_NATIVE_API_URL", "http://127.0.0.1:11435")
    monkeypatch.setenv("VOOL_PROJECT_ROOT", str(tmp_path))
    supervisor = NativeRuntimeSupervisor.from_environment(module_path=ROOT / "installer/bundle/vool_window.py")
    assert supervisor.command == ["/opt/vool/python/bin/python3", "-B", "-E", "-s", "-m", "apps.vool_api_server",
                                  "--port", "11435"]


def test_only_the_joined_runtime_that_is_serving_can_be_taken_over(tmp_path):
    supervisor = _joined(tmp_path, 7300)
    with pytest.raises(NativeRuntimeError, match="not the one serving"):
        supervisor.adopt(7301)
    supervisor.adopt(7300)
    assert supervisor.adopted_pid == 7300
    owner = _joined(tmp_path / "owner", 7400)
    owner.process = mock.Mock(pid=7400)
    with pytest.raises(NativeRuntimeError, match="only a joined runtime"):
        owner.adopt(7400)


def test_a_taken_over_runtime_is_watched_like_the_windows_own(tmp_path):
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        now = [100.0]
        supervisor = _joined(tmp_path, process.pid, clock=lambda: now[0])
        supervisor.adopt(process.pid)
        supervisor.assert_alive()
        supervisor._health.return_value = None      # a slow answer is tolerated for the grace
        supervisor.assert_alive()
        now[0] += 31
        with pytest.raises(NativeRuntimeError, match="health endpoint is unavailable"):
            supervisor.assert_alive()
        supervisor._unhealthy_since = None
        supervisor._health.return_value = _health(pid=process.pid + 1, home=tmp_path / "home")
        with pytest.raises(NativeRuntimeError, match="identity changed"):
            supervisor.assert_alive()
        supervisor._health.return_value = _health(pid=process.pid, home=tmp_path / "home")
        process.kill()
        process.wait()
        with pytest.raises(NativeRuntimeError, match="taken-over runtime exited"):
            supervisor.assert_alive()
    finally:
        if process.poll() is None:
            process.kill()


def test_while_the_updater_restarts_vool_any_version_of_this_profile_counts(tmp_path):
    supervisor = _joined(tmp_path, 7300)
    supervisor._health.return_value = _health(NEW_SHA, pid=7500, home=tmp_path / "home")
    assert supervisor.profile_runtime_identity()["commit_full"] == NEW_SHA
    supervisor._health.return_value = _health(NEW_SHA, pid=7500, home=tmp_path / "another-profile")
    assert supervisor.profile_runtime_identity() is None
    supervisor._health.return_value = {"ok": True, "runtime": {"commit_full": NEW_SHA}}
    assert supervisor.profile_runtime_identity() is None


# -- the window host ---------------------------------------------------------------------------------


@pytest.fixture
def window_state(tmp_path, monkeypatch):
    """vool_window's Linux lane, isolated: its log, VOOL home, package record and handoff state."""
    logged: list[str] = []
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "vool-home"))
    monkeypatch.setattr(vool_window, "_log", logged.append)
    monkeypatch.setattr(vool_window, "_LINUX_PACKAGE", {})
    monkeypatch.setattr(vool_window, "_UPDATE_HANDOFF", {})
    monkeypatch.setattr(vool_window, "_ADOPTED_RUNTIME", {})
    monkeypatch.setattr(vool_window, "_PRESENT_SERVER", {})
    monkeypatch.setattr(vool_window, "_PRESENT_WINDOWS", [])
    return logged


def test_the_window_knows_the_installed_package_it_runs_from(tmp_path, window_state):
    version = tmp_path / "share/vool/versions" / SHA
    version.mkdir(parents=True)
    (version / "vool.png").write_bytes(b"png")
    vool_window.configure_linux_package(version)
    assert vool_window._LINUX_PACKAGE["install"] == (tmp_path / "share/vool").resolve()
    assert vool_window._linux_start_options() == {"gui": "gtk", "icon": str(version.resolve() / "vool.png")}
    unpacked = tmp_path / "vool-0.8.0-beta-linux-x86_64"
    unpacked.mkdir()
    vool_window.configure_linux_package(unpacked)
    assert vool_window._LINUX_PACKAGE["install"] is None      # no installed layout: no update handoff
    assert vool_window._linux_start_options() == {"gui": "gtk"}


class _Settings:
    def __init__(self):
        self.local_storage = False

    def set_enable_html5_local_storage(self, value):
        self.local_storage = value

    def get_enable_html5_local_storage(self):
        return self.local_storage


class _View:
    def __init__(self, ephemeral):
        self.ephemeral, self.settings = ephemeral, _Settings()

    def is_ephemeral(self):
        return self.ephemeral

    def get_settings(self):
        return self.settings


class _Event:
    def __init__(self):
        self.handlers = []

    def __iadd__(self, handler):
        self.handlers.append(handler)
        return self


def _fake_gtk_webview(monkeypatch, views):
    gtk = types.ModuleType("webview.platforms.gtk")
    gtk.BrowserView = types.SimpleNamespace(instances={uid: types.SimpleNamespace(webview=v) for uid, v in views.items()})
    platforms = types.ModuleType("webview.platforms")
    platforms.gtk = gtk
    webview = types.ModuleType("webview")
    webview.platforms = platforms
    monkeypatch.setitem(sys.modules, "webview", webview)
    monkeypatch.setitem(sys.modules, "webview.platforms", platforms)
    monkeypatch.setitem(sys.modules, "webview.platforms.gtk", gtk)
    return webview


def test_the_chat_page_keeps_its_storage_in_memory_only(monkeypatch, window_state):
    # pywebview's private mode turns localStorage off on GTK, and the chat page's Send did nothing.
    private, persistent = _View(ephemeral=True), _View(ephemeral=False)
    _fake_gtk_webview(monkeypatch, {"chat": private, "other": persistent})
    vool_window._linux_keep_page_storage(types.SimpleNamespace(uid="chat"))
    assert private.settings.local_storage is True
    vool_window._linux_keep_page_storage(types.SimpleNamespace(uid="other"))
    assert persistent.settings.local_storage is False     # never on for a context that writes to disk
    assert "page storage left off: the WebKit context is not ephemeral" in window_state


def test_every_window_the_host_creates_gets_the_page_storage_handler(monkeypatch, window_state):
    webview = _fake_gtk_webview(monkeypatch, {})
    made = []
    webview.create_window = lambda *a, **k: made.append(types.SimpleNamespace(events=types.SimpleNamespace(before_show=_Event()))) or made[-1]
    vool_window._linux_wrap_window_factory(webview)
    vool_window._linux_wrap_window_factory(webview)  # once only
    webview.create_window("VOOL", "http://127.0.0.1:11435/chat")
    webview.create_window("VOOL Settings", "http://127.0.0.1:11435/settings")
    assert [w.events.before_show.handlers for w in made] == [[vool_window._linux_keep_page_storage]] * 2


@linux_only
def test_a_second_launch_brings_the_open_window_forward(monkeypatch, window_state):
    base = tempfile.mkdtemp(prefix="vw", dir="/tmp")  # a Unix socket path must stay short
    try:
        os.makedirs(os.path.join(base, "VOOL"))
        monkeypatch.setattr(vool_window, "_posix_lock_base", lambda: base)
        path = os.path.join(base, "VOOL", "window.sock")
        open(path, "w").close()  # left by a host that did not exit cleanly
        presented, arrived = [], threading.Semaphore(0)
        monkeypatch.setattr(vool_window, "_present_open_window", lambda token="": (presented.append(token), arrived.release()))
        vool_window._serve_come_forward_requests()
        assert stat.S_ISSOCK(os.stat(path).st_mode) and stat.S_IMODE(os.stat(path).st_mode) == 0o600
        monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
        monkeypatch.setenv("DESKTOP_STARTUP_ID", "gio-launch-1234_TIME42")
        vool_window._ask_open_window_to_come_forward()
        assert arrived.acquire(timeout=5)
        monkeypatch.setenv("WAYLAND_DISPLAY", "wayland-0")
        monkeypatch.setenv("XDG_ACTIVATION_TOKEN", "wayland-token")
        vool_window._ask_open_window_to_come_forward()
        assert arrived.acquire(timeout=5)
        with socket.socket(socket.AF_UNIX) as raw:   # a token that is not text is dropped, not passed on
            raw.connect(path)
            raw.sendall(b"\x1b[2J\x07\n")
        assert arrived.acquire(timeout=5)
        assert presented == ["gio-launch-1234_TIME42", "wayland-token", ""]
        vool_window._stop_serving_come_forward_requests()
        assert not os.path.exists(path)
    finally:
        shutil.rmtree(base, ignore_errors=True)


def test_vool_without_a_display_says_so_and_starts_nothing(monkeypatch, window_state):
    vool_window._LINUX_PACKAGE.update(root=Path("/nonexistent"), install=None, icon=None)
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    told = []
    monkeypatch.setattr(linux_desktop, "tell_user", lambda title, body, **kw: told.append(title))
    monkeypatch.setattr(vool_window, "_single_instance", lambda: pytest.fail("went on to open a window"))
    assert vool_window.main() == 1
    assert told == ["VOOL cannot open its window"]


def test_the_words_for_a_runtime_that_could_not_start(monkeypatch, window_state, tmp_path):
    monkeypatch.setattr(vool_window, "_API_ORIGIN", "http://127.0.0.1:11435")
    busy = vool_window._runtime_failure_text("port 11435 is already occupied by another or unproved runtime profile")
    assert busy == ("Another program, or another copy of VOOL, is using VOOL's address (127.0.0.1:11435). "
                    "Close it, then open VOOL again.")
    other = vool_window._runtime_failure_text("runtime readiness timed out after 300.0s")
    assert other.startswith("VOOL's engine did not start. The details are in ")
    assert other.endswith(os.path.join("logs", "native-runtime-child.log") + ".")


# -- the window through an in-app update -------------------------------------------------------------


def _install(tmp_path: Path) -> Path:
    install = tmp_path / "share/vool"
    (install / "versions" / SHA / "app").mkdir(parents=True)
    (install / "versions" / NEW_SHA / "app").mkdir(parents=True)
    vool_window.configure_linux_package(install / "versions" / SHA)
    return install.resolve()


class _QuickEvent(threading.Event):
    def wait(self, timeout=None):
        return super().wait(0.01 if timeout else timeout)


def test_the_window_stays_open_while_the_updater_works_then_hands_over(tmp_path, monkeypatch, window_state):
    _install(tmp_path)
    working = iter([True, True, True])
    monkeypatch.setattr(vool_window, "_update_worker_running", lambda: next(working, False))
    answers = iter([None, {"pid": 7500, "commit_full": NEW_SHA}])
    supervisor = types.SimpleNamespace(profile_runtime_identity=lambda: next(answers))
    done = _QuickEvent()
    window = types.SimpleNamespace(destroy=done.set)
    settings = types.SimpleNamespace(destroyed=False)
    settings.destroy = lambda: setattr(settings, "destroyed", True)
    monkeypatch.setitem(sys.modules, "webview", types.SimpleNamespace(windows=[window, settings]))
    assert vool_window._hand_over_after_update(supervisor, window, done, "owned runtime child exited") is True
    assert vool_window._UPDATE_HANDOFF == {"pid": 7500, "commit": NEW_SHA}
    assert settings.destroyed and done.is_set() and vool_window.update_handoff_requested()


def test_vool_not_coming_back_after_an_update_is_said_plainly(tmp_path, monkeypatch, window_state):
    _install(tmp_path)
    told = []
    monkeypatch.setattr(vool_window, "_update_worker_running", lambda: False)
    monkeypatch.setattr(vool_window, "_UPDATED_RUNTIME_WAIT_S", 0.05)
    monkeypatch.setattr(vool_window, "_tell_linux_user", lambda title, body: told.append(title))
    supervisor = types.SimpleNamespace(profile_runtime_identity=lambda: None)
    window = types.SimpleNamespace(destroy=lambda: pytest.fail("closed the window"))
    assert vool_window._hand_over_after_update(supervisor, window, _QuickEvent(), "exited") is False
    assert told == ["VOOL stopped during the update"] and not vool_window._UPDATE_HANDOFF


def test_closing_the_window_during_an_update_hands_nothing_over(tmp_path, monkeypatch, window_state):
    _install(tmp_path)
    monkeypatch.setattr(vool_window, "_update_worker_running", lambda: True)
    done = _QuickEvent()
    done.set()
    supervisor = types.SimpleNamespace(profile_runtime_identity=lambda: pytest.fail("waited after the close"))
    assert vool_window._hand_over_after_update(supervisor, types.SimpleNamespace(), done, "exited") is True
    assert not vool_window._UPDATE_HANDOFF


@pytest.mark.parametrize("updating", [True, False])
def test_a_runtime_stopped_by_the_updater_is_not_a_lost_runtime(monkeypatch, window_state, updating):
    monkeypatch.setenv("VOOL_RUNTIME_WATCHDOG_POLL", "0.05")
    monkeypatch.setattr(vool_window, "_update_worker_running", lambda: updating)
    handed = []
    monkeypatch.setattr(vool_window, "_hand_over_after_update", lambda *a: handed.append(a[3]) or True)
    done = threading.Event()
    window = types.SimpleNamespace(destroy=done.set)

    def assert_alive():
        raise NativeRuntimeError("owned runtime child exited (returncode=0)")

    lost = vool_window._start_runtime_watchdog(types.SimpleNamespace(assert_alive=assert_alive), window, done)
    deadline = time.monotonic() + 5
    while not (handed or lost.is_set()) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert bool(handed) is updating and lost.is_set() is (not updating)


def test_the_new_version_opens_and_is_handed_the_runtime_the_updater_started(tmp_path, monkeypatch, window_state):
    install = _install(tmp_path)
    vool_window._UPDATE_HANDOFF.update(pid=7500, commit=NEW_SHA)
    stale = {"VOOL_OWNED_BY_WINDOW_PID": "123", "VOOL_NATIVE_REQUIRE_OWNED_RUNTIME": "1", "VOOL_EXPECTED_COMMIT": SHA,
             "VOOL_RUNTIME_MODE": "self-contained", "VOOL_RUNTIME_PYTHON": "/old/python3", "VOOL_PROJECT_ROOT": "/old/app",
             "VOOL_NATIVE_API_URL": "http://127.0.0.1:12000", "DESKTOP_STARTUP_ID": "used", "XDG_ACTIVATION_TOKEN": "used"}
    for name, value in stale.items():
        monkeypatch.setenv(name, value)
    monkeypatch.setattr(vool_window, "_API_ORIGIN", "http://127.0.0.1:12000")
    started = []
    monkeypatch.setattr(vool_window.subprocess, "Popen", lambda command, **kw: started.append((command, kw)))
    assert vool_window.relaunch_current_version() == 0
    ((command, options),) = started
    assert command == [str(install / "current" / "vool"), "--port", "12000"]
    assert options["env"]["VOOL_WINDOW_ADOPT_RUNTIME_PID"] == "7500" and options["start_new_session"] is True
    assert not set(stale) & set(options["env"])   # the new version derives everything else from itself


def _pidfile(pid: int) -> Path:
    path = vool_window._runtime_pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{pid}\n")
    return path


@pytest.mark.parametrize("identity_pid, named_pid, attached, taken", [
    (7500, 7500, True, True),
    (7500, 7600, True, False),    # the pidfile names another process
    (7600, 7500, True, False),    # another process is serving
    (7500, 7500, False, False),   # this host started its own runtime: nothing to take over
])
def test_the_new_window_takes_over_only_the_runtime_it_was_handed(tmp_path, window_state, identity_pid, named_pid,
                                                                  attached, taken):
    _install(tmp_path)
    _pidfile(named_pid)
    adopted = []
    supervisor = types.SimpleNamespace(attached_existing=attached, adopt=adopted.append)
    vool_window._adopt_updated_runtime(supervisor, {"pid": identity_pid}, "7500")
    assert (adopted == [7500]) is taken and (vool_window._ADOPTED_RUNTIME == {"pid": 7500}) is taken


@linux_only
def test_closing_the_window_stops_the_taken_over_runtime_and_nothing_else(tmp_path, window_state):
    install = _install(tmp_path)
    server = [sys.executable, "-c", "import time; time.sleep(60)", "-m", "apps.vool_api_server"]
    runtime = subprocess.Popen(server, cwd=install / "versions" / NEW_SHA / "app")
    bystander = subprocess.Popen(server, cwd=install / "versions" / NEW_SHA / "app")
    try:
        _pidfile(bystander.pid)            # the pidfile no longer names the runtime: left alone
        vool_window._ADOPTED_RUNTIME["pid"] = runtime.pid
        vool_window._stop_adopted_runtime()
        time.sleep(0.3)
        assert runtime.poll() is None and bystander.poll() is None
        _pidfile(runtime.pid)
        vool_window._ADOPTED_RUNTIME["pid"] = runtime.pid
        vool_window._stop_adopted_runtime()
        assert runtime.wait(timeout=20) == -15 and bystander.poll() is None
        assert f"taken-over runtime pid {runtime.pid} stopped" in window_state
    finally:
        for process in (runtime, bystander):
            if process.poll() is None:
                process.kill()
                process.wait()


def test_window_host_puts_its_own_libraries_first_and_opens_the_updated_version(tmp_path):
    package = tmp_path / "versions" / SHA
    shutil.copytree(LINUX, package, ignore=shutil.ignore_patterns("__pycache__"))
    bundle = package / "app/installer/bundle"
    bundle.mkdir(parents=True)
    (package / "app/installer/__init__.py").write_text("")
    (bundle / "__init__.py").write_text("")
    (package / "window-lib").mkdir()
    (bundle / "vool_window.py").write_text(
        "import os, sys\n"
        "def configure_linux_package(root): print('ROOT', root)\n"
        "def main(): print('PATH', sys.path[0], sys.path[1]); return 0\n"
        "def update_handoff_requested(): return os.environ.get('HANDOFF') == '1'\n"
        "def relaunch_current_version(): print('RELAUNCH'); return 7\n")
    for handoff, code in (("0", 0), ("1", 7)):
        done = subprocess.run([sys.executable, "-I", "-B", str(package / "window_host.py")], capture_output=True,
                              text=True, env={"PATH": "/usr/bin:/bin", "HANDOFF": handoff}, timeout=60)
        assert done.returncode == code, done.stderr
        assert f"PATH {package.resolve()}/window-lib {package.resolve()}/app" in done.stdout
        assert f"ROOT {package.resolve()}" in done.stdout
        assert ("RELAUNCH" in done.stdout) is (handoff == "1")


def test_the_app_menu_entry_names_vools_window(tmp_path):
    spec = importlib.util.spec_from_file_location("linux_install_window", LINUX / "install_bundle.py")
    installer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installer)
    entry = installer.desktop_text(tmp_path / "vool")
    assert "StartupWMClass=VOOL\n" in entry and "StartupNotify=true\n" in entry
