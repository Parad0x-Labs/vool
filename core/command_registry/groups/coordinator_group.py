"""Coordinator group — the standing coordinator's watches, status and the user's decisions.

Binds to ``core.standing_coordinator.api``. Reads are read-only; watch, unwatch, decide
and forget are operator acts. Every handler returns the authority's own typed answer.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from core.command_registry.spec import (
    Availability,
    CommandSpec,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    OperatorAuthority,
)

_MODULE = "core.command_registry.groups.coordinator_group"


@dataclass(frozen=True)
class NoInput:
    pass


@dataclass(frozen=True)
class TeamInput:
    team_id: str


@dataclass(frozen=True)
class DecisionsInput:
    include_superseded: bool = False


@dataclass(frozen=True)
class PayloadInput:
    payload: dict = dataclasses.field(default_factory=dict)


def _probe_store(context: dict) -> tuple[bool, str]:
    """The coordinator's database opens and answers. Whether anything is watched is not
    an availability fact: the handlers answer typed truths for unknown teams."""
    try:
        from core.standing_coordinator import api

        status_code, _payload = api.watches()
        if status_code != 200:
            return False, f"coordinator store answered {status_code}"
        return True, "coordinator store reachable"
    except Exception as exc:
        return False, f"coordinator store unreachable: {exc}"


def _result(status_code: int, payload: dict, summary: str):
    if status_code == 200:
        return HandlerOk(data=payload, summary=summary)
    code = "fault_validation" if status_code in (400, 404) else "fault_tool"
    detail = {"legacy_status": int(status_code)}
    detail.update(payload or {})
    return HandlerFault(fault_code=code, summary=f"{summary} answered {status_code}", detail=detail)


def _handle_watches(inp, ctx):
    from core.standing_coordinator import api

    return _result(*api.watches(), "Coordinator watches")


def _handle_status(inp, ctx):
    from core.standing_coordinator import api

    return _result(*api.status(inp.team_id), f"Coordinator status for {inp.team_id}")


def _handle_decisions(inp, ctx):
    from core.standing_coordinator import api

    return _result(*api.decisions(bool(inp.include_superseded)), "Coordinator decisions")


def _handle_watch(inp, ctx):
    from core.standing_coordinator import api

    return _result(*api.watch(dict(inp.payload or {})), "Coordinator watch")


def _handle_unwatch(inp, ctx):
    from core.standing_coordinator import api

    return _result(*api.unwatch(dict(inp.payload or {})), "Coordinator unwatch")


def _handle_decide(inp, ctx):
    from core.standing_coordinator import api

    return _result(*api.decide(dict(inp.payload or {})), "Coordinator decision")


def _handle_forget(inp, ctx):
    from core.standing_coordinator import api

    return _result(*api.forget(dict(inp.payload or {})), "Coordinator forget")


def register(reg) -> None:
    reg.add_group(GroupSpec(group_id="coordinator", description="standing coordinator: watches, status, decisions"))
    reads = (
        ("watches", "List the teams the standing coordinator is watching", NoInput),
        ("status", "Show one watched team: agents, spend, open questions, recent events", TeamInput),
        ("decisions", "List the decisions the coordinator remembers", DecisionsInput),
    )
    for name, description, schema in reads:
        reg.add(
            CommandSpec(
                command_id=f"coordinator.{name}",
                group="coordinator",
                description=description,
                input_schema=schema,
                effects="read_only",
                capabilities=frozenset({"coordinator.read"}),
                handler=Handler(f"{_MODULE}:_handle_{name}"),
                exit_codes=(0, 2, 42),
            )
        )
    controls = (
        ("watch", "Start watching a team"),
        ("unwatch", "Stop watching a team"),
        ("decide", "Answer an agent's question, optionally remembered for later agents"),
        ("forget", "Forget a remembered decision"),
    )
    for name, description in controls:
        reg.add(
            CommandSpec(
                command_id=f"coordinator.{name}",
                group="coordinator",
                description=description,
                input_schema=PayloadInput,
                effects="mutating",
                capabilities=frozenset({"coordinator.control"}),
                permission=OperatorAuthority(
                    kind="coordinator.control",
                    verifier="core.command_registry.groups.convergence:_gate_operator",
                ),
                handler=Handler(f"{_MODULE}:_handle_{name}"),
                availability=Availability(f"{_MODULE}:_probe_store"),
                exit_codes=(0, 2, 10, 21, 42),
                lifecycle="preview",
            )
        )
