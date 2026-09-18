"""Owned native-runtime lifecycle: identity, readiness, stale rejection, and teardown."""

from __future__ import annotations

from unittest import mock

import pytest

from installer.bundle.native_runtime_supervisor import NativeRuntimeError, NativeRuntimeSupervisor

SHA = "a" * 40
OTHER_SHA = "b" * 40


def _health(sha: str = SHA, *, pid: int = 4123, agent: str = "Atlas") -> dict:
    return {
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
    supervisor._health = mock.Mock(return_value=_health())
    identity = supervisor.ensure_ready()
    assert identity["commit_full"] == SHA
    assert supervisor.attached_existing is True
    assert supervisor.owns_runtime is False
    supervisor._popen.assert_not_called()


def test_bundle_mode_replaces_even_a_matching_runtime_to_make_teardown_owned(tmp_path, monkeypatch) -> None:
    child = _Child(pid=9000)
    supervisor = _supervisor(tmp_path, child=child, require_owned=True)
    supervisor._health = mock.Mock(side_effect=[_health(SHA, pid=7000), None, _health(SHA, pid=9000)])
    kill = mock.Mock()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.kill", kill)
    identity = supervisor.ensure_ready()
    kill.assert_called_once_with(7000, __import__("signal").SIGTERM)
    assert identity["pid"] == 9000
    assert supervisor.owns_runtime is True


def test_missing_runtime_is_spawned_and_gated_on_exact_health_identity(tmp_path) -> None:
    child = _Child()
    supervisor = _supervisor(tmp_path, child=child)
    supervisor._health = mock.Mock(side_effect=[None, None, _health(pid=child.pid)])
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


def test_recognized_stale_runtime_is_stopped_before_the_exact_child_starts(tmp_path, monkeypatch) -> None:
    child = _Child(pid=9002)
    supervisor = _supervisor(tmp_path, child=child)
    supervisor._health = mock.Mock(side_effect=[_health(OTHER_SHA, pid=7123), None, _health(SHA, pid=9002)])
    kill = mock.Mock()
    monkeypatch.setattr("installer.bundle.native_runtime_supervisor.os.kill", kill)
    identity = supervisor.ensure_ready()
    kill.assert_called_once_with(7123, __import__("signal").SIGTERM)
    assert identity["commit_full"] == SHA
    assert supervisor.owns_runtime is True


def test_spawned_identity_mismatch_is_red_and_child_is_torn_down(tmp_path, monkeypatch) -> None:
    child = _Child(pid=9003)
    supervisor = _supervisor(tmp_path, child=child)
    supervisor._health = mock.Mock(side_effect=[None, _health(OTHER_SHA, pid=9003)])
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
    supervisor._health = mock.Mock(side_effect=[None, _health(SHA, pid=9004)])
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
