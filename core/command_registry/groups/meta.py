"""The meta group — the registry describing itself."""
from __future__ import annotations

from dataclasses import dataclass

from core.command_registry.registry import CommandRegistry
from core.command_registry.spec import (
    CommandSpec,
    GroupSpec,
    Handler,
    HandlerOk,
    NextAction,
)


@dataclass(frozen=True)
class ListInput:
    group: str = ""


def _handle_commands_list(inp, ctx):
    from core.command_registry.projections import commands_json

    reg = _current_registry()
    payload = commands_json(reg)
    if inp and inp.group:
        payload["commands"] = [c for c in payload["commands"] if c["group"] == inp.group]
    return HandlerOk(data=payload, summary=f"{len(payload['commands'])} commands in {len(payload['groups'])} groups")


def _handle_commands_check(inp, ctx):
    from core.command_registry.projections import check_output

    reg = _current_registry()
    report, lines = check_output(reg)
    return HandlerOk(
        data={"ok": report.ok, "findings": [f"{f.kind}: {f.command_id}: {f.detail}" for f in report.findings]},
        summary="\n".join(lines),
    )


def _current_registry() -> CommandRegistry:
    from core.command_registry.registry import registry as _reg

    return _reg()


def register(reg: CommandRegistry) -> None:
    reg.add_group(GroupSpec(group_id="commands", description="registry self-description"))
    reg.add(
        CommandSpec(
            command_id="commands.list",
            group="commands",
            description="List every registered command with its typed contract",
            aliases=("commands",),
            input_schema=ListInput,
            effects="read_only",
            handler=Handler("core.command_registry.groups.meta:_handle_commands_list"),
            exit_codes=(0, 2),
            next_actions=(NextAction(command_id="commands.check", label="Lint the registry"),),
        )
    )
    reg.add(
        CommandSpec(
            command_id="commands.check",
            group="commands",
            description="Fail on duplicated, orphaned, unbound or contract-divergent commands",
            effects="read_only",
            handler=Handler("core.command_registry.groups.meta:_handle_commands_check"),
            exit_codes=(0, 2),
        )
    )
