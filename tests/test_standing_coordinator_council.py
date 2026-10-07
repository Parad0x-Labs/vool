"""The standing coordinator over a real council run store and the real notification inbox.

Council runs are written through ``CouncilRunStore`` exactly as the orchestrator writes
them; what reaches the user is read back through the served ``/api/notifications`` route.
"""

from __future__ import annotations

import time

import pytest

from tests.pa_beta_gate._pc_calendar_rig import api_get, prepare_home


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_MODEL_RADAR_POLLER", "0")
    prepare_home(tmp_path, monkeypatch)
    from core.standing_coordinator import api as coordinator_api
    from core.standing_coordinator.ports import reset_ports_for_tests

    reset_ports_for_tests()
    coordinator_api.set_default_coordinator_for_tests(None)
    yield tmp_path
    coordinator_api.set_default_coordinator_for_tests(None)
    reset_ports_for_tests()


def _write_run(run_id: str, state: str, *, started_at: float, usage=None, outcome=None):
    from core.council.run_store import CouncilRunStore

    store = CouncilRunStore(run_id)
    store.write_state({
        "state": state, "problem": "Is the cache key stable?", "chat_session": "chat-9",
        "started_at": started_at, "seats": [],
        "rounds": [{"round_no": 1, "reports": [{"seat_id": "s1", "usage": usage or {}}]}],
        "outcome": outcome,
    })
    store.append_event(state)
    return store


def _inbox():
    status, body = api_get("/api/notifications")
    assert status == 200 and body["ok"], body
    return body["items"]


def test_council_run_is_watched_escalated_asked_and_closed_through_the_inbox(home):
    from core.standing_coordinator import WatchPolicy
    from core.standing_coordinator import api as coordinator_api

    now = time.time()
    _write_run("run-a", "round_open", started_at=now - 7200,
               usage={"prompt_tokens": 1200, "output_tokens": 300, "usd_actual": 0.012})
    code, body = coordinator_api.watch({"team_id": "council:run-a",
                                        "policy": {"stall_after_seconds": 60, "max_nudges": 1}})
    assert code == 200 and body["created"] is True

    coordinator = coordinator_api.default_coordinator()
    later = time.time() + 120
    coordinator.tick(later)  # nudge: a seat turn cannot take one
    coordinator.tick(later + 1)  # so the user is told instead
    items = _inbox()
    [stuck] = [i for i in items if i["title"] == "An agent is stuck"]
    assert stuck["session_id"] == "chat-9" and stuck["payload"]["section"] == "needs"
    assert stuck["payload"]["event_type"] == "coordinator_escalate"
    assert "cannot take a message" in stuck["body"] and "$0.01" in stuck["body"]

    status_code, status = coordinator_api.status("council:run-a")
    assert status_code == 200 and status["spend"] == {"cost_usd": 0.012, "tokens": 1500, "calls": 1}

    # The run pauses on a seat that produced nothing: one question, which memory never answers.
    _write_run("run-a", "needs_attention", started_at=now - 7200,
               outcome={"result": "needs_attention", "round_no": 1, "blocking_seats": [{"seat_id": "s2"}]})
    coordinator.tick(later + 2)
    coordinator.tick(later + 3)
    asks = [i for i in _inbox() if i["title"] == "Agents need your decision"]
    assert len(asks) == 1 and "seat(s) s2 produced no report" in asks[0]["body"]

    _write_run("run-a", "converged", started_at=now - 7200, outcome={"result": "converged", "detail": "AGREE x3"})
    coordinator.tick(later + 4)
    [final] = [i for i in _inbox() if i["title"] == "Team finished"]
    assert "Council run: done. Its report: AGREE x3" in final["body"]
    assert coordinator_api.watches()[1]["watches"] == []


def test_api_refuses_unknown_fields_unknown_teams_and_ports(home):
    from core.standing_coordinator import api as coordinator_api

    assert coordinator_api.watch({"team_id": "council:nope"})[0] == 404
    assert coordinator_api.watch({"team_id": "council:x", "extra": 1})[0] == 400
    assert coordinator_api.watch({"team_id": "nobody:x"})[0] == 400
    assert coordinator_api.watch({"team_id": "no-prefix"})[0] == 400
    assert coordinator_api.decide({"team_id": "council:x", "request_id": "r", "answer": "y",
                                   "remember": "forever"})[0] == 400
    assert coordinator_api.forget({"decision_id": "dec-missing"})[0] == 404
    assert coordinator_api.status("council:never-watched")[0] == 404


def test_council_stop_acts_only_on_runs_this_process_holds(home):
    from core.standing_coordinator.council_port import CouncilRunPort

    _write_run("run-b", "round_open", started_at=time.time())
    port = CouncilRunPort()
    receipt = port.stop("council:run-b", "run-b")
    assert receipt.ok is False and receipt.effect == "not_found"
    assert port.stop("council:run-b", "other-run").effect == "not_found"
    assert port.snapshot("council:../run-b") is None


def test_daemon_ticks_reconciles_on_start_and_stops(home, tmp_path):
    import threading

    from core.standing_coordinator.coordinator import StandingCoordinator
    from core.standing_coordinator.daemon import CoordinatorDaemon
    from core.standing_coordinator.notifier import MemoryNotifier
    from core.standing_coordinator.store import CoordinatorStore

    store = CoordinatorStore(tmp_path / "d.sqlite3", clock=lambda: 0.0)
    store.claim("nudge:team:x:a:0.000:1", team_id="team:x", kind="nudge", agent_id="a")
    store._clock = time.time
    ticked = threading.Event()

    def wait(event, seconds):
        ticked.set()
        return event.wait(0.01)

    daemon = CoordinatorDaemon(StandingCoordinator(store, notifier=MemoryNotifier()), wait_fn=wait)
    assert daemon.start() is True
    assert ticked.wait(5)
    daemon.stop()
    assert not daemon.alive and daemon.ticks >= 1 and daemon.failures == 0
    assert store.actions("team:x")[0]["status"] == "uncertain"


def test_env_switch_keeps_the_daemon_off(home, monkeypatch):
    from core.standing_coordinator import daemon

    monkeypatch.setenv(daemon.DISABLE_ENV, "0")
    assert daemon.start_default_daemon() is None
