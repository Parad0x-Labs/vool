"""Typed views the coordinator reads and the policy it runs under.

A snapshot is what the team's registry says right now. The coordinator never edits a
snapshot and never fills a missing field with a guess: an agent with no recorded
progress time has ``last_progress_at=None``, and that agent is judged on its start time
instead of on an invented one.
"""

from __future__ import annotations

import enum
import math
from dataclasses import dataclass, field
from typing import Any

from core.council.cost_ladder import SpendCeilings


class StandingCoordinatorError(ValueError):
    """A typed refusal from the coordinator: bad input, never a silent default."""


class AgentState(str, enum.Enum):
    RUNNING = "running"
    WAITING = "waiting"  # stopped on a decision only someone else can make
    PAUSED = "paused"
    DONE = "done"
    PARTIAL = "partial"  # ended early (budget, stop) with a labelled partial result
    FAILED = "failed"
    STOPPED = "stopped"


TERMINAL_STATES = frozenset({AgentState.DONE, AgentState.PARTIAL, AgentState.FAILED, AgentState.STOPPED})

#: Request kinds the coordinator never answers from memory, whatever a stored decision
#: says. Each is an action nobody can take back by the time the user reads about it.
ALWAYS_ASK_KINDS = frozenset({
    "git_push", "deploy", "publish", "external_message", "payment", "spend_increase",
    "delete", "credential", "merge",
})


@dataclass(frozen=True)
class SpendView:
    cost_usd: float = 0.0
    tokens: int = 0
    calls: int = 0

    def __post_init__(self) -> None:
        cost = float(self.cost_usd or 0.0)
        if math.isnan(cost) or cost < 0:
            raise StandingCoordinatorError("spend cost must be a non-negative number")
        object.__setattr__(self, "cost_usd", cost)
        object.__setattr__(self, "tokens", max(0, int(self.tokens or 0)))
        object.__setattr__(self, "calls", max(0, int(self.calls or 0)))

    def plus(self, other: SpendView) -> SpendView:
        return SpendView(self.cost_usd + other.cost_usd, self.tokens + other.tokens, self.calls + other.calls)

    def as_row(self) -> dict[str, Any]:
        return {"cost_usd": round(self.cost_usd, 6), "tokens": self.tokens, "calls": self.calls}


@dataclass(frozen=True)
class DecisionRequest:
    """One question an agent stopped on.

    ``topic_key`` names the decision itself ("dependency.add.httpx"), so a second agent
    asking the same thing gets the same answer. A request with no topic key can only be
    answered by the user, one at a time.
    """

    request_id: str
    question: str
    topic_key: str = ""
    kind: str = ""
    options: tuple[str, ...] = ()
    irreversible: bool = False

    def __post_init__(self) -> None:
        if not str(self.request_id or "").strip():
            raise StandingCoordinatorError("a decision request needs a request id")
        if not str(self.question or "").strip():
            raise StandingCoordinatorError("a decision request needs its question")
        object.__setattr__(self, "topic_key", str(self.topic_key or "").strip())
        object.__setattr__(self, "kind", str(self.kind or "").strip().lower())
        object.__setattr__(self, "options", tuple(str(o) for o in (self.options or ())))

    @property
    def must_ask_user(self) -> bool:
        return bool(self.irreversible) or self.kind in ALWAYS_ASK_KINDS or not self.topic_key


@dataclass(frozen=True)
class AgentSnapshot:
    agent_id: str
    state: AgentState
    started_at: float
    label: str = ""
    last_progress_at: float | None = None
    request: DecisionRequest | None = None
    spend: SpendView = field(default_factory=SpendView)
    #: The agent's own typed result, shown to the user labelled as the agent's words.
    summary: str = ""

    def __post_init__(self) -> None:
        if not str(self.agent_id or "").strip():
            raise StandingCoordinatorError("an agent snapshot needs an agent id")
        object.__setattr__(self, "state", AgentState(self.state))
        if self.state is AgentState.WAITING and self.request is None:
            raise StandingCoordinatorError(f"agent {self.agent_id} is waiting but names no request")

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def progress_mark(self) -> float:
        """The time progress was last seen: the recorded one, else the start time."""
        return float(self.last_progress_at if self.last_progress_at is not None else self.started_at)


@dataclass(frozen=True)
class TeamSnapshot:
    team_id: str
    agents: tuple[AgentSnapshot, ...]
    started_at: float
    title: str = ""
    #: The chat the team was started from; reports land there.
    session_id: str = ""
    #: The registry's own team total, when it keeps one. Else the sum of the agents.
    spend: SpendView | None = None

    def total_spend(self) -> SpendView:
        if self.spend is not None:
            return self.spend
        total = SpendView()
        for agent in self.agents:
            total = total.plus(agent.spend)
        return total

    @property
    def all_terminal(self) -> bool:
        return bool(self.agents) and all(agent.terminal for agent in self.agents)


