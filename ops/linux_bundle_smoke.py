"""Exercise the downloaded Linux package in a disposable HOME, not the checkout."""
from __future__ import annotations

import argparse
import json
import shutil
import socket
import subprocess
import tarfile
import time
import urllib.error
import urllib.request
from pathlib import Path


def fetch(origin: str, path: str, body: dict | None = None) -> dict:
    request = urllib.request.Request(
        origin + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status != 200:
            raise RuntimeError(f"Unexpected status: {response.status}")
        return json.load(response)


def smoke(archive: Path, scratch: Path, expected: str) -> None:
    scratch.mkdir(parents=True, exist_ok=False)
    home = scratch / "home with spaces"
    home.mkdir()
    extraction = scratch / "extracted"
    extraction.mkdir()
    with tarfile.open(archive) as tar:
        # data filter rejects absolute paths, traversal and escaping symlinks.
        tar.extractall(extraction, filter="data")
    bundles = list(extraction.iterdir())
    if len(bundles) != 1 or not bundles[0].is_dir():
        raise RuntimeError("Expected one package directory")
    bundle = bundles[0]
    if (bundle / "SOURCE_COMMIT").read_text().strip() != expected:
        raise RuntimeError("Downloaded package is from the wrong source commit")
    # A fresh HOME and a stripped environment prevent runner credentials, provider
    # settings or system site-packages from qualifying as bundled dependencies.
    env = {"HOME": str(home), "PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", "CI": "true", "VOOL_UNATTENDED": "1", "VOOL_LOCAL_MODELS_ENABLED": "0"}
    env["TMPDIR"] = str(scratch)
    env["XDG_CACHE_HOME"] = str(home / ".cache")
    subprocess.run(["bash", str(bundle / "install.sh")], env=env, check=True)
    binary = home / ".local/bin/vool"
    current = home / ".local/share/vool/current"
    profile = home / ".vool_runtime"
    profile.mkdir()
    preserved = profile / "preserve-this-profile.txt"
    preserved.write_text("existing runtime data\n")
    # Remove the extracted original: runtime must use only installed package bytes.
    shutil.rmtree(extraction)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    with (scratch / "runtime.log").open("w") as log:
        process = subprocess.Popen([str(binary), "--headless", "--port", str(port)], cwd=home, env=env, stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 180
            while True:
                if process.poll() is not None:
                    raise RuntimeError(f"Runtime exited before readiness: {process.returncode}; see runtime.log")
                try:
                    health = fetch(origin, "/healthz")
                    break
                except (OSError, ValueError, urllib.error.URLError):
                    if time.monotonic() >= deadline:
                        raise RuntimeError("Readiness timed out; see runtime.log")
                    time.sleep(0.3)
            runtime = health.get("runtime", {})
            if health.get("ok") is not True or runtime.get("commit_full") != expected or runtime.get("pid") != process.pid:
                raise RuntimeError("Health did not prove the installed process and exact source identity")
            version = fetch(origin, "/api/runtime/version")
            if version.get("commit_full") != expected:
                raise RuntimeError("Runtime version endpoint disagrees with the package")
            answer = fetch(origin, "/api/chat", {"messages": [{"role": "user", "content": "Calculate 23 * 19"}], "stream": False})
            content = str(answer.get("message", {}).get("content", ""))
            if "437" not in content:
                raise RuntimeError(f"Installed runtime did not answer the arithmetic request: {content!r}")
            (scratch / "response.json").write_text(json.dumps({"health": health, "version": version, "answer": answer}, indent=2))
        finally:
            if process.poll() is None:
                process.terminate()  # Only the exact child started above.
                try:
                    process.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise RuntimeError("Runtime shutdown timed out; forced kill is not a clean stop")
        if process.returncode != 0:
            raise RuntimeError(f"Runtime shutdown failed: {process.returncode}")
    # The API routes uvicorn's logs to its profile after bootstrap. Retain them
    # with the smoke evidence and require actual graceful shutdown and cleanup.
    api_log = (profile / "data/logs/vool_api.log").read_text()
    with (scratch / "runtime.log").open("a") as log:
        log.write(api_log)
    if "Application shutdown complete." not in api_log:
        raise RuntimeError("Runtime did not prove graceful application shutdown")
    if (profile / "data/vool_api.pid").exists():
        raise RuntimeError("Runtime shutdown left its pidfile")
    try:
        fetch(origin, "/healthz")
    except (OSError, urllib.error.URLError):
        pass
    else:
        raise RuntimeError("Listener survived runtime shutdown")
    subprocess.run(["bash", str(current / "uninstall.sh")], env=env, check=True)
    if binary.exists() or binary.is_symlink() or (home / ".local/share/applications/vool.desktop").exists():
        raise RuntimeError("Uninstall left package entrypoints")
    if preserved.read_text() != "existing runtime data\n":
        raise RuntimeError("Uninstall damaged runtime data")
    print(f"PASS: installed tarball, exact head {expected}, real chat answer, shutdown, uninstall, profile preservation")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--scratch", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    args = parser.parse_args()
    smoke(args.archive.resolve(), args.scratch.resolve(), args.expected_commit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
