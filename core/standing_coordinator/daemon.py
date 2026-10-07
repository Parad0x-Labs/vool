"""The background loop: one thread, one tick every ``interval_seconds``.

Started by the served process only. With no watched team a tick reads one empty table
and does nothing else, so the thread costs nothing while there is nothing to watch.
On start it first marks actions a crash interrupted as ``uncertain`` (never replayed).
A failing tick is logged and recorded; the loop keeps going.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

from core.standing_coordinator.coordinator import StandingCoordinator

logger = logging.getLogger(__name__)

DEFAULT_INTERVAL_SECONDS = 30.0
DISABLE_ENV = "VOOL_STANDING_COORDINATOR"


class CoordinatorDaemon:
    def __init__(self, coordinator: StandingCoordinator, *, interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
                 wait_fn: Callable[[threading.Event, float], bool] | None = None) -> None:
        self.coordinator = coordinator
        self._interval = max(5.0, float(interval_seconds))
        self._stop = threading.Event()
        self._wait = wait_fn or (lambda event, seconds: event.wait(seconds))
        self._thread: threading.Thread | None = None
        # Guards the loop's decision to exit against `resume`, so a resumed loop never exits anyway.
        self._exit_lock = threading.Lock()
        self._exiting = False
        self.ticks = 0
        self.failures = 0

    def run_once(self) -> dict:
        try:
            result = self.coordinator.tick()
        except Exception as exc:
            self.failures += 1
            logger.exception("standing coordinator tick failed")
            return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        self.ticks += 1
        return result

    def start(self) -> bool:
        if self._thread is not None and self._thread.is_alive():
            return False
        self.coordinator.store.reconcile()
        self._stop.clear()
        self._exiting = False
        self._thread = threading.Thread(target=self._run, name="vool-standing-coordinator", daemon=True)
        self._thread.start()
        return True

    def stop(self, timeout: float = 5.0) -> bool:
        """Ask the loop to end and wait up to ``timeout``. True only when it has really ended."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
        return not self.alive

    def resume(self) -> bool:
        """Keep a loop that was asked to stop but is still inside a tick. False when it is ending or gone."""
        with self._exit_lock:
            if not self.alive or self._exiting:
                return False
            self._stop.clear()
            return True

    @property
    def alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def _run(self) -> None:
        while True:
            with self._exit_lock:
                if self._stop.is_set():
                    self._exiting = True
                    return
            self.run_once()
            self._wait(self._stop, self._interval)


_DEFAULT: CoordinatorDaemon | None = None
_LOCK = threading.Lock()


def disabled_by_env() -> bool:
    return os.environ.get(DISABLE_ENV, "").strip().lower() in {"0", "off", "false", "no"}


def start_default_daemon() -> CoordinatorDaemon | None:
    """Start the one process-wide loop. Safe to call twice; off when VOOL_STANDING_COORDINATOR=0."""
    global _DEFAULT
    if disabled_by_env():
        return None
    from core.standing_coordinator.api import default_coordinator

    with _LOCK:
        if _DEFAULT is not None and _DEFAULT.alive:
            # A loop whose stop timed out still owns the process: it is kept, never doubled. One that
            # is already on its way out is waited for, so two ticks never run at once.
            if _DEFAULT.resume():
                return _DEFAULT
            if not _DEFAULT.stop(timeout=5.0):
                logger.warning("standing coordinator: the previous loop has not ended; not starting another")
                return _DEFAULT
        _DEFAULT = CoordinatorDaemon(default_coordinator())
        _DEFAULT.start()
        return _DEFAULT


def stop_default_daemon() -> bool:
    """Stop the process-wide loop. A loop still inside a tick after the timeout keeps its ownership
    (False), so a later start resumes it instead of running a second loop beside it."""
    global _DEFAULT
    with _LOCK:
        if _DEFAULT is not None and not _DEFAULT.stop():
            logger.warning("standing coordinator: stop timed out; the loop keeps ownership until it ends")
            return False
        _DEFAULT = None
        return True


__all__ = ["DISABLE_ENV", "CoordinatorDaemon", "start_default_daemon", "stop_default_daemon"]
