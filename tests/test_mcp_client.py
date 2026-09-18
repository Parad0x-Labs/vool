"""End-to-end: VOOL as a local MCP host talking to a real stdio MCP server subprocess."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from core.mcp_client import MCPError, MCPStdioClient

_STUB = str(Path(__file__).parent / "mcp_stub_server.py")


def _client() -> MCPStdioClient:
    return MCPStdioClient(sys.executable, [_STUB], name="stub", timeout=20.0)


def test_initialize_handshake_and_list_tools() -> None:
    with _client() as c:
        assert c.server_info.get("name") == "stub"
        tools = c.list_tools()
        assert {t.name for t in tools} == {"echo", "add"}
        echo = next(t for t in tools if t.name == "echo")
        assert echo.description and echo.input_schema.get("type") == "object"


def test_call_tools_over_stdio() -> None:
    with _client() as c:
        c.list_tools()
        assert c.call_tool_text("echo", {"text": "hi vool"}) == "hi vool"
        assert c.call_tool_text("add", {"a": 2, "b": 40}) == "42.0"


def test_unknown_tool_raises_mcp_error() -> None:
    with _client() as c:
        with pytest.raises(MCPError):
            c.call_tool("does_not_exist", {})


def test_context_manager_starts_and_cleans_up() -> None:
    c = _client()
    c.start()
    assert c._proc is not None and c._proc.poll() is None
    c.close()
    assert c._proc is None


def test_timeout_on_a_nonresponsive_command() -> None:
    # A command that reads nothing back (a sleeper) must time out, not hang forever.
    slow = MCPStdioClient(sys.executable, ["-c", "import time; time.sleep(30)"], timeout=2.0)
    with pytest.raises(MCPError):
        slow.start()
    slow.close()
