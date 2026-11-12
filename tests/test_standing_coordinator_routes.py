"""The standing coordinator's served routes: owner-local, typed, through the command registry."""

from __future__ import annotations

import time

import pytest

from tests.pa_beta_gate._pc_calendar_rig import api_get, api_post, prepare_home


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


def _write_run(run_id: str, state: str, *, started_at: float, outcome=None):
    from core.council.run_store import CouncilRunStore

    store = CouncilRunStore(run_id)
    store.write_state({"state": state, "problem": "Is the cache key stable?", "chat_session": "chat-9",
                       "started_at": started_at, "seats": [], "rounds": [], "outcome": outcome})
    store.append_event(state)


def test_served_routes_watch_status_decide_and_stay_owner_local(home):
    _write_run("run-c", "needs_attention", started_at=time.time(),
               outcome={"result": "needs_attention", "round_no": 2, "blocking_seats": [{"seat_id": "s1"}]})
    status, body = api_post("/api/coordinator/watch", {"team_id": "council:run-c"})
    assert status == 200 and body["created"] is True, body
    assert api_post("/api/coordinator/watch", {"team_id": "council:run-c", "bogus": 1})[0] == 400
    assert api_post("/api/coordinator/watch", {"team_id": "council:run-c"}, host="10.0.0.5")[0] == 403
    assert api_get("/api/coordinator/watches", host="10.0.0.5")[0] == 403

    status, body = api_get("/api/coordinator/status", {"team": ["council:run-c"]})
    assert status == 200 and body["agents"][0]["state"] == "waiting", body
    waiting = body["agents"][0]["waiting_on"]
    assert waiting["always_ask"] is True
    # A council pause is answered from its own card: the decision is recorded, the delivery says so.
    status, body = api_post("/api/coordinator/decide", {"team_id": "council:run-c", "request_id": waiting["request_id"],
                                                         "answer": "retry s1", "remember": "always"})
    assert status == 200 and body["decision"]["standing"] is False
    assert body["delivered"] == [{"agent_id": "run-c", "request_id": waiting["request_id"], "status": "unsupported"}]
    status, body = api_get("/api/coordinator/decisions", {"all": ["1"]})
    assert status == 200 and [d["answer"] for d in body["decisions"]] == ["retry s1"]
    assert api_get("/api/coordinator/status", {})[0] == 400
    status, body = api_post("/api/coordinator/unwatch", {"team_id": "council:run-c"})
    assert status == 200 and body["ended"] is True
