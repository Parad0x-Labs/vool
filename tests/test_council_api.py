"""Council API contract tests: owner-local walls, typed validation, real thread runs.

The seat-turn factory is stubbed at its seam (no HTTP, no model); everything else —
the endpoint arms, the api module, the orchestrator thread, the run store — is real.
"""

from __future__ import annotations

import json
import time

import pytest

from core.web.api.runtime import RuntimeServices
from core.web.api.service import dispatch_get, dispatch_post


@pytest.fixture()
def isolated_council(tmp_path, monkeypatch):
    def _patched(*parts):
        base = tmp_path / "data"
        base.mkdir(parents=True, exist_ok=True)
        out = base
        for part in parts:
            out = out / part
        return out

    monkeypatch.setattr("core.council.run_store.data_path", _patched)
    monkeypatch.setattr("core.council.api.read_current_pin", lambda base_url: "")
    _restore_pin_double(monkeypatch)
    from core.council import api as council_api
    from core.council import pin_lock

    # The model-pin fence is process-global. Starting from a known-clear owner is what
    # keeps one test's leaked pin from reading as the next test's typed refusal.
    pin_lock.reset_on_startup()
    # A PRIOR test's run thread may still be draining (the local-seat-override convene
    # above starts one). While it lives, _RUNS answers a fresh convene with a 409 that is
    # about last test's run. Wait for it to end, then clear the registries either way.
    for _ in range(100):
        if not council_api._RUNS and not council_api._PAUSED:
            break
        time.sleep(0.05)
    council_api.forget_all_runs_for_tests()

    def _factory(base_url, dispatch_capability=""):
        # The double CHECKS rather than absorbs: convene must hand every seat factory the
        # run's own fence capability, or the seats would be refused by their own council.
        assert dispatch_capability, "convene built a seat factory with no pin capability"

        def _turn(seat, prompt, round_no, run_id):
            if round_no == 1:
                text = "DIAGNOSIS: scripted root cause." if seat.role_id == "builder" else "looked around"
            else:
                text = "VERDICT: AGREE"
            return {"text": text, "receipt_count": 1, "session_id": f"stub-{seat.seat_id}"}

        return _turn

    monkeypatch.setattr("core.council.api.live_seat_turn_factory", _factory)
    yield tmp_path
    pin_lock.reset_on_startup()


def _restore_pin_double(monkeypatch):
    """A restore_pin stand-in that CHECKS rather than absorbs: the operator's pin is handed
    back while the run still owns the fence, so the call must carry the run's capability or
    the council's own restoration would be refused by its own guard."""
    from core.council import pin_lock

    def _restore(base_url, model, capability=""):
        assert capability, "restore_pin was called without the run's pin capability"
        assert pin_lock.is_dispatch_capability(capability, owner_local=True), (
            "restore_pin carried something that is not the live run's capability"
        )

    monkeypatch.setattr("core.council.api.restore_pin", _restore)


def _get(path, host, query=None):
    res = dispatch_get(path=path, query=query or {}, runtime=RuntimeServices(display_name="N"),
                       model_name="vool", client_host=host)
    return res.status, json.loads(res.body.decode("utf-8"))


def _post(path, body, host):
    res = dispatch_post(path=path, body=body, headers={"content-type": "application/json"},
                        runtime=RuntimeServices(display_name="N"), model_name="vool",
                        workspace_root_provider=lambda: "/tmp", client_host=host)
    return res.status, json.loads(res.body.decode("utf-8"))


def test_every_council_endpoint_is_owner_local(isolated_council) -> None:
    for path in ("/api/council/runs", "/api/council/status"):
        status, payload = _get(path, "203.0.113.9", {"run": ["x"]})
        assert status == 403 and payload.get("error") == "owner_local_required"
    for path in ("/api/council/convene", "/api/council/stop"):
        status, payload = _post(path, {"problem": "p"}, "203.0.113.9")
        assert status == 403


CLOUD_BENCH = [
    {"role_id": "builder", "model": "vendor/model-a", "votes": True},
    {"role_id": "falsifier", "model": "vendor/model-b", "votes": True},
    {"role_id": "reviewer", "model": "vendor/model-c", "votes": True},
]


def test_convene_validation_is_typed(isolated_council) -> None:
    status, payload = _post("/api/council/convene", {"problem": "p", "surprise": 1}, "127.0.0.1")
    assert status == 400 and "unknown fields" in payload["error"]
    status, payload = _post("/api/council/convene", {"problem": "", "seats": CLOUD_BENCH}, "127.0.0.1")
    assert status == 400 and "problem" in payload["error"]
    status, payload = _post(
        "/api/council/convene",
        {"problem": "p", "seats": [{"role_id": "prophet", "model": "m"}]},
        "127.0.0.1",
    )
    assert status == 400 and "unknown role" in payload["error"]
    status, payload = _post(
        "/api/council/convene",
        {"problem": "p", "seats": [{"role_id": "verifier", "model": "vendor/m", "votes": False}]},
        "127.0.0.1",
    )
    assert status == 400 and "voting seat" in payload["error"]


def test_local_and_auto_seats_are_refused_by_the_server_wall(isolated_council) -> None:
    """HARD RULE (operator, 2026-08-28): a local seat can freeze the host — the wall is
    server-side so no client, no default, and no future operator can seat one by accident."""
    status, payload = _post("/api/council/convene", {"problem": "p"}, "127.0.0.1")
    assert status == 400 and "local_seat_refused" in payload["error"], (
        "a bare convene must not silently seat local models"
    )
    for model in ("", "auto", "vool", "qwen3:14b", "local"):
        seats = [{"role_id": "builder", "model": model, "votes": True}]
        status, payload = _post("/api/council/convene", {"problem": "p", "seats": seats}, "127.0.0.1")
        assert status == 400 and "local_seat_refused" in payload["error"], f"model {model!r} slipped the wall"


