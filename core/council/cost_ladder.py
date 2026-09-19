"""Cost ladder, operator escalation and hard spend ceilings.

The ladder is the law: FREE, FREE, CHEAP, then PAUSE. Retrying on a second free
model of a DIFFERENT family is legitimate; buying is not a rung. PREMIUM exists
only behind an explicit operator escalation, and the escalation object cannot be
forged by the code that wants to spend — the same sentinel construction
``core/council/policy.py`` uses for adjudication authorizations.

Ceilings are HARD on four axes — calls, tokens, cost, wall clock — and
:class:`SpendCeilings` refuses to be constructed unbounded, because "unlimited
budget" is not a budget; there is no ``unbounded()`` factory and zero/negative
values are construction errors, not runtime surprises.

:class:`SpendMeter` is the enforcement point. Reserving a call is atomic under a
lock, so two threads racing the last call see exactly one winner and one typed
:class:`BudgetExhausted`; usage is recorded after the fact and the meter's
``exhaustion()`` names which ceiling was crossed. The clock is injectable because
wall-clock law must be testable without sleeping.
"""

from __future__ import annotations

import enum
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


class CostLadderError(ValueError):
    """The declared cost policy violates the ladder law."""


class EscalationRefused(PermissionError):
    """A premium tier was demanded without an operator-issued escalation."""


class CostTier(str, enum.Enum):
    FREE = "free"
    CHEAP = "cheap"
    PREMIUM = "premium"


#: The default ladder: a second free attempt (a different family), then a cheap
#: model, then STOP. ``PAUSE`` is not a tier — it is the ladder's terminal word.
DEFAULT_LADDER: tuple[CostTier, ...] = (CostTier.FREE, CostTier.FREE, CostTier.CHEAP)

LADDER_TERMINAL = "pause"


class _EscalationSentinel:
    __slots__ = ()
    _instance: _EscalationSentinel | None = None

    def __new__(cls) -> _EscalationSentinel:
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance


_SENTINEL = _EscalationSentinel()


@dataclass(frozen=True)
class OperatorEscalation:
    """The operator's EXPLICIT unlock of the premium tier for ONE task.

    Minted only by :func:`mint_operator_escalation`; constructing one directly fails
    on the private sentinel, so neither a seat nor a caller can self-authorize the
    spend it wants.
    """

    task_id: str
    tier: CostTier
    granted_by: str
    granted_at: float
    token: str
    _sentinel: _EscalationSentinel = field(repr=False, compare=False, default=_SENTINEL)

    def __post_init__(self) -> None:
        if self._sentinel is not _SENTINEL:
            raise EscalationRefused(
                "escalations can only be minted by mint_operator_escalation"
            )


def mint_operator_escalation(
    *, task_id: str, tier: CostTier = CostTier.PREMIUM, granted_by: str = "operator"
) -> OperatorEscalation:
    """The one constructor. Premium only — an escalation IS the premium unlock."""
    clean_task = str(task_id or "").strip()
    if not clean_task:
        raise EscalationRefused("an escalation names the task it unlocks")
    if tier is not CostTier.PREMIUM:
        raise EscalationRefused(
            f"an escalation unlocks PREMIUM only; {tier!r} sits on the ladder already"
        )
    return OperatorEscalation(
        task_id=clean_task,
        tier=tier,
        granted_by=str(granted_by or "operator"),
        granted_at=time.time(),
        token=uuid.uuid4().hex,
    )


@dataclass(frozen=True)
class SpendCeilings:
    """Hard ceilings on the four spend axes. None of them may be unbounded."""

    max_calls: int
    max_tokens: int
    max_cost_usd: float
    wall_clock_seconds: float

    def __post_init__(self) -> None:
        for name in ("max_calls", "max_tokens"):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
                raise CostLadderError(f"{name} must be a positive integer ceiling")
        for name in ("max_cost_usd", "wall_clock_seconds"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0:
                raise CostLadderError(f"{name} must be a positive ceiling")
        object.__setattr__(self, "max_calls", int(self.max_calls))
        object.__setattr__(self, "max_tokens", int(self.max_tokens))
        object.__setattr__(self, "max_cost_usd", float(self.max_cost_usd))
        object.__setattr__(self, "wall_clock_seconds", float(self.wall_clock_seconds))


@dataclass(frozen=True)
class CostPolicy:
    """One task's cost law: the ladder it climbs and the ceilings it dies at.

    ``escalation`` is the operator's premium unlock for THIS task; a ladder may
    never contain PREMIUM, because premium is never a default path — only an
    escalation appends it.
    """

    ceilings: SpendCeilings
    ladder: tuple[CostTier, ...] = DEFAULT_LADDER
    escalation: OperatorEscalation | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ceilings, SpendCeilings):
            raise CostLadderError("a cost policy requires typed SpendCeilings")
        ladder = tuple(self.ladder or ())
        if not ladder:
            raise CostLadderError("a ladder with no rungs is a pause, not a policy")
        if ladder[0] is not CostTier.FREE:
            raise CostLadderError("a ladder starts on FREE — the first attempt never pays")
        if CostTier.PREMIUM in ladder:
            raise CostLadderError(
                "PREMIUM is not a rung: it exists only behind an operator escalation"
            )
        if len(ladder) > 6:
            raise CostLadderError("a ladder longer than six rungs is a spend plan")
        object.__setattr__(self, "ladder", ladder)


