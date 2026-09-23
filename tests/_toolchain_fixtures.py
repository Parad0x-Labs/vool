"""Shared fixtures for the toolchain-convergence packs (built-in, skill, plugin, MCP).

Not a test module. Everything here builds REAL artefacts on disk — a plugin with a subprocess
handler that actually runs, a skill with a body, an MCP stdio server — so the packs drive the real
registry, graph, permission gate, executor and record store rather than mocks of them.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

_STUB_MCP = str(Path(__file__).parent / "mcp_stub_server.py")

PLUGIN_ID = "pack"
SKILL_MARKER = "GUIDANCE_MARKER_WIDGET_REPORT"

# The handler's interpreter is pinned to THIS interpreter: the child environment is an allowlist,
# so `#!/usr/bin/env python3` would resolve against a PATH the test does not control.
_HANDLER = f"""#!{sys.executable}
import json, os, sys
payload = json.loads(sys.stdin.read())
intent = payload["intent"]
args = payload.get("arguments") or {{}}
root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
try:
    with open(os.path.join(root, "calls.log"), "a", encoding="utf-8") as fh:
        fh.write(intent + "\\n")
except OSError:
    pass
if intent.endswith(".echo"):
    text = str(args.get("text", ""))
    out = {{"ok": True, "text": "echo:" + text,
           "observation": {{"text": text, "env_keys": sorted(os.environ), "cwd": os.getcwd()}},
           "resolved_target": text}}
elif intent.endswith(".touch"):
    path = str(args.get("path") or "")
    try:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("touched\\n")
        out = {{"ok": True, "text": "wrote " + path, "observation": {{"path": path}}, "resolved_target": path}}
    except OSError as exc:
        out = {{"ok": False, "status": "write_denied", "error": str(exc), "observation": {{"path": path}}}}
elif intent.endswith(".crash"):
    sys.stderr.write("handler exploded\\n")
    sys.exit(3)
else:
    out = {{"ok": False, "status": "unknown_intent", "error": intent}}
