"""Local MCP host — speak the Model Context Protocol to any local MCP server over stdio.

MCP won the AI tool-integration standard war (97M+ SDK downloads/mo; universal first-party
support), and its **stdio transport is inherently local**: the client launches the server as
a subprocess and exchanges newline-delimited JSON-RPC 2.0 messages over stdin/stdout — no
network, nothing leaves the box. This module makes VOOL a first-class local MCP host: spawn a
server, do the initialize handshake, list its tools, and call them, with real timeouts.

Deliberately dependency-free (stdlib only) so it runs anywhere VOOL runs, including offline.

The server child is a third-party process the operator configured but did not write, so it is
started exactly like a plugin handler: an allowlisted environment (never the ambient one) and the
host's kernel confinement around the process — writes confined to its cwd or a scratch directory,
credential directories and VOOL's key home read-denied, network denied unless the config allows
it. A host that cannot confine refuses to start the server (`MCPError`) unless the config opted
into ``confinement="heuristic_only"``; `confinement` on the client records which happened.
"""
from __future__ import annotations

import contextlib
import json
import queue
import re
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

PROTOCOL_VERSION = "2025-06-18"
_CLIENT_INFO = {"name": "vool", "version": "1.0"}


class MCPError(RuntimeError):
    """MCP transport or protocol error."""


@dataclass
class MCPTool:
    name: str
    description: str = ""
    input_schema: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MCPTool:
        return cls(
            name=str(d.get("name") or ""),
            description=str(d.get("description") or ""),
            input_schema=dict(d.get("inputSchema") or d.get("input_schema") or {}),
        )


@dataclass
class MCPResource:
    """One server resource (MCP resources/list entry). Only uri/name/mimeType are standard."""

    uri: str
    name: str = ""
    description: str = ""
    mime_type: str = ""

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MCPResource:
        return cls(
            uri=str(d.get("uri") or ""),
            name=str(d.get("name") or ""),
            description=str(d.get("description") or ""),
            mime_type=str(d.get("mimeType") or d.get("mime_type") or ""),
        )


