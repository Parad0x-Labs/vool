"""Build a complete picture of the model-facing tool surface, for before/after comparison.

The tool metadata is about to be consolidated onto one record. The risk in that migration is
not a red test — it is a permission that quietly widens, or an intent that stops being
advertised, in a diff that otherwise looks like a refactor.

So this captures the three things a model and the sandbox actually see:

* which intents are advertised, and with what arguments;
* the native function schema each one becomes on a cloud provider;
* the permission actions each one resolves to, **at several argument shapes**, and the
  resulting effect in every operating mode.

The argument shapes matter and are the reason this is not a one-liner. ``actions_for_tool``
is argument-dependent in at least two places: ``sandbox.run_command`` reads ``command`` to
decide safe versus side-effecting, and ``workspace.write_file`` resolves to
OVERWRITE_EXISTING_FILES or CREATE_FILES depending on whether the target already exists. A
snapshot taken at one argument shape would call both of those stable and miss the change.

Import and call :func:`build_snapshot`; it has no side effects and touches only a temp dir.
"""
from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from core.cloud_tool_call_contract import build_cloud_tool_definitions
from core.execution.capabilities import runtime_tool_specs
from core.mode_permission_policy import (
    MODE_PERMISSION_MATRIX,
    OperatingMode,
    actions_for_tool,
)

# Probes chosen to hit every argument-dependent branch in actions_for_tool. Named so a diff
# says which situation changed rather than which dict index did.
ARGUMENT_SHAPES = (
    "no_arguments",
    "existing_path",
    "missing_path",
    "safe_command",
    "side_effecting_command",
)


def _shape_arguments(shape: str, workspace_root: str) -> dict[str, Any]:
    # `workspace_root` is intentionally NOT an argument here: `actions_for_tool` must resolve a
    # write target's existence against the trusted `source_context`, never a model-controlled
    # argument (see `build_snapshot` below, which passes it via `source_context` instead) --
    # a call shape that put it in `arguments` was exercising exactly the bug this rule exists to
    # catch, not a realistic model call.
    if shape == "existing_path":
        return {"path": "present.txt"}
    if shape == "missing_path":
        return {"path": "absent.txt"}
    if shape == "safe_command":
        return {"command": "ls -la"}
    if shape == "side_effecting_command":
        return {"command": "rm -rf ./build"}
    return {}


def build_snapshot(*, web: bool = True, browser: bool = True) -> dict[str, Any]:
    """Capture the tool surface at an explicitly declared policy, as JSON-comparable data.

    The policy is passed in rather than inherited, and that is not tidiness. Measured on
    2026-07-28, `runtime_tool_specs()` returns a different set depending on where it is called
    from: 58 specs in a plain process, 53 under `tests/conftest.py`, 57 from a module that
    imported `core.execution.capabilities` early. `runtime_tool_specs` takes its policy
    callables as *default arguments*, which Python binds once at import time, so patching
    `policy_engine.allow_web_fallback` afterwards does not reliably change what the model is
    offered — the surface depends on import order.

    A golden built on whichever configuration happened to be in effect would freeze an
    accident. Declaring it means a diff says "the tool surface changed", not "the harness ran
    in a different order this time".
    """

    specs = list(
        runtime_tool_specs(
            allow_web_fallback_fn=lambda: bool(web),
            allow_browser_fallback_fn=lambda: bool(browser),
        )
    )
    definitions = build_cloud_tool_definitions(specs)
    native_by_intent = {item.intent: item for item in definitions}

    with tempfile.TemporaryDirectory(prefix="vool-tool-surface-") as tmp:
        root = Path(tmp)
        (root / "present.txt").write_text("x", encoding="utf-8")
        workspace_root = str(root)

        tools: dict[str, Any] = {}
        for spec in specs:
            intent = str(spec.get("intent") or "")
            if not intent:
                continue
            native = native_by_intent.get(intent)
            permissions: dict[str, Any] = {}
            for shape in ARGUMENT_SHAPES:
                actions = actions_for_tool(
                    intent,
                    _shape_arguments(shape, workspace_root),
                    {"workspace_root": workspace_root},
                )
                names = sorted(action.value for action in actions)
                permissions[shape] = {
                    "actions": names,
                    # The effect is what the sandbox enforces; the action list alone can look
                    # unchanged while the mode outcome moves.
                    "effects": {
                        mode.value: _effect_for(mode, actions) for mode in OperatingMode
                    },
                }
            tools[intent] = {
                "description": str(spec.get("description") or ""),
                "read_only": bool(spec.get("read_only")),
                "arguments": sorted(str(key) for key in (spec.get("arguments") or {})),
                "native_name": native.name if native else None,
                "native_parameters": native.parameters if native else None,
                "permissions": permissions,
            }

    return {
        "intent_count": len(tools),
        "native_definition_count": len(definitions),
        "intents": sorted(tools),
        "tools": tools,
    }


def _effect_for(mode: OperatingMode, actions: tuple[Any, ...]) -> str:
    """The strictest effect across a tool's actions, which is what the sandbox applies."""

    row = MODE_PERMISSION_MATRIX[mode]
    if not actions:
        return "allow"
    effects = {row[action].value for action in actions if action in row}
    for worst in ("deny", "require_approval", "allow"):
        if worst in effects:
            return worst
    return "unknown"


__all__ = ["ARGUMENT_SHAPES", "build_snapshot"]
