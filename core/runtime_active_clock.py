"""Execution-time clock for an explicitly price-queued live turn.

Only time spent waiting BEFORE paid dispatch is excluded from execution deadlines.
Consent expiry, budget renewal and price freshness keep using wall time. This is live
continuation, not a serializable checkpoint: process exit ends this clock and stack.
"""
from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager

_CLOCK = contextvars.ContextVar("price_wait_execution_clock", default=None)


class ActiveClock:
    def __init__(self):
        self.lock = threading.RLock()
        self.depth = 0
        self.started = 0.0
        self.excluded = 0.0

    def now(self):
        with self.lock:
            return (self.started if self.depth else time.monotonic()) - self.excluded

    @contextmanager
    def paused(self):
        with self.lock:
            if not self.depth:
                self.started = time.monotonic()
            self.depth += 1
        try:
            yield
        finally:
            with self.lock:
                self.depth -= 1
                if not self.depth:
                    self.excluded += time.monotonic() - self.started


def monotonic():
    clock = _CLOCK.get()
    return clock.now() if clock else time.monotonic()


def enabled():
    return _CLOCK.get() is not None


@contextmanager
def task_clock(enabled=True):
    token = _CLOCK.set(ActiveClock() if enabled else None)
    try:
        yield
    finally:
        _CLOCK.reset(token)


@contextmanager
def paused():
    clock = _CLOCK.get()
    if clock is None:
        raise RuntimeError("price pause requires an owned live task clock")
    with clock.paused():
        yield
