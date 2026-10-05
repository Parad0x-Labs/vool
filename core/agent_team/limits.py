"""Hard spend limits per agent and per team: dollars, tokens, calls and wall clock.

Every model call is RESERVED before it is made. A reservation names the most the call can cost
(prompt estimate + output ceiling, at the model's price) and is checked against BOTH the agent's
and the team's remaining budget under one lock. If either would be crossed, the call is refused
before it happens — never discovered after. When the call returns, the reservation is settled
with what the provider actually reported; settling never widens a limit.

Wall clock counts running time only. Time an agent spends paused (by an overlap alert, or by
the user) is not billed against it: a pause is the coordinator's decision, and charging the
paused agent for it would turn every alert into a partial result.

No limit may be unbounded. A team or agent without a positive value on every axis is refused.
"""

from __future__ import annotations

import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from typing import Any


class LimitsRefused(ValueError):
    """A limit was missing, zero, negative or unbounded."""


class BudgetRefused(RuntimeError):
    """A reservation would cross a hard limit. ``scope`` is ``agent`` or ``team``."""

    def __init__(self, axis: str, scope: str, detail: str) -> None:
        super().__init__(detail)
        self.axis = axis
        self.scope = scope
        self.detail = detail


@dataclass(frozen=True)
class Limits:
    max_usd: float
    max_tokens: int
    max_calls: int
    wall_clock_seconds: float

    def __post_init__(self) -> None:
        for name in ("max_tokens", "max_calls"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise LimitsRefused(f"{name} must be a positive integer; unbounded is refused")
        for name in ("max_usd", "wall_clock_seconds"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
                raise LimitsRefused(f"{name} must be positive; unbounded is refused")
            if value != value or value == float("inf"):
                raise LimitsRefused(f"{name} must be finite")
        object.__setattr__(self, "max_usd", float(self.max_usd))
        object.__setattr__(self, "wall_clock_seconds", float(self.wall_clock_seconds))

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> Limits:
        if not isinstance(raw, dict):
            raise LimitsRefused("limits are required: max_usd, max_tokens, max_calls, wall_clock_seconds")
        try:
            return cls(
                max_usd=raw["max_usd"],
                max_tokens=raw["max_tokens"],
                max_calls=raw["max_calls"],
                wall_clock_seconds=raw["wall_clock_seconds"],
            )
        except KeyError as exc:
            raise LimitsRefused(f"limit {exc.args[0]} is missing; unbounded is refused") from None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def narrowed_by(self, other: Limits) -> Limits:
        """The tighter of two limit sets on every axis (a child never outspends its parent)."""
        return Limits(
            max_usd=min(self.max_usd, other.max_usd),
            max_tokens=min(self.max_tokens, other.max_tokens),
            max_calls=min(self.max_calls, other.max_calls),
            wall_clock_seconds=min(self.wall_clock_seconds, other.wall_clock_seconds),
        )


class PausableClock:
    """Elapsed running time; paused intervals are excluded."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, *, carried: float = 0.0) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._carried = max(0.0, float(carried))
        self._running_since: float | None = None

    def start(self) -> None:
        with self._lock:
            if self._running_since is None:
                self._running_since = self._clock()

    def pause(self) -> None:
        with self._lock:
            if self._running_since is not None:
                self._carried += self._clock() - self._running_since
                self._running_since = None

    resume = start

    @property
    def paused(self) -> bool:
        with self._lock:
            return self._running_since is None

    def elapsed(self) -> float:
        with self._lock:
            live = 0.0 if self._running_since is None else self._clock() - self._running_since
            return self._carried + live


@dataclass
class _Books:
    limits: Limits
    usd: float = 0.0
    tokens: int = 0
    calls: int = 0
    reserved_usd: float = 0.0
    reserved_tokens: int = 0

    def remaining(self) -> dict[str, float]:
        return {
            "usd": self.limits.max_usd - self.usd - self.reserved_usd,
            "tokens": self.limits.max_tokens - self.tokens - self.reserved_tokens,
            "calls": self.limits.max_calls - self.calls,
        }

    def snapshot(self) -> dict[str, Any]:
        return {
            "usd": round(self.usd, 6),
            "tokens": self.tokens,
            "calls": self.calls,
            "reserved_usd": round(self.reserved_usd, 6),
            "reserved_tokens": self.reserved_tokens,
            "limits": self.limits.to_dict(),
        }


@dataclass(frozen=True)
class Reservation:
    reservation_id: str
    agent_id: str
    tokens: int
    usd: float


@dataclass
class TeamBudget:
    """The one door every model call of a team passes through."""

    team_limits: Limits
    clock: Callable[[], float] = field(default=time.monotonic)

    def __post_init__(self) -> None:
        self._lock = threading.RLock()
        self._team = _Books(self.team_limits)
        self._agents: dict[str, _Books] = {}
        self._clocks: dict[str, PausableClock] = {}
        self._open: dict[str, Reservation] = {}

    # ------------------------------------------------------------- agents
    def add_agent(self, agent_id: str, limits: Limits, *, carried_seconds: float = 0.0,
                  spent: dict[str, Any] | None = None) -> None:
        with self._lock:
            books = _Books(limits.narrowed_by(self.team_limits))
            if spent:
                books.usd = float(spent.get("usd") or 0.0)
                books.tokens = int(spent.get("tokens") or 0)
                books.calls = int(spent.get("calls") or 0)
                self._team.usd += books.usd
                self._team.tokens += books.tokens
                self._team.calls += books.calls
            self._agents[agent_id] = books
            self._clocks[agent_id] = PausableClock(self.clock, carried=carried_seconds)

    def clock_for(self, agent_id: str) -> PausableClock:
        with self._lock:
            return self._clocks[agent_id]

    def limits_for(self, agent_id: str) -> Limits:
        with self._lock:
            return self._agents[agent_id].limits

    # --------------------------------------------------------------- door
    def wall_clock_exhausted(self, agent_id: str) -> bool:
        with self._lock:
            books = self._agents[agent_id]
            return self._clocks[agent_id].elapsed() >= books.limits.wall_clock_seconds

    def reserve(self, agent_id: str, *, tokens: int, usd: float) -> Reservation:
        """Reserve the most one call can cost, or refuse before the call is made."""
        tokens = max(0, int(tokens))
        usd = max(0.0, float(usd))
        with self._lock:
            books = self._agents.get(agent_id)
            if books is None:
                raise BudgetRefused("agent", "agent", f"unknown agent {agent_id!r}")
            if self._clocks[agent_id].elapsed() >= books.limits.wall_clock_seconds:
                raise BudgetRefused("wall_clock", "agent", "the agent's running-time limit is used up")
            for scope, book in (("agent", books), ("team", self._team)):
                left = book.remaining()
                if left["calls"] < 1:
                    raise BudgetRefused("calls", scope, f"the {scope}'s call limit is used up")
                if tokens > left["tokens"]:
                    raise BudgetRefused(
                        "tokens", scope,
                        f"this call could use {tokens} tokens; the {scope} has {max(0, int(left['tokens']))} left",
                    )
                if usd > left["usd"] + 1e-12:
                    raise BudgetRefused(
                        "usd", scope,
                        f"this call could cost ${usd:.4f}; the {scope} has ${max(0.0, left['usd']):.4f} left",
                    )
            for book in (books, self._team):
                book.calls += 1
                book.reserved_tokens += tokens
                book.reserved_usd += usd
            reservation = Reservation(uuid.uuid4().hex, agent_id, tokens, usd)
            self._open[reservation.reservation_id] = reservation
            return reservation

    def settle(self, reservation: Reservation, *, tokens: int, usd: float) -> None:
        """Book what the call actually used and release its reservation. Idempotent."""
        with self._lock:
            if self._open.pop(reservation.reservation_id, None) is None:
                return
            for book in (self._agents[reservation.agent_id], self._team):
                book.reserved_tokens -= reservation.tokens
                book.reserved_usd -= reservation.usd
                book.tokens += max(0, int(tokens))
                book.usd += max(0.0, float(usd))

    def snapshot(self, agent_id: str | None = None) -> dict[str, Any]:
        with self._lock:
            if agent_id is None:
                return self._team.snapshot()
            row = self._agents[agent_id].snapshot()
            row["elapsed_seconds"] = round(self._clocks[agent_id].elapsed(), 3)
            return row


__all__ = [
    "BudgetRefused",
    "Limits",
    "LimitsRefused",
    "PausableClock",
    "Reservation",
    "TeamBudget",
]
