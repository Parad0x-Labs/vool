"""Models group — model truth from the runtime capability authority."""
from __future__ import annotations

from dataclasses import dataclass

from core.command_registry.spec import (
    Availability,
    CommandSpec,
    GroupSpec,
    Handler,
    HandlerOk,
)


@dataclass(frozen=True)
class ListInput:
    include_unavailable: bool = False


def _probe_capability_runtime(context: dict) -> tuple[bool, str]:
    try:
        from core.runtime_capabilities import runtime_capability_snapshot

        snap = runtime_capability_snapshot()
        if not isinstance(snap, dict):
            return False, "capability snapshot unreadable"
        return True, ""
    except Exception as exc:
        return False, f"capability authority unreachable: {exc}"


def _handle_models_list(inp, ctx):
    from core.runtime_capabilities import runtime_capability_snapshot

    snap = runtime_capability_snapshot()
    truth = snap.get("provider_capability_truth") or []
    models = []
    seen: set[str] = set()
    default_model = str(snap.get("model_name") or snap.get("default_model") or "")
    if default_model:
        models.append({"id": default_model, "provider": "vool-runtime", "default": True})
        seen.add(default_model)
    for item in truth:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("model_id") or "").strip()
        provider = str(item.get("provider_id") or "vool-runtime")
        if model_id and model_id not in seen:
            models.append({"id": model_id, "provider": provider, "default": model_id == default_model})
            seen.add(model_id)
    return HandlerOk(
        data={"count": len(models), "models": models, "default": default_model},
        summary=f"{len(models)} models visible to the runtime (default: {default_model or 'unset'})",
    )


def _handle_models_capabilities(inp, ctx):
    from core.runtime_capabilities import runtime_capability_snapshot

    snap = runtime_capability_snapshot()
    lanes = snap.get("model_lanes") or snap.get("lanes") or []
    return HandlerOk(
        data={"snapshot_keys": sorted(snap.keys()), "lanes": lanes},
        summary="Runtime capability snapshot (model lanes)",
    )


def register(reg) -> None:
    reg.add_group(GroupSpec(group_id="models", description="model truth: what the runtime can run, from the capability authority"))
    reg.add(
        CommandSpec(
            command_id="models.list",
            group="models",
            description="List models visible to the runtime with their providers",
            aliases=("models",),
            input_schema=ListInput,
            effects="read_only",
            capabilities=frozenset({"model_catalog.read"}),
            handler=Handler("core.command_registry.groups.models:_handle_models_list"),
            availability=Availability("core.command_registry.groups.models:_probe_capability_runtime"),
            exit_codes=(0, 2, 10),
            model_offerable=True
        )
    )
    reg.add(
        CommandSpec(
            command_id="models.capabilities",
            group="models",
            description="Show the runtime capability snapshot's model lanes",
            effects="read_only",
            capabilities=frozenset({"model_catalog.read"}),
            handler=Handler("core.command_registry.groups.models:_handle_models_capabilities"),
            availability=Availability("core.command_registry.groups.models:_probe_capability_runtime"),
            exit_codes=(0, 10),
        )
    )
