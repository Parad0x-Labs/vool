"""Stop helpers for the Mac/source uninstaller (installer/uninstall_vool_local.sh).

The dev/source uninstall used to ``pkill -f`` anything whose command line named
VOOL's server, which can kill an unrelated process (an editor or test runner
holding that string in its arguments) and never waited for exit. This module
gives the script the same rules the Linux package uninstall applies
(installer/bundle/linux/install_bundle.py): a process is stopped only when it is
PROVEN ours -- the runtime's own pidfile AND the command line names VOOL's server
AND its working folder is this install -- with a bounded wait and a plain refusal
when it will not die. The inspection helpers are reused from the Linux module so
both platforms agree on what "proven" means.

The llama.cpp specialist lane keeps no pidfile of its own (it is spawned
detached by the launcher), so it is proven by its whole command line instead:
run with this install's virtualenv (or from this install's folder) AND serving a
model file from this runtime's model folder. A process matching only the module
name is never touched.

Never signalled: pid 0/1, this process, a pid the pidfile names but the command
line or working folder does not prove, and anything that cannot be inspected
(that case refuses the uninstall instead of proceeding unproven).
"""
from __future__ import annotations

import argparse
import os
import signal
import subprocess
import sys
from pathlib import Path

if __package__ in (None, ""):  # run as a script by the uninstaller, not -m
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from installer.bundle.linux.install_bundle import (
    CANNOT_STOP_MESSAGE,
    KILL_WAIT_SECONDS,
    PIDFILE_RELATIVE,
    TERM_WAIT_SECONDS,
    _pid_has_exited,
    _wait_until_exited,
    process_cwd,
)

SERVER_ARG = "-m apps.vool_api_server"
LLAMACPP_MODULE = "llama_cpp.server"
# ps(1) folds long argument lists unless told not to; a checkout path with a few
# directories already crosses the default width, which once hid the server arg.
PS_ARGS = ["ps", "-ww", "-p", "{pid}", "-o", "args="]
PS_ALL_ARGS = ["ps", "-ww", "-eo", "pid=,args="]


def _ps_args(pid: int) -> list[str]:
    return [part.format(pid=pid) for part in PS_ARGS]


def _run(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)


def owned_server_pid(runtime_home: Path, install_root: Path) -> int:
    """This install's own API server pid, proven the way the updater proves it.

    The pidfile alone proves nothing (numbers get reused): the command line must
    be VOOL's server (read whole, ``ps -ww``) and the working folder must be this
    install's root -- exactly the source-install rule core/updater/macos.py
    applies (``expected_cwd = root`` for a non-.app install). Anything else
    returns 0 and is never signalled. A live pidfile number that cannot be
    inspected raises: the caller must refuse, never proceed unproven.
    """
    pidfile = Path(runtime_home) / PIDFILE_RELATIVE
    try:
        pid = int(pidfile.read_text(encoding="utf-8").strip() or "0")
    except (OSError, ValueError):
        return 0
    if pid <= 1 or pid == os.getpid():
        return 0
    if _pid_has_exited(pid):
        return 0  # already gone, or an unreaped zombie: nothing to stop
    try:
        command = _run(_ps_args(pid))
    except Exception as exc:
        raise OSError(f"could not check whether VOOL is running: {exc}") from exc
    if command.returncode or SERVER_ARG not in command.stdout:
        return 0  # no such process (stale number) or not VOOL's server
    try:
        cwd = process_cwd(pid)
    except OSError as exc:
        raise OSError(f"could not prove the running VOOL belongs to this install: {exc}") from exc
    if cwd is None:
        raise OSError("could not prove the running VOOL belongs to this install")
    return pid if cwd == Path(install_root).resolve() else 0


def _has_module_arg(arguments: str, module: str) -> bool:
    parts = arguments.split()
    return any(part == "-m" and parts[index + 1] == module for index, part in enumerate(parts[:-1]))


