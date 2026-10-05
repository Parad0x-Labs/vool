"""Agent teams group — the chat starts agents, plain code coordinates them.

Binds to ``core.agent_team.service``. ``agents.status`` is a read and the only command offered to
the model (the registry's law this milestone: model-offerable means read-only). ``agents.start``
spends against hard per-agent and per-team limits, and ``agents.stop`` / ``agents.decide`` /
``agents.answer`` change running work: all four are the operator's own gestures, gated here.

Registration is one line in ``groups/__init__.py`` (owned by another lane; sent as a patch).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from core.command_registry.spec import (
    ApprovalDecision,
    ApprovalGate,
    Availability,
    CommandSpec,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
)


@dataclass(frozen=True)
class StartInput:
    session_id: str
    agents: list = dataclasses.field(default_factory=list)
    team_limits: dict = dataclasses.field(default_factory=dict)
    workspace: str = ""
    chat_text: str = ""
    base_url: str = ""


@dataclass(frozen=True)
class SessionInput:
    session_id: str


@dataclass(frozen=True)
class StopInput:
    session_id: str
    agent: str = ""


@dataclass(frozen=True)
class DecideInput:
    session_id: str
    alert_id: str
    choice: str


@dataclass(frozen=True)
class AnswerInput:
    session_id: str
    agent: str
    decision: str


def _gate_start(inp, ctx) -> ApprovalDecision:
    approval = ctx.approval_context or {}
    if str(ctx.principal or "") == "operator" and approval.get("gesture"):
        return ApprovalDecision(required=False)
    return ApprovalDecision(required=True, reason="starting agents spends against your limits; approve the plan once")


def _gate_operator(inp, ctx) -> ApprovalDecision:
    if str(ctx.principal or "") != "operator":
        return ApprovalDecision(required=True, reason="changing running agents is the operator's decision")
    return ApprovalDecision(required=False)


def _probe_teams_root(context: dict) -> tuple[bool, str]:
    try:
        from core.agent_team.service import teams_root

        root = teams_root()
        probe = root / ".probe"
        probe.write_text("ok")
        probe.unlink()
        return True, f"team store writable at {root}"
    except Exception as exc:
        return False, f"team store unavailable: {exc}"


def _result(payload: dict, summary: str):
    if not payload.get("ok", True):
        return HandlerFault(fault_code="fault_validation", summary=str(payload.get("error") or summary), detail=payload)
    return HandlerOk(data=payload, summary=summary)


def _handle_start(inp, ctx):
    from core.agent_team import service
    from core.runtime_paths import resolve_workspace_root

    workspace = str(resolve_workspace_root(inp.workspace or None))
    payload = service.start(inp.session_id, inp.agents, team_limits=inp.team_limits, workspace=workspace,
                            chat_text=inp.chat_text, base_url=inp.base_url)
    return _result(payload, "; ".join(payload.get("status") or []) or "agents started")


def _handle_status(inp, ctx):
    from core.agent_team import service

    payload = service.status(inp.session_id)
    return _result(payload, "; ".join(payload.get("lines") or []) or "no agents in this chat")


def _handle_stop(inp, ctx):
    from core.agent_team import service

    payload = service.stop(inp.session_id, inp.agent)
    return _result(payload, "stopped: " + ", ".join(payload.get("stopped") or []))


def _handle_decide(inp, ctx):
    from core.agent_team import service

    payload = service.decide(inp.session_id, inp.alert_id, inp.choice)
    return _result(payload, f"{payload.get('running', '')} runs; {payload.get('paused', '')} paused".strip("; "))


def _handle_answer(inp, ctx):
    from core.agent_team import service

    payload = service.answer(inp.session_id, inp.agent, inp.decision)
    return _result(payload, f"{payload.get('agent', '')} continues with your decision")


def register(reg) -> None:
    reg.add_group(GroupSpec(group_id="agents", description="agent teams: start, watch, pause and stop agents for this chat"))
    base = {"group": "agents", "exit_codes": (0, 2, 10, 20), "lifecycle": "preview"}
    probe = Availability("core.command_registry.groups.agent_team_group:_probe_teams_root")
    operator = ApprovalGate(kind="agents.control", verifier="core.command_registry.groups.agent_team_group:_gate_operator")
    reg.add(CommandSpec(
        command_id="agents.start", description="Start a team of agents for this chat under hard spend limits",
        input_schema=StartInput, effects="spend", capabilities=frozenset({"agents.run"}),
        permission=ApprovalGate(kind="agents.start", verifier="core.command_registry.groups.agent_team_group:_gate_start"),
        availability=probe, handler=Handler("core.command_registry.groups.agent_team_group:_handle_start"), **base))
    reg.add(CommandSpec(
        command_id="agents.status", description="Show this chat's agents, their progress, spend and alerts",
        input_schema=SessionInput, effects="read_only", capabilities=frozenset({"agents.read"}),
        handler=Handler("core.command_registry.groups.agent_team_group:_handle_status"), model_offerable=True, **base))
    for command_id, description, schema, handler in (
        ("agents.stop", "Stop one agent by its task name, or all of this chat's agents", StopInput, "_handle_stop"),
        ("agents.decide", "Answer an overlap alert: wait (recommended), swap or stop", DecideInput, "_handle_decide"),
        ("agents.answer", "Give an agent the decision it asked for so it can continue", AnswerInput, "_handle_answer"),
    ):
        reg.add(CommandSpec(
            command_id=command_id, description=description, input_schema=schema, effects="mutating",
            capabilities=frozenset({"agents.run"}), permission=operator, availability=probe,
            handler=Handler(f"core.command_registry.groups.agent_team_group:{handler}"), **base))
