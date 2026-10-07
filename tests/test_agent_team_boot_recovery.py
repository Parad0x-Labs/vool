"""Team recovery at server boot: nothing when there is nothing, and one bad team never costs the others.

`recover_all` runs inside the server's start-up. It created `<data>/agent_teams` on every boot even when no
team had ever existed, and its loop had no guard of its own: one corrupt `team.sqlite3` raised out of the
loop, so every team after it in the listing was never re-adopted (the server still started, only because its
caller swallowed the error).
"""
from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name == "nt", reason="POSIX process signals")

AGENT = str(Path(__file__).with_name("agent_team_agent.py"))
LIMITS = {"max_usd": 1.0, "max_tokens": 10_000, "max_calls": 5, "wall_clock_seconds": 60}


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("VOOL_HOME", str(tmp_path / "home"))
    from core.runtime_paths import configure_runtime_home

    configure_runtime_home(tmp_path / "home")
    yield tmp_path / "home"
    from core.agent_team import service

    service._forget_for_tests()
    configure_runtime_home(None)


def test_boot_recovery_with_no_teams_changes_nothing(home):
    from core.agent_team import service
    from core.runtime_paths import active_data_dir

    before = sorted(p.relative_to(home) for p in home.rglob("*")) if home.exists() else []
    assert service.recover_all() == []
    assert not (active_data_dir() / "agent_teams").exists()
    after = sorted(p.relative_to(home) for p in home.rglob("*")) if home.exists() else []
    assert after == before


def test_a_corrupt_team_is_reported_and_the_next_team_is_still_recovered(home, tmp_path):
    from core.agent_team import service
    from core.agent_team.coordinator import TeamCoordinator
    from core.runtime_paths import active_data_dir

    ws = tmp_path / "ws"
    (ws / "a").mkdir(parents=True)
    root = active_data_dir() / "agent_teams"
    # Sorted first, so a loop that stops at the first error never reaches the live team.
    (root / "aaa-corrupt").mkdir(parents=True)
    (root / "aaa-corrupt" / "team.sqlite3").write_bytes(b"this is not a sqlite database" * 64)
    live = TeamCoordinator(root / "zzz-live", workspace=ws, team_limits=LIMITS, tick_seconds=0.2)
    live.start([{"key": "s", "objective": "Cache warmup", "importance": "low", "claims": ["a"],
                 "command": [sys.executable, AGENT, "sleeper", "30"], "limits": LIMITS}])
    live.tick()
    pid = live.registry.agents()[0]["pid"]
    live.close()  # the agent keeps running, as it would across a server restart
    try:
        report = service.recover_all()
        by_team = {entry["team"]: entry for entry in report}
        assert "zzz-live" in by_team and "Cache warmup · low" in by_team["zzz-live"].get("adopted", []), report
        assert by_team.get("aaa-corrupt", {}).get("error"), report
    finally:
        service._forget_for_tests()
        os.kill(pid, signal.SIGTERM)  # the agent this test started; pid recorded above


def test_the_server_starts_with_a_corrupt_team_on_disk(home):
    from core.runtime_paths import active_data_dir

    root = active_data_dir() / "agent_teams" / "broken"
    root.mkdir(parents=True)
    (root / "team.sqlite3").write_bytes(b"\x00garbage" * 128)
    from apps.vool_api_server import create_app

    assert create_app() is not None
