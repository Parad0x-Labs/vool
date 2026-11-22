"""Execution-time clock for a live turn that waits on the owner (a live browser hand-over).

Only time spent waiting on the owner is excluded from execution deadlines.
Consent expiry and budget renewal keep using wall time. This is live
continuation, not a serializable checkpoint: process exit ends this clock and stack.
"""
from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager

_CLOCK = contextvars.ContextVar("owner_wait_execution_clock", default=None)


class ActiveClock:
    def __init__(self):
        self.lock = threading.RLock()
        self.depth = 0
        self.started = 0.0
        self.excluded = 0.0
        self.resumed = 0.0   # when the last pause ended
        self.floor = float("-inf")

    def now(self):
        with self.lock:
            # Never below a reading already given: a pause dated back to when the owner was asked must not make
            # this clock run backwards for whoever read it in between.
            self.floor = max(self.floor, (self.started if self.depth else time.monotonic()) - self.excluded)
            return self.floor

    @contextmanager
    def paused(self, since=None):
        with self.lock:
            if not self.depth:
                now = time.monotonic()
                # The wait began when the owner was asked (`since`), which can be a little before this pause is
                # entered: never later than now, and never before time this clock already counted as excluded.
                self.started = max(min(now, since), self.resumed) if isinstance(since, (int, float)) else now
            self.depth += 1
        try:
            yield
        finally:
            with self.lock:
                self.depth -= 1
                if not self.depth:
                    self.resumed = time.monotonic()
                    self.excluded += self.resumed - self.started


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
def paused(since=None):
    clock = _CLOCK.get()
    if clock is None:
        raise RuntimeError("price pause requires an owned live task clock")
    with clock.paused(since):
        yield
