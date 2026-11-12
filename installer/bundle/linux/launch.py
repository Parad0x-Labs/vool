"""Installed Linux entry point: VOOL in its own window, or the headless API (--headless).

On a desktop, VOOL opens in its own window: window_host.py runs on the system python3 (the only
interpreter the distribution's GTK and WebKitGTK bindings load in) and starts, owns and stops the
server, which keeps this package's own Python. Without a display (an SSH login, a CI runner) there is
no window to show, so the chat is served and opened in the browser as before. --headless only serves.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path


def _health(origin: str) -> dict:
    """What answers /healthz on VOOL's address, or {} when nothing recognisable does."""
    try:
        with urllib.request.urlopen(origin + "/healthz", timeout=3) as response:
            health = json.load(response)
    except (OSError, ValueError, urllib.error.URLError):
        return {}
    return health if isinstance(health, dict) else {}


def _serves_this_package(health: dict, expected: str) -> bool:
    return health.get("ok") is True and (health.get("runtime") or {}).get("commit_full") == expected


def _open_window(root: Path, port: int) -> int:
    from installer.bundle import linux_desktop

    window_python = linux_desktop.find_window_python()
    if window_python is None:
        linux_desktop.tell_user("VOOL cannot open its window", linux_desktop.window_guidance(), icon=root / "vool.png")
        return 1
    origin = f"http://127.0.0.1:{port}"
    expected = (root / "SOURCE_COMMIT").read_text().strip()
    os.environ["VOOL_RUNTIME_MODE"] = "self-contained"
    os.environ["VOOL_RUNTIME_PYTHON"] = sys.executable
    os.environ["VOOL_EXPECTED_COMMIT"] = expected
    os.environ["VOOL_NATIVE_API_URL"] = origin
    # The window owns the server it starts, and closing the window stops VOOL. This exact package
    # already serving here (`vool --headless`, or the server VOOL's updater restarted) is joined instead.
    # Anything else on the address is refused by the window, which stops nothing it did not start.
    if _serves_this_package(_health(origin), expected):
        os.environ["VOOL_NATIVE_REQUIRE_OWNED_RUNTIME"] = "0"
    else:
        os.environ.pop("VOOL_NATIVE_REQUIRE_OWNED_RUNTIME", None)
    os.execv(window_python, [window_python, "-I", "-B", str(root / "window_host.py")])
    return 1  # not reached: execv replaces this process or raises


def _port_is_free(port: int) -> bool:
    """True when nothing listens on VOOL's address. SO_REUSEADDR, as the server's own listener sets
    it: right after VOOL closes, its port sits in TIME_WAIT for up to a minute, and a plain bind failed
    with EADDRINUSE, so reopening VOOL straight after closing it showed nothing. On Linux the option
    still refuses a port another process is listening on."""
    with socket.socket() as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def _serve_for_browser(root: Path, port: int, command: list[str]) -> int:
    """No display: serve the chat and open it in the browser (or print its address)."""
    origin = f"http://127.0.0.1:{port}"
    expected = (root / "SOURCE_COMMIT").read_text().strip()
    # Never open a page served by an unknown port occupant, or stop that process. This exact package
    # already serving (an update restarts it in the background) is not unknown: open its chat.
    if not _port_is_free(port):
        if _serves_this_package(_health(origin), expected):
            if not webbrowser.open(origin + "/chat"):
                print(f"Open {origin}/chat in your browser.", file=sys.stderr)
            return 0
        print(f"VOOL cannot start: another program is using {origin}. Close it, then open VOOL again.", file=sys.stderr)
        return 1
    process = subprocess.Popen(command)

    def stop(_signum: int, _frame: object) -> None:
        process.terminate()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return process.returncode or 1
            health = _health(origin)
            runtime = health.get("runtime") or {}
            if _serves_this_package(health, expected) and runtime.get("pid") == process.pid:
                if not webbrowser.open(origin + "/chat"):
                    print(f"Open {origin}/chat in your browser.", file=sys.stderr)
                return process.wait()
            time.sleep(0.2)
        raise RuntimeError("VOOL did not become ready within 180 seconds")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()  # Only the child this launcher created.
                process.wait()


def main() -> int:
    parser = argparse.ArgumentParser(description="VOOL for Linux: opens in its own window")
    parser.add_argument("--headless", action="store_true", help="serve the API and chat without a window")
    parser.add_argument("--port", type=int, default=11435)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    root = Path(__file__).resolve().parent
    os.chdir(root / "app")
    # The install's app/ is VOOL's source, never the person's workspace: name it the way the Mac app
    # does, so chats work under the VOOL home and never read or size up VOOL's own files.
    os.environ["VOOL_PROJECT_ROOT"] = str(root / "app")
    # Prevent host Python environment leakage, while retaining explicit product settings.
    os.environ.pop("PYTHONHOME", None)
    os.environ.pop("PYTHONPATH", None)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path.insert(0, str(root / "app"))
    from core.runtime_paths import user_runtime_default
    from installer.bundle.linux_desktop import has_display

    if not (os.environ.get("VOOL_HOME") or os.environ.get("NULLA_HOME")):
        os.environ["VOOL_HOME"] = str(user_runtime_default())
    command = [sys.executable, "-B", "-E", "-s", "-m", "apps.vool_api_server", "--bind", "127.0.0.1", "--port", str(args.port)]
    if args.headless:
        os.execv(sys.executable, command)
    if has_display():
        return _open_window(root, args.port)
    return _serve_for_browser(root, args.port, command)


if __name__ == "__main__":
    raise SystemExit(main())
