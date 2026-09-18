"""Bounded work queue with backpressure for daemon message processing."""
from __future__ import annotations

import contextlib
import logging
import queue
import threading
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class WorkQueue:
    """Bounded work queue that applies backpressure when full.

    Instead of spawning unlimited threads per incoming message
    (the current daemon pattern), this queue bounds concurrency
    and rejects overflow.
    """

    def __init__(
        self,
        name: str,
        max_size: int = 256,
        max_workers: int = 8,
        on_reject: Callable[[Any], None] | None = None,
    ) -> None:
        self.name = name
        self._queue: queue.Queue[Any] = queue.Queue(maxsize=max_size)
        self._workers: list[threading.Thread] = []
        self._handler: Callable[[Any], None] | None = None
        self._on_reject = on_reject
        self._max_workers = max_workers
        self._running = False

    def start(self, handler: Callable[[Any], None]) -> None:
        self._handler = handler
        self._running = True
        for i in range(self._max_workers):
            t = threading.Thread(
                target=self._worker_loop,
                name=f"workqueue-{self.name}-{i}",
                daemon=True,
            )
            t.start()
            self._workers.append(t)
        logger.info("WorkQueue %s started with %d workers, max_size=%d", self.name, self._max_workers, self._queue.maxsize)

    def submit(self, item: Any) -> bool:
        try:
            self._queue.put_nowait(item)
            return True
        except queue.Full:
            logger.warning("WorkQueue %s full — applying backpressure (dropped item)", self.name)
            if self._on_reject:
                with contextlib.suppress(Exception):
                    self._on_reject(item)
            return False

    def stop(self, timeout: float = 5.0) -> None:
        self._running = False
        # Drain with poison pills
        for _ in self._workers:
            with contextlib.suppress(queue.Full):
                self._queue.put_nowait(None)
        for t in self._workers:
            t.join(timeout=timeout)
        self._workers.clear()

    def _worker_loop(self) -> None:
        while self._running:
            try:
                item = self._queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if item is None:
                break
            if self._handler:
                try:
                    self._handler(item)
                except Exception as e:
                    logger.error("WorkQueue %s handler error: %s", self.name, e, exc_info=True)

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    @property
    def is_full(self) -> bool:
        return self._queue.full()

    def status(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "pending": self.pending,
            "max_size": self._queue.maxsize,
            "workers": len(self._workers),
            "running": self._running,
        }
