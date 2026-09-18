"""On-demand local service starter: start-if-down, fail-soft, single-flight, hardcoded-argv safety."""

from __future__ import annotations

import core.local_service_autostart as sa
from core.local_service_autostart import LocalService, ensure_service

_NOSLEEP = lambda _s: None  # noqa: E731


def test_already_running_short_circuits() -> None:
    svc = LocalService(name="X", reachable=lambda: True, argv=["should", "not", "run"])
    started = {"n": 0}
    ok, msg = ensure_service(svc, sleep=_NOSLEEP, spawn=lambda *a, **k: started.__setitem__("n", 1))
    assert ok is True and "already running" in msg
    assert started["n"] == 0  # never spawned


def test_preflight_blocks_a_doomed_start() -> None:
    svc = LocalService(name="X", reachable=lambda: False, argv=["x"], preflight=lambda: "not installed")
    started = {"n": 0}
    ok, msg = ensure_service(svc, sleep=_NOSLEEP, spawn=lambda *a, **k: started.__setitem__("n", 1))
    assert ok is False and msg == "not installed"
    assert started["n"] == 0


def test_starts_and_waits_until_reachable() -> None:
    started = {"n": 0}

    def spawn(argv, **kwargs):
        started["n"] += 1
        started["up"] = True
        return object()

    svc = LocalService(name="X", reachable=lambda: started.get("up", False),
                       argv=["srv", "--port", "8"], ready_timeout=5, poll=0.01)
    ok, msg = ensure_service(svc, sleep=_NOSLEEP, spawn=spawn)
    assert ok is True and "is up" in msg
    assert started["n"] == 1


def test_reports_timeout_when_never_ready() -> None:
    svc = LocalService(name="X", reachable=lambda: False, argv=["x"], ready_timeout=0.02, poll=0.01)
    ok, msg = ensure_service(svc, sleep=_NOSLEEP, spawn=lambda *a, **k: object())
    # The spawn seam returns a bare object with no poll(), so the process cannot be watched and this
    # falls back to the time budget. A child that is still alive is still starting, not failed.
    assert ok is False and "still starting" in msg and "0s" in msg


def test_spawn_error_is_soft() -> None:
    def boom(*a, **k):
        raise OSError("nope")

    svc = LocalService(name="X", reachable=lambda: False, argv=["x"], ready_timeout=0.02, poll=0.01)
    ok, msg = ensure_service(svc, sleep=_NOSLEEP, spawn=boom)
    assert ok is False and "could not start X" in msg


def test_argv_cwd_env_are_passed_and_child_is_detached() -> None:
    captured: dict = {}
    up = {"v": False}

    def spawn(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        up["v"] = True
        return object()

    svc = LocalService(name="X", reachable=lambda: up["v"], argv=["srv", "--port", "8188"],
                       cwd="/opt/x", env={"FOO": "bar"}, ready_timeout=5, poll=0.01)
    ok, _ = ensure_service(svc, sleep=_NOSLEEP, spawn=spawn)
    assert ok is True
    assert captured["argv"] == ["srv", "--port", "8188"]  # hardcoded, not derived from any input
    assert captured["kwargs"]["cwd"] == "/opt/x"
    assert captured["kwargs"]["env"]["FOO"] == "bar"
    assert captured["kwargs"]["start_new_session"] is True  # detached: outlives the request


def test_single_flight_waits_without_a_second_spawn(monkeypatch) -> None:
    # Simulate another request already starting "X": this call must wait, not spawn a duplicate.
    monkeypatch.setattr(sa, "_STARTING", {"X"})
    seq = iter([False, True])  # not-up, then up
    spawned = {"n": 0}
    svc = LocalService(name="X", reachable=lambda: next(seq, True), argv=["x"], ready_timeout=5, poll=0.01)
    ok, _ = ensure_service(svc, sleep=_NOSLEEP, spawn=lambda *a, **k: spawned.__setitem__("n", spawned["n"] + 1))
    assert ok is True
    assert spawned["n"] == 0
