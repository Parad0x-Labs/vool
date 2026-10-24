"""Invocation-owned cancellation and bounded pipes for untrusted plugin children."""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

_LOCK = threading.RLock()
_CALLS: dict[tuple[str, str], list[Call]] = {}


def _scope() -> str:
    from core.plugin_lifecycle import store_path

    return str(store_path().resolve())


class InvocationStopped(RuntimeError):
    pass


class OutputTooLarge(RuntimeError):
    pass


@dataclass(eq=False)
class Call:
    plugin_id: str
    current: Callable[[], bool]
    stopped: threading.Event = field(default_factory=threading.Event)
    resources: list[Callable[[], None]] = field(default_factory=list)
    lock: threading.RLock = field(default_factory=threading.RLock)

    def check(self) -> None:
        if self.stopped.is_set() or not self.current():
            self.cancel()
            raise InvocationStopped("The plugin was disabled or changed. Its result was discarded.")

    def own(self, close: Callable[[], None]) -> None:
        with self.lock:
            if self.stopped.is_set():
                close()
                raise InvocationStopped("The plugin is disabled. The call was stopped.")
            self.resources.append(close)

    def cancel(self) -> None:
        with self.lock:
            self.stopped.set()
            resources, self.resources = self.resources, []
        for close in resources:
            with contextlib.suppress(Exception):
                close()

    def release(self, close: Callable[[], None]) -> None:
        with self.lock:
            if close in self.resources:
                self.resources.remove(close)


@contextlib.contextmanager
def invocation(plugin_id: str, current: Callable[[], bool]) -> Iterator[Call]:
    key = (_scope(), plugin_id)
    call = Call(plugin_id, current)
    with _LOCK:
        call.check()
        _CALLS.setdefault(key, []).append(call)
    try:
        call.check()
        yield call
        call.check()
    finally:
        call.cancel()
        with _LOCK:
            if call in _CALLS.get(key, []):
                _CALLS[key].remove(call)
            if not _CALLS.get(key):
                _CALLS.pop(key, None)


def stop(plugin_id: str) -> None:
    with _LOCK:
        calls = list(_CALLS.get((_scope(), plugin_id), []))
    for call in calls:
        call.cancel()


def terminate_owned(child: subprocess.Popen) -> None:
    """Only a child created with start_new_session=True by this invocation enters here."""
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Darwin can report EPERM for an already exited group. A still-running
        # leader is addressed through its own Popen handle, never a process search.
        if child.poll() is None:
            child.kill()


def communicate(child: subprocess.Popen, payload: bytes, *, timeout: float, call: Call | None,
                limit: int = 4 * 1024 * 1024) -> tuple[str, str]:
    if len(payload) > limit:
        raise OutputTooLarge("The plugin request exceeds the size limit.")
    overflow = threading.Event()
    buffers = [bytearray(), bytearray()]

    def read(pipe, target):
        try:
            while chunk := pipe.read(65536):
                if len(target) + len(chunk) > limit:
                    overflow.set()
                    terminate_owned(child)
                    return
                target.extend(chunk)
        except (OSError, ValueError):
            return

    def write():
        with contextlib.suppress(OSError, ValueError):
            child.stdin.write(payload)
            child.stdin.close()

    readers = [threading.Thread(target=read, args=(pipe, target), daemon=True)
               for pipe, target in zip((child.stdout, child.stderr), buffers, strict=True)]
    writer = threading.Thread(target=write, daemon=True)
    def close():
        terminate_owned(child)
    if call:
        call.own(close)
    for thread in (*readers, writer):
        thread.start()
    deadline = time.monotonic() + timeout
    try:
        while child.poll() is None:
            if call:
                call.check()
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(child.args, timeout)
            if overflow.wait(0.05):
                break
        # Descendants cannot retain our pipes or execution snapshot after the parent exits.
        terminate_owned(child)
        child.wait(timeout=5)
        for reader in readers:
            reader.join(2)
        if overflow.is_set():
            raise OutputTooLarge("The plugin response exceeds the size limit.")
        if any(reader.is_alive() for reader in readers):
            raise InvocationStopped("The plugin connection did not close. Its result was discarded.")
        if call:
            call.check()
        return tuple(bytes(value).decode("utf-8", errors="replace") for value in buffers)
    finally:
        terminate_owned(child)
        child.wait(timeout=5)
        if call:
            call.release(close)
