"""Fixtures for the core.liquefy test pack.

The root conftest pins ``VOOL_LIQUEFY_LOGS=0`` so the projection can never
pollute real data or other lanes' tests; every fixture here opts back in with
an explicit per-test home.
"""
from __future__ import annotations

import pytest

from core.liquefy.store import LiquefyLogStore


@pytest.fixture(autouse=True)
def _liquefy_lane_enabled(monkeypatch, tmp_path):
    home = tmp_path / "liquefy-store"
    monkeypatch.setenv("VOOL_LIQUEFY_LOGS", "1")
    monkeypatch.setenv("VOOL_LIQUEFY_LOGS_HOME", str(home))
    from core.liquefy import hooks as liquefy_hooks

    liquefy_hooks.reset_default_store()
    yield home
    liquefy_hooks.reset_default_store()


@pytest.fixture()
def make_store(tmp_path):
    counter = {"n": 0}

    def _make(**kwargs) -> LiquefyLogStore:
        counter["n"] += 1
        root = kwargs.pop("root", tmp_path / f"store-{counter['n']}")
        return LiquefyLogStore(root, **kwargs)

    return _make


@pytest.fixture()
def sample_events():
    def _make(count: int, session: str = "sess-1", start: int = 0) -> list[dict]:
        kinds = ["effect_intended", "effect_terminal", "coverage_gap", "retention_pruned"]
        return [
            {
                "kind": kinds[i % 4],
                "source": "blackbox",
                "session_id": session,
                "trace_id": f"trace-{(start + i) // 10}",
                "ref": {"event_id": f"effect-{start + i:04d}", "turn_id": f"turn-{(start + i) // 5}"},
                "payload": {
                    "path": f"/workspace/f{i % 7}.txt",
                    "operation": "write",
                    "outcome": ["succeeded", "failed", "refused"][i % 3],
                    "before_sha256": f"{'ab' * 16}{start + i:04d}",
                    "nested": {"attempt": i % 3, "channel": "sandbox"},
                },
            }
            for i in range(count)
        ]

    return _make
