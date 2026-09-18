from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import BinaryIO

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NO_WINDOW = 0x08000000


def _wrap_command_for_windows(command: list[str]) -> list[str]:
    if os.name != "nt" or not command:
        return command
    # Use os.path (string-only) rather than Path(command[0]).suffix here. When a
    # POSIX test fakes os.name="nt", pathlib.Path resolves to WindowsPath, which
    # raises NotImplementedError on Python <=3.11 - crashing the test (and pytest's
    # failure rendering) instead of exercising the wrap logic. splitext is flavour-
    # agnostic and gives the identical suffix on a real Windows host.
    suffix = os.path.splitext(command[0])[1].lower()
    if suffix not in {".bat", ".cmd"}:
        return command
    return [os.environ.get("COMSPEC") or "cmd.exe", "/c", *command]


def _creationflags(*, include_breakaway: bool) -> int:
    if os.name != "nt":
        return 0
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
    if include_breakaway:
        flags |= CREATE_BREAKAWAY_FROM_JOB
    return flags


def _fallback_log_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.{os.getpid()}.{int(time.time() * 1000)}{path.suffix}")


def _open_append_log(path: str) -> BinaryIO:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        return target.open("ab")
    except PermissionError:
        fallback = _fallback_log_path(target)
        fallback.parent.mkdir(parents=True, exist_ok=True)
        return fallback.open("ab")


def start_detached(*, command: list[str], cwd: str, stdout_path: str, stderr_path: str) -> int:
    if not command:
        raise ValueError("no command provided")

    wrapped = _wrap_command_for_windows(command)
    with _open_append_log(stdout_path) as stdout_file, _open_append_log(stderr_path) as stderr_file:
        kwargs = {
            "cwd": cwd,
            "stdin": subprocess.DEVNULL,
            "stdout": stdout_file,
            "stderr": stderr_file,
            "close_fds": True,
            "env": os.environ.copy(),
        }
        startupinfo_factory = getattr(subprocess, "STARTUPINFO", None)
        if os.name == "nt" and startupinfo_factory is not None:
            startupinfo = startupinfo_factory()
            startupinfo.dwFlags |= getattr(subprocess, "STARTF_USESHOWWINDOW", 1)
            startupinfo.wShowWindow = 0
            kwargs["startupinfo"] = startupinfo
        try:
            process = subprocess.Popen(
                wrapped,
                creationflags=_creationflags(include_breakaway=True),
                **kwargs,
            )
        except OSError as exc:
            if os.name != "nt" or getattr(exc, "winerror", None) != 5:
                raise
            process = subprocess.Popen(
                wrapped,
                creationflags=_creationflags(include_breakaway=False),
                **kwargs,
            )
    print(process.pid)
    return process.pid


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="start_windows_detached")
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--stdout", required=True)
    parser.add_argument("--stderr", required=True)
    args, command = parser.parse_known_args(raw_args)
    if command and command[0] == "--":
        command = command[1:]
    try:
        start_detached(command=command, cwd=args.cwd, stdout_path=args.stdout, stderr_path=args.stderr)
    except Exception as exc:
        print(f"ERROR: could not start detached process: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
