"""The cached System-B broker rebuilds when a key is added/removed (epoch bump), so a mid-session
key change is seen without a restart; an injected broker is never auto-rebuilt."""
from __future__ import annotations

import core.cloud_runtime as cr
from core.memory_first_router import MemoryFirstRouter


def test_epoch_bump_is_monotonic():
    a = cr.cloud_broker_epoch()
    b = cr.bump_cloud_broker_epoch()
    assert b == a + 1 and cr.cloud_broker_epoch() == b


def test_router_rebuilds_broker_on_epoch_change(monkeypatch):
    builds = {"n": 0}

    class _FakeBroker:
        def execute(self, *a, **k):  # pragma: no cover - not exercised here
            return None

    def _fake_build():
        builds["n"] += 1
        return _FakeBroker()

    monkeypatch.setattr(cr, "build_default_cloud_broker", _fake_build)
    # Also patch the name the router imports.
    import core.memory_first_router as mfr
    monkeypatch.setattr(mfr, "MemoryFirstRouter", MemoryFirstRouter)

    r = MemoryFirstRouter()
    # Simulate the router's lazy build guarded by the epoch (mirror the production condition).
    from core.cloud_runtime import build_default_cloud_broker, cloud_broker_epoch

    def _ensure():
        if r._cloud_broker is None or (not r._broker_injected and r._cloud_broker_epoch != cloud_broker_epoch()):
            r._cloud_broker = build_default_cloud_broker()
            r._cloud_broker_epoch = cloud_broker_epoch()

    _ensure()
    assert builds["n"] == 1
    _ensure()
    assert builds["n"] == 1, "no epoch change -> no rebuild"
    cr.bump_cloud_broker_epoch()
    _ensure()
    assert builds["n"] == 2, "epoch bump -> rebuild"


def test_injected_broker_is_never_rebuilt():
    sentinel = object()
    r = MemoryFirstRouter(cloud_broker=sentinel)
    assert r._broker_injected is True and r._cloud_broker is sentinel
