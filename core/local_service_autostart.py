"""On-demand starter for the local services a VOOL capability depends on (e.g. ComfyUI for image gen).

When a capability needs a local server that is installed but not running, VOOL starts it herself instead
of telling the user to do it by hand -- "if she needs it, she can bring it up". The design keeps this from
becoming arbitrary process execution:

* Only KNOWN services can be started. A caller builds a ``LocalService`` with a HARDCODED argv; the trigger
  (a chat message) never supplies any part of the command -- it only decides *whether* a known service is
  needed.
* ``preflight`` proves the thing is actually installed before spawning; a missing install fails soft.
* Single-flight: concurrent requests for the same service don't spawn duplicates -- the later ones wait for
  the first to become reachable.
* The child is detached (its own session) and its output goes to a log file, never into a chat reply.

This never installs or builds anything (the key-machine rule) -- it only starts already-installed software.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

# Services currently being started, so a second concurrent request waits instead of spawning a duplicate.
_STARTING: set[str] = set()
_LOCK = threading.Lock()
# The child each service was last started as, by THIS process. Stopping a service signals only this
# recorded process (and its own session's group), never whatever else matches a name or a port.
_SPAWNED: dict[str, object] = {}


@dataclass(frozen=True)
class LocalService:
    """A known local dependency VOOL may start on demand.

    ``reachable`` is the health check (True once the server answers). ``argv`` is the FULL, hardcoded
    launch command -- never assembled from user input. ``preflight`` returns an error string when the
    service isn't installed/startable (so we fail soft instead of spawning a doomed process).
    """

    name: str
    reachable: Callable[[], bool]
    argv: list[str]
    cwd: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    ready_timeout: float = 180.0
    poll: float = 2.0
    log_path: str | None = None
    preflight: Callable[[], str | None] = lambda: None


def _log_tail(log_path: str | None, *, lines: int = 3) -> str:
    """Last few non-blank lines of the service log, so a failure can say WHY rather than just when."""
    if not log_path:
        return ""
    with contextlib.suppress(Exception):
        kept = [
            ln.strip()
            for ln in Path(log_path).read_text(encoding="utf-8", errors="ignore").splitlines()
            if ln.strip()
        ]
        if kept:
            return " | ".join(kept[-lines:])[:300]
    return ""


def _await_ready(
    svc: LocalService, *, sleep: Callable[[float], None], proc: object | None = None,
) -> tuple[bool, str]:
    """Wait for the service to answer -- but stop the moment its process dies.

    Watching the child is what makes a failed start FAST. Without it, a process that exits two
    seconds in looks exactly like one still loading, so the caller waits out the entire
    ready_timeout -- 180s for ComfyUI, far longer than a chat turn -- and then reports a timeout
    for something that had already crashed. The turn was cut off before the honest message
    arrived, and the user got a generic non-answer instead.
    """
    deadline = time.monotonic() + svc.ready_timeout
    poll_exit = getattr(proc, "poll", None)
    while time.monotonic() < deadline:
        if svc.reachable():
            return True, f"{svc.name} is up"
        if callable(poll_exit):
            code = poll_exit()
            if code is not None:
                tail = _log_tail(svc.log_path)
                return False, (
                    f"{svc.name} exited during startup (code {code})"
                    + (f": {tail}" if tail else "")
                )
        sleep(svc.poll)
    # Still alive, just not listening yet. Say that, rather than implying it failed -- it is
    # detached, so it keeps coming up and the next request usually finds it ready.
    return False, f"{svc.name} is still starting (not ready within {svc.ready_timeout:.0f}s)"


def ensure_service(svc: LocalService, *, sleep: Callable[[float], None] = time.sleep,
                   spawn: Callable[..., object] | None = None) -> tuple[bool, str]:
    """Make ``svc`` reachable, starting it if needed. Returns (ok, message). Never raises.

    ``spawn`` (test seam) defaults to subprocess.Popen. Fail-soft everywhere: a missing install,
    a spawn error, or a readiness timeout all return (False, reason) rather than raising.
    """
    if svc.reachable():
        return True, f"{svc.name} already running"
    problem = svc.preflight()
    if problem:
        return False, problem

    # Single-flight: if another request is already starting this service, just wait for it.
    with _LOCK:
        already_starting = svc.name in _STARTING
        if not already_starting:
            _STARTING.add(svc.name)
    if already_starting:
        return _await_ready(svc, sleep=sleep)

    try:
        launcher = spawn or subprocess.Popen
        log_handle = subprocess.DEVNULL
        if svc.log_path:
            with contextlib.suppress(Exception):
                Path(svc.log_path).parent.mkdir(parents=True, exist_ok=True)
                log_handle = open(svc.log_path, "a", encoding="utf-8")  # noqa: SIM115 (lives for the child)
        try:
            child = launcher(
                list(svc.argv), cwd=svc.cwd, env={**os.environ, **svc.env},
                stdout=log_handle, stderr=log_handle, stdin=subprocess.DEVNULL,
                start_new_session=True,  # detach: the server outlives this request
            )
        except Exception as exc:  # never surface a raw failure to chat
            return False, f"could not start {svc.name} ({type(exc).__name__})"
        # Keep the handle: it is the only way to tell "crashed" from "still loading". A test seam
        # may return something without poll(), in which case we fall back to timing out.
        proc = child if callable(getattr(child, "poll", None)) else None
        if proc is not None:
            with _LOCK:
                _SPAWNED[svc.name] = proc
        return _await_ready(svc, sleep=sleep, proc=proc)
    finally:
        with _LOCK:
            _STARTING.discard(svc.name)


def stop_started_service(name: str, *, sig: int = signal.SIGTERM) -> bool:
    """Signal the service ``name`` only if THIS process started it and it is still running.

    The target is the recorded child: its process group when the child leads its own session
    (it was spawned with ``start_new_session``), else the recorded PID alone. A service started by
    someone else, or by an earlier VOOL run, is never touched, and neither is any other process
    that happens to match its name or port. Returns True if a signal was sent. Never raises.
    """
    with _LOCK:
        proc = _SPAWNED.get(name)
    if proc is None:
        return False
    try:
        if proc.poll() is not None:  # exited (and reaped): its PID may already belong to another process
            with _LOCK:
                if _SPAWNED.get(name) is proc:
                    _SPAWNED.pop(name, None)
            return False
        pid = int(proc.pid)
    except Exception:
        return False
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, sig)
        else:
            os.kill(pid, sig)
    except (ProcessLookupError, PermissionError, OSError):
        return False
    return True


__all__ = ["LocalService", "ensure_service", "stop_started_service"]