@dataclass(frozen=True)
class PortReceipt:
    """What a port says an action did. ``applied`` is the only effect that counts as done."""

    ok: bool
    effect: str  # applied | unsupported | refused | not_found
    detail: str = ""

    @classmethod
    def applied(cls, detail: str = "") -> PortReceipt:
        return cls(True, "applied", detail)

    @classmethod
    def unsupported(cls, detail: str = "") -> PortReceipt:
        return cls(False, "unsupported", detail)


@dataclass(frozen=True)
class WatchPolicy:
    """How patiently the coordinator watches one team.

    ``ceilings`` is the team-wide cap. The agents' own meters stop each agent at its own
    cap; this is the backstop over all of them, checked on every tick.
    """

    stall_after_seconds: float = 600.0
    nudge_gap_seconds: float = 300.0
    max_nudges: int = 2
    report_every_seconds: float = 900.0
    spend_warn_fraction: float = 0.8
    auto_pause_stalled: bool = False
    ceilings: SpendCeilings | None = None

    def __post_init__(self) -> None:
        for name in ("stall_after_seconds", "nudge_gap_seconds", "report_every_seconds"):
            value = float(getattr(self, name))
            if math.isnan(value) or value < 30.0:
                raise StandingCoordinatorError(f"{name} must be at least 30 seconds")
            object.__setattr__(self, name, value)
        if not isinstance(self.max_nudges, int) or isinstance(self.max_nudges, bool) or not 0 <= self.max_nudges <= 5:
            raise StandingCoordinatorError("max_nudges must be an integer from 0 to 5")
        fraction = float(self.spend_warn_fraction)
        if math.isnan(fraction) or not 0.1 <= fraction < 1.0:
            raise StandingCoordinatorError("spend_warn_fraction must be at least 0.1 and below 1")
        object.__setattr__(self, "spend_warn_fraction", fraction)
        if self.ceilings is not None and not isinstance(self.ceilings, SpendCeilings):
            raise StandingCoordinatorError("team ceilings must be typed SpendCeilings")

    def as_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "stall_after_seconds": self.stall_after_seconds,
            "nudge_gap_seconds": self.nudge_gap_seconds,
            "max_nudges": self.max_nudges,
            "report_every_seconds": self.report_every_seconds,
            "spend_warn_fraction": self.spend_warn_fraction,
            "auto_pause_stalled": self.auto_pause_stalled,
            "ceilings": None,
        }
        if self.ceilings is not None:
            row["ceilings"] = {
                "max_calls": self.ceilings.max_calls,
                "max_tokens": self.ceilings.max_tokens,
                "max_cost_usd": self.ceilings.max_cost_usd,
                "wall_clock_seconds": self.ceilings.wall_clock_seconds,
            }
        return row

    @classmethod
    def from_row(cls, row: dict[str, Any] | None) -> WatchPolicy:
        data = dict(row or {})
        allowed = {
            "stall_after_seconds", "nudge_gap_seconds", "max_nudges", "report_every_seconds",
            "spend_warn_fraction", "auto_pause_stalled", "ceilings",
        }
        unknown = set(data) - allowed
        if unknown:
            raise StandingCoordinatorError(f"unknown policy fields: {sorted(unknown)}")
        ceilings_row = data.pop("ceilings", None)
        ceilings = None
        if ceilings_row is not None:
            if not isinstance(ceilings_row, dict):
                raise StandingCoordinatorError("ceilings must be an object")
            try:
                ceilings = SpendCeilings(**ceilings_row)
            except (TypeError, ValueError) as exc:
                raise StandingCoordinatorError(f"invalid team ceilings: {exc}") from exc
        if "auto_pause_stalled" in data and not isinstance(data["auto_pause_stalled"], bool):
            raise StandingCoordinatorError("auto_pause_stalled must be true or false")
        try:
            return cls(ceilings=ceilings, **data)
        except (TypeError, ValueError) as exc:
            if isinstance(exc, StandingCoordinatorError):
                raise
            raise StandingCoordinatorError(f"invalid watch policy: {exc}") from exc


@dataclass(frozen=True)
class Action:
    """One thing the planner wants done, keyed so it happens at most once.

    ``covers`` are further keys claimed in the same step: a report that announces three
    finished agents claims all three announcements, so none is announced twice.
    """

    key: str
    kind: str  # nudge | answer | ask_user | escalate | pause | stop | report | end_watch
    team_id: str
    agent_id: str = ""
    text: str = ""
    payload: dict[str, Any] = field(default_factory=dict)
    covers: tuple[str, ...] = ()
    section: str = "updates"  # updates | needs (where a user-facing item is filed)


__all__ = [
    "ALWAYS_ASK_KINDS",
    "TERMINAL_STATES",
    "Action",
    "AgentSnapshot",
    "AgentState",
    "DecisionRequest",
    "PortReceipt",
    "SpendView",
    "StandingCoordinatorError",
    "TeamSnapshot",
    "WatchPolicy",
]
