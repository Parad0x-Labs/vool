"""A real daemon and owner door, without models or external accounts."""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest


@pytest.fixture(scope="module")
def daemon(tmp_path_factory):
    home = tmp_path_factory.mktemp("my-things-served")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, VOOL_HOME=str(home), VOOL_UNATTENDED="1", VOOL_CREDENTIAL_STORE="vault", VOOL_KEY_STORAGE_MODE="file", VOOL_LOCAL_MODELS_ENABLED="0", VOOL_SKIP_PROVIDER_PREWARM="1", VOOL_REGISTER_INSTALLED_OLLAMA_MODELS="0", VOOL_DISABLE_MESH_DAEMON="1", VOOL_DISABLE_COMPUTE_MODE="1", VOOL_DISABLE_STUN="1", VOOL_INSTALL_PROFILE="local-only", VOOL_READER_TOOLS_DIR=str(home / "reader_tools"))
    # No default model endpoint may touch an operator's local model service.
    env.update(OLLAMA_HOST=f"http://127.0.0.1:{port}", VOOL_OLLAMA_URL=f"http://127.0.0.1:{port}")
    env.pop("PYTEST_CURRENT_TEST", None)
    log = (home / "daemon.log").open("wb")
    process = subprocess.Popen([sys.executable, "-m", "apps.vool_api_server", "--bind", "127.0.0.1", "--port", str(port)], env=env, stdout=log, stderr=subprocess.STDOUT)
    process_log = os.environ.get("VOOL_THINGS_PROCESS_LOG")
    def record(state):
        if process_log:
            with Path(process_log).open("a") as handle:
                handle.write(json.dumps({"pid": process.pid, "port": port, "state": state}) + "\n")
    record("started")
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(600):
            if process.poll() is not None:
                pytest.fail("daemon exited; see isolated daemon log")
            try:
                with urlopen(base + "/healthz", timeout=1) as res:
                    if res.status == 200:
                        break
            except (URLError, TimeoutError):
                time.sleep(.1)
        else:
            pytest.fail("daemon did not start within 60 seconds")
        yield base, home
    finally:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()  # this exact recorded child only
            process.wait(timeout=10)
        record("stopped")
        log.close()


def call(base, action="", body=None, headers=None):
    request = Request(base + "/api/my-things" + ("/" + action if action else ""), data=None if body is None else json.dumps(body).encode(), headers={"Content-Type": "application/json", **(headers or {})})
    try:
        response = urlopen(request, timeout=60)
    except HTTPError as exc:
        response = exc
    with response:
        data = response.read()
        return response.status, json.loads(data) if "json" in response.headers.get("Content-Type", "") else data, response.headers


def file(name, text, kind="receipt"):
    return {"name": name, "data": base64.b64encode(text if isinstance(text, bytes) else text.encode()).decode(), "kind": kind}


def test_served_add_proposal_confirmation_edit_attach_export_and_delete(daemon):
    base, home = daemon
    status, proposal, _ = call(base, "propose", {"file": file("receipt.txt", "Item: Headphones\nModel: WH-1000\nBought on: 2026-03-02\nBought from: Example shop\nWarranty end: 2027-03-02")})
    assert status == 200 and proposal["requires_confirmation"] is True
    assert proposal["fields"]["model"] == "WH-1000"
    assert call(base)[1]["things"] == []
    assert call(base, "save", {"fields": proposal["fields"]})[0] == 409
    status, saved, _ = call(base, "save", {"fields": proposal["fields"], "confirm": True, "file": file("receipt.txt", "Synthetic receipt MARBLE-33913")})
    assert status == 200, saved
    item = saved["thing"]
    status, attached, _ = call(base, "attach", {"id": item["id"], "revision": 1, "file": file("manual.txt", "User instructions", "manual")})
    assert status == 200 and len(attached["thing"]["files"]) == 2
    assert call(base, "save", {"id": item["id"], "revision": 1, "fields": {"name": "Stale"}, "confirm": True})[0] == 409
    status, raw, headers = call(base, "export", {})
    assert status == 200 and headers["Cache-Control"] == "no-store"
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert b"Synthetic receipt MARBLE-33913" in [archive.read(n) for n in archive.namelist()]
    assert call(base, "delete", {"id": item["id"], "revision": 2})[0] == 409
    assert call(base, "delete", {"id": item["id"], "revision": 2, "confirm": True})[0] == 200
    assert call(base)[1]["things"] == []
    objects = home / "data" / "my_things" / "objects"
    assert not [p for p in objects.rglob("*") if p.is_file()]
    assert all(b"MARBLE-33913" not in p.read_bytes() for p in home.rglob("*") if p.is_file())


def test_served_owner_guards_and_bad_upload(daemon):
    base, _ = daemon
    assert call(base, "save", {"fields": {"name": "Intruder"}, "confirm": True}, {"Origin": "http://127.0.0.1:1"})[0] == 403
    assert call(base, "save", {}, {"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call(base, headers={"Sec-Fetch-Site": "cross-site"})[0] == 403
    assert call(base, "save", {}, {"Content-Type": "text/plain"})[0] == 415
    assert call(base, "attach", {"file": {"name": "bad", "kind": "photo", "data": "broken!"}})[0] == 400
    assert call(base, "preferences", {"enabled": "false"})[0] == 400
    assert call(base, "manual", {"id": "a" * 32, "url": "https://example.invalid/manual.pdf", "confirm": True})[0] == 409
    assert call(base)[1]["things"] == []


def test_served_settings_off_refuses_then_recovers(daemon):
    base, _ = daemon
    assert call(base, "preferences", {"enabled": False})[0] == 200
    assert call(base)[0] == 409
    assert call(base, "save", {"fields": {"name": "No"}, "confirm": True})[0] == 409
    assert call(base, "preferences", {"enabled": True})[0] == 200
    assert call(base)[1]["things"] == []
