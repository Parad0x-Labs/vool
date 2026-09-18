"""The hard-stop sequence: disable the watchdog first, then kill gateway + API, write the marker.

Every process action goes through an injected runner, so these assert the exact command sequence
without killing anything real.
"""
from __future__ import annotations

import sys
import types

from installer import vool_stop
from installer.vool_stop import STOPPED_MARKER, hard_stop, kill_api_by_pidfile, kill_listener


class _FakeRun:
    def __init__(self, port_pids=None, python_pids=None) -> None:
        self.calls: list[list[str]] = []
        self.port_pids = port_pids or {}
        self.python_pids = python_pids or set()

    def __call__(self, cmd, capture_output=False, text=False, check=False):
        self.calls.append(list(cmd))
        out = ""
        joined = " ".join(cmd)
        if "Get-NetTCPConnection" in joined:
            for port, pid in self.port_pids.items():
                if f"-LocalPort {port} " in joined:
                    out = str(pid)
        elif cmd and cmd[0] == "tasklist":
            for tok in cmd:
                if tok.startswith("PID eq "):
                    pid = int(tok.split()[-1])
                    if pid in self.python_pids:
                        out = f'"python.exe","{pid}","Console","1","10,000 K"'
        return types.SimpleNamespace(stdout=out, returncode=0)

    def joined(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


def test_hard_stop_disables_watchdog_then_kills_gateway_and_api(tmp_path) -> None:
    run = _FakeRun(port_pids={11435: 4321, 18789: 8765})
    result = hard_stop(tmp_path, None, run=run)
    cmds = run.joined()

    assert any("schtasks /end /tn VOOL_Daemon" in c for c in cmds)
    assert any("schtasks /change /tn VOOL_Daemon /disable" in c for c in cmds)
    assert any("vool_background" in c for c in cmds)  # watchdog process kill
    assert any("taskkill /PID 8765 /T /F" in c for c in cmds)  # gateway
    assert any("taskkill /PID 4321 /T /F" in c for c in cmds)  # api

    # Ordering: the watchdog is disabled before the API is killed, so nothing respawns mid-stop.
    disable_idx = next(i for i, c in enumerate(cmds) if "/disable" in c)
    api_idx = next(i for i, c in enumerate(cmds) if "taskkill /PID 4321" in c)
    assert disable_idx < api_idx

    assert result["gateway_pid"] == 8765 and result["api_pid"] == 4321
    assert (tmp_path / STOPPED_MARKER).read_text(encoding="utf-8").strip() == "stopped"


def test_hard_stop_signals_bundle_supervisor_before_killing_api(tmp_path) -> None:
    home = tmp_path / "vool-home"
    run = _FakeRun(port_pids={11435: 4321})

    result = hard_stop(tmp_path, home, run=run)
    cmds = run.joined()

    assert result["bundle_supervisor_signaled"] is True
    assert (home / "run" / "stop").read_text(encoding="utf-8").strip() == "stop"
    assert any("taskkill /PID 4321 /T /F" in c for c in cmds)


def test_api_falls_back_to_pidfile_when_port_lookup_is_empty(tmp_path) -> None:
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "vool_api.pid").write_text("5555", encoding="utf-8")
    run = _FakeRun(port_pids={18789: 8765}, python_pids={5555})  # 11435 absent -> port lookup empty
    result = hard_stop(tmp_path, None, run=run)
    assert result["api_pid"] == 5555
    assert any("taskkill /PID 5555 /T /F" in c for c in run.joined())


def test_pidfile_kill_refuses_a_non_python_pid(tmp_path) -> None:
    pidfile = tmp_path / "data" / "vool_api.pid"
    pidfile.parent.mkdir()
    pidfile.write_text("9999", encoding="utf-8")
    run = _FakeRun(python_pids=set())  # tasklist reports 9999 is NOT python
    assert kill_api_by_pidfile(run, pidfile) is None
    assert not any("taskkill /PID 9999" in c for c in run.joined())


def test_kill_listener_returns_none_when_nothing_listens(tmp_path) -> None:
    run = _FakeRun(port_pids={})
    assert kill_listener(run, 18789) is None
    assert not any("taskkill" in c for c in run.joined())


# --- macOS / Linux path -----------------------------------------------------


class _FakePosixRun:
    """Fake runner for the POSIX path. ``lsof`` on a port returns ``port_pids`` the first time and
    ``survivors`` on the follow-up look-up; ``ps -p`` returns ``comm[pid]``."""

    def __init__(self, port_pids=None, survivors=None, comm=None, systemd_units=None) -> None:
        self.calls: list[list[str]] = []
        self.port_pids = port_pids or {}
        self.survivors = survivors or {}
        self.comm = comm or {}
        self.systemd_units = systemd_units or []  # unit names reported by `systemctl list-unit-files`
        self._lsof_seen: set[int] = set()

    def __call__(self, cmd, capture_output=False, text=False, check=False):
        self.calls.append(list(cmd))
        out = ""
        if cmd and cmd[0] == "lsof":
            port = int(cmd[2].split(":")[1])  # cmd[2] == "tcp:11435"
            if port not in self._lsof_seen:
                self._lsof_seen.add(port)
                out = "\n".join(str(p) for p in self.port_pids.get(port, []))
            else:
                out = "\n".join(str(p) for p in self.survivors.get(port, []))
        elif cmd and cmd[0] == "ps":
            out = self.comm.get(int(cmd[2]), "")  # cmd = ["ps","-p",str(pid),"-o","comm="]
        elif cmd and cmd[0] == "systemctl" and "list-unit-files" in cmd:
            out = "\n".join(self.systemd_units)
        return types.SimpleNamespace(stdout=out, returncode=0)

    def joined(self) -> list[str]:
        return [" ".join(str(x) for x in c) for c in self.calls]


