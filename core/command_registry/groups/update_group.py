"""Update group — the one update flow, surfaced.

Binds to ``core.release_channel`` (the shipped channel truth) and the updater
subsystem's check seam (``core.updater.runtime.boot_update_subsystem``).
Two-press semantics live in the updater; this surface only reads and triggers
checks — apply remains on the updater's own authority.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.command_registry.spec import (
    ApprovalDecision,
    ApprovalGate,
    Availability,
    CommandSpec,
    FaultBinding,
    GroupSpec,
    Handler,
    HandlerFault,
    HandlerOk,
)


@dataclass(frozen=True)
class ChannelInput:
    pass


def _gate_check(inp, ctx) -> ApprovalDecision:
    # The named scope self_update.check is the updater's own; an operator-surface
    # trigger is the same gesture the chat chip makes — never widened.
    if str(ctx.principal or "") != "operator":
        return ApprovalDecision(required=True, reason="update check from a non-operator principal")
    return ApprovalDecision(required=False)


def _probe_release_config(context: dict) -> tuple[bool, str]:
    try:
        from core.release_channel import load_release_manifest

        load_release_manifest()
        return True, ""
    except Exception as exc:
        return False, f"release channel config unreadable: {exc}"


def _handle_channel(inp, ctx):
    from core.release_channel import release_manifest_snapshot, release_manifest_warnings

    snapshot = release_manifest_snapshot()
    warnings = release_manifest_warnings()
    channel = str(snapshot.get("channel_name") or "")
    return HandlerOk(
        data={"channel": channel, "manifest": snapshot, "warnings": warnings},
        summary=f"Update channel: {channel}" + (f" ({len(warnings)} warning(s))" if warnings else ""),
    )


def _handle_check(inp, ctx):
    from core.updater.runtime import boot_update_subsystem

    subsystem = boot_update_subsystem()
    payload = subsystem.trigger_check()
    check_ok = bool(payload.get("check_ok"))
    if not check_ok:
        return HandlerFault(
            fault_code="fault_provider",
            summary=f"Update check did not complete: {payload.get('error') or 'feed unreachable'}",
            detail={"payload": payload},
        )
    return HandlerOk(data=payload, summary="Update check completed")


def register(reg) -> None:
    reg.add_group(GroupSpec(group_id="update", description="the one update flow: channel truth and checks"))
    reg.add(
        CommandSpec(
            command_id="update.channel",
            group="update",
            description="Show the effective update channel and its manifest truth",
            aliases=("update",),
            input_schema=ChannelInput,
            effects="read_only",
            capabilities=frozenset({"self_update.check"}),
            handler=Handler("core.command_registry.groups.update_group:_handle_channel"),
            availability=Availability("core.command_registry.groups.update_group:_probe_release_config"),
            exit_codes=(0, 2, 10),
        )
    )
    reg.add(
        CommandSpec(
            command_id="update.check",
            group="update",
            description="Trigger an update check against the configured feed",
            effects="idempotent_write",
            capabilities=frozenset({"self_update.check"}),
            permission=ApprovalGate(
                kind="self_update.check",
                verifier="core.command_registry.groups.update_group:_gate_check",
            ),
            handler=Handler("core.command_registry.groups.update_group:_handle_check"),
            availability=Availability("core.command_registry.groups.update_group:_probe_release_config"),
            fault_bindings=(FaultBinding(when="feed_unreachable", fault_code="fault_provider", remediation=("vool update channel",)),),
            exit_codes=(0, 10, 20, 41),
            lifecycle="preview",
        )
    )
