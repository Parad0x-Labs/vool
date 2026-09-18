"""The ONE fail-closed cross-process publication lock.

Used by the first-run pact authority, the provider-choice authority and the operator
policy setter — no private fcntl imports, no duplicate lock authorities, no fail-open
fallback. POSIX locks via ``fcntl.flock``; Windows locks via ``msvcrt.locking``
(the ``installer/bundle/bundle_supervisor.py`` pattern). ``fcntl`` and ``msvcrt`` are
imported lazily INSIDE the platform branch so the module imports on every platform.

Contention raises :class:`LockUnavailable` — fail closed, never "yield unlocked and
continue". Callers translate that into their own typed faults; the code string is
``"lock_unavailable"`` everywhere.

NOTE: actual Windows packaged execution is UNMEASURED — the Windows branch is proven
by import-and-call-path selection tests only (see
tests/test_first_run_pact_correction.py).
"""
from __future__ import annotations

import os

IS_WINDOWS = os.name == "nt"


class LockUnavailable(RuntimeError):
    """The publication lock is held by another process (or cannot be taken)."""

    code = "lock_unavailable"

    def __init__(self, path: os.PathLike | str, detail: str = ""):
        super().__init__(
            f"another process holds the publication lock: {path}"
            + (f" ({detail})" if detail else "")
        )
        self.path = str(path)
        self.detail = detail


class PublicationLock:
    """Non-blocking exclusive lock on one file. Hold across read → CAS → publish."""

    def __init__(self, path: os.PathLike | str):
        # os.fspath + os.path here: path handling stays on the REAL platform while the
        # LOCKING branch follows IS_WINDOWS — pathlib.Path would try to instantiate a
        # WindowsPath on POSIX the moment os.name is simulated as "nt".
        self._path = os.fspath(path)
        self._fh = None

    def __enter__(self) -> "PublicationLock":
        # The WHOLE acquisition — directory creation, lock-file open, and the lock
        # call itself — sits inside ONE fail-closed boundary: every failure becomes
        # LockUnavailable. Exceptions raised by the PROTECTED mutation body never
        # pass through here, so contention is never confused with body failures.
        fh = None
        try:
            parent = os.path.dirname(self._path)
            if parent:
                os.makedirs(parent, exist_ok=True)
            fh = open(self._path, "w")
            if IS_WINDOWS:
                import msvcrt

                fh.seek(0)
                fh.write("1")
                fh.flush()
                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ImportError) as exc:
            if fh is not None:
                try:
                    fh.close()
                except OSError:
                    pass
            raise LockUnavailable(self._path, str(exc)) from exc
        self._fh = fh
        return self

    def __exit__(self, *exc) -> bool:
        fh, self._fh = self._fh, None
        if fh is None:
            return False
        try:
            if IS_WINDOWS:
                import msvcrt

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            try:
                fh.close()
            except OSError:
                pass
        return False
