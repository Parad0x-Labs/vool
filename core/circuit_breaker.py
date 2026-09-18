"""Circuit breaker pattern for external service calls."""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from enum import Enum
from typing import Any, TypeVar

logger = logging.getLogger(__name__)
T = TypeVar("T")


class CircuitState(Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    """Thread-safe circuit breaker for external calls (model APIs, network, etc.)."""

    def __init__(
        self,
        name: str,
        failure_threshold: int = 5,
        recovery_timeout: float = 60.0,
        half_open_max_calls: int = 1,
    ) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.half_open_max_calls = half_open_max_calls

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._last_failure_time: float | None = None
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        with self._lock:
            if self._state == CircuitState.OPEN and self._last_failure_time:
                if time.time() - self._last_failure_time >= self.recovery_timeout:
                    self._state = CircuitState.HALF_OPEN
                    self._half_open_calls = 0
                    logger.info("Circuit %s transitioning to HALF_OPEN", self.name)
            return self._state

    def call(self, func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        state = self.state
        if state == CircuitState.OPEN:
            raise CircuitOpenError(f"Circuit {self.name} is OPEN — calls blocked until recovery")

        if state == CircuitState.HALF_OPEN:
            with self._lock:
                if self._half_open_calls >= self.half_open_max_calls:
                    raise CircuitOpenError(f"Circuit {self.name} HALF_OPEN max calls reached")
                self._half_open_calls += 1

        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except Exception:
            self._on_failure()
            raise

    def _on_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.CLOSED
                self._failure_count = 0
                logger.info("Circuit %s recovered → CLOSED", self.name)
            self._success_count += 1
            self._failure_count = max(0, self._failure_count - 1)

    def _on_failure(self) -> None:
        with self._lock:
            self._failure_count += 1
            self._last_failure_time = time.time()
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                logger.warning("Circuit %s HALF_OPEN test failed → OPEN", self.name)
            elif self._failure_count >= self.failure_threshold:
                self._state = CircuitState.OPEN
                logger.warning("Circuit %s tripped → OPEN (failures=%d)", self.name, self._failure_count)

    def reset(self) -> None:
        with self._lock:
            self._state = CircuitState.CLOSED
            self._failure_count = 0
            self._success_count = 0
            self._half_open_calls = 0

    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "name": self.name,
                "state": self._state.value,
                "failure_count": self._failure_count,
                "success_count": self._success_count,
            }


class CircuitOpenError(Exception):
    pass


# ── Global registry of circuit breakers ────────────────────────────────
_BREAKERS: dict[str, CircuitBreaker] = {}
_BREAKERS_LOCK = threading.Lock()


def get_breaker(
    name: str,
    failure_threshold: int = 5,
    recovery_timeout: float = 60.0,
) -> CircuitBreaker:
    with _BREAKERS_LOCK:
        if name not in _BREAKERS:
            _BREAKERS[name] = CircuitBreaker(name, failure_threshold, recovery_timeout)
        return _BREAKERS[name]


def all_breaker_statuses() -> list[dict[str, Any]]:
    with _BREAKERS_LOCK:
        return [b.status() for b in _BREAKERS.values()]
