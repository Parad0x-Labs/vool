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

import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

_LOG = logging.getLogger(__name__)
T = TypeVar("T")

_LOCK = threading.Lock()
_RUNNING: dict[str, threading.Thread] = {}


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

        def run() -> None:
            try:
                box["value"] = read()
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