class MCPStdioClient:
    """A minimal, robust MCP client over a stdio subprocess transport."""

    def __init__(
        self,
        command: str,
        args: list[str] | None = None,
        *,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
        timeout: float = 30.0,
        name: str = "",
        env_allowlist: tuple[str, ...] | list[str] | None = None,
        allow_network: bool = False,
        confinement: str = "auto",
    ) -> None:
        self.command = command
        self.args = list(args or [])
        self.env = env
        #: Names of THIS process's environment variables the server may inherit. Empty by default:
        #: a server sees only what its own config sets, never the ambient environment.
        self.env_allowlist = tuple(env_allowlist or ())
        self.cwd = cwd
        self.timeout = float(timeout)
        self.name = name or command
        self.allow_network = bool(allow_network)
        self.confinement_mode = str(confinement or "auto")
        #: How the running child is confined: ``kernel:<backend>`` or ``heuristic_only``.
        self.confinement = ""
        self._proc: subprocess.Popen[str] | None = None
        self._id = 0
        self._q: queue.Queue[dict[str, Any]] = queue.Queue()
        self._reader: threading.Thread | None = None
        self.server_info: dict[str, Any] = {}
        self.tools: list[MCPTool] = []

    # --- transport ---
    def _reader_loop(self) -> None:
        proc = self._proc
        assert proc is not None and proc.stdout is not None
        for line in proc.stdout:  # blocking iteration in a daemon thread
            line = line.strip()
            if not line:
                continue
            try:
                self._q.put(json.loads(line))
            except json.JSONDecodeError:
                continue  # tolerate stray non-JSON on stdout

    def _send(self, msg: dict[str, Any]) -> None:
        if self._proc is None or self._proc.stdin is None:
            raise MCPError("client not started")
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _read_response(self, expect_id: int) -> dict[str, Any]:
        deadline = time.monotonic() + self.timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise MCPError(f"timeout waiting for response id={expect_id} from {self.name}")
            try:
                msg = self._q.get(timeout=min(remaining, 1.0))
            except queue.Empty:
                if self._proc is not None and self._proc.poll() is not None:
                    raise MCPError(f"{self.name} exited (code {self._proc.returncode}) before responding")
                continue
            if msg.get("id") == expect_id:
                if "error" in msg:
                    raise MCPError(f"{self.name} error: {msg['error']}")
                return dict(msg.get("result") or {})
            # notification or unrelated id -> ignore

    def _request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self._id += 1
        rid = self._id
        self._send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return self._read_response(rid)

    def _notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._send({"jsonrpc": "2.0", "method": method, "params": params or {}})

    # --- lifecycle ---
    def start(self) -> MCPStdioClient:
        # ENVIRONMENT IS AN ALLOWLIST, NOT A FILTER. This built `{**os.environ, **self.env}` —
        # the whole environment, including every exported API key and token — and handed it to a
        # subprocess the operator configured but did not write. `core/plugin_executor.py` already
        # solved this for plugins and its docstring names THIS line as the defect it fixes; an MCP
        # server is the same trust class, so it gets the same treatment. A server still receives
        # every variable its own config names, via `self.env`, plus the minimum needed to start a
        # process — it simply no longer inherits secrets nobody offered it.
        from core.plugin_executor import ConfinementUnavailableError, build_child_env, confined_argv

        # The child's working directory is the one place it may write. A configured cwd is the
        # server's own; otherwise a per-server scratch directory outside VOOL's key home (which
        # the kernel profile read-denies to every confined child).
        if self.cwd:
            work_dir = Path(self.cwd)
        else:
            # `name` defaults to the command, which may be an absolute path; a raw join would
            # REPLACE the scratch root with it. Slug it.
            slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(self.name or "server")).strip("_") or "server"
            work_dir = Path(tempfile.gettempdir()) / "vool-mcp-scratch" / slug[-80:]
        with contextlib.suppress(OSError):
            work_dir.mkdir(parents=True, exist_ok=True)
        try:
            argv, self.confinement = confined_argv(
                [self.command, *self.args],
                writable_roots=(work_dir,),
                allow_network=self.allow_network,
                mode=self.confinement_mode,
            )
        except ConfinementUnavailableError as exc:
            raise MCPError(f"{self.name}: refusing to start unconfined ({exc})") from exc
        full_env = build_child_env(
            env_allowlist=tuple(self.env_allowlist or ()),
            secrets=dict(self.env or {}),
            scratch_dir=str(work_dir),
        )
        self._proc = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            bufsize=1,
            cwd=str(work_dir),
            env=full_env,
        )
        self._reader = threading.Thread(target=self._reader_loop, daemon=True)
        self._reader.start()
        result = self._request(
            "initialize",
            {"protocolVersion": PROTOCOL_VERSION, "capabilities": {}, "clientInfo": _CLIENT_INFO},
        )
        self.server_info = dict(result.get("serverInfo") or {})
        self._notify("notifications/initialized")
        return self

    def list_tools(self) -> list[MCPTool]:
        result = self._request("tools/list")
        self.tools = [MCPTool.from_dict(t) for t in (result.get("tools") or [])]
        return self.tools

    def list_resources(self) -> list[MCPResource]:
        """List the server's resources (MCP resources/list). A server without resource support
        raises a protocol error its CALLER treats as "ships no resources", not a failure."""
        result = self._request("resources/list")
        return [MCPResource.from_dict(r) for r in (result.get("resources") or [])]

    def read_resource(self, uri: str) -> dict[str, Any]:
        """Read one resource (MCP resources/read). Returns the raw result with `contents`."""
        return self._request("resources/read", {"uri": uri})

    def read_resource_text(self, uri: str) -> str:
        """Convenience: read a resource and join its text content blocks."""
        result = self.read_resource(uri)
        parts = [
            str(c.get("text") or "")
            for c in (result.get("contents") or [])
            if isinstance(c, dict) and c.get("text")
        ]
        return "\n".join(p for p in parts if p)

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        """Call a tool. Returns the raw MCP result ({content: [...], isError: bool})."""
        return self._request("tools/call", {"name": name, "arguments": arguments or {}})

    def call_tool_text(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Convenience: call a tool and join its text content blocks."""
        result = self.call_tool(name, arguments)
        parts = [str(b.get("text") or "") for b in (result.get("content") or []) if b.get("type") == "text"]
        return "\n".join(p for p in parts if p)

    def close(self) -> None:
        proc, self._proc = self._proc, None
        if proc is None:
            return
        with contextlib.suppress(Exception):
            if proc.stdin:
                proc.stdin.close()
        try:
            proc.terminate()
            proc.wait(timeout=5)
        except Exception:
            with contextlib.suppress(Exception):
                proc.kill()

    def __enter__(self) -> MCPStdioClient:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()


__all__ = ["PROTOCOL_VERSION", "MCPError", "MCPStdioClient", "MCPTool"]
