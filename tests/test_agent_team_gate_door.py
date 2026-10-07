"""The agent-team write gate at VOOL's one runtime tool door (``execute_runtime_tool``).

Real coordinator, real agent processes holding real claims, the real tool door. A call made for
an agent's chat session that writes into another agent's claim is refused at the door and the
file never appears; a write inside its own claim reaches the ordinary door; a shell command for an
agent is refused; an ordinary chat is untouched.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from core.agent_team import gate
from core.agent_team.coordinator import TeamCoordinator
from core.runtime_execution_tools import execute_runtime_tool

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process signals")

AGENT = str(Path(__file__).with_name("agent_team_agent.py"))
LIMITS = {"max_usd": 1.0, "max_tokens": 10_000, "max_calls": 5, "wall_clock_seconds": 60}


def test_runtime_tool_door_asks_the_agent_gate(tmp_path):
    assert gate.INSTALLED
    ws = tmp_path / "ws"
    for d in ("a", "b"):
        (ws / d).mkdir(parents=True)
    team = TeamCoordinator(tmp_path / "team", workspace=ws, team_limits=LIMITS, tick_seconds=0.25)
    session = "openclaw:" + "c" * 20
    try:
        team.start([
            {"key": "docs", "objective": "Docs update", "importance": "low", "claims": ["a"],
             "command": [sys.executable, AGENT, "sleeper", "20"], "limits": LIMITS},
            {"key": "login", "objective": "Login redirect fix", "importance": "high", "claims": ["b"],
             "command": [sys.executable, AGENT, "sleeper", "20"], "limits": LIMITS},
        ])
        docs = next(r for r in team.registry.agents() if r["key"] == "docs")
        gate.bind_session(session, team, docs["agent_id"])
        ctx = {"session_id": session, "workspace": str(ws), "workspace_root": str(ws)}
        intruding = execute_runtime_tool("workspace.write_file", {"path": str(ws / "b" / "auth.py"), "content": "x"},
                                         source_context=ctx)
        assert intruding.status == gate.GATE_STATUS and not intruding.ok
        assert "Login redirect fix · high" in intruding.response_text
        assert not (ws / "b" / "auth.py").exists()
        own = execute_runtime_tool("workspace.write_file", {"path": str(ws / "a" / "guide.md"), "content": "x"},
                                   source_context=ctx)
        assert own is None or own.status != gate.GATE_STATUS
        shell = execute_runtime_tool("sandbox.run_command", {"command": "touch b/auth.py"}, source_context=ctx)
        assert shell.status == gate.GATE_STATUS and not (ws / "b" / "auth.py").exists()
        plain = execute_runtime_tool("workspace.write_file", {"path": str(ws / "b" / "auth.py"), "content": "x"},
                                     source_context={**ctx, "session_id": "openclaw:" + "f" * 20})
        assert plain is None or plain.status != gate.GATE_STATUS
    finally:
        gate.unbind_session(session)
        team.stop()
        team.close()
