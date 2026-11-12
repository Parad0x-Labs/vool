"""A real VOOL daemon for My routines: its own VOOL home and its own person's home folder.

The person's Desktop, Downloads and Documents live under a temporary home (HOME points there), so
the routine's safe places are real folders this test owns. No model, no network, no other account.
"""
from __future__ import annotations

import contextlib
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


@contextlib.contextmanager
def served(root: Path, *, extra_env: dict[str, str] | None = None):
    person = root / "person"
    for name in ("Desktop", "Downloads", "Documents"):
        (person / name).mkdir(parents=True, exist_ok=True)
    vool_home = root / "vool"
    vool_home.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, HOME=str(person), VOOL_HOME=str(vool_home), VOOL_UNATTENDED="1", VOOL_CREDENTIAL_STORE="vault",
               VOOL_KEY_STORAGE_MODE="file", VOOL_LOCAL_MODELS_ENABLED="0", VOOL_SKIP_PROVIDER_PREWARM="1",
               VOOL_REGISTER_INSTALLED_OLLAMA_MODELS="0", VOOL_DISABLE_MESH_DAEMON="1", VOOL_DISABLE_COMPUTE_MODE="1",
               VOOL_DISABLE_STUN="1", VOOL_INSTALL_PROFILE="local-only", VOOL_READER_TOOLS_DIR=str(vool_home / "reader_tools"))
    # No default model endpoint may reach a local model service on this machine.
    env.update(OLLAMA_HOST=f"http://127.0.0.1:{port}", VOOL_OLLAMA_URL=f"http://127.0.0.1:{port}")
    env.update(extra_env or {})
    env.pop("PYTEST_CURRENT_TEST", None)
    log = (root / "daemon.log").open("wb")
    process = subprocess.Popen([sys.executable, "-m", "apps.vool_api_server", "--bind", "127.0.0.1", "--port", str(port)],
                               env=env, stdout=log, stderr=subprocess.STDOUT)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(900):
            if process.poll() is not None:
                raise RuntimeError("the daemon exited; see " + str(root / "daemon.log"))
            try:
                with urlopen(base + "/healthz", timeout=1) as res:
                    if res.status == 200:
                        break
            except (URLError, TimeoutError, ConnectionError):
                time.sleep(0.1)
        else:
            raise RuntimeError("the daemon did not start within 90 seconds")
        yield base, person, vool_home
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()  # only this recorded child
            process.wait(timeout=10)
        log.close()


def call(base: str, path: str, body: dict | None = None, headers: dict[str, str] | None = None):
    """GET (no body) or JSON POST to the daemon; (status, parsed JSON or raw bytes, headers)."""
    data = None if body is None else json.dumps(body).encode()
    request = Request(base + path, data=data, headers={"Content-Type": "application/json", **(headers or {})})
    try:
        response = urlopen(request, timeout=60)
    except HTTPError as exc:
        response = exc
    with response:
        raw = response.read()
        kind = response.headers.get("Content-Type", "")
        return response.status, json.loads(raw) if "json" in kind else raw, response.headers


def tree(folder: Path) -> dict[str, bytes]:
    """Every file under ``folder`` with its bytes: what a step left on disk."""
    return {path.relative_to(folder).as_posix(): path.read_bytes() for path in sorted(folder.rglob("*")) if path.is_file()}
