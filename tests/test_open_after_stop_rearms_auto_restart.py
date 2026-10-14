"""Opening VOOL after a "Stop VOOL" re-arms auto-restart; the watchdog never undoes a Stop.

"Stop VOOL" (installer/vool_stop.py) disarms the keep-alive supervisor -- the Windows
VOOL_Daemon task, the macOS ai.vool.runtime KeepAlive agent, the Linux vool-runtime.service
unit -- and writes the .vool_stopped marker. Nothing called enable_watchdog or
clear_stopped_marker, so one Stop left auto-restart off forever (Windows also across
logons) and the marker never went away.

The fix is rearm_after_open: the Open_Chat/Open_Web0 launchers -- the only surfaces a user
opens VOOL through -- call it, and it re-arms what Stop disarmed plus clears the marker.
Pinned here, per platform, with the injected runner like tests/test_vool_stop.py:

  * Stop then Open runs the exact enable/bootstrap command and removes the marker;
  * Open with no earlier Stop runs no command at all (nothing extra that could fail);
  * the watchdog's own restart path (Start_VOOL.sh / vool_background) never calls the
    re-arm or touches the marker, so a Stop stays a Stop until the user opens VOOL.
"""
from __future__ import annotations

import re
import sys
import types
from pathlib import Path

import pytest

from installer import vool_stop
from installer.vool_stop import STOPPED_MARKER, hard_stop, hard_stop_posix, rearm_after_open

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = PROJECT_ROOT / "installer" / "install_vool.sh"


