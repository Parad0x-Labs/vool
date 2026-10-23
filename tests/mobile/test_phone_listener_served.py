"""The real desktop router and the separate phone socket, with no model calls."""
from __future__ import annotations

import json
import socket
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest
import uvicorn


def http(base, path, body=None, headers=None):
    request = Request(base + path, data=None if body is None else json.dumps(body).encode(),
                      headers={"Content-Type": "application/json", **(headers or {})})
    try:
        response = urlopen(request, timeout=20)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read()
        return response.status, json.loads(raw) if raw else {}


@pytest.fixture
def desktop(tmp_path, monkeypatch, request):
    if getattr(request, "param", None) == "full":
        yield from process_desktop(tmp_path)
        return
    from apps.vool_api_server import create_app
    from core import runtime_paths
    from core.runtime_paths import configure_runtime_home
    from core.web.api.mobile_companion_api import reset_mobile_companion_service
    from core.web.api.runtime import RuntimeServices

    previous = runtime_paths._VOOL_HOME_OVERRIDE
    monkeypatch.setenv("VOOL_HOME", str(tmp_path))
    configure_runtime_home(tmp_path)
    reset_mobile_companion_service()
    runtime = RuntimeServices()
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(create_app(runtime), log_level="error", lifespan="off"))
    thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started, "desktop server did not start"
    try:
        yield f"http://127.0.0.1:{port}", runtime, tmp_path
    finally:
        listener = getattr(runtime, "phone_listener", None)
        if listener is not None:
            listener.close()
        server.should_exit = True
        thread.join(timeout=5)
        sock.close()
        reset_mobile_companion_service()
        configure_runtime_home(previous)
        assert not thread.is_alive()


def process_desktop(home):
    import os
    import subprocess
    import sys
    from pathlib import Path
    from types import SimpleNamespace

    sockets = [socket.socket(), socket.socket()]
    for sock in sockets:
        sock.bind(("127.0.0.1", 0))
    desktop_port, phone_port = [sock.getsockname()[1] for sock in sockets]
    for sock in sockets:
        sock.close()
    settings = home / "data/mobile_companion/listener.json"
    settings.parent.mkdir(parents=True)
    # Fixture setup only: normal-person Settings integration is blocked by
    # registry claims. This starts the actual daemon with its saved listener.
    settings.write_text(json.dumps({"version": 1, "enabled": True, "address": "127.0.0.1", "port": phone_port}))
    os.chmod(settings, 0o600)
    env = {**os.environ, "VOOL_HOME": str(home), "VOOL_TEST_MODE": "1", "VOOL_UNATTENDED": "1",
           "VOOL_LOCAL_MODELS_ENABLED": "0", "VOOL_ALLOWED_HOSTS": "127.0.0.1,localhost"}
    with (home / "daemon.log").open("wb") as log:
        process = subprocess.Popen([sys.executable, "-m", "apps.vool_api_server", "--port", str(desktop_port), "--bind", "127.0.0.1"],
                                   cwd=Path(__file__).resolve().parents[2], env=env, stdout=log, stderr=subprocess.STDOUT)
        (home / "process.json").write_text(json.dumps({"pid": process.pid, "desktop_port": desktop_port, "phone_port": phone_port}))
        base = f"http://127.0.0.1:{desktop_port}"
        try:
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                try:
                    if http(base, "/healthz")[0] == 200:
                        break
                except (URLError, ConnectionError, TimeoutError):
                    pass
                assert process.poll() is None, (home / "daemon.log").read_text()
                time.sleep(0.05)
            else:
                pytest.fail("Fresh daemon did not start: " + (home / "daemon.log").read_text())
            yield base, SimpleNamespace(phone_connection={"port": phone_port, "address": "127.0.0.1"}), home
        finally:
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()  # this exact child, recorded above; never by name
                process.wait(timeout=10)
            (home / "process.json").write_text(json.dumps({"pid": process.pid, "desktop_port": desktop_port,
                                                           "phone_port": phone_port, "exit": process.returncode}))


def test_fresh_off_and_upgrade_ignore_legacy_bind(desktop, monkeypatch):
    from core.mobile_companion.listener import listener_for, load_settings

    _base, runtime, home = desktop
    monkeypatch.setenv("VOOL_ALLOWED_HOSTS", "*")
    monkeypatch.setenv("VOOL_MOBILE_ADVERTISE_HOST", "192.168.1.30:9999")
    value = load_settings()
    assert value["enabled"] is False
    assert not listener_for(runtime).running
    assert not (home / "data/mobile_companion/listener.json").exists()


def enable(runtime, address="127.0.0.1"):
    if getattr(runtime, "phone_connection", None):
        return runtime.phone_connection
    from core.mobile_companion.listener import listener_for

    return listener_for(runtime).configure(enabled=True, address=address)


@pytest.mark.parametrize("path", [
    "/api/chat", "/api/commands", "/api/wallet/status", "/api/wallet/approve",
    "/api/settings/credentials", "/api/settings/prefs", "/settings",
    "/api/mobile/settings", "/api/mobile/pairing/start", "/api/mobile/pairing/page",
    "/api/mobile/devices", "/api/mobile/devices/revoke", "/api/mobile/receipts",
    "/api/mobile/companion/../devices", "/api/mobile/%64evices", "/healthz",
])
def test_only_closed_companion_routes_answer_even_with_spoofed_host(desktop, path):
    base, runtime, _home = desktop
    value = enable(runtime)
    phone = f"http://127.0.0.1:{value['port']}"
    assert runtime.phone_listener.server is not None
    for body in (None, {}):
        status, refused = http(phone, path, body, {"Host": "localhost"})
        assert status == 404, (path, status, refused)


def test_pairing_routes_and_switch_off_close_socket(desktop):
    base, runtime, _home = desktop
    value = enable(runtime)
    phone = f"http://127.0.0.1:{value['port']}"
    assert http(phone, "/api/mobile/info")[0] == 200
    status, pairing = http(base, "/api/mobile/pairing/start", {})
    assert status == 200, pairing
    assert f":{value['port']}?" in pairing["qr_uri"]
    assert http(phone, "/api/mobile/pairing/challenge?pairing_id=" + pairing["pairing_id"])[0] == 200
    assert http(phone, "/api/mobile/pairing/claim", {})[0] == 400
    assert http(phone, "/api/mobile/info", headers={"Origin": base})[0] == 403
    runtime.phone_listener.configure(enabled=False)
    assert runtime.phone_listener.server is None
    with pytest.raises((URLError, ConnectionError)):
        http(phone, "/api/mobile/info")


@pytest.mark.parametrize("address", ["0.0.0.0", "8.8.8.8", "224.0.0.1", "::", "invalid"])
def test_network_choice_never_widens_to_public_or_all_interfaces(desktop, address):
    base, runtime, _home = desktop
    with pytest.raises((ValueError, OSError)):
        enable(runtime, address)
    assert runtime.phone_listener.server is None
