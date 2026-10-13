"""Owned native-runtime lifecycle: identity, readiness, stale rejection, and teardown."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from pathlib import Path
from unittest import mock

import pytest

from core.runtime_paths import runtime_home_identity
from installer.bundle.native_runtime_supervisor import NativeRuntimeError, NativeRuntimeSupervisor

SHA = "a" * 40
OTHER_SHA = "b" * 40


def _health(sha: str = SHA, *, pid: int = 4123, agent: str = "Atlas", home: Path | None = None) -> dict:
    payload = {
        "ok": True,
        "agent": agent,
        "daemon": True,
        "runtime": {
            "commit": sha[:12],
            "commit_full": sha,
            "pid": pid,
            "build_id": f"test+{sha[:12]}",
            "protocol_version": 1,
            "release_version": "0.5.0-closed-test",
            "workstation_version": "vool-workstation-test-v1",
        },
    }
    if home is not None:
        payload["runtime"]["runtime_home_id"] = runtime_home_identity({"VOOL_HOME": str(home)})
    return payload


class _Child:
    def __init__(self, *, pid: int = 9001, returncode=None) -> None:
        self.pid = pid
        self.returncode = returncode
        self.waits: list[int] = []
        self.terminated = 0
        self.killed = 0

    def poll(self):
        return self.returncode

    def wait(self, timeout: int):
        self.waits.append(timeout)
        self.returncode = 0
        return 0

    def terminate(self):
        self.terminated += 1

    def kill(self):
        self.killed += 1


def _supervisor(
    tmp_path, *, child: _Child | None = None, require_owned: bool = True
) -> NativeRuntimeSupervisor:
    starter = tmp_path / "Start_VOOL.sh"
    starter.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    fake_child = child or _Child()
    return NativeRuntimeSupervisor(
        tmp_path,
        ["bash", str(starter)],
        expected_sha=SHA,
        env={"VOOL_HOME": str(tmp_path / "home")},
        require_owned=require_owned,
        startup_timeout=1,
        poll_interval=0,
        popen=mock.Mock(return_value=fake_child),
        sleep=lambda _: None,
    )


def test_matching_existing_runtime_is_attached_without_claiming_ownership(tmp_path) -> None:
    supervisor = _supervisor(tmp_path, require_owned=False)
    supervisor._health = mock.Mock(return_value=_health(home=tmp_path / "home"))
    identity = supervisor.ensure_ready()
    assert identity["commit_full"] == SHA
    assert supervisor.attached_existing is True
    assert supervisor.owns_runtime is False
    supervisor._popen.assert_not_called()


def test_bundle_mode_never_replaces_a_matching_preexisting_runtime(tmp_path, monkeypatch) -> None:
    child = _Child(pid=9000)
    supervisor = _supervisor(tmp_path, child=child, require_owned=True)
    supervisor._health = mock.Mock(return_value=_health(SHA, pid=7000, home=tmp_path / "home"))
    kill = mock.Mock()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.kill", kill)
    with pytest.raises(NativeRuntimeError, match="already occupied"):
        supervisor.ensure_ready()
    kill.assert_not_called()
    supervisor._popen.assert_not_called()
    assert supervisor.owns_runtime is False


def test_missing_runtime_is_spawned_and_gated_on_exact_health_identity(tmp_path) -> None:
    child = _Child()
    supervisor = _supervisor(tmp_path, child=child)
    supervisor._health = mock.Mock(side_effect=[None, None, _health(pid=child.pid, home=tmp_path / "home")])
    identity = supervisor.ensure_ready()
    assert identity["commit_full"] == SHA
    assert supervisor.owns_runtime is True
    call = supervisor._popen.call_args
    assert call.args[0][0] == "bash"
    assert call.kwargs["cwd"] == str(tmp_path.resolve())
    assert call.kwargs["start_new_session"] is True


def test_unidentifiable_port_occupant_fails_closed_and_is_never_killed(tmp_path, monkeypatch) -> None:
    supervisor = _supervisor(tmp_path)
    supervisor._health = mock.Mock(return_value={"ok": True, "runtime": {"commit_full": OTHER_SHA}})
    kill = mock.Mock()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.kill", kill)
    with pytest.raises(NativeRuntimeError, match="unidentifiable"):
        supervisor.ensure_ready()
    kill.assert_not_called()
    supervisor._popen.assert_not_called()


def test_mutable_agent_name_alone_cannot_spoof_the_stable_runtime_schema(tmp_path, monkeypatch) -> None:
    supervisor = _supervisor(tmp_path)
    supervisor._health = mock.Mock(
        return_value={"ok": True, "agent": "VOOL", "runtime": {"pid": 7122, "commit_full": OTHER_SHA}}
    )
    kill = mock.Mock()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.kill", kill)
    with pytest.raises(NativeRuntimeError, match="unidentifiable"):
        supervisor.ensure_ready()
    kill.assert_not_called()


def test_recognized_stale_runtime_cannot_be_replaced_even_in_the_same_profile(tmp_path, monkeypatch) -> None:
    child = _Child(pid=9002)
    supervisor = _supervisor(tmp_path, child=child)
    supervisor._health = mock.Mock(return_value=_health(OTHER_SHA, pid=7123, home=tmp_path / "home"))
    kill = mock.Mock()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.kill", kill)
    with pytest.raises(NativeRuntimeError, match="already occupied"):
        supervisor.ensure_ready()
    kill.assert_not_called()
    supervisor._popen.assert_not_called()
    assert supervisor.owns_runtime is False


def test_spawned_identity_mismatch_is_red_and_child_is_torn_down(tmp_path, monkeypatch) -> None:
    child = _Child(pid=9003)
    supervisor = _supervisor(tmp_path, child=child)
    supervisor._health = mock.Mock(side_effect=[None, _health(OTHER_SHA, pid=9003, home=tmp_path / "home")])
    killpg = mock.Mock()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.getpgid", lambda pid: pid)
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.killpg", killpg)
    with pytest.raises(NativeRuntimeError, match="identity mismatch"):
        supervisor.ensure_ready()
    killpg.assert_called_once()
    assert supervisor.owns_runtime is False


def test_owned_child_process_group_is_torn_down_on_native_quit(tmp_path, monkeypatch) -> None:
    child = _Child(pid=9004)
    supervisor = _supervisor(tmp_path, child=child)
    supervisor._health = mock.Mock(side_effect=[None, _health(SHA, pid=9004, home=tmp_path / "home")])
    supervisor.ensure_ready()
    killpg = mock.Mock()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.getpgid", lambda pid: pid)
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.killpg", killpg)
    supervisor.shutdown()
    killpg.assert_called_once_with(9004, __import__("signal").SIGTERM)
    assert child.waits == [8]
    assert supervisor.owns_runtime is False


def test_child_exit_before_readiness_fails_instead_of_opening_a_dead_window(tmp_path) -> None:
    child = _Child(returncode=17)
    supervisor = _supervisor(tmp_path, child=child)
    supervisor._health = mock.Mock(return_value=None)
    with pytest.raises(NativeRuntimeError, match="returncode=17"):
        supervisor.ensure_ready()
    assert supervisor.owns_runtime is False


def test_missing_expected_sha_and_missing_starter_each_fail_closed(tmp_path) -> None:
    no_sha = NativeRuntimeSupervisor(tmp_path, ["bash", str(tmp_path / "missing")], expected_sha="")
    with pytest.raises(NativeRuntimeError, match="expected source SHA"):
        no_sha.ensure_ready()

    missing = NativeRuntimeSupervisor(tmp_path, ["bash", str(tmp_path / "missing")], expected_sha=SHA)
    missing._health = mock.Mock(return_value=None)
    with pytest.raises(NativeRuntimeError, match="starter is missing"):
        missing.ensure_ready()


def test_self_contained_daemon_listens_on_the_port_the_window_will_look_at(tmp_path, monkeypatch) -> None:
    """A second bundle instance beside an operator's live window must own its own port: the daemon
    command follows the origin in VOOL_NATIVE_API_URL instead of the server's default, so the
    supervisor neither attaches to nor stops the runtime on 11435."""
    from installer.bundle.native_runtime_supervisor import NativeRuntimeSupervisor

    module = tmp_path / "installer" / "bundle" / "vool_window.py"
    module.parent.mkdir(parents=True)
    module.write_text("")
    monkeypatch.setenv("VOOL_RUNTIME_MODE", "self-contained")
    monkeypatch.setenv("VOOL_EXPECTED_COMMIT", "abc1234")
    monkeypatch.setenv("VOOL_NATIVE_API_URL", "http://127.0.0.1:11535")
    supervisor = NativeRuntimeSupervisor.from_environment(module_path=module)
    assert supervisor.command[-2:] == ["--port", "11535"]
    assert supervisor.command[1:3] == ["-m", "apps.vool_api_server"]
    assert supervisor.health_url == "http://127.0.0.1:11535/healthz"

    monkeypatch.delenv("VOOL_NATIVE_API_URL")
    default = NativeRuntimeSupervisor.from_environment(module_path=module)
    assert default.command[-2:] == ["--port", "11435"]
    assert default.health_url == "http://127.0.0.1:11435/healthz"

    monkeypatch.setenv("VOOL_RUNTIME_MODE", "wrapper")
    monkeypatch.setenv("VOOL_NATIVE_API_URL", "http://127.0.0.1:11535")
    wrapper = NativeRuntimeSupervisor.from_environment(module_path=module)
    assert "--port" not in wrapper.command  # the wrapper's starter script owns its own arguments


@pytest.mark.parametrize("require_owned", [True, False])
@pytest.mark.parametrize("kind", ["foreign-profile", "missing-profile"])
def test_same_commit_occupant_requires_profile_proof(tmp_path, monkeypatch, require_owned, kind) -> None:
    supervisor = _supervisor(tmp_path, require_owned=require_owned)
    home = tmp_path / "foreign" if kind == "foreign-profile" else None
    supervisor._health = mock.Mock(return_value=_health(home=home))
    kill = mock.Mock()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.kill", kill)
    with pytest.raises(NativeRuntimeError, match="another or unproved runtime profile"):
        supervisor.ensure_ready()
    assert supervisor.attached_existing is False
    assert supervisor.owns_runtime is False
    kill.assert_not_called()
    supervisor._popen.assert_not_called()


@pytest.mark.parametrize("kind", ["foreign-profile", "missing-profile", "wrong-pid"])
def test_spawned_readiness_requires_the_owned_child_and_profile(tmp_path, monkeypatch, kind) -> None:
    child = _Child(pid=9500)
    supervisor = _supervisor(tmp_path, child=child)
    home = None if kind == "missing-profile" else tmp_path / ("foreign" if kind == "foreign-profile" else "home")
    pid = child.pid + 1 if kind == "wrong-pid" else child.pid
    supervisor._health = mock.Mock(side_effect=[None, _health(pid=pid, home=home)])
    killpg = mock.Mock()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.getpgid", lambda own_pid: own_pid)
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.killpg", killpg)
    with pytest.raises(NativeRuntimeError, match="identity mismatch"):
        supervisor.ensure_ready()
    # Teardown is limited to the Popen child this supervisor created, never the served PID.
    killpg.assert_called_once_with(child.pid, __import__("signal").SIGTERM)
    assert supervisor.owns_runtime is False


def test_owned_watchdog_rejects_a_health_pid_changed_under_the_same_commit(tmp_path, monkeypatch) -> None:
    child = _Child(pid=9600)
    supervisor = _supervisor(tmp_path, child=child)
    supervisor._health = mock.Mock(side_effect=[None, _health(pid=child.pid, home=tmp_path / "home"),
                                               _health(pid=child.pid + 1, home=tmp_path / "home")])
    supervisor.ensure_ready()
    with pytest.raises(NativeRuntimeError, match="identity changed"):
        supervisor.assert_alive()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.getpgid", lambda own_pid: own_pid)
    killpg = mock.Mock()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.killpg", killpg)
    supervisor.shutdown()
    killpg.assert_called_once_with(child.pid, __import__("signal").SIGTERM)


_CONTROLLED_HTTP_SERVER = """
import json, os, sys, threading
from http.server import BaseHTTPRequestHandler, HTTPServer
payload = json.loads(sys.argv[1])
payload['runtime']['pid'] = os.getpid()
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if payload.get('redirect'):
            self.send_response(302); self.send_header('Location', payload['redirect'])
            self.end_headers(); return
        body = json.dumps(payload).encode()
        self.send_response(200); self.send_header('Content-Type', 'application/json')
        self.end_headers(); self.wfile.write(body)
