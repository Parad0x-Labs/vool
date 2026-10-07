"""Standing coordinator: a long-lived watcher over a team of helper agents.

It watches a team across a long task, nudges agents that stopped making progress,
answers what is waiting on it from decisions the user already made, keeps those
decisions durably, and reports back to the user only when something changed.

It is deterministic code. It makes no model call of its own, so watching costs
zero model tokens however long the task runs. Everything it knows about an agent
comes from the team's own registry through a :class:`TeamPort`, never from an
agent's description of itself.

What it may do is closed: nudge, answer from a recorded decision, ask the user,
pause or stop an agent the port owns, and report. It never approves anything the
user has not decided, never answers an irreversible request from memory, and never
signals a process the port did not start.
"""

from __future__ import annotations

from core.standing_coordinator.coordinator import StandingCoordinator
from core.standing_coordinator.model import (
    ALWAYS_ASK_KINDS,
    AgentSnapshot,
    AgentState,
    DecisionRequest,
    PortReceipt,
    SpendView,
    TeamSnapshot,
    WatchPolicy,
)
from core.standing_coordinator.ports import TeamPort, register_port, resolve_port

__all__ = [
    "ALWAYS_ASK_KINDS",
    "AgentSnapshot",
    "AgentState",
    "DecisionRequest",
    "PortReceipt",
    "SpendView",
    "StandingCoordinator",
    "TeamPort",
    "TeamSnapshot",
    "WatchPolicy",
    "register_port",
    "resolve_port",
]