def test_hard_stop_posix_boots_out_agent_then_kills_ports(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_uid = 501
    monkeypatch.setattr(vool_stop.os, "getuid", lambda: fake_uid, raising=False)
    plist = tmp_path / "ai.vool.runtime.plist"
    plist.write_text("x", encoding="utf-8")
    monkeypatch.setattr(vool_stop, "launch_agent_plist_path", lambda: plist)

    run = _FakePosixRun(port_pids={11435: [4321], 18789: [8765]})
    result = vool_stop.hard_stop_posix(tmp_path, None, run=run, sleep=lambda _s: None)
    cmds = run.joined()

    assert any(f"launchctl bootout gui/{fake_uid} {plist}" in c for c in cmds)
    assert any("kill -TERM 8765" in c for c in cmds)  # gateway
    assert any("kill -TERM 4321" in c for c in cmds)  # api

    # The KeepAlive agent is booted out before the API is killed, so nothing respawns mid-stop.
    bootout_idx = next(i for i, c in enumerate(cmds) if "bootout" in c)
    api_idx = next(i for i, c in enumerate(cmds) if "kill -TERM 4321" in c)
    assert bootout_idx < api_idx

    assert result["watchdog_disabled"] is True
    assert result["gateway_pid"] == 8765 and result["api_pid"] == 4321
    assert (tmp_path / STOPPED_MARKER).read_text(encoding="utf-8").strip() == "stopped"


def test_kill_listener_posix_sigkills_survivors(monkeypatch) -> None:
    run = _FakePosixRun(port_pids={11435: [4321]}, survivors={11435: [4321]})
    assert vool_stop.kill_listener_posix(run, 11435, sleep=lambda _s: None) == 4321
    cmds = run.joined()
    assert any("kill -TERM 4321" in c for c in cmds)  # graceful first
    assert any("kill -KILL 4321" in c for c in cmds)  # survivor hard-killed


def test_hard_stop_posix_falls_back_to_pidfile(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")  # non-darwin -> no launch agent
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "vool_api.pid").write_text("5555", encoding="utf-8")
    run = _FakePosixRun(port_pids={18789: [8765]}, comm={5555: "python3.12"})  # 11435 absent
    result = vool_stop.hard_stop_posix(tmp_path, None, run=run, sleep=lambda _s: None)
    assert result["api_pid"] == 5555
    assert any("kill -TERM 5555" in c for c in run.joined())


def test_pidfile_posix_refuses_a_non_python_pid(tmp_path) -> None:
    pidfile = tmp_path / "data" / "vool_api.pid"
    pidfile.parent.mkdir()
    pidfile.write_text("9999", encoding="utf-8")
    run = _FakePosixRun(comm={9999: "nginx"})  # ps reports 9999 is NOT python
    assert vool_stop.kill_api_by_pidfile_posix(run, pidfile) is None
    assert not any("kill -TERM 9999" in c for c in run.joined())


def test_disable_launch_agent_is_a_noop_off_darwin(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    run = _FakePosixRun()
    assert vool_stop.disable_launch_agent(run) is False
    assert run.calls == []


def test_hard_stop_posix_stops_the_systemd_service_on_linux(monkeypatch) -> None:
    # Linux keep-alive is Restart=always, so Stop MUST tear the unit down or it respawns in ~5s.
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(vool_stop.shutil, "which", lambda _n: "/usr/bin/systemctl")
    run = _FakePosixRun(port_pids={11435: [4321]}, systemd_units=["vool-runtime.service enabled"])
    result = vool_stop.hard_stop_posix("/tmp/nd", None, run=run, sleep=lambda _s: None)
    cmds = run.joined()
    assert any("systemctl --user stop vool-runtime.service" in c for c in cmds)
    assert any("systemctl --user disable vool-runtime.service" in c for c in cmds)
    # The unit is stopped BEFORE the port kill, so nothing respawns mid-stop.
    stop_idx = next(i for i, c in enumerate(cmds) if "systemctl --user stop" in c)
    kill_idx = next(i for i, c in enumerate(cmds) if "kill -TERM 4321" in c)
    assert stop_idx < kill_idx
    assert result["watchdog_disabled"] is True


def test_disable_systemd_service_noop_when_unit_absent(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(vool_stop.shutil, "which", lambda _n: "/usr/bin/systemctl")
    run = _FakePosixRun(systemd_units=[])  # VOOL's unit not installed on this box
    assert vool_stop.disable_systemd_service(run) is False
    assert not any("stop" in " ".join(c) for c in run.calls)


def test_disable_systemd_service_noop_without_systemctl(monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(vool_stop.shutil, "which", lambda _n: None)
    run = _FakePosixRun()
    assert vool_stop.disable_systemd_service(run) is False
    assert run.calls == []
