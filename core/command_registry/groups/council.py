"""Council group — read surfaces bound to the council run store; controls gated.

Binds to ``core.council.api`` (status / events / stop / resume / runs). The
mutating controls carry an approval gate (the operator must present a resolved
approval context) and availability evidence (a run must exist to control).
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from core.command_registry.spec import (
    AuthorityDecision,
    Availability,
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
    NextAction,
    OperatorAuthority,
)


@dataclass(frozen=True)
class RunsInput:
    limit: int = 20


@dataclass(frozen=True)
class RunInput:
    run_id: str


@dataclass(frozen=True)
class EventsInput:
    run_id: str
    after: int = 0
    seq: str = ""


@dataclass(frozen=True)
class ResumeInput:
    run_id: str
    base_url: str = ""


@dataclass(frozen=True)
class ConveneInput:
    payload: dict = dataclasses.field(default_factory=dict)
    base_url: str = ""
    workspace_root: str = ""


@dataclass(frozen=True)
class SeatInput:
    run_id: str
    seat_id: str
    action: str
    model: str = ""


def _probe_run_store(context: dict) -> tuple[bool, str]:
    try:
        from core.council import api as council_api

        status_code, _payload = council_api.runs(limit=1)
        if status_code != 200:
            return False, f"council run store answered {status_code}"
        return True, ""
    except Exception as exc:
        return False, f"council run store unreachable: {exc}"


def _probe_runs_exist(context: dict) -> tuple[bool, str]:
    """The run STORE is reachable — whether any run exists is not an availability fact.

    It used to refuse when no run was recorded, which turned every control action on a
    dead or unknown run into a generic unavailable refusal (409) BEFORE the handler could
    answer. The handlers already answer typed, idempotent truths for missing runs
    (`no such council run` 404, `run_stopped` 409), and a typed answer is the law here;
    an availability gate that outranks it was hiding the fact the operator asked about.
    """
    try:
        from core.council import api as council_api

        status_code, _payload = council_api.runs(limit=1)
        if status_code != 200:
            return False, f"council run store answered {status_code}"
        return True, "council run store reachable"
    except Exception as exc:
        return False, f"council run store unreachable: {exc}"


def _gate_council_control(inp, ctx) -> AuthorityDecision:
    if str(ctx.principal or "") != "operator":
        return AuthorityDecision(granted=False, reason="council control is an operator decision")
    return AuthorityDecision(granted=True)


def _handle_runs(inp, ctx):
    from core.council import api as council_api

    limit = max(1, min(int(inp.limit or 20), 100))
    status_code, payload = council_api.runs(limit=limit)
    if status_code != 200:
        return HandlerFault(
            fault_code="fault_tool",
            summary=f"Council run store answered {status_code}",
            detail={"status_code": status_code, "payload": payload},
        )
    return HandlerOk(data=payload, summary="Council runs")


def _handle_status(inp, ctx):
    from core.council import api as council_api

    status_code, payload = council_api.status(inp.run_id)
    if status_code != 200:
        # `legacy_status` is what keeps the typed code alive across the registry seam:
        # without it an unknown run's 404 surfaced as a 500, lying about the fault.
        return _legacy_fault(
            "fault_validation", f"Council run {inp.run_id!r} not reachable ({status_code})",
            status_code, payload,
        )
    return HandlerOk(data=payload, summary=f"Council run {inp.run_id}")


def _handle_events(inp, ctx):
    from core.council import api as council_api

    seq = inp.seq if isinstance(inp.seq, (str, int)) and str(inp.seq) != "" else None
    status_code, payload = council_api.events(inp.run_id, inp.after, seq=seq)
    if status_code != 200:
        return _legacy_fault(
            "fault_validation", f"Council run {inp.run_id!r} not reachable ({status_code})",
            status_code, payload,
        )
    return HandlerOk(data=payload, summary=f"Council events for run {inp.run_id}")


def _handle_stop(inp, ctx):
    from core.council import api as council_api

    try:
        status_code, payload = council_api.stop(inp.run_id)
    except Exception as exc:
        return HandlerFault(
            fault_code="fault_tool",
            summary=f"Council stop for {inp.run_id!r} failed: {exc}",
            detail={"run_id": inp.run_id, "error": str(exc)},
        )
    if status_code != 200:
        return _legacy_fault(
            "fault_tool", f"Council stop for {inp.run_id!r} answered {status_code}",
            status_code, payload,
        )
    return HandlerOk(
        data=payload,
        summary=f"Council run {inp.run_id} stopped",
        receipts=({"kind": "council_control", "action": "stop", "run_id": inp.run_id},),
    )


def _handle_resume(inp, ctx):
    from core.council import api as council_api

    try:
        status_code, payload = council_api.resume(inp.run_id, base_url=inp.base_url or "http://127.0.0.1:11435")
    except Exception as exc:
        return HandlerFault(
            fault_code="fault_tool",
            summary=f"Council resume for {inp.run_id!r} failed: {exc}",
            detail={"run_id": inp.run_id, "error": str(exc)},
        )
    if status_code != 200:
        return _legacy_fault(
            "fault_tool", f"Council resume for {inp.run_id!r} answered {status_code}",
            status_code, payload,
        )
    return HandlerOk(
        data=payload,
        summary=f"Council run {inp.run_id} resumed",
        receipts=({"kind": "council_control", "action": "resume", "run_id": inp.run_id},),
    )


def _handle_convene(inp, ctx):
    from core.council import api as council_api

    status_code, payload = council_api.convene(
        dict(inp.payload or {}),
        base_url=inp.base_url,
        workspace_root=inp.workspace_root,
    )
    if status_code != 200:
        return _legacy_fault("fault_tool", f"Council convene answered {status_code}", status_code, payload)
    return HandlerOk(
        data=payload,
        summary="Council run convened",
        receipts=({"kind": "council_control", "action": "convene", "run_id": str((payload or {}).get("run_id") or "")},),
    )


def _handle_seat(inp, ctx):
    from core.council import api as council_api

    body = {"run_id": inp.run_id, "seat_id": inp.seat_id, "action": inp.action}
    if inp.model:
        body["model"] = inp.model
    status_code, payload = council_api.seat_action(body)
    if status_code != 200:
        return _legacy_fault("fault_tool", f"Council seat action answered {status_code}", status_code, payload)
    return HandlerOk(
        data=payload,
        summary=f"Council seat {inp.seat_id}: {inp.action}",
        receipts=({"kind": "council_control", "action": f"seat.{inp.action}", "run_id": inp.run_id, "seat_id": inp.seat_id},),
    )


def _handle_scorecard(inp, ctx):
    from core.council.scorecard import build_scorecard

    return HandlerOk(data={"ok": True, "scorecard": build_scorecard(inp.run_id)}, summary=f"Council scorecard for {inp.run_id}")


def _handle_lock(inp, ctx):
    from core.council.pin_lock import lock_payload

    return HandlerOk(data=lock_payload(), summary="Council pin lock state")


def _legacy_fault(code: str, summary: str, status: int, payload: dict | None = None):
    from core.command_registry.spec import HandlerFault

    detail: dict = {"legacy_status": int(status)}
    if isinstance(payload, dict):
        detail.update(payload)
    return HandlerFault(fault_code=code, summary=summary, detail=detail)


def register(reg) -> None:
    reg.add_group(GroupSpec(group_id="council", description="council runs: read surfaces and operator controls"))
    reg.add(
        CommandSpec(
            command_id="council.runs",
            group="council",
            description="List recorded council runs",
            aliases=("council",),
            input_schema=RunsInput,
            effects="read_only",
            capabilities=frozenset({"council.read"}),
            handler=Handler("core.command_registry.groups.council:_handle_runs"),
            availability=Availability("core.command_registry.groups.council:_probe_run_store"),
            exit_codes=(0, 2, 10, 40),
        )
    )
    reg.add(
        CommandSpec(
            command_id="council.status",
            group="council",
            description="Show one council run's status",
            input_schema=RunInput,
            effects="read_only",
            capabilities=frozenset({"council.read"}),
            handler=Handler("core.command_registry.groups.council:_handle_status"),
            exit_codes=(0, 2, 42),
        )
    )
    reg.add(
        CommandSpec(
            command_id="council.events",
            group="council",
            description="Show one council run's events",
            input_schema=EventsInput,
            effects="read_only",
            capabilities=frozenset({"council.read"}),
            handler=Handler("core.command_registry.groups.council:_handle_events"),
            exit_codes=(0, 2, 42),
        )
    )
    reg.add(
        CommandSpec(
            command_id="council.stop",
            group="council",
            description="Stop a running council run (operator approval required)",
            input_schema=RunInput,
            effects="mutating",
            capabilities=frozenset({"council.control"}),
            permission=OperatorAuthority(
                kind="council.control",
                verifier="core.command_registry.groups.council:_gate_council_control",
            ),
            handler=Handler("core.command_registry.groups.council:_handle_stop"),
            availability=Availability("core.command_registry.groups.council:_probe_runs_exist"),
            fault_bindings=(FaultBinding(when="run_missing", fault_code="fault_validation", remediation=("vool council runs",)),),
            exit_codes=(0, 2, 10, 20, 40, 42),
            lifecycle="preview",
            next_actions=(NextAction(command_id="council.runs", label="List council runs"),),
        )
    )
    reg.add(
        CommandSpec(
            command_id="council.resume",
            group="council",
            description="Resume a paused council run (operator approval required)",
            input_schema=ResumeInput,
            effects="mutating",
            capabilities=frozenset({"council.control"}),
            permission=OperatorAuthority(
                kind="council.control",
                verifier="core.command_registry.groups.council:_gate_council_control",
            ),
            handler=Handler("core.command_registry.groups.council:_handle_resume"),
            availability=Availability("core.command_registry.groups.council:_probe_runs_exist"),
            fault_bindings=(FaultBinding(when="run_missing", fault_code="fault_validation", remediation=("vool council runs",)),),
            exit_codes=(0, 2, 10, 20, 40, 42),
            lifecycle="preview",
            next_actions=(NextAction(command_id="council.runs", label="List council runs"),),
        )
    )

    reg.add(
        CommandSpec(
            command_id="council.convene",
            group="council",
            description="Convene a council run (spend-free by construction; operator-only)",
            input_schema=ConveneInput,
            effects="mutating",
            capabilities=frozenset({"council.control"}),
            permission=OperatorAuthority(
                kind="council.control",
                verifier="core.command_registry.groups.council:_gate_council_control",
            ),
            handler=Handler("core.command_registry.groups.council:_handle_convene"),
            availability=Availability("core.command_registry.groups.council:_probe_run_store"),
            exit_codes=(0, 2, 10, 21, 40),
            lifecycle="preview",
            next_actions=(NextAction(command_id="council.runs", label="List council runs"),),
        )
    )
    reg.add(
        CommandSpec(
            command_id="council.seat",
            group="council",
            description="One operator decision about one council seat (retry/re-model/remove)",
            input_schema=SeatInput,
            effects="mutating",
            capabilities=frozenset({"council.control"}),
            permission=OperatorAuthority(
                kind="council.control",
                verifier="core.command_registry.groups.council:_gate_council_control",
            ),
            handler=Handler("core.command_registry.groups.council:_handle_seat"),
            availability=Availability("core.command_registry.groups.council:_probe_runs_exist"),
            exit_codes=(0, 2, 10, 21, 40, 42),
            lifecycle="preview",
        )
    )
    reg.add(
        CommandSpec(
            command_id="council.scorecard",
            group="council",
            description="Recompute one run's scorecard from its ledger (never stored)",
            input_schema=RunInput,
            effects="read_only",
            capabilities=frozenset({"council.read"}),
            handler=Handler("core.command_registry.groups.council:_handle_scorecard"),
            exit_codes=(0, 2),
        )
    )
    reg.add(
        CommandSpec(
            command_id="council.lock",
            group="council",
            description="Show the council model-pin lock state",
            effects="read_only",
            capabilities=frozenset({"council.read"}),
            handler=Handler("core.command_registry.groups.council:_handle_lock"),
            exit_codes=(0,),
        )
    )
