"""Minimal MCP stdio server used as a test fixture for core.mcp_client.

Implements just enough of the protocol (initialize / tools/list / tools/call) with three tools
(echo, add, dump_env). Not a pytest module (no test_* funcs) -- it is spawned as a subprocess.

Options (argv, because the child's environment is an allowlist and cannot carry test knobs):

    --describe-echo TEXT   replace the `echo` tool's description with TEXT. The trust tests use
                           this to ship an instruction-shaped description and prove it reaches a
                           model only as framed, single-line, untrusted data.
    --marker PATH          write PATH on start. Lets a test prove the server was (or was not)
                           launched.
    --call-log PATH        append every tools/call name to PATH (one per line). Per-call launch
                           evidence for budget-refusal proofs: zero lines means the server was
                           never asked to run a tool. Off by default.
    --extra-tools          also serve `dump_env` and `write_outside` (confinement probes). Off by
                           default so the existing fixtures keep their exact two-tool surface.
    --skill-resource       also serve a `skill://mcp-pack-probe` resource whose text is a VALID
                           typed SKILL.md — the MCP skill-discovery fixture.
    --skill-resource-invalid
                           same, but the shipped skill deliberately violates the contract law
                           (unknown task family, no verification) — the refusal fixture.
"""
from __future__ import annotations

import json
import os
import sys

VALID_SKILL_MD = """---
name: mcp-pack-probe
id: mcp-pack-probe
version: 1.0.0
description: "Probe an installed pack's manifest from the MCP lane. Use for pack audits."
risk-class: read_only
task-families: [workspace_audit]
capability-families: [plugin]
tool-intents: [workspace.read_file]
permitted-tools: [workspace.read_file]
prerequisites: []
expected-outputs: [audit_report]
verification: [evidence_cited]
stopping-conditions: ["stop when the audit report names every manifest defect or none"]
incompatible-with: []
priority: 15
---

# MCP pack probe

Read the pack manifest through workspace.read_file and report defects with evidence.
"""

INVALID_SKILL_MD = """---
name: mcp-pack-probe
id: mcp-pack-probe
version: 1.0.0
description: "An invalid MCP-shipped skill: unknown task family, no verification."
risk-class: read_only
task-families: [not-a-family]
stopping-conditions: []
---

# Invalid probe

This contract must be REFUSED by the same law every source passes.
"""

SKILL_RESOURCES = {
    "skill://mcp-pack-probe": VALID_SKILL_MD,
}
INVALID_SKILL_RESOURCES = {
    "skill://mcp-pack-probe": INVALID_SKILL_MD,
}


def _tools(echo_description: str, *, extra: bool) -> list[dict]:
    tools = [
        {"name": "echo", "description": echo_description,
         "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
        {"name": "add", "description": "Add two numbers.",
         "inputSchema": {"type": "object", "properties": {"a": {"type": "number"}, "b": {"type": "number"}}}},
    ]
    if extra:
        tools.extend([
            {"name": "dump_env", "description": "Return the names of the environment variables the server sees.",
             "inputSchema": {"type": "object", "properties": {}}},
            {"name": "write_outside", "description": "Try to write a file at the given path.",
             "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}},
        ])
    return tools


def _send(msg: dict) -> None:
    sys.stdout.write(json.dumps(msg) + "\n")
    sys.stdout.flush()


def _parse_args(argv: list[str]) -> tuple[str, str, bool, dict[str, str], str]:
    echo_description = "Echo the given text back."
    marker = ""
    call_log = ""
    extra = False
    resources: dict[str, str] = {}
    items = list(argv)
    while items:
        head = items.pop(0)
        if head == "--describe-echo" and items:
            echo_description = items.pop(0)
        elif head == "--marker" and items:
            marker = items.pop(0)
        elif head == "--call-log" and items:
            call_log = items.pop(0)
        elif head == "--extra-tools":
            extra = True
        elif head == "--skill-resource":
            resources.update(SKILL_RESOURCES)
        elif head == "--skill-resource-invalid":
            resources.update(INVALID_SKILL_RESOURCES)
    return echo_description, marker, extra, resources, call_log


def _log_call(call_log: str, name: str) -> None:
    if not call_log:
        return
    try:
        with open(call_log, "a", encoding="utf-8") as handle:
            handle.write(str(name) + "\n")
    except OSError:
        pass


def main() -> None:
    echo_description, marker, extra, resources, call_log = _parse_args(sys.argv[1:])
    if marker:
        try:
            with open(marker, "w", encoding="utf-8") as handle:
                handle.write("started\n")
        except OSError:
            pass
    tools = _tools(echo_description, extra=extra)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        msg = json.loads(line)
        rid = msg.get("id")
        method = msg.get("method")
        if method == "initialize":
            _send({"jsonrpc": "2.0", "id": rid, "result": {
                "protocolVersion": "2025-06-18",
                "capabilities": {"tools": {}, "resources": {}},
                "serverInfo": {"name": "stub", "version": "0.1"},
            }})
        elif method == "notifications/initialized":
            continue  # notification: no response
        elif method == "tools/list":
            _send({"jsonrpc": "2.0", "id": rid, "result": {"tools": tools}})
        elif method == "resources/list":
            _send({"jsonrpc": "2.0", "id": rid, "result": {"resources": [
                {"uri": uri, "name": uri.split("://", 1)[-1], "mimeType": "text/markdown"}
                for uri in sorted(resources)
            ]}})
        elif method == "resources/read":
            uri = str((msg.get("params") or {}).get("uri") or "")
            text = resources.get(uri)
            if text is None:
                _send({"jsonrpc": "2.0", "id": rid,
                       "error": {"code": -32602, "message": f"unknown resource {uri}"}})
            else:
                _send({"jsonrpc": "2.0", "id": rid, "result": {"contents": [
                    {"uri": uri, "mimeType": "text/markdown", "text": text}
                ]}})
        elif method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name")
            _log_call(call_log, name)
            args = params.get("arguments") or {}
            if name == "echo":
                _send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": str(args.get("text", ""))}], "isError": False}})
            elif name == "add":
                total = float(args.get("a", 0)) + float(args.get("b", 0))
                _send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": str(total)}], "isError": False}})
            elif name == "dump_env":
                _send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": json.dumps(sorted(os.environ))}], "isError": False}})
            elif name == "write_outside":
                target = str(args.get("path") or "")
                try:
                    with open(target, "w", encoding="utf-8") as handle:
                        handle.write("escaped\n")
                    text, is_error = f"wrote {target}", False
                except OSError as exc:
                    text, is_error = f"denied: {exc}", True
                _send({"jsonrpc": "2.0", "id": rid, "result": {
                    "content": [{"type": "text", "text": text}], "isError": is_error}})
            else:
                _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown tool {name}"}})
        elif rid is not None:
            _send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}})


if __name__ == "__main__":
    main()
