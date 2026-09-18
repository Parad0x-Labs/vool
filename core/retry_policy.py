from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    initial_delay_seconds: float = 0.5
    backoff_multiplier: float = 2.0
    max_delay_seconds: float = 8.0


DEFAULT_RETRY_POLICY = RetryPolicy()


def next_delay(attempt: int, policy: RetryPolicy = DEFAULT_RETRY_POLICY) -> float:
    if attempt <= 0:
        return 0.0
    delay = policy.initial_delay_seconds * (policy.backoff_multiplier ** (attempt - 1))
    return min(policy.max_delay_seconds, delay)


def should_retry(attempt: int, policy: RetryPolicy = DEFAULT_RETRY_POLICY) -> bool:
    return attempt < policy.max_attempts
