"""Owned subprocess-group lifecycle for verification children."""

from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from typing import Any

import psutil


def process_group_kwargs() -> dict[str, Any]:
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP}
    return {"start_new_session": True}


def ensure_process_group_owner() -> None:
    """Make this coordinator the owner of the kill domain inherited by its children."""

    if os.name == "nt":
        return
    if os.getsid(0) != os.getpid() and os.getpgrp() == os.getpid():
        raise RuntimeError("verification coordinator is not in an isolated session")
    if os.getsid(0) != os.getpid():
        os.setsid()
    if os.getsid(0) != os.getpid() or os.getpgrp() != os.getpid():
        raise RuntimeError("verification coordinator could not own its process group")


def stop_owned_process_group_children(*, grace_seconds: float = 5.0) -> None:
    """Reap every other process in the coordinator's owned POSIX process group."""

    if os.name == "nt":
        return
    owner = os.getpid()
    if os.getpgrp() != owner:
        raise RuntimeError("refusing to clean a process group this process does not own")

    deadline = time.monotonic() + max(0.0, float(grace_seconds))
    while time.monotonic() < deadline:
        members = _owned_group_member_pids(owner)
        if not members:
            return
        for pid in members:
            with contextlib.suppress(ProcessLookupError, PermissionError):
                os.kill(pid, signal.SIGTERM)
        time.sleep(0.02)
    for pid in _owned_group_member_pids(owner):
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.kill(pid, signal.SIGKILL)


def _owned_group_member_pids(owner: int) -> tuple[int, ...]:
    members: list[int] = []
    for process in psutil.process_iter(attrs=("pid",)):
        pid = int(process.info["pid"])
        if pid <= 0 or pid == owner:
            continue
        try:
            if os.getpgid(pid) == owner:
                members.append(pid)
        except (ProcessLookupError, PermissionError, psutil.Error):
            continue
    return tuple(members)


def stop_process_tree(process: Any, *, grace_seconds: float = 5.0) -> None:
    """Terminate, then kill, the complete process group owned by ``process``."""

    pid = int(getattr(process, "pid", 0) or 0)
    if not pid:
        if process.poll() is None:
            with contextlib.suppress(Exception):
                process.terminate()
        try:
            process.wait(timeout=grace_seconds)
            return
        except Exception:
            pass
        with contextlib.suppress(Exception):
            process.kill()
        with contextlib.suppress(Exception):
            process.wait(timeout=grace_seconds)
        return
    if os.name == "nt":
        if process.poll() is None:
            with contextlib.suppress(Exception):
                process.send_signal(signal.CTRL_BREAK_EVENT)
            with contextlib.suppress(Exception):
                process.wait(timeout=grace_seconds)
        if pid:
            with contextlib.suppress(Exception):
                subprocess.run(
                    ("taskkill", "/PID", str(pid), "/T", "/F"),
                    capture_output=True,
                    check=False,
                    timeout=grace_seconds,
                )
        with contextlib.suppress(Exception):
            process.kill()
        with contextlib.suppress(Exception):
            process.wait(timeout=grace_seconds)
        return

    if pid:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGTERM)
    with contextlib.suppress(Exception):
        process.wait(timeout=grace_seconds)
    if pid:
        try:
            os.killpg(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pid, signal.SIGKILL)
    with contextlib.suppress(Exception):
        process.kill()
    with contextlib.suppress(Exception):
        process.wait(timeout=grace_seconds)