def owned_llamacpp_pids(install_root: Path, models_dir: Path) -> list[int]:
    """The llama.cpp servers this install spawned, proven by their whole command line.

    The launcher starts them with this checkout's virtualenv from this checkout's
    folder, serving a model file from this runtime's model folder. All three must
    hold; a same-named server owned by someone else (different folder, different
    model) is never touched.
    """
    root = Path(install_root).resolve()
    venv_bin = (root / ".venv/bin").resolve()
    models = Path(models_dir).resolve()
    try:
        listing = _run(PS_ALL_ARGS)
    except Exception:
        return []  # nothing can be proven ours, so nothing is signalled
    pids: list[int] = []
    for line in listing.stdout.splitlines():
        line = line.strip()
        if not line or LLAMACPP_MODULE not in line:
            continue
        try:
            pid_text, _, arguments = line.partition(" ")
            pid = int(pid_text)
        except ValueError:
            continue
        if pid <= 1 or pid == os.getpid() or _pid_has_exited(pid):
            continue
        if not _has_module_arg(arguments, LLAMACPP_MODULE):
            continue
        if str(models) not in arguments:
            continue  # not serving one of this runtime's model files
        try:
            executable = Path(arguments.split()[0]).resolve()
            cwd = process_cwd(pid)
        except (OSError, IndexError):
            continue
        if executable.is_relative_to(venv_bin) or cwd == root:
            pids.append(pid)
    return pids


def _stop_pid(pid: int, *, term_wait: float, kill_wait: float,
              label: str) -> tuple[bool, str | None]:
    # PermissionError means the process exists but is not ours to signal: that is
    # ALIVE, never stopped -- treating it as gone would remove the install around
    # a live server (the same law the Linux uninstall fixed).
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return True, None  # exited between the check and the signal
    except PermissionError:
        print(f"Could not stop {label} (process {pid}).", flush=True)
        return False, CANNOT_STOP_MESSAGE
    if _wait_until_exited(pid, term_wait):
        return True, None
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        return True, None
    except PermissionError:
        print(f"Could not stop {label} (process {pid}).", flush=True)
        return False, CANNOT_STOP_MESSAGE
    if _wait_until_exited(pid, kill_wait):
        return True, None
    print(f"Could not stop {label} (process {pid}).", flush=True)
    return False, CANNOT_STOP_MESSAGE


def stop_runtime(runtime_home: Path, install_root: Path, models_dir: Path, *,
                 term_wait: float = TERM_WAIT_SECONDS,
                 kill_wait: float = KILL_WAIT_SECONDS) -> tuple[bool, str | None]:
    """Stop this install's own server and llama.cpp lanes before files go away.

    Returns (True, None) when nothing ours is running or everything stopped, or
    (False, plain refusal message) when a proven-ours process would not die --
    the caller must then remove nothing.
    """
    pid = owned_server_pid(runtime_home, install_root)
    if pid > 0:
        stopped, refusal = _stop_pid(pid, term_wait=term_wait, kill_wait=kill_wait,
                                     label="the running VOOL")
        if not stopped:
            return False, refusal
    for lane in owned_llamacpp_pids(install_root, models_dir):
        stopped, refusal = _stop_pid(lane, term_wait=term_wait, kill_wait=kill_wait,
                                     label="a VOOL llama.cpp server")
        if not stopped:
            return False, refusal
    return True, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stop VOOL processes proven to belong to one install.")
    parser.add_argument("action", choices=("stop",))
    parser.add_argument("--runtime-home", type=Path, required=True)
    parser.add_argument("--install-root", type=Path, required=True)
    parser.add_argument("--llamacpp-models-dir", type=Path, required=True)
    parser.add_argument("--term-wait", type=float, default=TERM_WAIT_SECONDS)
    parser.add_argument("--kill-wait", type=float, default=KILL_WAIT_SECONDS)
    args = parser.parse_args(argv)
    try:
        stopped, refusal = stop_runtime(
            args.runtime_home, args.install_root, args.llamacpp_models_dir,
            term_wait=args.term_wait, kill_wait=args.kill_wait,
        )
    except OSError as exc:
        print(f"VOOL could not check whether it is still running ({exc}).", flush=True)
        print(CANNOT_STOP_MESSAGE, flush=True)
        return 3
    if not stopped:
        print(refusal or CANNOT_STOP_MESSAGE, flush=True)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
