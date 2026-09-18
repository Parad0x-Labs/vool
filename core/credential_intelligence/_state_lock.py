"""A cross-process read-modify-write lock for the credential-intelligence state files.

``os.replace`` makes each WRITE atomic, but every read-modify-write over these JSON files
(save one provider's row, drop another's, invalidate a third's) is NOT: two processes or
threads that each read the old file and replace their own version lose whichever row they did
not hold (measured by the 2026-09-15 review probe: two concurrent saves for different
providers, one survivor). An in-process ``threading.Lock`` covers one process only — the
daemon and a CLI surface are separate processes sharing the same data dir.

This module is the ONE mechanism: an advisory ``flock`` on a dedicated lock file next to the
state file (``<name>.lock``), exclusive for the whole read-modify-write. It composes with the
existing atomic-replace writer (the replace stays the crash-atomicity mechanism; the lock is
the concurrency mechanism). Same-thread re-entry is NOT supported — callers must not take the
lock and then, inside it, drive another door that takes the same lock (the test rigs inject
concurrent state changes between lock sections, never inside one).
"""
from __future__ import annotations

import contextlib
import fcntl
import os
from collections.abc import Iterator
from pathlib import Path


@contextlib.contextmanager
def state_lock(state_path: Path) -> Iterator[None]:
    """Hold the exclusive lock guarding read-modify-write access to ``state_path``."""
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(path.name + ".lock")
    fd = os.open(str(lock_path), os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def credential_state_lock():
    """The transaction lock guarding the credential binding index -- the ONE authority every
    credential writer already holds. Readers that must observe a multi-slot fact atomically
    (the custom provider's base URL and its key) take the SAME lock, so a writer's
    endpoint+key+index commit is indivisible to them too."""
    from core.credential_intelligence.binding import index_path

    return state_lock(index_path())


__all__ = ["credential_state_lock", "state_lock"]
