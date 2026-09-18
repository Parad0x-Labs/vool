"""Model-offerable projection: registry commands → the capability vocabulary.

ONLY specs with ``model_offerable=True`` become runtime tool contracts, under
the ``operator.command.*`` namespace. Operator-only commands are never offered
even though registered — and if the model somehow invokes one, the registry
executes it with ``principal="model"`` so every operator gate refuses.
"""
from __future__ import annotations

import dataclasses
from typing import Any

TOOL_NAMESPACE = "operator.command."


def tool_intent_for(command_id: str) -> str:
    return f"{TOOL_NAMESPACE}{command_id}"


def command_id_for(intent: str) -> str | None:
    if not intent.startswith(TOOL_NAMESPACE):
        return None
    return intent[len(TOOL_NAMESPACE) :]


def _input_schema(spec) -> dict[str, str]:
    if spec.input_schema is None:
        return {}
    out: dict[str, str] = {}
    for f in dataclasses.fields(spec.input_schema):
        out[f.name] = getattr(f.type, "__name__", str(f.type))
    return out


def contract_for_command(spec) -> Any:
    from core.runtime_tool_contracts import RuntimeToolContract

    return RuntimeToolContract(
        intent=tool_intent_for(spec.command_id),
        description=f"[registry] {spec.description}",
        tool_surface="command_registry",
        capability_id=next(iter(spec.capabilities), "operator.command"),
        capability_claim=spec.description,
        supported=True,
        unsupported_reason="",
        input_schema=_input_schema(spec),
        output_schema={"envelope": "json"},
        side_effect_class=spec.effects,
        approval_requirement="none" if spec.effects == "read_only" else "operator_authority",
        timeout_policy="standard",
        retry_policy="none",
        artifact_emission="none",
        error_contract="typed_envelope",
        handler="command_registry",  # ≠ "runtime": the runtime executor declines,
        # the command-registry lane in tool_intent_executor owns execution
    )


_PROJECTED: set[str] = set()


def project_model_tools() -> tuple[Any, ...]:
    """Register contracts for every model-offerable command (idempotent).

    Idempotence is judged against the LIVE tool registry, not a process-local cache: a
    registry reset (a test world, an embedder rebuilding the registry) must not leave the
    model-command contracts permanently absent because this projection memoized an epoch
    that no longer exists.
    """
    from core.command_registry.registry import registry
    from core.tool_registry import register as register_tool
    from core.tool_registry import registry_map

    live = registry_map()
    projected = []
    for spec in registry().commands():
        if not spec.model_offerable:
            continue
        intent = tool_intent_for(spec.command_id)
        if intent in live:
            projected.append(spec)
            continue
        try:
            register_tool(contract_for_command(spec))
            projected.append(spec)
        except Exception:
            _PROJECTED.add(intent)
    return tuple(projected)


def execute_model_command(
    intent: str,
    arguments: dict[str, Any],
    *,
    task_id: str = "",
    session_id: str = "",
) -> Any:
    """The model-lane execution of an ``operator.command.*`` tool call.

    The registry executes with ``principal="model"``: operator-only commands
    (OperatorAuthority / ApprovalGate) refuse; model-offerable reads execute
    through the same seam the CLI, chat and API use.
    """

    from core.command_registry.execute import ExecutionContext, execute_command
    from core.execution.models import ToolIntentExecution, _tool_observation

    command_id = command_id_for(intent)
    if command_id is None:
        return ToolIntentExecution(
            handled=True,
            ok=False,
            status="unknown_tool",
            response_text=f"{intent!r} is not a command-registry tool",
            mode="tool_failed",
            tool_name=intent,
            details={},
        )
    envelope = execute_command(
        command_id,
        dict(arguments or {}),
        context=ExecutionContext(
            projection="model",
            principal="model",
            # scoped identity for the budget door's direct leg; with an active turn
            # ledger the gateway resolves the session/turn identity itself
            extra={"session_id": str(session_id or ""), "task_id": str(task_id or "")},
        ),
    )
    payload = envelope.to_json()
    return ToolIntentExecution(
        handled=True,
        ok=bool(envelope.ok),
        status="executed" if envelope.ok else str(envelope.fault.code if envelope.fault else "failed"),
        response_text=envelope.summary,
        mode="tool_executed" if envelope.ok else "tool_failed",
        tool_name=intent,
        details={
            "envelope": payload,
            "task_id": task_id,
            "session_id": session_id,
            "observation": _tool_observation(
                intent=intent,
                tool_surface="command_registry",
                ok=bool(envelope.ok),
                status=str(envelope.fault.code if envelope.fault else "ok"),
                details={"exit_code": envelope.execution.exit_code},
                response_preview=envelope.summary[:280],
            ),
        },
    )
