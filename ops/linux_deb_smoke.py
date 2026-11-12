"""Install the downloaded .deb with apt and use VOOL the way a person does, then remove the .deb.

Run as an ordinary user who may use sudo for apt (the CI runner). The person is that user, in a fresh HOME:
the first open sets up their own copy, they chat, VOOL closes, `apt-get remove vool` takes only the system
files, and their own copy still opens with every chat, pin, project, preference and fact they had.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_preservation as keep
from linux_bundle_smoke import fetch

SYSTEM_PACKAGE = Path("/opt/vool/package")


def apt(*arguments: str) -> str:
    sudo = [] if os.geteuid() == 0 else ["sudo", "-n"]
    result = subprocess.run([*sudo, "env", "DEBIAN_FRONTEND=noninteractive", "apt-get", "-y", *arguments],
                            capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"apt-get {' '.join(arguments)} failed: {(result.stderr or result.stdout)[-1500:]}")
    return result.stdout


def open_vool(command: list[str], env: dict, log, expected: str) -> tuple[subprocess.Popen, str]:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    process = subprocess.Popen([*command, "--headless", "--port", str(port)], cwd=env["HOME"], env=env,
                               stdout=log, stderr=log)
    deadline = time.monotonic() + 240
    while True:
        if process.poll() is not None:
            raise RuntimeError(f"VOOL exited before it was ready: {process.returncode}; see runtime.log")
        try:
            health = fetch(origin, "/healthz")
            break
        except (OSError, ValueError, urllib.error.URLError):
            if time.monotonic() >= deadline:
                raise RuntimeError("VOOL was not ready in time; see runtime.log")
            time.sleep(0.3)
    runtime = health.get("runtime", {})
    if health.get("ok") is not True or runtime.get("commit_full") != expected or runtime.get("pid") != process.pid:
        raise RuntimeError(f"Health did not prove this person's VOOL at {expected}: {health}")
    return process, origin


def close(process: subprocess.Popen) -> None:
    process.terminate()  # only the exact process started above
    try:
        process.wait(timeout=30)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        raise RuntimeError("VOOL did not close in time; a forced kill is not a clean stop")
    if process.returncode != 0:
        raise RuntimeError(f"VOOL did not close cleanly: {process.returncode}")


def smoke(deb: Path, scratch: Path, expected: str) -> None:
    scratch.mkdir(parents=True, exist_ok=False)
    home = scratch / "home with spaces"
    home.mkdir()
    # A fresh HOME and a stripped environment, as in the tarball smoke.
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "CI": "true", "VOOL_UNATTENDED": "1",
           "VOOL_LOCAL_MODELS_ENABLED": "0", "TMPDIR": str(scratch), "XDG_CACHE_HOME": str(home / ".cache")}
    report: dict = {"deb": deb.name, "expected": expected}
    report["install"] = apt("install", "--no-install-recommends", str(deb))[-3000:]
    status = subprocess.run(["dpkg-query", "-W", "-f=${Status} ${Version}", "vool"], capture_output=True, text=True)
    if not status.stdout.startswith("install ok installed"):
        raise RuntimeError(f"apt did not install vool: {status.stdout} {status.stderr}")
    if (SYSTEM_PACKAGE / "SOURCE_COMMIT").read_text().strip() != expected:
        raise RuntimeError("The .deb carries a package from the wrong source commit")
    desktop = Path("/usr/share/applications/vool.desktop").read_text()
    if "Exec=/usr/bin/vool\n" not in desktop or not os.access("/usr/bin/vool", os.X_OK):
        raise RuntimeError("The menu entry does not open /usr/bin/vool")
    own = home / ".local/share/vool"
    with (scratch / "runtime.log").open("w") as log:
        # First open, with the menu entry's command: it sets up this person's own copy and opens it.
        process, origin = open_vool(["/usr/bin/vool"], env, log, expected)
        try:
            if os.readlink(own / "current") != f"versions/{expected}":
                raise RuntimeError("The first open did not set up this person's own copy")
            answer = fetch(origin, "/api/chat", {"messages": [{"role": "user", "content": "Calculate 23 * 19"}],
                                                 "stream": False})
            if "437" not in str(answer.get("message", {}).get("content", "")):
                raise RuntimeError(f"VOOL did not answer the arithmetic request: {answer}")
            seen = keep.seed(origin, home)
        finally:
            close(process)
        report["remove"] = apt("remove", "vool")
        if Path("/usr/bin/vool").exists() or SYSTEM_PACKAGE.exists() or Path("/usr/share/applications/vool.desktop").exists():
            raise RuntimeError("apt-get remove left the .deb's own files")
        if "and their chats and settings, are kept" not in report["remove"]:
            raise RuntimeError("Removing the .deb did not say that each person's copy and chats are kept")
        # The person's own copy is theirs: it still opens, with everything they had.
        process, origin = open_vool([str(home / ".local/bin/vool")], env, log, expected)
        try:
            losses = keep.view_losses(seen, keep.person_view(origin))
        finally:
            close(process)
    report["losses"] = losses
    (scratch / "response.json").write_text(json.dumps(report, indent=2))
    if losses:
        raise RuntimeError(f"After apt-get remove the person lost: {losses}")
    print(f"PASS: apt installed the .deb, the first open set up this person's own copy at {expected}, a real chat "
          "answer, chats saved, apt-get remove took only the system files, and the person's copy still opens with "
          "every chat, pin, project, preference and fact")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--deb", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    smoke(args.deb.resolve(), args.scratch.resolve(), args.expected_commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
