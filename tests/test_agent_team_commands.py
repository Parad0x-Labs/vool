"""Agent teams through the command registry: the chat's door, executed for real.

The group registers into a fresh registry (its wiring into the default registry is one line in an
owned file, sent as a patch), passes the registry's own contract check, and runs end to end:
start a team for a chat, read its status, stop an agent by its task name. The model is offered
only ``agents.status``; starting, stopping and deciding are the operator's gestures.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from core.command_registry.check import check_registry
from core.command_registry.execute import ExecutionContext, execute_command
from core.command_registry.groups import agent_team_group
from core.command_registry.registry import CommandRegistry

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process signals")

AGENT = str(Path(__file__).with_name("agent_team_agent.py"))
LIMITS = {"max_usd": 1.0, "max_tokens": 10_000, "max_calls": 5, "wall_clock_seconds": 60}


@pytest.fixture
def reg(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    from core.agent_team import service

    registry = CommandRegistry()
    agent_team_group.register(registry)
    yield registry
    service._forget_for_tests()


def test_group_passes_the_registry_contract_and_offers_the_model_only_reads(reg):
    report = check_registry(reg)
    assert report.ok, report.findings
    offered = {spec.command_id for spec in reg.commands() if spec.model_offerable}
    assert offered == {"agents.status"}


def test_start_status_stop_through_the_registry(reg, tmp_path):
    ws = tmp_path / "ws"
    (ws / "docs").mkdir(parents=True)
    operator = ExecutionContext(projection="chat", principal="operator", approval_context={"gesture": "web-ui-press"})
    session = "openclaw:" + "a" * 20
    plan = [{"key": "docs", "objective": "API reference refresh", "importance": "low", "claims": ["docs"],
             "command": [sys.executable, AGENT, "sleeper", "30"], "limits": LIMITS}]
    refused = execute_command("agents.start", {"session_id": session, "agents": plan, "team_limits": LIMITS,
                                               "workspace": str(ws)}, reg=reg,
                              context=ExecutionContext(projection="chat", principal="model"))
    assert refused.execution.exit_code == 20  # approval required before anything starts
    started = execute_command("agents.start", {"session_id": session, "agents": plan, "team_limits": LIMITS,
                                               "workspace": str(ws)}, reg=reg, context=operator)
    assert started.execution.exit_code == 0, started
    assert "API reference refresh · low" in started.summary
    time.sleep(0.5)
    status = execute_command("agents.status", {"session_id": session}, reg=reg,
                             context=ExecutionContext(projection="chat", principal="model"))
    assert status.execution.exit_code == 0 and "API reference refresh · low: running" in status.summary
    model_stop = execute_command("agents.stop", {"session_id": session, "agent": "API reference refresh"}, reg=reg,
                                 context=ExecutionContext(projection="chat", principal="model"))
    assert model_stop.execution.exit_code == 20
    stopped = execute_command("agents.stop", {"session_id": session, "agent": "API reference refresh"}, reg=reg,
                              context=operator)
    assert stopped.execution.exit_code == 0 and "API reference refresh · low" in stopped.summary
    unbounded = execute_command("agents.start", {"session_id": "openclaw:" + "b" * 20, "agents": plan,
                                                 "team_limits": {"max_usd": 1}, "workspace": str(ws)},
                                reg=reg, context=operator)
    assert unbounded.execution.exit_code != 0 and "unbounded" in unbounded.summary


def test_an_overlap_alert_is_pushed_to_the_chats_notifications(reg, tmp_path):
    from core.agent_team import service
    from core.operator import notification_center

    ws = tmp_path / "ws"
    for d in ("a", "b"):
        (ws / d).mkdir(parents=True)
    (ws / "b" / "session.py").write_text("SESSION = 1\n")
    session = "openclaw:" + "9" * 20
    limits = {**LIMITS, "wall_clock_seconds": 60}
    plan = [
        {"key": "login", "objective": "Login redirect fix", "importance": "high", "claims": ["b"],
         "command": [sys.executable, AGENT, "sleeper", "30"], "limits": limits},
        {"key": "store", "objective": "Session store refactor", "claims": ["a"],
         "command": [sys.executable, AGENT, "grandchild_writer", str(ws / "b" / "session.py"), "6"], "limits": limits},
    ]
    assert service.start(session, plan, team_limits=limits, workspace=str(ws), tick_seconds=0.25)["ok"]
    try:
        deadline = time.monotonic() + 40
        items: list = []
        while time.monotonic() < deadline and not items:
            items = [i for i in notification_center.list_items()["items"] if i.get("session_id") == session]
            time.sleep(0.25)
        assert items, service.status(session)
        item = items[0]
        assert item["source_kind"] == "background_run" and item["title"] == "Two agents touched the same files"
        assert "Login redirect fix · high" in item["body"] and "stays paused" in item["body"]
        alert_id = service.status(session)["alerts"][0]["alert_id"]
        assert service.decide(session, alert_id, "stop")["ok"]
    finally:
        service.stop(session)