server = HTTPServer(('127.0.0.1', 0), Handler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
print(server.server_port, flush=True)
sys.stdin.readline()
server.shutdown(); server.server_close(); thread.join()
"""


@pytest.mark.parametrize("require_owned", [True, False])
def test_real_http_foreign_profile_survives_native_refusal(tmp_path, require_owned) -> None:
    payload = _health(home=tmp_path / "foreign")
    occupant = subprocess.Popen([sys.executable, "-c", _CONTROLLED_HTTP_SERVER, json.dumps(payload)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        port = int(occupant.stdout.readline().strip())
        supervisor = _supervisor(tmp_path, require_owned=require_owned)
        supervisor.health_url = f"http://127.0.0.1:{port}/healthz"
        with pytest.raises(NativeRuntimeError, match="another or unproved runtime profile"):
            supervisor.ensure_ready()
        assert occupant.poll() is None
        assert supervisor._health()["runtime"]["pid"] == occupant.pid
        assert supervisor.attached_existing is False
        supervisor._popen.assert_not_called()
    finally:
        occupant.communicate(input="stop\n", timeout=10)
        assert occupant.returncode == 0


def test_real_owned_http_child_has_pid_profile_and_commit_proof(tmp_path) -> None:
    home = tmp_path / "home"
    payload = _health(home=home)
    source = _CONTROLLED_HTTP_SERVER.replace(
        "sys.stdin.readline()\nserver.shutdown(); server.server_close(); thread.join()", "thread.join()"
    )
    supervisor = NativeRuntimeSupervisor(
        tmp_path, [sys.executable, "-c", source, json.dumps(payload)], expected_sha=SHA,
        env={**os.environ, "VOOL_HOME": str(home)}, startup_timeout=10, poll_interval=0.01,
    )
    real_health = supervisor._health

    def owned_transport_health():
        if supervisor.process is None:
            return None
        try:
            line = (home / "logs" / "native-runtime-child.log").read_text().splitlines()[0]
            port = int(line)
        except (OSError, IndexError, ValueError):
            return None
        supervisor.health_url = f"http://127.0.0.1:{port}/healthz"
        return real_health()

    supervisor._health = owned_transport_health
    try:
        identity = supervisor.ensure_ready()
        child = supervisor.process
        assert identity["pid"] == child.pid
        assert identity["runtime_home_id"] == runtime_home_identity({"VOOL_HOME": str(home)})
        assert identity["commit_full"] == SHA
        assert child.poll() is None
        supervisor.assert_alive()
        (tmp_path / "owned-child-proof.json").write_text(json.dumps(identity, sort_keys=True))
    finally:
        supervisor.shutdown()
    assert child.poll() is not None
    assert supervisor.owns_runtime is False


@pytest.mark.parametrize("url", [
    "https://example.invalid/healthz",
    "http://localhost.example.invalid/healthz",
    "http://127.0.0.1.example.invalid/healthz",
    "http://user@127.0.0.1/healthz",
    "ftp://127.0.0.1/healthz",
    "file://localhost/healthz",
    "http://127.0.0.1:0/healthz",
    "http://127.0.0.1:99999/healthz",
    "http://127.0.0.1/healthz?query=value",
    "http://127.0.0.1/healthz#fragment",
])
def test_invalid_native_health_origin_refuses_before_opening_any_url(tmp_path, monkeypatch, url) -> None:
    opener = mock.Mock()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.urllib.request.build_opener", opener)
    with pytest.raises(NativeRuntimeError, match="direct loopback HTTP endpoint"):
        NativeRuntimeSupervisor(tmp_path, [], expected_sha=SHA, health_url=url)
    opener.assert_not_called()


@pytest.mark.parametrize("url", ["http://127.0.0.1:11535/healthz", "http://localhost:11535/healthz", "http://[::1]:11535/healthz"])
def test_valid_loopback_native_health_forms_remain_configurable(tmp_path, url) -> None:
    supervisor = NativeRuntimeSupervisor(tmp_path, [], expected_sha=SHA, health_url=url)
    assert supervisor.health_url == url


def test_native_health_redirect_refuses_without_dns_or_external_fetch(tmp_path, monkeypatch) -> None:
    payload = _health(home=tmp_path / "home")
    payload["redirect"] = "https://example.invalid/healthz"
    occupant = subprocess.Popen([sys.executable, "-c", _CONTROLLED_HTTP_SERVER, json.dumps(payload)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    original_dns = socket.getaddrinfo
    requested_hosts = []

    def local_dns_only(host, *args, **kwargs):
        requested_hosts.append(str(host))
        assert host in {"127.0.0.1", "localhost", "::1"}
        return original_dns(host, *args, **kwargs)

    monkeypatch.setattr(socket, "getaddrinfo", local_dns_only)
    try:
        port = int(occupant.stdout.readline().strip())
        supervisor = _supervisor(tmp_path, require_owned=False)
        supervisor.health_url = f"http://127.0.0.1:{port}/healthz"
        with pytest.raises(NativeRuntimeError, match="unidentifiable"):
            supervisor.ensure_ready()
        assert occupant.poll() is None
        assert "example.invalid" not in requested_hosts
        supervisor._popen.assert_not_called()
    finally:
        occupant.communicate(input="stop\n", timeout=10)
        assert occupant.returncode == 0


def test_native_health_ignores_ambient_network_proxies(tmp_path, monkeypatch) -> None:
    payload = _health(home=tmp_path / "home")
    occupant = subprocess.Popen([sys.executable, "-c", _CONTROLLED_HTTP_SERVER, json.dumps(payload)],
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    monkeypatch.setenv("HTTP_PROXY", "http://example.invalid:8080")
    monkeypatch.setenv("http_proxy", "http://example.invalid:8080")
    monkeypatch.setenv("NO_PROXY", "")
    monkeypatch.setenv("no_proxy", "")
    try:
        port = int(occupant.stdout.readline().strip())
        supervisor = _supervisor(tmp_path, require_owned=False)
        supervisor.health_url = f"http://127.0.0.1:{port}/healthz"
        identity = supervisor.ensure_ready()
        assert identity["pid"] == occupant.pid
        assert supervisor.attached_existing is True
        assert occupant.poll() is None
        supervisor._popen.assert_not_called()
    finally:
        occupant.communicate(input="stop\n", timeout=10)
        assert occupant.returncode == 0
