"""Typed seat-attempt outcomes and the bounded retry policy.

One vocabulary for "what happened when a seat was asked", owned here so the orchestrator,
the ledger and any future surface classify the same way. It is deliberately a SMALL,
dependency-free module: the live path must not import the council-mode ``turn_manager``,
which is a different topology (N advisors -> one sealed judge) reachable from no
production caller.

Why the distinctions are load-bearing:

* ``EMPTY`` and ``MALFORMED`` are not ``FAILED``. A seat that answered but broke its
  structural contract is a re-ask; a seat whose transport died is a different fault with
  a different fix. Collapsing them hides which one is happening.
* ``TIMED_OUT`` is not ``FAILED`` either. A seat turn is a real tool loop over a real
  workspace bounded by a wall-clock read timeout, so a deadline says "this seat needs
  more clock", not "this provider is broken".
* ``CANCELLED`` records an attempt the operator's stop prevented. It is never retried:
  retrying past a stop would be the runtime overruling the operator.

The bound is a CEILING ON RE-ASKS, never a token or spend guess (CLAUDE.md 4b). It exists
because a seat that cannot satisfy its contract will not satisfy it on the hundredth try
either, and each attempt is a full agent turn against a real workspace.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class AttemptOutcome(str, enum.Enum):
    """What one seat turn actually produced. Closed set; the ledger stores the value."""

    VALID = "VALID"
    EMPTY = "EMPTY"
    MALFORMED = "MALFORMED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    CANCELLED = "CANCELLED"


#: Outcomes a bounded re-ask can plausibly repair. CANCELLED is excluded on purpose --
#: the operator stopped the run -- and VALID needs no repair.
RETRYABLE_OUTCOMES = frozenset(
    {
        AttemptOutcome.EMPTY,
        AttemptOutcome.MALFORMED,
        AttemptOutcome.FAILED,
        AttemptOutcome.TIMED_OUT,
    }
)

#: An attempt in one of these ends the seat's round immediately, whatever the bound says.
TERMINAL_OUTCOMES = frozenset({AttemptOutcome.VALID, AttemptOutcome.CANCELLED})

#: One re-ask by default: enough to absorb a dropped connection or a forgotten verdict
#: line, short of paying for a seat that is structurally unable to comply.
DEFAULT_MAX_ATTEMPTS_PER_SEAT = 2

#: The hard ceiling on the configurable bound. Not a preference -- an unbounded (or
#: absurd) re-ask loop against a paid provider is the failure this module exists to make
#: impossible, so the ceiling is enforced at construction, not at call time.
MAX_ATTEMPTS_HARD_CAP = 5


@dataclass(frozen=True)
class RetryPolicy:
    """The named retry configuration. Replaces the literal ``for attempt in (1, 2)``.

    ``max_attempts_per_seat`` counts ATTEMPTS, not retries: 1 means "ask once, never
    re-ask". Validated at construction so an unbounded policy cannot be built at all --
    there is no runtime path by which a bad value becomes an infinite loop.
    """

    max_attempts_per_seat: int = DEFAULT_MAX_ATTEMPTS_PER_SEAT

    def __post_init__(self) -> None:
        value = self.max_attempts_per_seat
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError("max_attempts_per_seat must be an int")
        if not 1 <= value <= MAX_ATTEMPTS_HARD_CAP:
            raise ValueError(
                f"max_attempts_per_seat must be within 1..{MAX_ATTEMPTS_HARD_CAP}; "
                f"got {value}. A council never retries indefinitely: every attempt is a "
                "full agent turn against a real workspace."
            )

    @property
    def max_retries_per_seat(self) -> int:
        return self.max_attempts_per_seat - 1

    def should_retry(self, outcome: AttemptOutcome, attempt: int) -> bool:
        """True when ``outcome`` on attempt number ``attempt`` earns one more ask."""
        return outcome in RETRYABLE_OUTCOMES and attempt < self.max_attempts_per_seat


__all__ = [
    "DEFAULT_MAX_ATTEMPTS_PER_SEAT",
    "MAX_ATTEMPTS_HARD_CAP",
    "RETRYABLE_OUTCOMES",
    "TERMINAL_OUTCOMES",
    "AttemptOutcome",
    "RetryPolicy",
]
