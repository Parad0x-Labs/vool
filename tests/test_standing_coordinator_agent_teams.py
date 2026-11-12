"""The standing coordinator over real agent teams, started through the chat's own door.

Teams are started with ``core.agent_team.service.start`` exactly as the ``agents.start`` command
starts them, with real agent processes. What reaches the user is read back through the served
``/api/notifications`` route, and answers go through the coordinator's own ``decide``.
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from tests.pa_beta_gate._pc_calendar_rig import api_get, prepare_home

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX agent processes")

AGENT = str(Path(__file__).with_name("agent_team_agent.py"))
LIMITS = {"max_usd": 1.0, "max_tokens": 100_000, "max_calls": 20, "wall_clock_seconds": 120}
SESSION = "openclaw:" + "e" * 20


def _agent(key, title, importance, claims, *argv):
    return {"key": key, "objective": title, "title": title, "importance": importance, "claims": claims,
            "command": [sys.executable, AGENT, *argv], "limits": dict(LIMITS)}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_MODEL_RADAR_POLLER", "0")
    monkeypatch.delenv("VOOL_STANDING_COORDINATOR", raising=False)
    prepare_home(tmp_path, monkeypatch)
    from core.agent_team import service
    from core.standing_coordinator import api as coordinator_api
    from core.standing_coordinator.ports import reset_ports_for_tests

    reset_ports_for_tests()
    coordinator_api.set_default_coordinator_for_tests(None)
    ws = tmp_path / "ws"
    for name in ("a", "b", "c"):
        (ws / name).mkdir(parents=True)
    yield ws
    for session in (SESSION, SESSION + "x"):
        try:
            service.stop(session)
        except Exception:
            pass
    service._forget_for_tests()
    coordinator_api.set_default_coordinator_for_tests(None)
    reset_ports_for_tests()


def _start(ws, plan, session=SESSION):
    from core.agent_team import service

    result = service.start(session, plan, team_limits=LIMITS, workspace=str(ws), tick_seconds=0.25)
    assert result["ok"], result
    return result


def _states(session=SESSION):
    from core.agent_team import service

    return {row["agent"]: row["state"] for row in service.status(session)["agents"]}


def _wait_for(predicate, timeout=40.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(0.2)
    raise AssertionError(f"timed out; last agent states: {_states()}")


def _inbox():
    status, body = api_get("/api/notifications")
    assert status == 200 and body["ok"], body
    return [item for item in body["items"] if item.get("session_id") == SESSION]


def _watched():
    from core.standing_coordinator import api as coordinator_api

    return coordinator_api.watches()[1]["watches"]


def test_a_chat_team_is_watched_from_its_start_asked_once_answered_and_reported(home):
    from core.standing_coordinator import api as coordinator_api

    _start(home, [
        _agent("db", "Database upgrade", "high", ["a"], "ask", "Drop the legacy table?"),
        _agent("docs", "API reference refresh", "low", ["b"], "own_writer", str(home / "b"), "2", "0.05"),
    ])
    [watch] = _watched()
    team_id = watch["team_id"]
    assert team_id.startswith("team:") and team_id.count(":") == 2 and watch["session_id"] == SESSION
    _wait_for(lambda: _states() == {"Database upgrade · high": "needs_decision", "API reference refresh · low": "done"})

    coordinator = coordinator_api.default_coordinator()
    coordinator.tick()
    coordinator.tick()
    asks = [i for i in _inbox() if i["title"] == "Agents need your decision"]
    assert len(asks) == 1, asks
    assert "Database upgrade · high asks: Drop the legacy table? Options: yes, no." in asks[0]["body"]
    assert asks[0]["payload"]["section"] == "needs" and asks[0]["payload"]["team_id"] == team_id

    code, status = coordinator_api.status(team_id)
    assert code == 200 and status["watching"] is True
    [waiting] = [a for a in status["agents"] if a["state"] == "waiting"]
    assert waiting["waiting_on"]["always_ask"] is True  # an agent's own question is never answered from memory
    request_id = waiting["waiting_on"]["request_id"]
    assert request_id.endswith(":ask:1")

    code, decided = coordinator_api.decide({"team_id": team_id, "request_id": request_id, "answer": "yes, drop it"})
    assert code == 200 and [d["status"] for d in decided["delivered"]] == ["done"], decided
    _wait_for(lambda: _states()["Database upgrade · high"] == "done")

    coordinator.tick()
    [final] = [i for i in _inbox() if i["title"] == "Team finished"]
    assert "Agent team for Database upgrade and API reference refresh: 2 agents (2 done)" in final["body"]
    assert "- Database upgrade · high: done. Its report: went ahead with: yes, drop it" in final["body"]
    assert "- API reference refresh · low: done. Its report: wrote 2 files" in final["body"]
    assert _watched() == []
    assert len([i for i in _inbox() if i["title"] == "Agents need your decision"]) == 1  # never asked again

    # The same chat starts more agents later: a new watch for the new batch only.
    _start(home, [_agent("cache", "Cache warmup", "normal", ["c"], "sleeper", "0.2")])
    [again] = _watched()
    assert again["team_id"].rsplit(":", 1)[0] == team_id.rsplit(":", 1)[0]
    assert int(again["team_id"].rsplit(":", 1)[1]) > int(team_id.rsplit(":", 1)[1])
    code, batch = coordinator_api.status(again["team_id"])
    assert [a["label"] for a in batch["agents"]] == ["Cache warmup · normal"]


def test_agents_started_while_a_watch_is_open_join_it_instead_of_a_second_watch(home):
    _start(home, [_agent("s1", "Index rebuild", "normal", ["a"], "sleeper", "5")])
    [first] = _watched()
    _start(home, [_agent("s2", "Thumbnail cleanup", "low", ["b"], "sleeper", "5")])
    assert [w["team_id"] for w in _watched()] == [first["team_id"]]
    from core.standing_coordinator import api as coordinator_api

    labels = [a["label"] for a in coordinator_api.status(first["team_id"])[1]["agents"]]
    assert labels == ["Index rebuild · normal", "Thumbnail cleanup · low"]


def test_stopping_a_waiting_agent_through_the_port_withdraws_its_question(home):
    from core.agent_team import service
    from core.standing_coordinator.ports import resolve_port

    _start(home, [_agent("db", "Database upgrade", "high", ["a"], "ask", "Drop the legacy table?")])
    _wait_for(lambda: _states() == {"Database upgrade · high": "needs_decision"})
    team_id = _watched()[0]["team_id"]
    port = resolve_port(team_id)
    [agent] = port.snapshot(team_id).agents
    receipt = port.stop(team_id, agent.agent_id)
    assert receipt.effect == "applied", receipt
    assert _states() == {"Database upgrade · high": "stopped"}
    assert service.status(SESSION)["question"] == ""
    [after] = port.snapshot(team_id).agents
    assert after.state.value == "stopped" and "withdrawn" in after.summary
    assert port.answer(team_id, agent.agent_id, agent.request.request_id, "yes", "dec-1").effect == "refused"


def test_a_stuck_agent_is_put_to_the_user_with_a_stop_and_never_paused(home):
    from core.standing_coordinator import api as coordinator_api

    _start(home, [_agent("slow", "Archive import", "normal", ["a"], "sleeper", "30")])
    _wait_for(lambda: _states() == {"Archive import · normal": "running"})
    coordinator = coordinator_api.default_coordinator()
    later = time.time() + 700
    coordinator.tick(later)  # nudge: an agent process cannot take a message
    coordinator.tick(later + 1)
    [stuck] = [i for i in _inbox() if i["title"] == "An agent is stuck"]
    assert "cannot take a message mid-task" in stuck["body"]
    assert "cannot be paused from here, so stop it if it stays stuck" in stuck["body"]
    assert "paused it" not in stuck["body"] and "pausing" not in stuck["body"]
    assert _states() == {"Archive import · normal": "running"}  # nothing was done to the agent


def test_the_watch_is_off_with_the_coordinator(home, monkeypatch):
    monkeypatch.setenv("VOOL_STANDING_COORDINATOR", "0")
    _start(home, [_agent("s1", "Index rebuild", "normal", ["a"], "sleeper", "0.2")])
    assert _watched() == []


def test_the_port_reads_only_well_formed_ids_and_never_creates_a_team(home):
    from core.runtime_paths import active_data_dir
    from core.standing_coordinator import api as coordinator_api
    from core.standing_coordinator.team_port import AgentTeamPort

    port = AgentTeamPort()
    root = active_data_dir() / "agent_teams"
    for bad in ("team:../x", "team:ABCDEF0123456789", "team:0123456789abcdef:0", "team:0123456789abcdef:007",
                "team:0123456789abcdef:x", "council:0123456789abcdef"):
        assert port.snapshot(bad) is None, bad
    assert port.snapshot("team:0123456789abcdef") is None
    assert not (root / "0123456789abcdef").exists()
    assert coordinator_api.watch({"team_id": "team:0123456789abcdef"})[0] == 404