def next_tier(policy: CostPolicy, attempts_used: int) -> str:
    """The tier the NEXT attempt runs at: a ladder rung, ``"pause"``, or premium.

    With an escalation attached, exhausting the ladder continues at PREMIUM rather
    than pausing — the operator paid for exactly that. Whether the escalation names
    THIS task is a gate/spend-time check, not a string guess.
    """
    index = max(0, int(attempts_used))
    if index < len(policy.ladder):
        return policy.ladder[index].value
    if policy.escalation is not None:
        return CostTier.PREMIUM.value
    return LADDER_TERMINAL


class ExhaustionReason(str, enum.Enum):
    CALLS = "calls"
    TOKENS = "tokens"
    COST = "cost"
    WALL_CLOCK = "wall_clock"


class BudgetExhausted(RuntimeError):
    """A reservation crossed a hard ceiling. ``reason`` names which one."""

    def __init__(self, reason: ExhaustionReason, detail: str = "") -> None:
        super().__init__(detail or f"budget exhausted: {reason.value}")
        self.reason = reason


@dataclass
class SpendMeter:
    """The enforcement point for one task's ceilings.

    ``try_reserve_call`` is the atomic door: under the lock it checks EVERY ceiling
    (including wall clock) and only then increments ``calls_used``, so a budget race
    with one remaining call produces exactly one winner. ``record_usage`` books
    completed-call usage after the fact and never widens anything.
    """

    ceilings: SpendCeilings
    clock: Callable[[], float] = field(default=time.monotonic)

    def __post_init__(self) -> None:
        if not isinstance(self.ceilings, SpendCeilings):
            raise CostLadderError("a meter requires typed SpendCeilings")
        # RLock: exhaustion()/snapshot() are called while the door holds the lock.
        self._lock = threading.RLock()
        self._started_at = self.clock()
        self._calls_used = 0
        self._tokens_used = 0
        self._cost_used = 0.0

    # ------------------------------------------------------------------ state
    @property
    def calls_used(self) -> int:
        with self._lock:
            return self._calls_used

    def exhaustion(self) -> ExhaustionReason | None:
        """Which ceiling is already crossed, or None. Pure — reserves nothing."""
        with self._lock:
            if self._calls_used >= self.ceilings.max_calls:
                return ExhaustionReason.CALLS
            if self._tokens_used >= self.ceilings.max_tokens:
                return ExhaustionReason.TOKENS
            if self._cost_used >= self.ceilings.max_cost_usd:
                return ExhaustionReason.COST
            if self.clock() - self._started_at >= self.ceilings.wall_clock_seconds:
                return ExhaustionReason.WALL_CLOCK
        return None

    # ------------------------------------------------------------------- door
    def try_reserve_call(self) -> int:
        """Atomically claim the next call slot, or raise :class:`BudgetExhausted`."""
        with self._lock:
            reason = self.exhaustion()
            if reason is not None:
                raise BudgetExhausted(reason)
            self._calls_used += 1
            return self._calls_used

    def record_usage(self, *, tokens: int = 0, cost_usd: float = 0.0) -> SpendMeter:
        """Book what a completed call consumed. Returns self for test ergonomics.

        Usage is booked as-is — a call that crossed the token ceiling mid-flight
        still happened, and the books must say so; the EXHAUSTION this produces then
        blocks every future reservation.
        """
        with self._lock:
            self._tokens_used += max(0, int(tokens))
            self._cost_used += max(0.0, float(cost_usd))
        return self

    def snapshot(self) -> dict[str, Any]:
        """The books as a ledger-shaped row: usage, ceilings, exhaustion."""
        with self._lock:
            return {
                "calls_used": self._calls_used,
                "tokens_used": self._tokens_used,
                "cost_used": round(self._cost_used, 6),
                "elapsed_seconds": round(self.clock() - self._started_at, 3),
                "max_calls": self.ceilings.max_calls,
                "max_tokens": self.ceilings.max_tokens,
                "max_cost_usd": self.ceilings.max_cost_usd,
                "wall_clock_seconds": self.ceilings.wall_clock_seconds,
                "exhaustion": self.exhaustion(),
            }


__all__ = [
    "DEFAULT_LADDER",
    "LADDER_TERMINAL",
    "BudgetExhausted",
    "CostLadderError",
    "CostPolicy",
    "CostTier",
    "EscalationRefused",
    "ExhaustionReason",
    "OperatorEscalation",
    "SpendCeilings",
    "SpendMeter",
    "mint_operator_escalation",
    "next_tier",
]
