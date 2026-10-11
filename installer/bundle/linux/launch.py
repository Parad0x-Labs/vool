"""Installed Linux entry point: headless API or local chat in the user's browser."""
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


def main() -> int:
    parser = argparse.ArgumentParser(description="VOOL Linux runtime (local browser desktop)")
    parser.add_argument("--headless", action="store_true", help="serve without opening a browser")
    parser.add_argument("--port", type=int, default=11435)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    root = Path(__file__).resolve().parent
    os.chdir(root / "app")
    # Prevent host Python environment leakage, while retaining explicit product settings.
    os.environ.pop("PYTHONHOME", None)
    os.environ.pop("PYTHONPATH", None)
    os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
    sys.path.insert(0, str(root / "app"))
    from core.runtime_paths import user_runtime_default

    if not (os.environ.get("VOOL_HOME") or os.environ.get("NULLA_HOME")):
        os.environ["VOOL_HOME"] = str(user_runtime_default())
    command = [sys.executable, "-B", "-E", "-s", "-m", "apps.vool_api_server", "--bind", "127.0.0.1", "--port", str(args.port)]
    if args.headless:
        os.execv(sys.executable, command)
    # Never open a page served by an unknown port occupant, or stop that process.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", args.port))
    process = subprocess.Popen(command)
    origin = f"http://127.0.0.1:{args.port}"
    expected = (root / "SOURCE_COMMIT").read_text().strip()

    def stop(_signum: int, _frame: object) -> None:
        process.terminate()

    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, stop)
    try:
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if process.poll() is not None:
                return process.returncode or 1
            try:
                with urllib.request.urlopen(origin + "/healthz", timeout=2) as response:
                    health = json.load(response)
                runtime = health.get("runtime", {})
                if health.get("ok") is True and runtime.get("pid") == process.pid and runtime.get("commit_full") == expected:
                    if not webbrowser.open(origin + "/chat"):
                        print(f"Open {origin}/chat in your browser.", file=sys.stderr)
                    return process.wait()
            except (OSError, ValueError, urllib.error.URLError):
                pass
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


if __name__ == "__main__":
    raise SystemExit(main())
