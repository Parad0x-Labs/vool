"""Reads of the owner's folders never hold a chat turn.

macOS holds the first read of a protected folder (Desktop, Documents, Downloads) by a newly
installed or newly signed program until its privacy prompt is answered, and a folder on a sleeping
disk or a network share can hold a read just as long. Measured on the installed 0.8 (315c57e65,
2026-11-22): a turn sat for over seven minutes in ``os.walk`` of the owner's Desktop, and every
later turn waited behind it on the document index lock that the stuck read held.

``run_bounded`` runs such a read on its own daemon thread and waits a bounded time for it. A read
that is still blocked when the wait ends is left to finish (or not) on its own; the turn carries on
with the caller's fallback. While one read for a key is still blocked, later turns do not start a
second one behind it: they get the fallback at once, so no pile of stuck threads builds up.
"""
from __future__ import annotations

import contextvars
import logging
import os
import threading
from collections.abc import Callable
from typing import Any, TypeVar

_LOG = logging.getLogger(__name__)
T = TypeVar("T")

_LOCK = threading.Lock()
_RUNNING: dict[str, threading.Thread] = {}
#: Reads that outlived their wait and are still running, per group (see `run_bounded_shared`).
_STUCK: dict[str, set[threading.Thread]] = {}


def run_bounded(key: str, read: Callable[[], T], *, wait_s: float, fallback: T) -> T:
    """``read()`` on a daemon thread, waited on for at most ``wait_s`` seconds.

    Returns its value when it finishes in time, else ``fallback``. An exception inside ``read`` is
    logged and also gives ``fallback``. While an earlier read for the same ``key`` is still running,
    no new one is started and ``fallback`` is returned at once."""
    box: dict[str, Any] = {}
    with _LOCK:
        running = _RUNNING.get(key)
        if running is not None and running.is_alive():
            return fallback

        context = contextvars.copy_context()  # the turn's context (data folder, session) goes along

        def run() -> None:
            try:
                box["value"] = context.run(read)
            except Exception:
                _LOG.exception("off-turn read %s failed", key)
            finally:
                with _LOCK:
                    if _RUNNING.get(key) is threading.current_thread():
                        _RUNNING.pop(key, None)

        thread = threading.Thread(target=run, name=f"off-turn:{key[:40]}", daemon=True)
        _RUNNING[key] = thread
        thread.start()
    thread.join(max(0.0, float(wait_s)))
    if thread.is_alive() or "value" not in box:
        if thread.is_alive():
            _LOG.warning("off-turn read %s still blocked after %.1f s; the turn carries on", key, wait_s)
        return fallback
    return box["value"]


def still_blocked(key: str) -> bool:
    """Whether a read for ``key`` started earlier is still running."""
    with _LOCK:
        running = _RUNNING.get(key)
        return running is not None and running.is_alive()


def run_bounded_shared(group: str, read: Callable[[], T], *, wait_s: float, fallback: T) -> tuple[bool, T]:
    """Like `run_bounded`, for reads that may run side by side (two chats scanning one folder).

    Each call starts its own read, unless an earlier read in ``group`` outlived its wait and is
    still running: then the folder is known to be held, and the fallback comes at once. Returns
    ``(finished, value)``; ``finished`` is False when the fallback was given. An exception inside
    ``read`` is raised here, in the caller, as if the read had run on its thread."""
    box: dict[str, Any] = {}
    with _LOCK:
        stuck = {thread for thread in _STUCK.get(group, set()) if thread.is_alive()}
        if stuck:
            _STUCK[group] = stuck
            return False, fallback
        _STUCK.pop(group, None)

    context = contextvars.copy_context()

    def run() -> None:
        try:
            box["value"] = context.run(read)
        except BaseException as exc:
            box["error"] = exc

    thread = threading.Thread(target=run, name=f"off-turn:{group[:40]}", daemon=True)
    thread.start()
    thread.join(max(0.0, float(wait_s)))
    if thread.is_alive():
        with _LOCK:
            _STUCK.setdefault(group, set()).add(thread)
        _LOG.warning("off-turn read %s still blocked after %.1f s; the turn carries on", group, wait_s)
        return False, fallback
    if "error" in box:
        raise box["error"]
    return True, box["value"]


#: How long a request waits for one folder listing before carrying on without it.
FOLDER_LIST_WAIT_S = 2.0


def list_folder(path: str | os.PathLike[str], *, follow_symlinks: bool = True,
                wait_s: float = FOLDER_LIST_WAIT_S) -> list[tuple[str, str, bool, str]] | None:
    """(name, path, is_dir, real path) for each entry of one folder, read off the turn; None when the folder cannot
    be read or was not read in time.

    Mac gate on 91e898675: a new chat's first message ("save a contact ...") sat 7 min 53 s inside the chat request,
    before its turn existed, while the words-to-folder binding listed the owner's Desktop under a pending privacy
    prompt. Every stat of an entry happens on the reading thread too, so nothing of a held folder reaches the caller.
    """
    folder = os.fspath(path)

    def read() -> list[tuple[str, str, bool, str]]:
        rows: list[tuple[str, str, bool, str]] = []
        with os.scandir(folder) as entries:
            for entry in entries:
                try:
                    is_dir = entry.is_dir(follow_symlinks=follow_symlinks)
                    real = os.path.realpath(entry.path) if is_dir else ""
                except OSError:
                    is_dir, real = False, ""
                rows.append((entry.name, entry.path, is_dir, real))
        return rows

    try:
        finished, rows = run_bounded_shared(f"folder-list:{folder}", read, wait_s=wait_s, fallback=None)
    except OSError:
        return None
    return rows if finished else None
