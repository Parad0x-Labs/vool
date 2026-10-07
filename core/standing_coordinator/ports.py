"""The seam between the coordinator and whatever runs the agents.

A port is the team's own registry, seen through five calls. The coordinator owns no
process and no session: every effect goes through the port, and the port acts only on
agents it started. A port that cannot do something says ``unsupported``; it never
pretends.

Team ids carry their port as a prefix (``council:<run_id>``, ``team:<team_id>``), so a
watch can be resolved to its port after a restart without any other state.

Ports registered here:
  * ``council`` — council runs, from :mod:`core.standing_coordinator.council_port`;
    registered on first resolve.
  * ``team`` — Agent Teams (``core/agent_team/``). DEPENDENCY: that package is not on
    main yet; it registers its port on import with ``register_port("team", ...)``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Protocol, runtime_checkable

from core.standing_coordinator.model import PortReceipt, StandingCoordinatorError, TeamSnapshot


@runtime_checkable
class TeamPort(Protocol):
    def snapshot(self, team_id: str) -> TeamSnapshot | None:
        """The team as its registry records it now, or None when the registry has no such team."""

    def nudge(self, team_id: str, agent_id: str, text: str) -> PortReceipt:
        """Deliver a status-check message into a running agent's own chat."""

    def answer(self, team_id: str, agent_id: str, request_id: str, answer: str, decision_id: str) -> PortReceipt:
        """Hand a waiting agent the answer to its request, with the decision it came from."""

    def pause(self, team_id: str, agent_id: str) -> PortReceipt:
        """Pause one agent the port started. Its spend stops; its work is kept."""

    def stop(self, team_id: str, agent_id: str) -> PortReceipt:
        """Stop one agent the port started, through the port's own registry."""


_LOCK = threading.Lock()
_FACTORIES: dict[str, Callable[[], TeamPort]] = {}
_INSTANCES: dict[str, TeamPort] = {}


def _prefix(team_id: str) -> str:
    text = str(team_id or "")
    prefix, sep, rest = text.partition(":")
    if not sep or not prefix or not rest:
        raise StandingCoordinatorError("a team id is '<port>:<id>', for example 'team:abc' or 'council:run-1'")
    return prefix


def register_port(prefix: str, factory: Callable[[], TeamPort]) -> None:
    """Register (or replace) the port for one team-id prefix."""
    name = str(prefix or "").strip()
    if not name or ":" in name:
        raise StandingCoordinatorError("a port prefix is a bare name")
    with _LOCK:
        _FACTORIES[name] = factory
        _INSTANCES.pop(name, None)


def _builtin(prefix: str) -> Callable[[], TeamPort] | None:
    if prefix == "council":
        from core.standing_coordinator.council_port import CouncilRunPort

        return CouncilRunPort
    return None


def resolve_port(team_id: str) -> TeamPort:
    prefix = _prefix(team_id)
    with _LOCK:
        port = _INSTANCES.get(prefix)
        if port is not None:
            return port
        factory = _FACTORIES.get(prefix) or _builtin(prefix)
        if factory is None:
            raise StandingCoordinatorError(f"no port is registered for '{prefix}' teams")
        port = factory()
        _INSTANCES[prefix] = port
        return port


def reset_ports_for_tests() -> None:
    with _LOCK:
        _FACTORIES.clear()
        _INSTANCES.clear()


__all__ = ["TeamPort", "register_port", "reset_ports_for_tests", "resolve_port"]