class _FakeWinRun:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, cmd, capture_output=False, text=False, check=False):
        self.calls.append(list(cmd))
        return types.SimpleNamespace(stdout="", returncode=0)

    def joined(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


class _FakePosixRun:
    """``systemctl --user list-unit-files`` reports ``systemd_units``; everything else is recorded."""

    def __init__(self, systemd_units=None) -> None:
        self.calls: list[list[str]] = []
        self.systemd_units = systemd_units or []

    def __call__(self, cmd, capture_output=False, text=False, check=False):
        self.calls.append(list(cmd))
        out = ""
        if cmd and cmd[0] == "systemctl" and "list-unit-files" in cmd:
            out = "\n".join(self.systemd_units)
        return types.SimpleNamespace(stdout=out, returncode=0)

    def joined(self) -> list[str]:
        return [" ".join(str(x) for x in c) for c in self.calls]


# --- Stop then Open re-arms, per platform -----------------------------------


def test_stop_then_open_re_enables_the_windows_watchdog_task(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    stop_run = _FakeWinRun()
    hard_stop(tmp_path, None, run=stop_run)
    assert (tmp_path / STOPPED_MARKER).exists()
    assert any("schtasks /change /tn VOOL_Daemon /disable" in c for c in stop_run.joined())

    open_run = _FakeWinRun()
    result = rearm_after_open(tmp_path, run=open_run)
    cmds = open_run.joined()
    assert any("schtasks /change /tn VOOL_Daemon /enable" in c for c in cmds), (
        "an open must re-enable the task the Stop disabled, before the launcher's /run"
    )
    assert not (tmp_path / STOPPED_MARKER).exists()
    assert result == {
        "stopped": True,
        "watchdog_rearmed": True,
        "supervisor_started": False,  # enabling does not start; the launcher's /run does
        "marker_cleared": True,
    }


def test_stop_then_open_bootstraps_the_macos_keepalive_agent_again(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    fake_uid = 501
    monkeypatch.setattr(vool_stop.os, "getuid", lambda: fake_uid, raising=False)
    plist = tmp_path / "ai.vool.runtime.plist"
    plist.write_text("x", encoding="utf-8")
    monkeypatch.setattr(vool_stop, "launch_agent_plist_path", lambda: plist)

    stop_run = _FakePosixRun()
    hard_stop_posix(tmp_path, None, run=stop_run, sleep=lambda _s: None)
    assert (tmp_path / STOPPED_MARKER).exists()
    assert any(f"launchctl bootout gui/{fake_uid} {plist}" in c for c in stop_run.joined())

    open_run = _FakePosixRun()
    result = rearm_after_open(tmp_path, run=open_run)
    cmds = open_run.joined()
    assert any(f"launchctl bootstrap gui/{fake_uid} {plist}" in c for c in cmds), (
        "an open must bootstrap the agent the Stop booted out, so KeepAlive returns"
    )
    assert not any("bootout" in c for c in cmds), "re-arming must not disturb a loaded agent"
    assert not (tmp_path / STOPPED_MARKER).exists()
    assert result["stopped"] is True
    assert result["watchdog_rearmed"] is True
    assert result["supervisor_started"] is True, "RunAtLoad starts the agent on bootstrap"


def test_stop_then_open_re_enables_the_linux_systemd_unit_without_starting_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(vool_stop.shutil, "which", lambda _n: "/usr/bin/systemctl")

    stop_run = _FakePosixRun(systemd_units=["vool-runtime.service enabled"])
    hard_stop_posix(tmp_path, None, run=stop_run, sleep=lambda _s: None)
    assert (tmp_path / STOPPED_MARKER).exists()
    assert any("systemctl --user disable vool-runtime.service" in c for c in stop_run.joined())

    open_run = _FakePosixRun(systemd_units=["vool-runtime.service disabled"])
    result = rearm_after_open(tmp_path, run=open_run)
    cmds = open_run.joined()
    assert any("systemctl --user enable vool-runtime.service" in c for c in cmds), (
        "an open must re-enable the unit the Stop disabled, so auto-restart returns at login"
    )
    assert not any("systemctl --user start" in c for c in cmds), (
        "re-arming must not start a second API next to the one the launcher starts"
    )
    assert not (tmp_path / STOPPED_MARKER).exists()
    assert result["stopped"] is True
    assert result["watchdog_rearmed"] is True
    assert result["supervisor_started"] is False


def test_open_with_no_earlier_stop_runs_nothing_at_all(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "darwin")
    plist = tmp_path / "ai.vool.runtime.plist"
    plist.write_text("x", encoding="utf-8")
    monkeypatch.setattr(vool_stop, "launch_agent_plist_path", lambda: plist)

    run = _FakePosixRun()
    result = rearm_after_open(tmp_path, run=run)
    assert run.calls == [], "no Stop -> no marker -> an open must run nothing that could fail"
    assert result == {
        "stopped": False,
        "watchdog_rearmed": False,
        "supervisor_started": False,
        "marker_cleared": False,
    }


def test_rearm_cli_prints_the_supervisor_started_token(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    (tmp_path / STOPPED_MARKER).write_text("stopped\n", encoding="utf-8")
    run = _FakeWinRun()
    monkeypatch.setattr(vool_stop.subprocess, "run", run)

    assert vool_stop.main(["--rearm", "--project-root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "supervisor_started=no"
    assert any("schtasks /change /tn VOOL_Daemon /enable" in c for c in run.joined())
    assert not (tmp_path / STOPPED_MARKER).exists()


# --- the watchdog restart path must not clear a Stop -------------------------


def _launcher_heredoc(fn_name: str) -> str:
    """The single quoted LAUNCHER_HEAD heredoc a write_*_launcher function emits, verbatim."""
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index(f"{fn_name}() {{")
    m = re.search(r"<<'LAUNCHER_HEAD'\n(.*?)\nLAUNCHER_HEAD\n", text[start:], re.S)
    assert m, f"LAUNCHER_HEAD heredoc not found in {fn_name}"
    return m.group(1)


def _start_vool_sh_body() -> str:
    """The generated Start_VOOL.sh (what launchd/systemd/XDG autostart all run), like
    tests/test_start_launcher_honors_port.py extracts it."""
    text = INSTALLER.read_text(encoding="utf-8")
    head = re.search(r"<<'LAUNCHER_HEAD'\n(.*?)\nLAUNCHER_HEAD\n", text, re.S)
    assert head, "LAUNCHER_HEAD heredoc not found in write_launcher"
    tail = re.search(r'cat >>"\$\{target_path\}" <<EOF\n(.*?)\nEOF\n', text[head.end():], re.S)
    assert tail, "the launcher tail heredoc not found in write_launcher"
    return head.group(1) + "\n" + tail.group(1).replace("\\$", "$")


def test_the_watchdog_restart_path_never_calls_the_rearm_or_touches_the_marker() -> None:
    # Start_VOOL.sh is what the macOS launchd agent, the Linux systemd unit and the XDG
    # autostart entry all (re)run: it is the watchdog's own restart path, so it must not
    # re-arm anything or clear the stop marker.
    body = _start_vool_sh_body()
    assert "vool_stop" not in body, "the supervised launcher must not invoke the stop/re-arm tool"
    assert ".vool_stopped" not in body, "the watchdog restart loop must leave a Stop in place"

    vbs = (PROJECT_ROOT / "vool_background.vbs").read_text(encoding="utf-8")
    cmd = (PROJECT_ROOT / "vool_background.cmd").read_text(encoding="utf-8")
    for name, text in (("vool_background.vbs", vbs), ("vool_background.cmd", cmd)):
        assert ".vool_stopped" not in text, f"{name} is the Windows watchdog loop; it must not clear a Stop"
        assert "vool_stop" not in text


@pytest.mark.parametrize("bat_name", ["Open_Chat.bat", "Open_Web0.bat"])
def test_windows_open_launchers_rearm_before_the_watchdog_run(bat_name: str) -> None:
    launcher = (PROJECT_ROOT / bat_name).read_text(encoding="utf-8")
    rearm_idx = launcher.index("-m installer.vool_stop --rearm")
    assert 'if exist "%SCRIPT_ROOT%\\.vool_stopped" (' in launcher, (
        "the re-arm must be gated on the stop marker, so a normal open runs nothing extra"
    )
    run_idx = launcher.index('schtasks /run /tn "VOOL_Daemon"')
    assert rearm_idx < run_idx, "the task must be re-enabled before the launcher /runs it"
    # the start fallback contract is unchanged
    assert "vool_background.vbs" in launcher


@pytest.mark.parametrize("fn_name", ["write_chat_page_launcher", "write_web0_launcher"])
def test_posix_open_launchers_call_the_rearm_and_yield_the_port_to_a_restarted_supervisor(fn_name: str) -> None:
    body = _launcher_heredoc(fn_name)
    assert '[[ -f "${PROJECT_ROOT}/.vool_stopped" ]]' in body, (
        "the re-arm must be gated on the stop marker, so a normal open runs nothing extra"
    )
    assert '-m installer.vool_stop --rearm --project-root "${PROJECT_ROOT}"' in body
    # When the re-arm started the keep-alive supervisor (macOS bootstrap, RunAtLoad),
    # the launcher must wait for that supervisor's API before starting a second one.
    supervisor_wait_idx = body.index('*supervisor_started=yes*')
    nohup_idx = body.index("nohup bash \"${PROJECT_ROOT}/Start_VOOL.sh\"")
    assert supervisor_wait_idx < nohup_idx
    # the start fallback contract is unchanged
    assert "nohup bash \"${PROJECT_ROOT}/Start_VOOL.sh\"" in body
