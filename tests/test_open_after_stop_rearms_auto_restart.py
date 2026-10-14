"""Opening VOOL after a "Stop VOOL" re-arms auto-restart; the watchdog never undoes a Stop.

"Stop VOOL" (installer/vool_stop.py) disarms the keep-alive supervisor -- the Windows
VOOL_Daemon task, the macOS ai.vool.runtime KeepAlive agent, the Linux vool-runtime.service
unit -- and writes the .vool_stopped marker. Nothing called enable_watchdog or
clear_stopped_marker, so one Stop left auto-restart off for good and the marker never went away.

The fix is rearm_after_open: the Open_Chat/Open_Web0 launchers -- the surfaces a user opens
VOOL through -- call it while the marker is present, and it re-arms what Stop disarmed and
clears the marker. Pinned here:

  * per platform with the injected runner: Stop then Open runs the exact enable/bootstrap
    command and removes the marker; a failed re-arm keeps the marker and reports nothing
    started; Open with no earlier Stop runs no command at all;
  * on this Linux host, the generated Open_Chat.sh / Open_Web0.sh are executed for real
    (fake systemctl/curl/xdg-open on PATH, a project path with spaces): Stop then Open
    re-enables and starts the unit and never starts a second API next to it; the failure
    path falls back to the plain start and keeps the Stop marker;
  * the watchdog's own restart path (Start_VOOL.sh / vool_background) never calls the
    re-arm or touches the marker, so a Stop stays a Stop until the user opens VOOL.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import textwrap
import types
from pathlib import Path

import pytest

from installer import vool_stop
from installer.vool_stop import STOPPED_MARKER, hard_stop, hard_stop_posix, rearm_after_open

PROJECT_ROOT = Path(__file__).resolve().parent.parent
INSTALLER = PROJECT_ROOT / "installer" / "install_vool.sh"


class _FakeRun:
    """Records every command; ``fail`` lists command prefixes that exit 1."""

    def __init__(self, *, systemd_units=None, fail=()) -> None:
        self.calls: list[list[str]] = []
        self.systemd_units = systemd_units or []
        self.fail = [tuple(f) for f in fail]

    def __call__(self, cmd, capture_output=False, text=False, check=False):
        cmd = [str(c) for c in cmd]
        self.calls.append(cmd)
        out = ""
        if cmd[:1] == ["systemctl"] and "list-unit-files" in cmd:
            out = "\n".join(self.systemd_units)
        rc = 1 if any(tuple(cmd[: len(f)]) == f for f in self.fail) else 0
        return types.SimpleNamespace(stdout=out, returncode=rc)

    def joined(self) -> list[str]:
        return [" ".join(c) for c in self.calls]


def _macos(monkeypatch, tmp_path) -> Path:
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(vool_stop.os, "getuid", lambda: 501, raising=False)
    plist = tmp_path / "ai.vool.runtime.plist"
    plist.write_text("x", encoding="utf-8")
    monkeypatch.setattr(vool_stop, "launch_agent_plist_path", lambda: plist)
    return plist


# --- Stop then Open re-arms, per platform -----------------------------------


def test_stop_then_open_re_enables_the_windows_watchdog_task(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    stop_run = _FakeRun()
    hard_stop(tmp_path, None, run=stop_run)
    assert (tmp_path / STOPPED_MARKER).exists()
    assert "schtasks /change /tn VOOL_Daemon /disable" in stop_run.joined()

    open_run = _FakeRun()
    result = rearm_after_open(tmp_path, run=open_run)
    assert open_run.joined() == [
        "schtasks /query /tn VOOL_Daemon",
        "schtasks /change /tn VOOL_Daemon /enable",
    ]
    assert not (tmp_path / STOPPED_MARKER).exists()
    assert result == {
        "stopped": True,
        "watchdog_rearmed": True,
        "supervisor_started": False,  # enabling does not start; the launcher's /run does
        "marker_cleared": True,
    }


def test_windows_failed_enable_keeps_the_stop_marker_so_the_next_open_retries(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    (tmp_path / STOPPED_MARKER).write_text("stopped\n", encoding="utf-8")
    run = _FakeRun(fail=[("schtasks", "/change")])
    result = rearm_after_open(tmp_path, run=run)
    assert (tmp_path / STOPPED_MARKER).exists()
    assert result["watchdog_rearmed"] is False
    assert result["marker_cleared"] is False


def test_windows_without_a_watchdog_task_just_clears_the_marker(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    (tmp_path / STOPPED_MARKER).write_text("stopped\n", encoding="utf-8")
    run = _FakeRun(fail=[("schtasks", "/query")])
    result = rearm_after_open(tmp_path, run=run)
    assert run.joined() == ["schtasks /query /tn VOOL_Daemon"], "no task -> nothing to enable"
    assert not (tmp_path / STOPPED_MARKER).exists()
    assert result["watchdog_rearmed"] is False


def test_stop_then_open_bootstraps_the_macos_keepalive_agent_again(tmp_path, monkeypatch) -> None:
    plist = _macos(monkeypatch, tmp_path)
    stop_run = _FakeRun()
    hard_stop_posix(tmp_path, None, run=stop_run, sleep=lambda _s: None)
    assert (tmp_path / STOPPED_MARKER).exists()
    assert f"launchctl bootout gui/501 {plist}" in stop_run.joined()

    open_run = _FakeRun()
    result = rearm_after_open(tmp_path, run=open_run)
    assert open_run.joined() == [f"launchctl bootstrap gui/501 {plist}"]
    assert not (tmp_path / STOPPED_MARKER).exists()
    assert result == {
        "stopped": True,
        "watchdog_rearmed": True,
        "supervisor_started": True,  # RunAtLoad starts the agent on bootstrap
        "marker_cleared": True,
    }


def test_macos_agent_already_loaded_counts_as_rearmed(tmp_path, monkeypatch) -> None:
    # After a later login RunAtLoad loaded the agent again while the marker stayed; bootstrap
    # then refuses ("already loaded") and launchd itself confirms the job is there.
    plist = _macos(monkeypatch, tmp_path)
    (tmp_path / STOPPED_MARKER).write_text("stopped\n", encoding="utf-8")
    run = _FakeRun(fail=[("launchctl", "bootstrap")])
    result = rearm_after_open(tmp_path, run=run)
    assert run.joined() == [
        f"launchctl bootstrap gui/501 {plist}",
        f"launchctl load -w {plist}",
        "launchctl print gui/501/ai.vool.runtime",
    ]
    assert result["watchdog_rearmed"] is True
    assert not (tmp_path / STOPPED_MARKER).exists()


def test_macos_failed_bootstrap_fails_safe(tmp_path, monkeypatch) -> None:
    _macos(monkeypatch, tmp_path)
    (tmp_path / STOPPED_MARKER).write_text("stopped\n", encoding="utf-8")
    run = _FakeRun(fail=[("launchctl", "bootstrap"), ("launchctl", "load"), ("launchctl", "print")])
    result = rearm_after_open(tmp_path, run=run)
    assert result["supervisor_started"] is False, "the launcher must then start VOOL itself"
    assert result["watchdog_rearmed"] is False
    assert (tmp_path / STOPPED_MARKER).exists(), "a failed re-arm keeps the Stop for the next open"


def test_stop_then_open_re_enables_and_starts_the_linux_systemd_unit(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(vool_stop.shutil, "which", lambda _n: "/usr/bin/systemctl")

    stop_run = _FakeRun(systemd_units=["vool-runtime.service enabled"])
    hard_stop_posix(tmp_path, None, run=stop_run, sleep=lambda _s: None)
    assert (tmp_path / STOPPED_MARKER).exists()
    assert "systemctl --user stop vool-runtime.service" in stop_run.joined()
    assert "systemctl --user disable vool-runtime.service" in stop_run.joined()

    open_run = _FakeRun(systemd_units=["vool-runtime.service disabled"])
    result = rearm_after_open(tmp_path, run=open_run)
    assert open_run.joined() == [
        "systemctl --user list-unit-files vool-runtime.service",
        "systemctl --user enable --now vool-runtime.service",
    ]
    assert not (tmp_path / STOPPED_MARKER).exists()
    assert result["watchdog_rearmed"] is True
    assert result["supervisor_started"] is True


@pytest.mark.parametrize("platform", ["win32", "darwin", "linux"])
def test_open_with_no_earlier_stop_runs_nothing_at_all(tmp_path, monkeypatch, platform: str) -> None:
    if platform == "darwin":
        _macos(monkeypatch, tmp_path)
    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.setattr(vool_stop.shutil, "which", lambda _n: "/usr/bin/systemctl")
    run = _FakeRun(systemd_units=["vool-runtime.service enabled"])
    result = rearm_after_open(tmp_path, run=run)
    assert run.calls == [], "no Stop -> an open must run nothing that could fail"
    assert result["stopped"] is False


def test_rearm_cli_prints_the_supervisor_started_token(tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(sys, "platform", "win32")
    (tmp_path / STOPPED_MARKER).write_text("stopped\n", encoding="utf-8")
    run = _FakeRun()
    monkeypatch.setattr(vool_stop.subprocess, "run", run)

    assert vool_stop.main(["--rearm", "--project-root", str(tmp_path)]) == 0
    assert capsys.readouterr().out.strip() == "supervisor_started=no"
    assert "schtasks /change /tn VOOL_Daemon /enable" in run.joined()
    assert not (tmp_path / STOPPED_MARKER).exists()


# --- the generated POSIX launchers, executed ---------------------------------


def _launcher_heredoc(fn_name: str) -> str:
    """The single quoted LAUNCHER_HEAD heredoc a write_*_launcher function emits, verbatim."""
    text = INSTALLER.read_text(encoding="utf-8")
    start = text.index(f"{fn_name}() {{")
    m = re.search(r"<<'LAUNCHER_HEAD'\n(.*?)\nLAUNCHER_HEAD\n", text[start:], re.S)
    assert m, f"LAUNCHER_HEAD heredoc not found in {fn_name}"
    return m.group(1) + "\n"


def _exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + textwrap.dedent(body), encoding="utf-8")
    path.chmod(0o755)


def _install(tmp_path: Path, launcher_fn: str, *, enable_fails: bool = False) -> dict[str, Path]:
    """A minimal install in a path with spaces: the generated launcher, the real
    installer/ and core/ packages, a .venv python, and fake system tools on PATH."""
    root = tmp_path / "My VOOL"
    (root / ".venv" / "bin").mkdir(parents=True)
    for pkg in ("installer", "core"):
        (root / pkg).symlink_to(PROJECT_ROOT / pkg, target_is_directory=True)
    _exe(root / ".venv" / "bin" / "python", f'exec "{sys.executable}" "$@"\n')
    launcher = root / "Open.sh"
    launcher.write_text(_launcher_heredoc(launcher_fn), encoding="utf-8")
    launcher.chmod(0o755)

    state = tmp_path / "state"
    state.mkdir()
    log = state / "calls.log"
    healthy = state / "healthy"
    # Start_VOOL.sh is the plain start the launcher falls back to.
    _exe(root / "Start_VOOL.sh", f'echo start_vool >>"{log}"\ntouch "{healthy}"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    enable_rc = "1" if enable_fails else "0"
    _exe(bin_dir / "systemctl", f"""\
        echo "systemctl $*" >>"{log}"
        case "$*" in
          *list-unit-files*) echo "vool-runtime.service disabled enabled" ;;
          *"enable --now"*) [ {enable_rc} = 0 ] && touch "{healthy}"; exit {enable_rc} ;;
        esac
        exit 0
        """)
    _exe(bin_dir / "curl", f'[ -f "{healthy}" ]\n')
    _exe(bin_dir / "xdg-open", f'echo "xdg-open $*" >>"{log}"\n')
    _exe(bin_dir / "lsof", "exit 0\n")
    return {"root": root, "launcher": launcher, "log": log, "healthy": healthy, "bin": bin_dir}


def _env(inst: dict[str, Path], tmp_path: Path) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["PATH"] = f"{inst['bin']}:/usr/bin:/bin"
    env["HOME"] = str(tmp_path / "home")
    env["VOOL_HOME"] = str(tmp_path / "vool-home")
    return env


def _calls(inst: dict[str, Path]) -> list[str]:
    return inst["log"].read_text(encoding="utf-8").splitlines() if inst["log"].exists() else []


def _run_launcher(inst: dict[str, Path], env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(inst["launcher"])], env=env, capture_output=True,
                          text=True, timeout=60, check=False)


_linux_only = pytest.mark.skipif(not sys.platform.startswith("linux") or shutil.which("bash") is None,
                                 reason="executes the generated Linux launcher")
_LAUNCHERS = [("write_chat_page_launcher", "/chat"), ("write_web0_launcher", "/web0")]


@_linux_only
@pytest.mark.parametrize(("launcher_fn", "page"), _LAUNCHERS)
def test_real_stop_then_open_on_linux_rearms_the_unit_and_starts_no_second_api(
    tmp_path, launcher_fn: str, page: str
) -> None:
    inst = _install(tmp_path, launcher_fn)
    env = _env(inst, tmp_path)
    # The real Stop, as Stop_VOOL.sh runs it.
    stop = subprocess.run([str(inst["root"] / ".venv" / "bin" / "python"), "-m", "installer.vool_stop",
                           "--project-root", str(inst["root"])], cwd=inst["root"], env=env,
                          capture_output=True, text=True, timeout=60, check=False)
    assert stop.returncode == 0, stop.stderr
    assert (inst["root"] / STOPPED_MARKER).exists()
    assert "systemctl --user disable vool-runtime.service" in _calls(inst)
    inst["log"].unlink()

    opened = _run_launcher(inst, env)
    assert opened.returncode == 0, opened.stderr
    calls = _calls(inst)
    assert "systemctl --user enable --now vool-runtime.service" in calls
    assert "start_vool" not in calls, "the re-armed unit starts the API; the launcher must not start a second"
    assert f"xdg-open http://127.0.0.1:11435{page}" in calls
    assert not (inst["root"] / STOPPED_MARKER).exists()

    # The next open is an ordinary open: no re-arm command runs again.
    inst["log"].unlink()
    assert _run_launcher(inst, env).returncode == 0
    assert not any(c.startswith("systemctl") for c in _calls(inst))


@_linux_only
@pytest.mark.parametrize(("launcher_fn", "page"), _LAUNCHERS)
def test_linux_open_with_failing_rearm_still_opens_vool_and_keeps_the_stop(
    tmp_path, launcher_fn: str, page: str
) -> None:
    inst = _install(tmp_path, launcher_fn, enable_fails=True)
    (inst["root"] / STOPPED_MARKER).write_text("stopped\n", encoding="utf-8")
    opened = _run_launcher(inst, _env(inst, tmp_path))
    assert opened.returncode == 0, opened.stderr
    calls = _calls(inst)
    assert "systemctl --user enable --now vool-runtime.service" in calls
    assert "start_vool" in calls, "a failed re-arm falls back to the plain start"
    assert f"xdg-open http://127.0.0.1:11435{page}" in calls
    assert (inst["root"] / STOPPED_MARKER).exists(), "the next open must try the re-arm again"


@_linux_only
def test_linux_open_while_vool_already_runs_arms_nothing(tmp_path) -> None:
    # Started by hand after a Stop: arming the unit now would make it fight the running API.
    inst = _install(tmp_path, "write_chat_page_launcher")
    (inst["root"] / STOPPED_MARKER).write_text("stopped\n", encoding="utf-8")
    inst["healthy"].touch()
    assert _run_launcher(inst, _env(inst, tmp_path)).returncode == 0
    calls = _calls(inst)
    assert not any(c.startswith("systemctl") for c in calls)
    assert "start_vool" not in calls
    assert "xdg-open http://127.0.0.1:11435/chat" in calls


@_linux_only
def test_linux_open_with_no_earlier_stop_starts_vool_as_before(tmp_path) -> None:
    inst = _install(tmp_path, "write_chat_page_launcher")
    assert _run_launcher(inst, _env(inst, tmp_path)).returncode == 0
    calls = _calls(inst)
    assert not any(c.startswith("systemctl") for c in calls), "no Stop -> no re-arm command"
    assert "start_vool" in calls
    assert "xdg-open http://127.0.0.1:11435/chat" in calls


# --- the watchdog restart path must not clear a Stop -------------------------


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
    # re-arm anything or clear the stop marker. vool_background.vbs/.cmd are the Windows
    # watchdog loop the VOOL_Daemon logon task runs; it restarts Start_VOOL.bat.
    body = _start_vool_sh_body()
    assert "vool_stop" not in body, "the supervised launcher must not invoke the stop/re-arm tool"
    assert ".vool_stopped" not in body, "the watchdog restart loop must leave a Stop in place"
    for name in ("vool_background.vbs", "vool_background.cmd", "Start_VOOL.bat"):
        text = (PROJECT_ROOT / name).read_text(encoding="utf-8")
        assert ".vool_stopped" not in text, f"{name} is on the watchdog path; it must not clear a Stop"
        assert "vool_stop" not in text


@pytest.mark.parametrize("bat_name", ["Open_Chat.bat", "Open_Web0.bat"])
def test_windows_open_launchers_rearm_with_the_venv_python_before_the_watchdog_run(bat_name: str) -> None:
    # .bat files cannot run here; pin the exact call the launcher makes and its order.
    launcher = (PROJECT_ROOT / bat_name).read_text(encoding="utf-8").replace("\r\n", "\n")
    block = (
        'if exist "%SCRIPT_ROOT%\\.vool_stopped" (\n'
        '  if exist "%SCRIPT_ROOT%\\.venv\\Scripts\\python.exe" (\n'
        '    set "PYTHONPATH=%SCRIPT_ROOT%"\n'
        '    "%SCRIPT_ROOT%\\.venv\\Scripts\\python.exe" -m installer.vool_stop --rearm'
        ' --project-root "%SCRIPT_ROOT%" >nul 2>&1\n'
        '  )\n'
        ')\n'
    )
    assert block in launcher, "the re-arm must be marker-gated and use the install's venv python"
    assert launcher.index(block) < launcher.index('schtasks /run /tn "VOOL_Daemon"'), (
        "the task must be re-enabled before the launcher /runs it"
    )
    assert "vool_background.vbs" in launcher, "the start fallback stays"
