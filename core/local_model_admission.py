"""One place that decides how many local model calls may be in flight at once.

Measured on the installed daemon, 2026-07-31, driving N concurrent `POST /api/chat`:

    N=8    8/8 answered,      wall 20.6s
    N=12  12/12 answered,     wall 49.3s
    N=24  17/24 answered,     7 returned "I couldn't get a usable model response in this run" at ~64s
    N=32  18/32 answered,    14 returned the same at ~67s

and every one of those failures carries the same ledger evidence:

    model.call_failed      HTTPConnectionPool(host='127.0.0.1', port=11434):
                           Read timed out. (read timeout=59.99...)
    model_lane_failed      ollama-local:qwen3:8b failed; trying fallback if available.
    model_routing_failed   Provider fallback budget (60s) exceeded after 1 attempt(s)
    task_completed         I couldn't get a usable model response in this run...

Nothing was wrong with the model or the machine. The daemon dispatches every in-flight turn to
Ollama at once -- `max_safe_concurrency` exists in every provider manifest but is scoring metadata
that nothing enforces -- so the requests pile up inside Ollama, which generates for one or two of
them at a time. A queued request is doing nothing, yet its 60s ordinary-lane budget (and the read
timeout clipped to it, `core/memory_first_router.py`) is running from the moment it was sent. Past
roughly twenty concurrent turns, more than half never reach the head of Ollama's queue inside their
own timeout and die having never been served. It is a queue in the wrong place, not a slow model.

So the queue is moved to where the daemon can see it. A call waits HERE, before the socket is
opened, and the read timeout starts when the model is actually available. Waiting is not free, but a
turn that waits 30s and answers is strictly better than one that waits 60s and does not -- and when
the wait is genuinely hopeless the caller is told the lane is saturated, which is a fact, instead of
being handed a read timeout to interpret.

This gate is deliberately process-wide and shared by every local-model caller (chat adapter, builder
generations, the intent arbiter): they contend for the same single Ollama, so counting them
separately would count nothing.
"""
from __future__ import annotations

import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager

# How many local generations may be in flight at Ollama at once.
#
# Not 1: Ollama batches concurrent requests against a resident model, and serializing hard would
# throw that away. Not unbounded: unbounded is the bug. 4 is the measured knee on this class of
# machine -- N=8 answered 8/8 while N=24 lost 7 -- and it is the value a manifest's
# `max_safe_concurrency` (1-2 per provider, several providers) implies in aggregate.
_DEFAULT_CONCURRENCY = 4

# How long a call may wait for a slot before the lane is declared saturated. Sized well above the
# 60s ordinary-lane budget on purpose: the point of waiting here is that the wait does NOT consume
# the request's own timeout, so a long queue drains instead of failing all at once.
_DEFAULT_QUEUE_WAIT_SECONDS = 240.0


class LocalModelLaneSaturatedError(RuntimeError):
    """Raised when a local generation could not get a slot inside the queue-wait ceiling.

    A named class, not a bare RuntimeError: the provider lane classifies failures by type, and
    "the queue never drained" must never be reported to the operator as a model or network fault.
    """


# Keep the historical import name for callers while exposing a conventional
# exception class name to new code.
LocalModelLaneSaturated = LocalModelLaneSaturatedError


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(str(os.environ.get(name) or "").strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(str(os.environ.get(name) or "").strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def configured_concurrency() -> int:
    return _positive_int("VOOL_LOCAL_MODEL_CONCURRENCY", _DEFAULT_CONCURRENCY)


def configured_queue_wait_seconds() -> float:
    return _positive_float("VOOL_LOCAL_MODEL_QUEUE_WAIT", _DEFAULT_QUEUE_WAIT_SECONDS)


class _Gate:
    """A resizable counting gate that also reports how deep the queue is.

    `threading.Semaphore` alone would do the admission; the waiting count is what makes the
    saturation visible in `/healthz` and in the failure message, rather than something an operator
    has to infer from timings.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._limit = configured_concurrency()
        self._in_flight = 0
        self._waiting = 0

    def _refresh_limit_locked(self) -> None:
        # Re-read on every acquire so a test (or an operator) can change the ceiling without a
        # restart, and so the module-level singleton does not freeze the value read at import time.
        self._limit = configured_concurrency()

    def acquire(self, timeout: float) -> bool:
        deadline_wait = max(0.0, float(timeout))
        with self._condition:
            self._refresh_limit_locked()
            if self._in_flight < self._limit:
                self._in_flight += 1
                return True
            self._waiting += 1
            try:
                admitted = self._condition.wait_for(
                    lambda: self._in_flight < self._limit,
                    timeout=deadline_wait,
                )
            finally:
                self._waiting -= 1
            if not admitted:
                return False
            self._in_flight += 1
            return True

    def release(self) -> None:
        with self._condition:
            self._in_flight = max(0, self._in_flight - 1)
            self._condition.notify()

    def snapshot(self) -> dict[str, int]:
        with self._condition:
            self._refresh_limit_locked()
            return {"limit": self._limit, "in_flight": self._in_flight, "waiting": self._waiting}


_GATE = _Gate()


def lane_snapshot() -> dict[str, int]:
    """Live admission state. Read-only; safe to call from a health probe."""
    return _GATE.snapshot()


@contextmanager
def local_model_slot(*, provider_id: str = "", timeout_seconds: float | None = None) -> Iterator[None]:
    """Hold one local-generation slot for the duration of the block.

    Raises :class:`LocalModelLaneSaturatedError` when the queue does not drain inside the ceiling. The
    caller has not opened a socket at that point, so nothing is left half-done.
    """
    wait = configured_queue_wait_seconds() if timeout_seconds is None else float(timeout_seconds)
    if not _GATE.acquire(wait):
        state = _GATE.snapshot()
        raise LocalModelLaneSaturatedError(
            f"local model lane saturated: {state['in_flight']} generation(s) in flight, "
            f"{state['waiting']} waiting, ceiling {state['limit']}; "
            f"{provider_id or 'this provider'} did not get a slot within {wait:.0f}s"
        )
    try:
        yield
    finally:
        _GATE.release()
