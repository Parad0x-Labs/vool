"""The standing coordinator keeps one loop, and says it stopped an agent only when the stop applied.

Pack 2b review, 2026-10-07:
- `stop_default_daemon` dropped its loop even when the loop's tick outlived the stop timeout, so the
  next start ran a second loop beside it (two ticks at once).
- The cap report was written when the stops were planned, so a refused, unsupported or missing stop was
  still reported to the user as "I stopped 1 unfinished agent(s)".
"""
from __future__ import annotations

import threading
from types import SimpleNamespace

import pytest

from core.council.cost_ladder import SpendCeilings
from core.standing_coordinator import api, daemon, planner
from core.standing_coordinator.coordinator import StandingCoordinator
from core.standing_coordinator.model import AgentSnapshot, AgentState, PortReceipt, SpendView, TeamSnapshot, WatchPolicy
from core.standing_coordinator.notifier import MemoryNotifier
from core.standing_coordinator.store import CoordinatorStore


def test_a_restart_after_a_timed_out_stop_keeps_the_one_running_loop(monkeypatch):
    release, entered, second = threading.Event(), threading.Event(), threading.Event()
    lock, active, peak = threading.Lock(), set(), [0]

    def tick():
        with lock:
            active.add(threading.get_ident())
            peak[0] = max(peak[0], len(active))
            if len(active) > 1:
                second.set()
            entered.set()
        release.wait(20)
        with lock:
            active.discard(threading.get_ident())
        return {"ok": True}

    coordinator = SimpleNamespace(store=SimpleNamespace(reconcile=lambda: None), tick=tick)
    monkeypatch.setattr(api, "default_coordinator", lambda: coordinator)
    monkeypatch.setattr(daemon, "_DEFAULT", None)
    monkeypatch.setenv("VOOL_STANDING_COORDINATOR", "1")
    first = restarted = None
    try:
        first = daemon.start_default_daemon()
        assert entered.wait(3)
        assert daemon.stop_default_daemon() is False, "a loop still inside its tick has not stopped"
        restarted = daemon.start_default_daemon()
        second.wait(2)
        assert restarted is first and peak[0] == 1, (restarted is first, peak[0])
    finally:
        release.set()
        assert daemon.stop_default_daemon() is True
    assert not first.alive


def _team(agent_ids):
    agents = tuple(AgentSnapshot(agent_id=a, state=AgentState.RUNNING, started_at=1000) for a in agent_ids)
    return TeamSnapshot(team_id="owned:fixture", agents=agents, started_at=1000, spend=SpendView(cost_usd=2))


def _run_cap(tmp_path, effects):
    team = _team(list(effects))

    class Port:
        def snapshot(self, team_id):
            return team

        def stop(self, team_id, agent_id):
            effect = effects[agent_id]
            if effect == "raises":
                raise RuntimeError("port died mid-stop")
            return PortReceipt(effect == "applied", effect, "controlled outcome")

    notifier = MemoryNotifier()
    store = CoordinatorStore(tmp_path / "coordinator.sqlite3", clock=lambda: 1001)
    coordinator = StandingCoordinator(store, notifier=notifier, port_resolver=lambda _team_id: Port(), clock=lambda: 1001)
    coordinator.watch(team.team_id, WatchPolicy(ceilings=SpendCeilings(max_cost_usd=1, max_tokens=1000, max_calls=100,
                                                                        wall_clock_seconds=1000)))
    coordinator.tick()
    return [item["body"] for item in notifier.items]


@pytest.mark.parametrize("effect", ["unsupported", "refused", "not_found", "raises"])
def test_a_stop_that_did_not_apply_is_never_reported_as_stopped(tmp_path, effect):
    bodies = _run_cap(tmp_path, {"a": effect})
    assert bodies and not any("I stopped" in body for body in bodies), bodies
    assert any("could not stop 1" in body and "may still be running" in body for body in bodies), bodies


def test_an_applied_stop_is_reported_as_stopped(tmp_path):
    bodies = _run_cap(tmp_path, {"a": "applied"})
    assert any("I stopped 1 unfinished agent(s)" in body for body in bodies), bodies
    assert not any("could not stop" in body for body in bodies), bodies


def test_a_partial_stop_names_both_outcomes(tmp_path):
    bodies = _run_cap(tmp_path, {"a": "applied", "b": "refused"})
    body = next(b for b in bodies if "cap" in b)
    assert "I stopped 1 unfinished agent(s)" in body and "could not stop 1" in body and "refused" in body, body


def test_a_stop_with_no_receipt_is_reported_as_unknown():
    text = planner.stop_report_text({"axis": "cost", "stops": {"stop:t:a:budget": "a"}}, {"stop:t:a:budget": "intent"})
    assert "I stopped" not in text and "don't know whether 1 stopped" in text, text