sys.stdout.write(json.dumps(out))
"""


def _tool(intent: str, description: str, *, side_effect: str, approval: str, actions: list[str],
          schema_props: dict, required: list[str], claim: dict, handler_extra: dict | None = None) -> dict:
    tool = {
        "intent": intent,
        "description": description,
        "handler": {"kind": "subprocess", "entry": "bin/run", **dict(handler_extra or {})},
        "input_schema": {
            "type": "object",
            "additionalProperties": False,
            "required": required,
            "properties": schema_props,
        },
        "side_effect_class": side_effect,
        "approval_requirement": approval,
        "permission_actions": actions,
        "claim": claim,
    }
    if side_effect in {"workspace_write", "modify_files"}:
        # The registry's law: a local-mutating plugin tool declares its Blackbox coverage
        # or does not register. The probe writes exactly its claimed target path, so the
        # precise capability is declared_paths (preimage + postimage of the target only).
        tool["mutation"] = {
            "tool": intent,
            "scope": "workspace",
            "effect_class": "reversible",
            "snapshot_strategy": "declared_paths",
            "receipt_lifecycle": "intent_then_terminal",
            "rollback_support": "exact",
            "recorder": "blackbox.coverage_declared",
        }
    return tool


def default_tools(plugin_id: str = PLUGIN_ID) -> list[dict]:
    return [
        _tool(
            f"{plugin_id}.echo",
            "Echo text back through the pack plugin (read-only probe).",
            side_effect="read_only",
            approval="none",
            actions=["read_files"],
            schema_props={"text": {"type": "string"}},
            required=["text"],
            claim={"target_argument": "text", "resolved_target_key": "resolved_target"},
        ),
        _tool(
            f"{plugin_id}.touch",
            "Write a marker file at a path inside the plugin scratch directory.",
            side_effect="workspace_write",
            approval="runtime_policy",
            actions=["create_files"],
            schema_props={"path": {"type": "string"}},
            required=["path"],
            claim={"target_argument": "path", "resolved_target_key": "resolved_target", "asserts_action": True},
        ),
        _tool(
            f"{plugin_id}.crash",
            "A handler that exits non-zero, for failure-shape proofs.",
            side_effect="read_only",
            approval="none",
            actions=["read_files"],
            schema_props={},
            required=[],
            claim={},
        ),
    ]


def make_plugin(
    root: Path,
    *,
    plugin_id: str = PLUGIN_ID,
    tools: list[dict] | None = None,
    skills: dict[str, str] | None = None,
    manifest_extra: dict | None = None,
    admit: bool = True,
) -> Path:
    """Write a loadable plugin under ``root/plugins/<plugin_id>`` and return its directory.

    ``skills`` maps a skill directory name to the full SKILL.md text.
    """
    plugin_dir = root / "plugins" / plugin_id
    os.environ["VOOL_PLUGIN_SCRATCH_ROOT"] = str(root / "scratch")
    (plugin_dir / ".codex-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_dir / "bin").mkdir(parents=True, exist_ok=True)
    handler = plugin_dir / "bin" / "run"
    handler.write_text(_HANDLER, encoding="utf-8")
    handler.chmod(handler.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    manifest = {
        "name": plugin_id,
        "version": "1.0.0",
        "description": "Toolchain convergence probe pack.",
        "runtime": {"contract_version": 1},
        "tools": default_tools(plugin_id) if tools is None else tools,
        **dict(manifest_extra or {}),
    }
    (plugin_dir / ".codex-plugin" / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name, text in (skills or {}).items():
        skill_dir = plugin_dir / "skills" / name
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text(text, encoding="utf-8")
    if admit:
        admit_plugin(plugin_id, plugin_dir)
    return plugin_dir


def admit_plugin(plugin_id: str, plugin_dir: Path) -> None:
    """Take the pack through the REAL lifecycle so its tools are offered.

    Writing files under the plugins root is DISCOVERY. It is not installation, and installation
    is not availability. A fixture that skipped these steps would be testing a world the runtime
    no longer has -- so the fixture performs them instead of the gate being softened for it.
    """

    from core import plugin_lifecycle

    plugin_lifecycle.install(plugin_id, root=plugin_dir, source="test-fixture")
    plugin_lifecycle.verify(plugin_id, root=plugin_dir)
    plugin_lifecycle.enable(plugin_id)


def widget_skill(*, allowed_tools: str = "[pack.echo]", body_extra: str = "") -> str:
    return (
        "---\n"
        "name: widget-report\n"
        "description: Prepare a widget report. Use when the user asks for a widget report or widget summary.\n"
        "triggers: [widget, report]\n"
        f"allowed-tools: {allowed_tools}\n"
        "---\n"
        "# Widget report\n\n"
        f"When preparing a widget report, call pack.echo with the widget name first. {SKILL_MARKER}\n"
        f"{body_extra}\n"
    )


def calls_logged(plugin_dir: Path) -> list[str]:
    log = plugin_dir / "calls.log"
    if not log.is_file():
        return []
    return [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_mcp_config(
    tmp_path: Path,
    monkeypatch,
    *,
    name: str = "stub",
    trust: dict | None = None,
    extra_tools: bool = False,
    describe_echo: str = "",
    marker: str = "",
    allow_network: bool | None = None,
    confinement: str = "",
    enabled: bool = True,
    env: dict | None = None,
) -> Path:
    args = [_STUB_MCP]
    if extra_tools:
        args.append("--extra-tools")
    if describe_echo:
        args.extend(["--describe-echo", describe_echo])
    if marker:
        args.extend(["--marker", marker])
    entry: dict = {"name": name, "command": sys.executable, "args": args, "enabled": enabled}
    if trust is not None:
        entry["trust"] = trust
    if allow_network is not None:
        entry["allow_network"] = allow_network
    if confinement:
        entry["confinement"] = confinement
    if env:
        entry["env"] = env
    cfg = tmp_path / "mcp_servers.json"
    cfg.write_text(json.dumps({"servers": [entry]}), encoding="utf-8")
    monkeypatch.setenv("VOOL_MCP_CONFIG", str(cfg))
    return cfg


def read_only_pin(*tools: str) -> dict:
    return {tool: {"side_effect_class": "read_only", "permission_actions": ["read_files"]} for tool in tools}


def tracker():
    from core.hive_activity_tracker import HiveActivityTracker, HiveActivityTrackerConfig

    return HiveActivityTracker(config=HiveActivityTrackerConfig(enabled=False, watcher_api_url=None))


def reset_toolchain_state() -> None:
    """Restore the builtin-only world so no later test sees this pack's rows.

    Scope of "this pack's rows": the tool registry, capability graph, offer state, skill
    cache, plugin roots and the catalog's storage probe. The plugin-LIFECYCLE store is
    deliberately NOT touched — it is the durable record of real admissions, and callers
    here legitimately admit a pack first and reset the registries after (see
    tests/plugin_mcp_budgets/conftest.py::plugin_world). A world that must also start with
    an empty lifecycle catalog resets it through the lifecycle's own authority
    (`core.plugin_lifecycle.reset_for_tests`) at its fixture boundary, as
    tests/command_registry/test_projection_parity.py now does.
    """
    from core import capability_graph, tool_registry
    from core.execution import mcp_bridge

    mcp_bridge.reset_mcp_state()
    tool_registry.reset()
    capability_graph.reset()
    capability_graph.init_graph()
    capability_graph.bootstrap_from_registry()
    try:
        from core.tool_offer_state import reset_offer_state

        reset_offer_state()
    except Exception:
        pass
    try:
        from core import tool_offer_assembly

        tool_offer_assembly.reset_skill_cache()
    except Exception:
        pass
    try:
        from core import plugin_tools

        plugin_tools.reset_roots()
    except Exception:
        pass
    try:
        from core.plugin_catalog import reset_storage_state

        reset_storage_state()
    except Exception:
        pass


def internal_scope(label: str, *actions: str, intents: tuple[str, ...] = (), seconds: int = 120) -> dict:
    """A bounded, typed internal authority for a test caller, as the context key the gate reads."""
    from core.mode_permission_policy import PermissionAction, grant_internal_authority

    token = grant_internal_authority(
        label=label,
        actions=tuple(PermissionAction(a) for a in actions),
        duration_seconds=seconds,
        intents=tuple(intents),
    )
    return {"internal_authority_token": token}


def executor_kwargs(session_id: str = "s", **context) -> dict:
    return {
        "task_id": "t",
        "session_id": session_id,
        "source_context": dict(context) if context else None,
        "hive_activity_tracker": tracker(),
    }


__all__ = [
    "PLUGIN_ID",
    "SKILL_MARKER",
    "calls_logged",
    "default_tools",
    "executor_kwargs",
    "internal_scope",
    "make_plugin",
    "read_only_pin",
    "reset_toolchain_state",
    "tracker",
    "widget_skill",
    "write_mcp_config",
]
