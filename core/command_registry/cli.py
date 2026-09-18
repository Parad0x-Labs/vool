"""The ``vool`` CLI — a pure projection of the command registry.

    vool                          → grouped help (generated from the registry)
    vool commands --json          → machine truth
    vool commands --check         → registry lint (exit 1 on findings)
    vool <command_id> [--json] [--key value ...]   → execute, envelope out

No action names live here; everything renders from registry reads.
"""
from __future__ import annotations

import json
import sys
from typing import Any


def _parse_value(raw: str) -> Any:
    lowered = raw.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        return raw


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    from core.command_registry.execute import ExecutionContext, execute_command
    from core.command_registry.projections import check_output, cli_help, commands_json
    from core.command_registry.registry import registry as get_registry

    reg = get_registry()
    as_json = "--json" in args
    args = [a for a in args if a != "--json"]
    if "--help" in args or "-h" in args:
        args = [a for a in args if a not in ("--help", "-h")]
        if not args:
            sys.stdout.write(cli_help(reg))
            return 0

    if not args:
        sys.stdout.write(cli_help(reg))
        return 0

    if args[0] in {"commands", "help"}:
        rest = args[1:]
        if "--check" in rest:
            report, lines = check_output(reg)
            for line in lines:
                sys.stdout.write(line + "\n")
            return 0 if report.ok else 1
        payload = commands_json(reg, live_availability=True)
        if as_json:
            sys.stdout.write(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
            return 0
        sys.stdout.write(cli_help(reg))
        return 0

    # vool <surface> [--key value ...]
    input_data: dict[str, Any] = {}
    positional: list[str] = []
    i = 0
    rest = args
    while i < len(rest):
        arg = rest[i]
        if arg.startswith("--") and i + 1 < len(rest):
            input_data[arg.lstrip("-").replace("-", "_")] = _parse_value(rest[i + 1])
            i += 2
        else:
            positional.append(arg)
            i += 1

    # resolve the longest registry surface from positionals: `vool blackbox rollback`
    # means blackbox.rollback, not alias-blackbox + stray argument
    surface = ""
    remaining: list[str] = []
    for idx in range(len(positional), 0, -1):
        candidate = ".".join(positional[:idx])
        if reg.lookup(candidate) is not None:
            surface = candidate
            remaining = positional[idx:]
            break
    if not surface and positional:
        surface = positional[0]
        remaining = positional[1:]

    # bare positional values feed required input fields in declaration order
    if remaining:
        spec = reg.lookup(surface)
        if spec is not None and spec.input_schema is not None:
            import dataclasses as _dc

            fields = [
                f.name
                for f in _dc.fields(spec.input_schema)
                if f.name not in input_data
            ]
            for name, value in zip(fields, remaining, strict=False):
                input_data[name] = _parse_value(value)

    envelope = execute_command(surface, input_data or None, context=ExecutionContext(projection="cli"))
    if as_json:
        sys.stdout.write(json.dumps(envelope.to_json(), indent=2, sort_keys=True, default=str) + "\n")
    else:
        sys.stdout.write(envelope.summary + "\n")
        for crumb in envelope.breadcrumbs:
            sys.stdout.write(f"Next: {crumb.invocation} — {crumb.label}\n")
        if envelope.fault is not None:
            detail = envelope.fault.detail or {}
            if detail.get("reason"):
                sys.stdout.write(f"Reason: {detail['reason']}\n")
            if envelope.data:
                sys.stdout.write(json.dumps(envelope.data, sort_keys=True, default=str) + "\n")
    return envelope.execution.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