def test_local_seat_override_is_an_explicit_environment_act(isolated_council, monkeypatch) -> None:
    monkeypatch.setenv("VOOL_COUNCIL_ALLOW_LOCAL_SEATS", "1")
    seats = [{"role_id": "builder", "model": "qwen3:0.6b", "votes": True}]
    status, payload = _post("/api/council/convene", {"problem": "p", "seats": seats}, "127.0.0.1")
    assert status == 200 and payload["ok"] is True


def test_convene_runs_to_adjudication_and_status_tracks_it(isolated_council) -> None:
    status, payload = _post(
        "/api/council/convene",
        {"problem": "why does the turn lie", "seats": CLOUD_BENCH},
        "127.0.0.1",
    )
    assert status == 200 and payload["ok"] is True
    run_id = payload["run_id"]
    assert [s["role_id"] for s in payload["seats"]] == ["builder", "falsifier", "reviewer"]
    deadline = time.time() + 30
    final = None
    while time.time() < deadline:
        status, snapshot = _get("/api/council/status", "127.0.0.1", {"run": [run_id]})
        assert status == 200
        if snapshot["run"].get("state") in {"converged", "failed", "no_convergence", "crashed"}:
            final = snapshot
            break
        time.sleep(0.1)
    assert final is not None, "the council thread never reached a terminal state"
    assert final["run"]["state"] == "converged"
    assert final["run"]["outcome"]["result"] == "adjudicated"
    assert final["run"]["candidate"] == "scripted root cause."
    status, listing = _get("/api/council/runs", "127.0.0.1")
    assert status == 200 and any(row["run_id"] == run_id for row in listing["runs"])


def test_second_convene_while_one_is_live_is_a_typed_409(isolated_council, monkeypatch) -> None:
    """Five silent duplicate runs happened live (2026-08-28) — one council at a time is law."""
    import threading

    gate = threading.Event()

    def _blocking_factory(base_url, dispatch_capability=""):
        assert dispatch_capability, "convene built a seat factory with no pin capability"

        def _turn(seat, prompt, round_no, run_id):
            gate.wait(timeout=20)
            return {"text": "DIAGNOSIS: x" if round_no == 1 else "VERDICT: AGREE",
                    "receipt_count": 1, "session_id": None}

        return _turn

    monkeypatch.setattr("core.council.api.live_seat_turn_factory", _blocking_factory)
    status, payload = _post("/api/council/convene", {"problem": "p", "seats": CLOUD_BENCH}, "127.0.0.1")
    assert status == 200
    first_run = payload["run_id"]
    try:
        status, payload = _post("/api/council/convene", {"problem": "p2", "seats": CLOUD_BENCH}, "127.0.0.1")
        assert status == 409 and "council_already_live" in payload["error"]
        assert payload["live_run_id"] == first_run
    finally:
        gate.set()
        _post("/api/council/stop", {"run_id": first_run}, "127.0.0.1")
        deadline = time.time() + 20
        while time.time() < deadline:
            snapshot = _get("/api/council/status", "127.0.0.1", {"run": [first_run]})[1]
            if not snapshot.get("live"):
                break
            time.sleep(0.1)


def test_status_of_unknown_run_is_404_and_stop_of_dead_run_is_404(isolated_council) -> None:
    status, payload = _get("/api/council/status", "127.0.0.1", {"run": ["council-nope"]})
    assert status == 404
    status, payload = _get("/api/council/status", "127.0.0.1")
    assert status == 400, "a missing run parameter is a caller error, not a listing"
    status, payload = _post("/api/council/stop", {"run_id": "council-nope"}, "127.0.0.1")
    assert status == 404
    status, payload = _post("/api/council/stop", {"run_id": "x", "extra": 1}, "127.0.0.1")
    assert status == 400


def test_prewarm_is_killed_by_the_local_models_disabled_marker(tmp_path, monkeypatch) -> None:
    """Operator hard stop: with config/local_models_disabled present, boot warms NOTHING local."""
    from apps import vool_api_server

    # The canonical LocalModelPolicy (local-model-disable lane) resolves the marker through
    # active_vool_home()/data/config — the same production path the old inline check read via
    # active_data_dir()/config. Patch the seam the ONE authority actually consults.
    (tmp_path / "data" / "config").mkdir(parents=True)
    (tmp_path / "data" / "config" / "local_models_disabled").touch()
    monkeypatch.setattr("core.runtime_paths.active_vool_home", lambda *a, **k: tmp_path)
    calls = []
    monkeypatch.setattr("core.intent_arbiter.prewarm_async", lambda: calls.append("arbiter"))
    runtime = type("R", (), {"deferred_prewarm": staticmethod(lambda: calls.append("providers"))})()
    vool_api_server._start_background_prewarm(runtime)
    assert calls == [], f"prewarm ran despite the marker: {calls}"
    # Control: without the marker the same call DOES start warming (arbiter immediately).
    (tmp_path / "data" / "config" / "local_models_disabled").unlink()
    vool_api_server._start_background_prewarm(runtime)
    import time as _time
    deadline = _time.time() + 5
    while _time.time() < deadline and "providers" not in calls:
        _time.sleep(0.05)
    assert "arbiter" in calls and "providers" in calls
